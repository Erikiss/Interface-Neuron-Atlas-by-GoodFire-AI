"""Execute the Colab preparation offline, with no real Git/HF/model access."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.request
import zipfile
from pathlib import Path

import pytest

from .test_preflight import REVISION, config, index

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "attention_prepare_builder", ROOT / "notebooks/build_attention_prepare.py"
)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def fixture_run_source(payloads: dict[str, bytes]) -> str:
    """Replace only pinned metadata hashes, leaving the executable RUN unchanged."""
    tree = ast.parse(BUILDER.RUN)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "files" for target in node.targets)
    ]
    assert len(assignments) == 1
    pinned = ast.literal_eval(assignments[0].value)
    assert set(pinned) == set(payloads)
    source = BUILDER.RUN
    for name, expected in pinned.items():
        assert source.count(expected) == 1
        source = source.replace(expected, hashlib.sha256(payloads[name]).hexdigest())
    return source


def execute_offline(tmp_path, monkeypatch, *, tamper=None, gpu_env="0", gpu_command=False, poles=False):
    raw = {
        "config.json": json.dumps(config()).encode(),
        "model.safetensors.index.json": json.dumps(index()).encode(),
    }
    run_source = fixture_run_source(raw)
    downloads, commands, delivered = [], [], []
    created = []
    original_mkdtemp = tempfile.mkdtemp
    original_sys_path = list(sys.path)
    original_modules = set(sys.modules)
    namespace = {"__name__": "offline_colab_cell"}
    exec(compile(BUILDER.CONFIG, "attention-config", "exec"), namespace)
    assert namespace["MODEL_REVISION"] == REVISION
    if poles:
        selection = tmp_path / "chosen-poles.ndjson"
        selection.write_text('{"pid":"L3_E1_N4__pos"}\n{"pid":"L4_shared_N2__neg"}\n')
        namespace.update(POLE_EXPORT=str(selection), ATLAS_RELEASE="synthetic-release")

    def mkdtemp(*, prefix, dir):
        assert dir == "/content"
        result = original_mkdtemp(prefix=prefix, dir=tmp_path)
        created.append(Path(result))
        return result

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "nvidia-smi":
            assert gpu_command
            return subprocess.CompletedProcess(command, 0, stdout="synthetic GPU\n", stderr="")
        assert command[:2] == ["git", "clone"], command
        assert command[-2] == "https://github.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI.git"
        assert "--depth" in command and "--branch" in command and kwargs["check"] is True
        destination = Path(command[-1])
        shutil.copytree(ROOT / "src", destination / "src")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def check_output(command, **kwargs):
        commands.append(command)
        assert command[0] == "git" and command[-2:] == ["rev-parse", "HEAD"]
        assert kwargs["text"] is True
        return "f" * 40 + "\n"

    def urlopen(url, *, timeout):
        downloads.append(url)
        name = url.rsplit("/", 1)[-1]
        assert url == f"https://huggingface.co/Qwen/Qwen3.6-35B-A3B/resolve/{REVISION}/{name}"
        assert name in raw and timeout == 60
        payload = raw[name]
        if tamper == name:
            payload += b" "  # Still valid JSON; only exact-byte provenance fails.
        return io.BytesIO(payload)

    colab = types.ModuleType("google.colab")
    colab.files = types.SimpleNamespace(download=lambda path: delivered.append(path))
    google = types.ModuleType("google")
    google.colab = colab
    error = None
    with monkeypatch.context() as patcher:
        patcher.setenv("COLAB_GPU", gpu_env)
        patcher.setattr(tempfile, "mkdtemp", mkdtemp)
        patcher.setattr(shutil, "which", lambda name: "/stub/nvidia-smi" if gpu_command else None)
        patcher.setattr(subprocess, "run", run)
        patcher.setattr(subprocess, "check_output", check_output)
        patcher.setattr(urllib.request, "urlopen", urlopen)
        patcher.setitem(sys.modules, "google", google)
        patcher.setitem(sys.modules, "google.colab", colab)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(run_source, "attention-RUN", "exec"), namespace)
        except RuntimeError as caught:
            error = caught
        finally:
            sys.path[:] = original_sys_path
    assert len(created) == 1
    assert not {"torch", "transformers", "datasets", "huggingface_hub"} & (set(sys.modules) - original_modules)
    assert len(delivered) == 1
    archive = Path(delivered[0])
    assert archive.parent == created[0] and archive.is_file()
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        contents = {name: zipped.read(name) for name in zipped.namelist()}
    manifest = json.loads(contents["MANIFEST.json"])
    assert set(manifest) == set(contents) - {"MANIFEST.json"}
    for name, digest in manifest.items():
        assert digest == hashlib.sha256(contents[name]).hexdigest()
    assert not any(name.endswith(".safetensors") for name in contents)
    return error, contents, downloads, commands


@pytest.mark.parametrize("poles", [False, True])
def test_run_fetches_only_two_metadata_files_and_exports_an_unexecuted_plan(tmp_path, monkeypatch, poles):
    error, files, downloads, commands = execute_offline(tmp_path, monkeypatch, poles=poles)
    assert error is None
    assert downloads == [
        f"https://huggingface.co/Qwen/Qwen3.6-35B-A3B/resolve/{REVISION}/{name}"
        for name in ["config.json", "model.safetensors.index.json"]
    ]
    assert len(commands) == 2 and all(command[0] == "git" for command in commands)
    status = json.loads(files["status.json"])
    assert status == {"state": "prepared_not_executed", "model_run": False, "weights_downloaded": False}
    preflight, plan = json.loads(files["preflight.json"]), json.loads(files["plan.json"])
    assert preflight["target_model"] == "Qwen/Qwen3.6-35B-A3B"
    assert preflight["architecture"]["reference_shape_matches"] is True
    assert all(value is False for value in preflight["execution"].values())
    assert plan["status"] == "prepared_not_executed" and plan["ready_for_inference"] is False
    assert plan["atlas"]["n_poles"] == (2 if poles else 0)
    assert plan["main_reference"]["n_fixed_neurons"] == 286
    assert plan["main_reference"]["cross_model_coordinate_mapping"] is None
    assert not any(name.startswith("results") for name in files)


@pytest.mark.parametrize("tamper", ["config.json", "model.safetensors.index.json"])
def test_tampered_metadata_stops_before_any_preflight_or_plan(tmp_path, monkeypatch, tamper):
    error, files, downloads, _ = execute_offline(tmp_path, monkeypatch, tamper=tamper)
    assert error is not None
    assert len(downloads) == (1 if tamper == "config.json" else 2)
    status = json.loads(files["status.json"])
    assert status["state"] == "failed" and status["error_type"] == "ValueError"
    assert "Prüfsumme" in status["error"]
    assert status["weights_downloaded"] is False and status["model_run"] is False
    assert "diagnostic.txt" in files
    assert not {"preflight.json", "plan.json", "sources.json"} & set(files)


@pytest.mark.parametrize("gpu_env,gpu_command", [("1", False), ("0", True)])
def test_gpu_guard_stops_before_git_or_network(tmp_path, monkeypatch, gpu_env, gpu_command):
    error, files, downloads, commands = execute_offline(
        tmp_path, monkeypatch, gpu_env=gpu_env, gpu_command=gpu_command
    )
    assert error is not None and downloads == []
    assert all(command[0] == "nvidia-smi" for command in commands)
    status = json.loads(files["status.json"])
    assert status["state"] == "failed" and "CPU" in status["error"]
    assert not {"config.json", "model.safetensors.index.json", "plan.json"} & set(files)


def test_saved_notebook_matches_builder_and_executable_cells_are_cpu_only(tmp_path):
    generated = json.loads(BUILDER.build(tmp_path).read_text())
    saved = json.loads((ROOT / "notebooks/attention_prepare.ipynb").read_text())
    assert generated == saved
    assert generated["nbformat"] == 4
    assert generated["metadata"].get("accelerator") not in {"GPU", "TPU"}
    assert generated["metadata"].get("colab", {}).get("accelerator") not in {"GPU", "TPU"}
    code = [cell for cell in generated["cells"] if cell["cell_type"] == "code"]
    assert ["".join(cell["source"]) for cell in code] == [BUILDER.CONFIG, BUILDER.RUN]
    for cell in code:
        ast.parse("".join(cell["source"]))
        assert cell["execution_count"] is None and cell["outputs"] == []
