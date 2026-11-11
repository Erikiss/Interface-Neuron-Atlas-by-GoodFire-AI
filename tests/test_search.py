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
    with pytest.raises(ValueError, match="has"):
        atlas.search("has:teleportation")
    with pytest.raises(ValueError, match="not a number"):
        atlas.search("rivers layer:abc")
    with pytest.raises(ValueError, match="neither"):
        atlas.search("verdict:banana")
    with pytest.raises(ValueError, match="unknown category"):
        atlas.search("cat:purple")
    # The one-letter aliases give way to text: "e:mail" is a word, not a broken filter.
    res = atlas.search("e:mail")
    assert any("searched as text" in n for n in res.notes)


def test_inline_filters_need_the_value_right_after_the_colon(atlas):
    # "in: chapter" in a sentence is text, not a scope filter.
    assert parse_query(SearchQuery(q="results in: chapter")).text == "results in: chapter"
    # Inside quotes nothing is a filter.
    parsed = parse_query(SearchQuery(q='"layer:4 notes"'))
    assert parsed.query.layers is None and parsed.groups[0][0].phrase


def test_ranges_are_bounded_and_may_be_reversed(atlas):
    parsed = parse_query(SearchQuery(q="layer:36..9 expert:3..1"))
    assert (parsed.query.layer_min, parsed.query.layer_max) == (9, 36) and parsed.query.experts == [1, 2, 3]
    with pytest.raises(ValueError, match="too large"):
        parse_query(SearchQuery(q="neuron:0..99999999999"))
    with pytest.raises(ValueError, match="usable"):
        parse_query(SearchQuery(q="sim:>" + "9" * 400))


def test_operators_are_capitals_only_and_terms_are_limited(atlas):
    # Lower-case "or", "not" are words; a trailing NOT does not swallow the terms before it.
    assert pids(atlas.search("chess or braille")) == []
    assert pids(atlas.search("chess NOT")) == [fakeatlas.MIXED]
    assert atlas.search("NOT").total == 0  # searched as a word, not "everything"
    many = " ".join(f"word{i}" for i in range(40))
    with pytest.raises(ValueError, match="too many search terms"):
        atlas.search(many)
    assert atlas.search(" ".join(["chess"] * 200)).total == 1  # repeats count once


def test_punctuation_is_matched_as_characters_not_as_one_letter_words(atlas):
    # Without the fix "C++" became the word prefix c* and matched almost everything.
    assert atlas.search("C++").total == 0
    assert pids(atlas.search("#ff8800")) == [fakeatlas.MIXED]


def test_whole_words_rank_above_word_beginnings(atlas):
    atlas.search_index.index_deep(fakeatlas.FN, "subcircuit", "t1", [TextField("subcircuit", "chessboard patterns")])
    hits = pids(atlas.search("chess"))
    assert hits[0] == fakeatlas.MIXED and fakeatlas.FN in hits


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


def test_the_model_loader_stays_offline_and_falls_back_to_lsa(tmp_path, monkeypatch):
    import sys
    import types

    from neuron_atlas.semantic import FastEmbedEmbedder, LsaEmbedder, make_embedder

    calls = []

    class TextEmbedding:
        def __init__(self, model_name, cache_dir=None, **kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise ValueError("model not found in the cache")

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=TextEmbedding))
    embedder, note = make_embedder("fastembed", model_dir=tmp_path, offline=True)
    assert isinstance(embedder, LsaEmbedder) and "offline" in note and calls == [{"local_files_only": True}]
    online, note = make_embedder("fastembed", model_dir=tmp_path)
    assert isinstance(online, FastEmbedEmbedder) and note is None and calls[-1] == {}


def test_a_leading_minus_excludes_words_only():
    def parse(text):
        return parse_query(SearchQuery(q=text))

    assert [t.text for t in parse("hash -base64").negatives] == ["base64"]
    arrow = parse("->")
    assert arrow.negatives == [] and [t.text for g in arrow.groups for t in g] == ["->"]
    assert parse("--verbose").negatives == []
