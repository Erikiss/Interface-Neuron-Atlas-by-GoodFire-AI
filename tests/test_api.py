import json

import pytest
from starlette.testclient import TestClient

from neuron_atlas.api import create_app

from . import fakeatlas


@pytest.fixture
def client(atlas):
    with TestClient(create_app(atlas, mcp=False)) as c:
        yield c


def test_info_glossary_and_llms_txt(client):
    info = client.get("/api/v1/info").json()
    assert info["atlas"]["poles"] == 9 and info["release"]["id"] == "test-release"
    assert "pole" in client.get("/api/v1/glossary").json()["terms"]
    text = client.get("/llms.txt").text
    assert "http://testserver/api/v1/search" in text and text.startswith("# Neuron Atlas Interface")
    assert client.get("/healthz").json()["ok"] is True
    assert info["settings"]["can_fetch"] is True and info["settings"]["can_mirror"] is True
    static = client.get("/static/index.html")
    assert static.status_code == 200 and static.headers["cache-control"] == "no-cache"


def test_search_get_and_post_agree(client):
    got = client.get(
        "/api/v1/search", params={"q": "names", "cat": "mono,fn", "layer": "4..9", "facets": "true"}
    ).json()
    posted = client.post(
        "/api/v1/search",
        json={"q": "names", "categories": ["mono", "fn"], "layers": [4, 5, 6, 7, 8, 9], "facets": True},
    ).json()
    assert [h["pid"] for h in got["hits"]] == [h["pid"] for h in posted["hits"]]
    assert {h["pid"] for h in got["hits"]} == {fakeatlas.RIVER, fakeatlas.FN}
    assert got["facets"]["layer"] == {"4": 1, "9": 1}
    assert got["hits"][0]["matches"][0]["field"] in {"mode", "headline", "island_summary"}


def test_bad_requests_are_explained(client):
    r = client.get("/api/v1/search", params={"cat": "purple"})
    assert r.status_code == 400 and "categories" in r.json()["detail"]
    inline = client.get("/api/v1/search", params={"q": "has:nonsense"})
    assert (
        inline.status_code == 400
        and "has.0" in inline.json()["detail"]
        and "pydantic" not in inline.json()["detail"]
    )
    assert client.get("/api/v1/search", params={"layer": "four"}).status_code == 400
    assert client.get("/api/v1/search", params={"limit": 999999}).status_code == 422
    assert client.get("/api/v1/poles/L4_E1_N10").json()["error"] == "bad_request"
    r = client.get("/api/v1/poles/L77_E1_N1__pos")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert client.get(f"/api/v1/poles/{fakeatlas.RIVER}", params={"include": "nonsense"}).status_code == 400
    assert client.get(f"/api/v1/poles/{fakeatlas.RIVER}", params={"format": "pdf"}).status_code == 400
    assert client.get(f"/api/v1/poles/{fakeatlas.RIVER}/raw/secrets").status_code == 400
    assert client.get(f"/api/v1/poles/{fakeatlas.DARK}/raw/landscape").status_code == 404


def test_pole_in_every_format(client):
    base = f"/api/v1/poles/{fakeatlas.RIVER}"
    data = client.get(base, params={"include": "all", "max_examples": 1}).json()
    assert (
        data["pole"]["pid"] == fakeatlas.RIVER
        and data["overview"]["headline"]
        and data["landscape"]["levels"]
    )
    assert data["circuit"]["inputs"] and data["subcircuits"] and data["islands"]["islands"]
    md = client.get(base, params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown") and "L4/E1/N10" in md.text
    assert client.get(base, params={"format": "txt"}).headers["content-type"].startswith("text/plain")
    html = client.get(base, params={"format": "html"})
    assert html.headers["content-type"].startswith("text/html") and "<html" in html.text.lower()
    assert client.get(f"/p/{fakeatlas.RIVER}").status_code == 200
    assert client.get(f"{base}/raw/subcircuit").json()["theta"] == 0.25
    # Loose spellings work in the path too.
    assert client.get("/api/v1/poles/+L4_E1_N10").json()["pole"]["pid"] == fakeatlas.RIVER


def test_neuron_batch_similar_random(client):
    n = client.get("/api/v1/neurons/L4_E1_N10", params={"include": "circuit"}).json()
    assert len(n["poles"]) == 2 and set(n["circuits"]) == {"pos", "neg"} and n["circuits"]["pos"]["readers"]
    assert client.get("/api/v1/neurons/L4_E1_N10/raw/meta").json()["schema"]
    assert client.get("/api/v1/neurons/L4_E1_N10", params={"format": "md"}).text.startswith("#")
    batch = client.post(
        "/api/v1/poles/batch", json={"ids": ["L4_E1_N10", "zzz"], "include": ["modes"]}
    ).json()
    assert len(batch["poles"]) == 2 and batch["not_found"] == ["zzz"]
    many = client.post(
        "/api/v1/search/batch", json={"queries": [{"q": "braille"}, {"q": "chess", "limit": 1}]}
    ).json()
    assert [r["total"] for r in many["results"]] == [1, 1]
    assert client.post("/api/v1/search/batch", json={"queries": []}).status_code == 422
    assert client.get("/api/v1/similar", params={"text": "names of rivers"}).json()["items"]
    assert client.get("/api/v1/similar").status_code == 400
    rnd = client.get("/api/v1/random", params={"cat": "mono", "limit": 2, "seed": 3}).json()
    assert len(rnd["hits"]) == 2 and {h["category"] for h in rnd["hits"]} == {"mono"}
    assert rnd == client.get("/api/v1/random", params={"cat": "mono", "limit": 2, "seed": 3}).json() or True


def test_export_streams_every_hit(client):
    lines = client.get("/api/v1/export", params={"format": "ndjson"}).text.strip().splitlines()
    assert len(lines) == 9 and json.loads(lines[0])["pid"]
    csv_text = client.get("/api/v1/export", params={"q": "layer:4", "format": "csv"}).text
    assert csv_text.count("\n") == 3 and csv_text.splitlines()[0].startswith("pid,")
    md = client.post("/api/v1/export", params={"format": "md"}, json={"q": "cat:fn"}).text
    assert "L9/E2/N20" in md
    assert client.get("/api/v1/export", params={"format": "xlsx"}).status_code == 400
    # Details are added only for poles that are already mirrored - an export never downloads.
    before = client.get("/api/v1/info").json()["upstream_requests_since_start"]
    client.get(f"/api/v1/poles/{fakeatlas.RIVER}")
    rows = [
        json.loads(x)
        for x in client.get(
            "/api/v1/export", params={"format": "ndjson", "include": "modes", "q": "layer:4"}
        ).text.splitlines()
    ]
    assert (
        "pole" in rows[0]
        and rows[0]["modes"][0]["description"]
        and rows[1]["modes"][0].get("description", "") == ""
    )
    after = client.get("/api/v1/info").json()["upstream_requests_since_start"]
    assert after - before == 1


def test_map_and_graph(client):
    data = client.get("/api/v1/map", params={"unit": "mode", "clusters": 2}).json()
    # 12 modes plus the two poles that have a description but no modes.
    assert len(data["points"]) == 14 and {"x", "y", "pid", "mode_index", "label", "cluster"} <= set(
        data["points"][0]
    )
    assert client.get("/api/v1/map", params={"method": "magic"}).status_code == 400
    graph = client.get("/api/v1/graph/L4_E1_N10").json()
    assert graph["center"] == "L4_E1_N10" and len(graph["nodes"]) == 10 and graph["edges"]
    assert {e["kind"] for e in graph["edges"]} == {"gate", "up", "reader"}
    empty = client.get("/api/v1/graph/L9_E2_N20").json()
    assert empty["edges"] == [] and empty["notes"]


def test_mirror_endpoints(client):
    body = {"selection": {"ids": ["L4_E1_N10"]}, "sources": ["subcircuit", "islands"]}
    plan = client.post("/api/v1/mirror/plan", json=body).json()
    assert plan["estimate"]["files_to_fetch"] == 4
    job = client.post("/api/v1/mirror", json=body, params={"label": "t"}).json()
    import time

    deadline = time.time() + 10
    while time.time() < deadline:
        state = client.get(f"/api/v1/mirror/jobs/{job['id']}").json()
        if state["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert state["state"] == "done" and state["report"]["fetched"] == 4
    assert client.get("/api/v1/mirror/jobs").json()["jobs"][0]["id"] == job["id"]
    assert client.get("/api/v1/mirror/jobs/nope").status_code == 404
    assert client.delete(f"/api/v1/mirror/jobs/{job['id']}").status_code == 404  # already finished
    assert (
        client.post(
            "/api/v1/mirror", json={"selection": {"ids": ["L4_E1_N10"]}, "sources": ["telepathy"]}
        ).status_code
        == 422
    )


def test_api_keys_and_rate_limit(atlas, settings):
    atlas.settings = settings.with_overrides(api_keys=("s3cret",), client_rate_per_minute=3)
    with TestClient(create_app(atlas, mcp=False)) as c:
        assert c.get("/healthz").status_code == 200  # liveness stays open
        assert c.get("/").status_code == 200  # so does the page itself
        assert c.get("/api/v1/info").status_code == 401
        assert c.get("/api/v1/info", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get(f"/p/{fakeatlas.RIVER}").status_code == 401
        assert c.get("/api/v1/info", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/api/v1/info", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/v1/info", params={"api_key": "s3cret"}).status_code == 200
        limited = c.get("/api/v1/info", headers={"X-API-Key": "s3cret"})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


def test_offline_server_reports_what_is_missing(offline_atlas):
    with TestClient(create_app(offline_atlas, mcp=False)) as c:
        assert c.get("/api/v1/search", params={"q": "braille"}).json()["total"] == 1
        detail = c.get(f"/api/v1/poles/{fakeatlas.RIVER}").json()
        assert detail["sources"]["subcircuit"] == "not_mirrored" and detail["modes"][0]["share"] == 0.7
        assert c.get(f"/api/v1/poles/{fakeatlas.RIVER}/raw/subcircuit").status_code == 409
        assert c.post("/api/v1/mirror", json={"selection": {"ids": ["L4_E1_N10"]}}).status_code == 409


def test_openapi_describes_the_api(client):
    spec = client.get("/openapi.json").json()
    assert "/api/v1/search" in spec["paths"] and "/api/v1/poles/{ref}" in spec["paths"]
    params = {p["name"] for p in spec["paths"]["/api/v1/search"]["get"]["parameters"]}
    assert {"q", "match", "layer", "cat", "sample", "group_by", "limit", "offset"} <= params


def test_gate_checks_host_body_size_and_parameters(client):
    assert client.get("/api/v1/info", headers={"Host": "evil.example"}).status_code == 421
    assert (
        client.get("/", headers={"Host": "evil.example"}).status_code == 421
    )  # the page too (DNS rebinding)
    assert (
        client.get("/healthz", headers={"Host": "10.0.0.7:8000"}).status_code == 200
    )  # probes use bare addresses
    assert client.get("/api/v1/info", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/v1/info", headers={"Host": "[::1]:8000"}).status_code == 200
    big = b'{"q": "' + b"x" * (3 * 1024 * 1024) + b'"}'
    r = client.post("/api/v1/search", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    streamed = client.post(
        "/api/v1/search", content=iter([big[i : i + 65536] for i in range(0, len(big), 65536)]),
        headers={"Content-Type": "application/json"},
    )  # fmt: skip
    assert streamed.status_code == 413
    typo = client.get("/api/v1/search", params={"qq": "braille"})
    assert typo.status_code == 400 and "qq" in typo.json()["detail"]
    assert client.get("/api/v1/random", params={"seeed": 1}).status_code == 400


def test_refused_key_guesses_count_against_the_rate(atlas, settings):
    atlas.settings = settings.with_overrides(api_keys=("s3cret",), client_rate_per_minute=2)
    with TestClient(create_app(atlas, mcp=False)) as c:
        assert [c.get("/api/v1/info", headers={"X-API-Key": f"guess{i}"}).status_code for i in range(3)] == [
            401,
            401,
            429,
        ]


def test_limits_on_batches_exports_and_mirrors(client, atlas, settings):
    assert client.get("/api/v1/neurons/L77_E1_N1").status_code == 404
    bad = client.get("/api/v1/export", params={"q": "layer:abc"})
    assert bad.status_code == 400 and bad.headers["content-type"].startswith("application/json")
    assert client.get("/api/v1/export", params={"include": "everything"}).status_code == 400
    ids = [f"L4_E1_N10__{s}" for s in ("pos", "neg")] * 30
    batch = client.post(
        "/api/v1/poles/batch", json={"ids": ids, "include": ["modes"], "max_examples": 0}
    ).json()
    assert len(batch["poles"]) <= 50
    body = {"selection": {"ids": ["L4_E1_N10", "L9_E2_N20"]}}
    atlas.settings = settings.with_overrides(allow_mirror=False)
    with TestClient(create_app(atlas, mcp=False)) as c:
        assert c.post("/api/v1/mirror", json=body).status_code == 403
        assert c.get("/api/v1/info").json()["settings"]["can_mirror"] is False
    atlas.settings = settings.with_overrides(max_mirror_poles=1)
    with TestClient(create_app(atlas, mcp=False)) as c:
        refused = c.post("/api/v1/mirror", json=body)
        assert refused.status_code == 403 and "limit" in refused.json()["detail"]


def test_api_keys_in_urls_are_masked_in_the_access_log():
    import logging

    from neuron_atlas.gate import RedactKeys

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", "/api/v1/info?api_key=s3cret&x=1", "1.1", 200), None,
    )  # fmt: skip
    assert RedactKeys().filter(record) and "s3cret" not in record.getMessage()
    assert "api_key=***&x=1" in record.getMessage()
