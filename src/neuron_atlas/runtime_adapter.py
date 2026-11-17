"""Optional, experimental hooks for the native Transformers 5.11 Qwen3.5 MoE.

This module imports neither torch nor transformers until an adapter is constructed.
No weights are loaded here. Architecture checks do not authenticate checkpoint bytes;
use the separately pinned model/preflight manifest for that purpose. This prototype
supports eager packed experts and text-only, unpadded full-sequence evaluation.
Positions are absolute zero-based input columns, identical across the batch.

The query pulse is *after* q_norm and before RoPE. q_proj also contains an output
gate: scaling that entire projection would be a different intervention. Neither
pulse mode reduces FLOPs or establishes an energy saving. Captures are raw signed
SiLU(gate)*up, before down projection, router weighting or shared output gating;
they are not CETT scores, and a negative atlas pole does not negate raw values.
Routed values are shadow recomputations on selected rows. Their smaller GEMM batch
can change rounding versus the full native expert batch, especially on GPU/BF16;
measure parity there before scientific use. Atlas-to-runtime hook mapping remains
unverified. Shared raw_z is captured directly from the native down_proj input.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from typing import Any

TRANSFORMERS_VERSION = "5.11.0"
MODELING_GIT_BLOB = "37131ca5ac8136d814bd85b8d6ccbffffb07b8f01"
MODEL_REPOSITORY = "Qwen/Qwen3.6-35B-A3B"
MODEL_REVISION = "345d467b117d1fd5eda9b879a5988de9ae5f47a7"


def _dependencies() -> tuple[Any, Any]:
    try:
        import torch
        from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe as native
    except ImportError as exc:
        raise RuntimeError("Optional runtime requires torch and transformers==5.11.0; no model is downloaded.") from exc
    if version("transformers") != TRANSFORMERS_VERSION:
        raise ValueError("Unsupported transformers version; this adapter requires exactly 5.11.0.")
    return torch, native


def _integer(value: Any, name: str, upper: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if upper is not None and value >= upper:
        raise ValueError(f"{name} outside native config bounds")
    return value


def _positions(values: Sequence[int]) -> tuple[int, ...]:
    result = tuple(_integer(value, "position") for value in values)
    if not result or len(set(result)) != len(result):
        raise ValueError("positions must be nonempty and unique")
    return result


def _arguments(module: Any, args: tuple, kwargs: dict) -> dict:
    bound = dict(inspect.signature(module.forward).bind_partial(*args, **kwargs).arguments)
    extra = bound.pop("kwargs", {})
    return {**bound, **extra}


def _resolve(model: Any, native: Any) -> tuple[Any, Any]:
    accepted = (native.Qwen3_5MoeTextModel, native.Qwen3_5MoeForCausalLM,
                native.Qwen3_5MoeModel, native.Qwen3_5MoeForConditionalGeneration)
    if type(model) not in accepted:
        raise ValueError("Expected an unmodified native Qwen3_5Moe model, not a wrapper or another architecture")
    text = model
    if isinstance(model, (native.Qwen3_5MoeForCausalLM, native.Qwen3_5MoeForConditionalGeneration)):
        text = model.model
    if isinstance(text, native.Qwen3_5MoeModel):
        text = text.language_model
    if type(text) is not native.Qwen3_5MoeTextModel or text.config.model_type != "qwen3_5_moe_text":
        raise ValueError("Unsupported text model layout")
    config = text.config
    if len(text.layers) != config.num_hidden_layers or len(config.layer_types) != config.num_hidden_layers:
        raise ValueError("Layer/config mismatch")
    if any(kind not in {"full_attention", "linear_attention"} for kind in config.layer_types):
        raise ValueError("Unsupported layer type")
    if model.training or any(layer.training for layer in text.layers):
        raise ValueError("Runtime adapters require model.eval(); training state is never changed automatically")
    if getattr(text, "gradient_checkpointing", False):
        raise ValueError("Gradient checkpointing is unsupported for once-only hooks")
    for index, layer in enumerate(text.layers):
        if type(layer) is not native.Qwen3_5MoeDecoderLayer or layer.layer_type != config.layer_types[index]:
            raise ValueError("Native decoder/config layer type mismatch")
    return text, config


class _FullPass:
    """Shared lifecycle: one unpadded text prefill, then ignore cached decoding."""

    def __init__(self, model: Any, positions: Sequence[int]) -> None:
        self.torch, self.native = _dependencies()
        self.model = model
        self.text, self.config = _resolve(model, self.native)
        self.positions = _positions(positions)
        self.fired_count = 0
        self.completed_count = 0
        self.skipped_decode_count = 0
        self._handles: list[Any] = []
        self._entered = False
        self._claimed = False
        self._window: tuple[int, int] | None = None

    def _before(self, module: Any, args: tuple, kwargs: dict) -> None:
        values = _arguments(module, args, kwargs)
        self._window = None
        cache = values.get("past_key_values")
        if cache is not None:
            try:
                cached = int(cache.get_seq_length())
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("Unsupported cache: cannot prove that this is a fresh prefill") from exc
            if cached > 0:
                self.skipped_decode_count += 1
                return
            if cached < 0:
                raise ValueError("Invalid negative cache length")
        cache_positions = values.get("cache_position")
        if cache_positions is not None and cache_positions.numel() and int(cache_positions.min()) > 0:
            self.skipped_decode_count += 1
            return
        if self._claimed:
            raise RuntimeError("Use one full forward per adapter context; a second uncached pass is ambiguous")
        for key in ("pixel_values", "pixel_values_videos", "image_grid_thw", "video_grid_thw"):
            if values.get(key) is not None:
                raise ValueError("Only text-only full passes are supported")
        token_types = values.get("mm_token_type_ids")
        if token_types is not None and bool((token_types != 0).any()):
            raise ValueError("Multimodal token positions are unsupported")
        inputs = values.get("input_ids")
        expected_ndim = 2
        if inputs is None:
            inputs = values.get("inputs_embeds")
            expected_ndim = 3
        if inputs is None or inputs.ndim != expected_ndim:
            raise ValueError("Expected input_ids[B,T] or inputs_embeds[B,T,H]")
        batch, length = inputs.shape[:2]
        if batch < 1 or length < 1 or max(self.positions) >= length:
            raise ValueError("Selected absolute position is outside this full sequence")
        mask = values.get("attention_mask")
        if mask is not None and (tuple(mask.shape) != (batch, length) or not bool((mask == 1).all())):
            raise ValueError("Prototype requires an unpadded full sequence with an all-one attention mask")
        position_ids = values.get("position_ids")
        if position_ids is not None:
            expected = self.torch.arange(length, device=position_ids.device)
            if position_ids.shape[-1] != length or not bool((position_ids == expected).all()):
                raise ValueError("position_ids must be absolute 0..T-1 for every text/rotary axis")
        if cache_positions is not None:
            expected = self.torch.arange(length, device=cache_positions.device)
            if tuple(cache_positions.shape) != (length,) or not bool((cache_positions == expected).all()):
                raise ValueError("cache_position must cover the complete absolute prefix")
        self._window = (batch, length)
        self._claimed = True

    def _after(self, _module: Any, _args: tuple, _kwargs: dict, output: Any) -> None:
        if self._window is not None and output is not None:
            self.completed_count += 1
        self._window = None

    def _install(self) -> None:
        raise NotImplementedError

    def _validate_complete(self) -> None:
        if self.fired_count != 1 or self.completed_count != 1:
            raise RuntimeError("Adapter did not capture exactly one completed full forward")

    def __enter__(self) -> Any:
        if self._entered:
            raise RuntimeError("Adapter instances cannot be re-entered")
        self._entered = True
        try:
            self._handles.append(self.model.register_forward_pre_hook(self._before, with_kwargs=True))
            self._handles.append(self.model.register_forward_hook(self._after, with_kwargs=True, always_call=True))
            self._install()
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()
        self._window = None

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()
        if exc_type is None:
            self._validate_complete()


class FullAttentionPulse(_FullPass):
    """Scale normalized queries by beta, or apply a per-head norm-matched random delta.

    ``mode='scale'`` uses q'=beta*q, a score-temperature pulse (beta=1/T).
    ``mode='random'`` uses ||delta||=abs(1-beta)*||q|| for each selected query head,
    with a local CPU generator; it does not preserve the norm of the resulting q.
    norm_records reports requested/applied delta norms, including native rounding;
    copying these diagnostics to CPU synchronizes device work and adds overhead.
    beta=1 is an exact no-op in both modes. Native projection/output gates are untouched.
    """

    def __init__(self, model: Any, block: int, positions: Sequence[int], beta: float = 1.0,
                 mode: str = "scale", seed: int = 0) -> None:
        super().__init__(model, positions)
        self.block = _integer(block, "block", self.config.num_hidden_layers)
        if isinstance(beta, bool) or not isinstance(beta, (int, float)) or not math.isfinite(beta) or not 0 <= beta <= 1:
            raise ValueError("beta must be finite in [0,1]")
        if mode not in {"scale", "random"}:
            raise ValueError("mode must be scale or random")
        self.beta = float(beta)
        self.mode = mode
        self.seed = _integer(seed, "seed")
        layer = self.text.layers[block]
        if self.config.layer_types[block] != "full_attention" or layer.layer_type != "full_attention":
            raise ValueError("Pulse requires a full_attention block; Gated DeltaNet is a different intervention")
        attention = layer.self_attn
        heads, dim = self.config.num_attention_heads, self.config.head_dim
        if (type(attention) is not self.native.Qwen3_5MoeAttention
                or type(attention.q_norm) is not self.native.Qwen3_5MoeRMSNorm
                or type(attention.q_proj) is not self.torch.nn.Linear
                or tuple(attention.q_proj.weight.shape) != (2 * heads * dim, self.config.hidden_size)
                or attention.q_proj.out_features != 2 * heads * dim
                or tuple(attention.q_norm.weight.shape) != (dim,)):
            raise ValueError("Unsupported post-q_norm query/gate layout")
        self.attention = attention
        self.norm_records: list[dict[str, Any]] = []

    def _install(self) -> None:
        self._handles.append(self.attention.q_norm.register_forward_hook(self._pulse))

    def _pulse(self, _module: Any, _args: tuple, output: Any) -> Any:
        if self._window is None:
            return None
        batch, length = self._window
        shape = (batch, length, self.config.num_attention_heads, self.config.head_dim)
        if not self.torch.is_tensor(output) or tuple(output.shape) != shape:
            raise ValueError("q_norm must return [B,T,num_attention_heads,head_dim]")
        if self.fired_count or not bool(self.torch.isfinite(output).all()):
            raise ValueError("Query hook fired repeatedly or received non-finite values")
        self.fired_count += 1
        selected = output[:, self.positions, :, :]
        if self.beta == 1:
            self._record_norms(selected, selected)
            return None
        if self.mode == "scale":
            changed = selected * self.beta
        else:
            generator = self.torch.Generator(device="cpu").manual_seed(self.seed)
            noise = self.torch.randn(selected.shape, generator=generator, device="cpu", dtype=self.torch.float32)
            noise = noise.to(device=selected.device)
            target = selected.float().norm(dim=-1, keepdim=True) * abs(1 - self.beta)
            delta = noise / noise.norm(dim=-1, keepdim=True).clamp_min(1e-30) * target
            changed = selected + delta.to(selected.dtype)
        self._record_norms(selected, changed)
        result = output.clone()
        result[:, self.positions, :, :] = changed
        return result

    def _record_norms(self, original: Any, changed: Any) -> None:
        requested = (original.detach().float().norm(dim=-1) * abs(1 - self.beta)).cpu()
        applied = (changed.detach().float() - original.detach().float()).norm(dim=-1).cpu()
        for batch in range(original.shape[0]):
            for index, position in enumerate(self.positions):
                for head in range(original.shape[2]):
                    self.norm_records.append({"batch_index": batch, "position": position, "head": head,
                                              "requested_delta_norm": float(requested[batch, index, head]),
                                              "applied_delta_norm": float(applied[batch, index, head]),
                                              "dtype": str(original.dtype)})


def _validate_experts(mlp: Any, config: Any, native: Any, torch: Any) -> None:
    if type(mlp) is not native.Qwen3_5MoeSparseMoeBlock or type(mlp.experts) is not native.Qwen3_5MoeExperts:
        raise ValueError("Unsupported native MoE module layout")
    experts = mlp.experts
    expected = (config.num_experts, 2 * config.moe_intermediate_size, config.hidden_size)
    if (config.hidden_act != "silu" or config._experts_implementation != "eager"
            or getattr(experts, "is_transposed", None) is not False
            or getattr(experts, "is_concatenated", None) is not True
            or getattr(experts, "has_gate", None) is not True
            or getattr(experts, "has_bias", None) is not False
            or tuple(experts.gate_up_proj.shape) != expected
            or tuple(experts.down_proj.shape) != (config.num_experts, config.hidden_size, config.moe_intermediate_size)):
        raise ValueError("Only eager, nontransposed packed [gate;up] SiLU experts are supported")
    for weight in (experts.gate_up_proj, experts.down_proj):
        if (type(weight) is not torch.nn.Parameter or weight.is_meta or weight.layout != torch.strided
                or weight.dtype not in (torch.float32, torch.bfloat16, torch.float16)):
            raise ValueError("Quantized, meta or custom expert parameter layouts are unsupported")
    shared = mlp.shared_expert
    size = config.shared_expert_intermediate_size
    if (type(shared) is not native.Qwen3_5MoeMLP
            or tuple(shared.gate_proj.weight.shape) != (size, config.hidden_size)
            or tuple(shared.up_proj.weight.shape) != (size, config.hidden_size)
            or tuple(shared.down_proj.weight.shape) != (config.hidden_size, size)
            or tuple(mlp.shared_expert_gate.weight.shape) != (1, config.hidden_size)):
        raise ValueError("Unsupported shared expert/gate layout")


class MoeActivationCapture(_FullPass):
    """Read-only capture in coordinate order for one full forward.

    ``coordinates`` uses selected_coordinates(): layer, expert (None for shared),
    neuron, kind ('routed'/'shared'), plus optional pid/did/pole metadata. ``records``
    is a JSON-safe list ordered coordinate, batch, then requested position. Null
    denotes not routed; an actual zero activation remains numeric zero. Gate/up
    and routing/output gates are separate fields. Hook work adds overhead and is
    not included in any claim of uninstrumented runtime or energy consumption.
    """

    def __init__(self, model: Any, coordinates: Sequence[Mapping[str, Any]], positions: Sequence[int]) -> None:
        super().__init__(model, positions)
        if not coordinates:
            raise ValueError("coordinates must be nonempty")
        self.coordinates = tuple(dict(item) for item in coordinates)
        self._layers: dict[int, Any] = {}
        self._values: dict[tuple[int, int, int], dict] = {}
        self._shared: dict[tuple[int, str], Any] = {}
        self._layer_calls: dict[int, int] = {}
        self._batch = 0
        for item in self.coordinates:
            layer = _integer(item.get("layer"), "coordinate layer", self.config.num_hidden_layers)
            expert = item.get("expert")
            kind = item.get("kind")
            if kind not in {"shared", "routed"} or (expert is None) != (kind == "shared"):
                raise ValueError("coordinate kind/expert mismatch")
            if expert is not None:
                _integer(expert, "expert", self.config.num_experts)
            size = (self.config.shared_expert_intermediate_size if expert is None
                    else self.config.moe_intermediate_size)
            _integer(item.get("neuron"), "neuron", size)
            mlp = self.text.layers[layer].mlp
            _validate_experts(mlp, self.config, self.native, self.torch)
            self._layers[layer] = mlp

    def _install(self) -> None:
        for layer, mlp in self._layers.items():
            self._handles.append(mlp.experts.register_forward_pre_hook(self._expert_hook(layer), with_kwargs=True))
            if any(item["layer"] == layer and item["expert"] is None for item in self.coordinates):
                for role in ("gate_proj", "up_proj"):
                    self._handles.append(getattr(mlp.shared_expert, role).register_forward_hook(
                        self._shared_output(layer, role)))
                self._handles.append(mlp.shared_expert.down_proj.register_forward_pre_hook(
                    self._shared_input(layer)))
                self._handles.append(mlp.shared_expert_gate.register_forward_hook(
                    self._shared_output(layer, "output_gate")))

    def _flat_selection(self, tensor: Any, width: int) -> Any:
        batch, length = self._window
        if tuple(tensor.shape) != (batch * length, width) or not bool(self.torch.isfinite(tensor).all()):
            raise ValueError("Native MoE input/output shape or finiteness mismatch")
        return tensor.reshape(batch, length, width)[:, self.positions, :]

    def _shared_output(self, layer: int, role: str) -> Any:
        def hook(_module: Any, _args: tuple, output: Any) -> None:
            if self._window is None:
                return
            if (layer, role) in self._shared:
                raise RuntimeError("Shared expert hook fired repeatedly")
            width = 1 if role == "output_gate" else self.config.shared_expert_intermediate_size
            # CPU copies are detached; no native input or output is replaced.
            selected = self._flat_selection(output, width)
            self._shared[layer, role] = selected.detach().float().cpu().clone()
            if role == "gate_proj":
                activated = self._layers[layer].shared_expert.act_fn(selected)
                self._shared[layer, "gate_post"] = activated.detach().float().cpu().clone()
            if role == "output_gate":
                self._shared[layer, "output_gate_sigmoid"] = selected.sigmoid().detach().float().cpu().clone()
        return hook

    def _shared_input(self, layer: int) -> Any:
        def hook(_module: Any, args: tuple) -> None:
            if self._window is None:
                return
            if (layer, "raw_z") in self._shared:
                raise RuntimeError("Shared down_proj hook fired repeatedly")
            selected = self._flat_selection(args[0], self.config.shared_expert_intermediate_size)
            self._shared[layer, "raw_z"] = selected.detach().float().cpu().clone()
        return hook

    def _expert_hook(self, layer: int) -> Any:
        def hook(module: Any, args: tuple, kwargs: dict) -> None:
            if self._window is None:
                return
            if self._layer_calls.get(layer):
                raise RuntimeError("Expert hook fired repeatedly")
            self._layer_calls[layer] = 1
            self.fired_count = 1
            self._batch, length = self._window
            values = _arguments(module, args, kwargs)
            hidden = values.get("hidden_states")
            indices = values.get("top_k_index")
            weights = values.get("top_k_weights")
            self._flat_selection(hidden, self.config.hidden_size)
            expected = (self._batch * length, self.config.num_experts_per_tok)
            if (indices is None or weights is None or tuple(indices.shape) != expected
                    or tuple(weights.shape) != expected or indices.dtype not in (self.torch.int32, self.torch.int64)
                    or not bool(self.torch.isfinite(weights).all()) or bool((weights < 0).any())
                    or bool((indices < 0).any()) or bool((indices >= self.config.num_experts).any())
                    or bool((indices.sort(dim=-1).values.diff(dim=-1) == 0).any())):
                raise ValueError("Unsupported native top-k routing inputs")
            flat = [batch * length + position for batch in range(self._batch) for position in self.positions]
            # torch.where on [rank,token] preserves native eager rank-major order.
            native_flat = sorted(flat)
            selected_index = indices[native_flat]
            requested = {item["expert"] for item in self.coordinates
                         if item["layer"] == layer and item["expert"] is not None}
            for expert in sorted(requested):
                ranks, local_tokens = self.torch.where((selected_index == expert).T)
                if not ranks.numel():
                    continue
                token_list = [native_flat[index] for index in local_tokens.tolist()]
                with self.torch.no_grad():
                    packed = self.torch.nn.functional.linear(hidden[token_list], module.gate_up_proj[expert])
                    gate, up = packed.chunk(2, dim=-1)
                    activated_gate = module.act_fn(gate)
                    raw = activated_gate * up
                if not bool(self.torch.isfinite(raw).all()):
                    raise ValueError("Non-finite shadow expert activation")
                for coord_index, item in enumerate(self.coordinates):
                    if item["layer"] != layer or item["expert"] != expert:
                        continue
                    neuron = item["neuron"]
                    for row, (token, rank) in enumerate(zip(token_list, ranks.tolist(), strict=True)):
                        batch, position = divmod(token, length)
                        self._values[coord_index, batch, position] = {
                            "raw_z": float(raw[row, neuron].detach()),
                            "gate_pre": float(gate[row, neuron].detach()),
                            "gate_post": float(activated_gate[row, neuron].detach()),
                            "up": float(up[row, neuron].detach()),
                            "routing_weight": float(weights[token, rank].detach()), "routing_rank": rank,
                        }
        return hook

    def _validate_complete(self) -> None:
        super()._validate_complete()
        if set(self._layer_calls) != set(self._layers):
            raise RuntimeError("A requested MoE layer was not observed")
        _ = self.records  # Validate completeness of all requested shared captures too.

    @property
    def records(self) -> list[dict[str, Any]]:
        if self.completed_count != 1:
            raise RuntimeError("Records require one completed full forward")
        output = []
        for coord_index, item in enumerate(self.coordinates):
            for batch in range(self._batch):
                for pos_index, position in enumerate(self.positions):
                    row = {**item, "coordinate_index": coord_index, "batch_index": batch, "position": position,
                           "measurement_method": "native_down_proj_input" if item["expert"] is None
                           else "shadow_selected_token_gemm", "atlas_hook_mapping_verified": False,
                           "routed": None, "raw_z": None, "gate_pre": None, "gate_post": None, "up": None,
                           "routing_weight": None, "routing_rank": None,
                           "shared_output_gate_logit": None, "shared_output_gate": None}
                    if item["expert"] is not None:
                        observed = self._values.get((coord_index, batch, position))
                        row["routed"] = observed is not None
                        if observed is not None:
                            row.update(observed)
                    else:
                        layer, neuron = item["layer"], item["neuron"]
                        try:
                            for key, role in (("raw_z", "raw_z"), ("gate_pre", "gate_proj"), ("up", "up_proj")):
                                row[key] = float(self._shared[layer, role][batch, pos_index, neuron])
                            row["gate_post"] = float(self._shared[layer, "gate_post"][batch, pos_index, neuron])
                            row["shared_output_gate_logit"] = float(self._shared[layer, "output_gate"][batch, pos_index, 0])
                            row["shared_output_gate"] = float(
                                self._shared[layer, "output_gate_sigmoid"][batch, pos_index, 0])
                        except KeyError as exc:
                            raise RuntimeError("Incomplete shared expert capture") from exc
                    output.append(row)
        return output
