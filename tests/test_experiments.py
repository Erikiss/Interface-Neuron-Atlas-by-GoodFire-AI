import json

import pytest

from neuron_atlas.experiments import build_plan, read_poles, selected_coordinates

PREFLIGHT = {"status": "prepared_not_executed", "target_model": "Qwen/Qwen3.6-35B-A3B",
             "architecture": {"reference_shape_matches": True}}


def test_pole_and_neuron_identity_and_shared_experts():
    records = [{"pid": "L30_E47_N449__pos"}, {"pid": "L30_E47_N449__neg"},
               {"pid": "L4_shared_N126__pos"}, {"pid": "+ L30/E47/N449"}]
    plan = build_plan(PREFLIGHT, records,
                      atlas_release="release-one", selection_sha256="a" * 64)
    assert plan["atlas"]["n_poles"] == 3
    assert plan["atlas"]["n_neurons"] == 2
    assert {p["kind"] for p in plan["atlas"]["poles"]} == {"routed", "shared"}
    assert plan["main_reference"]["cross_model_coordinate_mapping"] is None
    assert plan["ready_for_inference"] is False
    assert plan["atlas"]["checkpoint_identity_with_atlas"] == "unverified"


@pytest.mark.parametrize("record", [
    {"pid": "L40_E0_N1__pos"}, {"pid": "L0_E256_N1__pos"}, {"pid": "L0_E0_N512__pos"},
    {"pid": "L0_E0_N1"}, {"pid": "L0_E0_N1__pos", "expert": 3},
    {"pid": "L0_shared_N1__pos", "expert": 0},
])
def test_incompatible_coordinates_are_rejected(record):
    with pytest.raises(ValueError):
        selected_coordinates([record])


def test_export_formats_and_incomplete_search(tmp_path):
    records = [{"pid": "L1_E1_N1__neg"}, {"pid": "L2_shared_N3__pos"}]
    path = tmp_path / "selection.json"
    for content in [json.dumps(records), json.dumps({"hits": records, "next_offset": None}),
                    "\n".join(json.dumps(r) for r in records)]:
        path.write_text(content)
        parsed, digest = read_poles(path)
        assert parsed == records
        assert len(digest) == 64
    path.write_text(json.dumps({"hits": records, "next_offset": 2}))
    with pytest.raises(ValueError, match="paginated"):
        read_poles(path)


def test_selection_requires_release_and_file_provenance():
    with pytest.raises(ValueError, match="release"):
        build_plan(PREFLIGHT, [{"pid": "L1_E1_N1__pos"}])
    assert build_plan(PREFLIGHT)["atlas"]["n_poles"] == 0


def test_wrong_model_shape_cannot_bind_atlas_coordinates():
    with pytest.raises(ValueError, match="architecture"):
        build_plan({**PREFLIGHT, "architecture": {"reference_shape_matches": False}})
