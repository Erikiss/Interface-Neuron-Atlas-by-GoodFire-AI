import csv
import json
import xml.dom.minidom

import pytest

from neuron_atlas.geometry.graph import (
    circuit_edges,
    clean_group_description,
    clean_group_name,
    group_side,
    neighbourhood,
    neuron_links,
    shared_input_similarity,
    write_graph,
)
from neuron_atlas.geometry.maps import project, semantic_map, write_map_html
from neuron_atlas.geometry.tables import TABLES, firing_rows, write_tables
from neuron_atlas.models import MirrorRequest, Selection

from . import fakeatlas


@pytest.fixture
def mirrored(atlas):
    atlas.mirror(MirrorRequest(selection=Selection(all=True), index=False))
    return atlas


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_circuit_edges_follow_the_explorers_sign_rules(upstream):
    meta = json.loads(upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"])
    edges = list(circuit_edges(meta))
    assert len(edges) == 9
    first = edges[0]
    assert (first["kind"], first["target"], first["target_pole"], first["role"]) == ("input", "L4_E1_N10", "pos", "drives")
    assert (first["source_pole"], first["gate"], first["up"], first["layer_distance"]) == ("pos", 0.05, 0.08, -1)
    catch_all = next(e for e in edges if e["group"] == "g_single_no")
    assert (catch_all["target_pole"], catch_all["role"]) == ("neg", "opposes")  # parsed from the group name
    readers = [e for e in edges if e["kind"] == "reader"]
    assert [(e["source_pole"], e["effect"], e["weight"]) for e in readers] == [("pos", "raised", 0.02), ("pos", "raised", 0.01), ("neg", "lowered", 0.03)]
    assert len(neuron_links(meta)) == 9 and neuron_links(None) == []


def test_group_helpers():
    assert group_side({"bucket": "-|drives"}) == "-"
    assert group_side({"name": "single units · + pole, drives"}) == "+"
    assert group_side({"name": "mystery"}) is None
    assert clean_group_name({"name": "Geography words · gate + / up −"}) == "Geography words"
    assert clean_group_name({"name": ""}) == "Unnamed group"
    assert clean_group_description({"description": "+ pole, drives — 3 categories: A (2) · B (1)"}) == ""
    assert clean_group_description({"description": "- pole, opposes — Units for city names."}) == "Neurons for city names."


def test_neighbourhood_keeps_the_strongest_edges_within_the_budget(mirrored):
    full = neighbourhood(mirrored, "L4_E1_N10", depth=1, max_nodes=100)
    assert len(full["nodes"]) == 10 and full["nodes"][0]["id"] == "L4_E1_N10" and full["nodes"][0]["in_atlas"]
    assert {(e["kind"], e["sign"]) for e in full["edges"]} >= {("gate", "+"), ("gate", "-"), ("up", "-"), ("reader", "-")}
    small = neighbourhood(mirrored, "L4_E1_N10", depth=1, max_nodes=3)
    assert len(small["nodes"]) == 3 and "left out" in small["notes"][0]
    assert small["edges"][0]["weight"] == max(e["weight"] for e in full["edges"])
    assert neighbourhood(mirrored, "L39_E4_N1")["notes"]


def test_neighbourhood_falls_back_to_subcircuit_links(atlas):
    atlas.pole(fakeatlas.RIVER)  # mirrors only the subcircuit file
    atlas.source.settings = atlas.source.settings.with_overrides(offline=True)
    graph = neighbourhood(atlas, "L4_E1_N10")
    assert graph["edges"] and "subcircuit files" in graph["notes"][0]


def test_graph_files(mirrored, tmp_path):
    stats = write_graph(mirrored, tmp_path / "g.csv")
    assert stats == {"nodes": 10, "edges": 9}
    assert len(read_csv(tmp_path / "g.csv")) == 9 and len(read_csv(tmp_path / "g_nodes.csv")) == 10
    write_graph(mirrored, tmp_path / "g.json")
    assert len(json.loads((tmp_path / "g.json").read_text())["nodes"]) == 10
    write_graph(mirrored, tmp_path / "g.graphml")
    doc = xml.dom.minidom.parse(str(tmp_path / "g.graphml"))
    assert len(doc.getElementsByTagName("node")) == 10 and len(doc.getElementsByTagName("edge")) == 9
    with pytest.raises(ValueError):
        write_graph(mirrored, tmp_path / "g.txt")
    assert write_graph(mirrored, tmp_path / "none.csv", dids=["L9_E2_N20"]) == {"nodes": 0, "edges": 0}


def test_shared_input_similarity_needs_shared_inputs(mirrored, upstream):
    assert shared_input_similarity(mirrored) == []  # one hub only: nothing to compare
    with pytest.raises(ValueError):
        shared_input_similarity(mirrored, lane="sideways")


def test_tables_cover_everything_that_is_mirrored(mirrored, tmp_path):
    written = write_tables(mirrored, tmp_path / "t")
    assert set(written) == set(TABLES)
    assert written["poles"] == 9 and written["modes"] == 6 and written["subcircuits"] == 7
    assert written["subcircuit_links"] == 7 * 13 and written["examples"] == 7 * 12
    assert written["landscape_levels"] == 10 and written["firings"] == 48 and written["circuit_edges"] == 9

    poles = {r["pid"]: r for r in read_csv(tmp_path / "t" / "poles.csv")}
    assert poles[fakeatlas.RIVER]["theta"] == "0.25" and poles[fakeatlas.DARK]["theta"] == ""
    links = read_csv(tmp_path / "t" / "subcircuit_links.csv")
    incoming = next(r for r in links if r["direction"] == "in")
    assert incoming["target"] == "L4_E1_N10" and incoming["source"] == incoming["unit"]
    outgoing = next(r for r in links if r["direction"] == "out")
    assert outgoing["source"] == "L4_E1_N10" and outgoing["role"] == "reader"
    modes = read_csv(tmp_path / "t" / "landscape_modes.csv")
    shares = [float(r["firing_share"]) for r in modes if r["pid"] == fakeatlas.RIVER]
    assert sum(shares) == pytest.approx(1.0)
    sim = read_csv(tmp_path / "t" / "mode_similarity.csv")
    assert {r["kind"] for r in sim} == {"description", "firing"}

    only = write_tables(mirrored, tmp_path / "one", pids=[fakeatlas.MIXED], tables=["modes"])
    assert only == {"modes": 3}
    with pytest.raises(ValueError):
        write_tables(mirrored, tmp_path / "x", tables=["nonsense"])
    with pytest.raises(ValueError):
        write_tables(mirrored, tmp_path / "x", fmt="xlsx")


def test_firing_rows_align_activations_on_the_firing_token(upstream):
    raw = json.loads(upstream.files[f"neuron_landscape_data/{fakeatlas.RIVER}.json"])
    row = next(firing_rows(fakeatlas.RIVER, raw))
    assert row["firing_token"] == " Danube" and row["act_0"] == 0.5 and row["act_m1"] == 0.1
    assert row["act_m2"] is None and row["act_p1"] is None and row["act_p12"] is None
    assert row["text_before"] == " one two three" and row["tested"] is True and row["subcircuit"] == "c0"
    assert sum(1 for k in row if k.startswith("act_")) == 29


def test_parquet_tables(mirrored, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    written = write_tables(mirrored, tmp_path / "p", fmt="parquet", tables=["firings", "modes"])
    table = pq.read_table(tmp_path / "p" / "firings.parquet")
    assert table.num_rows == written["firings"] == 48 and "act_0" in table.column_names


def test_semantic_map(atlas, tmp_path):
    data = semantic_map(atlas, None, unit="pole", method="pca", clusters=2)
    assert len(data["points"]) == 8 and len(data["clusters"]) <= 2
    assert all(-5 < p["x"] < 5 and -5 < p["y"] < 5 for p in data["points"])
    assert any("no description" in n for n in data["notes"])  # the dark pole has nothing to place
    region = semantic_map(atlas, [fakeatlas.MIXED], unit="mode")
    assert [p["mode_index"] for p in region["points"]] == [0, 1, 2]
    assert semantic_map(atlas, [], unit="mode")["points"] == []
    with pytest.raises(ValueError):
        semantic_map(atlas, None, unit="galaxy")
    write_map_html(data, tmp_path / "map.html", title="Test <map>")
    html = (tmp_path / "map.html").read_text(encoding="utf-8")
    assert "Test &lt;map&gt;" in html and '"points"' in html and "</script>" in html


def test_projection_edge_cases():
    import numpy as np

    xy, notes = project(np.zeros((0, 4), dtype=np.float32))
    assert xy.shape == (0, 2)
    xy, _ = project(np.ones((2, 4), dtype=np.float32))
    assert xy.shape == (2, 2)
    big = np.random.RandomState(0).randn(30, 6).astype(np.float32)
    xy, _ = project(big, "pca")
    assert xy.shape == (30, 2) and abs(float(xy.mean())) < 1e-3
    with pytest.raises(ValueError):
        project(big, "magic")
