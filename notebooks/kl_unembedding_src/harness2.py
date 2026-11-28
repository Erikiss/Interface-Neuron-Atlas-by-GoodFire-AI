"""Führt alle Zellen des Notebooks im Testmodus aus und prüft danach das Verhalten bei erneutem Ausführen.

Das winzige Modell wird als Checkpoint geschrieben und mit den Klassen aus der Bibliothekszelle des Notebooks
geladen (LayerStreamer, Aufteilung bei Schicht 1), also mit genau dem Code, der im echten Lauf läuft.
Gate 0 läuft gegen die lokalen Atlas-Dateien mit einer Ersatzschicht (Schicht 2, Neuron 3/5).

Zusätzlich geprüft:
  A  Läufe-Zelle erneut ausgeführt: kein Modelllauf, Ergebnisse unverändert.
  B  Läufe-Zelle mitten im Lauf abgebrochen: kein Hook bleibt hängen; Fortsetzen ergibt dieselben Ergebnisse.
  C  Bibliotheks- und Modellzelle erneut ausgeführt: Modell bleibt, keine doppelten Hooks, Folgezellen laufen.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

os.environ.setdefault("ATLAS_DATA_DIR", os.path.abspath("../kl345/data"))   # lokaler Atlas-Spiegel (nicht im Repo)
os.environ["ATLAS_OFFLINE"] = "1"
import torch
from transformers import AutoTokenizer

import tiny_model as TM
from tiny_stream_test import save_checkpoint

full = TM.build()
tok = AutoTokenizer.from_pretrained("tokdir")
ckpt = tempfile.mkdtemp(prefix="tinyck_", dir=os.path.abspath("."))
shard_of = save_checkpoint(full, ckpt)

NB_PATH = sys.argv[1] if len(sys.argv) > 1 else "kl_unembedding_test.ipynb"
nb = json.load(open(NB_PATH, encoding="utf-8"))
cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
ns = {"__name__": "__notebook__", "tok": tok}
t0 = time.time()
problems = []


def find(marker):
    return next(s for s in cells if marker in s)


def fail(msg):
    print("FEHLER:", msg, flush=True)
    shutil.rmtree(ckpt, ignore_errors=True)
    sys.exit(1)


def run(src, label):
    try:
        exec(compile(src, label, "exec"), ns)
        print(f"{label} ok | {time.time() - t0:.0f} s", flush=True)
    except Exception as ex:
        traceback.print_exc()
        fail(f"{label}: {type(ex).__name__}: {ex}")


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)
    if not ok:
        problems.append(name)


def close(a, b, tol=1e-12):
    if isinstance(a, dict):
        return set(a) == set(b) and all(close(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


run(find("# ---------- Konfiguration"), "[config]")
ns.update(TEST_MODE=True, SMOKE=True, STOP_IF_CALIBRATION_FAILS=False,
          CANDIDATES=[(2, 3, 5), (1, 2, 1)], N_MATCH=2)
run(find("# ---------- Bibliothek"), "[library]")
ns["STREAM"] = ns["LayerStreamer"](ns["skeleton"](full.config), shard_of, "cpu", torch.float32, 1, 64)

for i, src in enumerate(cells):
    if "!pip" in src or "# ---------- Konfiguration" in src or "# ---------- Bibliothek" in src:
        continue
    if "# ---------- Gate 0" in src:
        src = src.replace("NeuronHook(model, 24, 124, 345, RC)", "NeuronHook(model, 2, 3, 5, RC)")
        ns["TEST_MODE"] = False          # Gate 0 läuft gegen die Atlas-Dateien (mit Ersatzschicht)
        run(src, f"[cell {i} Gate 0 (Ersatzschicht)]")
        ns["TEST_MODE"] = True
        print("GATE 0 (Ersatzschicht) =", ns.get("CALIB_OK"), flush=True)
        continue
    run(src, f"[cell {i}]")
print("ALLE ZELLEN OK (Testmodus, geschichtetes Laden, winziges Modell)", flush=True)

runs_src = find("# ---------- Läufe")
ref_runs, ref_ctrl = copy.deepcopy(ns["RUNS"]), copy.deepcopy(ns["CTRL_RUNS"])
check("0 the streamed model loaded each lower layer once per prefill call",
      ns["STREAM"].stats["layer_loads"] >= 1 and ns["STREAM"].stats["texts"] == len(ns["STREAM"].cache),
      f"stats {ns['STREAM'].stats}, cached {len(ns['STREAM'].cache)}")

# A: Zelle erneut ausführen -> nichts wird neu gerechnet
count = {"n": 0}
handle = ns["model"].register_forward_pre_hook(lambda mod, args: count.__setitem__("n", count["n"] + 1))
run(runs_src, "[runs cell again]")
handle.remove()
check("A re-running the runs cell computes nothing and keeps the results",
      count["n"] == 0 and close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl), f"model calls {count['n']}")

# B: Abbruch mitten im Lauf, dann fortsetzen
ns.pop("RUN_STATE")
orig_table, calls = ns["ablation_table"], {"n": 0}


def interrupting(*a, **kw):
    calls["n"] += 1
    if calls["n"] == 9:
        raise KeyboardInterrupt()
    return orig_table(*a, **kw)


ns["ablation_table"] = interrupting
try:
    exec(compile(runs_src, "[runs cell interrupted]", "exec"), ns)
    interrupted = False
except KeyboardInterrupt:
    interrupted = True
ns["ablation_table"] = orig_table
done_before = len(ns["RUN_STATE"]["runs"]) + len(ns["RUN_STATE"]["ctrl"])
check("B1 an interrupt leaves no hook and no frozen routing behind",
      interrupted and len(ns["_LIVE_HOOKS"]) == 0 and ns["RC"].mode == "free" and done_before == 1,
      f"live hooks {len(ns['_LIVE_HOOKS'])}, routing {ns['RC'].mode}, finished runs kept {done_before}")
run(runs_src, "[runs cell resumed]")
check("B2 resuming after the interrupt gives the same results as an uninterrupted run",
      close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl))
check("B3 the partial results file matches",
      json.load(open("kl_unembedding_partial.json", encoding="utf-8"))["sig"] == ns["RUN_SIG"])

# C: Bibliotheks- und Modellzelle erneut ausführen, während noch ein vergessener Hook mit Eingriff am Modell hängt
old_stream, old_model = ns["STREAM"], ns["model"]
stray = ns["NeuronHook"](ns["model"], 2, 3, 5, ns["RC"])
stray.mask, stray.scale = torch.ones(1, 5, dtype=torch.bool), 0.0
mlp2 = ns["moe_block"](ns["model"], 2)
run(find("# ---------- Bibliothek"), "[library cell again]")
run(find("# ---------- Modell laden"), "[model cell again]")
check("C0 a hook left over from before the library cell was re-run is still found and removed",
      len(mlp2._forward_hooks) + len(mlp2._forward_pre_hooks) == 0 and len(ns["_LIVE_HOOKS"]) == 0,
      f"hooks on the block {len(mlp2._forward_hooks) + len(mlp2._forward_pre_hooks)}")
gate = ns["text_model"](ns["model"]).layers[1].mlp.gate
check("C1 the model is kept, classes are adopted, routing hooks are not duplicated",
      ns["STREAM"] is old_stream and ns["model"] is old_model and type(ns["STREAM"]) is ns["LayerStreamer"]
      and all(type(lz) is ns["LazyLayer"] for lz in ns["STREAM"].lazy) and len(gate._forward_hooks) == 1,
      f"gate hooks {len(gate._forward_hooks)}")
ns.pop("RUN_STATE")
run(runs_src, "[runs cell after re-running library and model cells]")
check("C2 results after re-running library and model cells are unchanged",
      close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl))

shutil.rmtree(ckpt, ignore_errors=True)
print("PROBLEME:", problems if problems else "keine", flush=True)
sys.exit(1 if problems else 0)
