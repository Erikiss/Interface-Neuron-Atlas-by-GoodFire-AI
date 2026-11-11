import json

import pytest
from starlette.testclient import TestClient

from neuron_atlas.api import create_app

from . import fakeatlas

pytest.importorskip("mcp")

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "localhost:8000"}


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
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    assert "Neuron Atlas" in init["result"]["instructions"]


def test_search_and_read(client):
    found = call(client, "search_neurons", query="braille", limit=5)
    assert found["total"] == 1 and found["hits"][0]["id"] == "L39_E4_N1__neg"
    assert found["hits"][0]["matched"][0]["mode"] == 1
    modes = call(client, "search_neurons", query="chess", group_by="mode")
    assert modes["modes"][0]["pole"] == fakeatlas.MIXED
    page = call(client, "get_pole", id="+ L4/E1/N10", include="modes,examples", max_examples=1)
    assert isinstance(page, str) and "L4/E1/N10" in page and "Danube" in page
    clipped = call(client, "get_pole", id=fakeatlas.RIVER, include="modes,examples,subcircuits,overview", max_examples=3, max_chars=400)
    assert len(clipped) < 600 and "cut at 400 characters" in clipped
    both = call(client, "get_poles", ids=["L4_E1_N10", "zzz"])
    assert "L4/E1/N10" in both and "Not found: zzz" in both
    assert "L4/E1/N10" in call(client, "get_neuron", id="L4_E1_N10", include_circuit=True)


def test_batch_similar_info_glossary(client):
    batch = call(client, "batch_search", queries=["braille", "chess"], limit=2)
    assert [r["total"] for r in batch["results"]] == [1, 1]
    sim = call(client, "find_similar", text="names of rivers", top=3)
    assert sim["items"] and sim["method"].startswith("lsa")
    assert call(client, "atlas_info")["atlas"]["poles"] == 9
    assert "mode" in call(client, "glossary")["terms"]


def test_errors_reach_the_model_as_tool_errors(client):
    result = rpc(client, "tools/call", {"name": "get_pole", "arguments": {"id": "L77_E1_N1__pos"}}).json()["result"]
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
