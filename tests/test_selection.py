"""Synthetic-only private selection tests; no Atlas access or actual target IDs."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from neuron_atlas.selection import SelectionError, canonical_bytes, freeze_selection, main


def pole(neuron, *, expert=1, layer=3, sign="pos", category="poly", modes=3, sim=None):
    did = f"L{layer}_{'shared' if expert is None else 'E' + str(expert)}_N{neuron}"
    return {
        "pid": did + "__" + sign,
        "did": did,
        "layer": layer,
        "expert": expert,
        "neuron": neuron,
        "pole": sign,
        "category": category,
        "n_modes": modes,
        "simulatability": sim,
        "description": "Synthetic fixture only",
    }


def spec(*targets):
    return {
        "selection_scope": "exploratory_label_hypotheses",
        "targets": [
            {
                "pid": target["pid"],
                "rationale": "Own synthetic rationale",
                "short_own_word_rationale_de": "Testhypothese",
            }
            for target in targets
        ],
    }


def freeze(catalog, targets, seed=20261117):
    return freeze_selection(catalog, targets, "synthetic-release", seed)


def test_both_poles_are_recorded_without_counting_them_as_independent_selected_neurons():
    target, control = pole(1), pole(2)
    catalog = [target, pole(1, sign="neg"), control, pole(2, sign="neg")]
    result = freeze(catalog, spec(target))
    m = result["manifest"]
    assert m["n_target_neurons"] == 1 and m["n_control_neurons"] == 1
    assert m["n_primary_poles"] == 2 and m["n_neurons"] == 2 and m["n_poles"] == 4
    assert len(result["selected_poles"]) == 4
    assert m["primary_poles"] == {"targets": [target["pid"]], "controls": [control["pid"]]}
    assert all(len(item["available_poles"]) == 2 for item in m["selected_neurons"])
    assert m["targets"][0]["rationale"] == "Own synthetic rationale"
    assert m["checkpoint_identity"] == "unverified" and m["ready_for_inference"] is False


def test_absent_opposite_poles_are_not_fabricated():
    target, control = pole(1), pole(2)
    result = freeze([target, control], spec(target))
    assert result["manifest"]["n_poles"] == 2
    assert len(result["selected_poles"]) == 2


def test_expert_preference_precedes_mode_distance_and_other_expert_is_flagged():
    target = pole(1, modes=3)
    same = pole(2, modes=12)
    other = pole(3, expert=2, modes=3)
    pair = freeze([target, same, other], spec(target))["manifest"]["control_pairs"][0]
    assert pair["control_pid"] == same["pid"] and pair["router_matched"] is True
    fallback = freeze([target, other], spec(target))["manifest"]["control_pairs"][0]
    assert fallback["router_matched"] is False and fallback["control_pid"] == other["pid"]


def test_mode_distance_then_simulatability_availability_then_seeded_hash():
    target = pole(1, modes=5, sim=0.4)
    exact = pole(2, modes=5, sim=None)
    near = pole(3, modes=6, sim=0.5)
    assert (
        freeze([target, exact, near], spec(target))["manifest"]["control_pairs"][0]["control_pid"]
        == exact["pid"]
    )
    first = pole(4, modes=5, sim=0.5)
    second = pole(5, modes=5, sim=0.9)
    candidates = [exact, first, second]
    expected = min(
        [first, second],
        key=lambda c: hashlib.sha256(
            json.dumps(
                [20261117, target["pid"], c["pid"]], separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest(),
    )
    pair = freeze([target, *candidates], spec(target))["manifest"]["control_pairs"][0]
    assert pair["control_pid"] == expected["pid"] and pair["simulatability_status_matched"] is True


def test_unknown_modes_remain_null_and_are_not_zero():
    target = pole(1, modes=None)
    unknown = pole(2, modes=None)
    zero = pole(3, modes=0)
    pair = freeze([target, unknown, zero], spec(target))["manifest"]["control_pairs"][0]
    assert pair["control_pid"] == unknown["pid"] and pair["n_modes_distance"] is None
    assert pair["target_n_modes"] is None and pair["control_n_modes"] is None
    fallback = freeze([target, zero], spec(target))["manifest"]["control_pairs"][0]
    assert fallback["n_modes_distance"] is None and fallback["n_modes_status_matched"] is False


def test_assignment_looks_ahead_instead_of_reusing_or_falsely_exhausting_a_neuron():
    first = pole(1, expert=0)
    second = pole(1, expert=1, sign="neg")
    shared_candidate = pole(2, expert=0)
    shared_negative = pole(2, expert=0, sign="neg")
    positive_only = pole(2, expert=1)
    result = freeze([first, second, shared_candidate, shared_negative, positive_only], spec(first, second))
    pairs = {p["target_pid"]: p for p in result["manifest"]["control_pairs"]}
    assert pairs[first["pid"]]["control_pid"] == positive_only["pid"]
    assert pairs[second["pid"]]["control_pid"] == shared_negative["pid"]
    assert len({p["control_did"] for p in pairs.values()}) == 2


@pytest.mark.parametrize("change", ["layer", "sign", "category", "kind"])
def test_layer_polarity_category_and_routed_shared_kind_are_hard_constraints(change):
    target = pole(1)
    kwargs = (
        {"layer": 4}
        if change == "layer"
        else {"sign": "neg"}
        if change == "sign"
        else ({"category": "mono"} if change == "category" else {"expert": None})
    )
    with pytest.raises(SelectionError, match="No complete") as error:
        freeze([target, pole(2, **kwargs)], spec(target))
    assert error.value.diagnostics["available_controls_not_fabricated"] is True


def test_targets_cannot_control_each_other_and_opposite_target_poles_are_not_extra_targets():
    target = pole(1)
    opposite = pole(1, sign="neg")
    with pytest.raises(SelectionError, match="unique"):
        freeze([target, opposite, pole(2)], spec(target, opposite))
    other = pole(3)
    with pytest.raises(SelectionError, match="No complete"):
        freeze([target, other], spec(target, other))


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "alias_duplicate", "conflicting", "bounds", "bad_category", "bool_modes", "nan_sim"],
)
def test_invalid_catalog_is_rejected_cleanly(mutation):
    target, control = pole(1), pole(2)
    catalog = [target, control]
    if mutation == "duplicate":
        catalog.append(copy.deepcopy(control))
    elif mutation == "alias_duplicate":
        alias = copy.deepcopy(control)
        alias["pid"] = "+ " + control["did"].replace("_", "/")
        catalog.append(alias)
    elif mutation == "conflicting":
        control["expert"] = 2
    elif mutation == "bounds":
        control["pid"] = "L40_E1_N2__pos"
        control["layer"] = 40
        control["did"] = "L40_E1_N2"
    elif mutation == "bad_category":
        control["category"] = "invented"
    elif mutation == "bool_modes":
        control["n_modes"] = True
    else:
        control["simulatability"] = float("nan")
    with pytest.raises(SelectionError):
        freeze(catalog, spec(target))


def test_target_existence_rationale_and_redundant_metadata_are_checked():
    target, control = pole(1), pole(2)
    for invalid in [
        spec(pole(9)),
        {"targets": [{"pid": target["pid"]}]},
        {"targets": [{"pid": target["pid"], "rationale": "x", "category": "mono"}]},
    ]:
        with pytest.raises(SelectionError):
            freeze([target, control], invalid)
    with pytest.raises(SelectionError):
        freeze_selection([target, control], spec(target), "")


@pytest.mark.parametrize("source", ["catalog", "targets"])
def test_contradictory_routed_shared_kind_is_rejected(source):
    target, control = pole(1), pole(2)
    targets = spec(target)
    if source == "catalog":
        control["kind"] = "shared"
    else:
        targets["targets"][0]["kind"] = "shared"
    with pytest.raises(SelectionError):
        freeze([target, control], targets)


def test_explicit_target_release_must_match_and_is_not_independent_authentication():
    target, control = pole(1), pole(2)
    targets = spec(target)
    targets["release"] = "another-release"
    with pytest.raises(SelectionError, match="release"):
        freeze([target, control], targets)
    targets["release"] = "synthetic-release"
    result = freeze([target, control], targets)
    assert result["manifest"]["release_authenticated"] is False
    assert result["manifest"]["catalog_membership"] == "validated_against_supplied_catalog"


def test_order_independence_repeated_hashes_and_no_input_mutation():
    target, control, second = pole(1), pole(2), pole(3, layer=4)
    catalog = [target, control, second, pole(4, layer=4)]
    targets = spec(target, second)
    before = copy.deepcopy((catalog, targets))
    first = freeze(catalog, targets)
    reordered = copy.deepcopy(targets)
    reordered["targets"].reverse()
    assert first == freeze(list(reversed(catalog)), reordered)
    assert (catalog, targets) == before
    assert len(first["manifest"]["catalog_canonical_sha256"]) == 64
    assert (
        first["manifest"]["selected_poles_canonical_sha256"]
        == hashlib.sha256(canonical_bytes(first["selected_poles"])).hexdigest()
    )


def test_cli_freeze_hashes_original_bytes_is_idempotent_and_protects_inputs(tmp_path):
    target, control = pole(1), pole(2)
    catalog, targets = tmp_path / "catalog.json", tmp_path / "targets.json"
    catalog.write_text(json.dumps([target, control], indent=3))
    targets.write_text(json.dumps(spec(target)))
    before = {p: p.read_bytes() for p in [catalog, targets]}
    output = tmp_path / "private"
    args = [
        "--catalog",
        str(catalog),
        "--targets",
        str(targets),
        "--release",
        "fixture",
        "--out-dir",
        str(output),
    ]
    assert main(args) == 0
    saved = {p.name: p.read_bytes() for p in output.iterdir()}
    manifest = json.loads(saved["selection_manifest.json"])
    assert manifest["source_files"]["catalog"]["sha256"] == hashlib.sha256(before[catalog]).hexdigest()
    assert manifest["source_files"]["targets"]["sha256"] == hashlib.sha256(before[targets]).hexdigest()
    assert main(args) == 0 and saved == {p.name: p.read_bytes() for p in output.iterdir()}
    for p, raw in before.items():
        assert p.read_bytes() == raw
    with pytest.raises(SystemExit):
        main([*args, "--seed", "1"])
    assert saved == {p.name: p.read_bytes() for p in output.iterdir()}


def test_cli_must_not_overwrite_an_input_with_an_output_name(tmp_path):
    target, control = pole(1), pole(2)
    catalog = tmp_path / "selected_poles.json"
    targets = tmp_path / "targets.json"
    catalog.write_text(json.dumps([target, control]))
    targets.write_text(json.dumps(spec(target)))
    before = catalog.read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "--catalog",
                str(catalog),
                "--targets",
                str(targets),
                "--release",
                "fixture",
                "--out-dir",
                str(tmp_path),
            ]
        )
    assert catalog.read_bytes() == before and not (tmp_path / "selection_manifest.json").exists()


def test_cli_does_not_overwrite_or_remove_another_writer_lock(tmp_path):
    target, control = pole(1), pole(2)
    catalog, targets, out = tmp_path / "catalog.json", tmp_path / "targets.json", tmp_path / "private"
    catalog.write_text(json.dumps([target, control]))
    targets.write_text(json.dumps(spec(target)))
    out.mkdir()
    lock = out / ".selection.lock"
    lock.write_text("another writer")
    with pytest.raises(SystemExit):
        main(
            [
                "--catalog",
                str(catalog),
                "--targets",
                str(targets),
                "--release",
                "fixture",
                "--out-dir",
                str(out),
            ]
        )
    assert lock.read_text() == "another writer"
    assert not (out / "selection_manifest.json").exists()
    assert not (out / "selected_poles.json").exists()


def test_module_import_has_no_network_or_model_runtime_dependency():
    root = Path(__file__).resolve().parents[1]
    code = "import sys; import neuron_atlas.selection; assert not {'torch','transformers','httpx','numpy'} & set(sys.modules)"
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)
