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
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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

_SAFE_RELPATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.json$")
_EXPLORER_ATTR = re.compile(r'data-explorer="([^"]+)"')


def pole_path(source: str, pid: str) -> str:
    return POLE_FILES[source].format(pid=pid)


def neuron_path(source: str, did: str) -> str:
    return NEURON_FILES[source].format(did=did)


def check_relpath(relpath: str) -> str:
    """Reject anything that is not a plain relative JSON path inside the release."""
    if not _SAFE_RELPATH.match(relpath) or ".." in relpath or "//" in relpath:
        raise ValueError(f"illegal atlas path: {relpath!r}")
    return relpath


# --------------------------------------------------------------------------- errors


class AtlasError(Exception):
    """Base class of everything this package raises on purpose."""


class UpstreamError(AtlasError):
    """The upstream server could not be reached or answered unexpectedly."""


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


class Store:
    """Gzip-compressed files of one release plus a small SQLite manifest."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.root / "manifest.sqlite", check_same_thread=False, timeout=30)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS files(
                       relpath TEXT PRIMARY KEY,
                       status TEXT NOT NULL,
                       raw_bytes INTEGER NOT NULL DEFAULT 0,
                       stored_bytes INTEGER NOT NULL DEFAULT 0,
                       sha256 TEXT NOT NULL DEFAULT '',
                       fetched_at REAL NOT NULL DEFAULT 0)"""
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _file(self, relpath: str) -> Path:
        return self.files_dir / (check_relpath(relpath) + ".gz")

    def info(self, relpath: str) -> FileInfo:
        with self._lock:
            row = self._db.execute(
                "SELECT relpath,status,raw_bytes,stored_bytes,sha256,fetched_at FROM files WHERE relpath=?",
                (relpath,),
            ).fetchone()
        if row is None:
            return FileInfo(relpath, UNKNOWN)
        info = FileInfo(*row)
        if info.status == PRESENT and not self._file(relpath).exists():
            return FileInfo(relpath, UNKNOWN)  # deleted behind our back
        return info

    def status(self, relpath: str) -> str:
        return self.info(relpath).status

    def statuses(self, relpaths: Iterable[str]) -> dict[str, str]:
        """Manifest status of many files at once (does not stat the files)."""
        wanted = list(relpaths)
        out = dict.fromkeys(wanted, UNKNOWN)
        with self._lock:
            for i in range(0, len(wanted), 500):
                chunk = wanted[i : i + 500]
                marks = ",".join("?" * len(chunk))
                for relpath, status in self._db.execute(
                    f"SELECT relpath,status FROM files WHERE relpath IN ({marks})", chunk
                ):
                    out[relpath] = status
        return out

    def read(self, relpath: str) -> bytes:
        with gzip.open(self._file(relpath), "rb") as fh:
            return fh.read()

    def write(self, relpath: str, raw: bytes) -> FileInfo:
        path = self._file(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(raw)
        os.replace(tmp, path)
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
        parts = urlsplit(m.group(1))
        if parts.scheme != "https" or parts.hostname not in ALLOWED_RELEASE_HOSTS:
            raise UpstreamError(f"unexpected explorer location {m.group(1)!r}")
        base = m.group(1).rsplit("/", 1)[0] + "/"
        return base

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

    def get_bytes(self, relpath: str, *, remote: bool | None = None, refresh: bool = False) -> bytes | None:
        """Raw JSON bytes of ``relpath`` or ``None`` when upstream has no such file.

        ``remote`` overrides :attr:`Settings.allow_remote_fetch` for this call;
        ``refresh`` re-downloads even when the file is mirrored.
        """
        check_relpath(relpath)
        store = self.store
        if not refresh:
            status = store.status(relpath)
            if status == PRESENT:
                return store.read(relpath)
            if status == ABSENT:
                return None
        if not self.can_fetch(remote):
            raise NotMirrored(relpath)
        with self._inflight_guard(relpath):
            if not refresh:  # another thread may have fetched it meanwhile
                status = store.status(relpath)
                if status == PRESENT:
                    return store.read(relpath)
                if status == ABSENT:
                    return None
            raw = self._download(relpath)
            if raw is None:
                store.mark_absent(relpath)
                self._json.drop(relpath)
                return None
            store.write(relpath, raw)
            self._json.drop(relpath)
            return raw

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
            value = json.loads(raw)
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
        """Make sure many files are mirrored.  Never raises for a single file."""
        todo = list(dict.fromkeys(relpaths))
        results: list[FetchResult] = []
        known = {} if refresh else self.store.statuses(todo)

        def one(relpath: str) -> FetchResult:
            if should_stop and should_stop():
                return FetchResult(relpath, "error", error="cancelled")
            before = known.get(relpath, UNKNOWN)
            if before in (PRESENT, ABSENT) and not refresh:
                return FetchResult(relpath, before)
            try:
                raw = self.get_bytes(relpath, refresh=refresh)
            except AtlasError as exc:
                return FetchResult(relpath, "error", error=str(exc))
            if raw is None:
                return FetchResult(relpath, ABSENT, fetched=True)
            return FetchResult(relpath, PRESENT, fetched=True, raw_bytes=len(raw))

        n = max(1, workers or self.settings.max_concurrency)
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="atlas-fetch") as pool:
            futures = [pool.submit(one, p) for p in todo]
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                if on_result:
                    on_result(res)
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

    def _download(self, relpath: str) -> bytes | None:
        url = self.url_for(relpath)
        retries = max(0, self.settings.max_retries)
        last = ""
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(min(30.0, 0.75 * 2 ** (attempt - 1)))
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
                return resp.content
            if code == 404 or (code == 403 and _is_s3_missing(resp)):
                return None
            if code in (408, 425, 429, 500, 502, 503, 504):
                last = f"HTTP {code}"
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    time.sleep(min(60.0, float(retry_after)))
                continue
            raise UpstreamError(f"{url} answered HTTP {code}")
        raise UpstreamError(f"{url} failed after {retries + 1} attempts ({last})")


def _is_s3_missing(resp: httpx.Response) -> bool:
    """The release bucket answers 403 *AccessDenied* for keys that do not exist.

    A 403 from anything else (a proxy, a WAF) must not be mistaken for "absent".
    """
    if "amazons3" not in resp.headers.get("server", "").lower():
        return False
    return b"<Code>AccessDenied</Code>" in resp.content[:2048]
