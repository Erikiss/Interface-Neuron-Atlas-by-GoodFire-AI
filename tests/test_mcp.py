import json

import pytest
from starlette.testclient import TestClient

from neuron_atlas.api import create_app

from . import fakeatlas

pytest.importorskip("mcp")

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Host": "localhost:8000",
}


def rpc(client, method, params=None, headers=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return client.post("/mcp", headers=headers or HEADERS, json=body)


def call(client, name, **arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    assert not result.get("isError"), result
    text = result["content"][0]["text"]
    try:
        return json.loads(text)
    except ValueError:
        return text


@pytest.fixture
def client(atlas):
    with TestClient(create_app(atlas)) as c:
        yield c


def test_tools_are_listed_with_descriptions(client):
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {
        "search_neurons", "batch_search", "get_pole", "get_poles", "get_neuron", "find_similar",
        "atlas_info", "glossary", "plan_region",
    }  # fmt: skip
    assert all(len(t["description"]) > 40 for t in tools.values())
    assert "query" in tools["search_neurons"]["inputSchema"]["properties"]
    init = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    ).json()
    assert "Neuron Atlas" in init["result"]["instructions"]


def test_search_and_read(client):
    found = call(client, "search_neurons", query="braille", limit=5)
    assert found["total"] == 1 and found["hits"][0]["id"] == "L39_E4_N1__neg"
    assert found["hits"][0]["matched"][0]["mode"] == 1
    modes = call(client, "search_neurons", query="chess", group_by="mode")
    assert modes["modes"][0]["pole"] == fakeatlas.MIXED
    page = call(client, "get_pole", id="+ L4/E1/N10", include="modes,examples", max_examples=1)
    assert isinstance(page, str) and "L4/E1/N10" in page and "Danube" in page
    clipped = call(
        client,
        "get_pole",
        id=fakeatlas.RIVER,
        include="modes,examples,subcircuits,overview",
        max_examples=3,
        max_chars=400,
    )
    assert len(clipped) < 600 and "cut at 400 characters" in clipped
    both = call(client, "get_poles", ids=["L4_E1_N10", "zzz"])
    assert "L4/E1/N10" in both and "Not found: zzz" in both
    assert "L4/E1/N10" in call(client, "get_neuron", id="L4_E1_N10", include_circuit=True)


def test_clipping_never_leaves_a_code_fence_open():
    from neuron_atlas.mcp_server import _clip

    doc = "# Title\n\nintro\n\n```text\n" + "passage line\n" * 40 + "```\n\nafter\n"
    assert _clip(doc, 0) == doc and _clip(doc, 10_000) == doc
    for limit in range(30, len(doc), 37):
        clipped = _clip(doc, limit)
        body = clipped.rsplit("\n\n[… cut at", 1)[0]
        fences = [line for line in body.split("\n") if line.startswith("```")]
        assert len(fences) % 2 == 0, (limit, clipped[-80:])
        assert clipped.endswith("raise max_chars]")
    longer = "````\ncode with ``` inside\n" + "x\n" * 50
    assert _clip(longer, 60).split("\n\n[…")[0].endswith("````")


def test_batch_similar_info_glossary(client):
    batch = call(client, "batch_search", queries=["braille", "chess"], limit=2)
    assert [r["total"] for r in batch["results"]] == [1, 1]
    sim = call(client, "find_similar", text="names of rivers", top=3)
    assert sim["items"] and sim["method"].startswith("lsa")
    assert call(client, "atlas_info")["atlas"]["poles"] == 9
    assert "mode" in call(client, "glossary")["terms"]


def test_errors_reach_the_model_as_tool_errors(client):
    result = rpc(client, "tools/call", {"name": "get_pole", "arguments": {"id": "L77_E1_N1__pos"}}).json()[
        "result"
    ]
    assert result["isError"] is True


def test_plan_region_reports_and_downloads_within_limits(client, atlas, settings):
    plan = call(client, "plan_region", query="layer:4")
    assert plan["estimate"]["poles"] == 2 and "download" not in plan
    started = call(client, "plan_region", ids=["L4_E1_N10"], download=True)
    assert started["download"]["state"] in {"queued", "running", "done"}
    atlas.settings = settings.with_overrides(max_mirror_poles=1)
    with TestClient(create_app(atlas)) as limited:
        refused = call(limited, "plan_region", query="layer:4", download=True)
        assert refused["download"].startswith("refused")


def test_the_endpoint_answers_only_to_known_hosts_and_keys(atlas, settings):
    with TestClient(create_app(atlas)) as c:
        assert rpc(c, "tools/list", headers={**HEADERS, "Host": "evil.example"}).status_code == 421
    atlas.settings = settings.with_overrides(allowed_hosts=("atlas.example.org",), api_keys=("k",))
    with TestClient(create_app(atlas)) as c:
        ok = {**HEADERS, "Host": "atlas.example.org", "X-API-Key": "k"}
        assert rpc(c, "tools/list", headers=ok).status_code == 200
        assert rpc(c, "tools/list", headers={**ok, "X-API-Key": "nope"}).status_code == 401
    atlas.settings = settings.with_overrides(allowed_hosts=("*",))
    with TestClient(create_app(atlas)) as c:
        assert rpc(c, "tools/list", headers={**HEADERS, "Host": "anything.example"}).status_code == 200


def tool_error(client, name, **arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def test_post_to_mcp_is_served_in_place(client):
    r = client.post(
        "/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and r.json()["result"]["tools"]


def test_mistakes_come_back_as_readable_tool_errors(client):
    assert "no such neuron or pole" in tool_error(client, "get_pole", id="L77_E1_N1__pos")
    bad_match = tool_error(client, "search_neurons", query="river", match="telepathy")
    assert "invalid argument" in bad_match and "match" in bad_match
    assert "layer" in tool_error(client, "search_neurons", query="layer:abc")
    assert "at most" in tool_error(client, "batch_search", queries=["x"] * 60)


def test_answers_stay_within_max_chars(client):
    full = call(client, "search_neurons", query="layer:4..39", limit=10)
    small = call(client, "search_neurons", query="layer:4..39", limit=10, max_chars=700)
    assert len(json.dumps(small)) <= 900 and small["notes"]
    shown = len(small["hits"])
    if shown < len(full["hits"]):
        assert small["next_offset"] == shown
    assert (
        call(client, "search_neurons", query="layer:4..39", limit=10, max_chars=0) == full
    )  # 0 = default budget
    batch = call(client, "batch_search", queries=["river", "chess", "names"], max_chars=600)
    assert batch["notes"] and len(batch["results"]) == 3


def test_long_pole_documents_lose_examples_before_they_are_cut(client):
    whole = call(
        client, "get_pole", id=fakeatlas.RIVER, include="modes,examples", max_examples=3, max_chars=100_000
    )
    budget = len(whole) - 10
    fitted = call(
        client, "get_pole", id=fakeatlas.RIVER, include="modes,examples", max_examples=3, max_chars=budget
    )
    assert len(fitted) <= budget and "shortened to fit" in fitted and "cut at" not in fitted
    assert "Danube" in fitted  # the modes themselves are kept


def test_get_poles_names_the_ids_it_did_not_read(client):
    ids = [fakeatlas.RIVER] * 26 + ["L9_E2_N20__pos"]
    text = call(client, "get_poles", ids=ids, max_chars=200_000)
    assert "Not read" in text and "L9_E2_N20__pos" in text
