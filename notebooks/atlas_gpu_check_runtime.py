"""Embedded technical adapter check. Random native models only; never loads weights."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import platform
import time
import traceback
import zipfile
from pathlib import Path


def bundle(output: Path) -> Path:
    target = output / 'atlas_gpu_check.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(output.iterdir()):
            if item.is_file() and item != target:
                archive.write(item, item.name)
    return target


def run_check(outputdir, *, use_gpu=True, production_shapes=True):
    """CPU smoke mode is explicit; its success never qualifies a GPU runtime."""
    output = Path(outputdir)
    output.mkdir(parents=True, exist_ok=True)
    metrics = {'purpose': 'technical_random_model_check', 'atlas_measurements': False,
               'pretrained_weights_loaded': False, 'atlas_mapping_verified': False,
               'production_shapes': production_shapes, 'checks': {}}
    cleanup_handles = []
    original_linear = None
    started = time.monotonic()
    status = {'state': 'running', 'stage': 'imports', 'gpu_qualified': False}

    def save():
        (output / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
        (output / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False) + '\n')

    def check(name, passed, detail=None):
        metrics['checks'][name] = {'passed': bool(passed), 'detail': detail}
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError(name)

    save()
    try:
        import torch
        import transformers
        from transformers.models.qwen3_5_moe.configuration_qwen3_5_moe import Qwen3_5MoeTextConfig
        from transformers.models.qwen3_5_moe.modeling_qwen3_5_moe import Qwen3_5MoeForCausalLM

        import neuron_atlas.runtime_adapter as adapter
        from neuron_atlas.runtime_adapter import FullAttentionPulse, MoeActivationCapture

        original_linear = torch.nn.functional.linear
        check('transformers_pin', transformers.__version__ == '5.11.0')
        metrics.update(python=platform.python_version(), torch=torch.__version__,
                       transformers=transformers.__version__, adapter_sha256=hashlib.sha256(
                           Path(adapter.__file__).read_bytes()).hexdigest())
        if use_gpu:
            if not torch.cuda.is_available():
                raise RuntimeError('Keine GPU: Colab-Laufzeit auf A100 oder L4 stellen; kein Warten.')
            capability = torch.cuda.get_device_capability()
            if capability[0] < 8 or not torch.cuda.is_bf16_supported():
                raise RuntimeError('Native BF16-GPU erforderlich. T4/Float16 wird hier nicht qualifiziert.')
            device, dtype = 'cuda', torch.bfloat16
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = False
            metrics.update(gpu=torch.cuda.get_device_name(), compute_capability=list(capability),
                           native_bf16_hardware=True)
            torch.cuda.reset_peak_memory_stats()
        else:
            device, dtype = 'cpu', torch.float32
            torch.set_num_threads(2)
        metrics.update(device=device, dtype=str(dtype))
        config_values = {
            'vocab_size': 32, 'hidden_size': 2048, 'num_attention_heads': 16, 'num_key_value_heads': 2,
            'head_dim': 256, 'moe_intermediate_size': 512, 'shared_expert_intermediate_size': 512,
            'num_experts': 8, 'num_experts_per_tok': 2, 'num_hidden_layers': 2,
            'layer_types': ['linear_attention', 'full_attention'],
            'linear_key_head_dim': 128, 'linear_value_head_dim': 128,
            'linear_num_key_heads': 16, 'linear_num_value_heads': 32,
            'rope_parameters': {'rope_type': 'default', 'rope_theta': 10000000.,
                             'partial_rotary_factor': .25, 'mrope_section': [11, 11, 10]},
        }
        if not production_shapes:
            config_values.update(hidden_size=16, num_attention_heads=2, num_key_value_heads=1,
                                 head_dim=8, moe_intermediate_size=4, shared_expert_intermediate_size=4,
                                 num_experts=3, linear_key_head_dim=4, linear_value_head_dim=4,
                                 linear_num_key_heads=2, linear_num_value_heads=2,
                                 rope_parameters={'rope_type': 'default', 'rope_theta': 10000.,
                                                  'partial_rotary_factor': .5, 'mrope_section': [1, 1, 0]})
        metrics['random_model_config'] = config_values
        metrics['seed'] = 412
        status['stage'] = 'random_model'
        save()
        torch.manual_seed(412)
        config = Qwen3_5MoeTextConfig(**config_values)
        config._attn_implementation = 'eager'
        config._experts_implementation = 'eager'
        model = Qwen3_5MoeForCausalLM(config).eval().to(device=device, dtype=dtype)
        inputs = torch.tensor([[1, 2, 3, 4, 5, 6, 7, 8], [8, 7, 6, 5, 4, 3, 2, 1]], device=device)
        positions = [2, 6]
        mlp = model.model.layers[1].mlp
        attention = model.model.layers[1].self_attn
        coords = [{'layer': 1, 'expert': expert, 'neuron': neuron, 'kind': 'routed'}
                  for expert in range(config.num_experts) for neuron in [0, config.moe_intermediate_size - 1]]
        coords += [{'layer': 1, 'expert': None, 'neuron': n, 'kind': 'shared'}
                   for n in [0, config.shared_expert_intermediate_size - 1]]

        def forward():
            return model(inputs, use_cache=False).logits.detach()

        def count_hooks():
            return sum(len(module._forward_hooks) + len(module._forward_pre_hooks) for module in model.modules())

        @contextlib.contextmanager
        def observe_native():
            # This marker is installed AFTER the adapter's prehook: its shadow
            # GEMMs run with active=False. Only the actual native forward is observed.
            state = {'active': False, 'expert': None}
            seen, handles = {}, []
            original = torch.nn.functional.linear
            gate_ptrs = {mlp.experts.gate_up_proj[e].data_ptr(): e for e in range(config.num_experts)}
            down_ptrs = {mlp.experts.down_proj[e].data_ptr(): e for e in range(config.num_experts)}

            def begin(_module, args):
                state['active'] = True
                hidden, indices, weights = args
                seen['routing'] = (indices.detach().cpu().clone(), weights.detach().float().cpu().clone())

            def end(_module, _args, _output):
                state['active'] = False

            def linear(x, weight, *args, **kwargs):
                if state['active'] and weight.data_ptr() in down_ptrs:
                    expert = down_ptrs[weight.data_ptr()]
                    seen[expert, 'raw_z'] = x.detach().float().cpu().clone()
                result = original(x, weight, *args, **kwargs)
                if state['active'] and weight.data_ptr() in gate_ptrs:
                    expert = gate_ptrs[weight.data_ptr()]
                    state['expert'] = expert
                    gate, up = result.chunk(2, dim=-1)
                    seen[expert, 'gate_pre'] = gate.detach().float().cpu().clone()
                    seen[expert, 'up'] = up.detach().float().cpu().clone()
                return result

            def activated(_module, _args, value):
                if state['active']:
                    seen[state['expert'], 'gate_post'] = value.detach().float().cpu().clone()

            def shared_down(_module, args):
                seen['shared'] = args[0].detach().float().cpu().clone()

            try:
                handles.append(mlp.experts.register_forward_pre_hook(begin))
                handles.append(mlp.experts.register_forward_hook(end, always_call=True))
                handles.append(mlp.experts.act_fn.register_forward_hook(activated))
                handles.append(mlp.shared_expert.down_proj.register_forward_pre_hook(shared_down))
                torch.nn.functional.linear = linear
                yield seen
            finally:
                state['active'] = False
                torch.nn.functional.linear = original
                for handle in reversed(handles):
                    handle.remove()

        cpu_rng = torch.random.get_rng_state().clone()
        cuda_rng = torch.cuda.get_rng_state().clone() if use_gpu else None
        initial_hooks = count_hooks()
        baseline_q, baseline_gate = [], []
        q_hook = attention.q_norm.register_forward_hook(lambda _m, _a, out: baseline_q.append(out.detach().clone()))
        gate_hook = attention.q_proj.register_forward_hook(lambda _m, _a, out: baseline_gate.append(out.detach().clone()))
        cleanup_handles.extend([q_hook, gate_hook])
        status['stage'] = 'native_sham_and_capture'
        save()
        with torch.inference_mode():
            baseline = forward()
            with (MoeActivationCapture(model, coords, positions) as capture,
                  FullAttentionPulse(model, 1, positions, beta=1) as sham, observe_native() as native):
                sham_output = forward()
        q_hook.remove()
        gate_hook.remove()
        check('sham_output_bitwise_equal', torch.equal(baseline, sham_output))
        check('sham_once', sham.fired_count == sham.completed_count == capture.fired_count == 1)
        routed_pairs = {key: ([], []) for key in ('raw_z', 'gate_pre', 'gate_post', 'up')}
        shared_equal, null_count = True, 0
        indices, weights = native['routing']
        for row in capture.records:
            token = row['batch_index'] * inputs.shape[1] + row['position']
            if row['expert'] is None:
                shared_equal &= row['raw_z'] == float(native['shared'][token, row['neuron']])
                continue
            expert = row['expert']
            native_ranks, native_tokens = torch.where((indices == expert).T)
            matched = (native_tokens == token).nonzero()
            if not matched.numel():
                null_count += 1
                check_value = row['routed'] is False and row['raw_z'] is None
                if not check_value:
                    raise AssertionError('Not-routed record must be null, never a zero activation')
                continue
            native_row = int(matched[0, 0])
            rank = int(native_ranks[native_row])
            if not row['routed'] or row['routing_rank'] != rank or row['routing_weight'] != float(weights[token, rank]):
                raise AssertionError('Native routing identity/rank/weight mismatch')
            for key, (shadow, actual) in routed_pairs.items():
                shadow.append(row[key])
                actual.append(float(native[expert, key][native_row, row['neuron']]))
        check('shared_native_down_input_exact', shared_equal)
        check('not_routed_is_null', null_count > 0, {'records': null_count})
        check('at_least_one_routed_comparison', len(routed_pairs['raw_z'][0]) > 0)
        metrics['routed_comparison'] = {}
        rtol, atol = ((.03, .02) if use_gpu else (1e-4, 1e-5))
        for key, (shadow, actual) in routed_pairs.items():
            shadow, actual = torch.tensor(shadow), torch.tensor(actual)
            difference = (shadow - actual).abs()
            detail = {'n': len(shadow), 'max_abs': float(difference.max()),
                      'rms_error': float(difference.square().mean().sqrt()),
                      'native_reference_rms': float(actual.square().mean().sqrt()),
                      'native_reference_max_abs': float(actual.abs().max()),
                      'max_rel_floor_1e-6': float((difference / actual.abs().clamp_min(1e-6)).max()),
                      'rtol': rtol, 'atol': atol,
                      'reference': 'actual_native_expert_forward_full_routed_batch',
                      'shadow': 'adapter_selected_token_gemm',
                      'threshold_status': 'engineering_smoke_threshold_not_scientific_calibration'}
            metrics['routed_comparison'][key] = detail
            check('routed_' + key + '_parity', torch.allclose(shadow, actual, rtol=rtol, atol=atol), detail)
        changed_q, changed_gate = [], []
        status['stage'] = 'post_norm_pulse'
        save()
        with torch.inference_mode(), FullAttentionPulse(model, 1, positions, beta=.9) as pulse:
            handles = [attention.q_norm.register_forward_hook(lambda _m, _a, out: changed_q.append(out.detach().clone())),
                       attention.q_proj.register_forward_hook(lambda _m, _a, out: changed_gate.append(out.detach().clone()))]
            cleanup_handles.extend(handles)
            try:
                pulsed_output = forward()
            finally:
                for handle in handles:
                    handle.remove()
        expected = baseline_q[0].clone()
        expected[:, positions] *= .9
        check('pulse_exactly_after_q_norm', torch.equal(changed_q[0], expected))
        check('q_proj_including_output_gate_unchanged', torch.equal(changed_gate[0], baseline_gate[0]))
        check('pulse_finite_and_effective', bool(torch.isfinite(pulsed_output).all()) and not torch.equal(pulsed_output, baseline))
        check('causal_earlier_tokens_unchanged', torch.equal(pulsed_output[:, :min(positions)], baseline[:, :min(positions)]))
        with torch.inference_mode(), FullAttentionPulse(model, 1, positions, beta=.9, mode='random', seed=9) as random:
            forward()
        metrics['random_delta_norms'] = random.norm_records
        check('random_norm_match', all(abs(row['applied_delta_norm'] - row['requested_delta_norm'])
                                     <= (0.04 * row['requested_delta_norm'] + 1e-6)
                                     for row in random.norm_records))
        check('cpu_rng_unchanged', torch.equal(cpu_rng, torch.random.get_rng_state()))
        if use_gpu:
            check('cuda_rng_unchanged', torch.equal(cuda_rng, torch.cuda.get_rng_state()))
            metrics['peak_gpu_allocated_bytes'] = torch.cuda.max_memory_allocated()
            metrics['peak_gpu_reserved_bytes'] = torch.cuda.max_memory_reserved()
        check('all_hooks_cleaned', count_hooks() == initial_hooks)
        check('functional_linear_spy_restored', torch.nn.functional.linear is original_linear)
        metrics['pulse_delta_norms'] = pulse.norm_records
        status.update(state='complete', stage='complete', gpu_qualified=bool(use_gpu),
                      qualification_scope='random_reduced_native_model_only')
    except BaseException as exc:
        status.update(state='failed', stage=status['stage'], error_type=type(exc).__name__, error=str(exc))
        (output / 'traceback.txt').write_text(traceback.format_exc())
        print('HARD FAILURE: ' + str(exc), flush=True)
    finally:
        for handle in reversed(cleanup_handles):
            handle.remove()
        if original_linear is not None:
            torch.nn.functional.linear = original_linear
        metrics['elapsed_seconds'] = time.monotonic() - started
        save()
        bundle(output)
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--outputdir', required=True)
    parser.add_argument('--cpu-smoke', action='store_true')
    args = parser.parse_args()
    result = run_check(args.outputdir, use_gpu=not args.cpu_smoke, production_shapes=not args.cpu_smoke)
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['state'] == 'complete' else 1)
