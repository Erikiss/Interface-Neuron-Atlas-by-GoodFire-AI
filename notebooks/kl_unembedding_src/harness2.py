"""Führt alle Zellen des Notebooks im Testmodus aus und prüft danach erneutes Ausführen, Abbruch und Fortsetzen.

Das winzige Modell wird als Checkpoint geschrieben und mit den Klassen aus der Bibliothekszelle des Notebooks
geladen (LayerStreamer, Aufteilung bei Schicht 1), also mit genau dem Code, der im echten Lauf läuft.
Gate 0 läuft gegen die lokalen Atlas-Dateien mit einer Ersatzschicht (Schicht 2, Neuron 3/5).
Alles läuft in einem frischen Arbeitsordner; dort liegt auch die Ablage (im Testmodus der Arbeitsordner).

Zusätzlich geprüft:
  A  Läufe-Zelle erneut ausgeführt: kein Modelllauf, Ergebnisse unverändert.
  B  Läufe-Zelle mitten im Lauf abgebrochen: kein Hook bleibt hängen; Fortsetzen ergibt dieselben Ergebnisse.
  C  Bibliotheks- und Modellzelle erneut ausgeführt: Modell bleibt, keine doppelten Hooks, Folgezellen laufen.
  D  Frische Sitzung: Kontroll-Neuronen und fertige Läufe kommen aus der Ablage, ohne einen einzigen Modelllauf.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ATLAS_DATA_DIR", os.path.abspath(os.path.join(HERE, "../kl345/data")))   # lokaler Atlas-Spiegel
import torch
from transformers import AutoTokenizer

sys.path.insert(0, HERE)
import tiny_model as TM
from tiny_stream_test import save_checkpoint

NB_PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "kl_unembedding_test.ipynb")
nb = json.load(open(NB_PATH, encoding="utf-8"))
cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
full = TM.build()
tok = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
work = tempfile.mkdtemp(prefix="harness_", dir=HERE)
os.chdir(work)
shard_of = save_checkpoint(full, os.path.join(work))
ns = {"__name__": "__notebook__", "tok": tok}
t0 = time.time()
problems = []


def find(marker):
    return next(s for s in cells if marker in s)


def fail(msg):
    print("FEHLER:", msg, flush=True)
    os.chdir(HERE)
    shutil.rmtree(work, ignore_errors=True)
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


def count_model_calls(src, label):
    count = {"n": 0}
    handle = ns["model"].register_forward_pre_hook(lambda mod, args: count.__setitem__("n", count["n"] + 1))
    try:
        run(src, label)
    finally:
        handle.remove()
    return count["n"]


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
        print("GATE 0 (Ersatzschicht) =", ns.get("CALIB_OK"), "| GATE 0b =", ns.get("CALIB_B_OK"), flush=True)
        continue
    run(src, f"[cell {i}]")
print("ALLE ZELLEN OK (Testmodus, geschichtetes Laden, winziges Modell)", flush=True)
shutil.copy("kl_unembedding_results.json", os.path.join(HERE, "ref_results_harness.json"))

runs_src = find("# ---------- Läufe")
ctrl_src = find("# ---------- Aktivitätsgleiche")
ref_runs, ref_ctrl, ref_CTRL = copy.deepcopy(ns["RUNS"]), copy.deepcopy(ns["CTRL_RUNS"]), copy.deepcopy(ns["CTRL"])
saved = sorted(f for f in os.listdir(".") if f.startswith("kl_unembedding"))
check("0 the streamed model loaded each lower layer once per prefill call; the storage holds every file",
      ns["STREAM"].stats["layer_loads"] >= 1 and ns["STREAM"].stats["texts"] == len(ns["STREAM"].cache)
      and {"kl_unembedding_gate0.json", "kl_unembedding_controls.json", "kl_unembedding_partial.json",
           "kl_unembedding_results.json"} <= set(saved),
      f"stats {ns['STREAM'].stats}, files {saved}")

# A: Zelle erneut ausführen -> nichts wird neu gerechnet
n_calls = count_model_calls(runs_src, "[runs cell again]")
check("A re-running the runs cell computes nothing and keeps the results",
      n_calls == 0 and close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl), f"model calls {n_calls}")

# B: Abbruch mitten im Lauf, dann fortsetzen (ohne gesicherten Zwischenstand: Zustand der Sitzung allein)
ns.pop("RUN_STATE")
os.remove("kl_unembedding_partial.json")
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
part = json.load(open("kl_unembedding_partial.json", encoding="utf-8"))
check("B1 an interrupt leaves no hook and no frozen routing behind; the finished run is saved",
      interrupted and len(ns["_LIVE_HOOKS"]) == 0 and ns["RC"].mode == "free" and done_before == 1
      and len(part["runs_list"]) + len(part["controls_list"]) == 1,
      f"live hooks {len(ns['_LIVE_HOOKS'])}, routing {ns['RC'].mode}, finished runs kept {done_before}")
run(runs_src, "[runs cell resumed]")
check("B2 resuming after the interrupt gives the same results as an uninterrupted run",
      close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl))
check("B3 the partial results file matches", json.load(open("kl_unembedding_partial.json", encoding="utf-8"))["sig"] == ns["RUN_SIG"])

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
os.remove("kl_unembedding_partial.json")
run(runs_src, "[runs cell after re-running library and model cells]")
check("C2 results after re-running library and model cells are unchanged",
      close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl))

# D: frische Sitzung (kein Zustand im Speicher): Kontrollen und Läufe kommen aus der Ablage
ns.pop("RUN_STATE")
ns.pop("CTRL")
n_ctrl = count_model_calls(ctrl_src, "[controls cell in a fresh session]")
check("D1 a fresh session takes the control neurons from the storage without a model run",
      n_ctrl == 0 and ns["CTRL"] == ref_CTRL, f"model calls {n_ctrl}")
n_runs = count_model_calls(runs_src, "[runs cell in a fresh session]")
check("D2 a fresh session takes all finished runs from the storage without a model run",
      n_runs == 0 and close(ns["RUNS"], ref_runs) and close(ns["CTRL_RUNS"], ref_ctrl), f"model calls {n_runs}")

os.chdir(HERE)
shutil.rmtree(work, ignore_errors=True)
print("PROBLEME:", problems if problems else "keine", flush=True)
sys.exit(1 if problems else 0)
