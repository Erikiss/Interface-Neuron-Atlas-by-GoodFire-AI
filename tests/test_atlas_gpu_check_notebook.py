"""The downloadable notebook embeds its tested helper; no model downloads."""
from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _embedded(tmp_path):
    notebook = json.loads((ROOT / 'notebooks/atlas_gpu_check.ipynb').read_text())
    scope = {'OUT': tmp_path}
    exec(compile(''.join(notebook['cells'][2]['source']), '<embedded-helper>', 'exec'), scope)
    return scope['HELPER_PATH'], notebook


def test_self_contained_notebook_pin_and_syntax(tmp_path):
    path, notebook = _embedded(tmp_path)
    assert path.read_bytes() == (ROOT / 'notebooks/atlas_gpu_check_runtime.py').read_bytes()
    assert len(notebook['cells']) == 5
    code = '\n'.join(''.join(cell['source']) for cell in notebook['cells'] if cell['cell_type'] == 'code')
    for index, cell in enumerate(notebook['cells']):
        if cell['cell_type'] == 'code':
            compile(''.join(cell['source']), f'<cell-{index}>', 'exec')
    assert '4ffee35204a6e0c0626ecfa4b2a2e1371a47bfd4' in code
    assert 'transformers==5.11.0' in code and 'CPU_SMOKE = False' in code
    assert 'from_pretrained' not in code and 'drive.mount' not in code
    assert "env['HF_HUB_OFFLINE'] = '1'" in code
    assert 'files.download' in code


def test_failure_still_saves_diagnostic_zip(tmp_path, monkeypatch):
    path, _ = _embedded(tmp_path)
    namespace = {'__name__': 'test_embedded'}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == 'torch':
            raise ImportError('simulated missing optional Torch')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked)
    result = namespace['run_check'](tmp_path / 'failure', use_gpu=False, production_shapes=False)
    assert result['state'] == 'failed' and result['stage'] == 'imports'
    assert result['gpu_qualified'] is False
    with zipfile.ZipFile(tmp_path / 'failure/atlas_gpu_check.zip') as bundle:
        assert {'status.json', 'metrics.json', 'traceback.txt'} <= set(bundle.namelist())
        assert b'simulated missing optional Torch' in bundle.read('traceback.txt')


def test_extracted_helper_cpu_smoke(tmp_path):
    pytest.importorskip('torch')
    transformers = pytest.importorskip('transformers')
    if transformers.__version__ != '5.11.0':
        pytest.skip('Optional native test pins transformers==5.11.0')
    path, _ = _embedded(tmp_path)
    result = subprocess.run([sys.executable, str(path), '--outputdir', str(tmp_path / 'smoke'), '--cpu-smoke'],
                            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
                            text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads((tmp_path / 'smoke/status.json').read_text())
    metrics = json.loads((tmp_path / 'smoke/metrics.json').read_text())
    assert status['state'] == 'complete' and status['gpu_qualified'] is False
    assert metrics['pretrained_weights_loaded'] is False
    assert metrics['checks']['at_least_one_routed_comparison']['passed'] is True
    assert metrics['checks']['functional_linear_spy_restored']['passed'] is True
    assert metrics['routed_comparison']['raw_z']['n'] > 0
    with zipfile.ZipFile(tmp_path / 'smoke/atlas_gpu_check.zip') as bundle:
        assert {'status.json', 'metrics.json'} <= set(bundle.namelist())


@pytest.mark.parametrize("child_state,return_code", [("complete", 1), ("running", -9)])
def test_finalizer_never_qualifies_crashed_child(tmp_path, monkeypatch, child_state, return_code):
    _, notebook = _embedded(tmp_path)
    (tmp_path / 'status.json').write_text(json.dumps({'state': child_state, 'gpu_qualified': True}))
    downloaded = []
    colab = types.ModuleType('google.colab')
    colab.files = types.SimpleNamespace(download=lambda path: downloaded.append(path))
    monkeypatch.setitem(sys.modules, 'google.colab', colab)
    namespace = {'OUT': tmp_path, 'RETURN_CODE': return_code, 'json': json, 'zipfile': zipfile}
    with pytest.raises(RuntimeError, match='fehlgeschlagen'):
        exec(compile(''.join(notebook['cells'][4]['source']), '<finalizer>', 'exec'), namespace)
    result = json.loads((tmp_path / 'status.json').read_text())
    assert result['state'] == 'failed' and result['gpu_qualified'] is False
    assert result['child_state'] == child_state and result['return_code'] == return_code
    assert downloaded == [str(tmp_path / 'atlas_gpu_check.zip')]
    with zipfile.ZipFile(downloaded[0]) as bundle:
        assert json.loads(bundle.read('status.json')) == result
