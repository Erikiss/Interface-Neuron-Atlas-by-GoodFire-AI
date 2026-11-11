import time

import pytest

from neuron_atlas.mirror import MirrorJobs, files_of_pole, make_plan, mirrored_sources, run_plan
from neuron_atlas.models import MirrorRequest, SearchQuery, Selection

from . import fakeatlas


def test_a_plan_lists_exactly_the_files_that_exist(atlas):
    cat = atlas.catalog
    assert set(files_of_pole(cat.pole(fakeatlas.RIVER))) == {
        "subcircuit", "overview", "landscape", "islands", "circuit_meta", "circuit_exemplars", "circuit_incidence",
    }  # fmt: skip
    assert set(files_of_pole(cat.pole(fakeatlas.DARK))) == {"islands"}
    assert set(files_of_pole(cat.pole(fakeatlas.FAILED_READ))) == {"subcircuit", "islands"}
    assert set(files_of_pole(cat.pole(fakeatlas.RIVER), ["landscape"])) == {"landscape"}

    plan = make_plan(cat, atlas.source, [fakeatlas.RIVER, fakeatlas.MIXED, "L77_E1_N1__pos"])
    assert plan.poles == [fakeatlas.RIVER, fakeatlas.MIXED] and plan.neurons == ["L4_E1_N10"]
    est = plan.estimate()
    # 7 index files (already fetched to build the catalog) + 2 x 4 pole files + 3 shared circuit files.
    assert est["files_total"] == 18 and est["files_present"] == 7 and est["files_to_fetch"] == 11
    assert est["to_fetch_by_kind"]["circuit_meta"] == 1 and est["estimated_download_bytes"] > 0
    with pytest.raises(ValueError):
        make_plan(cat, atlas.source, [fakeatlas.RIVER], ["everything"])


def test_running_a_plan_is_resumable_and_never_refetches(atlas, upstream):
    plan = make_plan(atlas.catalog, atlas.source, [fakeatlas.RIVER, fakeatlas.FAILED_READ])
    report = run_plan(plan, atlas.source)
    # The failed read is listed in the subcircuit index, but upstream has no file for it.
    assert report.fetched == 8 and report.absent == 1 and report.failed == 0
    before = len(upstream.requests)
    again = make_plan(atlas.catalog, atlas.source, [fakeatlas.RIVER, fakeatlas.FAILED_READ])
    assert again.missing == [] and again.absent == 1
    assert run_plan(again, atlas.source).fetched == 0 and len(upstream.requests) == before
    have = mirrored_sources(atlas.catalog, atlas.source, [fakeatlas.RIVER, fakeatlas.MIXED, fakeatlas.FAILED_READ])
    assert have[fakeatlas.RIVER] == ["subcircuit", "overview", "landscape", "islands", "circuit"]
    assert have[fakeatlas.MIXED] == ["circuit"]  # circuit files belong to the neuron
    assert have[fakeatlas.FAILED_READ] == ["islands"]


def test_a_plan_can_be_cancelled(atlas):
    plan = make_plan(atlas.catalog, atlas.source, list(atlas.catalog.poles))
    report = run_plan(plan, atlas.source, should_stop=lambda: True)
    assert report.cancelled and report.fetched == 0


def test_selections(atlas):
    select = atlas.select
    assert len(select(Selection(all=True))) == 9
    assert fakeatlas.ISLAND_ONLY not in select(Selection(public_sample=True))
    assert select(Selection(ids=["L4/E1/N10", "L9_E2_N20__neg", "nope"])) == [fakeatlas.RIVER, fakeatlas.MIXED, fakeatlas.DARK]
    assert select(Selection(query=SearchQuery(q="cat:fn", limit=1))) == [fakeatlas.FN]
    assert select(Selection(query=SearchQuery(q="layer:4"), ids=["L9_E2_N20__pos"])) == [fakeatlas.FN, fakeatlas.RIVER, fakeatlas.MIXED]
    assert select(Selection(similar_to_text="chess opening names", top=1)) == [fakeatlas.MIXED]
    assert select(Selection(similar_to_pole=fakeatlas.MIXED, top=2))[0] == fakeatlas.MIXED
    assert len(select(Selection(all=True, max_poles=3))) == 3
    assert select(Selection()) == []


def test_circuit_neighbours_extend_a_selection_using_mirrored_files_only(atlas, upstream):
    upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"] = upstream.files[
        "moe_circuit_explorer_data/L4_E1_N10/meta.json"
    ].replace(b"L5_E40_N500", b"L9_E2_N20")
    sel = Selection(ids=[fakeatlas.RIVER], neighbours=1)
    assert atlas.select(sel) == [fakeatlas.RIVER]  # nothing mirrored yet: nothing to follow
    atlas.mirror(MirrorRequest(selection=Selection(ids=[fakeatlas.RIVER]), sources=["circuit"], index=False))
    assert atlas.select(sel) == [fakeatlas.RIVER, fakeatlas.FN, fakeatlas.DARK]


def test_mirror_then_search_deep_texts(atlas):
    request = MirrorRequest(selection=Selection(ids=["L4_E1_N10"]), index_examples=True)
    plan, report = atlas.mirror(MirrorRequest(selection=request.selection, dry_run=True))
    assert report.fetched == 0 and len(plan.missing) == 11
    plan, report = atlas.mirror(request)
    assert report.fetched == 11 and report.failed == 0
    stats = atlas.search_index.stats()
    assert stats["poles_with_deep_texts"] == 2 and stats["example_texts"] > 0
    assert atlas.search(SearchQuery(q="has:mirrored", limit=50)).total == 2
    assert atlas.search("experiments OR Vienna").hits[0].matches[0].field in {"overview", "report"}


def test_background_jobs(atlas):
    jobs = MirrorJobs(atlas.source)
    done = []
    jobs.after = done.append
    job = jobs.submit(make_plan(atlas.catalog, atlas.source, [fakeatlas.RIVER]), label="test")
    deadline = time.time() + 10
    while job.state not in ("done", "failed") and time.time() < deadline:
        time.sleep(0.01)
    info = job.as_dict()
    assert info["state"] == "done" and info["progress"] == 1.0 and info["report"]["fetched"] == 7
    assert done == [job] and jobs.get(job.id) is job and jobs.list() == [job]
    assert not jobs.cancel(job.id) and not jobs.cancel("unknown")
