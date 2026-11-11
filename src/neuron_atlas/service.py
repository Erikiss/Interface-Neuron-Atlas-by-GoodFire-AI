"""The one object everything else talks to.

:class:`Atlas` wires the source, the catalog, the search index, the semantic
index, the detail builder and the mirror together.  The REST API, the MCP
server and the command line are thin layers over its methods, so all three
behave identically.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from .catalog import Catalog
from .config import Settings
from .details import DEFAULT_INCLUDES, Details
from .ids import parse_reference
from .mirror import (
    MirrorJob,
    MirrorJobs,
    MirrorPlan,
    MirrorReport,
    make_plan,
    mirrored_sources,
    run_plan,
)
from .models import (
    BatchPolesResponse,
    MirrorRequest,
    NeuronDetail,
    PoleDetail,
    SearchQuery,
    SearchResponse,
    Selection,
    SimilarItem,
    SimilarResponse,
)
from .search import SearchIndex
from .semantic import SemanticIndex, make_embedder
from .upstream import AtlasSource, neuron_path

#: Most hits one batch search returns over all its queries.
MAX_BATCH_HITS = 2000
#: Most pole documents one batch request returns.
MAX_BATCH_POLES = 50


class NotFound(KeyError):
    """No such neuron or pole in this release."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what

    def __str__(self) -> str:
        return f"no such neuron or pole in this release: {self.what}"


class Atlas:
    def __init__(self, settings: Settings | None = None, *, source: AtlasSource | None = None):
        self.settings = settings or (source.settings if source else Settings.from_env())
        self.source = source or AtlasSource(self.settings)
        self._lock = threading.RLock()
        self._catalog: Catalog | None = None
        self._search: SearchIndex | None = None
        self._semantic: SemanticIndex | None = None
        self._details: Details | None = None
        self._jobs: MirrorJobs | None = None
        #: Set when the configured embedder could not be used and LSA stepped in.
        self.embedder_note: str | None = None
        self._fetch_slots = threading.BoundedSemaphore(max(2, self.settings.max_concurrency * 2))
        self.started_at = time.time()

    def close(self) -> None:
        # Order matters: background downloads first (they write to the index and the store).
        if self._jobs is not None:
            self._jobs.shutdown()
        if self._search is not None:
            self._search.close()
        self.source.close()

    def stop_jobs(self) -> None:
        """Cancel running and queued downloads (the Atlas stays usable)."""
        if self._jobs is not None:
            self._jobs.cancel_all()

    def __enter__(self) -> Atlas:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ parts (lazy)

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            with self._lock:
                if self._catalog is None:
                    self._catalog = Catalog.load(self.source)
        return self._catalog

    @property
    def semantic(self) -> SemanticIndex:
        if self._semantic is None:
            with self._lock:
                if self._semantic is None:
                    embedder, note = make_embedder(
                        self.settings.embedder,
                        model_dir=self.settings.data_dir / "models",
                        offline=self.settings.offline,
                    )
                    self.embedder_note = note
                    self._semantic = SemanticIndex(
                        self.catalog, cache_dir=self.source.store.root, embedder=embedder
                    )
        return self._semantic

    @property
    def search_index(self) -> SearchIndex:
        if self._search is None:
            with self._lock:
                if self._search is None:
                    catalog = self.catalog
                    self._search = SearchIndex(
                        catalog,
                        self.source.store.root / "search.sqlite",
                        mirrored=lambda pids: mirrored_sources(catalog, self.source, pids),
                        semantic=self.semantic,
                        allow_regex=self.settings.allow_regex,
                    )
        return self._search

    @property
    def details(self) -> Details:
        if self._details is None:
            with self._lock:
                if self._details is None:
                    self._details = Details(self.source, self.catalog, self.search_index)
        return self._details

    @property
    def jobs(self) -> MirrorJobs:
        if self._jobs is None:
            with self._lock:
                if self._jobs is None:
                    self._jobs = MirrorJobs(self.source)
        return self._jobs

    def warm(self, semantic: bool = False) -> None:
        """Build what the first request would otherwise wait for."""
        _ = self.search_index
        if semantic:
            self.semantic.ensure()

    # ------------------------------------------------------------------ ids

    def resolve_pid(self, ref: str, default_pole: str | None = None) -> str:
        parsed = parse_reference(ref)
        if not parsed:
            raise NotFound(ref)
        nid, pole = parsed
        pole = pole or default_pole
        if pole is None:
            known = self.catalog.poles_of(nid.did)
            if not known:
                raise NotFound(ref)
            if len(known) == 1:
                return known[0].pid
            raise ValueError(f"{ref!r} names a neuron; add the pole, e.g. {nid.did}__pos or {nid.did}__neg")
        pid = nid.pid(pole)
        if pid not in self.catalog.poles:
            raise NotFound(ref)
        return pid

    def resolve_did(self, ref: str) -> str:
        parsed = parse_reference(ref)
        if not parsed or parsed[0].did not in self.catalog.neurons:
            raise NotFound(ref)
        return parsed[0].did

    def expand(self, refs: Iterable[str]) -> tuple[list[str], list[str]]:
        """Free-form references -> (pole ids found, references not found)."""
        found: list[str] = []
        missing: list[str] = []
        for ref in refs:
            parsed = parse_reference(ref)
            if not parsed:
                missing.append(ref)
                continue
            nid, pole = parsed
            pids = [nid.pid(pole)] if pole else [p.pid for p in self.catalog.poles_of(nid.did)]
            pids = [pid for pid in pids if pid in self.catalog.poles]
            if pids:
                found.extend(pids)
            else:
                missing.append(ref)
        return list(dict.fromkeys(found)), missing

    # ------------------------------------------------------------------ search

    def _clamp(self, query: SearchQuery) -> SearchQuery:
        if query.limit > self.settings.max_page_size:
            query = query.model_copy(update={"limit": self.settings.max_page_size})
        return query

    def search(
        self, query: SearchQuery | dict[str, Any] | str, *, group_by: str | None = None
    ) -> SearchResponse:
        if isinstance(query, str):
            query = SearchQuery(q=query)
        elif isinstance(query, dict):
            query = SearchQuery.model_validate(query)
        return self.search_index.search(self._clamp(query), group_by=group_by)

    def batch_search(self, queries: Iterable[SearchQuery]) -> list[SearchResponse]:
        """Several searches at once.  All queries together return at most :data:`MAX_BATCH_HITS` hits."""
        queries = list(queries)
        if len(queries) > self.settings.max_batch_size:
            raise ValueError(f"at most {self.settings.max_batch_size} queries per batch")
        share = max(1, MAX_BATCH_HITS // max(1, len(queries)))
        out = []
        for q in queries:
            limited = q.limit > share
            res = self.search(q.model_copy(update={"limit": min(q.limit, share)}) if limited else q)
            if limited:
                res.notes.append(
                    f"limit lowered to {share} (a batch returns at most {MAX_BATCH_HITS:,} hits in all)"
                )
            out.append(res)
        return out

    def iter_search(self, query: SearchQuery, page: int = 500) -> Iterator[Any]:
        """Every hit of a search, paging internally (for exports and selections)."""
        offset = 0
        page = min(page, self.settings.max_page_size)
        while True:
            res = self.search_index.search(
                query.model_copy(update={"limit": page, "offset": offset, "facets": False}), group_by="pole"
            )
            items = res.hits or res.neurons or res.modes
            yield from items
            if res.next_offset is None:
                return
            offset = res.next_offset

    # ------------------------------------------------------------------ details

    def pole(
        self,
        ref: str,
        include: Iterable[str] = DEFAULT_INCLUDES,
        *,
        max_examples: int = 3,
        remote: bool | None = None,
    ) -> PoleDetail:
        pid = self.resolve_pid(ref)
        include = list(include)
        if remote is not False and self.source.can_fetch(remote) and self._needs_fetch(pid):
            # Waiting for the polite upstream rate ties up a worker thread; never let more than a few
            # requests do that at once, so searches and other requests stay responsive.
            if not self._fetch_slots.acquire(blocking=False):
                detail = self.details.pole(pid, include, max_examples=max_examples, remote=False)
                detail.notes.append(
                    "The server is busy fetching other poles; this answer shows what is downloaded already. "
                    "Ask again in a moment for the rest."
                )
                return detail
            try:
                detail = self.details.pole(pid, include, max_examples=max_examples, remote=remote)
            finally:
                self._fetch_slots.release()
        else:
            detail = self.details.pole(pid, include, max_examples=max_examples, remote=remote)
        self._index_quietly(pid)
        return detail

    def _needs_fetch(self, pid: str) -> bool:
        from .mirror import files_of_pole
        from .upstream import ABSENT, PRESENT

        paths = list(files_of_pole(self.catalog.poles[pid]).values())
        return any(status not in (PRESENT, ABSENT) for status in self.source.store.statuses(paths).values())

    def neuron(self, ref: str, include: Iterable[str] = (), *, remote: bool | None = None) -> NeuronDetail:
        return self.details.neuron(self.resolve_did(ref), include, remote=remote)

    def poles(
        self, refs: Iterable[str], include: Iterable[str] = DEFAULT_INCLUDES, *, max_examples: int = 2
    ) -> BatchPolesResponse:
        started = time.perf_counter()
        refs = list(refs)
        if len(refs) > self.settings.max_batch_size:
            raise ValueError(f"at most {self.settings.max_batch_size} ids per batch")
        pids, missing = self.expand(refs)
        out = BatchPolesResponse(not_found=missing)
        if len(pids) > MAX_BATCH_POLES:
            out.notes.append(
                f"only the first {MAX_BATCH_POLES} of {len(pids)} poles were read; ask for the rest separately"
            )
            pids = pids[:MAX_BATCH_POLES]
        for pid in pids:  # through pole(), so a batch shares the same few upstream fetch slots
            out.poles.append(self.pole(pid, include, max_examples=max_examples))
        out.took_ms = round((time.perf_counter() - started) * 1000, 2)
        return out

    def raw(self, kind: str, ref: str, *, remote: bool | None = None) -> Any:
        ident = self.resolve_did(ref) if kind.startswith("circuit") else self.resolve_pid(ref)
        return self.details.raw(kind, ident, remote=remote)

    def _index_quietly(self, pid: str) -> None:
        """Make freshly fetched texts searchable; never let that break the request."""
        with contextlib.suppress(Exception):
            self.details.index_deep(pid)

    # ------------------------------------------------------------------ similarity

    def similar(
        self,
        *,
        pole: str | None = None,
        mode: int | None = None,
        text: str | None = None,
        top: int = 20,
    ) -> SimilarResponse:
        sem = self.semantic.ensure()
        method = sem.embedder.name
        items: list[SimilarItem] = []
        notes: list[str] = []

        def item(pid: str, score: float, mode_index: int | None = None, label: str = "") -> SimilarItem:
            p = self.catalog.poles[pid]
            return SimilarItem(
                pid=pid,
                display=p.display,
                score=round(score, 4),
                mode_index=mode_index,
                label=label,
                description=p.description,
                category=p.category,  # type: ignore[arg-type]
                layer=p.layer,
                atlas_url=p.atlas_url(),
            )

        if text:
            for pid, mode_index, score, label in sem.query(text, top=top):
                items.append(item(pid, score, mode_index, label))
            if not items:
                notes.append(
                    "None of the words occur in the atlas vocabulary (labels are written in English)."
                )
            return SimilarResponse(anchor=text, by="text", method=method, items=items, notes=notes)
        if not pole:
            raise ValueError("give a pole or a text")
        pid = self.resolve_pid(pole)
        if mode is not None:
            for other, mode_index, score, label in sem.similar_modes(pid, mode, top=top):
                items.append(item(other, score, mode_index, label))
            if not items:
                notes.append(f"{pid} has no mode {mode} with a label.")
            return SimilarResponse(
                anchor=f"{pid} mode {mode}", by="mode", method=method, items=items, notes=notes
            )
        for other, score in sem.similar_poles(pid, top=top):
            items.append(item(other, score))
        if not items:
            notes.append(f"{pid} has no description to compare.")
        return SimilarResponse(anchor=pid, by="pole", method=method, items=items, notes=notes)

    # ------------------------------------------------------------------ selections and mirroring

    def select(self, selection: Selection) -> list[str]:
        """Pole ids a :class:`Selection` stands for, in a stable order."""
        catalog = self.catalog
        out: dict[str, None] = {}
        if selection.all:
            out.update(dict.fromkeys(catalog.poles))
        if selection.public_sample:
            out.update(dict.fromkeys(p.pid for p in catalog.iter_poles() if p.in_public_sample))
        if selection.ids:
            found, _missing = self.expand(selection.ids)
            out.update(dict.fromkeys(found))
        if selection.query is not None:
            query = selection.query.model_copy(update={"snippets": False})
            out.update(dict.fromkeys(hit.pid for hit in self.iter_search(query)))
        if selection.similar_to_text:
            for pid, _mode, score, _label in self.semantic.query(
                selection.similar_to_text, top=selection.top
            ):
                if selection.min_score is None or score >= selection.min_score:
                    out[pid] = None
        if selection.similar_to_pole:
            anchor = self.resolve_pid(selection.similar_to_pole)
            out[anchor] = None
            for pid, score in self.semantic.similar_poles(anchor, top=selection.top):
                if selection.min_score is None or score >= selection.min_score:
                    out[pid] = None
        pids = list(out)
        if selection.neighbours:
            pids = self._with_circuit_neighbours(pids, selection.neighbours)
        if selection.max_poles is not None:
            pids = pids[: selection.max_poles]
        return pids

    def _with_circuit_neighbours(self, pids: list[str], steps: int) -> list[str]:
        """Add poles of neurons that the circuit files of the selection name (mirrored files only)."""
        from .geometry.graph import neuron_links

        catalog = self.catalog
        seen = dict.fromkeys(pids)
        frontier = list(dict.fromkeys(pid.rsplit("__", 1)[0] for pid in pids))
        visited = set(frontier)
        for _ in range(steps):
            nxt: list[str] = []
            for did in frontier:
                try:
                    meta = self.source.get_json(neuron_path("circuit_meta", did), remote=False)
                except Exception:  # noqa: BLE001 - not mirrored: nothing to follow
                    continue
                for other in neuron_links(meta):
                    if other in catalog.neurons and other not in visited:
                        visited.add(other)
                        nxt.append(other)
                        seen.update(dict.fromkeys(p.pid for p in catalog.poles_of(other)))
            frontier = nxt
        return list(seen)

    def circuit_only_neurons(self, selection: Selection) -> list[str]:
        """Neurons without poles that the circuit explorer knows, as far as ``selection`` names them.

        ``all`` takes every one of them, ``ids`` the ones it lists.  Searches and
        similarity work on poles, so they never reach these neurons.
        """
        catalog = self.catalog
        out: dict[str, None] = {}
        if selection.all:
            out.update(dict.fromkeys(did for did, n in catalog.neurons.items() if not n.poles and n.circuit is not None))
        for ref in selection.ids or ():
            parsed = parse_reference(ref)
            n = catalog.neurons.get(parsed[0].did) if parsed else None
            if n is not None and not n.poles and n.circuit is not None:
                out[n.did] = None
        return list(out)

    def plan_mirror(self, request: MirrorRequest) -> MirrorPlan:
        selection = request.selection
        pids = self.select(selection)
        extra = self.circuit_only_neurons(selection)
        if selection.max_poles is not None:  # the cap covers poles and circuit-only neurons together
            extra = extra[: max(0, selection.max_poles - len(pids))]
        return make_plan(self.catalog, self.source, pids, request.sources, circuit_neurons=extra)

    def mirror(
        self,
        request: MirrorRequest,
        *,
        on_progress: Callable[[MirrorReport, Any], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[MirrorPlan, MirrorReport]:
        """Fetch a selection now (blocking).  Used by the command line."""
        plan = self.plan_mirror(request)
        if request.dry_run:
            return plan, MirrorReport(requested=len(plan.missing))
        report = run_plan(plan, self.source, on_progress=on_progress, should_stop=should_stop)
        if request.index:
            self.index_mirrored(plan.poles, examples=request.index_examples)
        return plan, report

    def mirror_in_background(
        self, request: MirrorRequest, label: str = "", *, plan: MirrorPlan | None = None
    ) -> MirrorJob:
        """Queue a selection for fetching (non-blocking).  Used by the API."""
        plan = plan or self.plan_mirror(request)

        def after(job: MirrorJob) -> None:
            if request.index:
                self.index_mirrored(job.plan.poles, examples=request.index_examples)

        return self.jobs.submit(plan, label, after=after)

    def index_mirrored(
        self,
        pids: Iterable[str] | None = None,
        *,
        examples: bool = False,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Make the texts of mirrored files searchable.  Returns the number of texts added."""
        pids = list(pids) if pids is not None else list(self.catalog.poles)
        added = 0
        for i, pid in enumerate(pids, 1):
            try:
                added += self.details.index_deep(pid, examples=examples)
            except Exception:  # noqa: BLE001 - one broken file must not stop the run
                continue
            if on_progress and (i % 50 == 0 or i == len(pids)):
                on_progress(i, len(pids))
        return added

    # ------------------------------------------------------------------ info

    def info(self) -> dict[str, Any]:
        from . import __version__

        stats = self.catalog.stats()
        return {
            "name": "Neuron Atlas Interface",
            "version": __version__,
            "about": (
                "Unofficial search interface and API for Goodfire's Qwen3.6 Neuron Atlas. "
                "The data belongs to Goodfire AI; this service only reads the published files."
            ),
            "original": self.source.explorer_url(),
            "release": {"url": self.source.release_url, "id": self.source.release_id},
            "atlas": stats,
            "mirror": self.source.store.summary(),
            "search_index": self.search_index.stats(),
            "semantic": {
                "method": self.semantic.embedder.name,
                "ready": self.semantic._ready,  # noqa: SLF001
                "note": self.embedder_note,
            },
            "settings": {
                "offline": self.settings.offline,
                "allow_remote_fetch": self.settings.allow_remote_fetch,
                "can_fetch": self.source.can_fetch(),
                "can_mirror": self.source.can_fetch(remote=True) and self.settings.allow_mirror,
                "max_mirror_poles": self.settings.max_mirror_poles,
                "upstream_requests_per_second": self.settings.requests_per_second,
                "max_page_size": self.settings.max_page_size,
                "max_batch_size": self.settings.max_batch_size,
            },
            "upstream_requests_since_start": self.source.network_requests,
        }
