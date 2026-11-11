import pytest

from neuron_atlas.models import SearchQuery
from neuron_atlas.search import TextField, parse_query

from . import fakeatlas


def pids(response):
    return [h.pid for h in response.hits]


def test_plain_words_find_modes_and_say_which_one(atlas):
    res = atlas.search("braille")
    assert pids(res) == ["L39_E4_N1__neg"]
    match = res.hits[0].matches[0]
    assert match.field == "mode" and match.mode_index == 1 and match.share == 0.2


def test_prefix_phrase_exclusion_and_or(atlas):
    assert fakeatlas.RIVER in pids(atlas.search("riv"))  # word beginnings match
    assert pids(atlas.search('"chess opening"')) == [fakeatlas.MIXED]
    assert pids(atlas.search('"opening chess"')) == []
    both = pids(atlas.search("names"))
    assert fakeatlas.RIVER in both and fakeatlas.FN in both
    assert fakeatlas.FN not in pids(atlas.search("names -planets"))
    assert fakeatlas.FN not in pids(atlas.search("names NOT planets"))
    assert set(pids(atlas.search("sourdough OR braille"))) == {fakeatlas.MIXED, "L39_E4_N1__neg"}
    assert pids(atlas.search("sourdough braille")) == []  # all terms must match the same pole
    assert pids(atlas.search("sourdough chess")) == [fakeatlas.MIXED]  # ... but may sit in different modes


def test_terms_the_word_index_cannot_see_fall_back_to_substring(atlas):
    assert pids(atlas.search("#ff8800")) == [fakeatlas.MIXED]
    assert pids(atlas.search("anub")) == [fakeatlas.RIVER]  # inside "Danube"
    assert pids(atlas.search(SearchQuery(q="anub", match="words"))) == []
    assert pids(atlas.search(SearchQuery(q="anub", match="substring"))) == [fakeatlas.RIVER]


def test_ids_open_poles_directly(atlas):
    assert pids(atlas.search("L4/E1/N10")) == [fakeatlas.RIVER, fakeatlas.MIXED]
    assert pids(atlas.search("− L4/E1/N10")) == [fakeatlas.MIXED]
    res = atlas.search("L77_E1_N1")
    assert res.total == 0 and "no such neuron" in res.notes[0]
    assert {h.did for h in atlas.search("L39").hits} == {"L39_E4_N1"}  # a bare layer token


def test_inline_filters(atlas):
    assert pids(atlas.search("layer:4")) == [fakeatlas.RIVER, fakeatlas.MIXED]
    assert pids(atlas.search("layer:4 pole:neg")) == [fakeatlas.MIXED]
    assert pids(atlas.search("cat:fn")) == [fakeatlas.FN]
    assert pids(atlas.search("cat:dark,fn")) == [fakeatlas.FN, fakeatlas.DARK]
    assert pids(atlas.search("modes:>=3")) == [fakeatlas.MIXED, "L39_E4_N1__neg"]
    assert pids(atlas.search("sim:>0.5")) == [fakeatlas.RIVER]
    assert pids(atlas.search("has:simulatability sim:<0")) == [fakeatlas.FN]
    assert fakeatlas.ISLAND_ONLY not in pids(atlas.search("sample:public"))
    assert pids(atlas.search("sample:hidden")) == [fakeatlas.ISLAND_ONLY]
    assert pids(atlas.search("layer:9..30 has:islands lacks:subcircuit")) == [fakeatlas.DARK, fakeatlas.ISLAND_ONLY, fakeatlas.FAILED_READ]
    parsed = parse_query(SearchQuery(q='names layer:4,9 cat:mono sort:layer'))
    assert parsed.text == "names" and parsed.query.layers == [4, 9] and parsed.query.sort == "layer"


def test_unknown_inline_values_are_reported_not_swallowed(atlas):
    with pytest.raises(ValueError):
        atlas.search("has:teleportation")
    res = atlas.search("rivers layer:abc")
    assert any("ignored filter" in n for n in res.notes)


def test_structured_filters_and_facets(atlas):
    res = atlas.search(SearchQuery(categories=["mono"], facets=True, limit=100))
    assert {h.category for h in res.hits} == {"mono"}
    assert res.facets["category"] == {"mono": res.total}
    assert sum(res.facets["layer"].values()) == res.total
    res = atlas.search(SearchQuery(ids=["L39_E4_N1", "L4/E1/N10 +"]))
    assert pids(res) == [fakeatlas.RIVER, "L39_E4_N1__pos", "L39_E4_N1__neg"]


def test_sorting_puts_missing_values_last_in_both_directions(atlas):
    res = atlas.search(SearchQuery(sort="simulatability", limit=100))
    assert pids(res)[:2] == [fakeatlas.RIVER, fakeatlas.FN]
    res = atlas.search(SearchQuery(sort="simulatability", order="asc", limit=100))
    assert pids(res)[:2] == [fakeatlas.FN, fakeatlas.RIVER]
    assert all(h.simulatability is None for h in res.hits[2:])
    by_modes = atlas.search(SearchQuery(sort="modes", limit=3))
    assert by_modes.hits[0].n_modes == 3


def test_paging(atlas):
    first = atlas.search(SearchQuery(limit=4))
    assert first.total == 9 and len(first.hits) == 4 and first.next_offset == 4
    last = atlas.search(SearchQuery(limit=4, offset=8))
    assert len(last.hits) == 1 and last.next_offset is None
    everything = [h.pid for h in atlas.iter_search(SearchQuery(), page=2)]
    assert len(everything) == 9 and len(set(everything)) == 9


def test_grouping_by_neuron_and_by_mode(atlas):
    by_neuron = atlas.search(SearchQuery(q="names", group_by="neuron"))
    assert by_neuron.hits == [] and {n.did for n in by_neuron.neurons} >= {"L4_E1_N10", "L9_E2_N20"}
    by_mode = atlas.search(SearchQuery(q="chess", group_by="mode"))
    assert [(m.pid, m.mode_index) for m in by_mode.modes] == [(fakeatlas.MIXED, 1)]
    assert by_mode.modes[0].label.startswith("Chess opening")
    all_modes = atlas.search(SearchQuery(group_by="mode", limit=100))
    assert all_modes.total == 12


def test_regex_and_its_guards(atlas):
    assert pids(atlas.search(SearchQuery(q=r"#[0-9a-f]{6}", match="regex"))) == [fakeatlas.MIXED]
    with pytest.raises(ValueError):
        atlas.search(SearchQuery(q="(unclosed", match="regex"))
    with pytest.raises(ValueError):
        atlas.search(SearchQuery(q="a" * 400, match="regex"))
    atlas.search_index.allow_regex = False
    with pytest.raises(ValueError):
        atlas.search(SearchQuery(q="x", match="regex"))


def test_odd_input_never_breaks_the_word_index(atlas):
    for text in ['"', '""', "AND", "OR", "NOT", "-", "*", "(((", "a:b:c", "\x00", "🙂", "x" * 500, "layer:", 'col:"x']:
        atlas.search(text)  # must not raise


def test_deep_texts_become_searchable_after_a_pole_was_read(atlas):
    assert atlas.search("geography").total == 0
    atlas.pole(fakeatlas.RIVER, ["modes"])
    hit = atlas.search("geography").hits[0]
    assert hit.pid == fakeatlas.RIVER and hit.matches[0].field == "subcircuit" and hit.matches[0].ref
    assert atlas.search(SearchQuery(q="geography", scope=["labels"])).total == 0
    # Example passages are a separate, opt-in scope.
    assert atlas.search(SearchQuery(q="stone bridge", scope=["examples"])).total == 0
    atlas.index_mirrored([fakeatlas.RIVER], examples=True)
    assert pids(atlas.search(SearchQuery(q='"stone bridge"', scope=["examples"]))) == [fakeatlas.RIVER]


def test_deep_index_is_idempotent_and_replaceable(atlas):
    index = atlas.search_index
    fields = [TextField("subcircuit", "zebra crossing", ref="c0")]
    assert index.index_deep(fakeatlas.FN, "subcircuit", "sha1", fields) == 1
    assert index.index_deep(fakeatlas.FN, "subcircuit", "sha1", fields) == 0
    assert pids(atlas.search("zebra")) == [fakeatlas.FN]
    index.index_deep(fakeatlas.FN, "subcircuit", "sha2", [TextField("subcircuit", "giraffe")])
    assert atlas.search("zebra").total == 0 and pids(atlas.search("giraffe")) == [fakeatlas.FN]
    index.drop_deep(fakeatlas.FN)
    assert atlas.search("giraffe").total == 0


def test_semantic_search_and_similarity(atlas):
    res = atlas.search(SearchQuery(q="chess opening names", match="semantic"))
    assert res.hits[0].pid == fakeatlas.MIXED
    assert atlas.search(SearchQuery(q="qwertyuiop", match="semantic")).total == 0
    sim = atlas.similar(text="names of rivers")
    assert sim.items and sim.by == "text"
    assert atlas.similar(pole=fakeatlas.MIXED).by == "pole"
    assert atlas.similar(pole=fakeatlas.MIXED, mode=1).anchor.endswith("mode 1")
    with pytest.raises(ValueError):
        atlas.similar()


def test_the_page_size_is_clamped(atlas, settings):
    atlas.settings = settings.with_overrides(max_page_size=2)
    assert len(atlas.search(SearchQuery(limit=500)).hits) == 2
