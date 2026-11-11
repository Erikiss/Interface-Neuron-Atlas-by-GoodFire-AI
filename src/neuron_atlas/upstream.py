"""Access to the atlas files: a polite HTTP client in front of a local mirror.

The published atlas is a static site.  Every piece of data is a JSON file
below one immutable *release* prefix, so a file that was fetched once never
has to be fetched again.  :class:`AtlasSource` therefore works read-through:

* a file that is in the local store is served from disk,
* a file that is not is fetched once (rate limited), stored gzip-compressed
  and served,
* a file that upstream does not have is remembered as absent.

The class is synchronous and thread-safe; the API server calls it from its
worker threads and the mirror tool from a small thread pool.
"""

from __future__ import annotations

import contextlib
import errno
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import ALLOWED_RELEASE_HOSTS, ATLAS_PAGE_URL, KNOWN_RELEASE_URL, Settings, release_id

# --------------------------------------------------------------------------- paths

#: Global files (one per release).
INDEX_FILES: dict[str, str] = {
    "atlas": "public_atlas_data/index.json",
    "subcircuit": "subcircuit_modes_index.json",
    "overview": "ns11_pages/data/index.json",
    "landscape": "neuron_landscape_data/atlas_index.json",
    "islands": "public_atlas_data/islands/index.json",
    "circuit": "moe_circuit_explorer_data/index.json",
    "headlines": "ns11_pages/headline_overrides.json",
}

#: Per-pole files, keyed by pole id (``L30_E47_N449__pos``).
POLE_FILES: dict[str, str] = {
    "subcircuit": "subcircuit_modes_data/{pid}.json",
    "overview": "ns11_pages/data/{pid}.json",
    "landscape": "neuron_landscape_data/{pid}.json",
    "islands": "public_atlas_data/islands/poles/{pid}.json",
}

#: Per-neuron files, keyed by neuron id (``L30_E47_N449``).
NEURON_FILES: dict[str, str] = {
    "circuit_meta": "moe_circuit_explorer_data/{did}/meta.json",
    "circuit_exemplars": "moe_circuit_explorer_data/{did}/exemplars.json",
    "circuit_incidence": "moe_circuit_explorer_data/{did}/incidence.json",
}

_SAFE_RELPATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.json")
#: The global index files: never remembered as absent on disk (a stray answer must not disable the installation).
_INDEX_PATHS = frozenset(INDEX_FILES.values())
_EXPLORER_ATTR = re.compile(r'data-explorer="([^"]+)"')


def pole_path(source: str, pid: str) -> str:
    return POLE_FILES[source].format(pid=pid)


def neuron_path(source: str, did: str) -> str:
    return NEURON_FILES[source].format(did=did)


def check_relpath(relpath: str) -> str:
    """Reject anything that is not a plain relative JSON path inside the release."""
    if not isinstance(relpath, str) or not _SAFE_RELPATH.fullmatch(relpath) or ".." in relpath or "//" in relpath:
        raise ValueError(f"illegal atlas path: {relpath!r}")
    return relpath


# --------------------------------------------------------------------------- errors


class AtlasError(Exception):
    """Base class of everything this package raises on purpose."""


class UpstreamError(AtlasError):
    """The upstream server could not be reached or answered unexpectedly."""


class Cancelled(AtlasError):
    """A transfer was stopped on request (Ctrl-C, a cancelled job) before it finished."""

    def __init__(self) -> None:
        super().__init__("cancelled")


class NotMirrored(AtlasError):
    """The file is not in the local mirror and fetching it is not allowed."""

    def __init__(self, relpath: str):
        super().__init__(
            f"{relpath} is not in the local mirror and remote fetching is disabled "
            "(offline mode / ATLAS_ALLOW_REMOTE_FETCH=0)"
        )
        self.relpath = relpath


# --------------------------------------------------------------------------- rate limiting


class RateLimiter:
    """Token bucket shared by all threads: at most ``rate`` acquisitions per second."""

    def __init__(self, rate: float, burst: int | None = None):
        self.rate = float(rate)
        self.capacity = float(burst if burst is not None else max(1.0, rate))
        self._tokens = self.capacity
        self._stamp = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._stamp) * self.rate)
                self._stamp = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(wait)


# --------------------------------------------------------------------------- local store

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class FileInfo:
    relpath: str
    status: str
    raw_bytes: int = 0
    stored_bytes: int = 0
    sha256: str = ""
    fetched_at: float = 0.0


#: Errors that mean a stored file is damaged (cut short, not gzip, bad checksum) or gone.
_DAMAGED = (EOFError, OSError, zlib.error)


def _json_like(raw: bytes) -> bool:
    """A cheap test that ``raw`` is a JSON object or array (catches HTML pages and cut-off bodies)."""
    body = raw.strip().removeprefix(b"\xef\xbb\xbf").strip()
    return bool(body) and body[:1] in (b"{", b"[") and body[-1:] in (b"}", b"]")


class Store:
    """Gzip-compressed files of one release plus a small SQLite manifest.

    The manifest is a cache of what is on disk, not the truth: a file it calls
    present is checked against its recorded size before it is trusted, a file
    on disk that it does not know is checked and adopted, and a damaged
    manifest is set aside and rebuilt.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.rebuilt_from: str | None = None  # name of a damaged manifest that was set aside
        self._db = self._open_manifest()

    def _open_manifest(self) -> sqlite3.Connection:
        path = self.root / "manifest.sqlite"
        db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        try:
            self._prepare(db)
            return db
        except sqlite3.OperationalError as exc:
            db.close()
            if "locked" in str(exc).lower():
                raise
            damaged: Exception = exc
        except sqlite3.DatabaseError as exc:
            db.close()
            damaged = exc
        # Keep the damaged manifest for inspection and start a new one; the files are adopted again on demand.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            old = Path(f"{path}{suffix}")
            if old.exists():
                old.replace(old.with_name(f"{old.name}.damaged-{stamp}"))
        self.rebuilt_from = f"manifest.sqlite.damaged-{stamp} ({damaged})"
        db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._prepare(db)
        return db

    @staticmethod
    def _prepare(db: sqlite3.Connection) -> None:
        deadline = time.monotonic() + 30
        while True:  # several processes opening a fresh store at once: the WAL switch may meet a lock
            try:
                db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS files(
                   relpath TEXT PRIMARY KEY,
                   status TEXT NOT NULL,
                   raw_bytes INTEGER NOT NULL DEFAULT 0,
                   stored_bytes INTEGER NOT NULL DEFAULT 0,
                   sha256 TEXT NOT NULL DEFAULT '',
                   fetched_at REAL NOT NULL DEFAULT 0)"""
        )
        db.commit()
        check = db.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError(f"manifest check failed: {check[0] if check else 'no answer'}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _file(self, relpath: str) -> Path:
        return self.files_dir / (check_relpath(relpath) + ".gz")

    def _intact(self, relpath: str, stored_bytes: int) -> bool:
        """The file of a present row exists and has the size recorded when it was written."""
        try:
            size = self._file(relpath).stat().st_size
        except OSError:
            return False
        return stored_bytes <= 0 or size == stored_bytes

    def _adopt(self, relpath: str) -> FileInfo:
        """A file on disk that the manifest does not know (a rebuilt manifest, a copied data dir): check, record."""
        path = self._file(relpath)
        try:
            raw = self.read(relpath)
            stat = path.stat()
        except _DAMAGED:
            path.unlink(missing_ok=True)
            return FileInfo(relpath, UNKNOWN)
        if not _json_like(raw):
            path.unlink(missing_ok=True)
            return FileInfo(relpath, UNKNOWN)
        info = FileInfo(relpath, PRESENT, len(raw), stat.st_size, hashlib.sha256(raw).hexdigest(), stat.st_mtime)
        self._record(info)
        return info

    def info(self, relpath: str) -> FileInfo:
        with self._lock:
            row = self._db.execute(
                "SELECT relpath,status,raw_bytes,stored_bytes,sha256,fetched_at FROM files WHERE relpath=?",
                (relpath,),
            ).fetchone()
        if row is None:
            return self._adopt(relpath) if self._file(relpath).exists() else FileInfo(relpath, UNKNOWN)
        info = FileInfo(*row)
        if info.status == PRESENT and not self._intact(relpath, info.stored_bytes):
            self.forget(relpath)  # deleted or damaged behind our back
            return FileInfo(relpath, UNKNOWN)
        return info

    def status(self, relpath: str) -> str:
        return self.info(relpath).status

    def statuses(self, relpaths: Iterable[str]) -> dict[str, str]:
        """Status of many files at once (present rows are checked against the files on disk)."""
        wanted = list(relpaths)
        out = dict.fromkeys(wanted, UNKNOWN)
        rows: dict[str, tuple[str, int]] = {}
        with self._lock:
            for i in range(0, len(wanted), 500):
                chunk = wanted[i : i + 500]
                marks = ",".join("?" * len(chunk))
                for relpath, status, stored in self._db.execute(
                    f"SELECT relpath,status,stored_bytes FROM files WHERE relpath IN ({marks})", chunk
                ):
                    rows[relpath] = (status, stored)
        for relpath in wanted:
            row = rows.get(relpath)
            if row is None:
                if self._file(relpath).exists():
                    out[relpath] = self._adopt(relpath).status
            elif row[0] == PRESENT and not self._intact(relpath, row[1]):
                self.forget(relpath)
            else:
                out[relpath] = row[0]
        return out

    def read(self, relpath: str) -> bytes:
        with gzip.open(self._file(relpath), "rb") as fh:
            return fh.read()

    def write(self, relpath: str, raw: bytes) -> FileInfo:
        path = self._file(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                with gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=6, mtime=0) as gz:
                    gz.write(raw)
                fh.flush()
                os.fsync(fh.fileno())  # a power cut must not leave a cut-off file behind a "present" row
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        info = FileInfo(
            relpath, PRESENT, len(raw), path.stat().st_size, hashlib.sha256(raw).hexdigest(), time.time()
        )
        self._record(info)
        return info

    def mark_absent(self, relpath: str) -> None:
        self._record(FileInfo(check_relpath(relpath), ABSENT, fetched_at=time.time()))

    def forget(self, relpath: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE relpath=?", (relpath,))
            self._db.commit()
        self._file(relpath).unlink(missing_ok=True)

    def _record(self, info: FileInfo) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO files(relpath,status,raw_bytes,stored_bytes,sha256,fetched_at) "
                "VALUES(?,?,?,?,?,?)",
                (info.relpath, info.status, info.raw_bytes, info.stored_bytes, info.sha256, info.fetched_at),
            )
            self._db.commit()

    def list(self, prefix: str = "", status: str | None = PRESENT) -> list[FileInfo]:
        sql = "SELECT relpath,status,raw_bytes,stored_bytes,sha256,fetched_at FROM files WHERE relpath LIKE ? ESCAPE '\\'"
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args: list[Any] = [like]
        if status:
            sql += " AND status=?"
            args.append(status)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY relpath", args).fetchall()
        return [FileInfo(*r) for r in rows]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT status,COUNT(*),COALESCE(SUM(raw_bytes),0),COALESCE(SUM(stored_bytes),0) "
                "FROM files GROUP BY status"
            ).fetchall()
        out: dict[str, Any] = {"files": 0, "raw_bytes": 0, "stored_bytes": 0, "absent": 0}
        for status, count, raw, stored in rows:
            if status == PRESENT:
                out.update(files=count, raw_bytes=raw, stored_bytes=stored)
            elif status == ABSENT:
                out["absent"] = count
        return out


# --------------------------------------------------------------------------- the source

#: A bulk fetch stops when this many files in a row failed (upstream down, blocking us, or a portal in the way).
BREAKER_FAILURES = 12
#: Longest pause honoured when upstream asks to slow down (Retry-After); a longer request ends the run.
MAX_PAUSE_SECONDS = 600.0
#: Local errors after which no further file is requested in a bulk fetch (the disk cannot take more).
_FATAL_DISK = frozenset(
    code for code in (errno.ENOSPC, errno.EROFS, errno.EACCES, errno.EPERM, getattr(errno, "EDQUOT", None)) if code
)


def _wait(seconds: float, stop: threading.Event | None) -> None:
    """Sleep, but give up at once with :class:`Cancelled` when ``stop`` is set."""
    if seconds <= 0:
        return
    if stop is None:
        time.sleep(seconds)
    elif stop.wait(seconds):
        raise Cancelled()


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds a Retry-After header asks for (number or HTTP date), if any."""
    value = resp.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def release_base(explorer_url: str) -> str:
    """The release prefix of the explorer URL a page names, e.g. ``https://static.goodfire.ai/neuron-atlas/<id>/``.

    Only an https URL on an allowed host, with a file name below at least one
    directory and without ``.``/``..`` segments, user names or odd ports is accepted.
    """
    try:
        parts = urlsplit(explorer_url)
        port = parts.port
    except ValueError as exc:
        raise UpstreamError(f"unexpected explorer location {explorer_url!r}") from exc
    segments = parts.path.split("/")
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_RELEASE_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or len(segments) < 3
        or segments[0] != ""
        or not segments[-1]
        or any(seg in ("", ".", "..") for seg in segments[1:-1])
    ):
        raise UpstreamError(f"unexpected explorer location {explorer_url!r}")
    return f"https://{parts.hostname}{'/'.join(segments[:-1])}/"


@dataclass(frozen=True)
class FetchResult:
    relpath: str
    #: ``present`` / ``absent`` after the call, or ``error``.
    status: str
    #: True when the network was used.
    fetched: bool = False
    raw_bytes: int = 0
    error: str = ""


class _Lru:
    def __init__(self, size: int):
        self.size = size
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key: str, value: Any) -> None:
        if self.size <= 0:
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def drop(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class AtlasSource:
    """Read-through access to one atlas release."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        json_cache_size: int = 48,
    ):
        self.settings = settings or Settings.from_env()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._owns_client = client is None
        self._client_lock = threading.Lock()
        self._limiter = RateLimiter(self.settings.requests_per_second)
        self._slots = threading.BoundedSemaphore(max(1, self.settings.max_concurrency))
        self._release_url: str | None = None
        self._release_lock = threading.Lock()
        self._store: Store | None = None
        self._json = _Lru(json_cache_size)
        self._inflight: dict[str, threading.Lock] = {}
        self._inflight_lock = threading.Lock()
        #: Index files upstream did not have, remembered for this process only (see _INDEX_PATHS).
        self._absent_now: set[str] = set()
        #: Until this time (monotonic) no request goes out: upstream asked every client to slow down.
        self._pause_until = 0.0
        self._pause_lock = threading.Lock()
        self.network_requests = 0

    # ------------------------------------------------------------------ plumbing

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> AtlasSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def http(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                limits = httpx.Limits(
                    max_connections=max(2, self.settings.max_concurrency * 2),
                    max_keepalive_connections=max(2, self.settings.max_concurrency),
                )
                self._client = httpx.Client(
                    headers={"User-Agent": self.settings.user_agent, "Accept": "application/json, */*"},
                    timeout=self.settings.timeout_seconds,
                    follow_redirects=True,
                    limits=limits,
                )
            return self._client

    # ------------------------------------------------------------------ release

    @property
    def release_url(self) -> str:
        if self._release_url is None:
            with self._release_lock:
                if self._release_url is None:
                    self._release_url = self._resolve_release()
        return self._release_url

    @property
    def release_id(self) -> str:
        return release_id(self.release_url)

    @property
    def store(self) -> Store:
        if self._store is None:
            url = self.release_url
            with self._release_lock:
                if self._store is None:
                    self._store = Store(self.settings.data_dir / release_id(url))
        return self._store

    def explorer_url(self) -> str:
        """Human-facing page of the original atlas."""
        return ATLAS_PAGE_URL

    def _state_file(self) -> Path:
        return self.settings.data_dir / "release.json"

    def _resolve_release(self) -> str:
        s = self.settings
        if s.release_url:
            return s.release_url
        remembered: dict[str, Any] = {}
        try:
            remembered = json.loads(self._state_file().read_text("utf-8"))
        except (OSError, ValueError):
            remembered = {}
        last = remembered.get("release_url")
        if s.offline or not s.discover_release:
            return last or KNOWN_RELEASE_URL
        if last and time.time() - float(remembered.get("checked_at", 0)) < 24 * 3600:
            return last
        try:
            found = self.discover_release()
        except AtlasError:
            return last or KNOWN_RELEASE_URL
        with contextlib.suppress(OSError):
            self._state_file().write_text(
                json.dumps({"release_url": found, "checked_at": time.time()}), "utf-8"
            )
        return found

    def discover_release(self) -> str:
        """Ask goodfire.com which release the public explorer currently embeds."""
        try:
            self._limiter.acquire()
            self.network_requests += 1
            resp = self.http.get(ATLAS_PAGE_URL, headers={"Accept": "text/html"})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"could not load {ATLAS_PAGE_URL}: {exc}") from exc
        if resp.status_code != 200:
            raise UpstreamError(f"{ATLAS_PAGE_URL} answered HTTP {resp.status_code}")
        m = _EXPLORER_ATTR.search(resp.text)
        if not m:
            raise UpstreamError("the atlas page no longer names an explorer URL")
        return release_base(m.group(1))

    # ------------------------------------------------------------------ reading

    def url_for(self, relpath: str) -> str:
        return self.release_url + check_relpath(relpath)

    def status(self, relpath: str) -> str:
        """``present`` / ``absent`` / ``unknown`` in the local mirror (no network)."""
        return self.store.status(relpath)

    def can_fetch(self, remote: bool | None = None) -> bool:
        if self.settings.offline:
            return False
        return self.settings.allow_remote_fetch if remote is None else remote

    def get_bytes(
        self,
        relpath: str,
        *,
        remote: bool | None = None,
        refresh: bool = False,
        stop: threading.Event | None = None,
    ) -> bytes | None:
        """Raw JSON bytes of ``relpath`` or ``None`` when upstream has no such file.

        ``remote`` overrides :attr:`Settings.allow_remote_fetch` for this call;
        ``refresh`` re-downloads even when the file is mirrored; ``stop`` cancels
        waits (back-off, pauses) when it is set.  A damaged stored copy is
        dropped and fetched again.
        """
        check_relpath(relpath)
        if not refresh:
            raw, known = self._stored(relpath)
            if known:
                return raw
        if not self.can_fetch(remote):
            raise NotMirrored(relpath)
        with self._inflight_guard(relpath):
            if not refresh:  # another thread may have fetched it meanwhile
                raw, known = self._stored(relpath)
                if known:
                    return raw
            raw = self._download(relpath, stop)
            store = self.store
            if raw is None:
                if relpath in _INDEX_PATHS:
                    self._absent_now.add(relpath)
                else:
                    store.mark_absent(relpath)
                self._json.drop(relpath)
                return None
            self._absent_now.discard(relpath)
            store.write(relpath, raw)
            self._json.drop(relpath)
            return raw

    def _stored(self, relpath: str) -> tuple[bytes | None, bool]:
        """``(bytes, True)`` for a mirrored file, ``(None, True)`` for a known absent one, else ``(None, False)``."""
        store = self.store
        if relpath in self._absent_now:
            return None, True
        status = store.status(relpath)
        if status == ABSENT and relpath not in _INDEX_PATHS:
            return None, True
        if status == PRESENT:
            try:
                return store.read(relpath), True
            except _DAMAGED:
                store.forget(relpath)  # cut short or overwritten: fetch it again
                self._json.drop(relpath)
        return None, False

    def get_json(self, relpath: str, *, remote: bool | None = None, refresh: bool = False) -> Any:
        """Parsed JSON of ``relpath`` or ``None`` when upstream has no such file."""
        if not refresh:
            cached = self._json.get(relpath)
            if cached is not None:
                return cached
        raw = self.get_bytes(relpath, remote=remote, refresh=refresh)
        if raw is None:
            return None
        try:
            value = parse_json(raw)
        except ValueError as exc:
            self.store.forget(relpath)
            raise UpstreamError(f"{relpath} is not valid JSON: {exc}") from exc
        self._json.put(relpath, value)
        return value

    def get_index(self, name: str, *, remote: bool | None = None, refresh: bool = False) -> Any:
        return self.get_json(INDEX_FILES[name], remote=remote, refresh=refresh)

    def get_pole_file(self, source: str, pid: str, *, remote: bool | None = None) -> Any:
        return self.get_json(pole_path(source, pid), remote=remote)

    def get_neuron_file(self, source: str, did: str, *, remote: bool | None = None) -> Any:
        return self.get_json(neuron_path(source, did), remote=remote)

    # ------------------------------------------------------------------ bulk

    def fetch_many(
        self,
        relpaths: Iterable[str],
        *,
        refresh: bool = False,
        workers: int | None = None,
        on_result: Callable[[FetchResult], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[FetchResult]:
        """Make sure many files are mirrored.  Never raises for a single file.

        This is the explicit mirror path, so it fetches even where
        ``allow_remote_fetch`` forbids read-through (never in offline mode).
        Files that are not tried come back as errors: ``cancelled`` when
        ``should_stop()`` turned true, ``not attempted: …`` when the run stopped
        on its own - after :data:`BREAKER_FAILURES` failures in a row (upstream
        down, blocking us, a portal in the way) or when the disk refuses files.
        An exception in the calling thread (Ctrl-C) cancels everything that has
        not started and is raised again at once.
        """
        todo = list(dict.fromkeys(relpaths))
        known = {} if refresh else self.store.statuses(todo)
        results: list[FetchResult] = []
        stop = threading.Event()
        lock = threading.Lock()
        state: dict[str, Any] = {"failures": 0, "reason": ""}
        online = self.can_fetch(remote=True)

        def halt(reason: str) -> None:
            with lock:
                state["reason"] = state["reason"] or reason
            stop.set()

        def skipped(relpath: str) -> FetchResult:
            reason = state["reason"]
            return FetchResult(relpath, "error", error=f"not attempted: {reason}" if reason else "cancelled")

        def one(relpath: str) -> FetchResult:
            before = known.get(relpath, UNKNOWN)
            if before in (PRESENT, ABSENT) and not refresh:
                return FetchResult(relpath, before)
            if not online:
                return FetchResult(relpath, "error", error="offline mode: not fetched")
            if not stop.is_set() and should_stop and should_stop():
                stop.set()
            if stop.is_set():
                return skipped(relpath)
            try:
                raw = self.get_bytes(relpath, refresh=refresh, remote=True, stop=stop)
            except Cancelled:
                return skipped(relpath)
            except AtlasError as exc:
                failure = str(exc)
            except OSError as exc:
                failure = f"could not store the file: {exc}"
                if exc.errno in _FATAL_DISK:
                    halt(f"the local disk refuses new files ({exc.strerror or exc})")
            except Exception as exc:  # noqa: BLE001 - one odd file must not end the run
                failure = f"{type(exc).__name__}: {exc}"
            else:
                with lock:
                    state["failures"] = 0
                if raw is None:
                    return FetchResult(relpath, ABSENT, fetched=True)
                return FetchResult(relpath, PRESENT, fetched=True, raw_bytes=len(raw))
            with lock:
                state["failures"] += 1
                tripped = state["failures"] >= BREAKER_FAILURES
            if tripped:
                halt(f"{BREAKER_FAILURES} files in a row failed, the last with: {failure}")
            return FetchResult(relpath, "error", error=failure)

        n = max(1, workers or self.settings.max_concurrency)
        pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="atlas-fetch")
        try:
            pending = {pool.submit(one, p) for p in todo}
            while pending:
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    res = fut.result()
                    results.append(res)
                    if on_result:
                        on_result(res)
                if should_stop and not stop.is_set() and should_stop():
                    stop.set()  # also wakes workers that wait for a retry or a pause
        except BaseException:
            stop.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return results

    # ------------------------------------------------------------------ network

    def _inflight_guard(self, relpath: str) -> threading.Lock:
        with self._inflight_lock:
            lock = self._inflight.get(relpath)
            if lock is None:
                lock = self._inflight[relpath] = threading.Lock()
                if len(self._inflight) > 4096:  # keep the table from growing without bound
                    for key in [k for k, v in self._inflight.items() if not v.locked() and k != relpath][:2048]:
                        del self._inflight[key]
        return lock

    def _pause(self, seconds: float) -> None:
        """Hold back every request of this source for ``seconds`` (upstream asked to slow down)."""
        with self._pause_lock:
            self._pause_until = max(self._pause_until, time.monotonic() + seconds)

    def _wait_for_pause(self, stop: threading.Event | None) -> None:
        while True:
            with self._pause_lock:
                left = self._pause_until - time.monotonic()
            if left <= 0:
                return
            _wait(min(left, 5.0), stop)

    def _download(self, relpath: str, stop: threading.Event | None = None) -> bytes | None:
        url = self.url_for(relpath)
        retries = max(0, self.settings.max_retries)
        last = ""
        for attempt in range(retries + 1):
            if attempt:
                _wait(min(30.0, 0.75 * 2 ** (attempt - 1)), stop)
            self._wait_for_pause(stop)
            if stop is not None and stop.is_set():
                raise Cancelled()
            with self._slots:
                self._limiter.acquire()
                self.network_requests += 1
                try:
                    resp = self.http.get(url)
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}: {exc}"
                    continue
            code = resp.status_code
            if code == 200:
                if not _json_like(resp.content) or "html" in resp.headers.get("content-type", "").lower():
                    raise UpstreamError(
                        f"{url} answered 200 without JSON ({resp.headers.get('content-type', 'no content type')}); "
                        "a proxy or login portal in the way?"
                    )
                return resp.content
            if code in (403, 404) and _is_s3_missing(resp):
                return None
            if code in (408, 425, 429, 500, 502, 503, 504):
                last = f"HTTP {code}"
                if code in (429, 503):  # a request to every client of ours: pause them all
                    delay = _retry_after(resp)
                    if delay is not None and delay > MAX_PAUSE_SECONDS:
                        raise UpstreamError(f"{url}: upstream asks to wait {delay:.0f} s (HTTP {code}); try again later")
                    self._pause(delay if delay is not None else min(60.0, 2.0 * 2**attempt))
                continue
            raise UpstreamError(f"{url} answered HTTP {code}")
        raise UpstreamError(f"{url} failed after {retries + 1} attempts ({last})")


_SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F]")


def parse_json(raw: bytes) -> Any:
    """Parse upstream JSON so that every string in it can be written out again as UTF-8.

    The corpus text is raw web data cut into tokens.  A token boundary can fall
    inside a character, and files that escape non-ASCII text can then carry
    *half* of a surrogate pair - a string Python accepts but cannot encode.
    Such halves (and undecodable bytes) are replaced by U+FFFD.
    """
    value = json.loads(raw.decode("utf-8", errors="replace"))
    if _SURROGATE_ESCAPE.search(raw):
        value = _without_lone_surrogates(value)
    return value


def _without_lone_surrogates(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        return value
    if isinstance(value, list):
        return [_without_lone_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {_without_lone_surrogates(k): _without_lone_surrogates(v) for k, v in value.items()}
    return value


def _is_s3_missing(resp: httpx.Response) -> bool:
    """The release bucket answers 403 *AccessDenied* (404 *NoSuchKey* where listing is allowed) for missing keys.

    A 403 or 404 from anything else (a proxy, a WAF, a portal, a CDN error page)
    must not be mistaken for "absent": that mark is kept.
    """
    if "amazons3" not in resp.headers.get("server", "").lower():
        return False
    head = resp.content[:2048]
    return b"<Code>AccessDenied</Code>" in head or b"<Code>NoSuchKey</Code>" in head
