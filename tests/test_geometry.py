import collections
import csv
import json
import re
import xml.dom.minidom

import pytest

from neuron_atlas.geometry import tables
from neuron_atlas.geometry.graph import (
    circuit_edges,
    circuit_group_rows,
    clean_group_description,
    clean_group_name,
    group_side,
    hub_edges,
    neighbourhood,
    neuron_links,
    pole_label,
    shared_input_similarity,
    write_graph,
)
from neuron_atlas.geometry.maps import project, semantic_map, write_map_html
from neuron_atlas.geometry.tables import COLUMNS, TABLES, firing_rows, write_tables
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


def test_subcircuit_fallback_signs_follow_the_circuit_convention(atlas):
    atlas.pole(fakeatlas.RIVER)  # the subcircuit files of both poles, no circuit file
    atlas.pole(fakeatlas.MIXED)
    atlas.source.settings = atlas.source.settings.with_overrides(offline=True)
    edges, origin = hub_edges(atlas, "L4_E1_N10")
    assert origin == "subcircuit"
    lanes = {(e["source"], e["target_pole"]): (e["role"], e["gate"], e["up"]) for e in edges if e["kind"] == "input"}
    # Gate drivers open the gate on either pole; up drivers push up towards the pole's sign.
    assert lanes[("L2_E7_N100", "pos")] == lanes[("L2_E7_N100", "neg")] == ("drives", 0.03, None)
    assert lanes[("L1_E7_N100", "pos")] == lanes[("L1_E7_N100", "neg")] == ("opposes", -0.01, None)
    assert lanes[("L3_E7_N100", "pos")] == ("drives", None, 0.05)
    assert lanes[("L3_E7_N100", "neg")] == ("drives", None, -0.05)  # a negative pole fires when up goes down
    assert lanes[("L0_E7_N100", "neg")] == ("opposes", None, 0.008)
    # A reader's signed value is the push on the reader's own activation: it names the pole that is raised.
    readers = {(e["target"], e["source_pole"]): (e["target_pole"], e["effect"], e["weight"]) for e in edges if e["kind"] == "reader"}
    assert readers[("L9_E7_N100", "neg")] == ("pos", "raised", 0.02)
    assert readers[("L9_E8_N101", "neg")] == ("neg", "raised", 0.01)

    shown = neighbourhood(atlas, "L4_E1_N10")["edges"]
    up = {(e["source"], e["target_pole"]): e["sign"] for e in shown if e["kind"] == "up"}
    assert (up[("L3_E7_N100", "pos")], up[("L3_E7_N100", "neg")]) == ("+", "-")


def _hub_meta(did, inputs, readers):
    """A minimal circuit file: ``inputs`` / ``readers`` are (neuron, pole, value, label) tuples."""

    def member(unit, pole, value, label, reader):
        out = {"did": unit, "pole": pole, "layer": int(unit.split("_")[0][1:]), "label_full": label}
        return {**out, "w": value, "hop": 1} if reader else {**out, "gate_s": value, "up_s": value}

    return {
        "identity": {"did": did, "layer": int(did.split("_")[0][1:])},
        "groups": {
            "input": [{"id": "g1", "name": "Inputs", "bucket": "+|drives", "members": [member(*m, False) for m in inputs]}],
            "readers": [{"id": "r1", "name": "Readers", "bucket": "+|raised", "members": [member(*m, True) for m in readers]}],
        },
    }


def test_neighbourhood_ranks_edges_within_their_kind(atlas, upstream):
    # Reader weights (static couplings) are a hundred times the input contributions here.
    inputs = [(f"L3_E{i}_N{i}", "+", 0.001 * (10 - i), "") for i in range(10)]
    readers = [(f"L5_E{i}_N{i}", "+", 0.1 * (10 - i), "") for i in range(10)]
    upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"] = json.dumps(_hub_meta("L4_E1_N10", inputs, readers)).encode()
    graph = neighbourhood(atlas, "L4_E1_N10", max_nodes=7)
    assert len(graph["nodes"]) == 7 and "within their kind" in graph["notes"][0]
    assert collections.Counter(e["kind"] for e in graph["edges"]) == {"reader": 3, "gate": 3, "up": 3}


def test_node_labels_describe_each_pole(atlas, upstream, tmp_path):
    inputs = [("L3_E1_N1", "+", 0.02, "Fires on plus things"), ("L3_E1_N1", "-", 0.01, "Fires on minus things"),
              ("L3_E2_N2", "-", 0.01, "Only the negative pole")]  # fmt: skip
    upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"] = json.dumps(_hub_meta("L4_E1_N10", inputs, [])).encode()
    nodes = {n["id"]: n["label"] for n in neighbourhood(atlas, "L4_E1_N10")["nodes"]}
    assert nodes["L3_E1_N1"] == "+: Fires on plus things / −: Fires on minus things"
    assert nodes["L3_E2_N2"] == "−: Only the negative pole"
    assert nodes["L4_E1_N10"].startswith("+: ") and " / −: " in nodes["L4_E1_N10"]  # an atlas neuron: both poles' texts
    write_graph(atlas, tmp_path / "g.json", dids=["L4_E1_N10"])
    written = {n["id"]: n["label"] for n in json.loads((tmp_path / "g.json").read_text())["nodes"]}
    assert written["L3_E1_N1"] == nodes["L3_E1_N1"]


def test_pole_label():
    assert pole_label({}) == "" and pole_label({None: "unit text"}) == "unit text"
    assert pole_label({"pos": "a", "neg": "b"}) == "+: a / −: b" and pole_label({"neg": "b"}) == "−: b"
    assert pole_label({"pos": "same", "neg": "same"}) == "same"
    assert pole_label({"pos": "x" * 400, "neg": "y"}, limit=300) == "+: " + "x" * 150 + " … / −: y"


def test_circuit_groups_keep_what_the_explorer_shows_on_the_other_pole(upstream):
    meta = json.loads(upstream.files["moe_circuit_explorer_data/L4_E1_N10/meta.json"])
    rows = {r["group"]: r for r in circuit_group_rows("L4_E1_N10", meta)}
    r2 = rows["r2"]  # belongs to the negative pole; the explorer lists it on the positive pole as well
    assert (r2["pole"], r2["examples"], r2["mean_effect"]) == ("neg", 60, -0.02)
    assert (r2["examples_other_pole"], r2["mean_effect_other_pole"]) == (5, 0.004)
    assert rows["r1"]["examples_other_pole"] is None and rows["g1"]["mean_effect_other_pole"] is None


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


def test_tables_cover_the_mirrored_files(mirrored, tmp_path):
    written = write_tables(mirrored, tmp_path / "t")
    assert set(written) == set(TABLES)
    assert written["poles"] == 9 and written["modes"] == 6 and written["subcircuits"] == 7
    assert written["subcircuit_links"] == 7 * 13 and written["examples"] == 7 * 12
    assert written["landscape_levels"] == 10 and written["firings"] == 48 and written["circuit_edges"] == 9
    # Five islands files with two islands each; the dark pole has only the unassigned island mode.
    assert (written["islands"], written["island_modes"], written["island_examples"]) == (10, 9, 25)
    assert (written["overview"], written["overview_situations"], written["overview_experiments"]) == (2, 5, 2)
    assert written["circuit_exemplars"] == 1
    for name in TABLES:  # one fixed header per table
        with open(tmp_path / "t" / f"{name}.csv", newline="", encoding="utf-8") as fh:
            assert next(csv.reader(fh)) == list(COLUMNS[name]), name

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
    # Only act_m1 and act_p1/act_p2 lie inside the document; the other empty cells are padding.
    assert (row["window_before"], row["window_after"]) == (1, 2)


def test_landscape_tables_number_modes_in_their_own_index_space(mirrored, tmp_path):
    write_tables(mirrored, tmp_path / "t", pids=[fakeatlas.RIVER])
    modes = read_csv(tmp_path / "t" / "modes.csv")
    landscape = read_csv(tmp_path / "t" / "landscape_modes.csv")
    assert [(r["mode_index"], r["kind"]) for r in modes] == [("0", "open"), ("1", "closed_gate"), ("2", "residue")]
    assert [(r["landscape_mode_index"], r["kind"], r["pole_mode_index"]) for r in landscape] == [
        ("0", "open", "0"), ("1", "closed_gate", ""), ("2", "unassigned", ""),
    ]  # fmt: skip
    assert modes[1]["label"] != landscape[1]["label"]  # index 1 is a different mode in the two numberings
    assert landscape[0]["label"] == modes[0]["label"]
    firing = read_csv(tmp_path / "t" / "firings.csv")[0]
    assert "landscape_mode_index" in firing and "mode_index" not in firing
    assert {"landscape_mode_a", "landscape_mode_b"} <= set(read_csv(tmp_path / "t" / "mode_similarity.csv")[0])
    assert "landscape_mode_index" in read_csv(tmp_path / "t" / "landscape_level_modes.csv")[0]


def test_island_tables_number_examples_like_the_api(mirrored, tmp_path):
    write_tables(mirrored, tmp_path / "t", pids=[fakeatlas.ISLAND_ONLY])
    islands = read_csv(tmp_path / "t" / "islands.csv")
    modes = read_csv(tmp_path / "t" / "island_modes.csv")
    assert [(r["island"], r["island_mode"], r["unassigned"], r["n"]) for r in islands] == [
        ("0", "0", "false", "40"), ("1", "1", "true", "12"),
    ]  # fmt: skip
    assert [(m["name"], m["islands"], m["unassigned"]) for m in modes] == [
        ("River names", "0", "false"), ("Unassigned examples", "1", "true"),
    ]  # fmt: skip
    assert float(modes[0]["share"]) == pytest.approx(0.742)
    examples = {r["ref"]: r for r in read_csv(tmp_path / "t" / "island_examples.csv")}
    strongest = mirrored.pole(fakeatlas.ISLAND_ONLY).islands.islands[0].examples[0]
    row = examples[strongest.ref]
    assert strongest.ref == "island0:1" and row["label"] == "top" and float(row["activation"]) == strongest.act
    assert row["firing_token"] == " Danube" and float(row["raw_0"]) == 2.5 and float(row["threshold"]) == pytest.approx(0.25)
    short = examples["island0:0"]  # " the calm Rhine flows", the peak on " Rhine"
    assert [short[c] for c in ("raw_m3", "raw_m2", "raw_m1", "raw_0", "raw_p1", "raw_p2")] == ["", "0.3", "0.0", "1.0", "0.3", ""]
    assert short["text"] == " the calm« Rhine» flows"


def test_overview_and_circuit_exemplar_tables(mirrored, tmp_path):
    write_tables(mirrored, tmp_path / "t", pids=[fakeatlas.RIVER, fakeatlas.MIXED])
    overview = {r["pid"]: r for r in read_csv(tmp_path / "t" / "overview.csv")}
    river = overview[fakeatlas.RIVER]
    assert river["headline"] == "River names, mostly in travel writing" and river["kl_recovered_own"] == "0.81"
    assert river["not_this"].splitlines() == ["City names: 'Vienna' stays near 0.", "Lakes reach at most 1×."]
    situations = read_csv(tmp_path / "t" / "overview_situations.csv")
    # A single-mode pole's situations are its subcircuits; a multi-mode pole's are its modes.
    assert [(r["refers_to"], r["mode_index"], r["subcircuit"]) for r in situations if r["pid"] == fakeatlas.RIVER] == [
        ("subcircuit", "", "c0"), ("subcircuit", "", "c1"),
    ]  # fmt: skip
    assert [r["mode_index"] for r in situations if r["pid"] == fakeatlas.MIXED] == ["0", "1", "2"]
    assert read_csv(tmp_path / "t" / "overview_experiments.csv")[0]["result"] == "Fell from 8× to 0.2×."
    exemplars = read_csv(tmp_path / "t" / "circuit_exemplars.csv")
    assert [(r["hub"], r["ex_id"], r["pole"], r["firing_token"], r["text"]) for r in exemplars] == [
        ("L4_E1_N10", "x1", "pos", " Danube", " the« Danube»"),
    ]  # fmt: skip
    assert float(exemplars[0]["strength"]) == pytest.approx(2.2 / 0.8)  # in multiples of the circuit data's θ


def test_parquet_tables_keep_their_column_types(mirrored, tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    written = write_tables(mirrored, tmp_path / "p", fmt="parquet")
    assert set(written) == set(TABLES)
    table = pq.read_table(tmp_path / "p" / "firings.parquet")
    assert table.num_rows == written["firings"] == 48 and "act_0" in table.column_names
    kinds = {"str": pa.string(), "int": pa.int64(), "float": pa.float64(), "bool": pa.bool_()}
    for name in written:
        schema = pq.read_schema(tmp_path / "p" / f"{name}.parquet")
        assert [(f.name, f.type) for f in schema] == [(c, kinds[t]) for c, t in COLUMNS[name].items()], name
    # A column that is empty in the first row group keeps its type; stray values are coerced to it.
    sink = tables._ParquetSink(tmp_path / "groups.parquet", COLUMNS["circuit_groups"], batch=1)
    sink.write({"hub": "L1_E1_N1", "examples": None, "mean_effect": None, "members": None})
    sink.write({"hub": "L1_E1_N1", "examples": 64, "mean_effect": -0.0039, "members": "12"})
    sink.close()
    groups = pq.read_table(tmp_path / "groups.parquet")
    assert groups.schema.field("examples").type == pa.int64() and groups.schema.field("mean_effect").type == pa.float64()
    assert groups.column("mean_effect").to_pylist() == [None, -0.0039] and groups.column("members").to_pylist() == [None, 12]


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


def test_map_viewer_keeps_labels_inside_its_script(tmp_path):
    labels = ["opens a comment <!-- here", "then a <script> tag", "</script><img src=x onerror=alert(1)>", "a & b > c" + chr(0x2028)]
    data = {
        "unit": "pole", "method": "pca", "embedding": "lsa-256", "notes": [],
        "points": [{"pid": f"L4_E1_N{i}__pos", "x": 0.1 * i, "y": 0.0, "layer": 4, "category": "mono", "pole": "pos",
                    "label": label, "cluster": 0} for i, label in enumerate(labels)],
        "clusters": [{"id": 0, "size": 4, "label": "<!-- caption", "x": 0.0, "y": 0.0}],
    }  # fmt: skip
    write_map_html(data, tmp_path / "map.html", title="__DATA__ <map>")
    page = (tmp_path / "map.html").read_text(encoding="utf-8")
    # Only the viewer's own script tags remain, so the browser runs the script.
    assert page.count("<script>") == 1 and page.count("</script>") == 1 and "<!--" not in page
    assert json.loads(re.search(r"const DATA = (.*);\n", page).group(1)) == data
    assert "<title>__DATA__ &lt;map&gt;</title>" in page  # placeholders inside values stay text


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
