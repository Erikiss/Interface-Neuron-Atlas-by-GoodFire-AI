"""The interface inside a notebook: Google Colab or a local Jupyter.

::

    from neuron_atlas import notebook

    server = notebook.start()           # web page, API and MCP endpoint in the background, shown inline
    atlas = server.atlas                # the same atlas from Python
    notebook.show_pole("L30_E47_N449__pos")
    server.stop()

The server runs in a thread of the notebook's own Python process, so the page
and the Python code share one atlas (one mirror, one search index, one set of
download jobs).

In Colab the page is shown inside the cell output (Colab's port proxy in an
iframe: copying, printing, downloads and links work there), with a link that
opens it in a tab of its own.  Colab's proxy lets only the signed-in notebook
user in, and only while the notebook is open; the address changes with every
runtime.

Host names: the server normally answers only to ``localhost`` - that protects
a server on your own computer from web pages that point a domain of theirs at
it (DNS rebinding).  In Colab the server runs on Google's machine, is reached
only through that authenticated proxy, and sees an internal Google host name
(``….colab-user-runtimes.internal``), so the protection does not apply there
and every host name is accepted.
"""

from __future__ import annotations

import html
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .service import Atlas

#: Where Colab keeps the mirror unless ATLAS_DATA_DIR says otherwise (the local disk of the session).
COLAB_DATA_DIR = Path("/content/neuron-atlas")
#: Ports Colab's own services use inside the runtime (Node proxy, Jupyter, kernel manager, file shim).
_RESERVED_PORTS = frozenset({8080, 9000, 6000, 3453})


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
        if port in _RESERVED_PORTS:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"no free port between {preferred} and {preferred + 49}")


@dataclass
class NotebookServer:
    """A running server; ``stop()`` ends it (it also ends with the notebook's Python process)."""

    atlas: Atlas
    port: int
    #: Where the page is: the Colab proxy address (valid for you while the notebook is open) or localhost.
    url: str
    height: int = 900
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

    def show(self, height: int | None = None) -> None:
        """Show the page again (after reconnecting, or in another cell)."""
        _show(self.port, self.url, height or self.height)

    def _repr_html_(self) -> str:  # pragma: no cover - notebook display
        state = "läuft" if self.running else "gestoppt"
        return f'Neuron Atlas Interface ({state}): <a href="{html.escape(self.url)}" target="_blank" rel="noopener">{html.escape(self.url)}</a>'


_current: NotebookServer | None = None
_atlas: Atlas | None = None


def open_atlas(**settings: Any) -> Atlas:
    """The notebook's atlas: created on first use (settings from the environment plus ``settings``), then shared.

    :func:`start` serves this same atlas, so the page and the Python cells see
    one mirror and one search index.
    """
    global _atlas
    if _atlas is None or settings:
        _atlas = Atlas(settings_for_notebook(**settings))
    return _atlas


def start(
    atlas: Atlas | None = None,
    *,
    port: int = 8000,
    show: bool = True,
    height: int = 900,
    timeout: float = 60.0,
    **settings: Any,
) -> NotebookServer:
    """Start the web page, the HTTP API and the MCP endpoint in the background and show the page.

    ``atlas`` is shared with the server (one is created when it is omitted);
    keyword arguments override settings, e.g. ``start(offline=True)``.
    Calling ``start()`` again stops the previous server first.
    """
    global _current
    import logging

    import uvicorn

    from .api import create_app

    if _current is not None:
        _current.stop()
        _current = None
    colab = in_colab()
    if atlas is None:
        atlas = open_atlas(**settings)
    elif settings:
        atlas.settings = atlas.settings.with_overrides(**settings)
    if colab and "*" not in atlas.settings.allowed_hosts:
        # Reached only through Colab's authenticated proxy, under internal host names (see the module docstring).
        atlas.settings = atlas.settings.with_overrides(allowed_hosts=(*atlas.settings.allowed_hosts, "*"))

    # Library chatter at INFO level (the MCP session manager, every HTTP request) is only noise in a notebook.
    for name in ("mcp", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    host = "127.0.0.1"
    port = _free_port(port, host)
    app = create_app(atlas)
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False, lifespan="on", timeout_graceful_shutdown=2
    )
    server = uvicorn.Server(config)
    # A thread of its own: a notebook cell already runs an event loop, so uvicorn.run() cannot be used here.
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

    url = (_proxy_url(port) if colab else None) or f"http://{host}:{port}/"
    app.state.public_url = url  # /llms.txt names this address instead of the internal one
    _current = NotebookServer(atlas=atlas, port=port, url=url, height=height, _server=server, _thread=thread)
    if show:
        _show(port, url, height)
    return _current


def stop() -> None:
    """Stop the server started by :func:`start`."""
    global _current
    if _current is not None:
        _current.stop()
        _current = None


def _proxy_url(port: int) -> str | None:
    """The address under which Colab's proxy serves ``port`` to this notebook's user (None outside Colab).

    Needs the browser tab of the notebook (it asks the Colab page); without one
    it gives up after the time-out instead of waiting for ever.
    """
    try:
        from google.colab.output import eval_js  # type: ignore[import-not-found]

        url = eval_js(f"google.colab.kernel.proxyPort({int(port)})", timeout_sec=30)
    except Exception:  # noqa: BLE001 - no Colab front end attached (a headless run, a closed tab)
        return None
    return str(url) if url else None


def _show(port: int, url: str, height: int = 900) -> None:
    """Show the page: in Colab inside the cell output, with a link to a tab of its own; elsewhere a link."""
    try:
        from IPython.display import HTML, display
    except ImportError:  # pragma: no cover - plain Python
        print(f"Neuron Atlas Interface: {url}")
        return
    try:
        from google.colab import output  # type: ignore[import-not-found]

        # The inline view is the reliable one: a new tab needs the proxy's cookie, which browsers that
        # block or partition third-party cookies do not send there.
        output.serve_kernel_port_as_iframe(port, height=str(int(height)))
        display(
            HTML(
                f'<a href="{html.escape(url)}" target="_blank" rel="noopener">In neuem Tab öffnen</a> '
                "<small>(nur für dich, solange dieses Notebook offen ist; bleibt der Tab leer oder meldet 403, "
                "die eingebettete Ansicht nutzen oder Drittanbieter-Cookies für colab.dev erlauben)</small>"
            )
        )
        return
    except ImportError:
        pass
    display(HTML(f'<a href="{html.escape(url)}" target="_blank" rel="noopener">Neuron Atlas Interface öffnen</a> ({html.escape(url)})'))


# --------------------------------------------------------------------------- keeping the mirror between sessions


def backup(target: str | Path, atlas: Atlas | None = None) -> Path:
    """Pack the mirrored files into one archive (e.g. on Google Drive) and return its path.

    Only the data files go in (they are gzip already, so the archive is a plain
    tar): one big file copies to Drive far faster than thousands of small ones,
    and the SQLite manifest and search index are not copied while they are in
    use - they are rebuilt from the files after :func:`restore`.  The archive is
    built on the local disk first and then copied as one file (``.part``, then
    renamed), so a broken copy never replaces a good archive.  ``target`` is a
    directory (the archive is named after the release) or a file name.  On
    Colab, call ``google.colab.drive.flush_and_unmount()`` afterwards: Drive
    uploads in the background and loses what is not uploaded when the runtime
    ends.
    """
    import tarfile

    atlas = atlas or (_current.atlas if _current else None) or open_atlas()
    root = atlas.source.store.root
    release = root.name
    target = Path(target)
    path = target / f"neuron-atlas-{release}.tar" if target.is_dir() or not target.suffix else target
    path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in (root / "files").rglob("*.gz") if p.is_file())
    with tempfile.TemporaryDirectory(dir=atlas.settings.data_dir) as tmp:
        local = Path(tmp) / path.name
        with tarfile.open(local, "w") as tar:
            for f in files:
                tar.add(f, arcname=f"{release}/files/{f.relative_to(root / 'files').as_posix()}")
            state = atlas.settings.data_dir / "release.json"
            if state.is_file():
                tar.add(state, arcname="release.json")
        part = path.with_name(path.name + ".part")
        shutil.copyfile(local, part)
        os.replace(part, path)
    return path


def restore(archive: str | Path, data_dir: str | Path | None = None) -> int:
    """Unpack an archive made by :func:`backup` into the data directory; returns the number of files.

    The archive is copied to the local disk first (reading it straight from
    Google Drive is slow) and unpacked there.  Members that would land outside
    the data directory, links and anything but the mirror's ``*.gz`` files and
    ``release.json`` are skipped.  Afterwards the manifest is rebuilt on
    demand; call ``atlas.index_mirrored()`` to make the restored texts
    searchable again.
    """
    import tarfile

    base = Path(data_dir) if data_dir else settings_for_notebook().data_dir
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    count = 0
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        local = Path(tmp) / "archive.tar"
        shutil.copyfile(archive, local)
        with tarfile.open(local, "r") as tar:
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

    atlas = atlas or (_current.atlas if _current else None) or open_atlas()
    return Markdown(pole_markdown(atlas.pole(ref, include, max_examples=max_examples)))


def show_map(data: dict[str, Any], *, title: str = "Neuron Atlas map", height: int = 720) -> Any:
    """The interactive map of :func:`neuron_atlas.geometry.maps.semantic_map` inside the notebook's output."""
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
