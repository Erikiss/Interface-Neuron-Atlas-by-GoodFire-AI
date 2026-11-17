"""Native Transformers 5.11 tiny random CPU modules; no pretrained weights/network.

Runtime dependencies are optional. The import test runs without them, and native
integration tests skip explicitly when the pinned optional packages are absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from neuron_atlas import runtime_adapter as adapter


def test_import_has_no_torch_or_transformers_dependency():
    source = Path(adapter.__file__).resolve().parents[1]
    code = '''
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'transformers'}:
        raise ImportError('blocked optional runtime')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from neuron_atlas.runtime_adapter import FullAttentionPulse
assert 'torch' not in sys.modules and 'transformers' not in sys.modules
try:
    FullAttentionPulse(object(), 0, [0])
except RuntimeError as exc:
    assert 'Optional runtime' in str(exc)
else:
    raise AssertionError('missing dependency not reported')
'''
    subprocess.run([sys.executable, "-S", "-c", code], check=True,
                   env={**os.environ, "PYTHONPATH": str(source)}, capture_output=True, text=True)


@pytest.fixture(scope="module")
def native():
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    if transformers.__version__ != "5.11.0":
        pytest.skip("Runtime prototype pins native transformers==5.11.0")
    from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe as modeling
    from transformers.models.qwen3_5_moe.configuration_qwen3_5_moe import Qwen3_5MoeTextConfig

    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield torch, modeling, Qwen3_5MoeTextConfig
    torch.set_num_threads(old_threads)


@pytest.fixture
def model(native):
    torch, modeling, config_class = native
    with torch.random.fork_rng():
        torch.manual_seed(712)
        config = config_class(
            vocab_size=24, hidden_size=16, num_hidden_layers=2, num_attention_heads=2,
            num_key_value_heads=1, head_dim=8, moe_intermediate_size=4,
            shared_expert_intermediate_size=4, num_experts=3, num_experts_per_tok=2,
            layer_types=["full_attention"] * 2,
            rope_parameters={"rope_type": "default", "rope_theta": 10000.,
                             "partial_rotary_factor": .5, "mrope_section": [1, 1, 0]},
        )
        config._attn_implementation = "eager"
        config._experts_implementation = "eager"
        yield modeling.Qwen3_5MoeForCausalLM(config).eval()


def _forward(model, torch, **kwargs):
    with torch.no_grad():
        return model(torch.tensor([[1, 2, 3, 4], [4, 3, 2, 1]]), use_cache=False, **kwargs).logits


def _coordinate(expert, neuron=1, layer=1, pole="pos"):
    return {"layer": layer, "expert": expert, "neuron": neuron,
            "kind": "shared" if expert is None else "routed", "pole": pole}


def _hook_count(model):
    return sum(len(m._forward_hooks) + len(m._forward_pre_hooks) for m in model.modules())


@pytest.mark.parametrize("mode", ["scale", "random"])
def test_sham_is_bitwise_identical_and_cleans_hooks(model, native, mode):
    torch, _, _ = native
    baseline = _forward(model, torch)
    count = _hook_count(model)
    rng = torch.random.get_rng_state().clone()
    with adapter.FullAttentionPulse(model, 1, [2], beta=1, mode=mode) as pulse:
        actual = _forward(model, torch)
    assert torch.equal(actual, baseline)
    assert pulse.fired_count == pulse.completed_count == 1
    assert len(pulse.norm_records) == 4
    assert all(row["applied_delta_norm"] == 0 for row in pulse.norm_records)
    assert torch.equal(rng, torch.random.get_rng_state())
    assert _hook_count(model) == count
    assert not model.training


@pytest.mark.parametrize("beta", [.95, .9, 0.])
def test_post_norm_scale_preserves_qproj_gate_and_causality(model, native, beta):
    torch, _, _ = native
    attention = model.model.layers[1].self_attn
    before, after, gates, earlier = [], [], [], []
    handles = [attention.q_norm.register_forward_hook(lambda _m, _a, out: before.append(out.detach().clone())),
               attention.q_proj.register_forward_hook(lambda _m, _a, out: gates.append(out.detach().clone())),
               model.model.layers[0].register_forward_hook(lambda _m, _a, out: earlier.append(out.detach().clone()))]
    baseline = _forward(model, torch)
    with adapter.FullAttentionPulse(model, 1, [2], beta=beta) as pulse:
        handles.append(attention.q_norm.register_forward_hook(lambda _m, _a, out: after.append(out.detach().clone())))
        changed = _forward(model, torch)
    for handle in handles:
        handle.remove()
    assert torch.equal(gates[0], gates[1])  # Includes the interleaved native output gate branch.
    assert torch.equal(earlier[0], earlier[1])
    assert torch.equal(after[0][:, 2], before[1][:, 2] * beta)
    assert torch.equal(after[0][:, [0, 1, 3]], before[1][:, [0, 1, 3]])
    assert torch.equal(changed[:, :2], baseline[:, :2])
    assert not torch.equal(changed[:, 2:], baseline[:, 2:])
    assert pulse.fired_count == 1
    # Scaling happened after RMSNorm: the new query norm changes rather than being normalized away.
    torch.testing.assert_close(after[0][:, 2].norm(dim=-1), before[1][:, 2].norm(dim=-1) * beta)


def test_random_control_matches_per_head_delta_norm_and_preserves_rng(model, native):
    torch, _, _ = native
    attention = model.model.layers[1].self_attn
    before, after = [], []
    raw = attention.q_norm.register_forward_hook(lambda _m, _a, out: before.append(out.detach().clone()))
    state = torch.random.get_rng_state().clone()
    with adapter.FullAttentionPulse(model, 1, [3, 1], beta=.9, mode="random", seed=7) as pulse:
        changed_hook = attention.q_norm.register_forward_hook(lambda _m, _a, out: after.append(out.detach().clone()))
        _forward(model, torch)
        changed_hook.remove()
    raw.remove()
    assert torch.equal(state, torch.random.get_rng_state())
    requested = before[0][:, [3, 1]].norm(dim=-1) * .1
    actual = (after[0][:, [3, 1]] - before[0][:, [3, 1]]).norm(dim=-1)
    torch.testing.assert_close(actual, requested, rtol=2e-6, atol=2e-7)
    assert torch.equal(before[0][:, [0, 2]], after[0][:, [0, 2]])
    for row in pulse.norm_records:
        assert row["applied_delta_norm"] == pytest.approx(row["requested_delta_norm"], rel=2e-6)


def test_cached_decode_is_ignored_and_second_full_pass_rejected(model, native):
    torch, modeling, config_class = native
    # Native all-full configs hit HF's linear-cache assumption. A true tiny hybrid
    # exercises the checkpoint's attention/cache structure without patching HF.
    config = config_class.from_dict({**model.config.to_dict(),
                                    "layer_types": ["linear_attention", "full_attention"],
                                    "linear_key_head_dim": 4, "linear_value_head_dim": 4,
                                    "linear_num_key_heads": 2, "linear_num_value_heads": 2})
    config._attn_implementation = "eager"
    config._experts_implementation = "eager"
    hybrid = modeling.Qwen3_5MoeForCausalLM(config).eval()
    with (torch.no_grad(), adapter.FullAttentionPulse(hybrid, 1, [3], .9) as pulse,
          adapter.MoeActivationCapture(hybrid, [_coordinate(0)], [3]) as capture):
        full = hybrid(torch.tensor([[1, 2, 3, 4]]), use_cache=True)
        hybrid(torch.tensor([[5]]), past_key_values=full.past_key_values, use_cache=True)
    assert pulse.fired_count == pulse.completed_count == pulse.skipped_decode_count == 1
    assert capture.fired_count == capture.completed_count == capture.skipped_decode_count == 1
    with (pytest.raises(RuntimeError, match="second uncached"),
          adapter.FullAttentionPulse(model, 1, [3]) as pulse):
        _forward(model, torch)
        _forward(model, torch)
    assert _hook_count(model) == 0


@pytest.mark.parametrize("positions,kwargs,error", [
    ([8], {}, "outside"), ([1], {"attention_mask": [[1, 0, 1, 1], [1, 1, 1, 1]]}, "unpadded"),
    ([1], {"position_ids": [[1, 2, 3, 4], [1, 2, 3, 4]]}, "absolute"),
])
def test_invalid_full_pass_fails_and_removes_hooks(model, native, positions, kwargs, error):
    torch, _, _ = native
    kwargs = {key: torch.tensor(value) for key, value in kwargs.items()}
    with pytest.raises(ValueError, match=error), adapter.FullAttentionPulse(model, 1, positions):
        _forward(model, torch, **kwargs)
    assert _hook_count(model) == 0


def test_wrong_layer_shape_version_training_and_no_forward_fail_closed(model, native, monkeypatch):
    with pytest.raises(ValueError, match="eval"):
        adapter.FullAttentionPulse(model.train(), 1, [0])
    model.eval()
    model.config.layer_types[0] = model.model.layers[0].layer_type = "linear_attention"
    with pytest.raises(ValueError, match="full_attention"):
        adapter.FullAttentionPulse(model, 0, [0])
    model.model.layers[1].self_attn.q_proj.out_features -= 1
    with pytest.raises(ValueError, match="layout"):
        adapter.FullAttentionPulse(model, 1, [0])
    monkeypatch.setattr(adapter, "version", lambda _name: "5.12.0")
    with pytest.raises(ValueError, match="exactly"):
        adapter.FullAttentionPulse(model, 1, [0])


def test_no_forward_and_exception_cleanup_preserve_other_hooks(model, native):
    torch, _, _ = native
    handle = model.model.layers[0].register_forward_hook(lambda *_: None)
    count = _hook_count(model)
    with pytest.raises(RuntimeError, match="exactly one"), adapter.FullAttentionPulse(model, 1, [0]):
        pass
    with pytest.raises(RuntimeError, match="deliberate"), adapter.FullAttentionPulse(model, 1, [0]):
        fault = model.model.layers[0].register_forward_hook(
            lambda *_: (_ for _ in ()).throw(RuntimeError("deliberate")))
        try:
            _forward(model, torch)
        finally:
            fault.remove()
    assert _hook_count(model) == count
    handle.remove()


def test_native_moe_capture_is_read_only_ordered_and_mathematically_bound(model, native):
    torch, _, _ = native
    mlp = model.model.layers[1].mlp
    coordinates = [_coordinate(2, 3, pole="neg"), _coordinate(None, 2), _coordinate(0, 1),
                   _coordinate(2, 3, pole="pos"), _coordinate(1, 0, layer=0)]
    snapshot = {key: value.clone() for key, value in model.state_dict().items()}
    original_forward = mlp.experts.forward.__func__
    original_parameters = [id(parameter) for parameter in model.parameters()]
    observed, shared, gate = [], [], []
    handles = [mlp.experts.register_forward_pre_hook(
        lambda _m, args: observed.append(tuple(x.detach().clone() for x in args))),
        mlp.shared_expert.down_proj.register_forward_pre_hook(lambda _m, args: shared.append(args[0].detach().clone())),
        mlp.shared_expert_gate.register_forward_hook(lambda _m, _a, out: gate.append(out.detach().clone()))]
    baseline = _forward(model, torch)
    with adapter.MoeActivationCapture(model, coordinates, [3, 1]) as capture:
        actual = _forward(model, torch)
    for handle in handles:
        handle.remove()
    assert torch.equal(baseline, actual)
    assert original_forward is mlp.experts.forward.__func__
    assert original_parameters == [id(parameter) for parameter in model.parameters()]
    assert all(torch.equal(value, snapshot[key]) for key, value in model.state_dict().items())
    assert capture.fired_count == capture.completed_count == 1
    rows = capture.records
    assert len(rows) == len(coordinates) * 2 * 2
    assert [(r["coordinate_index"], r["batch_index"], r["position"]) for r in rows] == [
        (c, b, p) for c in range(len(coordinates)) for b in range(2) for p in [3, 1]]
    hidden, indices, weights = observed[-1]
    for row in rows[:16]:  # layer1 coordinates; last4 are independently captured layer0.
        token, neuron = row["batch_index"] * 4 + row["position"], row["neuron"]
        if row["expert"] is None:
            assert row["measurement_method"] == "native_down_proj_input"
            assert row["atlas_hook_mapping_verified"] is False
            assert row["raw_z"] == float(shared[-1][token, neuron])
            assert row["shared_output_gate"] == float(gate[-1][token, 0].sigmoid())
            assert row["routing_weight"] is row["routed"] is None
        else:
            assert row["measurement_method"] == "shadow_selected_token_gemm"
            assert row["atlas_hook_mapping_verified"] is False
            selected = (indices[token] == row["expert"]).nonzero()
            assert row["routed"] == bool(selected.numel())
            if not row["routed"]:
                assert row["raw_z"] is row["gate_pre"] is row["routing_weight"] is None
                continue
            rank = int(selected[0, 0])
            pre, up = torch.nn.functional.linear(hidden[token], mlp.experts.gate_up_proj[row["expert"]]).chunk(2)
            expected = torch.nn.functional.silu(pre) * up
            assert row["routing_rank"] == rank
            assert row["routing_weight"] == float(weights[token, rank])
            assert row["gate_pre"] == pytest.approx(float(pre[neuron].detach()), rel=2e-6, abs=2e-8)
            assert row["raw_z"] == pytest.approx(float(expected[neuron].detach()), rel=2e-5, abs=1e-9)
    assert [r["raw_z"] for r in rows[:4]] == [r["raw_z"] for r in rows[12:16]]
    json.dumps(rows, allow_nan=False)
    assert _hook_count(model) == 0


def test_not_routed_null_is_distinct_from_true_zero(model, native):
    torch, _, _ = native
    mlp = model.model.layers[1].mlp
    with torch.no_grad():
        mlp.gate.weight.zero_()  # All ties: native topk deterministically selects two experts.
        mlp.experts.gate_up_proj.zero_()
        selected = torch.topk(torch.ones(3), 2).indices.tolist()
    absent = next(expert for expert in range(3) if expert not in selected)
    with adapter.MoeActivationCapture(model, [_coordinate(selected[0]), _coordinate(absent)], [1]) as cap:
        _forward(model, torch)
    for row in cap.records[:2]:
        assert row["routed"] is True and row["raw_z"] == 0.
    for row in cap.records[2:]:
        assert row["routed"] is False and row["raw_z"] is None


@pytest.mark.parametrize("change", ["backend", "transposed", "shape", "activation"])
def test_unknown_packed_expert_layout_fails_closed(model, change):
    mlp = model.model.layers[1].mlp
    if change == "backend":
        model.config._experts_implementation = "batched_mm"
    elif change == "transposed":
        mlp.experts.is_transposed = True
    elif change == "shape":
        mlp.experts.gate_up_proj = type(mlp.experts.gate_up_proj)(mlp.experts.gate_up_proj[:, :2])
    else:
        model.config.hidden_act = "relu"
    with pytest.raises(ValueError, match="packed|layout"):
        adapter.MoeActivationCapture(model, [_coordinate(1)], [0])
    assert _hook_count(model) == 0


def test_shared_native_dtype_gate_and_raw_capture_are_separate(model, native):
    torch, _, _ = native
    model = model.to(torch.bfloat16)
    mlp = model.model.layers[1].mlp
    gate_post, raw, handles = [], [], []
    handles.append(mlp.shared_expert.gate_proj.register_forward_hook(
        lambda _m, _a, out: gate_post.append(mlp.shared_expert.act_fn(out).detach().clone())))
    handles.append(mlp.shared_expert.down_proj.register_forward_pre_hook(
        lambda _m, args: raw.append(args[0].detach().clone())))
    baseline = _forward(model, torch)
    with adapter.MoeActivationCapture(model, [_coordinate(None, 0)], [2]) as capture:
        actual = _forward(model, torch)
    for handle in handles:
        handle.remove()
    assert torch.equal(actual, baseline)
    for row in capture.records:
        token = row["batch_index"] * 4 + 2
        assert row["gate_post"] == float(gate_post[-1][token, 0])
        assert row["raw_z"] == float(raw[-1][token, 0])
        assert 0 < row["shared_output_gate"] < 1
        assert row["routing_weight"] is None


def test_shadow_recomputes_only_requested_routed_experts(model, native, monkeypatch):
    torch, _, _ = native
    mlp = model.model.layers[1].mlp
    originals = []
    actual_linear = torch.nn.functional.linear
    weight_ptrs = {mlp.experts.gate_up_proj[i].data_ptr(): i for i in range(3)}

    def spy(inputs, weight, *args, **kwargs):
        if weight.data_ptr() in weight_ptrs:
            originals.append((weight_ptrs[weight.data_ptr()], len(inputs)))
        return actual_linear(inputs, weight, *args, **kwargs)

    monkeypatch.setattr(torch.nn.functional, "linear", spy)
    _forward(model, torch)
    baseline_calls = Counter(originals)
    originals.clear()
    with adapter.MoeActivationCapture(model, [_coordinate(0)], [1]) as capture:
        _forward(model, torch)
    extra = Counter(originals) - baseline_calls
    selected_count = sum(row["routed"] for row in capture.records)
    assert extra == (Counter({(0, selected_count): 1}) if selected_count else Counter())


def test_q_norm_runtime_shape_failure_and_capture_error_cleanup(model, native):
    torch, _, _ = native
    q_norm = model.model.layers[1].self_attn.q_norm
    wrong_shape = q_norm.register_forward_hook(lambda _m, _a, output: output[:, :, :1])
    with pytest.raises(ValueError, match="q_norm must"), adapter.FullAttentionPulse(model, 1, [2]):
        _forward(model, torch)
    wrong_shape.remove()
    assert _hook_count(model) == 0
    with pytest.raises(RuntimeError, match="deliberate"), adapter.MoeActivationCapture(
            model, [_coordinate(0), _coordinate(None)], [2]) as capture:
        fault = model.model.layers[1].register_forward_hook(
            lambda *_: (_ for _ in ()).throw(RuntimeError("deliberate")))
        try:
            _forward(model, torch)
        finally:
            fault.remove()
    assert _hook_count(model) == 0
    with pytest.raises(RuntimeError, match="completed"):
        _ = capture.records
