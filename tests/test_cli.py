import json

import pytest
from typer.testing import CliRunner

from neuron_atlas import cli
from neuron_atlas.service import Atlas
from neuron_atlas.upstream import AtlasSource

from . import fakeatlas


@pytest.fixture
def run(settings, upstream, monkeypatch):
    """Invoke the CLI against the fake upstream."""
    monkeypatch.setattr(cli, "_atlas", lambda: Atlas(source=AtlasSource(settings, client=upstream.client())))
    runner = CliRunner()

    def invoke(*args: str):
        return runner.invoke(cli.app, list(args))

    return invoke


def test_info_and_search(run):
    assert "9 (8 listed on goodfire.com)" in run("info").output
    assert json.loads(run("info", "--json").output)["atlas"]["neurons"] == 6
    out = run("search", "braille").output
    assert "L39/E4/N1" in out and "1 hits" in out
    data = json.loads(run("search", "names", "--cat", "fn", "-f", "json").output)
    assert [h["pid"] for h in data["hits"]] == [fakeatlas.FN]
    assert "mode" in run("search", "chess", "--group-by", "mode").output
    assert run("search", "has:nonsense").exit_code == 1


def test_show_in_every_format(run, tmp_path):
    md = run("show", "+ L4/E1/N10", "-e", "1").output
    assert "L4/E1/N10" in md and "Danube" in md
    assert json.loads(run("show", fakeatlas.RIVER, "-f", "json").output)["pole"]["pid"] == fakeatlas.RIVER
    target = tmp_path / "pole.html"
    assert run("show", fakeatlas.RIVER, "-f", "html", "-o", str(target)).exit_code == 0
    assert "<html" in target.read_text(encoding="utf-8").lower()
    assert run("show", "L77_E1_N1__pos").exit_code == 2
    assert run("show", "L4_E1_N10").exit_code == 1  # a neuron: the pole is missing
    assert run("show", fakeatlas.RIVER, "-f", "docx").exit_code == 1
    assert "L4/E1/N10" in run("neuron", "L4_E1_N10", "--circuit").output


def test_similar_and_export(run, tmp_path):
    assert "L4/E1/N10" in run("similar", "--text", "chess opening names").output
    assert run("similar").exit_code == 1
    target = tmp_path / "all.csv"
    result = run("export", "", "-f", "csv", "-o", str(target))
    assert result.exit_code == 0 and target.read_text(encoding="utf-8").count("\n") == 10
    lines = run("export", "layer:4", "-f", "ndjson").output.strip().splitlines()
    assert [json.loads(x)["pid"] for x in lines if x.startswith("{")] == [fakeatlas.RIVER, fakeatlas.MIXED]


def test_mirror_dry_run_download_and_index(run):
    assert run("mirror").exit_code == 1  # nothing chosen
    dry = run("mirror", "--ids", "L4_E1_N10", "--dry-run").output
    assert "2 poles of 1 neurons" in dry and "11 to fetch" in dry
    done = run("mirror", "--ids", "L4_E1_N10").output
    assert "fetched 11 files" in done and "indexed" in done
    assert "nothing to download" in run("mirror", "--ids", "L4_E1_N10").output
    assert "indexed" in run("index", "--examples").output
    assert "L4/E1/N10" in run("search", "geography").output  # a deep text, now searchable


def test_geometry_commands(run, tmp_path):
    run("mirror", "--all", "--yes", "--no-index")
    out = run("geometry", "tables", str(tmp_path / "tables")).output
    assert "firings: 48 rows" in out and (tmp_path / "tables" / "circuit_edges.csv").exists()
    assert "points" in run("geometry", "map", str(tmp_path / "map.html"), "--clusters", "2").output
    assert (tmp_path / "map.html").stat().st_size > 2000
    assert "1 neurons" not in run("geometry", "graph", str(tmp_path / "g.graphml")).output
    assert (tmp_path / "g.graphml").exists()


def test_atlas_text_is_printed_as_text_not_as_markup(run, monkeypatch):
    original = Atlas.search

    def search(self, query, **kwargs):
        res = original(self, query, **kwargs)
        for hit in res.hits:
            hit.headline = "Closing tags such as '</a', '[/lastfm]' and [named hazard/factor]"
        res.notes.append("note with [/bold] in it")
        return res

    monkeypatch.setattr(Atlas, "search", search)
    result = run("search", "braille")
    assert result.exit_code == 0, result.output
    assert "[/lastfm]" in result.output and "[named hazard/factor]" in result.output
