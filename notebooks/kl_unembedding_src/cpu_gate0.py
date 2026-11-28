"""Gate 0 auf der CPU nachrechnen, ohne GPU: Schichten 0 bis 24 des echten Modells werden Schicht für Schicht
per HTTP-Bereichsanfragen geladen (nichts wird gespeichert) und auf alle ausgewählten Fenster angewandt.

Gruppen von Fenstern (nur solche, die sich exakt zurück in Token übersetzen lassen, wie im Notebook):
  full      alle Fenster, die am Textanfang beginnen (u < 84): voller Kontext im Fenster
  full_eot  dieselben Fenster mit vorangestelltem <|endoftext|> (falls der Atlas so gerechnet hat)
  cropped   Stichprobe abgeschnittener Fenster (u = 84): der Text vor dem Fenster fehlt
Gerechnet wird in float32. Gemessen wird wie im Notebook: Aktivierung SiLU(Gate_n)*Up_n des Neurons 345 im
Experten 124 von Schicht 24, geteilt durch theta, und ob der Router den Experten wählt.

Aufruf:  python cpu_gate0.py --selftest     (winziges Modell, Vergleich mit kl_lib2.activation_at)
         python cpu_gate0.py                (echtes Modell; schreibt cpu_gate0_results.json)
"""
import concurrent.futures
import gzip
import json
import os
import random
import sys
import time
import urllib.request

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kl_lib2 as K

torch.set_num_threads(4)
MODEL_ID = "Qwen/Qwen3.6-35B-A3B"
BASE = f"https://huggingface.co/{MODEL_ID}/resolve/main/"
CAL_ABS, CAL_REL = 0.005, 0.05
EOT = 248044
DTYPES = {"BF16": torch.bfloat16, "F32": torch.float32, "F16": torch.float16}


class _Stop(Exception):
    pass


class _Rec:
    """Nimmt im Aufzeichnungsmodus von K.LazyLayer die Argumente jeder Schicht auf."""

    def __init__(self):
        self.rec = None

    def _capture(self, idx, hidden_states, kwargs):
        if idx == 0:
            self.rec["h"] = hidden_states.detach()
        self.rec["kw"][idx] = kwargs


# ----------------------------------------------------------------------------- Gewichte per HTTP
def _get(url, a, b, tries=5):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
            with urllib.request.urlopen(req, timeout=600) as r:
                data = r.read()
            if len(data) == b - a + 1:
                return data
            raise IOError(f"kurze Antwort {len(data)} statt {b - a + 1}")
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


class HttpWeights:
    def __init__(self, weight_map, workers=8, chunk=64 * 2**20):
        self.wm, self.workers, self.chunk = weight_map, workers, chunk
        self.headers = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        self.bytes = 0

    def _header(self, file):
        if file not in self.headers:
            url = BASE + file
            n = int.from_bytes(_get(url, 0, 7), "little")
            self.headers[file] = (json.loads(_get(url, 8, 8 + n - 1)), 8 + n)
        return self.headers[file]

    def tensors(self, names):
        out = {}
        for name in names:
            file = self.wm[name]
            h, base = self._header(file)
            info = h[name]
            a, b = info["data_offsets"]
            a, b = base + a, base + b - 1
            parts = [(s, min(b, s + self.chunk - 1)) for s in range(a, b + 1, self.chunk)]
            buf = bytearray(b - a + 1)                     # ein Puffer, Teile werden direkt hineingeschrieben
            mv = memoryview(buf)

            def part(ab, a=a, mv=mv, url=BASE + file):
                s, e = ab
                mv[s - a:e - a + 1] = _get(url, s, e)

            list(self.pool.map(part, parts))
            del mv
            self.bytes += len(buf)
            out[name] = torch.frombuffer(buf, dtype=DTYPES[info["dtype"]]).reshape(info["shape"])   # Typ wie gespeichert
        return out


# ----------------------------------------------------------------------------- Rechnung
def compute(skel, get_tensors, needed, seqs, L, E, N, block=32, log=print):
    """seqs: Liste von Token-ID-Listen. Liefert je Text (Aktivierung [T], Routing-Gewicht [T]) in float32.

    Die unteren Schichten werden mit den Argumenten aufgerufen, die das Modell selbst übergibt (aufgezeichnet
    wie in kl_lib2.LayerStreamer); jede Schicht wird einmal geladen und auf alle Blöcke angewandt."""
    from accelerate import init_empty_weights
    from transformers.models.qwen3_5_moe.modeling_qwen3_5_moe import Qwen3_5MoeDecoderLayer

    text = K.text_model(skel)
    tc = skel.config.text_config
    emb = get_tensors(["model.language_model.embed_tokens.weight"])["model.language_model.embed_tokens.weight"].float()
    text.embed_tokens = torch.nn.Embedding.from_pretrained(emb, freeze=True)
    text.rotary_emb.to("cpu")
    rec = _Rec()
    for i in range(tc.num_hidden_layers):
        lz = K.LazyLayer(rec, i)
        lz.mode = "capture"
        text.layers[i] = lz

    order = sorted(range(len(seqs)), key=lambda k: len(seqs[k]))
    plans = []

    def stop(mod, args):
        raise _Stop()

    hook = text.layers[L + 1].register_forward_pre_hook(stop)
    try:
        for c0 in range(0, len(order), block):
            ks = order[c0:c0 + block]
            T = max(len(seqs[k]) for k in ks)
            ids = torch.zeros(len(ks), T, dtype=torch.long)
            for r, k in enumerate(ks):
                ids[r, :len(seqs[k])] = torch.tensor(seqs[k])
            rec.rec = {"h": None, "kw": {}}
            with torch.no_grad():
                try:
                    text(input_ids=ids, use_cache=False)
                except _Stop:
                    pass
            assert sorted(rec.rec["kw"]) == list(range(L + 1)), "Aufzeichnung unvollständig"
            plans.append({"ks": ks, "h": rec.rec["h"], "kw": rec.rec["kw"]})
    finally:
        hook.remove()

    def names_of(i):
        pre = f"model.language_model.layers.{i}."
        return [n for n in needed if n.startswith(pre)]

    def build(i, raw):
        pre = f"model.language_model.layers.{i}."
        with init_empty_weights():
            layer = Qwen3_5MoeDecoderLayer(tc, i)
        layer.load_state_dict({k[len(pre):]: v.float() for k, v in raw.items()}, strict=True, assign=True)
        return layer.eval()

    results = [None] * len(seqs)
    prefetch = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    nxt = prefetch.submit(get_tensors, names_of(0))
    t0 = time.time()
    for i in range(L + 1):
        raw = nxt.result()
        if i < L:
            nxt = prefetch.submit(get_tensors, names_of(i + 1))
        layer = build(i, raw)
        del raw
        with torch.no_grad():
            if i < L:
                for p in plans:
                    p["h"] = layer(p["h"], **p["kw"][i])
            else:
                got = {}

                def grab(mod, args):
                    got["x"] = args[0].detach()
                    raise _Stop()

                hk = layer.mlp.register_forward_pre_hook(grab)
                try:
                    for p in plans:
                        try:
                            layer(p["h"], **p["kw"][i])
                        except _Stop:
                            pass
                        x = got.pop("x")
                        for r, k in enumerate(p["ks"]):
                            xr = x[r, :len(seqs[k])].float()
                            top_i, top_v = K.route(layer.mlp, xr)
                            w = (top_v * (top_i == E)).sum(-1).float()
                            act = K.neuron_act(layer.mlp, E, N, xr)
                            results[k] = (act.clone(), w.clone())
                finally:
                    hk.remove()
        del layer
        log(f"Schicht {i} fertig nach {time.time() - t0:.0f} s")
    prefetch.shutdown()
    return results


# ----------------------------------------------------------------------------- Selbsttest
def selftest():
    import tiny_model as TM
    full = TM.build()
    sd = {k: v.detach().clone() for k, v in full.state_dict().items()}
    skel = K.skeleton(full.config)
    needed = K.needed_names(skel)
    L, E, N = 2, 3, 5
    g = torch.Generator().manual_seed(5)
    seqs = [torch.randint(0, 5000, (n,), generator=g).tolist() for n in (7, 12, 3, 12, 9, 15, 4)]
    got = compute(skel, lambda names: {n: sd[n].float() for n in names}, needed, seqs, L, E, N, block=3, log=lambda s: None)
    RC = K.RoutingControl(full)
    hook = K.NeuronHook(full, L, E, N, RC)
    worst_a = worst_w = 0.0
    for s, (act, w) in zip(seqs, got):
        a_ref, w_ref = K.activation_at(full, hook, RC, torch.tensor(s))
        worst_a = max(worst_a, float((a_ref - act).abs().max()))
        worst_w = max(worst_w, float((w_ref - w).abs().max()))
    hook.remove()
    RC.remove()
    ok = worst_a < 1e-4 and worst_w < 1e-5
    print(f"{'PASS' if ok else 'FAIL'} selftest: activation max diff {worst_a:.1e}, routing weight max diff {worst_w:.1e}")
    return ok


# ----------------------------------------------------------------------------- Auswertung
def share_ok(pairs):
    return sum(abs(a - v) <= CAL_ABS + CAL_REL * abs(v) for a, v in pairs) / max(len(pairs), 1)


def summarize(rows):
    val = [r for r in rows if r["v"] is not None]
    out = {"slots": len(rows), "valued": len(val),
           "share_value_ok": share_ok([(r["a"], r["v"]) for r in val]),
           "share_routing_ok": sum(r["routed"] == (r["v"] is not None) for r in rows) / max(len(rows), 1),
           "routing_both": sum(r["routed"] and r["v"] is not None for r in rows),
           "routing_only_atlas": sum((not r["routed"]) and r["v"] is not None for r in rows),
           "routing_only_model": sum(r["routed"] and r["v"] is None for r in rows)}
    out["gate_would_pass"] = out["share_value_ok"] >= 0.90 and out["share_routing_ok"] >= 0.98
    if len(val) > 2:
        A = torch.tensor([r["a"] for r in val], dtype=torch.float64)
        V = torch.tensor([r["v"] for r in val], dtype=torch.float64)
        e = (A - V).abs()
        out.update(pearson_r=float(torch.corrcoef(torch.stack([A, V]))[0, 1]),
                   slope_through_origin=float((A * V).sum() / (V * V).sum()),
                   median_abs_err=float(e.median()), q90_abs_err=float(torch.quantile(e, 0.9)))
    out["share_ok_if_value_is"] = {
        "activation_times_routing_weight": share_ok([(r["a"] * r["w"], r["v"]) for r in val]),
        "activation_without_theta": share_ok([(r["a"] * r["theta"], r["v"]) for r in val]),
        "negative_activation": share_ok([(-r["a"], r["v"]) for r in val]),
        "position_one_earlier": share_ok([(r["a_prev"], r["v"]) for r in val if r["a_prev"] is not None]),
        "position_one_later": share_ok([(r["a_next"], r["v"]) for r in val if r["a_next"] is not None]),
    }
    out["share_value_ok_where_model_routes"] = share_ok([(r["a"], r["v"]) for r in val if r["routed"]])
    return out


def main():
    from transformers import AutoConfig, AutoTokenizer
    import shutil
    import tempfile
    cfgdir = tempfile.mkdtemp(prefix="cfg_", dir=HERE)
    shutil.copy(os.path.join(HERE, "cfg_Qwen3.6-35B-A3B.json"), os.path.join(cfgdir, "config.json"))
    cfg = AutoConfig.from_pretrained(cfgdir)
    shutil.rmtree(cfgdir, ignore_errors=True)
    tok = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
    files = os.path.join(HERE, "..", "kl345", "data", "2026-11-09-082204", "files")
    dots = json.load(gzip.open(os.path.join(files, "neuron_landscape_data", "L24_E124_N345__pos.json.gz"), "rt"))["dots"]
    theta = float(json.load(gzip.open(os.path.join(files, "subcircuit_modes_data", "L24_E124_N345__pos.json.gz"), "rt"))["theta"])
    wm = json.load(open(os.path.join(HERE, "idx_bf16.json"), encoding="utf-8"))["weight_map"]
    L, E, N = 24, 124, 345

    exact = []
    for i, d in enumerate(dots):
        ids = tok.encode("".join(s for s, _ in d["t"]), add_special_tokens=False)
        if len(ids) == len(d["t"]) and [tok.decode([x]) for x in ids] == [s for s, _ in d["t"]]:
            exact.append((i, ids))
    full = [(i, ids) for i, ids in exact if dots[i]["u"] < 84]
    pool = [(i, ids) for i, ids in exact if dots[i]["u"] >= 84]
    n_crop = int(os.environ.get("N_CROPPED", "123"))
    cropped = random.Random(20261128).sample(pool, min(n_crop, len(pool)))
    jobs = [("full", i, ids, 0) for i, ids in full] + [("full_eot", i, [EOT] + ids, 1) for i, ids in full] \
        + [("cropped", i, ids, 0) for i, ids in cropped]
    print(f"Fenster exakt übersetzbar: {len(exact)} von {len(dots)} | full {len(full)}, cropped {len(cropped)}, "
          f"Texte gesamt {len(jobs)}, Token {sum(len(j[2]) for j in jobs)}", flush=True)

    skel = K.skeleton(cfg)
    needed = K.needed_names(skel)
    src = HttpWeights(wm)
    t0 = time.time()
    res = compute(skel, src.tensors, needed, [j[2] for j in jobs], L, E, N,
                  log=lambda s: print(f"{s} | geladen {src.bytes / 1e9:.1f} GB", flush=True))
    print(f"Rechnung fertig nach {time.time() - t0:.0f} s, geladen {src.bytes / 1e9:.1f} GB", flush=True)

    rows = {"full": [], "full_eot": [], "cropped": []}
    firing = {"full": [], "full_eot": [], "cropped": []}
    for (grp, i, ids, off), (act, w) in zip(jobs, res):
        d = dots[i]
        t, u, f = d["t"], d["u"], d["f"]
        a = (act / theta).tolist()
        firing[grp].append({"window": i, "model_a_at_f": a[f + off], "atlas_a": d["a"], "atlas_value_at_f": t[f][1],
                            "model_routing_weight_at_f": float(w[f + off]), "atlas_w": d["w"]})
        for j in range(u, len(t)):
            if j == f:
                continue
            p = j + off
            rows[grp].append({"window": i, "u": u, "j": j, "a": a[p], "w": float(w[p]), "routed": float(w[p]) > 0,
                              "v": t[j][1], "theta": theta,
                              "a_prev": a[p - 1] if p - 1 >= off else None, "a_next": a[p + 1] if p + 1 < len(a) else None})
    report = {"theta": theta, "groups": {g: summarize(r) for g, r in rows.items()},
              "firing_token": {g: {"n": len(v),
                                   "share_model_a_within_tol_of_atlas_a": share_ok([(x["model_a_at_f"], x["atlas_a"]) for x in v]),
                                   "pearson_r": float(torch.corrcoef(torch.tensor([[x["model_a_at_f"] for x in v],
                                                                                   [x["atlas_a"] for x in v]], dtype=torch.float64))[0, 1]) if len(v) > 2 else None,
                                   "share_routed_at_f": sum(x["model_routing_weight_at_f"] > 0 for x in v) / max(len(v), 1),
                                   "examples": v[:5]}
                               for g, v in firing.items()},
              "rows": rows}
    with open(os.path.join(HERE, "cpu_gate0_results.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    for g, s in report["groups"].items():
        print(f"\n[{g}] " + json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()
                                        if k != "share_ok_if_value_is"}, ensure_ascii=False))
        print("   andere Definitionen:", {k: round(v, 3) for k, v in s["share_ok_if_value_is"].items()})
        fs = report["firing_token"][g]
        print(f"   Feuer-Token: Modell a gegen Atlas a innerhalb Toleranz {fs['share_model_a_within_tol_of_atlas_a']:.3f}, "
              f"r {fs['pearson_r']}, Experte dort gewählt {fs['share_routed_at_f']:.3f}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
