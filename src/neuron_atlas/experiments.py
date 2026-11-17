"""Prepare an attention experiment from local interface exports; never load a model.

Atlas coordinates identify candidates. They do not certify that a model revision,
hook implementation or intervention has been validated against the atlas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .ids import parse_reference

METHOD_REPOSITORY = "https://github.com/BurnyCoder/jacobian-mem2gen"
METHOD_COMMIT = "85e2896304aa1c9d0297def43042a1c1df793d19"
REFERENCE_MODEL = "Qwen/Qwen3-1.7B"
REFERENCE_REVISION = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"


def read_poles(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Accept the interface's JSON search result, JSON list or NDJSON export."""
    raw = path.read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("Selection export exceeds 32 MiB; export the chosen candidates only.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(data, dict):
        if "hits" in data:
            if data.get("next_offset") is not None:
                raise ValueError("Search export is paginated; supply the complete intended selection.")
            data = data["hits"]
        elif "pid" in data:
            data = [data]
        else:
            raise ValueError("Use a pole export (pid) or a search response (hits).")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("Expected a list of pole records.")
    return data, hashlib.sha256(raw).hexdigest()


def selected_coordinates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reuse atlas ID parsing and verify redundant exported fields, without web access."""
    found: dict[str, dict[str, Any]] = {}
    for record in records:
        ref = parse_reference(record.get("pid", ""))
        if ref is None or ref[1] is None:
            raise ValueError("Every selected record needs an explicit neuron pole pid.")
        nid, pole = ref
        if not (0 <= nid.layer < 40 and 0 <= nid.neuron < 512):
            raise ValueError(f"Coordinate outside Qwen3.6-35B-A3B bounds: {nid}")
        if nid.expert is not None and not 0 <= nid.expert < 256:
            raise ValueError(f"Expert outside Qwen3.6-35B-A3B bounds: {nid}")
        expected = {"did": nid.did, "layer": nid.layer, "expert": nid.expert,
                    "neuron": nid.neuron, "pole": pole}
        for key, value in expected.items():
            if key in record and record[key] != value:
                raise ValueError(f"Conflicting {key} in pole record {record['pid']}")
        pid = nid.pid(pole)
        found[pid] = {"pid": pid, **expected,
                      "kind": "shared" if nid.expert is None else "routed"}
    return [found[pid] for pid in sorted(found)]


def build_plan(preflight: dict[str, Any], records: list[dict[str, Any]] | None = None,
               *, atlas_release: str = "", selection_sha256: str | None = None) -> dict[str, Any]:
    """A reviewable preparation record, deliberately not an inference-ready claim."""
    if (preflight.get("status") != "prepared_not_executed"
            or preflight.get("target_model") != "Qwen/Qwen3.6-35B-A3B"
            or not preflight.get("architecture", {}).get("reference_shape_matches")):
        raise ValueError("Expected a metadata preflight matching Qwen3.6-35B-A3B's architecture.")
    coordinates = selected_coordinates(records or [])
    if coordinates and (not atlas_release.strip() or not selection_sha256):
        raise ValueError("A selection requires its atlas release and exact export digest.")
    if selection_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", selection_sha256) is None:
        raise ValueError("Selection digest must be an exact SHA256.")
    return {
        "schema_version": 1,
        "status": "prepared_not_executed",
        "ready_for_inference": False,
        "method": {"repository": METHOD_REPOSITORY, "commit": METHOD_COMMIT,
                   "transfer": "Intervention method and controls; not a replication of its LoRA experiment."},
        "main_reference": {"model": REFERENCE_MODEL, "revision": REFERENCE_REVISION,
                           "n_fixed_neurons": 286, "selection": "H_tq; post-hoc exploratory",
                           "weights_frozen": True, "reselect_neurons": False,
                           "cross_model_coordinate_mapping": None},
        "atlas": {"model": "Qwen/Qwen3.6-35B-A3B", "release": atlas_release or None,
                  "selection_sha256": selection_sha256, "poles": coordinates,
                  "n_poles": len(coordinates),
                  "n_neurons": len({item["did"] for item in coordinates}),
                  "selection_validated_against_catalog": False,
                  "checkpoint_identity_with_atlas": "unverified"},
        "model_preflight": preflight,
        "protocol": {
            "stage": "calibrate_then_freeze_then_heldout_test",
            "site": {"block": None, "prompt_position": None, "decode_step": None},
            "attention_intervention": "Mild score-temperature pulse within an existing full-attention block.",
            "delta_net_intervention": "Separate later experiment; not interchangeable with softmax attention.",
            "candidate_doses": {"attention_temperature": [1.0, 1.03, 1.08],
                                "status": "uncalibrated; not output sampling temperature"},
            "controls": ["instrumented sham", "matched perturbation norm at the same site",
                         "same intervention at a separate control block", "unobserved numerical baseline"],
            "primary": ["paired answer log-probability", "all-item accuracy", "rescues and harms"],
            "state_measures": ["signed MLP activation before down projection", "residual displacement",
                               "routed expert identity, selection rank and routing weight",
                               "shared expert activation and gate separately"],
            "routing_missing_value": "null if not routed; not zero activation",
            "persistence": "identical token continuation; retained versus rebuilt KV and DeltaNet states",
            "selection_rule": "Choose site and dose on separate calibration; never best-of-grid on test items.",
            "energy": "No energy-saving claim from score temperature; measure joules and runtime separately.",
            "interpretation": "Perturbation response or external decodability is not proof of self-monitoring or weight memory.",
        },
        "before_inference": [
            "Freeze a held-out question split and calibration rule.",
            "Bind selected poles to the intended atlas release and verify checkpoint identity.",
            "Implement and test routed-expert instrumentation against the native unfused calculation.",
            "Check sham equality, causal direction and hook cleanup on the pinned model runtime.",
            "Measure available VRAM and a short pilot; metadata estimates are not fit guarantees.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--poles", type=Path)
    parser.add_argument("--atlas-release", default="")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records, digest = read_poles(args.poles) if args.poles else ([], None)
    plan = build_plan(json.loads(args.preflight.read_text()), records,
                      atlas_release=args.atlas_release, selection_sha256=digest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
