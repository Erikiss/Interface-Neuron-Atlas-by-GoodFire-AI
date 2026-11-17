"""Build the self-contained, directly downloadable Colab GPU adapter check."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMIT = '4ffee35204a6e0c0626ecfa4b2a2e1371a47bfd4'
HELPER = HERE / 'atlas_gpu_check_runtime.py'


def build():
    setup = '''# GPU auswählen → Alle ausführen → Ergebnis-ZIP wird heruntergeladen.
import datetime, json, os, pathlib, subprocess, sys, tempfile, traceback, uuid, zipfile
SOURCE_COMMIT = '4ffee35204a6e0c0626ecfa4b2a2e1371a47bfd4'
CPU_SMOKE = False  # Nur für lokale Entwicklertests; qualifiziert keine GPU.
OUT = pathlib.Path('/content/atlas_gpu_check') / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8])
OUT.mkdir(parents=True, exist_ok=True)

def emergency_bundle(exc):
    (OUT / 'status.json').write_text(json.dumps({'state': 'failed', 'stage': 'setup', 'gpu_qualified': False, 'error_type': type(exc).__name__, 'error': str(exc)}, indent=2))
    (OUT / 'traceback.txt').write_text(traceback.format_exc())
    if not (OUT / 'metrics.json').exists():
        (OUT / 'metrics.json').write_text(json.dumps({'purpose': 'technical_random_model_check', 'checks': {}, 'pretrained_weights_loaded': False}, indent=2))
    with (OUT / 'setup.log').open('a') as setup_log:
        setup_log.write(traceback.format_exc())
    target = OUT / 'atlas_gpu_check.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in OUT.iterdir():
            if item.is_file() and item != target:
                z.write(item, item.name)
    print('FEHLER. Diagnose:', target)
    try:
        from google.colab import files
        files.download(str(target))
    except Exception as download_error:
        print('Download bei Bedarf im Colab-Dateibrowser:', target, type(download_error).__name__)

try:
    import torch
    print('Torch:', torch.__version__)
    if not CPU_SMOKE:
        if not torch.cuda.is_available():
            raise RuntimeError('Keine GPU. Colab: Laufzeit → Laufzeittyp ändern → A100 oder L4. Kein Warten.')
        capability = torch.cuda.get_device_capability()
        print('GPU:', torch.cuda.get_device_name(), 'Capability:', capability)
        if capability[0] < 8 or not torch.cuda.is_bf16_supported():
            raise RuntimeError('A100/L4 oder andere native BF16-GPU erforderlich. T4 wird nicht unterstützt.')
    # Torch bleibt die von Colab bereitgestellte CUDA-Installation; kein stiller Upgrade.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'transformers==5.11.0'], check=True)
    REPO = pathlib.Path(tempfile.mkdtemp(prefix='atlas_gpu_source_', dir='/content'))
    def git(*args):
        return subprocess.run(['git', '-C', str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '--quiet')
    git('remote', 'add', 'origin', 'https://github.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI.git')
    git('fetch', '--quiet', '--depth', '1', 'origin', SOURCE_COMMIT)
    git('checkout', '--quiet', '--detach', 'FETCH_HEAD')
    if git('rev-parse', 'HEAD') != SOURCE_COMMIT:
        raise RuntimeError('Source-Commit stimmt nicht überein.')
    (OUT / 'source.json').write_text(json.dumps({'commit': SOURCE_COMMIT, 'transformers': '5.11.0', 'weights_loaded': False}, indent=2))
    print('Quellcode geprüft:', SOURCE_COMMIT)
except BaseException as exc:
    emergency_bundle(exc)
    raise RuntimeError('Setup abgebrochen; Diagnose-ZIP wurde vorbereitet.') from None
'''
    helper_cell = '# Eingebetteter Prüfcode; benötigt keine unveröffentlichten Repository-Dateien.\nHELPER_SOURCE = ' + repr(HELPER.read_text()) + '\nHELPER_PATH = OUT / "technical_check.py"\nHELPER_PATH.write_text(HELPER_SOURCE)\n'
    run = '''# Frischer Python-Prozess nach Paketinstallation; keine geerbten HF-Imports.
command = [sys.executable, str(HELPER_PATH), '--outputdir', str(OUT)]
if CPU_SMOKE:
    command.append('--cpu-smoke')
env = dict(os.environ)
env['PYTHONPATH'] = str(REPO / 'src')
env['TOKENIZERS_PARALLELISM'] = 'false'
env['HF_HUB_OFFLINE'] = '1'
env['TRANSFORMERS_OFFLINE'] = '1'
print('Technische Prüfung startet; Zufallsmodell, keine Atlas-Messung.')
try:
    with (OUT / 'run.log').open('w') as log:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line.rstrip(), flush=True)
            RETURN_CODE = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
except BaseException as exc:
    emergency_bundle(exc)
    raise RuntimeError('Prüfung unterbrochen; Diagnose-ZIP wurde vorbereitet.') from None
'''
    final = '''# Auch bei einem technischen FAIL werden Protokoll und Messwerte heruntergeladen.
ZIP = OUT / 'atlas_gpu_check.zip'
try:
    status = json.loads((OUT / 'status.json').read_text())
except Exception:
    status = {'state': 'failed', 'stage': 'child_process', 'gpu_qualified': False}
status['return_code'] = RETURN_CODE
if RETURN_CODE != 0 or status.get('state') != 'complete':
    status['child_state'] = status.get('state', 'missing')
    status['state'] = 'failed'
    status['gpu_qualified'] = False
    status.setdefault('stage', 'child_process')
(OUT / 'status.json').write_text(json.dumps(status, indent=2))
# run.log enthält auch die letzte Kindprozess-Ausgabe; ZIP deshalb neu abschließen.
with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
    for item in sorted(OUT.iterdir()):
        if item.is_file() and item != ZIP:
            z.write(item, item.name)
print('Status:', status['state'], '| GPU-Prüfung:', status.get('gpu_qualified', False))
print('Ergebnis:', ZIP)
try:
    from google.colab import files
    files.download(str(ZIP))
except Exception as exc:
    print('ZIP im Colab-Dateibrowser herunterladen:', ZIP, type(exc).__name__)
if RETURN_CODE or status['state'] != 'complete':
    raise RuntimeError('Technische Prüfung fehlgeschlagen. Das ZIP enthält den tatsächlichen Fehler.')
print('Technischer Zufallsmodell-Test bestanden; keine Ergebnisse auf echten Atlas-Gewichten.')
'''
    introduction = '''# Technischer GPU-Check — keine Modellgewichte nötig

**GPU auswählen → Alle ausführen → Ergebnis-ZIP herunterladen.** A100 oder L4 verwenden. T4 wird klar abgelehnt; es gibt keine Warteschleife.

Das Notebook prüft BF16-Hooks auf einem **zufällig initialisierten** nativen Qwen3.5-MoE mit zwei Layern und acht Experten. Hidden-, Head- und MLP-Dimensionen entsprechen dem vorgesehenen Modell; Layer-/Expertenzahl und Routing sind reduziert. Erwarteter GPU-Speicher unter etwa 3 GiB, ohne Garantie. Es lädt weder veröffentlichte Gewichte noch Drive-/Atlas-/H286-Daten.

Geprüft werden Sham-Identität, Query-Puls **nach** q_norm bei unverändertem Projektions-Gate, Shared-Aktivierungen, routed Schattenberechnung gegen den tatsächlich beobachteten nativen Expertenbatch sowie RNG und Hook-Cleanup. Toleranzen sind technische Prüfschwellen, keine wissenschaftliche Kalibrierung. Source ist auf `4ffee35204a6e0c0626ecfa4b2a2e1371a47bfd4`, Transformers auf `5.11.0` festgelegt. Die vorhandene Colab-Torch/CUDA-Version wird protokolliert und tatsächlich getestet, ohne sie still zu ersetzen. Das bestätigt keine Atlas-Koordinatenabbildung und keinen vollständigen Checkpointlauf.

Auch bei einem Fehler wird ein Diagnose-ZIP mit Status, Messwerten und Log vorbereitet. Nach dem Test GPU-Laufzeit bei Bedarf selbst beenden.
'''
    def cell(kind, source):
        value = {'id': hashlib.sha256(source.encode()).hexdigest()[:12], 'cell_type': kind,
                 'metadata': {}, 'source': source.splitlines(keepends=True)}
        if kind == 'code':
            value.update(execution_count=None, outputs=[])
        return value
    notebook = {'nbformat': 4, 'nbformat_minor': 5,
                'metadata': {'colab': {'name': 'atlas_gpu_check.ipynb'},
                             'accelerator': 'GPU', 'kernelspec': {'name': 'python3', 'display_name': 'Python 3'}},
                'cells': [cell('markdown', introduction), cell('code', setup), cell('code', helper_cell),
                          cell('code', run), cell('code', final)]}
    (HERE / 'atlas_gpu_check.ipynb').write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n')


if __name__ == '__main__':
    build()
