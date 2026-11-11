"""Shared fixtures: a synthetic atlas release served by an in-process HTTP transport.

No test touches the network.  ``requests`` records every URL the code asked
for, which lets tests assert how polite the client is.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from neuron_atlas.config import Settings
from neuron_atlas.service import Atlas
from neuron_atlas.upstream import AtlasSource

from . import fakeatlas


class FakeUpstream:
    """Answers like the real CDN: JSON files, and S3's 403 "AccessDenied" for missing keys."""

    def __init__(self) -> None:
        self.files = fakeatlas.build_release()
        self.requests: list[str] = []
        self.fail_next: list[int] = []  # status codes to answer with before behaving normally

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text="temporary trouble")
        if url == fakeatlas.EXPLORER_PAGE:
            return httpx.Response(200, text=fakeatlas.EXPLORER_HTML)
        if url.startswith(fakeatlas.RELEASE_URL):
            rel = url[len(fakeatlas.RELEASE_URL) :]
            if rel in self.files:
                return httpx.Response(200, content=self.files[rel], headers={"content-type": "application/json"})
            return httpx.Response(
                403,
                content=b"<?xml version='1.0'?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>",
                headers={"server": "AmazonS3", "content-type": "application/xml"},
            )
        return httpx.Response(404, text="not part of the fake upstream")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True)

    def fetched(self, suffix: str) -> int:
        return sum(1 for url in self.requests if url.endswith(suffix))


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        release_url=fakeatlas.RELEASE_URL,
        requests_per_second=0,  # no politeness delay in tests
        max_concurrency=4,
        max_retries=2,
    )


@pytest.fixture
def source(settings: Settings, upstream: FakeUpstream) -> Iterator[AtlasSource]:
    src = AtlasSource(settings, client=upstream.client())
    yield src
    src.close()


@pytest.fixture
def atlas(source: AtlasSource) -> Iterator[Atlas]:
    a = Atlas(source=source)
    yield a
    a.close()


@pytest.fixture
def offline_atlas(settings: Settings, upstream: FakeUpstream) -> Iterator[Atlas]:
    """An atlas whose mirror holds only the index files and that may not fetch anything else."""
    with AtlasSource(settings, client=upstream.client()) as warm:
        from neuron_atlas.upstream import INDEX_FILES

        warm.fetch_many(INDEX_FILES.values())
    src = AtlasSource(settings.with_overrides(offline=True), client=upstream.client())
    a = Atlas(source=src)
    yield a
    a.close()
