import sys
import types

import httpx
import pytest

from neuron_atlas import notebook

from . import fakeatlas


@pytest.fixture
def fake_colab(monkeypatch):
    """A stand-in for Colab's front-end helpers (the real ones need a browser attached to the kernel)."""
    calls = {"iframes": [], "js": []}

    def eval_js(code, ignore_result=False, timeout_sec=None):
        calls["js"].append((code, timeout_sec))
        return "https://8000-m-s-abc123.us-west4-0.prod.colab.dev/"

    def serve_kernel_port_as_iframe(port, path="/", width="100%", height="400", cache_in_notebook=False):
        calls["iframes"].append((port, height, cache_in_notebook))

    output = types.SimpleNamespace(eval_js=eval_js, serve_kernel_port_as_iframe=serve_kernel_port_as_iframe)
    colab = types.ModuleType("google.colab")
    colab.output = output
    google = types.ModuleType("google")
    google.colab = colab
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.setitem(sys.modules, "google.colab.output", output)
    return calls


def test_the_server_runs_in_the_background_of_a_notebook(atlas):
    server = notebook.start(atlas, port=18731, show=False)
    try:
        assert server.running and server.url == f"http://127.0.0.1:{server.port}/"
        with httpx.Client(base_url=server.url, timeout=10) as c:
            assert c.get("/healthz").json()["ok"] is True
            assert c.get("/api/v1/search", params={"q": "braille"}).json()["total"] == 1
            # Outside Colab only this machine's names are accepted (DNS rebinding).
            assert c.get("/api/v1/info", headers={"Host": "evil.example"}).status_code == 421
        assert server.atlas is atlas  # the notebook and the page share one atlas
    finally:
        server.stop()
    assert not server.running


def test_starting_again_replaces_the_first_server(atlas):
    assert notebook._free_port(8080, "127.0.0.1") != 8080  # Colab's own services live there
    first = notebook.start(atlas, port=18741, show=False)
    second = notebook.start(atlas, port=18741, show=False)
    try:
        assert not first.running and second.running
    finally:
        notebook.stop()
    assert not second.running


def test_in_colab_the_page_is_shown_inline_and_internal_host_names_are_accepted(atlas, fake_colab):
    pytest.importorskip("IPython")
    assert notebook.in_colab()
    server = notebook.start(atlas, port=18751)
    try:
        assert server.url == "https://8000-m-s-abc123.us-west4-0.prod.colab.dev/"
        assert fake_colab["iframes"] == [(server.port, "900", False)]  # never cached into the notebook
        assert all(timeout for _code, timeout in fake_colab["js"])  # eval_js never waits for ever
        with httpx.Client(base_url=f"http://127.0.0.1:{server.port}", timeout=10) as c:
            # Colab's proxy passes an internal Google host name on, not the public one.
            internal = c.get("/api/v1/info", headers={"Host": "abc123.us-west4.c.colab-user-runtimes.internal:8000"})
            assert internal.status_code == 200
            assert internal.headers["x-colab-notebook-cache-control"] == "no-cache"
            assert "https://8000-m-s-abc123.us-west4-0.prod.colab.dev/api/v1/search" in c.get("/llms.txt").text
    finally:
        server.stop()


def test_colab_keeps_the_mirror_on_the_session_disk_unless_told_otherwise(fake_colab, monkeypatch):
    monkeypatch.delenv("ATLAS_DATA_DIR", raising=False)
    assert notebook.settings_for_notebook().data_dir == notebook.COLAB_DATA_DIR
    monkeypatch.setenv("ATLAS_DATA_DIR", "/tmp/elsewhere")
    assert str(notebook.settings_for_notebook().data_dir) == "/tmp/elsewhere"


def test_poles_and_maps_display_inline(atlas):
    pytest.importorskip("IPython")
    from neuron_atlas.geometry.maps import semantic_map

    doc = notebook.show_pole(fakeatlas.RIVER, atlas=atlas)
    assert "L4/E1/N10" in doc.data
    frame = notebook.show_map(semantic_map(atlas, unit="pole"), title="t")
    assert frame.data.startswith("<div><iframe srcdoc=") and "sandbox=" in frame.data


def test_the_mirror_survives_a_new_session_through_one_archive(atlas, settings, upstream, tmp_path):
    import io
    import tarfile

    from neuron_atlas.service import Atlas
    from neuron_atlas.upstream import AtlasSource, pole_path

    atlas.pole(fakeatlas.RIVER, ["modes", "overview"])  # mirrors a few files
    archive = notebook.backup(tmp_path / "drive", atlas=atlas)
    assert archive.name == "neuron-atlas-test-release.tar"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert f"test-release/files/{pole_path('overview', fakeatlas.RIVER)}.gz" in names
    assert not any(n.endswith((".sqlite", ".sqlite-wal")) for n in names)

    # A hostile archive: nothing may land outside the data directory, and only mirror files are taken.
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tar:
        for name in ("../escape.gz", "/abs/x.gz", "test-release/files/../../y.gz", "test-release/manifest.sqlite"):
            info = tarfile.TarInfo(name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    fresh = tmp_path / "new-session"
    assert notebook.restore(evil, fresh) == 0 and not (tmp_path / "escape.gz").exists()

    restored = notebook.restore(archive, fresh)
    assert restored == sum(1 for n in names if n.endswith(".gz"))
    offline = settings.with_overrides(data_dir=fresh, offline=True)
    later = Atlas(source=AtlasSource(offline, client=upstream.client()))
    try:
        assert later.pole(fakeatlas.RIVER, ["overview"]).sources["overview"] == "used"  # read offline
    finally:
        later.close()
