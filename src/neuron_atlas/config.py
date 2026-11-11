"""Runtime configuration.

Everything can be set through environment variables (prefix ``ATLAS_``) or by
constructing :class:`Settings` directly.  Nothing here talks to the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

#: Page on goodfire.com that embeds the explorer; the current release is read from it.
ATLAS_PAGE_URL = "https://www.goodfire.com/research/public_neuron_atlas"

#: Release that was current when this code was written.  Used when discovery is
#: switched off or fails (the release prefix is immutable upstream).
KNOWN_RELEASE_URL = "https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/"

#: Hosts a release may be served from.  Discovery refuses anything else, so a
#: tampered page cannot point the client at an arbitrary server.
ALLOWED_RELEASE_HOSTS = ("static.goodfire.ai",)

DEFAULT_USER_AGENT = "neuron-atlas-interface/0.1 (personal research tool)"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"ATLAS_{name}")
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value is not None else default


def default_data_dir() -> Path:
    """``$ATLAS_DATA_DIR``, else the user's data directory (``~/.local/share/neuron-atlas``).

    A data directory rather than a cache directory on purpose: a mirror took
    thousands of polite requests to build and should not be swept away by a
    cache cleaner.
    """
    explicit = _env("DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "neuron-atlas"


@dataclass(frozen=True)
class Settings:
    """All tunables of the client, the mirror and the server."""

    #: Where the local mirror, the manifest and the search index live.
    data_dir: Path = field(default_factory=default_data_dir)

    #: Base URL of the release (ends with ``/``).  ``None`` = discover it from
    #: :data:`ATLAS_PAGE_URL`, falling back to :data:`KNOWN_RELEASE_URL`.
    release_url: str | None = None
    #: Skip discovery and use :data:`KNOWN_RELEASE_URL` when ``release_url`` is unset.
    discover_release: bool = True

    #: Never touch the network; serve only what is already mirrored.
    offline: bool = False

    #: Politeness: sustained requests per second towards the upstream CDN and
    #: the number of requests that may be in flight at once.
    requests_per_second: float = 4.0
    max_concurrency: int = 4
    timeout_seconds: float = 60.0
    max_retries: int = 4
    user_agent: str = DEFAULT_USER_AGENT

    #: Text embedding behind "similar" and the maps: ``lsa`` (built in), ``fastembed``,
    #: ``fastembed:multilingual`` or ``fastembed:<model name>`` (optional package).
    embedder: str = "lsa"

    #: Server side.
    api_keys: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = ()
    #: Requests per minute allowed per client by the API itself (0 = unlimited).
    client_rate_per_minute: int = 0
    #: Largest ``limit`` a single search page may ask for.
    max_page_size: int = 500
    #: Largest number of queries / ids in one batch call.
    max_batch_size: int = 200
    #: May API callers trigger upstream fetches for poles that are not mirrored yet?
    allow_remote_fetch: bool = True
    #: May API / MCP callers start mirror jobs, and how many poles may one such job cover?
    #: (The command line is not limited.)
    allow_mirror: bool = True
    max_mirror_poles: int = 500
    #: Host names the MCP endpoint answers to (DNS-rebinding protection).  Empty = this
    #: machine only (localhost, 127.0.0.1, ::1); ``*`` = any host.
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        keys = tuple(k.strip() for k in (_env("API_KEYS") or "").split(",") if k.strip())
        cors = tuple(o.strip() for o in (_env("CORS_ORIGINS") or "").split(",") if o.strip())
        return cls(
            data_dir=default_data_dir(),
            release_url=_normalise_release(_env("RELEASE_URL")),
            discover_release=_env_bool("DISCOVER_RELEASE", True),
            offline=_env_bool("OFFLINE", False),
            requests_per_second=_env_float("REQUESTS_PER_SECOND", 4.0),
            max_concurrency=_env_int("MAX_CONCURRENCY", 4),
            timeout_seconds=_env_float("TIMEOUT_SECONDS", 60.0),
            max_retries=_env_int("MAX_RETRIES", 4),
            user_agent=_env("USER_AGENT", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
            embedder=_env("EMBEDDER", "lsa") or "lsa",
            api_keys=keys,
            cors_origins=cors,
            client_rate_per_minute=_env_int("CLIENT_RATE_PER_MINUTE", 0),
            max_page_size=_env_int("MAX_PAGE_SIZE", 500),
            max_batch_size=_env_int("MAX_BATCH_SIZE", 200),
            allow_remote_fetch=_env_bool("ALLOW_REMOTE_FETCH", True),
            allow_mirror=_env_bool("ALLOW_MIRROR", True),
            max_mirror_poles=_env_int("MAX_MIRROR_POLES", 500),
            allowed_hosts=tuple(h.strip() for h in (_env("ALLOWED_HOSTS") or "").split(",") if h.strip()),
        )

    def with_overrides(self, **changes: object) -> Settings:
        """Copy with the given fields replaced; ``None`` values are ignored."""
        clean = {k: v for k, v in changes.items() if v is not None}
        if "data_dir" in clean:
            clean["data_dir"] = Path(str(clean["data_dir"])).expanduser()
        if "release_url" in clean:
            clean["release_url"] = _normalise_release(str(clean["release_url"]))
        return replace(self, **clean)  # type: ignore[arg-type]


def _normalise_release(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    # Accept the explorer page itself (…/public_neuron_atlas-sample-3000-v2.html).
    if url.endswith(".html"):
        url = url.rsplit("/", 1)[0]
    return url.rstrip("/") + "/"


def release_id(release_url: str) -> str:
    """Stable directory name for a release, e.g. ``2026-11-09-082204``."""
    tail = release_url.rstrip("/").rsplit("/", 1)[-1]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in tail)
    return safe or "release"
