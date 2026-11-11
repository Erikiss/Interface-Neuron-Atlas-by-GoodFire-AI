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


@pytest.mark.parametrize(
    "bad", ["../secret.json", "/etc/passwd", "a//b.json", "index.html", "x.json?y=1", "", "x.json\n", "x\u0663.json"]
)
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
    assert release_id("https://x/..") == "release" and release_id("https://x/.") == "release"
    assert release_id("https://x/.hidden/") == "hidden" and release_id("https://x/r\u00e9l/") == "r_l"


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


# --------------------------------------------------------------------------- hardening


def _source(settings, handler):
    import httpx

    return AtlasSource(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_release_base_accepts_only_plausible_explorer_urls():
    from neuron_atlas.upstream import release_base

    good = "https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/explorer.html?v=2#top"
    assert release_base(good) == "https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/"
    for bad in [
        "https://static.goodfire.ai",
        "https://static.goodfire.ai/explorer.html",
        "https://static.goodfire.ai/neuron-atlas/../x.html",
        "https://static.goodfire.ai/a/./x.html",
        "https://static.goodfire.ai/a//x.html",
        "https://static.goodfire.ai/a/b/",
        "https://user@static.goodfire.ai/a/x.html",
        "https://static.goodfire.ai:8443/a/x.html",
        "https://static.goodfire.ai:99999/a/x.html",
        "http://static.goodfire.ai/a/x.html",
        "https://static.goodfire.ai.evil.example/a/x.html",
    ]:
        with pytest.raises(UpstreamError):
            release_base(bad)


@pytest.mark.parametrize("damage", ["truncate", "scramble"])
def test_a_damaged_stored_file_is_fetched_again(settings, upstream, damage):
    path = pole_path("subcircuit", fakeatlas.RIVER)
    with AtlasSource(settings, client=upstream.client()) as src:
        good = src.get_bytes(path)
        gz = src.store.files_dir / (path + ".gz")
        data = bytearray(gz.read_bytes())
        if damage == "truncate":
            gz.write_bytes(bytes(data[: len(data) // 2]))
        else:  # same size, broken content: only reading it can tell
            data[20:60] = bytes(40)
            gz.write_bytes(bytes(data))
        assert src.get_bytes(path) == good
        assert upstream.fetched(path) == 2 and src.status(path) == PRESENT
    with AtlasSource(settings.with_overrides(offline=True), client=upstream.client()) as src:
        gz.write_bytes(b"\x1f\x8b broken")
        with pytest.raises(NotMirrored):  # offline: a clear "not mirrored", never an EOFError
            src.get_bytes(path)


def test_the_manifest_is_checked_against_the_files_and_rebuilt_when_damaged(tmp_path):
    import gzip

    store = Store(tmp_path)
    store.write("a/b.json", b'{"x": 1}')
    store.write("a/c.json", b"[1, 2]")
    (tmp_path / "files" / "a" / "c.json.gz").unlink()
    assert store.statuses(["a/b.json", "a/c.json"]) == {"a/b.json": PRESENT, "a/c.json": UNKNOWN}
    store.close()

    # A lost manifest: files on disk are checked and adopted, not fetched again.
    for name in ("manifest.sqlite", "manifest.sqlite-wal", "manifest.sqlite-shm"):
        (tmp_path / name).unlink(missing_ok=True)
    (tmp_path / "files" / "a" / "portal.json.gz").write_bytes(gzip.compress(b"<html>log in</html>"))
    store = Store(tmp_path)
    assert store.statuses(["a/b.json", "a/portal.json"]) == {"a/b.json": PRESENT, "a/portal.json": UNKNOWN}
    assert store.info("a/b.json").raw_bytes == 8 and not (tmp_path / "files" / "a" / "portal.json.gz").exists()
    store.close()

    # A damaged manifest: set aside, rebuilt.
    (tmp_path / "manifest.sqlite").write_bytes(b"this is not a database" * 100)
    for name in ("manifest.sqlite-wal", "manifest.sqlite-shm"):
        (tmp_path / name).unlink(missing_ok=True)
    store = Store(tmp_path)
    assert store.rebuilt_from and store.status("a/b.json") == PRESENT
    assert list(tmp_path.glob("manifest.sqlite.damaged-*"))
    store.close()


def test_a_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    import errno
    import os

    store = Store(tmp_path)

    def full(*_a):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(OSError):
        store.write("a/b.json", b"{}")
    assert not list((tmp_path / "files").rglob("*.tmp")) and store.status("a/b.json") == UNKNOWN
    store.close()


def test_absence_needs_evidence_from_s3(settings):
    import httpx

    def handler(request):
        if "portal" in str(request.url):
            return httpx.Response(404, text="<html>not here</html>", headers={"content-type": "text/html"})
        return httpx.Response(
            404, content=b"<Error><Code>NoSuchKey</Code></Error>", headers={"server": "AmazonS3"}
        )

    with _source(settings, handler) as src:
        with pytest.raises(UpstreamError):
            src.get_json("portal/x.json")
        assert src.status("portal/x.json") == UNKNOWN
        assert src.get_json("s3/x.json") is None and src.status("s3/x.json") == ABSENT


def test_index_files_are_never_remembered_as_absent_on_disk(settings, upstream):
    del upstream.files["public_atlas_data/index.json"]
    with AtlasSource(settings, client=upstream.client()) as src:
        assert src.get_index("atlas") is None and src.get_index("atlas") is None
        assert upstream.fetched("public_atlas_data/index.json") == 1  # remembered for this process
        assert src.status("public_atlas_data/index.json") == UNKNOWN  # but not on disk
    with AtlasSource(settings, client=upstream.client()) as src:
        src.get_index("atlas")
    assert upstream.fetched("public_atlas_data/index.json") == 2


def test_a_200_that_is_not_json_is_refused(settings):
    import httpx

    def handler(request):
        return httpx.Response(200, text="<html>Please log in</html>", headers={"content-type": "text/html"})

    with _source(settings, handler) as src:
        with pytest.raises(UpstreamError, match="without JSON"):
            src.get_bytes("public_atlas_data/index.json")
        result = src.fetch_many([pole_path("subcircuit", fakeatlas.RIVER)])[0]
        assert result.status == "error" and "without JSON" in result.error
        assert src.store.summary()["files"] == 0


def test_bulk_fetch_stops_when_upstream_keeps_refusing(settings):
    import httpx

    from neuron_atlas.upstream import BREAKER_FAILURES

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(403, text="blocked")  # a WAF, not S3: every file fails

    paths = [pole_path("subcircuit", f"L4_E1_N{i}__pos") for i in range(60)]
    with _source(settings, handler) as src:
        results = src.fetch_many(paths)
    errors = [r.error for r in results]
    skipped = [e for e in errors if e.startswith("not attempted: ")]
    assert len(results) == 60 and len(skipped) >= 60 - BREAKER_FAILURES - settings.max_concurrency
    assert len(calls) <= BREAKER_FAILURES + settings.max_concurrency
    assert "in a row" in skipped[0]


def test_a_long_retry_after_ends_the_request_instead_of_waiting(settings, monkeypatch):
    import httpx

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3600"})

    with _source(settings, handler) as src, pytest.raises(UpstreamError, match="asks to wait"):
        src.get_bytes("public_atlas_data/index.json")
    assert len(calls) == 1 and not slept


def test_a_short_retry_after_pauses_every_request(settings, upstream, monkeypatch):
    import httpx

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    first = []

    def handler(request):
        if not first:
            first.append(1)
            return httpx.Response(503, headers={"retry-after": "7"})
        return upstream.handler(request)

    with _source(settings, handler) as src:
        assert src.get_index("atlas")["neurons"]
    assert any(5.0 <= s <= 7.0 for s in slept)  # the pause was honoured (in steps of at most 5 s)


def test_bulk_fetch_survives_a_full_disk(settings, upstream, monkeypatch):
    import errno

    def full(self, relpath, raw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Store, "write", full)
    paths = [pole_path(kind, pid) for kind in ("subcircuit", "landscape") for pid in (fakeatlas.RIVER, fakeatlas.MIXED, fakeatlas.FN)]
    with AtlasSource(settings, client=upstream.client()) as src:
        results = src.fetch_many(paths)  # never raises
    assert len(results) == len(paths)
    assert any("local disk" in r.error for r in results if r.error.startswith("not attempted"))
    assert len(upstream.requests) <= settings.max_concurrency


def test_ctrl_c_stops_a_bulk_fetch_at_once(settings, upstream):

    def slow(request):
        time.sleep(0.02)
        return upstream.handler(request)

    paths = [pole_path("subcircuit", f"L4_E1_N{i}__pos") for i in range(200)]

    def interrupt(_res):
        raise KeyboardInterrupt

    with _source(settings, slow) as src:
        with pytest.raises(KeyboardInterrupt):
            src.fetch_many(paths, on_result=interrupt)
        time.sleep(0.2)
        assert len(upstream.requests) <= 3 * settings.max_concurrency  # not the remaining 190 files


def test_the_explicit_mirror_fetches_even_when_read_through_is_off(settings, upstream):
    path = pole_path("subcircuit", fakeatlas.RIVER)
    with AtlasSource(settings.with_overrides(allow_remote_fetch=False), client=upstream.client()) as src:
        with pytest.raises(NotMirrored):
            src.get_json(path)
        assert src.fetch_many([path])[0].status == PRESENT
        assert src.get_json(path)["pid"] == fakeatlas.RIVER
