"""Freeze private Atlas candidates and metadata-matched controls, entirely offline.

The catalog supplies available poles, not functional validation or a verified
model checkpoint. Never commit the private input or generated selection files.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .experiments import selected_coordinates
from .ids import parse_reference

ALGORITHM = "atlas_metadata_controls_v1"
DEFAULT_SEED = 20261117
CATEGORIES = frozenset(("mono", "fn", "poly", "dark", "none"))


class SelectionError(ValueError):
    """Invalid private selection input or no feasible non-reused controls."""

    def __init__(self, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.diagnostics = copy.deepcopy(diagnostics or {})


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON after the identity/order normalization documented below."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SelectionError("Selection data must be finite JSON values.") from error


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or not isinstance(record.get("pid"), str):
        raise SelectionError("Each catalog record needs a pole pid.")
    reference = parse_reference(record["pid"])
    if reference is None or reference[1] is None:
        raise SelectionError("Catalog pid must identify an explicit pole.")
    for key in ("layer", "neuron", "expert"):
        if key in record and not (key == "expert" and record[key] is None) and type(record[key]) is not int:
            raise SelectionError("Redundant coordinate fields must be integers (expert may be null).")
    try:
        coordinate = selected_coordinates([record])[0]
    except (ValueError, TypeError, KeyError) as error:
        raise SelectionError("Catalog has invalid or conflicting coordinates.") from error
    if "kind" in record and record["kind"] != coordinate["kind"]:
        raise SelectionError("Catalog kind conflicts with the routed/shared coordinate identity.")
    if record.get("category") not in CATEGORIES:
        raise SelectionError("Each catalog pole needs an explicit valid category.")
    modes = record.get("n_modes")
    if modes is not None and (type(modes) is not int or modes < 0):
        raise SelectionError("n_modes must be a nonnegative integer or null.")
    sim = record.get("simulatability")
    if sim is not None and (type(sim) not in (int, float) or not math.isfinite(sim)):
        raise SelectionError("simulatability must be finite or null.")
    result = copy.deepcopy(record)
    result.update(coordinate, n_modes=modes, simulatability=sim)
    canonical_bytes(result)
    return result


def _targets(spec: Any, catalog: dict[str, dict]) -> tuple[list[dict], dict]:
    if not isinstance(spec, dict) or not isinstance(spec.get("targets"), list) or not spec["targets"]:
        raise SelectionError("Target specification needs a nonempty targets list.")
    result, neurons = [], set()
    for target in spec["targets"]:
        if not isinstance(target, dict) or not isinstance(target.get("pid"), str):
            raise SelectionError("Every target needs a pole pid.")
        ref = parse_reference(target["pid"])
        if ref is None or ref[1] is None:
            raise SelectionError("Targets require explicit pole identities.")
        pid, did = ref[0].pid(ref[1]), ref[0].did
        if pid not in catalog:
            raise SelectionError("A requested target pole is absent from the supplied catalog.")
        if did in neurons:
            raise SelectionError("Target neurons must be unique, including opposite poles.")
        neurons.add(did)
        if not isinstance(target.get("rationale"), str) or not target["rationale"].strip():
            raise SelectionError("Each target needs its own nonempty rationale.")
        for field in (
            "did",
            "pole",
            "layer",
            "expert",
            "neuron",
            "kind",
            "category",
            "n_modes",
            "simulatability",
        ):
            if field in target and (
                type(target[field]) is not type(catalog[pid][field]) or target[field] != catalog[pid][field]
            ):
                raise SelectionError("Target metadata conflicts with the supplied catalog.")
        item = copy.deepcopy(target)
        item["pid"] = pid
        result.append(item)
    result.sort(key=lambda item: item["pid"])
    normalized = copy.deepcopy(spec)
    normalized["targets"] = result
    canonical_bytes(normalized)
    return result, normalized


def _match(target: dict, control: dict, seed: int) -> tuple[tuple, dict]:
    modes_known = target["n_modes"] is not None and control["n_modes"] is not None
    distance = abs(target["n_modes"] - control["n_modes"]) if modes_known else None
    availability_match = (target["n_modes"] is None) == (control["n_modes"] is None)
    simulation_match = (target["simulatability"] is None) == (control["simulatability"] is None)
    router_matched = target["expert"] == control["expert"]
    tie = _sha([seed, target["pid"], control["pid"]])
    # The 0 below is only an internal sort placeholder after availability has
    # been ranked; the exported distance remains null when either count is unknown.
    rank = (
        not router_matched,
        not availability_match,
        distance if distance is not None else 0,
        not simulation_match,
        tie,
    )
    metadata = {
        "router_matched": router_matched,
        "n_modes_distance": distance,
        "n_modes_status_matched": availability_match,
        "target_n_modes": target["n_modes"],
        "control_n_modes": control["n_modes"],
        "simulatability_status_matched": simulation_match,
        "target_simulatability_known": target["simulatability"] is not None,
        "control_simulatability_known": control["simulatability"] is not None,
        "tie_sha256": tie,
    }
    return rank, metadata


def _can_complete(targets: list[str], candidates: dict[str, list[dict]], unavailable: set[str]) -> bool:
    """Small bipartite feasibility check: a control *neuron* may be used once."""
    assigned: dict[str, str] = {}

    def augment(target: str, seen: set[str]) -> bool:
        for candidate in candidates[target]:
            did = candidate["did"]
            if did in unavailable or did in seen:
                continue
            seen.add(did)
            if did not in assigned or augment(assigned[did], seen):
                assigned[did] = target
                return True
        return False

    return all(augment(target, set()) for target in targets)


def freeze_selection(catalog_records, target_spec, release, seed=DEFAULT_SEED) -> dict[str, Any]:
    """Return {manifest, selected_poles}; no I/O, random state or network access.

    Targets are processed in canonical PID order. For each, the best candidate
    that still permits a complete assignment is chosen. This makes the preference
    hierarchy deterministic without falsely declaring greedy exhaustion.
    """
    if not isinstance(release, str) or not release.strip():
        raise SelectionError("An explicit Atlas release is required.")
    if (
        isinstance(target_spec, dict)
        and "release" in target_spec
        and (not isinstance(target_spec["release"], str) or target_spec["release"].strip() != release.strip())
    ):
        raise SelectionError("Target specification release does not match the requested release.")
    if type(seed) is not int or seed < 0:
        raise SelectionError("seed must be a nonnegative integer.")
    if not isinstance(catalog_records, list) or not catalog_records:
        raise SelectionError("Catalog must be a nonempty list of pole records.")
    catalog = {}
    for record in catalog_records:
        normalized = _record(record)
        if normalized["pid"] in catalog:
            raise SelectionError("Duplicate canonical pid in supplied catalog.")
        catalog[normalized["pid"]] = normalized
    targets, normalized_spec = _targets(target_spec, catalog)
    target_ids = [target["pid"] for target in targets]
    target_neurons = {catalog[pid]["did"] for pid in target_ids}
    candidates = {}
    for pid in target_ids:
        target = catalog[pid]
        eligible = [
            record
            for record in catalog.values()
            if record["did"] not in target_neurons
            and all(record[key] == target[key] for key in ("layer", "pole", "category", "kind"))
        ]
        candidates[pid] = sorted(
            eligible, key=lambda control, target=target: _match(target, control, seed)[0]
        )
    if not _can_complete(target_ids, candidates, set()):
        raise SelectionError(
            "No complete non-reused control-neuron assignment exists under the exact metadata constraints.",
            {
                "candidate_pole_counts": {pid: len(candidates[pid]) for pid in target_ids},
                "target_count": len(target_ids),
                "available_controls_not_fabricated": True,
            },
        )
    pairs, used = [], set()
    for index, pid in enumerate(target_ids):
        for control in candidates[pid]:
            if control["did"] in used or not _can_complete(
                target_ids[index + 1 :], candidates, used | {control["did"]}
            ):
                continue
            used.add(control["did"])
            pairs.append(
                dict(
                    target_pid=pid,
                    target_did=catalog[pid]["did"],
                    control_pid=control["pid"],
                    control_did=control["did"],
                    **_match(catalog[pid], control, seed)[1],
                )
            )
            break
        else:  # Defensive: feasibility above should make this unreachable.
            raise SelectionError("Control assignment exhausted without a complete selection.")
    primary_controls = [pair["control_pid"] for pair in pairs]
    selected_neurons = target_neurons | used
    poles = [
        copy.deepcopy(catalog[pid]) for pid in sorted(catalog) if catalog[pid]["did"] in selected_neurons
    ]
    roles = {catalog[pid]["did"]: ("target", pid) for pid in target_ids}
    roles.update({pair["control_did"]: ("control", pair["control_pid"]) for pair in pairs})
    neurons = [
        {
            "did": did,
            "role": roles[did][0],
            "primary_pid": roles[did][1],
            "available_poles": [p["pid"] for p in poles if p["did"] == did],
        }
        for did in sorted(selected_neurons)
    ]
    manifest = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM,
        "seed": seed,
        "release": release.strip(),
        "status": "selection_frozen_not_executed",
        "ready_for_inference": False,
        "checkpoint_identity": "unverified",
        "catalog_membership": "validated_against_supplied_catalog",
        "release_authenticated": False,
        "selection_scope": target_spec.get("selection_scope", "exploratory"),
        "matching_scope": "Metadata only; activation, damage and functional matching were not measured.",
        "ranking": [
            "same layer/polarity/category/kind required",
            "same expert preferred (including null shared expert)",
            "same mode-count availability, then absolute known mode-count distance",
            "same simulatability availability",
            "SHA256([seed,target_pid,control_pid])",
        ],
        "unknown_modes": "Distance is null if either mode count is unknown; unknown is never treated as zero.",
        "assignment_order": "Canonical target PID order; best feasible candidate with neuron-level reuse forbidden.",
        "n_target_neurons": len(targets),
        "n_control_neurons": len(pairs),
        "n_neurons": len(selected_neurons),
        "n_poles": len(poles),
        "n_primary_poles": len(targets) + len(pairs),
        "primary_poles": {"targets": target_ids, "controls": primary_controls},
        "targets": targets,
        "control_pairs": pairs,
        "selected_neurons": neurons,
        "catalog_canonical_sha256": _sha([catalog[pid] for pid in sorted(catalog)]),
        "targets_canonical_sha256": _sha(normalized_spec),
        "selected_poles_canonical_sha256": _sha(poles),
    }
    return {"manifest": manifest, "selected_poles": poles}


def _load(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise SelectionError("Input is not valid JSON.") from error
    canonical_bytes(value)
    return value, raw


def _write_outputs(result: dict, out: Path, inputs: tuple[Path, Path]) -> None:
    payloads = {
        "selection_manifest.json": result["manifest"],
        "selected_poles.json": result["selected_poles"],
    }
    encoded = {
        name: (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        for name, value in payloads.items()
    }
    for name in encoded:
        destination = out / name
        if destination.resolve() in {path.resolve() for path in inputs}:
            raise SelectionError("Output must not overwrite either private input.")
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".selection.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise SelectionError(
            "Selection output directory is locked by another or unfinished writer."
        ) from error
    try:
        os.close(descriptor)
        for name, raw in encoded.items():
            destination = out / name
            if destination.exists() and destination.read_bytes() != raw:
                raise SelectionError(
                    "A different selection already exists in the output directory; choose a new private directory."
                )
        for name, raw in encoded.items():
            if (out / name).exists():
                continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=out, prefix=".selection-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, out / name)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out-dir", type=Path, default=Path("out/atlas_selection/frozen"))
    args = parser.parse_args(argv)
    try:
        catalog, catalog_raw = _load(args.catalog)
        targets, target_raw = _load(args.targets)
        result = freeze_selection(catalog, targets, args.release, args.seed)
        result["manifest"]["source_files"] = {
            "catalog": {"sha256": hashlib.sha256(catalog_raw).hexdigest(), "bytes": len(catalog_raw)},
            "targets": {"sha256": hashlib.sha256(target_raw).hexdigest(), "bytes": len(target_raw)},
        }
        _write_outputs(result, args.out_dir, (args.catalog, args.targets))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        f"Frozen {result['manifest']['n_neurons']} neurons / {result['manifest']['n_poles']} available poles; no inference."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
