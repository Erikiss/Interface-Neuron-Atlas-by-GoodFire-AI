"""Prüft gate0_diagnose_cell.py in einer nachgestellten Sitzung mit der vorigen Fassung (snap/), in der Gate 0
fertig gerechnet und dann abgebrochen hat (wie beim Nutzer: CALIB_OK False, keine Läufe). Danach dasselbe
mit den Variablen der neuen Fassung (rows mit zusätzlichen Schlüsseln)."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ATLAS_DATA_DIR", os.path.abspath(os.path.join(HERE, "../kl345/data")))
os.environ["ATLAS_OFFLINE"] = "1"
sys.path.insert(0, HERE)
import torch
from transformers import AutoTokenizer

import tiny_model as TM
from tiny_stream_test import save_checkpoint

DIAG = open(os.path.join(HERE, "gate0_diagnose_cell.py"), encoding="utf-8").read()
work = tempfile.mkdtemp(prefix="diag_", dir=HERE)
os.chdir(work)
full = TM.build()
tok = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
shard_of = save_checkpoint(full, work)
ok_all = True

for label, nb_path in (("vorige Fassung", os.path.join(HERE, "snap", "kl_unembedding_test.ipynb")),
                       ("neue Fassung", os.path.join(HERE, "snap2", "kl_unembedding_test.ipynb"))):
    cells = ["".join(c["source"]) for c in json.load(open(nb_path, encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
    cell = lambda m: next(s for s in cells if m in s)
    ns = {"__name__": "__notebook__", "tok": tok}
    exec(cell("# ---------- Konfiguration"), ns)
    ns.update(TEST_MODE=True, SMOKE=True, CANDIDATES=[(2, 3, 5), (1, 2, 1)], N_MATCH=2)   # STOP bleibt True wie beim Nutzer
    exec(cell("# ---------- Bibliothek"), ns)
    ns["STREAM"] = ns["LayerStreamer"](ns["skeleton"](full.config), shard_of, "cpu", torch.float32, 1, 64)
    exec(cell("# ---------- Modell laden"), ns)
    exec(cell("# ---------- Zielwörter"), ns)
    gate = cell("# ---------- Gate 0").replace("NeuronHook(model, 24, 124, 345, RC)", "NeuronHook(model, 2, 3, 5, RC)")
    gate = gate.replace("if TEST_MODE or model is None:", "if model is None:")
    try:
        exec(gate, ns)
        stopped = False
    except RuntimeError:
        stopped = True
    print(f"--- {label}: Gate 0 abgebrochen {stopped}, CALIB_OK {ns.get('CALIB_OK')}, Läufe vorhanden {'RUNS' in ns}")
    try:
        exec(compile(DIAG, "diagnose", "exec"), ns)
        print(f"PASS Diagnose-Zelle läuft ({label})")
    except Exception as ex:
        ok_all = False
        print(f"FAIL Diagnose-Zelle ({label}): {type(ex).__name__}: {ex}")

os.chdir(HERE)
shutil.rmtree(work, ignore_errors=True)
print("PROBLEME: keine" if ok_all else "PROBLEME: ja")
sys.exit(0 if ok_all else 1)
