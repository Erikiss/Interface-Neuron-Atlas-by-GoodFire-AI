"""Offline metadata tests: no weights, Atlas download or inference needed."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from neuron_atlas.preflight import GIB, TARGET_MODEL, prepare_preflight

REVISION = "345d467b117d1fd5eda9b879a5988de9ae5f47a7"


def config(layers: int = 40) -> dict:
    return {
        "model_type": "qwen3_5_moe",
        "architectures": ["Qwen3_5MoeForConditionalGeneration"],
        "text_config": {
            "model_type": "qwen3_5_moe_text",
            "num_hidden_layers": layers,
            "hidden_size": 2048,
            "full_attention_interval": 4,
            "num_experts": 256,
            "num_experts_per_tok": 8,
            "moe_intermediate_size": 512,
            "shared_expert_intermediate_size": 512,
            "dtype": "bfloat16",
        },
    }


def index(size: int | float = 71903645408.0) -> dict:
    return {
        "metadata": {"total_size": size},
        "weight_map": {
            "model.language_model.layers.0.mlp.experts.down_proj": "not-downloaded-1.safetensors",
            "model.language_model.layers.0.mlp.shared_expert.down_proj.weight": "not-downloaded-2.safetensors",
        },
    }


class PreflightTests(unittest.TestCase):
    def test_target_layout_and_expert_coordinates_are_distinct_from_dsse(self):
        result = prepare_preflight(config(), index(), REVISION)
        self.assertEqual(result["status"], "prepared_not_executed")
        self.assertEqual(result["target_model"], TARGET_MODEL)
        arch = result["architecture"]
        self.assertEqual(arch["attention"]["full_attention_layers"], list(range(3, 40, 4)))
        self.assertEqual(arch["attention"]["delta_net_count"], 30)
        self.assertEqual(arch["attention"]["full_attention_count"], 10)
        self.assertTrue(arch["reference_shape_matches"])
        self.assertEqual(arch["moe"]["experts_per_layer"], 256)
        self.assertEqual(arch["moe"]["shared_expert_intermediate_size"], 512)
        self.assertTrue(arch["moe"]["packed_routed_projections_in_index"])
        self.assertFalse(arch["moe"]["dsse_286_neuron_mapping_available"])
        self.assertFalse(arch["moe"]["hook_adapter_verified"])
        self.assertFalse(result["atlas_checkpoint"]["verified"])
        self.assertTrue(all(value is False for value in result["execution"].values()))

    def test_explicit_layer_types_take_precedence_over_interval(self):
        cfg = config(8)
        cfg["text_config"]["layer_types"] = ["full_attention"] + ["linear_attention"] * 7
        result = prepare_preflight(cfg, index(), REVISION)
        attn = result["architecture"]["attention"]
        self.assertEqual(attn["full_attention_layers"], [0])
        self.assertEqual(attn["source"], "text_config.layer_types")
        self.assertFalse(attn["explicit_types_match_interval"])
        self.assertTrue(any("takes precedence" in warning for warning in result["warnings"]))

    def test_explicit_layer_types_do_not_require_an_interval(self):
        cfg = config(4)
        del cfg["text_config"]["full_attention_interval"]
        cfg["text_config"]["layer_types"] = ["linear_attention"] * 3 + ["full_attention"]
        self.assertEqual(prepare_preflight(cfg, index(), REVISION)["architecture"]["attention"]["full_attention_layers"], [3])

    def test_unknown_architecture_and_unknown_or_incomplete_layer_list_fail(self):
        for mutation in ("model", "layer", "nonstring_layer", "length", "missing"):
            with self.subTest(mutation=mutation):
                cfg = config(4)
                if mutation == "model":
                    cfg["model_type"] = "llama"
                elif mutation == "layer":
                    cfg["text_config"]["layer_types"] = ["mystery"] * 4
                elif mutation == "nonstring_layer":
                    cfg["text_config"]["layer_types"] = [{}] * 4
                elif mutation == "length":
                    cfg["text_config"]["layer_types"] = ["linear_attention"]
                else:
                    del cfg["text_config"]["full_attention_interval"]
                with self.assertRaises(ValueError):
                    prepare_preflight(cfg, index(), REVISION)

    def test_stored_bytes_are_actual_index_bytes_not_active_parameters(self):
        result = prepare_preflight(config(), index(), REVISION)
        storage = result["storage"]
        self.assertEqual(storage["indexed_weight_bytes"], 71903645408)
        self.assertEqual(storage["indexed_weight_gib"], 71903645408 / GIB)
        self.assertEqual(storage["fp32_estimated_bytes_if_all_storage_bf16"], 143807290816)
        self.assertFalse(storage["tensor_storage_dtypes_verified"])
        self.assertEqual(storage["shard_count"], 2)

    def test_integer_valued_float_size_supported_but_bad_sizes_rejected(self):
        for size in (0, -1, True, "42", 42.5, float("nan"), float("inf")):
            with self.subTest(size=size), self.assertRaises(ValueError):
                prepare_preflight(config(), index(size), REVISION)

    def test_unknown_fp16_or_quantized_storage_is_not_doubled_to_fp32(self):
        for dtype, quantized in ((None, False), ("float16", False), ("bfloat16", True)):
            with self.subTest(dtype=dtype, quantized=quantized):
                cfg = config()
                cfg["text_config"]["dtype"] = dtype
                if quantized:
                    cfg["quantization_config"] = {"quant_method": "example_4bit"}
                storage = prepare_preflight(cfg, index(1024), REVISION)["storage"]
                self.assertIsNone(storage["fp32_estimated_bytes_if_all_storage_bf16"])

    def test_memory_budget_is_optional_explicit_and_never_a_fit_guarantee(self):
        empty = prepare_preflight(config(), index(8 * GIB), REVISION)
        self.assertIsNone(empty["memory_estimate"]["within_provided_budget"])
        result = prepare_preflight(config(), index(8 * GIB), REVISION, free_vram_gib=23, headroom_gib=16)
        memory = result["memory_estimate"]
        self.assertEqual(memory["indexed_storage_plus_headroom_gib"], 24)
        self.assertFalse(memory["within_provided_budget"])
        self.assertFalse(memory["fit_guaranteed"])
        enough = prepare_preflight(config(), index(8 * GIB), REVISION, 24, 16)
        self.assertTrue(enough["memory_estimate"]["within_provided_budget"])
        self.assertFalse(enough["memory_estimate"]["fit_guaranteed"])

    def test_invalid_memory_values_and_revision_tags_fail(self):
        for revision in ("main", "v1", "a" * 39, "g" * 40, "a" * 41):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "40-hex"):
                prepare_preflight(config(), index(), revision)
        for free, headroom in ((-1, 16), (float("inf"), 16), (1, float("nan")), (1, -1), (True, 16)):
            with self.subTest(free=free, headroom=headroom), self.assertRaises(ValueError):
                prepare_preflight(config(), index(), REVISION, free, headroom)

    def test_local_input_hashes_and_original_files_untouched(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cf, ix = root / "config.json", root / "index.json"
            cf.write_text(json.dumps(config(), indent=2))
            ix.write_text(json.dumps(index(), indent=2))
            before = {path: path.read_bytes() for path in (cf, ix)}
            result = prepare_preflight(cf, ix, REVISION.upper())
            self.assertEqual(result["revision"], REVISION)
            self.assertEqual(result["sources"]["config"]["sha256"], hashlib.sha256(before[cf]).hexdigest())
            self.assertFalse(result["sources"]["revision_authenticated"])
            for path, raw in before.items():
                self.assertEqual(path.read_bytes(), raw)

    def test_inputs_are_not_mutated(self):
        cfg, idx = config(), index()
        before = copy.deepcopy((cfg, idx))
        prepare_preflight(cfg, idx, REVISION)
        self.assertEqual((cfg, idx), before)

    def test_cli_runs_without_torch_or_any_weight_files_and_protects_inputs(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cf, ix, out = root / "config.json", root / "index.json", root / "out/preflight.json"
            cf.write_text(json.dumps(config(8)))
            ix.write_text(json.dumps(index(1024)))
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
            command = [sys.executable, "-m", "neuron_atlas.preflight", "--config", str(cf), "--index", str(ix),
                       "--revision", REVISION, "--out", str(out)]
            process = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(out.read_text())["status"], "prepared_not_executed")
            self.assertFalse(list(root.glob("*.safetensors")))
            protected = subprocess.run(command[:-1] + [str(cf)], capture_output=True, text=True, env=env, check=False)
            self.assertEqual(protected.returncode, 2)
            self.assertIn("must differ", protected.stderr)
            self.assertEqual(json.loads(cf.read_text())["model_type"], "qwen3_5_moe")


if __name__ == "__main__":
    unittest.main()
