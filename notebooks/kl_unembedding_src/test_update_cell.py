"""Stellt die laufende Colab-Sitzung nach und prüft die Aktualisierungszelle (update_cell.py).

1. Sitzung mit der ALTEN Fassung (snap/kl_unembedding_test.ipynb): Konfiguration, Bibliothek, Modell (winziges
   Modell, schichtweise geladen mit dem alten LayerStreamer), Zielwörter, dann Gate 0 - mitten im Rechnen der
   Fenster unterbrochen (wie Strg-C). Die alte Fassung lässt dabei ihren Hook am Modell hängen.
2. Die Aktualisierungszelle wird unverändert ausgeführt; nur der Download von GitHub ist durch die lokale neue
   Fassung ersetzt. Für das winzige Modell gilt in Gate 0 dieselbe Ersatzschicht wie im harness (Schicht 2).
3. Geprüft: dasselbe Modellobjekt (nichts neu geladen), alte Hooks entfernt, alle Zellen gelaufen, Ergebnisse
   gleich denen eines frischen Laufs der neuen Fassung (Referenz: argv[1], die Ergebnisdatei des harness).
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ATLAS_DATA_DIR", os.path.abspath(os.path.join(HERE, "../kl345/data")))
os.environ["ATLAS_OFFLINE"] = "1"
sys.path.insert(0, HERE)
import torch
from transformers import AutoTokenizer

import tiny_model as TM
from tiny_stream_test import save_checkpoint

REF = json.load(open(sys.argv[1], encoding="utf-8"))
OLD_NB = os.path.join(HERE, "snap", "kl_unembedding_test.ipynb")
NEW_NB = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(HERE, "kl_unembedding_test.ipynb")
problems = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)
    if not ok:
        problems.append(name)


def cells_of(path):
    return ["".join(c["source"]) for c in json.load(open(path, encoding="utf-8"))["cells"] if c["cell_type"] == "code"]


work = tempfile.mkdtemp(prefix="upd_", dir=HERE)
os.chdir(work)
full = TM.build()
tok = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
shard_of = save_checkpoint(full, work)
old = cells_of(OLD_NB)
old_cell = lambda m: next(s for s in old if m in s)
ns = {"__name__": "__notebook__", "tok": tok}
t0 = time.time()

# ---- 1. Sitzung mit der alten Fassung
exec(old_cell("# ---------- Konfiguration"), ns)
ns.update(TEST_MODE=True, SMOKE=True, STOP_IF_CALIBRATION_FAILS=False, CANDIDATES=[(2, 3, 5), (1, 2, 1)], N_MATCH=2)
exec(old_cell("# ---------- Bibliothek"), ns)
ns["STREAM"] = ns["LayerStreamer"](ns["skeleton"](full.config), shard_of, "cpu", torch.float32, 1, 64)
exec(old_cell("# ---------- Modell laden"), ns)
exec(old_cell("# ---------- Zielwörter"), ns)
stream0, model0 = ns["STREAM"], ns["model"]
orig_act, calls = ns["activation_at"], {"n": 0}


def interrupting(*a, **kw):
    calls["n"] += 1
    if calls["n"] == 5:
        raise KeyboardInterrupt()
    return orig_act(*a, **kw)


ns["activation_at"] = interrupting
gate_old = old_cell("# ---------- Gate 0").replace("NeuronHook(model, 24, 124, 345, RC)", "NeuronHook(model, 2, 3, 5, RC)")
gate_old = gate_old.replace("if TEST_MODE or model is None:", "if model is None:")
try:
    exec(gate_old, ns)
    interrupted = False
except KeyboardInterrupt:
    interrupted = True
mlp2 = ns["text_model"](ns["model"]).layers[2].mlp
stray = len(mlp2._forward_hooks) + len(mlp2._forward_pre_hooks)
check("1 old session: Gate 0 interrupted with the old hook left on the model", interrupted and stray == 2,
      f"hooks on layer 2 block {stray}, old cache {len(stream0.cache)} texts, {time.time() - t0:.0f} s")

# ---- 2. Aktualisierungszelle, Download durch die lokale neue Fassung ersetzt
new_nb = json.load(open(NEW_NB, encoding="utf-8"))
for c in new_nb["cells"]:                                  # nur für das winzige Modell: Ersatzschicht wie im harness
    s = "".join(c["source"])
    if "# ---------- Gate 0" in s:
        s = s.replace("NeuronHook(model, 24, 124, 345, RC)", "NeuronHook(model, 2, 3, 5, RC)")
        s = s.replace("if TEST_MODE or model is None:", "if model is None:")
        c["source"] = s.splitlines(keepends=True)
served = json.dumps(new_nb).encode()
seen_urls = []


def fake_urlopen(url, timeout=None):
    seen_urls.append(url)
    return io.BytesIO(served)


urllib.request.urlopen = fake_urlopen
ns["activation_at"] = orig_act
update_src = open(os.path.join(HERE, "update_cell.py"), encoding="utf-8").read()
exec(compile(update_src, "update cell", "exec"), ns)

# ---- 3. Prüfen
gates = [ns["text_model"](ns["model"]).layers[i].mlp.gate for i in range(1, 4)]
check("2 the update cell fetched the new notebook from the branch on GitHub",
      seen_urls == ["https://raw.githubusercontent.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI/"
                    "claude/neuronatlas-interface-api-h1uw92/notebooks/kl_unembedding_test.ipynb"])
check("3 the loaded model was kept and switched to the new classes",
      ns["STREAM"] is stream0 and ns["model"] is model0 and type(ns["STREAM"]) is ns["LayerStreamer"]
      and ns["STREAM"].stats["layer_loads"] == 1)
check("4 no hook of the old session is left; exactly one routing hook per gate",
      len(mlp2._forward_hooks) + len(mlp2._forward_pre_hooks) == 0 and all(len(g._forward_hooks) == 1 for g in gates)
      and len(ns["_LIVE_HOOKS"]) == 0, f"gate hooks {[len(g._forward_hooks) for g in gates]}")
res = json.load(open("kl_unembedding_results.json", encoding="utf-8"))
same_p2 = all(abs(res["P2_causal"][k]["D"] - REF["P2_causal"][k]["D"]) <= 1e-9 for k in REF["P2_causal"])
same_ctrl = all(all(abs(a - b) <= 1e-9 for a, b in zip(res["P2_matched_control_D"][k], REF["P2_matched_control_D"][k]))
                for k in REF["P2_matched_control_D"])
check("5 all cells ran; results equal a fresh run of the new version",
      same_p2 and same_ctrl and res["gate0"] is not None and res["streaming"]["texts_prepared"] == len(ns["STREAM"].cache),
      f"P2 {sorted(res['P2_causal'])}, gate0 slots {res['gate0']['slots'] if res['gate0'] else None}")
os.chdir(HERE)
shutil.rmtree(work, ignore_errors=True)
print("PROBLEME:", problems if problems else "keine", f"| {time.time() - t0:.0f} s", flush=True)
sys.exit(1 if problems else 0)
