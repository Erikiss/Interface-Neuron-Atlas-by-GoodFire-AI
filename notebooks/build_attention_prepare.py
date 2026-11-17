"""Build the CPU-only Atlas preparation notebook, without model or atlas downloads."""

import ast
import hashlib
import json
from pathlib import Path

INTRO = """# Atlas Attention-Versuch vorbereiten

**CPU verwenden: Laufzeittyp → Hardwarebeschleuniger → Keine. Dann Alle ausführen.**

Dieses Notebook bereitet den Vergleich auf Qwen3.6-35B-A3B vor. Es lädt nur zwei
kleine Modellmetadaten, prüft die Architektur und erstellt ein Versuchsmanifest.
Modellgewichte, Atlas-Daten, Inferenz und Training werden nicht gestartet.

Optional: Einen bereits vorhandenen JSON-/NDJSON-Export ausgewählter Atlas-Pole
links im Dateibrowser nach `/content` hochladen und unten Pfad sowie Atlas-Release
eintragen. Ohne Export entsteht eine Vorbereitung ohne erfundene Neuronenauswahl.

Der Hauptversuch bleibt Qwen3-1.7B mit den bisherigen 286 Neuronen. Die Atlas-Gruppe
ist eigenständig. Das Ergebnis-ZIP lässt sich anschließend im Chat zur Prüfung hochladen.
"""

CONFIG = '''CODE_BRANCH = "codex/attention-atlas-preparation"
MODEL_REVISION = "345d467b117d1fd5eda9b879a5988de9ae5f47a7"
POLE_EXPORT = ""  # Optional, z.B. /content/selected_poles.ndjson
ATLAS_RELEASE = ""  # Erforderlich, wenn ein Pole-Export angegeben wird.
PLANNED_FREE_VRAM_GIB = None  # Optionaler Planwert; dieses Notebook braucht keine GPU.
'''

RUN = '''import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import zipfile

run = Path(tempfile.mkdtemp(prefix="atlas_attention_", dir="/content"))
out = run / "output"
out.mkdir()
status = {"state": "running", "model_run": False, "weights_downloaded": False}
failure = None
try:
    has_gpu = os.environ.get("COLAB_GPU", "0") not in ("", "0")
    if shutil.which("nvidia-smi"):
        result = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=15)
        has_gpu = has_gpu or (result.returncode == 0 and bool(result.stdout.strip()))
    if has_gpu:
        raise RuntimeError("Diese Vorbereitung braucht nur CPU. Hardwarebeschleuniger auf Keine stellen.")
    if bool(POLE_EXPORT) != bool(ATLAS_RELEASE):
        raise ValueError("POLE_EXPORT und ATLAS_RELEASE gemeinsam ausfüllen oder beide leer lassen.")
    code = run / "code"
    subprocess.run(["git", "clone", "--depth", "1", "--branch", CODE_BRANCH,
                    "https://github.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI.git", str(code)],
                   check=True, capture_output=True, text=True, timeout=120)
    code_commit = subprocess.check_output(["git", "-C", str(code), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(code / "src"))
    # Only the standard-library preparation modules; no model runtime import.
    from neuron_atlas.preflight import prepare_preflight
    from neuron_atlas.experiments import build_plan, read_poles
    files = {
        "config.json": "93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99",
        "model.safetensors.index.json": "41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83",
    }
    sources = []
    for name, expected in files.items():
        url = f"https://huggingface.co/Qwen/Qwen3.6-35B-A3B/resolve/{MODEL_REVISION}/{name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Metadaten-Prüfsumme oder Größe stimmt nicht: " + name)
        (out / name).write_bytes(raw)
        sources.append({"file": name, "bytes": len(raw), "sha256": expected, "url": url})
    preflight = prepare_preflight(out / "config.json", out / "model.safetensors.index.json",
                                  MODEL_REVISION, free_vram_gib=PLANNED_FREE_VRAM_GIB)
    records, digest = read_poles(Path(POLE_EXPORT)) if POLE_EXPORT else ([], None)
    plan = build_plan(preflight, records, atlas_release=ATLAS_RELEASE, selection_sha256=digest)
    for name, value in [("preflight.json", preflight), ("plan.json", plan),
                         ("sources.json", {"code_commit": code_commit, "metadata": sources})]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\\n")
    status["state"] = "prepared_not_executed"
    print("Vorbereitung fertig. Kein Modelllauf gestartet.")
    print("Gewichte laut Index:", round(preflight["storage"]["indexed_weight_gib"], 2), "GiB, ohne Laufzeitreserve")
    print("Ausgewählte Atlas-Pole:", plan["atlas"]["n_poles"])
    print("Checkpoint-Zuordnung und Laufzeitadapter bleiben vor Inferenz zu prüfen.")
except Exception as error:
    failure = error
    status.update(state="failed", error_type=type(error).__name__, error=str(error))
    (out / "diagnostic.txt").write_text(traceback.format_exc())
    print("Vorbereitung angehalten:", error)
finally:
    (out / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\\n")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\\n")
    archive = run / ("atlas_attention_preparation.zip" if failure is None else "atlas_attention_diagnostic.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(out.iterdir()):
            if path.is_file():
                zipped.write(path, path.name)
    print("ZIP:", archive)
    try:
        from google.colab import files as colab_files
        colab_files.download(str(archive))
    except Exception:
        print("ZIP bitte links im Colab-Dateibrowser herunterladen.")
if failure is not None:
    raise RuntimeError("Vorbereitung angehalten; Diagnose-ZIP enthält den Fehler.") from failure
'''


def build(directory: Path = Path(__file__).parent) -> Path:
    cells = []
    for index, (kind, source) in enumerate((("markdown", INTRO), ("code", CONFIG), ("code", RUN))):
        cell = {"cell_type": kind, "id": hashlib.sha256(f"atlas-prepare:{index}".encode()).hexdigest()[:12],
                "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            ast.parse(source)
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    doc = {"cells": cells, "metadata": {"colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"}},
           "nbformat": 4, "nbformat_minor": 5}
    target = directory / "attention_prepare.ipynb"
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return target


if __name__ == "__main__":
    print(build())
