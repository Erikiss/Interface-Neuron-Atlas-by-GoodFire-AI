"""Offline architecture and storage preflight; never load or download model weights.

Run with ``python -m neuron_atlas.preflight --config config.json --index
model.safetensors.index.json --revision <40-hex-commit> --out preflight.json``.
The supplied revision is recorded, not authenticated against the Hub or Atlas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TARGET_MODEL = "Qwen/Qwen3.6-35B-A3B"
GIB = 1 << 30
MetadataInput = Mapping[str, Any] | str | os.PathLike[str]


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value is not metadata: {value}")


def _document(value: MetadataInput, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, Mapping):
        raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        data = json.loads(raw)
        source = {"kind": "provided_mapping", "sha256_basis": "canonical_json"}
    else:
        path = Path(value)
        raw = path.read_bytes()
        data = json.loads(raw, parse_constant=_reject_constant)
        source = {"kind": "local_file", "path": str(path.resolve()), "sha256_basis": "original_bytes"}
    if not isinstance(data, dict):
        raise ValueError(f"{name} must contain a JSON object")
    source.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    return data, source


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _gib(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number of GiB")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number of GiB")
    return value


def _total_bytes(index: dict[str, Any]) -> int:
    metadata = index.get("metadata")
    value = metadata.get("total_size") if isinstance(metadata, dict) else None
    # The actual Hub index serializes its integral total_size with a trailing .0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("index.metadata.total_size must be a positive integral byte count")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError("index.metadata.total_size must be a positive integral byte count")
    if value <= 0:
        raise ValueError("index.metadata.total_size must be a positive integral byte count")
    return int(value)


def _dtype(config: dict[str, Any], text: dict[str, Any]) -> str | None:
    value = text.get("dtype", text.get("torch_dtype", config.get("dtype", config.get("torch_dtype"))))
    aliases = {"bf16": "bfloat16", "fp16": "float16", "fp32": "float32"}
    if not isinstance(value, str):
        return None
    return aliases.get(value.removeprefix("torch.").lower(), value.removeprefix("torch.").lower())


def _architecture(config: dict[str, Any], tensor_names: list[str]) -> dict[str, Any]:
    model_type = config.get("model_type")
    if model_type not in {"qwen3_5_moe", "qwen3_5_moe_text"}:
        raise ValueError("Unsupported architecture: expected qwen3_5_moe or qwen3_5_moe_text")
    text = config.get("text_config", config)
    if not isinstance(text, dict) or text.get("model_type", "qwen3_5_moe_text") != "qwen3_5_moe_text":
        raise ValueError("Unsupported text_config architecture")
    layers = _positive_int(text.get("num_hidden_layers"), "num_hidden_layers")
    interval = text.get("full_attention_interval")
    if interval is not None:
        interval = _positive_int(interval, "full_attention_interval")
    kinds = text.get("layer_types")
    if kinds is not None:
        if not isinstance(kinds, list) or len(kinds) != layers:
            raise ValueError("layer_types must have exactly num_hidden_layers entries")
        if any(not isinstance(kind, str) or kind not in {"full_attention", "linear_attention"} for kind in kinds):
            raise ValueError("Unknown layer type; cannot classify it as full attention or DeltaNet")
        source = "text_config.layer_types"
    elif interval is not None:
        kinds = ["full_attention" if (i + 1) % interval == 0 else "linear_attention" for i in range(layers)]
        source = "text_config.full_attention_interval (derived, no explicit layer_types)"
    else:
        raise ValueError("Neither layer_types nor full_attention_interval is available")
    full = [i for i, kind in enumerate(kinds) if kind == "full_attention"]
    linear = [i for i, kind in enumerate(kinds) if kind == "linear_attention"]
    interval_indices = [i for i in range(layers) if (i + 1) % interval == 0] if interval else None
    expert_fields = {
        "num_experts": "experts_per_layer",
        "num_experts_per_tok": "selected_experts_per_token",
        "moe_intermediate_size": "expert_intermediate_size",
        "shared_expert_intermediate_size": "shared_expert_intermediate_size",
    }
    moe = {label: _positive_int(text.get(key), key) for key, label in expert_fields.items()}
    if moe["selected_experts_per_token"] > moe["experts_per_layer"]:
        raise ValueError("num_experts_per_tok exceeds num_experts")
    packed = any(name.endswith((".mlp.experts.gate_up_proj", ".mlp.experts.down_proj")) for name in tensor_names)
    return {
        "model_type": model_type,
        "text_model_type": text.get("model_type", "qwen3_5_moe_text"),
        "architectures_declared": config.get("architectures", []),
        "num_hidden_layers": layers,
        "hidden_size": _positive_int(text.get("hidden_size"), "hidden_size"),
        "layer_numbering": "zero_based",
        "attention": {
            "source": source,
            "full_attention_interval": interval,
            "layer_types": kinds,
            "full_attention_layers": full,
            "full_attention_count": len(full),
            "delta_net_layers": linear,
            "delta_net_count": len(linear),
            "explicit_types_match_interval": full == interval_indices if interval else None,
            "interpretation": "linear_attention is the supported Qwen Gated DeltaNet path, not a dense attention matrix",
            "full_heads": text.get("num_attention_heads"),
            "full_kv_heads": text.get("num_key_value_heads"),
            "full_head_dim": text.get("head_dim"),
            "output_gate": text.get("attn_output_gate"),
            "linear_key_heads": text.get("linear_num_key_heads"),
            "linear_value_heads": text.get("linear_num_value_heads"),
            "linear_key_head_dim": text.get("linear_key_head_dim"),
            "linear_value_head_dim": text.get("linear_value_head_dim"),
        },
        "moe": {
            **moe,
            "routed_coordinate_fields": ["layer", "expert", "neuron_within_expert"],
            "shared_coordinate_fields": ["layer", "shared_expert", "neuron_within_shared_expert"],
            "packed_routed_projections_in_index": packed,
            "hook_adapter_verified": False,
            "hook_note": "Packed expert parameters do not establish per-expert down_proj module hooks; an implementation/backend adapter must be checked separately.",
            "dsse_286_neuron_mapping_available": False,
            "mapping_note": "The DSSE Qwen3-1.7B dense H_tq coordinates cannot be reused as this model's expert/shared coordinates.",
        },
        "reference_shape_matches": (
            layers == 40 and full == list(range(3, 40, 4)) and len(linear) == 30
            and text.get("hidden_size") == 2048
            and moe == {"experts_per_layer": 256, "selected_experts_per_token": 8,
                        "expert_intermediate_size": 512, "shared_expert_intermediate_size": 512}
        ),
    }


def prepare_preflight(
    config: MetadataInput,
    index: MetadataInput,
    revision: str,
    free_vram_gib: float | None = None,
    headroom_gib: float = 16,
) -> dict[str, Any]:
    """Summarize supplied local metadata, without importing Torch or making network calls.

    ``metadata.total_size`` is the actual indexed storage size. Config dtype is
    only a declaration: the conditional FP32 estimate assumes all indexed bytes
    are unquantized BF16 tensors; tensor headers and a load have not been checked.
    """
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        raise ValueError("revision must be an explicit 40-hex commit, not a branch or model tag")
    cfg, config_source = _document(config, "config")
    idx, index_source = _document(index, "index")
    weight_map = idx.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        raise ValueError("weight index must contain a non-empty weight_map")
    if any(not isinstance(name, str) or not isinstance(shard, str) or not shard for name, shard in weight_map.items()):
        raise ValueError("weight_map must map tensor names to non-empty shard file names")
    architecture = _architecture(cfg, list(weight_map))
    stored_bytes = _total_bytes(idx)
    text = cfg.get("text_config", cfg)
    declared_dtype = _dtype(cfg, text)
    quantized = cfg.get("quantization_config") is not None or text.get("quantization_config") is not None
    conditional_fp32 = stored_bytes * 2 if declared_dtype == "bfloat16" and not quantized else None
    headroom = _gib(headroom_gib, "headroom_gib")
    free = _gib(free_vram_gib, "free_vram_gib") if free_vram_gib is not None else None
    required = stored_bytes / GIB + headroom
    warnings = [
        "No model weights, Atlas records or tensor headers were loaded; no inference or benchmark was executed.",
        "The supplied revision and local metadata hashes are recorded; correspondence to that Hub revision is not independently authenticated offline.",
        "Memory numbers describe the entire indexed checkpoint, including any vision/MTP weights; they are not an allocation or peak-memory measurement.",
        "Headroom is a user-selected allowance, not a measured bound on KV/DeltaNet caches, activations, workspaces, framework or loading overhead.",
    ]
    if not architecture["reference_shape_matches"]:
        warnings.append("Metadata differs from the target reference's layer layout or hidden/expert dimensions.")
    if architecture["attention"]["explicit_types_match_interval"] is False:
        warnings.append("Explicit layer_types differs from full_attention_interval; the explicit list takes precedence.")
    if quantized:
        warnings.append("Quantization metadata is present; no unquantized FP32 size is inferred by doubling stored bytes.")
    return {
        "schema": 1,
        "status": "prepared_not_executed",
        "target_model": TARGET_MODEL,
        "revision": revision.lower(),
        "sources": {"config": config_source, "index": index_source, "revision_authenticated": False},
        "architecture": architecture,
        "storage": {
            "indexed_weight_bytes": stored_bytes,
            "indexed_weight_gib": stored_bytes / GIB,
            "byte_count_source": "index.metadata.total_size (not inferred from active parameters)",
            "byte_count_scope": "indexed tensor payload; safetensors headers and filesystem overhead excluded",
            "indexed_tensor_count": len(weight_map),
            "shard_count": len(set(weight_map.values())),
            "declared_dtype": declared_dtype,
            "tensor_storage_dtypes_verified": False,
            "quantization_metadata_present": quantized,
            "fp32_estimated_bytes_if_all_storage_bf16": conditional_fp32,
            "fp32_estimated_gib_if_all_storage_bf16": conditional_fp32 / GIB if conditional_fp32 else None,
            "fp32_estimate_assumption": "All indexed tensor bytes are unquantized BF16; config dtype alone cannot verify this assumption.",
        },
        "memory_estimate": {
            "headroom_gib": headroom,
            "provided_free_vram_gib": free,
            "indexed_storage_plus_headroom_gib": required,
            "within_provided_budget": free >= required if free is not None else None,
            "fit_guaranteed": False,
            "benchmark_executed": False,
            "quantization_or_offload_enabled": False,
        },
        "atlas_checkpoint": {
            "verified": False,
            "exact_checkpoint_unverified": True,
            "reason": "No Atlas checkpoint/provenance ingestion or coordinate-to-weight identity verification was performed.",
        },
        "execution": {"weights_loaded": False, "weights_downloaded": False, "inference_executed": False,
                      "atlas_data_ingested": False, "gpu_queried": False},
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Local config.json; no Hub download")
    parser.add_argument("--index", required=True, type=Path, help="Local safetensors weight-index JSON")
    parser.add_argument("--revision", required=True, help="Explicit 40-hex model commit to record")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--free-vram-gib", type=float, help="Optional user-supplied free VRAM, in GiB")
    parser.add_argument("--headroom-gib", type=float, default=16)
    args = parser.parse_args(argv)
    try:
        result = prepare_preflight(args.config, args.index, args.revision, args.free_vram_gib, args.headroom_gib)
        # Do not allow an output argument to overwrite either input metadata file.
        if args.out.resolve() in {args.config.resolve(), args.index.resolve()}:
            raise ValueError("--out must differ from --config and --index")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"atlas preflight: {error}\n")
    print(f"prepared_not_executed: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
