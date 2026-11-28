"""Prüft die Entscheidung von Gate 0 und Gate 0b in der echten Gate-0-Zelle mit den echten Atlas-Fenstern.

Die Modellwerte werden vorgegeben (activation_at ersetzt): In Fenstern ab Textanfang (u < 84) liefert das Modell genau
die Atlas-Werte (mal theta) und wählt den Experten genau dort, wo der Atlas einen Wert hat. In abgeschnittenen
Fenstern sind Werte und Routing zufällig. Erwartet: Gate 0 nicht bestanden, Gate 0b bestanden.
  Fall 1  GATE0B = True,  Abbruch bei Fehlschlag: läuft weiter.
  Fall 2  GATE0B = False, Abbruch bei Fehlschlag: bricht ab (Gate 0 entscheidet).
  Fall 3  GATE0B = True, aber Modellwerte bei Textanfang um 10 % zu klein: bricht ab (Gate 0b nicht bestanden).
"""
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

NB_PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "kl_unembedding_test.ipynb")
cells = ["".join(c["source"]) for c in json.load(open(NB_PATH, encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
cell = lambda m: next(s for s in cells if m in s)
work = tempfile.mkdtemp(prefix="g0b_", dir=HERE)
os.chdir(work)
full = TM.build()
tok = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
shard_of = save_checkpoint(full, work)
problems = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)
    if not ok:
        problems.append(name)


ns = {"__name__": "__notebook__", "tok": tok}
exec(cell("# ---------- Konfiguration"), ns)
ns.update(TEST_MODE=True, SMOKE=False, CANDIDATES=[(2, 3, 5), (1, 2, 1)], N_MATCH=2)
exec(cell("# ---------- Bibliothek"), ns)
ns["STREAM"] = ns["LayerStreamer"](ns["skeleton"](full.config), shard_of, "cpu", torch.float32, 1, 64)
ns["STREAM"].verbose = False
exec(cell("# ---------- Modell laden"), ns)
exec(cell("# ---------- Zielwörter"), ns)
gate = cell("# ---------- Gate 0").replace("NeuronHook(model, 24, 124, 345, RC)", "NeuronHook(model, 2, 3, 5, RC)")
gate = gate.replace("if TEST_MODE or model is None:", "if model is None:")

# Zuordnung Token-IDs -> Atlas-Fenster (wie in der Zelle übersetzt)
import gzip
files = os.path.join(os.environ["ATLAS_DATA_DIR"], "2026-11-09-082204", "files")
dots = json.load(gzip.open(os.path.join(files, "neuron_landscape_data", "L24_E124_N345__pos.json.gz"), "rt"))["dots"]
theta = float(json.load(gzip.open(os.path.join(files, "subcircuit_modes_data", "L24_E124_N345__pos.json.gz"), "rt"))["theta"])
by_ids = {}
for d in dots:
    by_ids[tuple(tok.encode("".join(s for s, _ in d["t"]), add_special_tokens=False))] = d


def make_fake(scale_full):
    g = torch.Generator().manual_seed(1)

    def fake(model, hook, routing, ids):
        d = by_ids[tuple(ids.tolist())]
        T = len(d["t"])
        act, w = torch.zeros(T), torch.zeros(T)
        for j, (_, v) in enumerate(d["t"]):
            if d["u"] < 84:
                if v is not None:
                    act[j], w[j] = v * theta * scale_full, 0.5
            else:
                act[j] = float(torch.randn(1, generator=g)) * theta
                w[j] = 0.5 if float(torch.rand(1, generator=g)) < 0.5 else 0.0
        return act, w
    return fake


def run_gate(gate0b, scale_full):
    ns.update(GATE0B=gate0b, STOP_IF_CALIBRATION_FAILS=True, activation_at=make_fake(scale_full))
    try:
        exec(gate, ns)
        return False, None
    except RuntimeError as ex:
        return True, str(ex)


stopped, msg = run_gate(True, 1.0)
check("Fall 1: Gate 0 fails, Gate 0b passes, GATE0B = True -> the run continues",
      not stopped and ns["CALIB_OK"] is False and ns["CALIB_B_OK"] is True
      and abs(ns["GATE0B_STATS"]["pearson_r"] - 1) < 1e-6 and abs(ns["GATE0B_STATS"]["slope_through_origin"] - 1) < 1e-6
      and ns["GATE0B_STATS"]["share_routing_ok"] == 1.0,
      f"stats {ns['GATE0B_STATS']}")
stopped, msg = run_gate(False, 1.0)
check("Fall 2: same values, GATE0B = False -> Gate 0 decides and the run stops",
      stopped and msg.startswith("Gate 0 nicht bestanden") and ns["CALIB_B_OK"] is True, msg)
stopped, msg = run_gate(True, 0.9)
check("Fall 3: values 10 % too small on full-context windows -> Gate 0b fails and the run stops",
      stopped and msg.startswith("Gate 0b nicht bestanden") and ns["CALIB_B_OK"] is False
      and abs(ns["GATE0B_STATS"]["slope_through_origin"] - 0.9) < 1e-6, f"{msg} | slope {ns['GATE0B_STATS']['slope_through_origin']}")

# Ergebniszelle nimmt Gate 0b auf (mit Platzhaltern für die übrigen Teile)
ns.update(P1={}, SUMMARY={}, VERDICT={}, CTRL_RUNS={}, NUM={}, PAIRS=[], summarize=lambda r: (0.0, 0.0, 0.0, 0.0))
exec(cell("# ---------- Ergebnis speichern"), ns)
res = json.load(open("kl_unembedding_results.json", encoding="utf-8"))
check("results file records Gate 0 and Gate 0b separately",
      res["gate0_calibration_ok"] is False and res["gate0b_amended"]["ok"] is False and res["gate0b_amended"]["used"] is True
      and "Abweichung vom Plan" in res["gate0b_amended"]["note"])
os.chdir(HERE)
shutil.rmtree(work, ignore_errors=True)
print("PROBLEME:", problems if problems else "keine")
sys.exit(1 if problems else 0)
