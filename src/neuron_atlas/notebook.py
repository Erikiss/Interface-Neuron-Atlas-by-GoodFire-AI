"""The interface inside a notebook: Google Colab or a local Jupyter.

::

    from neuron_atlas import notebook

    server = notebook.start()           # web page, API and MCP endpoint in the background, with a link
    atlas = server.atlas                # the same atlas from Python
    notebook.show_pole("L30_E47_N449__pos")
    server.stop()

The server runs in a thread of the notebook's own Python process, so the page
and the Python code share one atlas (one mirror, one search index, one set of
download jobs).

Host names: the server normally answers only to ``localhost`` - that protects
a server on your own computer from web pages that point a domain of theirs at
it (DNS rebinding).  In Colab the server runs on Google's machine and is
reached only through Colab's authenticated port proxy, under a host name of
Google's, so that protection does not apply there and every host name is
accepted.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .service import Atlas

#: Where Colab keeps the mirror unless ATLAS_DATA_DIR says otherwise (the local disk of the session).
COLAB_DATA_DIR = Path("/content/neuron-atlas")


def in_colab() -> bool:
    """True inside a Google Colab runtime."""
    if "google.colab" in sys.modules:
        return True
    try:
        import importlib.util

        return importlib.util.find_spec("google.colab") is not None
    except (ImportError, ValueError):
        return False


def settings_for_notebook(**overrides: Any) -> Settings:
    """Settings from the environment, with a data directory that suits the notebook host."""
    settings = Settings.from_env()
    if in_colab() and not os.environ.get("ATLAS_DATA_DIR"):
        settings = settings.with_overrides(data_dir=COLAB_DATA_DIR)
    return settings.with_overrides(**overrides) if overrides else settings


def _free_port(preferred: int, host: str) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"no free port between {preferred} and {preferred + 49}")


@dataclass
class NotebookServer:
    """A running server; ``stop()`` ends it (it also ends with the notebook's Python process)."""

    atlas: Atlas
    port: int
    url: str
    _server: Any = field(repr=False, default=None)
    _thread: threading.Thread | None = field(repr=False, default=None)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self, timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)

    def show(self) -> None:
        """Show the link again (in Colab: a link that opens the page in a new tab)."""
        _show_link(self.port, self.url)

    def _repr_html_(self) -> str:  # pragma: no cover - notebook display
        state = "läuft" if self.running else "gestoppt"
        return f'Neuron Atlas Interface ({state}): <a href="{self.url}" target="_blank" rel="noopener">{self.url}</a>'


_current: NotebookServer | None = None


def start(
    atlas: Atlas | None = None,
    *,
    port: int = 8000,
    show: bool = True,
    timeout: float = 60.0,
    **settings: Any,
) -> NotebookServer:
    """Start the web page, the HTTP API and the MCP endpoint in the background and show a link.

    ``atlas`` is shared with the server (one is created when it is omitted);
    keyword arguments override settings, e.g. ``start(offline=True)``.
    Calling ``start()`` again stops the previous server first.
    """
    global _current
    import uvicorn

    from .api import create_app

    if _current is not None:
        _current.stop()
        _current = None
    colab = in_colab()
    if atlas is None:
        atlas = Atlas(settings_for_notebook(**settings))
    elif settings:
        atlas.settings = atlas.settings.with_overrides(**settings)
    if colab and "*" not in atlas.settings.allowed_hosts:
        # Reached only through Colab's authenticated proxy, under Google's host names (see the module docstring).
        atlas.settings = atlas.settings.with_overrides(allowed_hosts=(*atlas.settings.allowed_hosts, "*"))

    import logging

    # The MCP library announces its session manager at INFO level; in a notebook that is only noise.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    host = "127.0.0.1"
    port = _free_port(port, host)
    app = create_app(atlas)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="neuron-atlas-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("the server stopped while starting; see the messages above")
        if time.monotonic() > deadline:
            server.should_exit = True
            raise TimeoutError(f"the server did not start within {timeout:.0f} s")
        time.sleep(0.05)

    url = _proxy_url(port) if colab else None
    _current = NotebookServer(atlas=atlas, port=port, url=url or f"http://{host}:{port}/", _server=server, _thread=thread)
    if show:
        _show_link(port, _current.url)
    return _current


def stop() -> None:
    """Stop the server started by :func:`start`."""
    global _current
    if _current is not None:
        _current.stop()
        _current = None


def _proxy_url(port: int) -> str | None:
    """The URL under which Colab's proxy serves ``port`` to this notebook's user (None outside Colab)."""
    try:
        from google.colab.output import eval_js  # type: ignore[import-not-found]

        url = eval_js(f"google.colab.kernel.proxyPort({int(port)})")
    except Exception:  # noqa: BLE001 - no Colab front end (e.g. a headless run)
        return None
    return str(url) if url else None


def _show_link(port: int, url: str) -> None:
    """A link that opens the page.  In Colab a new tab (copy buttons and printing need a full tab, not an iframe)."""
    try:
        from google.colab import output  # type: ignore[import-not-found]

        output.serve_kernel_port_as_window(port, anchor_text="Neuron Atlas Interface öffnen")
        return
    except Exception:  # noqa: BLE001 - not in Colab
        pass
    try:
        from IPython.display import HTML, display

        display(HTML(f'<a href="{url}" target="_blank" rel="noopener">Neuron Atlas Interface öffnen</a> ({url})'))
    except ImportError:  # pragma: no cover - plain Python
        print(f"Neuron Atlas Interface: {url}")


# --------------------------------------------------------------------------- keeping the mirror between sessions


def backup(target: str | Path, atlas: Atlas | None = None) -> Path:
    """Pack the mirrored files into one archive (e.g. on Google Drive) and return its path.

    Only the data files go in (they are gzip already, so the archive is a plain
    tar): one big file copies to Drive far faster than thousands of small ones,
    and the SQLite manifest and search index are not copied while they are in
    use - they are rebuilt from the files after :func:`restore`.  ``target`` is
    a directory (the archive is named after the release) or a file name.
    """
    import tarfile

    atlas = atlas or (_current.atlas if _current else None) or Atlas(settings_for_notebook())
    root = atlas.source.store.root
    release = root.name
    target = Path(target)
    path = target / f"neuron-atlas-{release}.tar" if target.is_dir() or not target.suffix else target
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    files = sorted(p for p in (root / "files").rglob("*.gz") if p.is_file())
    with tarfile.open(tmp, "w") as tar:
        for f in files:
            tar.add(f, arcname=f"{release}/files/{f.relative_to(root / 'files').as_posix()}")
        state = atlas.settings.data_dir / "release.json"
        if state.is_file():
            tar.add(state, arcname="release.json")
    tmp.replace(path)
    return path


def restore(archive: str | Path, data_dir: str | Path | None = None) -> int:
    """Unpack an archive made by :func:`backup` into the data directory; returns the number of files.

    Members that would land outside the data directory, links and anything but
    the mirror's ``*.gz`` files and ``release.json`` are skipped.  Afterwards
    the manifest is rebuilt on demand; call ``atlas.index_mirrored()`` to make
    the restored texts searchable again.
    """
    import tarfile

    base = Path(data_dir) if data_dir else settings_for_notebook().data_dir
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    count = 0
    with tarfile.open(archive, "r") as tar:
        for member in tar:
            name = member.name
            parts = Path(name).parts
            wanted = name == "release.json" or (len(parts) >= 3 and parts[1] == "files" and name.endswith(".gz"))
            if not member.isfile() or not wanted or name.startswith("/") or ".." in parts:
                continue
            dest = (base / name).resolve()
            if resolved not in dest.parents:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(dest, "wb") as out:
                while chunk := source.read(1 << 20):
                    out.write(chunk)
            count += name != "release.json"
    return count


# --------------------------------------------------------------------------- inline views


def show_pole(
    ref: str,
    include: tuple[str, ...] = ("modes", "examples", "overview"),
    max_examples: int = 2,
    atlas: Atlas | None = None,
) -> Any:
    """A pole as a Markdown document in the notebook's output (the same document as /p/… or ``show``)."""
    from IPython.display import Markdown

    from .render import pole_markdown

    atlas = atlas or (_current.atlas if _current else None) or Atlas(settings_for_notebook())
    return Markdown(pole_markdown(atlas.pole(ref, include, max_examples=max_examples)))


def show_map(data: dict[str, Any], *, title: str = "Neuron Atlas map", height: int = 720) -> Any:
    """The interactive map of :func:`neuron_atlas.geometry.maps.semantic_map` inside the notebook's output."""
    import html
    import tempfile

    from IPython.display import HTML

    from .geometry.maps import write_map_html

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "map.html"
        write_map_html(data, path, title=title)
        page = path.read_text("utf-8")
    # In an iframe of its own, so that the viewer's script and styles do not mix with the notebook's.
    return HTML(
        f'<div><iframe srcdoc="{html.escape(page, quote=True)}" style="width:100%;height:{int(height)}px;border:0" '
        'sandbox="allow-scripts"></iframe></div>'
    )
