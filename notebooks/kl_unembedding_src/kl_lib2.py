"""Kernfunktionen für den Test: ein einzelnes Neuron eines MoE-Experten ausschalten und die Wirkung messen.

Geschrieben für transformers 5.24.1, Modelltyp qwen3_5_moe (Qwen3.6-35B-A3B). Alle Zugriffe auf
Modulnamen stehen hier an einer Stelle; weichen sie ab, bricht der Code mit einer klaren Meldung ab.

Routing: RoutingControl steuert die Expertenwahl ALLER MoE-Schichten. "free" lässt sie unverändert,
"record" merkt sich die Wahl einer Referenz, "freeze" erzwingt die gemerkte Wahl (und Gewichte) in allen
Schichten. Nur so ist ausgeschlossen, dass das Ausschalten über Routing-Umschaltungen in späteren
Schichten wirkt.
"""
import math
import time
import traceback

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors import safe_open


LIB_VERSION = "2026-11-28"   # geht in die Signatur fertiger Läufe ein


# ----------------------------------------------------------------------------- Modulzugriff
def text_model(model):
    """Der Textteil (Embedding, Decoder-Schichten, Endnorm) des Modells."""
    for path in ("model.language_model", "model"):
        m = model
        for part in path.split("."):
            m = getattr(m, part, None)
            if m is None:
                break
        if m is not None and hasattr(m, "layers") and hasattr(m, "embed_tokens"):
            return m
    raise RuntimeError("Textteil nicht gefunden: Modulnamen weichen ab (geprüft mit transformers 5.24.1).")


def moe_block(model, layer):
    blk = text_model(model).layers[layer]
    if isinstance(blk, LazyLayer):
        raise RuntimeError(f"Schicht {layer} liegt in der gestreamten Zone (unter SPLIT): Eingriffe sind nur ab SPLIT möglich.")
    mlp = blk.mlp
    for attr in ("gate", "experts", "shared_expert", "shared_expert_gate"):
        if not hasattr(mlp, attr):
            raise RuntimeError(f"Schicht {layer}: MoE-Block ohne '{attr}' – der Aufbau weicht ab.")
    return mlp


def expert_mats(mlp, e):
    """Gate-, Up- und Down-Matrix des Experten e: gate [I,H], up [I,H], down [H,I].
    Im Modell liegen sie fusioniert: gate_up_proj[e] = [2I,H], erste I Zeilen = Gate, letzte I = Up."""
    down = mlp.experts.down_proj[e]
    I = down.shape[1]
    gu = mlp.experts.gate_up_proj[e]
    return gu[:I], gu[I:], down


def route(mlp, x):
    """Top-k-Routing genau wie im Modell: Softmax über alle Experten, Top-k, dann normiert."""
    logits = F.linear(x, mlp.gate.weight)
    probs = torch.softmax(logits.float(), dim=-1)
    v, i = torch.topk(probs, mlp.gate.top_k, dim=-1)
    return i, v / v.sum(-1, keepdim=True)


def neuron_act(mlp, e, n, x):
    """Aktivierung SiLU(Gate_n) * Up_n des Neurons n im Experten e, für jede Zeile von x (fp32)."""
    g, u, _ = expert_mats(mlp, e)
    gate = x.float() @ g[n].float()
    up = x.float() @ u[n].float()
    return mlp.experts.act_fn(gate) * up


def expert_acts_all(mlp, e, x):
    """Aktivierungen aller I Neuronen des Experten e für jede Zeile von x: [N, I] (fp32)."""
    g, u, _ = expert_mats(mlp, e)
    return mlp.experts.act_fn(x.float() @ g.float().T) * (x.float() @ u.float().T)


def moe_manual(mlp, x, zero_neuron=None):
    """Der MoE-Block nachgebaut aus seinen Gewichten (fp32). zero_neuron=(e,n) schaltet ein Neuron aus."""
    x = x.float()
    top_i, top_v = route(mlp, x)
    out = torch.zeros_like(x)
    for e in torch.unique(top_i).tolist():
        rows, slot = torch.where(top_i == e)
        g, u, d = expert_mats(mlp, e)
        g, u, d = g.float(), u.float(), d.float()
        h = mlp.experts.act_fn(x[rows] @ g.T) * (x[rows] @ u.T)
        if zero_neuron is not None and zero_neuron[0] == e:
            h[:, zero_neuron[1]] = 0.0
        out.index_add_(0, rows, (h @ d.T) * top_v[rows, slot, None])
    shared = torch.sigmoid(x @ mlp.shared_expert_gate.weight.float().T) * mlp.shared_expert(x)
    return out + shared


# ----------------------------------------------------------------------------- Routing
class RoutingControl:
    """Steuert die Expertenwahl aller MoE-Schichten. modes: 'free', 'record', 'freeze'."""

    def __init__(self, model):
        self.mode = "free"
        self.ref = {}        # Schicht -> (Gewichte, Indizes) der Referenz (Batchform muss gleich bleiben)
        self.last = {}       # Schicht -> (Gewichte, Indizes), so wie der Block sie benutzt hat
        self._h = []
        for L, blk in enumerate(text_model(model).layers):
            if hasattr(blk, "mlp") and hasattr(blk.mlp, "gate"):
                self._h.append(blk.mlp.gate.register_forward_hook(self._make(L)))

    def _make(self, L):
        def hook(mod, args, out):
            logits, scores, idx = out
            if self.mode == "freeze" and L in self.ref:
                rs, ri = self.ref[L]
                if rs.shape != scores.shape or ri.shape != idx.shape:
                    raise RuntimeError(f"Schicht {L}: Batchform der Referenz passt nicht ({tuple(ri.shape)} gegen {tuple(idx.shape)})")
                scores, idx = rs.to(scores.device, scores.dtype), ri.to(idx.device)
                out = (logits, scores, idx)
            if self.mode == "record":
                self.ref[L] = (scores.detach().clone(), idx.detach().clone())
            self.last[L] = (scores, idx)
            return out
        return hook

    def remove(self):
        for h in self._h:
            h.remove()

    def reset(self):
        """Grundzustand: freies Routing, keine Referenz (zum Beispiel nach einem abgebrochenen Lauf)."""
        self.mode = "free"
        self.ref.clear()


# Angemeldete NeuronHook- und BlockInput-Objekte. Die Liste bleibt erhalten, wenn diese Zelle erneut ausgeführt
# wird; sonst wäre ein hängen gebliebener Hook danach nicht mehr auffindbar.
_LIVE_HOOKS = globals().get("_LIVE_HOOKS", [])


def remove_neuron_hooks(model=None):
    """Entfernt alle noch angemeldeten NeuronHook- und BlockInput-Hooks und liefert ihre Anzahl. Mit model werden
    zusätzlich alle solchen Hooks am Modell gesucht, auch aus einer früheren Fassung dieser Bibliothek, die kein
    Verzeichnis führte. Nach einem abgebrochenen Lauf hängt ein Hook sonst weiter am Modell."""
    n = len(_LIVE_HOOKS)
    for h in list(_LIVE_HOOKS):
        h.remove()
    if model is not None:
        n += _remove_model_hooks(model, ("NeuronHook.", "BlockInput."))
    return n


def remove_routing_hooks(model):
    """Entfernt die Routing-Hooks aller RoutingControl-Objekte am Modell, auch aus einer früheren Fassung."""
    return _remove_model_hooks(model, ("RoutingControl.",))


def _remove_model_hooks(model, prefixes):
    """Entfernt an MoE-Blöcken und Routern die Hooks, deren Funktion zu einer der genannten Klassen gehört."""
    n = 0
    for blk in text_model(model).layers:
        mlp = getattr(blk, "mlp", None)
        for mod in (mlp, getattr(mlp, "gate", None)):
            if mod is None:
                continue
            for d in (mod._forward_hooks, mod._forward_pre_hooks):
                for k, fn in list(d.items()):
                    if getattr(getattr(fn, "__func__", fn), "__qualname__", "").startswith(prefixes):
                        del d[k]
                        n += 1
    return n


class BlockInput:
    """Merkt sich die Eingabe (normierte Zwischenwerte) des MoE-Blocks einer Schicht."""

    def __init__(self, model, layer):
        self.x = None
        self._h = moe_block(model, layer).register_forward_pre_hook(self._pre)
        _LIVE_HOOKS.append(self)

    def _pre(self, mod, args):
        self.x = args[0].reshape(-1, args[0].shape[-1]).detach()

    def remove(self):
        self._h.remove()
        if self in _LIVE_HOOKS:
            _LIVE_HOOKS.remove(self)


# ----------------------------------------------------------------------------- Hook
class NeuronHook:
    """Zeichnet die Aktivierung eines Neurons (Experte e, Neuron n, Schicht L) auf und/oder schaltet es aus.

    scale = 1.0 rechnet den Eingriff ohne Änderung durch (No-op-Test); scale = 0.0 schaltet das Neuron aus.
    Ausgeschaltet wird nur an den Stellen, die `mask` (bool [B,T]) angibt. Das Routing kommt aus
    RoutingControl.last (so wie der Block es benutzt hat).
    """

    def __init__(self, model, layer, e, n, routing):
        self.mlp = moe_block(model, layer)
        self.layer, self.e, self.n = layer, e, n
        self.routing = routing
        self.mask, self.scale = None, 0.0
        self.record, self.rec = False, {}
        self._h = [self.mlp.register_forward_pre_hook(self._pre),
                   self.mlp.register_forward_hook(self._post)]
        _LIVE_HOOKS.append(self)

    def remove(self):
        for h in self._h:
            h.remove()
        if self in _LIVE_HOOKS:
            _LIVE_HOOKS.remove(self)

    def _pre(self, mod, args):
        self._x = args[0]

    def _post(self, mod, args, out):
        B, T, H = self._x.shape
        x = self._x.reshape(-1, H)
        scores, idx = self.routing.last[self.layer]
        w = (scores * (idx == self.e)).sum(-1).float()           # Routing-Gewicht von e pro Token
        act = neuron_act(self.mlp, self.e, self.n, x)            # Aktivierung des Neurons pro Token
        if self.record:
            self.rec["act"] = act.reshape(B, T).detach().cpu()
            self.rec["w"] = w.reshape(B, T).detach().cpu()
        if self.mask is None:
            return out
        _, _, down = expert_mats(self.mlp, self.e)
        col = down[:, self.n].float()                            # Schreibvektor des Neurons, [H]
        coef = (1.0 - self.scale) * w * act                      # Anteil, der wegfällt (0 bei scale = 1)
        delta = (coef[:, None] * col[None, :]).reshape(B, T, H)
        m = self.mask.to(delta.device).unsqueeze(-1).to(delta.dtype)
        return out - (delta * m).to(out.dtype)


# ----------------------------------------------------------------------------- Läufe
@torch.no_grad()
def logits_of(model, ids):
    """ids [B,T] -> Logits [B,T,V]."""
    return model(input_ids=ids, use_cache=False).logits


@torch.no_grad()
def activation_at(model, hook, routing, ids):
    """Aktivierung und Routing-Gewicht des Neurons für jede Position eines einzelnen Textes (ids [T])."""
    routing.mode = "free"
    hook.mask, hook.scale, hook.record = None, 0.0, True
    try:
        model(input_ids=ids[None], use_cache=False)
    finally:
        hook.record = False
    return hook.rec["act"][0], hook.rec["w"][0]


@torch.no_grad()
def ablation_table(model, hook, routing, ids, target_ids, W=16, batch=32, freeze=None, scale=0.0):
    """Für einen Text (ids [T]) wird Zeile i mit ausgeschaltetem Neuron an Position i gerechnet.

    Referenz (Clean) und alle ausgeschalteten Läufe haben dieselbe Batchform (batch Zeilen).
    freeze='clean': das Routing aller Schichten wird aus der Referenz übernommen.
    target_ids[p] = Token nach Position p (-1 am Textende).
    Liefert:
      kl[i,p]     KL(clean_p || abl_i,p), 0 für p < i (kausal)
      lp_tgt[i,p] log-Wahrscheinlichkeit des Ziel-Tokens an Position p, Zeile i (float64)
      clean_tgt[p] dieselbe Größe im Clean-Lauf (float64)
      act[p], w[p] Aktivierung und Routing-Gewicht des Neurons im Clean-Lauf
      dmg[i]      Summe von kl[i,p] über p = i .. i+W-1 (beschreibend)
    """
    T = ids.shape[0]
    dev = ids.device
    B = max(int(batch), 1)
    full = ids[None].expand(B, -1).contiguous()
    kl = torch.zeros(T, T, dtype=torch.float64)
    lp_tgt = torch.zeros(T, T, dtype=torch.float64)
    try:
        routing.mode = "record"
        hook.mask, hook.scale, hook.record = None, 0.0, True
        clean_logits = logits_of(model, full)[0]                 # Zeile 0 = Referenz in dieser Batchform
        hook.record = False
        act, w = hook.rec["act"][0], hook.rec["w"][0]
        clean_lp = torch.log_softmax(clean_logits.double(), -1)  # float64 gegen Rundungsartefakte erster Ordnung
        tg = target_ids.clamp(min=0).to(dev)
        arangeT = torch.arange(T, device=dev)
        clean_tgt = clean_lp[arangeT, tg].cpu()
        p_clean = clean_lp.exp()
        routing.mode = "freeze" if freeze == "clean" else "free"
        for s in range(0, T, B):
            rows = list(range(s, min(T, s + B)))
            m = torch.zeros(B, T, dtype=torch.bool, device=dev)
            for b, i in enumerate(rows):
                m[b, i] = True
            hook.mask, hook.scale = m, scale
            lg = logits_of(model, full)
            for b, i in enumerate(rows):
                lp_b = torch.log_softmax(lg[b].double(), -1)
                kl[i] = ((p_clean * (clean_lp - lp_b)).sum(-1)).cpu()
                lp_tgt[i] = lp_b[arangeT, tg].cpu()
            del lg
    finally:                                                     # auch nach einem Abbruch bleibt kein Eingriff aktiv
        routing.mode = "free"
        hook.mask, hook.scale, hook.record = None, 0.0, False
    dmg = torch.stack([kl[i, i:min(i + W, T)].sum() for i in range(T)])
    return dict(kl=kl, lp_tgt=lp_tgt, clean_tgt=clean_tgt, act=act, w=w, dmg=dmg)


def term_positions(ids, family):
    """Positionen p, an denen das nächste Token zur Familie gehört (ids[p+1] in family)."""
    fam = set(family)
    return [p for p in range(len(ids) - 1) if ids[p + 1] in fam]


# ----------------------------------------------------------------------------- Gewichte (ohne Vorwärtslauf)
@torch.no_grad()
def direct_effect_columns(W_U, gain, Wd_all, family_rows):
    """Direkte Ausgabewirkung aller Neuronen einer Schicht auf das Vokabular, ohne spätere Schichten.

    W_U [V,H] Ausgabe-Matrix (nur echte Token), gain [H] = 1 + Gewicht der Endnorm, Wd_all [E,H,I] Down-Matrizen,
    family_rows Liste von Token-IDs. Liefert für jedes der E*I Neuronen (Index e*I+n):
      z_fam[c]  Mittel über die Familie von (Wirkung - Mittel über Vokabular) / Standardabweichung über Vokabular
      sd[c]     Standardabweichung der Wirkung über das Vokabular
      mean[c]   Mittel der Wirkung über das Vokabular
    """
    W = W_U.float()
    mu = W.mean(0)
    Wc = W - mu
    C = (Wc.T @ Wc) / W.shape[0]
    WK = W[family_rows] if family_rows else None
    E, H, I = Wd_all.shape
    z_fam = torch.empty(E * I)
    sd = torch.empty(E * I)
    mean_all = torch.empty(E * I)
    for e in range(E):
        M = (gain.float()[:, None] * Wd_all[e].float())
        mean_c = mu @ M
        var_c = (M * (C @ M)).sum(0)
        s_c = var_c.clamp_min(1e-30).sqrt()
        sd[e * I:(e + 1) * I] = s_c
        mean_all[e * I:(e + 1) * I] = mean_c
        if WK is not None:
            zk = ((WK @ M) - mean_c) / s_c
            z_fam[e * I:(e + 1) * I] = zk.mean(0)
    return z_fam, sd, mean_all


@torch.no_grad()
def direct_effect_vector(W_U, gain, Wd_e_n):
    """Wirkung eines einzelnen Neurons auf alle Vokabel-Token: W_U @ (gain * Schreibvektor)."""
    return W_U.float() @ (gain.float() * Wd_e_n.float())


@torch.no_grad()
def family_max_null(W_U, gain, Wd, rows_list):
    """Für jede Wortfamilie (rows_list [F,k] mit Token-IDs) das oberste Neuron der Schicht:
    Wert = Mittel der z-Werte der Familie über das Vokabular, Maximum über alle Neuronen.
    Liefert (Maximalwerte [F], Index e*I+n der Neuronen [F])."""
    W = W_U.float()
    mu = W.mean(0)
    Wc = W - mu
    C = (Wc.T @ Wc) / W.shape[0]
    E, H, I = Wd.shape
    F_, k = rows_list.shape
    Rows = W[rows_list.reshape(-1)]
    best = torch.full((F_,), -1e9)
    arg = torch.zeros(F_, dtype=torch.long)
    for e in range(E):
        M = gain.float()[:, None] * Wd[e].float()
        mean_c = mu @ M
        sd_c = (M * (C @ M)).sum(0).clamp_min(1e-30).sqrt()
        z = (((Rows @ M) - mean_c) / sd_c).reshape(F_, k, I).mean(1)
        vals, idxs = z.max(dim=1)
        upd = vals > best
        best = torch.where(upd, vals, best)
        arg = torch.where(upd, e * I + idxs, arg)
    return best, arg


def save_json(path, obj):
    """Schreibt JSON so, dass ein Abbruch keine halbe Datei hinterlässt (erst Hilfsdatei, dann umbenennen)."""
    import json
    import os
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load_json(path):
    """Liest eine JSON-Datei; None, wenn sie fehlt oder nicht lesbar ist."""
    import json
    import os
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def percentile_rank(values, x):
    """Anteil der Werte in values, die kleiner oder gleich x sind (0..1)."""
    v = torch.as_tensor(values).float().flatten()
    return float((v <= x).float().mean())


# ----------------------------------------------------------------------------- Statistik
def paired_bootstrap(d_by_pair, n_boot=5000, seed=1, lo_q=0.0125, hi_q=0.9875):
    """Mittelwert der Paardifferenzen mit Bootstrap über Paare. d_by_pair: Liste je Paar (Liste von Werten).
    Gibt (Schätzer, untere Grenze, obere Grenze) zum Niveau hi_q - lo_q."""
    import random
    per = [sum(v) / len(v) for v in d_by_pair if len(v)]
    est = sum(per) / len(per)
    rng = random.Random(seed)
    n = len(per)
    boots = []
    for _ in range(n_boot):
        boots.append(sum(per[rng.randrange(n)] for _ in range(n)) / n)
    boots.sort()
    return est, boots[int(lo_q * n_boot)], boots[min(n_boot - 1, int(hi_q * n_boot))]


def calibration_fit(rows, crop_u=84):
    """Gate 0b (nachträglich festgelegt am 28.11.2026): Übereinstimmung nur auf Fenstern, die am Textanfang beginnen.

    Der Atlas zeigt je Feuerung bis zu 100 Token davor; abgeschnittene Fenster haben u = crop_u und lassen sich ohne den
    Text davor nicht nachrechnen. rows: Einträge mit a (Modell, in theta), v (Atlas-Wert oder None), routed (Modell
    wählt den Experten), pos (j - u) und left (j). Liefert n_slots, n_valued, pearson_r, slope_through_origin und
    share_routing_ok (None, wo nicht berechenbar)."""
    sel = [r for r in rows if r["left"] - r["pos"] < crop_u]
    val = [r for r in sel if r["v"] is not None]
    out = {"n_slots": len(sel), "n_valued": len(val), "pearson_r": None, "slope_through_origin": None,
           "share_routing_ok": None}
    if sel:
        out["share_routing_ok"] = sum(r["routed"] == (r["v"] is not None) for r in sel) / len(sel)
    if len(val) > 2:
        A = torch.tensor([r["a"] for r in val], dtype=torch.float64)
        V = torch.tensor([r["v"] for r in val], dtype=torch.float64)
        r_ = float(torch.corrcoef(torch.stack([A, V]))[0, 1])
        s_ = float((A * V).sum() / (V * V).sum().clamp_min(1e-30))
        out["pearson_r"] = r_ if math.isfinite(r_) else None
        out["slope_through_origin"] = s_ if math.isfinite(s_) else None
    return out


def calibration_b_ok(fit, r_min=0.99, slope=(0.95, 1.05), route_min=0.98):
    """Entscheidung von Gate 0b aus calibration_fit."""
    return bool(fit["pearson_r"] is not None and fit["slope_through_origin"] is not None
                and fit["share_routing_ok"] is not None and fit["pearson_r"] >= r_min
                and slope[0] <= fit["slope_through_origin"] <= slope[1] and fit["share_routing_ok"] >= route_min)


def naive_kl_f32(logits_p, logits_q):
    """KL(p||q) mit dem naiven Ansatz in float32: Wahrscheinlichkeiten per Softmax, dann Summe p*(log p - log q)."""
    p = torch.softmax(logits_p.float(), -1)
    q = torch.softmax(logits_q.float(), -1)
    return (p * (torch.log(p) - torch.log(q))).sum(-1)


def stable_kl_f64(logits_p, logits_q):
    """KL(p||q) aus den Logits in float64 mit log_softmax (ohne Auslöschung)."""
    lp = torch.log_softmax(logits_p.double(), -1)
    lq = torch.log_softmax(logits_q.double(), -1)
    return (lp.exp() * (lp - lq)).sum(-1)


# ----------------------------------------------------------------------------- Safetensors per HTTP-Bereich
def read_safetensors_tensors(url, names):
    """Lädt einzelne Tensoren aus einer .safetensors-Datei über HTTP-Bereichsanfragen (ohne die ganze Datei)."""
    import json
    import urllib.request

    def get(a, b):
        req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()

    n = int.from_bytes(get(0, 7), "little")
    header = json.loads(get(8, 8 + n - 1))
    base = 8 + n
    out = {}
    dtypes = {"BF16": torch.bfloat16, "F32": torch.float32, "F16": torch.float16, "F8_E4M3": torch.float8_e4m3fn}
    for name in names:
        info = header[name]
        a, b = info["data_offsets"]
        buf = bytearray(get(base + a, base + b - 1))
        t = torch.frombuffer(buf, dtype=dtypes[info["dtype"]]).reshape(info["shape"]).clone()
        out[name] = t
    return out


# ----------------------------------------------------------------------------- Schichtweises Laden
# Das BF16-Modell (rund 70 GB) passt nicht auf eine A100 mit 40 GB. Die oberen Schichten (ab SPLIT, Standard 21)
# bleiben dauerhaft auf der GPU (rund 32 GiB). Die unteren Schichten werden für eine Gruppe von Texten einmal
# nacheinander geladen; ihre Ausgabe, also die Eingabe der Schicht SPLIT, wird pro Text gespeichert. Danach
# brauchen alle Läufe nur noch die oberen Schichten.
# Gerechnet wird mit dem Modellcode von transformers. Untere Schichten und Embedding sind Platzhalter. Für die
# Vorberechnung zeichnen die Platzhalter auf, womit das Modell jede untere Schicht aufruft; danach wird jede
# untere Schicht einmal geladen und rechnet alle Textblöcke. In den Läufen reichen die Platzhalter die
# gespeicherte Eingabe durch. Die Ergebnisse sind dieselben wie im vollen Modell (tiny_stream_test.py).


class _StopAfterSplit(Exception):
    """Bricht die Vorberechnung ab, sobald die Eingabe der Schicht SPLIT vorliegt."""


def progress(iterable, desc, show=True):
    """Fortschrittsanzeige, falls gewünscht und tqdm vorhanden ist."""
    if not show:
        return iterable
    try:
        from tqdm.auto import tqdm
        return tqdm(iterable, desc=desc)
    except Exception:
        return iterable


class LazyLayer(nn.Module):
    """Platzhalter für eine untere Schicht. mode 'pass': durchreichen. mode 'capture': durchreichen und merken,
    womit das Modell die Schicht aufruft. mode 'stream': Gewichte laden, rechnen, wieder freigeben."""

    def __init__(self, streamer, idx):
        super().__init__()
        self.streamer = streamer
        self.idx = idx
        self.mode = "pass"

    def forward(self, hidden_states, **kwargs):
        if self.mode == "pass":
            return hidden_states
        if self.mode == "capture":
            self.streamer._capture(self.idx, hidden_states, kwargs)
            return hidden_states
        layer = self.streamer.build_layer(self.idx)
        out = layer(hidden_states, **kwargs)
        del layer
        return out


class StreamEmbed(nn.Module):
    """Platzhalter für das Embedding. mode 'embed': echtes Embedding. mode 'inject': gespeicherte Eingabe."""

    def __init__(self, real, streamer):
        super().__init__()
        self.real = real
        self.streamer = streamer
        self.mode = "embed"

    def forward(self, input_ids):
        if self.mode == "embed":
            return self.real(input_ids)
        return self.streamer.injected(input_ids)


def skeleton(config):
    """Das vollständige Modell ohne Gewichte (Meta-Gerät). Liefert Namen, Formen und Aufbau."""
    from accelerate import init_empty_weights
    from transformers.models.qwen3_5_moe.modeling_qwen3_5_moe import Qwen3_5MoeForConditionalGeneration
    with init_empty_weights():
        m = Qwen3_5MoeForConditionalGeneration(config)
    return m.eval()


def needed_names(skel):
    """Namen der Gewichte, die Textteil und Ausgabe brauchen (ohne Bildmodul und Mehrfach-Vorhersage)."""
    return [n for n, _ in skel.named_parameters() if n.startswith(("model.language_model.", "lm_head."))]


class LayerStreamer:
    """Lädt Gewichte aus lokalen Dateien, hält die oberen Schichten auf dem Gerät und rechnet die unteren
    Schichten für Texte vor.

    skel:     Modell auf dem Meta-Gerät (skeleton(config)).
    shard_of: Gewichtsname -> lokaler Pfad der .safetensors-Datei, die ihn enthält.
    split:    erste Schicht, die dauerhaft auf dem Gerät liegt (Eingriffe nur ab hier).
    chunk:    Anzahl Texte je Block (ein Block wird gemeinsam gerechnet).

    Einstellbar nach dem Anlegen:
    layer_major   True (Standard): jede untere Schicht wird je ensure-Aufruf einmal geladen und rechnet alle
                  Blöcke. False: jeder Block lädt die unteren Schichten selbst (ursprünglicher Weg).
    state_budget  Bytes an Zwischenzuständen, die ein Durchlauf auf dem Gerät halten darf (Standard 2 GiB).
    verbose       Fortschritt und Zeiten ausgeben.
    """

    def __init__(self, skel, shard_of, device, dtype, split, chunk=64):
        self.layer_major = True
        self.state_budget = 2 * 2**30
        self.verbose = True
        self.stats = {"layer_loads": 0, "texts": 0}     # geladene untere Schichten, vorberechnete Texte
        self._rec = None
        self.model = skel
        self.text = text_model(skel)
        self.tc = skel.config.text_config
        self.shard_of = shard_of
        dev = torch.device(device)
        if dev.type == "cuda" and dev.index is None:
            dev = torch.device("cuda", torch.cuda.current_device())
        self.device = dev
        self.dtype = dtype
        self.split = split
        self.chunk = chunk
        self.pad_id = 0
        self.cache = {}     # Tupel der Token-IDs -> Eingabe der Schicht SPLIT, Form [1, T, H]
        n = len(self.text.layers)
        if not (1 <= split < n):
            raise ValueError(f"split muss zwischen 1 und {n - 1} liegen, ist {split}")
        self.embed = StreamEmbed(self.text.embed_tokens, self)
        self.text.embed_tokens = self.embed
        self.lazy = []
        for i in range(split):
            lz = LazyLayer(self, i)
            self.text.layers[i] = lz
            self.lazy.append(lz)
        self._materialize(self.embed.real, "model.language_model.embed_tokens.")
        self._materialize(self.text.norm, "model.language_model.norm.")
        self._materialize(skel.lm_head, "lm_head.")
        self.text.rotary_emb.to(self.device)          # nicht persistente Puffer (Rotation) liegen auf dem Gerät
        for i in progress(range(split, n), "Obere Schichten laden", self.verbose and self.device.type == "cuda"):
            self.text.layers[i] = self.build_layer(i)
        self._modes("pass", "inject")

    def get(self, raw):
        """Ein Gewicht unter seinem Originalnamen, auf das Gerät und in den Zieltyp gebracht."""
        if raw not in self.shard_of:
            raise KeyError(f"Gewicht nicht in den Dateien gefunden: {raw}")
        with safe_open(self.shard_of[raw], framework="pt", device=str(self.device)) as f:
            t = f.get_tensor(raw)
        return t.to(self.dtype) if t.is_floating_point() else t

    def _materialize(self, module, prefix):
        sd = {k: self.get(prefix + k) for k in module.state_dict().keys()}
        module.load_state_dict(sd, strict=True, assign=True)
        module.requires_grad_(False)

    def build_layer(self, i):
        """Schicht i mit allen Gewichten auf dem Gerät."""
        from accelerate import init_empty_weights
        from transformers.models.qwen3_5_moe.modeling_qwen3_5_moe import Qwen3_5MoeDecoderLayer
        with init_empty_weights():
            layer = Qwen3_5MoeDecoderLayer(self.tc, i)
        self._materialize(layer, f"model.language_model.layers.{i}.")
        if i < self.split:
            self.stats["layer_loads"] += 1
        return layer.to(self.device).eval()

    def _modes(self, lazy, embed):
        """Platzhalter umschalten. Läufe: ('pass', 'inject'). Vorberechnung: ('capture' oder 'stream', 'embed')."""
        for lz in self.lazy:
            lz.mode = lazy
        self.embed.mode = embed

    def injected(self, input_ids):
        """Eingabe der Schicht SPLIT für einen vorberechneten Text, für alle Zeilen des Batches."""
        if (input_ids != input_ids[0:1]).any():
            raise RuntimeError("Alle Zeilen eines Laufs müssen denselben Text enthalten.")
        key = tuple(int(x) for x in input_ids[0].tolist())
        h = self.cache.get(key)
        if h is None:
            raise RuntimeError(f"Text mit {len(key)} Token ist nicht vorberechnet: vorher STREAM.ensure([...]) "
                               "mit genau diesem Text aufrufen.")
        return h.expand(input_ids.shape[0], -1, -1).contiguous()

    @torch.no_grad()
    def ensure(self, seqs):
        """Berechnet die Eingabe der Schicht SPLIT für alle fehlenden Texte. Liefert die Anzahl neuer Texte.

        Die Texte werden nach Länge sortiert und in Blöcke zu chunk gefasst. Mit layer_major (Standard) wird jede
        untere Schicht für alle Blöcke nur einmal geladen; passen die Zwischenzustände nicht ins Budget, gibt es
        mehrere Durchläufe. Gespeichert wird erst am Ende eines Durchlaufs."""
        todo, seen = [], set()
        for s in seqs:
            key = tuple(int(x) for x in s)
            if key and key not in self.cache and key not in seen:
                seen.add(key)
                todo.append(key)
        if not todo:
            return 0
        t0, loads0 = time.time(), self.stats["layer_loads"]
        todo.sort(key=len)                              # ähnliche Längen zusammen: weniger Auffüllung
        blocks = [todo[c0:c0 + self.chunk] for c0 in range(0, len(todo), self.chunk)]
        if self.layer_major:
            per_token = self.tc.hidden_size * torch.finfo(self.dtype).bits // 8
            sweeps, cur, cur_bytes = [], [], 0
            for keys in blocks:
                need = len(keys) * len(keys[-1]) * per_token      # der längste Text steht am Blockende
                if cur and cur_bytes + need > self.state_budget:
                    sweeps.append(cur)
                    cur, cur_bytes = [], 0
                cur.append(keys)
                cur_bytes += need
            sweeps.append(cur)
            for j, sw in enumerate(sweeps):
                part = f" (Teil {j + 1} von {len(sweeps)})" if len(sweeps) > 1 else ""
                self._sweep(sw, f"Vorberechnung: {sum(len(b) for b in sw)} Texte{part}, untere Schichten")
        else:
            for keys in progress(blocks, f"Vorberechnung: {len(todo)} Texte, blockweise", self.verbose):
                self._prefill(keys)
        if self.verbose:
            print(f"Vorberechnung fertig: {len(todo)} Texte in {len(blocks)} Blöcken, "
                  f"{self.stats['layer_loads'] - loads0} Ladevorgänge unterer Schichten, {time.time() - t0:.0f} s", flush=True)
        return len(todo)

    def _padded(self, keys):
        """Token-IDs eines Blocks als [Anzahl, größte Länge], rechts aufgefüllt. Auffüllen und die anderen Zeilen
        ändern die Werte der echten Positionen nur im Rahmen der Rundung (kausale Rechnung, Zeilen unabhängig)."""
        ids = torch.full((len(keys), max(len(k) for k in keys)), self.pad_id, dtype=torch.long)
        for r, k in enumerate(keys):
            ids[r, :len(k)] = torch.tensor(k, dtype=torch.long)
        return ids.to(self.device)

    @torch.no_grad()
    def _sweep(self, blocks, desc):
        """Ein Durchlauf über mehrere Blöcke: Jede untere Schicht wird einmal geladen und rechnet alle Blöcke.
        Womit das Modell eine Schicht aufruft (Positionen, Masken), wird vorher je Block aufgezeichnet; die
        Aufrufe sind dieselben wie im Vorwärtslauf des Modells."""
        plans, layer = [], None
        try:
            for keys in blocks:
                plans.append(self._record_block(keys))
            for i in progress(range(self.split), desc, self.verbose):
                layer = self.build_layer(i)
                for p in plans:
                    p["h"] = layer(p["h"], **p["kw"][i])
                layer = None
            for p in plans:
                for r, k in enumerate(p["keys"]):
                    self.cache[k] = p["h"][r:r + 1, :len(k)].clone()
                self.stats["texts"] += len(p["keys"])
                p["h"] = None                           # Blockzustand sofort freigeben
        except BaseException as ex:
            traceback.clear_frames(ex.__traceback__)    # die Fehlermeldung soll keine geladene Schicht festhalten
            raise
        finally:                                        # auch nach einem Abbruch bleibt nichts auf dem Gerät hängen
            layer = None
            for p in plans:
                p.clear()
            plans.clear()

    @torch.no_grad()
    def _record_block(self, keys):
        """Lässt das Modell einen Block bis zur Schicht SPLIT durchlaufen, ohne die unteren Schichten zu rechnen.
        Aufgezeichnet werden die Einbettung und die Argumente, mit denen jede untere Schicht aufgerufen würde."""
        ids = self._padded(keys)
        self._rec = {"h": None, "kw": {}}

        def stop(mod, args):
            raise _StopAfterSplit()

        hook = None
        try:
            hook = self.text.layers[self.split].register_forward_pre_hook(stop)
            self._modes("capture", "embed")
            self.text(input_ids=ids, use_cache=False)
        except _StopAfterSplit:
            pass
        except BaseException as ex:
            traceback.clear_frames(ex.__traceback__)
            raise
        finally:
            if hook is not None:
                hook.remove()
            self._modes("pass", "inject")
            rec, self._rec = self._rec, None
        if rec["h"] is None or sorted(rec["kw"]) != list(range(self.split)):
            raise RuntimeError("Vorberechnung: Die Aufzeichnung der unteren Schichten ist unvollständig.")
        return {"keys": keys, "h": rec["h"], "kw": rec["kw"]}

    def _capture(self, idx, hidden_states, kwargs):
        if idx == 0:
            self._rec["h"] = hidden_states.detach()
        self._rec["kw"][idx] = kwargs

    @torch.no_grad()
    def _prefill(self, keys):
        """Blockweise Vorberechnung (layer_major = False): Das Modell rechnet einen Block bis zur Schicht SPLIT,
        jeder Platzhalter lädt seine Schicht für diesen einen Aufruf."""
        got = {}

        def grab(mod, args):
            got["h"] = args[0].detach()
            raise _StopAfterSplit()

        ids = self._padded(keys)
        hook = None
        try:
            hook = self.text.layers[self.split].register_forward_pre_hook(grab)
            self._modes("stream", "embed")
            self.text(input_ids=ids, use_cache=False)
        except _StopAfterSplit:
            pass
        except BaseException as ex:
            traceback.clear_frames(ex.__traceback__)    # die Fehlermeldung soll keine geladene Schicht festhalten
            raise
        finally:
            if hook is not None:
                hook.remove()
            self._modes("pass", "inject")
        H = got["h"]
        for r, k in enumerate(keys):
            self.cache[k] = H[r:r + 1, :len(k)].clone()
        self.stats["texts"] += len(keys)
        del got, H

    def gpu_gib(self):
        return torch.cuda.memory_allocated(self.device) / 2**30 if self.device.type == "cuda" else 0.0


def check_session(rc, streamer):
    """Bricht mit einer klaren Meldung ab, wenn Routing-Steuerung oder Modell noch aus einer früheren Fassung oder
    einer früheren Ausführung der Bibliothekszelle stammen."""
    if not isinstance(rc, RoutingControl) or not isinstance(streamer, LayerStreamer):
        raise RuntimeError("Bitte zuerst die Modellzelle erneut ausführen. Sie lädt das Modell nicht neu, "
                           "sondern übernimmt es für die jetzige Fassung der Bibliothek.")


def adopt_streamer(st):
    """Bringt einen bereits geladenen LayerStreamer auf den Stand der jetzt definierten Klassen, zum Beispiel
    nachdem die Bibliothekszelle erneut ausgeführt wurde. Gewichte und gespeicherte Eingaben bleiben; es wird
    nichts neu geladen."""
    st.__class__ = LayerStreamer
    for lz in st.lazy:
        lz.__class__ = LazyLayer
    st.embed.__class__ = StreamEmbed
    for name, default in (("layer_major", True), ("state_budget", 2 * 2**30), ("verbose", True), ("_rec", None)):
        if not hasattr(st, name):
            setattr(st, name, default)
    if not hasattr(st, "stats"):
        st.stats = {"layer_loads": 0, "texts": 0}
    st._modes("pass", "inject")
    return st
