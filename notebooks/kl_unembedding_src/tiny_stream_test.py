"""Prüft das schichtweise Laden (kl_lib2.LayerStreamer) gegen das volle Modell an winzigen Modellen.

Ein Checkpoint wird aus einem winzigen Modell geschrieben (Originalnamen, mehrere Dateien mit Index), dann
wird er wie beim echten Modell über skeleton() + LayerStreamer geladen und mit dem vollen Modell verglichen.
"""
import copy
import json
import os
import sys
import tempfile

import torch
from safetensors.torch import save_file
from transformers import Qwen3_5MoeConfig, Qwen3_5MoeForConditionalGeneration
from transformers.models.qwen3_5_moe.configuration_qwen3_5_moe import Qwen3_5MoeTextConfig, Qwen3_5MoeVisionConfig

import kl_lib2 as K

torch.set_num_threads(4)
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))
    if not ok:
        failures.append(name)


def make_full(layer_types, seed=0):
    torch.manual_seed(seed)
    tc = Qwen3_5MoeTextConfig(
        vocab_size=256, hidden_size=64, num_hidden_layers=len(layer_types), layer_types=layer_types,
        num_attention_heads=4, num_key_value_heads=2, head_dim=64,
        linear_num_key_heads=2, linear_num_value_heads=4, linear_key_head_dim=16, linear_value_head_dim=16,
        linear_conv_kernel_dim=4, num_experts=8, num_experts_per_tok=2, moe_intermediate_size=16,
        shared_expert_intermediate_size=16,
        rope_parameters={"rope_type": "default", "rope_theta": 10000.0, "partial_rotary_factor": 0.25,
                         "mrope_section": [3, 3, 2], "mrope_interleaved": True})
    vc = Qwen3_5MoeVisionConfig(depth=1, hidden_size=32, intermediate_size=64, num_heads=2, out_hidden_size=64)
    m = Qwen3_5MoeForConditionalGeneration(Qwen3_5MoeConfig(text_config=tc, vision_config=vc)).eval().float()
    with torch.no_grad():
        for _, p in m.named_parameters():
            if p.dim() >= 2:
                p.normal_(std=0.1)
    return m


def save_checkpoint(model, d, n_shards=3, dtype=None):
    """Schreibt die Gewichte wie der Hub-Checkpoint: mehrere .safetensors-Dateien und ein Index mit weight_map."""
    sd = {}
    for k, v in model.state_dict().items():
        v = v.detach().contiguous().clone()
        if dtype is not None and v.is_floating_point():
            v = v.to(dtype)
        sd[k] = v
    names = sorted(sd)
    wm = {}
    for s in range(n_shards):
        part = names[s::n_shards]
        fn = f"model-{s + 1:05d}-of-{n_shards:05d}.safetensors"
        save_file({k: sd[k] for k in part}, os.path.join(d, fn))
        for k in part:
            wm[k] = fn
    with open(os.path.join(d, "model.safetensors.index.json"), "w", encoding="utf-8") as fh:
        json.dump({"metadata": {}, "weight_map": wm}, fh)
    return {k: os.path.join(d, v) for k, v in wm.items()}


def best_neuron(full, L, ids, tgt, RC):
    """Neuron mit der größten Wirkung (max KL) im vollen Modell; damit ist der Vergleich nicht leer."""
    mlp = K.moe_block(full, L)
    E, I = mlp.experts.down_proj.shape[0], mlp.experts.down_proj.shape[2]
    best = (-1.0, 0, 0)
    for e in range(E):
        for n in range(I):
            h = K.NeuronHook(full, L, e, n, RC)
            tab = K.ablation_table(full, h, RC, ids, tgt, W=8, batch=4)
            h.remove()
            v = float(tab["kl"].max())
            if v > best[0]:
                best = (v, e, n)
    return best


def run_case(layer_types, split, label):
    full = make_full(layer_types)
    n_layers = len(layer_types)
    resident = list(range(split, n_layers))
    torch.manual_seed(7)
    ids = torch.randint(0, 256, (10,))
    tgt = torch.cat([ids[1:], torch.tensor([-1])])
    RCf = K.RoutingControl(full)
    with tempfile.TemporaryDirectory() as d:
        shard_of = save_checkpoint(full, d)
        S = K.LayerStreamer(K.skeleton(full.config), shard_of, "cpu", torch.float32, split, chunk=3)
        S.verbose = False
        model = S.model
        ft, st = K.text_model(full), K.text_model(model)

        # S0: jede Schicht, Einbettung, Endnorm und Ausgabe sind gleich den Gewichten des vollen Modells
        ok = True
        for i in range(n_layers):
            layer = S.build_layer(i) if i < split else st.layers[i]
            ref_sd, got = ft.layers[i].state_dict(), layer.state_dict()
            ok &= set(ref_sd) == set(got) and all(torch.equal(ref_sd[k], got[k]) for k in ref_sd)
        ok &= torch.equal(st.embed_tokens.real.weight, ft.embed_tokens.weight)
        ok &= torch.equal(st.norm.weight, ft.norm.weight)
        ok &= torch.equal(model.lm_head.weight, full.lm_head.weight)
        check(f"{label} S0 weights equal the full model (split {split})", bool(ok))

        # S1: Logits für einen Text
        with torch.no_grad():
            S.ensure([ids.tolist()])
            ref = full(input_ids=ids[None], use_cache=False).logits
            got = model(input_ids=ids[None], use_cache=False).logits
        d1 = float((ref - got).abs().max())
        check(f"{label} S1 logits equal, one text", d1 < 1e-5, f"max diff {d1:.1e}")

        # S2: mehrere Texte verschiedener Länge, in Blöcken zu 3 vorberechnet
        lens = [5, 9, 12, 7, 12]
        seqs = [torch.randint(0, 256, (L,)) for L in lens]
        n_new = S.ensure([s.tolist() for s in seqs])
        worst = 0.0
        with torch.no_grad():
            for s in seqs:
                r_ = full(input_ids=s[None], use_cache=False).logits
                g_ = model(input_ids=s[None], use_cache=False).logits
                worst = max(worst, float((r_ - g_).abs().max()))
        check(f"{label} S2 logits equal, lengths {lens} in blocks of 3", n_new == len(seqs) and worst < 1e-4,
              f"new texts {n_new}, max diff {worst:.1e}")

        # S3: Ablation an jeder oberen Schicht, stärkstes Neuron des vollen Modells
        RCs = K.RoutingControl(model)
        for L in resident:
            v, e, n = best_neuron(full, L, ids, tgt, RCf)
            hf = K.NeuronHook(full, L, e, n, RCf)
            hs = K.NeuronHook(model, L, e, n, RCs)
            tf_ = K.ablation_table(full, hf, RCf, ids, tgt, W=8, batch=4)
            ts_ = K.ablation_table(model, hs, RCs, ids, tgt, W=8, batch=4)
            hf.remove()
            hs.remove()
            dlp = float((tf_["lp_tgt"] - ts_["lp_tgt"]).abs().max())
            dkl = float((tf_["kl"] - ts_["kl"]).abs().max())
            check(f"{label} S3 ablation layer {L} neuron E{e}N{n} equals the full model",
                  dlp < 1e-4 and dkl < 1e-6 and v > 1e-6,
                  f"max dlogp {dlp:.1e}, max dKL {dkl:.1e}, effect max KL {v:.1e}")

        # S4: No-op über den Rechenweg bleibt bei null
        hs = K.NeuronHook(model, resident[0], 0, 0, RCs)
        tab1 = K.ablation_table(model, hs, RCs, ids, tgt, W=8, batch=4, scale=1.0)
        hs.remove()
        check(f"{label} S4 no-op (scale 1.0) gives KL < 1e-9", float(tab1["kl"].abs().max()) < 1e-9,
              f"max {float(tab1['kl'].abs().max()):.1e}")

        # S5: Einfrieren des Routings hält die Expertenwahl in allen oberen Schichten
        ids2 = torch.randint(0, 256, (10,))          # gleiche Länge wie die Referenz (Batchform muss passen)
        S.ensure([ids2.tolist()])
        with torch.no_grad():
            RCs.mode = "record"
            model(input_ids=ids[None], use_cache=False)
            ref_idx = {L: RCs.ref[L][1].clone() for L in RCs.ref}
            RCs.mode = "freeze"
            model(input_ids=ids2[None], use_cache=False)
            RCs.mode = "free"
        same = sorted(ref_idx) == resident and all(torch.equal(RCs.last[L][1], ref_idx[L]) for L in resident)
        check(f"{label} S5 freeze holds the expert choice in every resident layer", same, f"layers {sorted(ref_idx)}")

        # S6: ein nicht vorberechneter Text wird abgelehnt (kein stiller Fehler)
        try:
            with torch.no_grad():
                model(input_ids=torch.randint(0, 256, (1, 11)), use_cache=False)
            raised = False
        except RuntimeError:
            raised = True
        check(f"{label} S6 text that was not prepared is refused", raised)

        # S7: Eingriff in einer gestreamten Schicht wird abgelehnt
        try:
            K.NeuronHook(model, 0, 0, 0, RCs)
            raised = False
        except RuntimeError:
            raised = True
        check(f"{label} S7 hook in a streamed layer is refused", raised)
        RCs.remove()
    RCf.remove()


def run_bf16(layer_types, split, label):
    """Checkpoint in BF16 geschrieben, in FP32 geladen: gleich dem vollen Modell mit gerundeten Gewichten."""
    full = make_full(layer_types, seed=3)
    ref = copy.deepcopy(full)
    with torch.no_grad():
        for p in ref.parameters():
            p.copy_(p.to(torch.bfloat16).to(torch.float32))
    with tempfile.TemporaryDirectory() as d:
        shard_of = save_checkpoint(full, d, dtype=torch.bfloat16)
        S = K.LayerStreamer(K.skeleton(full.config), shard_of, "cpu", torch.float32, split, chunk=8)
        S.verbose = False
        ids = torch.randint(0, 256, (10,))
        with torch.no_grad():
            S.ensure([ids.tolist()])
            r_ = ref(input_ids=ids[None], use_cache=False).logits
            g_ = S.model(input_ids=ids[None], use_cache=False).logits
        d1 = float((r_ - g_).abs().max())
        check(f"{label} D bf16 checkpoint loaded as float32 equals the rounded full model", d1 < 1e-5, f"max diff {d1:.1e}")


def run_sweep_tests(layer_types, split, label):
    """Der schichtweise Durchlauf (layer_major) gegen den blockweisen Weg; Ladevorgänge; Abbruch; Aufräumen."""
    full = make_full(layer_types, seed=11)
    torch.manual_seed(13)
    lens = [5, 9, 12, 7, 12, 3, 12]
    seqs = [torch.randint(0, 256, (L,)).tolist() for L in lens]
    blocks = -(-len(seqs) // 3)
    with tempfile.TemporaryDirectory() as d:
        shard_of = save_checkpoint(full, d)

        def fresh(layer_major):
            S = K.LayerStreamer(K.skeleton(full.config), shard_of, "cpu", torch.float32, split, chunk=3)
            S.verbose = False
            S.layer_major = layer_major
            return S

        # S8: beide Wege liefern dieselben gespeicherten Eingaben
        A, B = fresh(True), fresh(False)
        nA, nB = A.ensure(seqs), B.ensure(seqs)
        same = set(A.cache) == set(B.cache) and all(torch.equal(A.cache[k], B.cache[k]) for k in A.cache)
        worst = max(float((A.cache[k] - B.cache[k]).abs().max()) for k in A.cache)
        check(f"{label} S8 sweep equals the blockwise path bit for bit", nA == nB == len(seqs) and same,
              f"max diff {worst:.1e}")

        # S9: Ladevorgänge der unteren Schichten
        check(f"{label} S9 sweep loads each lower layer once, blockwise once per block",
              A.stats["layer_loads"] == split and B.stats["layer_loads"] == blocks * split,
              f"sweep {A.stats['layer_loads']}, blockwise {B.stats['layer_loads']} (split {split}, blocks {blocks})")

        # S9b: Logits über den schichtweisen Weg gleich dem vollen Modell (mehrere Blöcke)
        worst = 0.0
        with torch.no_grad():
            for s in seqs:
                t = torch.tensor(s)[None]
                worst = max(worst, float((full(input_ids=t, use_cache=False).logits
                                          - A.model(input_ids=t, use_cache=False).logits).abs().max()))
        check(f"{label} S9b logits after a sweep equal the full model", worst < 1e-4, f"max diff {worst:.1e}")

        # S10: Abbruch mitten im Durchlauf (wie Strg-C in der letzten unteren Schicht)
        C = fresh(True)
        orig_build = C.build_layer

        def failing(i):
            if i == split - 1:
                raise KeyboardInterrupt()
            return orig_build(i)

        C.build_layer = failing
        try:
            C.ensure(seqs)
            interrupted = False
        except KeyboardInterrupt:
            interrupted = True
        st = K.text_model(C.model)
        clean = (interrupted and len(C.cache) == 0 and C._rec is None
                 and all(lz.mode == "pass" for lz in C.lazy) and C.embed.mode == "inject"
                 and len(st.layers[split]._forward_pre_hooks) == 0)
        C.build_layer = orig_build
        n_again = C.ensure(seqs)
        same2 = all(torch.equal(C.cache[k], A.cache[k]) for k in A.cache)
        check(f"{label} S10 an interrupted sweep leaves the streamer clean; a second call completes",
              clean and n_again == len(seqs) and same2)

        # S11: kleines Budget -> ein Durchlauf je Block, gleiche Ergebnisse
        D = fresh(True)
        D.state_budget = 1
        D.ensure(seqs)
        same3 = all(torch.equal(D.cache[k], A.cache[k]) for k in A.cache)
        check(f"{label} S11 a tiny state budget gives one sweep per block with equal results",
              same3 and D.stats["layer_loads"] == blocks * split, f"loads {D.stats['layer_loads']}")

        # S12: abgebrochene Ablation hinterlässt keinen aktiven Eingriff
        ids = torch.tensor(seqs[2])
        tgt = torch.cat([ids[1:], torch.tensor([-1])])
        RC = K.RoutingControl(A.model)
        hook = K.NeuronHook(A.model, split, 0, 0, RC)
        calls = {"n": 0}
        orig_logits = K.logits_of

        def flaky(model, x):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("Abbruch im Test")
            return orig_logits(model, x)

        K.logits_of = flaky
        try:
            K.ablation_table(A.model, hook, RC, ids, tgt, W=8, batch=4, freeze="clean")
            raised = False
        except RuntimeError:
            raised = True
        finally:
            K.logits_of = orig_logits
        check(f"{label} S12 an aborted ablation leaves no intervention active",
              raised and RC.mode == "free" and hook.mask is None and hook.record is False)

        # S13: vergessene Hooks lassen sich gesammelt entfernen
        mlp = K.moe_block(A.model, split)
        K.BlockInput(A.model, split)
        hook.mask = torch.ones(1, len(ids), dtype=torch.bool)
        n_before = len(mlp._forward_hooks) + len(mlp._forward_pre_hooks)
        removed = K.remove_neuron_hooks()
        n_after = len(mlp._forward_hooks) + len(mlp._forward_pre_hooks)
        check(f"{label} S13 remove_neuron_hooks clears every registered hook",
              removed == 2 and n_before == 3 and n_after == 0 and len(K._LIVE_HOOKS) == 0,
              f"hooks before {n_before}, after {n_after}, removed {removed}")
        RC.remove()


def run_adopt_tests(layer_types, split, label):
    """Ein geladener LayerStreamer wird von neu definierten Klassen übernommen: einmal vom selben Code (die
    Bibliothekszelle wurde erneut ausgeführt), einmal von der Fassung, mit der der erste echte Lauf lief
    (snap/kl_lib2.py). Nichts wird neu geladen, gespeicherte Eingaben bleiben, neue Texte rechnen richtig."""
    import importlib.util

    def load_module(path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    here = os.path.dirname(os.path.abspath(__file__))
    full = make_full(layer_types, seed=21)
    torch.manual_seed(23)
    seqs = [torch.randint(0, 256, (L,)).tolist() for L in (6, 11, 9, 11)]
    with tempfile.TemporaryDirectory() as d:
        shard_of = save_checkpoint(full, d)
        for n_src, (tag, path) in enumerate((("same code executed again", os.path.join(here, "kl_lib2.py")),
                                             ("version of the first real run", os.path.join(here, "snap", "kl_lib2.py")))):
            if not os.path.exists(path):
                print(f"SKIP {label} S14 adopt ({tag}): {path} fehlt")
                continue
            OLD = load_module(path, f"kl_other_{n_src}")
            S = OLD.LayerStreamer(OLD.skeleton(full.config), shard_of, "cpu", torch.float32, split, chunk=2)
            S.verbose = False
            S.ensure(seqs[:2])                                  # mit dem anderen Code vorberechnet
            before = {k: v.clone() for k, v in S.cache.items()}
            loads_before = getattr(S, "stats", {"layer_loads": 0})["layer_loads"]
            S2 = K.adopt_streamer(S)
            types_ok = (S2 is S and type(S2) is K.LayerStreamer and type(S2.embed) is K.StreamEmbed
                        and all(type(lz) is K.LazyLayer for lz in S2.lazy))
            kept = set(S2.cache) == set(before) and all(torch.equal(S2.cache[k], before[k]) for k in before)
            n_new = S2.ensure(seqs)                             # die übrigen Texte mit dem jetzigen Code
            worst = 0.0
            with torch.no_grad():
                for s in seqs:
                    t = torch.tensor(s)[None]
                    worst = max(worst, float((full(input_ids=t, use_cache=False).logits
                                              - S2.model(input_ids=t, use_cache=False).logits).abs().max()))
            try:
                K.moe_block(S2.model, 0)
                refused = False
            except RuntimeError:
                refused = True
            check(f"{label} S14 adopt ({tag}): classes switched, cache kept, new texts correct",
                  types_ok and kept and n_new == 2 and worst < 1e-4 and refused
                  and S2.stats["layer_loads"] - loads_before == split,
                  f"max diff {worst:.1e}, new lower-layer loads {S2.stats['layer_loads'] - loads_before}")


if __name__ == "__main__":
    LT = ["linear_attention", "full_attention", "linear_attention", "full_attention"]
    run_case(LT, 2, "A")
    run_case(LT, 1, "B")
    run_case(LT, 3, "C")
    run_bf16(LT, 2, "D")
    run_sweep_tests(LT, 2, "E")
    run_sweep_tests(LT, 3, "F")
    run_adopt_tests(LT, 2, "G")
    print("\nFAILURES:", failures if failures else "none")
    sys.exit(1 if failures else 0)
