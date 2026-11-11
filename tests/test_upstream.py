import threading
import time

import pytest

from neuron_atlas.config import KNOWN_RELEASE_URL, Settings, release_id
from neuron_atlas.upstream import (
    ABSENT,
    PRESENT,
    UNKNOWN,
    AtlasSource,
    NotMirrored,
    RateLimiter,
    Store,
    UpstreamError,
    check_relpath,
    pole_path,
)

from . import fakeatlas


def test_files_are_fetched_once_and_then_served_from_the_mirror(source, upstream):
    first = source.get_index("atlas")
    again = source.get_index("atlas")
    assert first == again and len(first["neurons"]) == 5
    assert upstream.fetched("public_atlas_data/index.json") == 1
    assert source.status("public_atlas_data/index.json") == PRESENT
    info = source.store.info("public_atlas_data/index.json")
    assert info.raw_bytes == len(upstream.files["public_atlas_data/index.json"]) and len(info.sha256) == 64


def test_a_second_process_reads_the_mirror_without_network(source, settings, upstream):
    source.get_pole_file("subcircuit", fakeatlas.RIVER)
    before = len(upstream.requests)
    with AtlasSource(settings.with_overrides(offline=True), client=upstream.client()) as other:
        assert other.get_pole_file("subcircuit", fakeatlas.RIVER)["pid"] == fakeatlas.RIVER
    assert len(upstream.requests) == before


def test_missing_files_are_remembered_as_absent(source, upstream):
    path = pole_path("landscape", fakeatlas.ISLAND_ONLY)
    assert source.status(path) == UNKNOWN
    assert source.get_json(path) is None
    assert source.get_json(path) is None
    assert source.status(path) == ABSENT
    assert upstream.fetched(path) == 1


def test_a_403_that_is_not_s3_is_an_error_not_an_absence(settings):
    import httpx

    def handler(request):
        return httpx.Response(403, text="blocked by a proxy")

    with AtlasSource(settings, client=httpx.Client(transport=httpx.MockTransport(handler))) as src:
        with pytest.raises(UpstreamError):
            src.get_json("public_atlas_data/index.json")
        assert src.status("public_atlas_data/index.json") == UNKNOWN


def test_offline_refuses_to_fetch(settings, upstream):
    src = AtlasSource(settings.with_overrides(offline=True), client=upstream.client())
    with src, pytest.raises(NotMirrored):
        src.get_index("atlas")
    assert upstream.requests == []


def test_transient_errors_are_retried(source, upstream, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    upstream.fail_next = [503, 502]
    assert source.get_index("atlas")["neurons"]
    assert upstream.fetched("public_atlas_data/index.json") == 3


def test_giving_up_after_too_many_errors(source, upstream, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    upstream.fail_next = [503] * 10
    with pytest.raises(UpstreamError):
        source.get_index("atlas")


def test_fetch_many_skips_what_is_there_and_reports_each_file(source, upstream):
    paths = [pole_path("subcircuit", fakeatlas.RIVER), pole_path("subcircuit", fakeatlas.DARK), "public_atlas_data/index.json"]
    results = {r.relpath: r for r in source.fetch_many(paths)}
    assert results[paths[0]].status == PRESENT and results[paths[0]].fetched
    assert results[paths[1]].status == ABSENT
    again = source.fetch_many(paths)
    assert all(not r.fetched for r in again)
    assert upstream.fetched(paths[0]) == 1


def test_concurrent_readers_trigger_a_single_download(source, upstream):
    path = pole_path("landscape", fakeatlas.RIVER)
    threads = [threading.Thread(target=source.get_json, args=(path,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert upstream.fetched(path) == 1


@pytest.mark.parametrize("bad", ["../secret.json", "/etc/passwd", "a//b.json", "index.html", "x.json?y=1", ""])
def test_paths_outside_the_release_are_rejected(bad):
    with pytest.raises(ValueError):
        check_relpath(bad)


def test_release_discovery_reads_the_explorer_page(tmp_path, upstream):
    settings = Settings(data_dir=tmp_path, requests_per_second=0)
    with AtlasSource(settings, client=upstream.client()) as src:
        assert src.release_url == fakeatlas.RELEASE_URL
        assert src.release_id == "test-release"
    # The answer is remembered, so a second start does not ask again.
    with AtlasSource(settings, client=upstream.client()) as src:
        assert src.release_url == fakeatlas.RELEASE_URL
    assert upstream.fetched("public_neuron_atlas") == 1


def test_discovery_refuses_foreign_hosts_and_falls_back(tmp_path):
    import httpx

    def handler(request):
        return httpx.Response(200, text='<iframe data-explorer="https://evil.example/x/atlas.html"></iframe>')

    settings = Settings(data_dir=tmp_path, requests_per_second=0)
    with AtlasSource(settings, client=httpx.Client(transport=httpx.MockTransport(handler))) as src:
        with pytest.raises(UpstreamError):
            src.discover_release()
        assert src.release_url == KNOWN_RELEASE_URL


def test_release_id_is_a_safe_directory_name():
    assert release_id("https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/") == "2026-11-09-082204"
    assert "/" not in release_id("https://x/../..%2f/")


def test_store_survives_a_deleted_file(tmp_path):
    store = Store(tmp_path)
    store.write("a/b.json", b"{}")
    assert store.status("a/b.json") == PRESENT
    (tmp_path / "files" / "a" / "b.json.gz").unlink()
    assert store.status("a/b.json") == UNKNOWN
    store.close()


def test_rate_limiter_spaces_out_calls():
    limiter = RateLimiter(rate=50, burst=1)
    started = time.monotonic()
    for _ in range(6):
        limiter.acquire()
    assert time.monotonic() - started >= 0.08
