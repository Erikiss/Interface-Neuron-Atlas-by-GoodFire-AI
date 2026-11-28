"""Trockenlauf des Modell-Zweigs für das echte Modell: Konfiguration und Gewichtsindex sind echt (lokale Kopien),
GPU, Festplatte und Download sind nachgebildet. Der Lauf endet absichtlich am Download; geprüft wird, dass
Konfiguration, Namen, Dateiliste und Speicherprüfung wie vorgesehen durchlaufen."""
import json
import os
import shutil
import tempfile
import types

import torch
import transformers
import huggingface_hub
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ATLAS_DATA_DIR", os.path.abspath(os.path.join(HERE, "../kl345/data")))
os.environ["ATLAS_OFFLINE"] = "1"

cfgdir = tempfile.mkdtemp(prefix="cfg_", dir=HERE)
shutil.copy(os.path.join(HERE, "cfg_Qwen3.6-35B-A3B.json"), os.path.join(cfgdir, "config.json"))
REAL_CFG = transformers.AutoConfig.from_pretrained(cfgdir)
IDX = os.path.join(HERE, "idx_bf16.json")
TOK = AutoTokenizer.from_pretrained(os.path.join(HERE, "tokdir"))
rec = {}


class _Stop(Exception):
    pass


def fake_snapshot(repo_id, allow_patterns=None, **kw):
    rec["repo"], rec["files"] = repo_id, list(allow_patterns)
    raise _Stop()


transformers.AutoConfig.from_pretrained = lambda *a, **k: REAL_CFG
transformers.AutoTokenizer.from_pretrained = lambda *a, **k: TOK
huggingface_hub.hf_hub_download = lambda repo_id, filename, **k: IDX
huggingface_hub.snapshot_download = fake_snapshot
torch.cuda.is_available = lambda: True
torch.cuda.get_device_properties = lambda i: types.SimpleNamespace(total_memory=42.4e9)
torch.cuda.get_device_name = lambda i: "NVIDIA A100-SXM4-40GB"
shutil.disk_usage = lambda p: types.SimpleNamespace(free=200e9)

import sys
NB_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "kl_unembedding_test.ipynb")
nb = json.load(open(NB_PATH, encoding="utf-8"))
cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
ns = {"__name__": "__notebook__"}
want = ["# ---------- Konfiguration", "# ---------- Bibliothek", "# ---------- Modell laden"]
for w in want:
    src = next(s for s in cells if w in s)
    try:
        exec(compile(src, w, "exec"), ns)
        print("ok:", w, flush=True)
    except _Stop:
        print("ok (Download erreicht):", w, flush=True)
    except Exception as ex:
        raise SystemExit(f"FEHLER in {w}: {type(ex).__name__}: {ex}")

files = rec.get("files", [])
print("Konfiguration: MODEL_ID =", ns["MODEL_ID"], "| TEST_MODE =", ns["TEST_MODE"], "| SPLIT_LAYER =", ns["SPLIT_LAYER"],
      "| STREAM_CHUNK =", ns["STREAM_CHUNK"], "| MIN_GPU_GB =", ns["MIN_GPU_GB"])
print("Download-Liste:", len(files), "Dateien, z. B.", files[:2])
skel = ns["skel"]
names = ns["needed_names"](skel)
wm = json.load(open(IDX, encoding="utf-8"))["weight_map"]
assert sorted({wm[n] for n in names}) == files, "Dateiliste passt nicht zu den benötigten Gewichten"
print("Dateiliste deckt alle", len(names), "benötigten Gewichte ab: ja")
print("Bereiche: DEV-Variable noch nicht gesetzt (vor dem Download): ", "DEV" not in ns)

# Zweiter Teil: Die Modellzelle wird erneut ausgeführt, während schon ein Modell geladen ist. Es darf nicht
# noch einmal geladen werden (kein Download-Aufruf), und eine andere Aufteilung muss abgelehnt werden.
import tempfile as _tf
from tiny_stream_test import make_full, save_checkpoint
LT = ["linear_attention", "full_attention", "linear_attention", "full_attention"]
tiny = make_full(LT)
ck = _tf.mkdtemp(prefix="ck_", dir=HERE)
loaded = ns["LayerStreamer"](ns["skeleton"](tiny.config), save_checkpoint(tiny, ck), "cpu", torch.float32, 1, 64)
model_src = next(s for s in cells if "# ---------- Modell laden" in s)
rec.clear()
ns.update(STREAM=loaded, SPLIT_LAYER=1)
exec(compile(model_src, "model cell again", "exec"), ns)
assert "files" not in rec, "Die Modellzelle hat trotz geladenem Modell den Download aufgerufen"
assert ns["STREAM"] is loaded and ns["model"] is loaded.model and ns["RC"] is not None
print("Erneutes Ausführen mit geladenem Modell: kein Download, Modell weiterverwendet: ja")
ns.update(SPLIT_LAYER=21)
try:
    exec(compile(model_src, "model cell, other split", "exec"), ns)
    raise SystemExit("FEHLER: andere Aufteilung wurde nicht abgelehnt")
except RuntimeError as ex:
    assert ns["STREAM"] is loaded and "files" not in rec
    print("Andere Aufteilung bei geladenem Modell wird abgelehnt:", str(ex)[:60])
shutil.rmtree(ck, ignore_errors=True)
shutil.rmtree(cfgdir, ignore_errors=True)
print("TROCKENLAUF OK")
