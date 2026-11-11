import pytest

from neuron_atlas.catalog import Catalog, ShellConstants, sample_hash

from . import fakeatlas


def test_sample_hash_matches_the_explorer():
    # Values computed with the explorer's own rhash() (seed 20261109).
    assert sample_hash("L30_E47_N449__pos") == 206476305
    assert sample_hash("L24_E31_N88__neg") == 1274435
    assert sample_hash("L4_E1_N177__neg") == 1067936918
    assert sample_hash("L30_E143_N28__neg") == 2353800160
    assert sample_hash("abc", seed=0) != sample_hash("abc", seed=1)
    assert 0 <= sample_hash("L4_E1_N10__pos") < 2**32


def test_merge_follows_the_explorers_rules(atlas):
    cat = atlas.catalog
    river = cat.pole(fakeatlas.RIVER)
    assert river.category == "mono" and river.has_subcircuit
    # The subcircuit read replaces the island description; the old one is kept.
    assert river.description == "River names (Danube, Loire) in travel writing and verse"
    assert river.island_description == "River names in running text" and river.island_category == "mono"
    assert river.simulatability == 0.81 and river.headline.startswith("Names of rivers")
    assert [m.index for m in river.modes] == [0] and river.n_subcircuits == 4

    assert cat.pole(fakeatlas.MIXED).category == "poly"
    assert cat.pole(fakeatlas.FN).category == "fn"  # polysemantic verdict + fn_mono
    assert cat.pole(fakeatlas.FN).main_damage_share == 0.95
    dark = cat.pole(fakeatlas.DARK)
    assert dark.category == "dark" and not dark.has_subcircuit and dark.description == ""


def test_a_read_without_modes_is_a_failed_read(atlas):
    failed = atlas.catalog.pole(fakeatlas.FAILED_READ)
    assert failed.subcircuit_empty and not failed.has_subcircuit
    assert failed.category == "mono" and failed.description == "Knitting stitch names"  # island values stay
    assert failed.modes == []


def test_missing_category_becomes_none_and_coverage_flags_are_set(atlas):
    cat = atlas.catalog
    pole = cat.pole("L30_E3_N7__neg")
    assert pole.category == "poly" and not pole.has_islands
    assert cat.pole(fakeatlas.ISLAND_ONLY).has_islands and not cat.pole(fakeatlas.ISLAND_ONLY).has_overview
    assert cat.pole(fakeatlas.RIVER).has_circuit and not cat.pole(fakeatlas.FN).has_circuit


def test_circuit_only_neurons_are_known_and_junk_ids_are_dropped(atlas):
    cat = atlas.catalog
    extra = cat.neuron("L2_E5_N6")
    assert extra is not None and not extra.in_atlas_index and extra.poles == {}
    assert extra.circuit_label == "A neuron only the circuit index knows"
    assert cat.neuron("DEMO_FIXTURE") is None
    assert cat.model == "Test-Model-1"


def test_public_sample_excludes_the_whole_expert_and_takes_the_first_by_hash(atlas, upstream):
    full = atlas.catalog
    assert not full.pole(fakeatlas.ISLAND_ONLY).in_public_sample  # L14/E78 is left out
    assert sum(p.in_public_sample for p in full.iter_poles()) == 8  # all other poles fit in 3,000

    import json

    files = {k: json.loads(v) for k, v in upstream.files.items()}
    small = Catalog.build(
        atlas=files["public_atlas_data/index.json"],
        subcircuit=files["subcircuit_modes_index.json"],
        shell=ShellConstants(sample_size=3),
    )
    sampled = sorted((p for p in small.iter_poles() if p.in_public_sample), key=lambda p: p.sample_rank)
    assert len(sampled) == 3
    candidates = [p for p in small.iter_poles() if p.did != "L14_E78_N5"]
    expected = sorted(candidates, key=lambda p: (sample_hash(p.pid), p.pid))[:3]
    assert [p.pid for p in sampled] == [p.pid for p in expected]
    assert sampled[0].atlas_url().endswith(f"?n={sampled[0].did}&pole={sampled[0].pole}")
    assert small.pole(fakeatlas.ISLAND_ONLY).atlas_url() is None


def test_shell_constants_are_read_from_the_page():
    html = 'const RSEED=42; const SAMPLE_SIZE=10; const NOT_SAMPLE=new Set(["L1_E2_N3","junk"]); n.L===7&&n.E===9'
    shell = ShellConstants.from_shell_html(html)
    assert (shell.seed, shell.sample_size) == (42, 10)
    assert shell.not_sampled_neurons == {"L1_E2_N3"} and shell.not_sampled_experts == {(7, 9)}
    assert ShellConstants.from_shell_html("nothing here") == ShellConstants()


def test_stats_and_optional_indices(atlas, upstream):
    stats = atlas.catalog.stats()
    assert stats["poles"] == 9 and stats["neurons"] == 6 and stats["modes"] == 12
    assert stats["categories"]["dark"] == 1 and stats["poles_with_simulatability"] == 2

    import json

    bare = Catalog.build(atlas=json.loads(upstream.files["public_atlas_data/index.json"]))
    assert bare.pole(fakeatlas.RIVER).description == "River names in running text"
    assert bare.pole(fakeatlas.RIVER).modes == []


def _fixture_indices(upstream):
    import json

    def load(path):
        return json.loads(upstream.files[path])

    return load


def test_a_pole_without_category_and_read_is_none(upstream):
    load = _fixture_indices(upstream)
    atlas = load("public_atlas_data/index.json")
    for raw in atlas["neurons"]:
        for pole in raw["poles"].values():
            if pole and pole["pid"] == "L30_E3_N7__neg":
                pole["cat"] = None
    # Without the subcircuit index the pole has no read: it is "not yet read".
    cat = Catalog.build(atlas=atlas)
    pole = cat.pole("L30_E3_N7__neg")
    assert pole.category == "none" and pole.category_words == "not yet read"
    assert cat.stats()["categories"]["none"] >= 1


def test_poles_only_the_other_indices_name_are_kept_but_marked(upstream):
    load = _fixture_indices(upstream)
    sub = load("subcircuit_modes_index.json")
    sub["poles"]["L50_E1_N1__pos"] = {
        "verdict": "monosemantic", "n_modes": 1, "modes": [{"label": "Only in the subcircuit index", "share": 1.0}]
    }  # fmt: skip
    cat = Catalog.build(
        atlas=load("public_atlas_data/index.json"),
        subcircuit=sub,
        overview={"pids": ["L50_E1_N1__neg"], "sim": {}, "model": {}},
        shell=ShellConstants(sample_size=10_000),
    )
    pos, neg = cat.pole("L50_E1_N1__pos"), cat.pole("L50_E1_N1__neg")
    assert pos.category == "mono" and pos.description == "Only in the subcircuit index" and pos.has_subcircuit
    assert neg.has_overview and neg.category == "none"
    assert not pos.in_atlas_index and not pos.in_public_sample and not neg.in_public_sample
    assert not cat.neuron("L50_E1_N1").in_atlas_index
    # The public sample is unchanged: every pole of the atlas index is in it (size 10,000), the extras are not.
    assert all(p.in_public_sample == p.in_atlas_index for p in cat.iter_poles() if p.layer != 14)


def test_a_failing_optional_index_fails_the_load_instead_of_degrading_it(settings, upstream, monkeypatch):
    import time

    import httpx

    from neuron_atlas.upstream import AtlasSource, UpstreamError

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    broken = {"on": True}

    def handler(request):
        if broken["on"] and str(request.url).endswith("subcircuit_modes_index.json"):
            return httpx.Response(503, text="overloaded")
        return upstream.handler(request)

    with AtlasSource(settings, client=httpx.Client(transport=httpx.MockTransport(handler))) as src:
        with pytest.raises(UpstreamError):
            Catalog.load(src)
        broken["on"] = False
        assert Catalog.load(src).stats()["categories"].get("fn") == 1  # the next attempt has every index


def test_the_catalog_loads_with_read_through_switched_off(settings, upstream):
    from neuron_atlas.upstream import AtlasSource

    with AtlasSource(settings.with_overrides(allow_remote_fetch=False), client=upstream.client()) as src:
        assert len(Catalog.load(src)) == 9
