import sys
import types

import httpx
import pytest

from neuron_atlas import notebook

from . import fakeatlas


@pytest.fixture
def fake_colab(monkeypatch):
    """A stand-in for Colab's front-end helpers (the real ones need a browser attached to the kernel)."""
    calls = {"windows": [], "js": []}

    def eval_js(code):
        calls["js"].append(code)
        return "https://8000-m-s-abc123.us-west4-0.prod.colab.dev/"

    def serve_kernel_port_as_window(port, path="/", anchor_text=None):
        calls["windows"].append((port, anchor_text))

    output = types.SimpleNamespace(eval_js=eval_js, serve_kernel_port_as_window=serve_kernel_port_as_window)
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
    first = notebook.start(atlas, port=18741, show=False)
    second = notebook.start(atlas, port=18741, show=False)
    try:
        assert not first.running and second.running
    finally:
        notebook.stop()
    assert not second.running


def test_in_colab_the_proxy_link_is_shown_and_its_host_names_are_accepted(atlas, fake_colab):
    assert notebook.in_colab()
    server = notebook.start(atlas, port=18751)
    try:
        assert server.url == "https://8000-m-s-abc123.us-west4-0.prod.colab.dev/"
        assert fake_colab["windows"] == [(server.port, "Neuron Atlas Interface öffnen")]
        with httpx.Client(base_url=f"http://127.0.0.1:{server.port}", timeout=10) as c:
            proxied = c.get("/api/v1/info", headers={"Host": "8000-m-s-abc123.us-west4-0.prod.colab.dev"})
            assert proxied.status_code == 200
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
