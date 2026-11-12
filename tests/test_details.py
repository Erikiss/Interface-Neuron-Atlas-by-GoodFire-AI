import pytest

from neuron_atlas.details import island_window, mark_focal
from neuron_atlas.service import NotFound
from neuron_atlas.views import circuit_view, landscape_view, mode_numbers, overview_view

from . import fakeatlas


def test_default_view_has_modes_with_kinds_and_examples(atlas):
    d = atlas.pole(fakeatlas.RIVER)
    assert [m.kind for m in d.modes] == ["open", "closed_gate", "residue"]
    assert d.modes[1].public_label == "Q3/Q4 firings (gate input negative)"
    assert d.modes[2].public_label == "Unassigned examples"
    assert d.modes[1].label == "Closed-gate firings (silu leak)"  # the raw label is kept
    assert d.theta == 0.25 and d.n_firings == 5000 and d.closed_gate_share == 0.2
    assert d.sources["subcircuit"] == "used" and d.sources["overview"] == "not_requested"
    assert d.modes[0].damage.ratio == 1.4 and d.modes[0].subcircuits == ["c0", "c1"]


def test_mode_examples_come_in_turn_from_the_largest_subcircuits(atlas):
    d = atlas.pole(fakeatlas.RIVER, ["modes", "examples"], max_examples=3)
    examples = d.modes[0].examples
    assert [e.ref for e in examples] == ["c0:0", "c1:0", "c0:1"]
    first = examples[0]
    assert "« Danube»" in first.text and first.focal_token == " Danube" and first.focal_index == 16
    assert first.text.endswith("at dawn<|im_end|>")  # padding past the document end is shown once
    assert first.a_rel == 9.0 and first.group == "strongest" and first.closed_gate is False
    assert first.pre.startswith("Earlier text") and first.tokens is None
    assert examples[2].repeats == 3
    assert d.modes[1].examples[0].closed_gate is True


def test_subcircuits_carry_readings_links_and_one_example_per_group_first(atlas):
    d = atlas.pole(fakeatlas.RIVER, ["subcircuits", "examples", "tokens", "research"], max_examples=3)
    c0 = next(s for s in d.subcircuits if s.id == "c0")
    assert d.subcircuits[0].id == "c0"  # largest share first
    assert c0.mode_index == 0 and c0.function_label == "River names in travel writing"
    assert [e.group for e in c0.examples] == ["strongest", "typical", "weakest"]
    assert c0.examples[0].tokens[16] == (" Danube", 9.0) and c0.examples[0].held_out is False
    assert c0.circuit.gate_drivers[0].unit == "L2_E7_N100" and c0.circuit.gate_drivers[0].weight == 0.03
    assert c0.circuit.readers[1].weight < 0  # readers are signed
    assert c0.research["spec_flags"][0]["p"] == 0.1 and d.research["partition"] == "leiden_test"
    assert c0.profile.ge_theta == 0.7 and c0.damage.n_measured == 12


def test_negative_pole_examples_keep_raw_signs(atlas):
    d = atlas.pole(fakeatlas.MIXED, ["modes", "examples", "tokens"], max_examples=1)
    example = d.modes[0].examples[0]
    assert example.act < 0 and example.a_rel > 0
    assert example.tokens[16][1] < 0 and "«" in example.text


def test_poles_without_a_subcircuit_read_fall_back_to_islands(atlas):
    d = atlas.pole(fakeatlas.ISLAND_ONLY)
    assert d.modes == [] and d.sources["subcircuit"] == "absent" and d.sources["islands"] == "used"
    river, unassigned = d.islands.modes
    assert river.name == "River names" and river.share == pytest.approx(0.742) and river.islands == [0]
    assert unassigned.unassigned and unassigned.share == pytest.approx(0.258) and unassigned.islands == [1]
    island = d.islands.islands[0]
    assert island.number == 1 and island.n == 40
    # Primary examples by strength: the long passage first, shown as a window around the peak.
    top = island.examples[0]
    assert top.group == "strongest" and top.text.startswith("… ") and top.text.endswith(" …") and "« Danube»" in top.text
    assert top.a_rel == 10.0

    failed = atlas.pole(fakeatlas.FAILED_READ)
    assert failed.islands is not None and failed.modes == []
    dark = atlas.pole(fakeatlas.DARK, ["islands"])
    assert [m.unassigned for m in dark.islands.modes] == [True] and dark.islands.modes[0].islands == [0, 1]


def test_island_example_refs_match_the_search_index(atlas):
    d = atlas.pole(fakeatlas.ISLAND_ONLY, ["islands", "examples", "tokens"], max_examples=12)
    island = d.islands.islands[0]
    # Shown by strength (the file holds median, top, random, then one "more" example), numbered by file position.
    assert [e.ref for e in island.examples] == ["island0:1", "island0:0", "island0:2", "island0:3"]
    assert [e.group for e in island.examples] == ["strongest", "typical", "random", None]
    raw = atlas.raw("islands", fakeatlas.ISLAND_ONLY)
    indexed = {f.ref: f.text for f in atlas.details.text_fields("islands", raw) if f.kind == "example"}
    for example in island.examples:
        passage = "".join(f"«{t}»" if i == example.focal_index else t for i, (t, _) in enumerate(example.tokens)).strip()
        assert indexed[example.ref] == passage


def test_island_examples_say_when_their_threshold_differs(atlas, upstream):
    import json

    path = f"public_atlas_data/islands/poles/{fakeatlas.RIVER}.json"
    raw = json.loads(upstream.files[path])
    for isl in raw["islands"]:
        for e in isl["examples"] + isl["examples_more"]:
            e["a_over_theta"] = round(abs(e["act"]) / 0.5, 3)  # the islands data's own θ is 0.5, the pole's 0.25
    upstream.files[path] = json.dumps(raw).encode()
    d = atlas.pole(fakeatlas.RIVER, ["modes", "examples", "islands"], max_examples=1)
    assert d.islands.islands[0].examples[0].a_rel == 5.0 and d.modes[0].examples[0].a_rel == 9.0
    assert d.islands.theta == pytest.approx(0.5, rel=0.01) and d.theta == 0.25
    assert any("θ ≈ 0.5" in n and "θ = 0.25" in n for n in d.notes)
    same = atlas.pole(fakeatlas.MIXED, ["modes", "examples", "islands"], max_examples=1)
    assert same.islands.islands[0].examples and not any("θ" in n for n in same.notes)  # one θ: nothing to say


def test_index_only_view_when_files_may_not_be_fetched(offline_atlas):
    d = offline_atlas.pole(fakeatlas.RIVER, ["modes", "examples", "overview", "landscape", "circuit"])
    assert [m.label for m in d.modes] == ["River names (Danube, Loire) in travel writing and verse"]
    assert d.modes[0].share == 0.7 and d.modes[0].description == "" and d.modes[0].examples == []
    assert d.sources == {
        "subcircuit": "not_mirrored", "islands": "not_requested", "overview": "not_mirrored",
        "landscape": "not_mirrored", "circuit": "not_mirrored",
    }  # fmt: skip
    assert d.overview is None and any("not mirrored" in n for n in d.notes)


def test_overview_view(atlas):
    ov = atlas.pole(fakeatlas.RIVER, ["overview"]).overview
    assert ov["headline"] == "River names, mostly in travel writing"
    # On a single-mode pole the scientist's entries refer to subcircuits.
    assert ov["situations_refer_to"] == "subcircuit" and ov["situations"][1]["subcircuit"] == "c1"
    assert ov["experiments"][0]["result"] == "Fell from 8× to 0.2×."
    assert [s["source"] for s in ov["simulatability"]] == ["fleet", "own"] and ov["simulatability"][1]["is_this_description"]
    assert ov["subcircuit_names"] == {"c0": "Travel prose", "c1": "Verse"}
    assert ov["report_markdown"].startswith("# Report") and ov["circuit_digest"]["output_tokens"][0]["token"] == " river"
    multi = atlas.pole(fakeatlas.MIXED, ["overview"]).overview
    assert multi["situations_refer_to"] == "mode" and multi["situations"][2]["mode_index"] == 2
    assert overview_view({})["situations"] == []  # tolerant of empty input


def test_landscape_numbers_follow_the_overviews_formula(atlas, upstream):
    import json

    raw = json.loads(upstream.files[f"neuron_landscape_data/{fakeatlas.RIVER}.json"])
    numbers = mode_numbers(raw)
    # Recompute by hand: weight of one sampled firing = mass / n of its level.
    weights = [b["mass"] / b["n"] for b in raw["bands"]]
    firings = [sum(w * b["counts"][k] for w, b in zip(weights, raw["bands"], strict=True)) for k in range(len(raw["modes"]))]
    damage = [sum(w * b["dmg_lin"][k] for w, b in zip(weights, raw["bands"], strict=True)) for k in range(len(raw["modes"]))]
    assert numbers[0]["firing_share"] == pytest.approx(firings[0] / sum(firings))
    assert numbers[0]["damage_share"] == pytest.approx(damage[0] / sum(damage))
    assert numbers[0]["damage_per_firing"] == pytest.approx((damage[0] / firings[0]) / (sum(damage) / sum(firings)))
    assert sum(n["firing_share"] for n in numbers) == pytest.approx(1.0)
    assert sum(n["tested_firings"] for n in numbers) == 12

    view = atlas.pole(fakeatlas.RIVER, ["landscape"]).landscape
    assert [m["kind"] for m in view["modes"]] == ["open", "closed_gate", "unassigned"]
    assert view["modes"][1]["relation"]["text"].startswith("A narrower case of “River names")
    assert view["levels"][0]["name"] == "Top 5%" and view["levels"][0]["one_in_tokens"] == 25
    assert view["damage_fit"]["slope"] == 2.5 and view["mode_similarity"]["by_firing"] is None
    assert landscape_view({})["modes"] == []


def test_circuit_view_per_pole(atlas, upstream):
    import json

    meta = json.loads(upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"])
    pos = atlas.pole(fakeatlas.RIVER, ["circuit"]).circuit
    assert pos["line"].startswith("Fires on river names") and pos["examples_analysed"] == 120
    assert [g["id"] for g in pos["inputs"]] == ["g1", "g2"]
    g1 = pos["inputs"][0]
    assert g1["name"] == "Geography words" and g1["lane"] == "gate + / up +" and g1["role"] == "drives"
    assert (g1["gate"], g1["up"], g1["share"], g1["n_members"]) == (0.08, 0.09, 0.9, 3)
    assert g1["members"][0]["pole"] == "pos" and g1["members"][0]["weight"] == 0.08 and g1["about"][0]["name"] == "Rivers"
    assert pos["inputs"][1]["description"] == "Neurons for city names."
    # r2 belongs to the other pole: the explorer's label says "-", the measurement says raised.
    r2 = next(r for r in pos["readers"] if r["id"] == "r2")
    assert r2["effect"] == "-" and r2["measured_direction"] == "raised"
    assert pos["output_tokens"] == [{"token": " river", "weight": 0.2, "static_weight": 0.2}]
    assert pos["strongest_contexts"][0]["text"] == "crossed the wide «Danube» near"

    neg = circuit_view(meta, "neg")
    assert [g["id"] for g in neg["inputs"]] == ["g3", "g_single_no"]  # the catch-all is placed by its name
    assert neg["line"].startswith("Uninterpretable: mixed") and neg["output_tokens"][0]["token"] == " oven"
    # The negative pole raises the logits of its tokens (negative activation x negative static weight).
    oven = neg["output_tokens"][0]
    assert oven["static_weight"] < 0 < oven["weight"] and oven["weight"] == -oven["static_weight"]
    assert circuit_view({}, "pos")["inputs"] == []


def test_circuit_view_keeps_unsigned_target_tokens_apart(upstream):
    import json

    meta = json.loads(upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"])
    # Rows with source "target" hold a mean |effect| over the examples of both poles; their sign is always 1.
    meta["logits"].append({"token": ",", "token_id": 11, "w": 0.003, "sign": 1, "source": "target", "n_active": 7})
    pos, neg = circuit_view(meta, "pos"), circuit_view(meta, "neg")
    assert [t["token"] for t in pos["output_tokens"]] == [" river"] and [t["token"] for t in neg["output_tokens"]] == [" oven"]
    assert pos["example_tokens"] == neg["example_tokens"] == [{"token": ",", "mean_abs_effect": 0.003, "examples_active": 7}]


def test_neuron_view_and_errors(atlas):
    n = atlas.neuron("L4/E1/N10", ["circuit"])
    assert [p.pole for p in n.poles] == ["pos", "neg"]
    # The two poles have different inputs: g1/g2 feed the positive pole, g3 and the catch-all the negative one.
    assert [g["id"] for g in n.circuits["pos"]["inputs"]] == ["g1", "g2"]
    assert [g["id"] for g in n.circuits["neg"]["inputs"]] == ["g3", "g_single_no"]
    assert n.circuit_summary["n_ex"] == 210
    lonely = atlas.neuron("L2_E5_N6")
    assert lonely.poles == [] and not lonely.in_atlas_index
    with pytest.raises(NotFound):
        atlas.pole("L77_E1_N1__pos")
    with pytest.raises(ValueError):
        atlas.pole("L4_E1_N10")  # a neuron: the pole is missing
    with pytest.raises(ValueError):
        atlas.pole(fakeatlas.RIVER, ["everything"])
    assert atlas.resolve_pid("L14_E78_N5") == fakeatlas.ISLAND_ONLY  # only one pole exists


def test_batch_and_raw(atlas):
    res = atlas.poles(["L4_E1_N10", "L9_E2_N20__pos", "nonsense", "L77_E1_N1"], ["modes"])
    assert [d.pole.pid for d in res.poles] == [fakeatlas.RIVER, fakeatlas.MIXED, fakeatlas.FN]
    assert res.not_found == ["nonsense", "L77_E1_N1"]
    assert atlas.raw("landscape", fakeatlas.RIVER)["pid"] == fakeatlas.RIVER
    assert atlas.raw("circuit_meta", "L4_E1_N10")["schema"].startswith("moe-circuit")
    with pytest.raises(ValueError):
        atlas.details.raw("secrets", fakeatlas.RIVER)


def test_text_helpers():
    tokens = [["a", None], ["", 1.0], [" b", None]]
    assert mark_focal(tokens, 1) == "a«» b"  # an empty firing token still shows where it fired
    assert mark_focal(tokens, None) == "a b"
    long = [[f" w{i}", 0.0] for i in range(100)]
    lo, hi = island_window(long, 50)
    assert (lo, hi) == (26, 76)  # 24 before, the peak, 25 after
    assert island_window(long, None) == (0, 100) and island_window(long[:5], 2) == (0, 5)


def test_a_reader_of_the_other_pole_keeps_its_direction_when_the_mean_rounds_to_zero(upstream):
    import json

    from neuron_atlas.render import _reader_effect
    from neuron_atlas.views import circuit_view

    meta = json.loads(upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"])
    rows = meta["pole_view"]["+"]["readers"]
    r2 = next(r for r in rows if r["id"] == "r2")  # home: the negative pole, label "-"
    r2["mean_w"] = 0.0  # a small positive mean, stored rounded: still "raised" on this pole
    item = next(r for r in circuit_view(meta, "pos")["readers"] if r["id"] == "r2")
    assert item["label_of_other_pole"] and item["measured_direction"] == "raised"
    r2["mean_w"] = -0.0  # agrees with the label: no contradiction to report, but the label is the other pole's
    item = next(r for r in circuit_view(meta, "pos")["readers"] if r["id"] == "r2")
    assert item["label_of_other_pole"] and "measured_direction" not in item
    assert _reader_effect(item) == "lowered by this pole (measured; the explorer's label comes from the other pole)"


def test_the_overview_digest_is_checked_against_the_circuit_file(atlas, upstream):
    unchecked = atlas.pole(fakeatlas.RIVER, ["overview"]).overview["circuit_digest"]
    assert unchecked["checked"] is False  # the circuit file is not mirrored yet: nothing is downloaded for this
    assert unchecked["output_tokens"] == [{"token": " river", "weight": 0.2, "static_weight": 0.2}]  # same shape

    from neuron_atlas.details import align_digest

    neg = {"output_tokens": [{"token": "truk", "weight": -0.1057}, {"token": " the", "weight": 0.0168}]}
    align_digest(neg, None, "neg")  # a negative pole: raw static weights are negative, unsigned rows positive
    assert neg["output_tokens"] == [{"token": "truk", "weight": 0.1057, "static_weight": -0.1057}]
    atlas.pole(fakeatlas.RIVER, ["circuit"])  # now it is
    digest = atlas.pole(fakeatlas.RIVER, ["overview"]).overview["circuit_digest"]
    assert digest["checked"] is True and digest["output_tokens"] == [{"token": " river", "weight": 0.2, "static_weight": 0.2}]
