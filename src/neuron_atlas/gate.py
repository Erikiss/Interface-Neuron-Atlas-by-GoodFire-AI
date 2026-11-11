"""The front door of the HTTP application.

One small ASGI middleware does, for every request and before any route or
request body is touched:

* **Host check** - only host names this server is meant to answer to
  (DNS-rebinding protection for a server on ``localhost``).
* **API key** - on ``/api/``, ``/mcp`` and ``/p/`` when keys are configured.
* **Rate limit** - per client (a valid key, otherwise the client address).
* **Body size** - requests larger than the limit are refused.
* ``/mcp`` without a trailing slash is served in place (no redirect).

Doing this in one place, below the routing, means a mounted application or a
``--root-path`` cannot slip past it.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from .config import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: Host names a server answers to when ATLAS_ALLOWED_HOSTS is not set: this machine only.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
#: Paths that carry atlas data and therefore need the key (when keys are set) and count against the rate.
PROTECTED = ("/api/", "/mcp", "/p/")
#: Largest request body accepted (batch requests included).
MAX_BODY_BYTES = 2 * 1024 * 1024
#: Sent with every response: no URL (which may carry ?api_key=) leaks to other sites; no type sniffing.
SECURITY_HEADERS = ((b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff"))


class ClientLimiter:
    """Per-client sliding budget (requests per minute); in-memory, per process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> tuple[bool, int]:
        if self.per_minute <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(client, ()) if now - t < 60.0]
            if len(hits) >= self.per_minute:
                self._hits[client] = hits
                return False, max(1, int(60.0 - (now - hits[0])) + 1)
            hits.append(now)
            self._hits[client] = hits
            if len(self._hits) > 20_000:  # forget idle clients
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 60.0]:
                    del self._hits[key]
        return True, 0


def host_name(host_header: str) -> str:
    """``Example.org:8000`` -> ``example.org``; ``[::1]:8000`` -> ``::1``."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class _TooLarge(Exception):
    pass


class RedactKeys(logging.Filter):
    """Masks ``api_key=…`` in log lines (uvicorn's access log prints the full request path)."""

    _KEY = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._KEY.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = self._KEY.sub(r"\1***", record.msg)
        return True


def redact_access_log() -> None:
    """Install :class:`RedactKeys` on uvicorn's access logger (once per process)."""
    log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactKeys) for f in log.filters):
        log.addFilter(RedactKeys())


class Gate:
    def __init__(self, app: ASGIApp, settings: Settings, *, max_body: int = MAX_BODY_BYTES):
        self.app = app
        self.settings = settings
        self.max_body = max_body
        self.limiter = ClientLimiter(settings.client_rate_per_minute)
        hosts = [host_name(h) for h in (*LOCAL_HOSTS, *settings.allowed_hosts) if h]
        self.any_host = "*" in settings.allowed_hosts
        self.hosts = frozenset(hosts)
        self.keys = tuple(k.encode() for k in settings.api_keys)

    # -------------------------------------------------------------- helpers

    def key_ok(self, key: str) -> bool:
        presented = key.encode()
        return bool(key) and any(hmac.compare_digest(presented, k) for k in self.keys)

    @staticmethod
    def presented_key(scope: Scope) -> str:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if headers.get("x-api-key"):
            return headers["x-api-key"].strip()
        from urllib.parse import parse_qs

        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        return (query.get("api_key") or [""])[0]

    @staticmethod
    async def reply(
        send: Send, status: int, error: str, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps({"error": error, "detail": detail}).encode()
        raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]
        raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        await send({"type": "http.response.start", "status": status, "headers": raw})
        await send({"type": "http.response.body", "body": body})

    # -------------------------------------------------------------- the gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}

        root = scope.get("root_path") or ""
        path = scope.get("path") or ""
        # As Starlette routes: the root path counts only when a "/" (or the end) follows it.
        if root and (path == root or path.startswith(root.rstrip("/") + "/")):
            relative = path[len(root.rstrip("/")) :] or "/"
        else:
            relative = path

        # The liveness probe answers to any name: it carries no data, and probes often use the bare address.
        if (
            not self.any_host
            and relative != "/healthz"
            and host_name(headers.get("host", "")) not in self.hosts
        ):
            await self.reply(
                send,
                421,
                "misdirected",
                "this server does not answer to that host name (see ATLAS_ALLOWED_HOSTS)",
            )
            return

        if relative == "/mcp":
            # Serve the MCP endpoint in place instead of redirecting POST /mcp to /mcp/.
            prefix = path[: len(path) - len(relative)]
            scope = dict(scope, path=prefix + "/mcp/", raw_path=(prefix + "/mcp/").encode())
            relative = "/mcp/"

        if relative.startswith(PROTECTED) and scope.get("method") != "OPTIONS":
            key = self.presented_key(scope)
            valid = self.key_ok(key)
            # Count every request, refused ones too, so that guessing keys is as slow as using one.
            client = scope.get("client")
            who = f"key:{key}" if valid else (client[0] if client else "unknown")
            ok, wait = self.limiter.allow(who)
            if not ok:
                await self.reply(
                    send, 429, "rate_limited", "too many requests; slow down", {"Retry-After": str(wait)}
                )
                return
            if self.keys and not valid:
                await self.reply(
                    send, 401, "unauthorized", "missing or wrong API key (send it as 'X-API-Key' or 'Authorization: Bearer …')",
                    {"WWW-Authenticate": "Bearer"},
                )  # fmt: skip
                return

        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > self.max_body:
            await self.reply(send, 413, "too_large", f"request body larger than {self.max_body:,} bytes")
            return

        received = 0
        started = False
        replied = False

        async def limited_receive() -> Message:
            nonlocal received, replied
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_body:
                    # Answer here: the framework may catch the exception below and answer 400 itself.
                    if not started and not replied:
                        replied = True
                        await self.reply(
                            send, 413, "too_large", f"request body larger than {self.max_body:,} bytes"
                        )
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if replied:
                return  # the 413 is out; whatever the application answers to the cut-off body is dropped
            if message.get("type") == "http.response.start":
                started = True
                present = {k.lower() for k, _v in message.get("headers") or []}
                extra = [h for h in SECURITY_HEADERS if h[0] not in present]
                if extra:
                    message = dict(message, headers=[*(message.get("headers") or []), *extra])
            await send(message)

        # _TooLarge was answered in limited_receive (or the response had begun and ends incomplete).
        with contextlib.suppress(_TooLarge):
            await self.app(scope, limited_receive, tracking_send)
