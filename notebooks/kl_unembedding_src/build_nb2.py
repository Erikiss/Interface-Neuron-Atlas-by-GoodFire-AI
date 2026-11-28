"""Erzeugt kl_unembedding_test.ipynb (Version 2) aus kl_lib2.py und den Zellen unten."""
import json
import pathlib

HERE = pathlib.Path(__file__).parent
LIB = (HERE / "kl_lib2.py").read_text(encoding="utf-8")

CELLS = []


def md(text):
    CELLS.append(("markdown", text.strip("\n")))


def code(text):
    CELLS.append(("code", text.strip("\n")))


md(r"""
# KL-Divergenz und Unembedding bei Qwen3.6-35B-A3B: vorbereiteter Test (Version 2)

**Frage.** Hat das Modell eine Unembedding-Anomalie in Bezug auf die Darstellung von KL-Divergenz? Konkret:
1. Schreiben die Neuronen, die mit KL-Divergenz zu tun haben, direkt auf KL-Wörter, und wenn ja, ungewöhnlich stark?
2. Ändert das Ausschalten dieser Neuronen die Vorhersage von KL-Wörtern in KL-Texten stärker als in Kontrolltexten, und stärker als bei Neuronen, die im selben Experten ähnlich aktiv sind?

**Untersucht werden**
- `L24_E124_N345` (positiver Pol im Atlas; KL-Modus mit 24 von 5.000 Feuerungen)
- `L21_E166_N131` (im Atlas nicht katalogisiert; im Label des Atlas der stärkste Treiber des KL-Modus)

**Vier Teile**
0. **Kalibrierung:** Stimmt der Hook mit den gespeicherten Atlas-Aktivierungen überein? (Einheit: Vielfache von θ, wie im Atlas.)
1. **Gewichte (ohne Vorwärtslauf):** direkte Ausgabewirkung auf KL-Wörter, verglichen mit Zufallsdreiergruppen aus dem Vokabular.
2. **Kausal:** Ausschalten an der Zielposition, in KL-Texten und in längenangepassten Kontrolltexten. Das Routing aller Schichten bleibt festgehalten (primär). Kontrollen: Neuronen desselben Experten mit ähnlicher Aktivität.
3. **Numerik:** Untergrenze durch die Batchgröße, No-op-Kontrolle, negative Werte der naiven KL-Formel.

**Vorab festgelegte Regeln** (vor dem Lauf auf dem echten Modell eingefroren)
- **Gate 0:** Von den Atlas-Werten, die zu einer Position mit Wert gehören (Fensterposition ≥ `u`, ohne den Feuer-Token), stimmen mindestens 90 % innerhalb von 0,005 + 5 % (Rundung auf zwei Nachkommastellen) überein, und das Routing stimmt in mindestens 98 % dieser Positionen überein. Sonst bricht das Notebook ab.
- **P1a:** Die KL-Familie gilt als auffällig, wenn ihr oberstes Neuron in der Schicht über dem 99-%-Quantil liegt, das zufällige Dreiergruppen erreichen.
- **P1b:** Die Ausgabevektoren der KL-Wörter gelten als auffällig, wenn ihre Norm im obersten oder untersten Prozent des Vokabulars liegt.
- **P2 (primär, Routing festgehalten):** D = mittlere Änderung der Log-Wahrscheinlichkeit des KL-Wortes minus dieselbe Größe für das Kontrollwort, jeweils gepaart pro Satzpaar, beim Ausschalten an der Zielposition. Gepaarter Bootstrap über Satzpaare; 97,5-%-Intervall (Bonferroni über die zwei Kandidaten). *Spezifisch*, wenn das Intervall ganz unter −0,05 Nat liegt **und** D höchstens so groß ist wie bei allen Kontrollen (Permutations-artiges p ≤ 0,05 mit 19 Kontrollen). *Keine spezifische Wirkung*, wenn das Intervall 0 enthält.
- **P3:** nur berichten, kein Bestehen-Kriterium.

**Offenlegung.** Die Gewichte-Ergebnisse (P1) und die Null-Verteilung wurden vor dem Schreiben dieses Notebooks berechnet. Die P1-Regeln sind danach formuliert. Das ist im Bericht anzugeben.

**Was der Test nicht kann**
- Er zeigt Wirkungen im Modell, nicht ihre Bedeutung für Menschen.
- Das Modell rechnet in BF16. Die gemessene Untergrenze in Teil 3 zeigt, was sich trennen lässt.
- Das schichtweise Laden ist am winzigen Modell gegen das volle Modell geprüft (Gewichte, Logits, Ablationen, No-op, festgehaltenes Routing, Laden aus BF16). Am echten Checkpoint sind nur Namen, Formen und Dtypes geprüft (aus den Dateikopfzeilen). Der erste volle Lauf am echten Modell ist daher Gate 0: Besteht es nicht, bricht das Notebook vor der Auswertung ab, und die Ursache steht in der Ausgabe von Teil 0.
- 30 Satzpaare sind eine kleine Stichprobe; die Konfidenzintervalle und die minimal nachweisbare Wirkung stehen in Teil 2.

**Hardware und Kosten (ungeprüft)**
- Teil 1 braucht das Modell nicht: Mit `P1_FROM_FILES = True` werden nur die nötigen Tensoren (rund 3 GB) geladen; Teile 0, 2 und 3 entfallen dann.
- Teile 0 bis 3 laufen mit schichtweisem Laden auf einer GPU mit mindestens 40 GB (eine A100 mit 40 GB genügt). Die Schichten ab 21 bleiben dauerhaft auf der GPU (rund 32 GiB), die unteren werden für jede Gruppe von Texten einmal nacheinander geladen. Der Download braucht rund 72 GB Festplattenplatz; das Notebook prüft das vorab.
- Laufzeit nicht gemessen. Schätzung: 2 bis 4 Stunden (rund 5.000 Modellläufe für Teil 2); ein Probelauf (`SMOKE = True`) etwa 15 bis 30 Minuten.
- Atlas-Dateien werden über das Interface-Paket mit der Standardrate geholt (4 Anfragen pro Sekunde). Nichts davon wird gespeichert oder weitergegeben.
""")

code(r'''
# In Colab einmal ausführen (lokal überspringen, wenn die Pakete vorhanden sind)
!pip install -q "transformers==5.24.1" "tokenizers" "safetensors" "accelerate"
!pip install -q "neuron-atlas-interface @ git+https://github.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI.git@main"
# Optional, beschleunigt die Linear-Attention-Schichten (ohne diese läuft die langsame Referenz-Implementierung):
!pip install -q flash-linear-attention
''')

code(r'''
# ---------- Konfiguration: vor dem Lauf festlegen ----------
import json, math, random, time
import torch

MODEL_ID = "Qwen/Qwen3.6-35B-A3B"       # BF16, rund 70 GB. Die FP8-Fassung ist ungeprüft.
TEST_MODE = False                        # True nur zum Prüfen des Notebooks (winziges Modell, vom Testlauf bereitgestellt)
SMOKE = False                            # True: wenige Texte und Kontrollen, schneller Probelauf
P1_FROM_FILES = False                    # True: nur Teil 1, ohne Modell (nur die nötigen Tensoren laden)
CALIB_POLE = "L24_E124_N345__pos"        # Atlas-Pol mit gespeicherten Aktivierungen (Kalibrierung)
CANDIDATES = [(24, 124, 345), (21, 166, 131)]   # (Schicht, Experte, Neuron)
W_WINDOW = 16                            # Atlas: Schaden = Summe über 16 Positionen
BATCH = 48                               # gleiche Batchform für Referenz und Ablation
SPLIT_LAYER = 21                         # obere Schichten ab hier dauerhaft auf der GPU (höchstens die kleinste Kandidatenschicht)
STREAM_CHUNK = 64                        # Texte je Vorberechnungslauf (die unteren Schichten werden je Lauf einmal geladen)
MIN_GPU_GB = 38                          # A100 mit 40 GB; die oberen Schichten brauchen rund 32 GiB
N_MATCH = 19                             # aktivitätsgleiche Kontroll-Neuronen je Kandidat (gleicher Experte)
CAL_ABS, CAL_REL = 0.005, 0.05           # Toleranz Gate 0 (Rundung auf 2 Nachkommastellen + 5 %)
GATE_VALUE, GATE_ROUTE = 0.90, 0.98      # Schwellen Gate 0
SEED = 20261127
STOP_IF_CALIBRATION_FAILS = True
FAM_KL = [" divergence", " KL", "KL"]                        # einteilige Zielwörter
FAM_CTL = [" distance", " error", " norm", " Distance"]     # Kontrollwörter
random.seed(SEED)
torch.manual_seed(SEED)
if SMOKE:
    N_MATCH = 2
print("Konfiguration:", dict(MODEL_ID=MODEL_ID, TEST_MODE=TEST_MODE, SMOKE=SMOKE, P1_FROM_FILES=P1_FROM_FILES, CANDIDATES=CANDIDATES))
''')

code("# ---------- Bibliothek (getestet an einem winzigen Modell derselben Architektur) ----------\n" + LIB)

code(r'''
# ---------- Modell laden: schichtweise, passt auf eine GPU mit 40 GB ----------
from transformers import AutoTokenizer
if TEST_MODE:
    assert "STREAM" in globals() and "tok" in globals(), "Testmodus: STREAM und tok müssen bereitgestellt werden"
    STREAM = adopt_streamer(STREAM)
    model = STREAM.model
else:
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    LOADED = globals().get("STREAM")
    STREAM, model = None, None
    if P1_FROM_FILES:
        DEV = torch.device("cpu")
        E_NUM, I_NUM = 256, 512
        print("Nur Teil 1 (ohne Modell). Teile 0, 2 und 3 brauchen P1_FROM_FILES = False.")
    elif LOADED is not None:
        # Die Zelle wurde erneut ausgeführt: Das geladene Modell bleibt. Ein zweites Laden würde den GPU-Speicher sprengen.
        STREAM = LOADED
        if STREAM.split != SPLIT_LAYER:
            raise RuntimeError("Ein Modell mit anderer Aufteilung ist geladen. Für eine neue Aufteilung die Laufzeit neu starten.")
        STREAM = adopt_streamer(STREAM)
        model = STREAM.model
        print("Das Modell ist bereits geladen und wird weiterverwendet (zum Neuladen die Laufzeit neu starten).")
    else:
        import os, shutil
        from huggingface_hub import hf_hub_download, snapshot_download
        from transformers import AutoConfig
        assert torch.cuda.is_available(), "Keine GPU gefunden: in Colab unter Laufzeit eine GPU (A100) wählen."
        gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU: {torch.cuda.get_device_name(0)}, {gb:.1f} GB")
        if gb < MIN_GPU_GB:
            raise RuntimeError(f"Zu wenig GPU-Speicher: {gb:.1f} GB, nötig sind mindestens {MIN_GPU_GB} GB.")
        cfg_full = AutoConfig.from_pretrained(MODEL_ID)
        tc = cfg_full.text_config
        expect = dict(num_hidden_layers=40, hidden_size=2048, num_experts=256, num_experts_per_tok=8, moe_intermediate_size=512)
        for k, v in expect.items():
            assert getattr(tc, k) == v, f"{k} = {getattr(tc, k)}, erwartet {v}"
        skel = skeleton(cfg_full)                # Aufbau ohne Gewichte
        names = needed_names(skel)
        wm = json.load(open(hf_hub_download(MODEL_ID, "model.safetensors.index.json"), encoding="utf-8"))["weight_map"]
        missing = [n for n in names if n not in wm]
        assert not missing, f"Gewichte fehlen im Checkpoint: {missing[:5]}"
        files = sorted({wm[n] for n in names})
        free_gb = shutil.disk_usage("/").free / 1e9
        print(f"Festplatte frei: {free_gb:.0f} GB (der Download braucht rund 72 GB)")
        if free_gb < 74:
            raise RuntimeError("Zu wenig Festplattenplatz für die Gewichte (rund 72 GB nötig).")
        local = snapshot_download(MODEL_ID, allow_patterns=files)
        shard_of = {n: os.path.join(local, wm[n]) for n in names}
        STREAM = LayerStreamer(skel, shard_of, "cuda", torch.bfloat16, SPLIT_LAYER, STREAM_CHUNK)
        model = STREAM.model
        print(f"Obere Schichten ab {SPLIT_LAYER} liegen auf der GPU: {STREAM.gpu_gib():.1f} GiB belegt")
if model is not None:
    assert STREAM.split <= min(c[0] for c in CANDIDATES), "Kandidaten müssen in den oberen, dauerhaft geladenen Schichten liegen"
    DEV = STREAM.device
    tc = model.config.text_config
    E_NUM, I_NUM = tc.num_experts, tc.moe_intermediate_size
    remove_neuron_hooks()                    # nichts aus einem früheren, abgebrochenen Lauf bleibt am Modell hängen
    if globals().get("RC") is not None:
        RC.remove()                          # keine doppelten Routing-Hooks, wenn die Zelle erneut läuft
    RC = RoutingControl(model)               # steuert das Routing aller oberen Schichten
V_TOK = len(tok)                             # nur echte Token (das Modell hat Auffüll-Zeilen darüber)
print("Bereit | Modell:", "geladen" if model is not None else "nicht geladen", "| Gerät", DEV,
      "| Experten", E_NUM, "| Neuronen je Experte", I_NUM, "| Token", V_TOK)
''')

code(r'''
# ---------- Zielwörter als einzelne Token-IDs ----------
def family_ids(words):
    out = []
    for w in words:
        ids_w = tok.encode(w, add_special_tokens=False)
        assert len(ids_w) == 1, f"{w!r} ist kein einzelnes Token: {ids_w}"
        out.append(ids_w[0])
    return out

FAM_KL_IDS = family_ids(FAM_KL)
FAM_CTL_IDS = family_ids(FAM_CTL)
print("KL-Zielwörter:", [tok.decode([i]) for i in FAM_KL_IDS])
print("Kontroll-Zielwörter:", [tok.decode([i]) for i in FAM_CTL_IDS])
''')

md(r"""
## Teil 0: Kalibrierung gegen den Atlas

Der Atlas speichert für jede Feuerung ein Token-Fenster. Die Werte im Fenster sind die Aktivierung **in Vielfachen von θ**, auf zwei Nachkommastellen gerundet. Positionen vor `u` sind nur Kontext ohne Wert; `null` bedeutet, dass der Router den Experten dort nicht gewählt hat. Der Feuer-Token (Position `f`) hat einen Wert, der nicht die Feuerung selbst ist, und wird deshalb ausgelassen.

**Ablauf.** Alle Fenster laufen zuerst gemeinsam durch die unteren Schichten: Jede der 21 Schichten wird einmal geladen, eine Fortschrittsanzeige zählt mit. Danach läuft jedes Fenster einzeln durch die oberen Schichten. Unter dem Ergebnis steht eine beschreibende Diagnose (Zusammenhang, Übereinstimmung nach Kontextlänge, andere denkbare Definitionen des gespeicherten Werts). Sie ändert die Regel nicht.

**Hinweis zur Ausgabe.** Beim ersten Lauf meldet transformers, dass `causal_conv1d` nicht installiert ist und seine Referenz-Implementierung benutzt wird. Das ist kein Fehler. Die Referenz ist ein einzelner Faltungsaufruf je Schicht; die Rechnung ist dieselbe.
""")

code(r'''
# ---------- Gate 0: Kalibrierung gegen die gespeicherten Atlas-Aktivierungen ----------
from neuron_atlas import notebook
from neuron_atlas.upstream import pole_path

CALIB_OK, GATE0_STATS = None, None
if TEST_MODE or model is None:
    print("Kalibrierung übersprungen (Testmodus oder kein Modell geladen)")
else:
    atlas = notebook.open_atlas()
    rel = pole_path("landscape", CALIB_POLE)
    srel = pole_path("subcircuit", CALIB_POLE)
    fetched = atlas.source.fetch_many([rel, srel])
    bad = [r.relpath for r in fetched if r.status not in ("present", "absent")]
    assert not bad, f"Atlas-Dateien nicht lesbar: {bad}"
    dat = json.loads(atlas.source.store.read(rel))
    THETA = float(json.loads(atlas.source.store.read(srel))["theta"])
    print(f"theta = {THETA} (aus {srel})")
    dots = dat["dots"][:25] if SMOKE else dat["dots"]
    windows = []                                   # (Punkt, Token-IDs, exakt übersetzt?)
    for dot in dots:
        t = dot["t"]
        ids_list = tok.encode("".join(s for s, _ in t), add_special_tokens=False)
        exact = len(ids_list) == len(t) and [tok.decode([i]) for i in ids_list] == [s for s, _ in t]
        windows.append((dot, ids_list, exact))
    remove_neuron_hooks()
    RC.reset()
    STREAM.ensure([ids for _, ids, exact in windows if exact])   # alle Fenster in einem Durchlauf der unteren Schichten
    hookC = NeuronHook(model, 24, 124, 345, RC)
    rows, skipped = [], 0
    shifted = {-1: [], 1: []}                      # nur für die Diagnose: Wert der Nachbarposition gegen den Atlas-Wert
    try:
        for dot, ids_list, exact in progress(windows, "Gate 0: Fenster rechnen"):
            t, u, f = dot["t"], dot["u"], dot["f"]
            if not exact:
                skipped += 1                       # Fenster lässt sich nicht exakt zurück in Token übersetzen
                continue
            act_m, w_m = activation_at(model, hookC, RC, torch.tensor(ids_list, device=DEV))
            for j in range(u, len(t)):
                if j == f:
                    continue
                rows.append(dict(a=float(act_m[j]) / THETA, w=float(w_m[j]), routed=float(w_m[j]) > 0, v=t[j][1],
                                 pos=j - u, left=j))
                if t[j][1] is not None:
                    for d in (-1, 1):
                        if 0 <= j + d < len(t):
                            shifted[d].append((float(act_m[j + d]) / THETA, t[j][1]))
    finally:
        hookC.remove()
    routed = [r for r in rows if r["v"] is not None]
    ok_val = [abs(r["a"] - r["v"]) <= CAL_ABS + CAL_REL * abs(r["v"]) for r in routed]
    frac_value = sum(ok_val) / len(routed)
    agree = sum(r["routed"] == (r["v"] is not None) for r in rows) / len(rows)
    print(f"Fenster übernommen: {len(dots) - skipped} von {len(dots)} (nicht exakt übersetzt: {skipped})")
    print(f"Positionen mit Atlas-Wert (ohne Kontext, ohne Feuer-Token): {len(rows)}, davon gerouted: {len(routed)}")
    print(f"Wert innerhalb 0,005 + 5 %: {frac_value:.3f}   (Gate: >= {GATE_VALUE})")
    print(f"Routing stimmt überein:     {agree:.3f}   (Gate: >= {GATE_ROUTE})")
    # Beschreibende Diagnose. Sie ändert die Regel nicht; sie zeigt, woran es liegt, falls das Gate nicht besteht.
    def share_ok(pairs):
        return sum(abs(a - v) <= CAL_ABS + CAL_REL * abs(v) for a, v in pairs) / max(len(pairs), 1)

    GATE0_STATS = dict(windows=len(dots) - skipped, slots=len(rows), slots_routed_in_atlas=len(routed),
                       share_value_ok=round(frac_value, 4), share_routing_ok=round(agree, 4))
    if len(routed) > 2:
        A_ = torch.tensor([r["a"] for r in routed], dtype=torch.float64)
        V_ = torch.tensor([r["v"] for r in routed], dtype=torch.float64)
        err = (A_ - V_).abs()
        GATE0_STATS.update(pearson_r=round(float(torch.corrcoef(torch.stack([A_, V_]))[0, 1]), 4),
                           slope_through_origin=round(float((A_ * V_).sum() / (V_ * V_).sum().clamp_min(1e-30)), 4),
                           median_abs_error=round(float(err.median()), 4),
                           q90_abs_error=round(float(torch.quantile(err, 0.9)), 4))
        print(f"Diagnose (beschreibend): Pearson r = {GATE0_STATS['pearson_r']}, Steigung a gegen v durch 0 = "
              f"{GATE0_STATS['slope_through_origin']}, Median |a - v| = {GATE0_STATS['median_abs_error']}, "
              f"90-%-Quantil = {GATE0_STATS['q90_abs_error']}")
    print("  Übereinstimmung nach Kontext links im Fenster (Zahl der Token vor der Position):")
    by_left = {}
    for lo_, hi_ in ((0, 8), (8, 16), (16, 32), (32, 64), (64, 10**9)):
        part = [ok for ok, r in zip(ok_val, routed) if lo_ <= r["left"] < hi_]
        if part:
            label = f"{lo_} bis {hi_ - 1 if hi_ < 10**9 else '…'}"
            by_left[label] = [round(sum(part) / len(part), 4), len(part)]
            print(f"    {label}: {by_left[label][0]:.3f} (n={len(part)})")
    alt = {"Aktivierung mal Routing-Gewicht": share_ok([(r["a"] * r["w"], r["v"]) for r in routed]),
           "Aktivierung ohne Teilen durch theta": share_ok([(r["a"] * THETA, r["v"]) for r in routed]),
           "Position um eins früher": share_ok(shifted[-1]),
           "Position um eins später": share_ok(shifted[1])}
    for name, val in alt.items():
        print(f"  Wäre der Atlas-Wert »{name}«: {val:.3f} innerhalb der Toleranz")
    n_both = sum(r["routed"] and r["v"] is not None for r in rows)
    n_atlas = sum((not r["routed"]) and r["v"] is not None for r in rows)
    n_model = sum(r["routed"] and r["v"] is None for r in rows)
    print(f"  Routing: in beiden gewählt {n_both}, nur im Atlas {n_atlas}, nur im Modell {n_model}, "
          f"in keinem {len(rows) - n_both - n_atlas - n_model}")
    GATE0_STATS.update(share_ok_by_left_context=by_left, share_ok_under_other_definitions={k: round(v, 4) for k, v in alt.items()},
                       routing_both=n_both, routing_only_atlas=n_atlas, routing_only_model=n_model)
    CALIB_OK = frac_value >= GATE_VALUE and agree >= GATE_ROUTE
    print("GATE 0 BESTANDEN" if CALIB_OK else "GATE 0 NICHT BESTANDEN")
    if not CALIB_OK and STOP_IF_CALIBRATION_FAILS:
        raise RuntimeError("Gate 0 nicht bestanden: Hook oder Definition stimmen nicht mit dem Atlas überein.")
''')

md(r"""
## Teil 1: Direkte Ausgabewirkung auf KL-Wörter (ohne Vorwärtslauf)

Für jedes Neuron rechnen wir `W_U · (Endnorm-Gewicht ⊙ Schreibvektor)`. Das ist die Wirkung auf alle echten Vokabel-Token. Der Atlas nennt diese Liste `static`; wir prüfen, ob sie genau diese Wirkung ist (in früheren Läufen für 57 Neuronen mit r = 1,000000 bestätigt). Die Kennzahl ist der Mittelwert der z-Werte der KL-Wörter. Verglichen wird mit allen Neuronen der Schicht und mit zufälligen Dreiergruppen aus dem Vokabular.
""")

code(r'''
# ---------- Teil 1: Gewichte (aus dem Modell oder per Bereichsanfrage) ----------
def weights_for_p1():
    layers = sorted({c[0] for c in CANDIDATES})
    if not P1_FROM_FILES:
        W_U = model.lm_head.weight.detach().float().cpu()[:V_TOK]
        gain = (1.0 + text_model(model).norm.weight.detach().float()).cpu()
        Wd = {L: moe_block(model, L).experts.down_proj.detach().float().cpu() for L in layers}
        return W_U, gain, Wd
    import urllib.request
    BASE = f"https://huggingface.co/{MODEL_ID}/resolve/main/"
    with urllib.request.urlopen(BASE + "model.safetensors.index.json") as r:
        IDX = json.loads(r.read())["weight_map"]

    def get(name):
        return read_safetensors_tensors(BASE + IDX[name], [name])[name]

    gain = 1.0 + get("model.language_model.norm.weight").float()
    W_U = get("lm_head.weight").float()[:V_TOK]
    Wd = {L: get(f"model.language_model.layers.{L}.mlp.experts.down_proj").float() for L in layers}
    return W_U, gain, Wd

W_U, gain, Wd_by_layer = weights_for_p1()
P1 = {"candidates": {}, "null": {}, "static_check": None}
for L in sorted(Wd_by_layer):
    Wd = Wd_by_layer[L]
    zK, _, _ = direct_effect_columns(W_U, gain, Wd, FAM_KL_IDS)
    zC, _, _ = direct_effect_columns(W_U, gain, Wd, FAM_CTL_IDS)
    for (LL, e, n) in CANDIDATES:
        if LL != L:
            continue
        idx = e * Wd.shape[2] + n
        v = direct_effect_vector(W_U, gain, Wd[e][:, n])
        z = (v - v.mean()) / v.std(unbiased=False)
        P1["candidates"][f"L{L}_E{e}_N{n}"] = {
            "KL_family_z_mean": round(float(zK[idx]), 3),
            "KL_percentile_in_layer": round(percentile_rank(zK, zK[idx]), 5),
            "control_family_z_mean": round(float(zC[idx]), 3),
            "control_percentile_in_layer": round(percentile_rank(zC, zC[idx]), 5),
            "KL_token_z": {tok.decode([i]): round(float(z[i]), 3) for i in FAM_KL_IDS},
            "top_promoted": [[tok.decode([i]), round(float(z[i]), 2)] for i in torch.topk(z, 10).indices.tolist()],
            "top_suppressed": [[tok.decode([i]), round(float(z[i]), 2)] for i in torch.topk(-z, 10).indices.tolist()],
        }
    rng = random.Random(SEED)
    fams = torch.tensor([rng.sample(range(1000, V_TOK), 3) for _ in range(300)] + [FAM_KL_IDS])
    best, arg = family_max_null(W_U, gain, Wd, fams)
    kl_max = float(best[-1])
    rnd = best[:-1]
    q99 = float(torch.quantile(rnd, 0.99))
    norms = W_U.norm(dim=1)
    pct = {tok.decode([i]): round(percentile_rank(norms, norms[i]), 4) for i in FAM_KL_IDS}
    P1["null"][f"L{L}"] = {
        "KL_family_max_neuron_z": round(kl_max, 3),
        "random_triplets_max_neuron_z_quantiles": {str(q): round(float(torch.quantile(rnd, q)), 3) for q in (0.5, 0.9, 0.95, 0.99)},
        "KL_rank_among_301_families(1=highest)": int((best >= kl_max).sum()),
        "KL_argmax_neuron": [int(arg[-1]) // Wd.shape[2], int(arg[-1]) % Wd.shape[2]],
        "P1a_auffaellig": bool(kl_max > q99),
        "KL_token_norm_percentile": pct,
        "P1b_auffaellig": bool(any(p >= 0.99 or p <= 0.01 for p in pct.values())),
    }

if not TEST_MODE and 24 in Wd_by_layer:
    if "atlas" not in globals():
        atlas = notebook.open_atlas()
    from neuron_atlas.upstream import neuron_path
    mrel = neuron_path("circuit_meta", "L24_E124_N345")
    fm = atlas.source.fetch_many([mrel])
    assert all(r.status in ("present", "absent") for r in fm), "Schaltkreis-Datei nicht lesbar"
    meta = json.loads(atlas.source.store.read(mrel))
    static = [(r["token_id"], r["w"]) for r in meta["logits"] if r["source"] in ("static", "both")]
    vv = direct_effect_vector(W_U, gain, Wd_by_layer[24][124][:, 345])
    tids = torch.tensor([t for t, _ in static])
    ws = torch.tensor([w for _, w in static], dtype=torch.float32)
    P1["static_check"] = {
        "rows": len(static),
        "sign_agreement": round(float(((ws > 0) == (vv[tids] > 0)).float().mean()), 4),
        "pearson_r": round(float(torch.corrcoef(torch.stack([ws, vv[tids]]))[0, 1]), 6),
        "max_abs_diff": round(float((ws - vv[tids]).abs().max()), 6),
    }
print(json.dumps(P1, indent=1, ensure_ascii=False))
''')

md(r"""
## Teil 2: Kausal. Ausschalten an der Zielposition

**Satzpaare.** Jeder KL-Satz hat einen Kontrollsatz mit demselben Aufbau, in dem der Fachbegriff ausgetauscht ist (`Kullback-Leibler divergence` gegen `Euclidean distance`, `Hamming distance`, `mean squared error` oder `Jensen-Shannon distance`). Der Ersatz wird pro Satz so gewählt, dass die Tokenzahl möglichst gleich ist. Paare ohne Zielposition in einem der beiden Sätze werden ausgelassen und gezählt.

**Messung.** Das Neuron wird an der Position ausgeschaltet, die das Zielwort vorhersagt. Gemessen wird die Änderung der Log-Wahrscheinlichkeit des Zielworts. Das Routing aller Schichten ist festgehalten (primär). Der freie Lauf (wie der Atlas) steht daneben.

**Kontrollen.** Pro Kandidat werden 19 Neuronen desselben Experten gewählt, die ihm in der mittleren Aktivität am nächsten liegen. Sie teilen das Routing, unterscheiden sich aber in Aktivität und Ausgaberichtung.

**Abbruch und Fortsetzen.** Die Läufe-Zelle schreibt nach jedem fertigen Neuron den Zwischenstand in `kl_unembedding_partial.json`. Wird sie nach einem Abbruch erneut ausgeführt, rechnet sie nur das, was noch fehlt, solange Kandidaten, Satzpaare, Kontrollen und Einstellungen dieselben sind. Die Modellzelle lädt nicht neu, wenn das Modell schon geladen ist.
""")

code(r'''
# ---------- Satzpaare (von Hand geschrieben, keine fremden Texte) ----------
KL_SENTENCES = [
    "To measure how far the predicted distribution is from the true one, we compute the Kullback-Leibler divergence.",
    "The variational lower bound contains a penalty term equal to the Kullback-Leibler divergence between the posterior and the prior.",
    "Minimising the forward Kullback-Leibler divergence forces the model to cover every mode of the data.",
    "For two univariate Gaussians the closed form of the Kullback-Leibler divergence follows from the log-variance ratio.",
    "The distillation loss is the Kullback-Leibler divergence between the teacher and the student output distributions.",
    "Each sparse autoencoder layer adds a penalty, implemented as a Kullback-Leibler divergence between the target and the observed activation rates.",
    "A large Kullback-Leibler divergence indicates that the two language model distributions disagree strongly on the next token.",
    "The policy update is constrained by a bound on the Kullback-Leibler divergence between the old and the new policy.",
    "Because the Kullback-Leibler divergence is asymmetric, reversing the two arguments changes the value of the loss.",
    "We report the average Kullback-Leibler divergence over all test sequences, computed from the token probabilities.",
    "Information gain is the expected Kullback-Leibler divergence between the posterior and the prior beliefs.",
    "The evidence lower bound decomposes into a reconstruction term and a Kullback-Leibler divergence term.",
    "Temperature scaling changes the Kullback-Leibler divergence between the softened teacher and the student distributions.",
    "In the experiments the Kullback-Leibler divergence between the clean and the ablated predictions was summed over sixteen positions.",
    "The gradient of the Kullback-Leibler divergence with respect to the logits equals the difference of the two probability vectors.",
    "Entropy regularisation adds a bonus term, while the Kullback-Leibler divergence to a reference policy acts as a penalty.",
    "The score measures the Kullback-Leibler divergence between the empirical and the model distribution of the tokens.",
    "Calibration error can be estimated from the Kullback-Leibler divergence between predicted and observed frequencies.",
    "Variational inference fits a simpler family by minimising the Kullback-Leibler divergence between the approximate and the true posterior.",
    "The Kullback-Leibler divergence is zero exactly when the two distributions coincide on every outcome.",
    "Stochastic estimates of the Kullback-Leibler divergence are unbiased when the samples come from the reference distribution.",
    "Our regulariser penalises the Kullback-Leibler divergence between the attention distributions of the two heads.",
    "The reward model is calibrated by minimising the Kullback-Leibler divergence to the human preference distribution.",
    "We approximate the intractable posterior with a Gaussian and report its Kullback-Leibler divergence to the prior.",
    "Mutual information can be written as an expected Kullback-Leibler divergence between a joint and a product distribution.",
    "The diffusion loss reduces to a sum of Kullback-Leibler divergences between consecutive noise levels.",
    "Model merging is evaluated by the Kullback-Leibler divergence between the merged and the individual expert predictions.",
    "Temperature-matched softmax outputs give a smaller Kullback-Leibler divergence than the raw logits do.",
    "A hidden Markov model is trained by minimising a free energy that contains the Kullback-Leibler divergence to the approximate posterior.",
    "The Kullback-Leibler divergence penalises confident predictions that put little mass on the true outcome.",
]
REPLACEMENTS = ["Euclidean distance", "Hamming distance", "mean squared error", "Jensen-Shannon distance"]

def make_control(s, repl):
    out = s.replace("Kullback-Leibler divergence", repl).replace("KL divergence", repl)
    assert "Kullback" not in out and "KL" not in out and "divergence" not in out, f"Rest im Kontrollsatz: {out}"
    return out

pairs_raw = []
for s in KL_SENTENCES:
    kl_ids = tok.encode(s, add_special_tokens=False)
    best = None
    for repl in REPLACEMENTS:
        c = make_control(s, repl)
        c_ids = tok.encode(c, add_special_tokens=False)
        diff = abs(len(c_ids) - len(kl_ids))
        if best is None or diff < best[0]:
            best = (diff, c, c_ids)
    pairs_raw.append((s, kl_ids, best[1], best[2]))

PAIRS = []
dropped = 0
for s, kl_ids, ctl, ctl_ids in pairs_raw:
    kl_pos = term_positions(kl_ids, FAM_KL_IDS)
    ctl_pos = term_positions(ctl_ids, FAM_CTL_IDS)
    if kl_pos and ctl_pos:
        PAIRS.append((kl_ids, kl_pos, ctl_ids, ctl_pos))
    else:
        dropped += 1
if SMOKE:
    PAIRS = PAIRS[:3]
len_diff = [abs(len(a) - len(c)) for a, _, c, _ in PAIRS]
print(f"Satzpaare: {len(PAIRS)} (ausgelassen wegen fehlender Zielposition: {dropped})")
print(f"Tokenlängen KL / Kontrolle: Mittel {sum(len(a) for a, *_ in PAIRS) / len(PAIRS):.1f} / "
      f"{sum(len(c) for _, _, c, _ in PAIRS) / len(PAIRS):.1f}; Längenunterschied Mittel {sum(len_diff) / len(len_diff):.2f}, Maximum {max(len_diff)}")
print("Beispiel:")
print("  KL:      ", tok.decode(PAIRS[0][0]))
print("  Kontrolle:", tok.decode(PAIRS[0][2]))
''')

code(r'''
# ---------- Aktivitätsgleiche Kontroll-Neuronen (gleicher Experte, gleiches Routing) ----------
def matched_neurons(L, e, n_cand, texts_ids, k):
    """Die k Neuronen desselben Experten, deren mittlere Aktivität (an gerouteten Positionen) dem Kandidaten am nächsten liegt."""
    STREAM.ensure(texts_ids)
    bi = BlockInput(model, L)
    RC.mode = "free"
    acc = torch.zeros(I_NUM, dtype=torch.float64)
    cnt = 0
    mlp_L = moe_block(model, L)
    try:
        with torch.no_grad():
            for ids_list in texts_ids:
                ids = torch.tensor(ids_list, device=DEV)
                model(input_ids=ids[None], use_cache=False)
                x = bi.x
                A = expert_acts_all(mlp_L, e, x).abs()
                sc, ix = RC.last[L]
                m = ((sc * (ix == e)).sum(-1) > 0)
                acc += A[m].sum(0).double().cpu()
                cnt += int(m.sum())
    finally:
        bi.remove()
    mean_act = acc / max(cnt, 1)
    cand_val = float(mean_act[n_cand])
    diffs = sorted((abs(math.log(float(mean_act[j]) + 1e-9) - math.log(cand_val + 1e-9)), j)
                   for j in range(I_NUM) if j != n_cand)
    return [j for _, j in diffs[:k]], cand_val

CTRL = {}
if model is not None:
    remove_neuron_hooks()
    RC.reset()
    all_ids = [p[0] for p in PAIRS] + [p[2] for p in PAIRS]
    for (L, e, n) in CANDIDATES:
        ms, cv = matched_neurons(L, e, n, all_ids, N_MATCH)
        CTRL[(L, e, n)] = [(e, j) for j in ms]
        print(f"L{L}_E{e}_N{n}: mittlere Aktivität {cv:.4f}; Kontrollen (Experte {e}): {ms}")
''')

code(r'''
# ---------- Läufe: Kandidaten (zwei Arme) und Kontrollen (festgehaltenes Routing) ----------
assert model is not None, "Dieser Teil braucht das Modell (P1_FROM_FILES = False)"
import hashlib
remove_neuron_hooks()
RC.reset()
STREAM.ensure([p[0] for p in PAIRS] + [p[2] for p in PAIRS])

def run_text(hook, ids_list, pos, freeze):
    ids = torch.tensor(ids_list, device=DEV)
    tgt = torch.tensor(ids_list[1:] + [-1])
    tab = ablation_table(model, hook, RC, ids, tgt, W=W_WINDOW, batch=BATCH, freeze=freeze)
    return [float(tab["lp_tgt"][p, p] - tab["clean_tgt"][p]) for p in pos]

def run_neuron(L, e, n, arm):
    hook = NeuronHook(model, L, e, n, RC)
    freeze = "clean" if arm == "frozen" else None
    per_pair = []
    try:
        for kl_ids, kl_pos, ctl_ids, ctl_pos in PAIRS:
            d_kl = run_text(hook, kl_ids, kl_pos, freeze)
            d_ctl = run_text(hook, ctl_ids, ctl_pos, freeze)
            per_pair.append(sum(d_kl) / len(d_kl) - sum(d_ctl) / len(d_ctl))   # gepaarter Kontrast pro Satzpaar
    finally:
        hook.remove()
    return per_pair

# Fertige Läufe bleiben erhalten, solange Kandidaten, Satzpaare, Kontrollen und Einstellungen dieselben sind.
# Nach einem Abbruch setzt ein erneutes Ausführen dieser Zelle dort fort, wo sie stand. Der Zwischenstand steht
# zusätzlich in kl_unembedding_partial.json (zum Herunterladen, falls die Sitzung endet).
RUN_SIG = hashlib.sha256(repr((CANDIDATES, PAIRS, sorted(CTRL.items()), W_WINDOW, BATCH)).encode()).hexdigest()
if globals().get("RUN_STATE", {}).get("sig") != RUN_SIG:
    RUN_STATE = {"sig": RUN_SIG, "runs": {}, "ctrl": {}}
RUNS, CTRL_DONE = RUN_STATE["runs"], RUN_STATE["ctrl"]

def save_partial():
    part = {"sig": RUN_SIG, "n_pairs": len(PAIRS),
            "runs": {f"L{k[0]}_E{k[1]}_N{k[2]}_{k[3]}": v for k, v in RUNS.items()},
            "controls": {f"L{k[0]}_E{k[1]}_N{k[2]}__E{k[3]}_N{k[4]}": v for k, v in CTRL_DONE.items()}}
    with open("kl_unembedding_partial.json", "w", encoding="utf-8") as fh:
        json.dump(part, fh)

t0 = time.time()
for (L, e, n) in CANDIDATES:
    for arm in ("frozen", "free"):
        if (L, e, n, arm) not in RUNS:
            RUNS[(L, e, n, arm)] = run_neuron(L, e, n, arm)
            save_partial()
        print(f"Kandidat L{L}_E{e}_N{n} [{arm}] fertig nach {time.time() - t0:.0f} s", flush=True)
    for ce, cj in progress(CTRL[(L, e, n)], f"Kontrollen L{L}_E{e}_N{n}"):
        if (L, e, n, ce, cj) not in CTRL_DONE:
            CTRL_DONE[(L, e, n, ce, cj)] = run_neuron(L, ce, cj, "frozen")
            save_partial()
    print(f"Kontrollen für L{L}_E{e}_N{n}: {len(CTRL[(L, e, n)])} fertig nach {time.time() - t0:.0f} s", flush=True)
CTRL_RUNS = {c: [CTRL_DONE[(*c, ce, cj)] for ce, cj in CTRL[c]] for c in CANDIDATES}
''')

code(r'''
# ---------- Statistik und Entscheidung nach den vorab festgelegten Regeln ----------
def summarize(per_pair):
    est, lo, hi = paired_bootstrap([[d] for d in per_pair], n_boot=5000, seed=SEED)
    sd = (sum((d - est) ** 2 for d in per_pair) / (len(per_pair) - 1)) ** 0.5
    return est, lo, hi, sd

SUMMARY, VERDICT = {}, {}
for (L, e, n) in CANDIDATES:
    ctrl_D = [summarize(r)[0] for r in CTRL_RUNS[(L, e, n)]]
    for arm in ("frozen", "free"):
        est, lo, hi, sd = summarize(RUNS[(L, e, n, arm)])
        p = (1 + sum(c <= est for c in ctrl_D)) / (len(ctrl_D) + 1)
        n_pairs = len(RUNS[(L, e, n, arm)])
        mde = 2.8 * sd / math.sqrt(n_pairs)
        SUMMARY[(L, e, n, arm)] = dict(D=est, ci=(lo, hi), p_vs_controls=p, sd=sd, mde_80=mde, n_pairs=n_pairs,
                                        ctrl_median=sorted(ctrl_D)[len(ctrl_D) // 2])
        if lo <= 0 <= hi:
            v = "keine spezifische Wirkung (Intervall enthält 0)"
        elif est > 0:
            v = "erhöht die Ziel-Wörter"
        elif hi <= -0.05 and p <= 0.05:
            v = "spezifisch: Abnahme über die aktivitätsgleichen Kontrollen hinaus"
        elif hi <= -0.05:
            v = "Abnahme, aber nicht über alle Kontrollen hinaus (p > 0,05)"
        else:
            v = "unklar (Abnahme kleiner als 0,05 Nat)"
        VERDICT[(L, e, n, arm)] = v
        note = "  (Kontrollen: festgehaltenes Routing)" if arm == "free" else ""
        print(f"L{L}_E{e}_N{n} [{arm}]  D = {est:+.4f}  97,5-%-CI [{lo:+.4f}, {hi:+.4f}]  "
              f"p gegen Kontrollen = {p:.3f}  minimal nachweisbar (80 %) = {mde:.3f} Nat  -> {v}{note}")
    print(f"  Kontrollen L{L}_E{e}_N{n}: D-Median {sorted(ctrl_D)[len(ctrl_D) // 2]:+.4f}, Spanne {min(ctrl_D):+.4f} bis {max(ctrl_D):+.4f}")
''')

code(r'''
# ---------- Teil 3: Untergrenzen ----------
assert model is not None, "Dieser Teil braucht das Modell (P1_FROM_FILES = False)"
remove_neuron_hooks()
RC.reset()
STREAM.ensure([p[0] for p in PAIRS])
NUM = {}
cand = CANDIDATES[0]
kl_floor = []
for kl_ids, _, _, _ in PAIRS[:5]:
    ids = torch.tensor(kl_ids, device=DEV)
    RC.mode = "free"
    with torch.no_grad():
        l1 = logits_of(model, ids[None])[0]
        lB = logits_of(model, ids[None].expand(BATCH, -1).contiguous())[0]
    kl_floor.append(float(stable_kl_f64(l1, lB).max()))
NUM["batch_size_floor_max_KL_nat"] = max(kl_floor)

hookN = NeuronHook(model, *cand, RC)
noop = []
for kl_ids, _, _, _ in PAIRS[:5]:
    tab = ablation_table(model, hookN, RC, torch.tensor(kl_ids, device=DEV), torch.tensor(kl_ids[1:] + [-1]),
                         W=W_WINDOW, batch=BATCH, scale=1.0)
    noop.append(float(tab["kl"].abs().max()))
hookN.remove()
NUM["noop_max_KL_nat"] = max(noop)

hookC2 = NeuronHook(model, *cand, RC)
neg_naive = tot = neg_stable = 0
for kl_ids, _, _, _ in PAIRS[:3]:
    T = len(kl_ids)
    ids = torch.tensor(kl_ids, device=DEV)
    full = ids[None].expand(BATCH, -1).contiguous()
    RC.mode = "free"
    with torch.no_grad():
        hookC2.mask, hookC2.scale = None, 0.0
        clean = logits_of(model, full)[0].float()
        m = torch.zeros(BATCH, T, dtype=torch.bool, device=DEV)
        for b in range(min(BATCH, T)):
            m[b, b] = True
        hookC2.mask, hookC2.scale = m, 0.0
        lg = logits_of(model, full)
        hookC2.mask = None
        for b in range(min(BATCH, T)):
            nk = naive_kl_f32(clean, lg[b].float())
            sk = stable_kl_f64(clean, lg[b].float())
            seg = slice(b, T)
            neg_naive += int((nk[seg] < 0).sum())
            neg_stable += int((sk[seg] < 0).sum())
            tot += T - b
hookC2.remove()
NUM["naive_f32_negative_values"] = neg_naive
NUM["stable_f64_negative_values"] = neg_stable
NUM["positions_checked"] = tot

pert = {}
for sc in (1 - 1e-4, 1 - 1e-2, 0.0):
    hookP = NeuronHook(model, *cand, RC)
    vals = []
    for kl_ids, _, _, _ in PAIRS[:5]:
        tab = ablation_table(model, hookP, RC, torch.tensor(kl_ids, device=DEV), torch.tensor(kl_ids[1:] + [-1]),
                             W=W_WINDOW, batch=BATCH, scale=sc)
        dg = tab["kl"].diagonal()
        vals.append({"max": float(dg.max()), "positions_nonzero": int((dg > 1e-12).sum()), "positions": int(dg.numel())})
    hookP.remove()
    pert[f"scale={sc:.6f}"] = vals
NUM["perturbation_at_own_position"] = pert
print(json.dumps(NUM, indent=1))
''')

code(r'''
# ---------- Ergebnis speichern und zusammenfassen ----------
def key_name(k):
    return f"L{k[0]}_E{k[1]}_N{k[2]}_{k[3]}"

out = {
    "config": dict(MODEL_ID=MODEL_ID, CANDIDATES=CANDIDATES, W_WINDOW=W_WINDOW, BATCH=BATCH, N_MATCH=N_MATCH,
                   CAL_ABS=CAL_ABS, CAL_REL=CAL_REL, GATE_VALUE=GATE_VALUE, GATE_ROUTE=GATE_ROUTE,
                   SMOKE=SMOKE, TEST_MODE=TEST_MODE, SEED=SEED, n_pairs=len(PAIRS)),
    "gate0_calibration_ok": CALIB_OK,
    "gate0": GATE0_STATS,
    "streaming": dict(SPLIT_LAYER=SPLIT_LAYER, STREAM_CHUNK=STREAM_CHUNK,
                      texts_prepared=len(STREAM.cache) if STREAM is not None else None,
                      lower_layer_loads=STREAM.stats["layer_loads"] if STREAM is not None else None),
    "P1_weights": P1,
    "P2_causal": {key_name(k): dict(D=round(v["D"], 5), ci97_5=[round(x, 5) for x in v["ci"]],
                                    p_vs_matched_controls=round(v["p_vs_controls"], 4), mde_80_nat=round(v["mde_80"], 4),
                                    verdict=VERDICT[k], n_pairs=v["n_pairs"])
                  for k, v in SUMMARY.items()},
    "P2_matched_control_D": {f"L{k[0]}_E{k[1]}_N{k[2]}": [round(summarize(r)[0], 5) for r in CTRL_RUNS[k]]
                             for k in CTRL_RUNS},
    "P3_numerics": NUM,
}
with open("kl_unembedding_results.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, ensure_ascii=False)
print("Gespeichert: kl_unembedding_results.json")
print("Gate 0:", CALIB_OK)
for k, v in out["P2_causal"].items():
    print(k, "->", v["verdict"])
''')

md(r"""
## Auslegung

- **P1a/P1b** beantworten die Frage nach einer *direkten* Ausgabe-Anomalie: Schreibt ein Neuron ungewöhnlich stark auf KL-Wörter, verglichen mit dem, was zufällige Wortgruppen in derselben Schicht bekommen?
- **P2** beantwortet die kausale Frage: Ändert das Ausschalten die Vorhersage von KL-Wörtern in KL-Texten mehr als in den Kontrolltexten, und mehr als bei Neuronen desselben Experten mit ähnlicher Aktivität?
- Ein Ergebnis „spezifisch“ in P2 ohne Auffälligkeit in P1 hieße: Die Wirkung läuft über spätere Schichten, nicht über den direkten Schreibvektor. Umgekehrt wäre eine starke direkte Wirkung ohne P2-Effekt ein Hinweis, dass das Modell das Wort über andere Wege erreicht.
- **P3** gibt die Messgenauigkeit an. Liegt das Rauschen der Batchgröße über dem minimal nachweisbaren Effekt aus P2, ist P2 nicht auswertbar.

Die Entscheidungen oben sind die vorab festgelegten Regeln. Sie ersetzen keine Interpretation; die Interpretation steht im Bericht.
""")


def make_notebook(cells):
    nb = {
        "cells": [],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    for i, (kind, text) in enumerate(cells):
        lines = text.splitlines(keepends=True)
        cid = f"cell{i:03d}"
        if kind == "markdown":
            nb["cells"].append({"cell_type": "markdown", "id": cid, "metadata": {}, "source": lines})
        else:
            nb["cells"].append({"cell_type": "code", "id": cid, "metadata": {}, "execution_count": None,
                                "outputs": [], "source": lines})
    return nb


if __name__ == "__main__":
    import ast
    for kind, text in CELLS:
        if kind == "code" and not any(line.lstrip().startswith("!") for line in text.splitlines()):
            ast.parse(text)
    nb = make_notebook(CELLS)
    import sys
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "kl_unembedding_test.ipynb"
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print("geschrieben:", out, "| Zellen:", len(CELLS), "| Codezellen:", sum(1 for k, _ in CELLS if k == "code"))
