"""Mirroring: bring a chosen part of the atlas (or all of it) into the local store.

A *plan* lists the files that belong to a set of poles; running it downloads
what is missing, politely (rate limited, few connections, resumable - files
that are already mirrored are skipped, so an interrupted run simply continues).

The catalog knows which files exist upstream, so a plan never probes for files
that are not there.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .catalog import Catalog, PoleEntry
from .upstream import ABSENT, INDEX_FILES, PRESENT, AtlasSource, FetchResult, neuron_path, pole_path

#: Data sets that can be mirrored, in the order the explorer's tabs use them.
SOURCES = ("subcircuit", "overview", "landscape", "islands", "circuit")

#: The three per-neuron files behind the Circuit tab.
CIRCUIT_PARTS = ("circuit_meta", "circuit_exemplars", "circuit_incidence")

#: Typical size of one file (bytes on the wire, bytes uncompressed), measured on the
#: 2026-11-09 release.  Only used for estimates before anything has been mirrored.
TYPICAL_BYTES: dict[str, tuple[int, int]] = {
    "subcircuit": (180_000, 820_000),
    "overview": (150_000, 680_000),
    "landscape": (330_000, 1_540_000),
    "islands": (80_000, 290_000),
    "circuit_meta": (130_000, 690_000),
    "circuit_exemplars": (95_000, 450_000),
    "circuit_incidence": (35_000, 430_000),
    "index": (400_000, 1_300_000),
}


def pole_sources(p: PoleEntry) -> list[str]:
    """Per-pole data sets that exist upstream for this pole."""
    out = []
    if p.has_subcircuit or p.subcircuit_empty:
        out.append("subcircuit")
    if p.has_overview:
        out.append("overview")
    if p.has_landscape:
        out.append("landscape")
    if p.has_islands:
        out.append("islands")
    return out


def files_of_pole(p: PoleEntry, sources: Iterable[str] = SOURCES) -> dict[str, str]:
    """``kind -> relative path`` of the files of one pole (circuit files belong to its neuron)."""
    wanted = set(sources)
    out = {s: pole_path(s, p.pid) for s in pole_sources(p) if s in wanted}
    if "circuit" in wanted and p.has_circuit:
        out.update({part: neuron_path(part, p.did) for part in CIRCUIT_PARTS})
    return out


@dataclass
class MirrorPlan:
    poles: list[str]
    sources: tuple[str, ...]
    #: relative path -> kind (``subcircuit`` … ``circuit_meta`` … ``index``)
    files: dict[str, str] = field(default_factory=dict)
    present: int = 0
    absent: int = 0
    missing: list[str] = field(default_factory=list)
    #: Neurons without poles in the atlas (the circuit explorer knows them) whose circuit files are included.
    circuit_neurons: list[str] = field(default_factory=list)

    @property
    def neurons(self) -> list[str]:
        return list(dict.fromkeys([*(pid.rsplit("__", 1)[0] for pid in self.poles), *self.circuit_neurons]))

    @property
    def size(self) -> int:
        """Poles plus circuit-only neurons: what a server's per-job limit counts."""
        return len(self.poles) + len(self.circuit_neurons)

    def estimate(self) -> dict[str, Any]:
        """Rough size of what still has to be downloaded."""
        kinds = Counter(self.files[path] for path in self.missing)
        wire = sum(TYPICAL_BYTES.get(k, (150_000, 600_000))[0] * n for k, n in kinds.items())
        raw = sum(TYPICAL_BYTES.get(k, (150_000, 600_000))[1] * n for k, n in kinds.items())
        return {
            "poles": len(self.poles),
            "neurons": len(dict.fromkeys(pid.rsplit("__", 1)[0] for pid in self.poles)),  # neurons of the poles
            "circuit_only_neurons": len(self.circuit_neurons),
            "files_total": len(self.files),
            "files_present": self.present,
            "files_known_absent": self.absent,
            "files_to_fetch": len(self.missing),
            "to_fetch_by_kind": dict(sorted(kinds.items())),
            "estimated_download_bytes": wire,
            "estimated_uncompressed_bytes": raw,
        }


def make_plan(
    catalog: Catalog,
    source: AtlasSource,
    pids: Iterable[str],
    sources: Iterable[str] = SOURCES,
    *,
    include_indices: bool = True,
    circuit_neurons: Iterable[str] = (),
) -> MirrorPlan:
    """The files of the poles ``pids`` (and the circuit files of ``circuit_neurons``) and what is missing of them."""
    requested = set(sources)
    unknown = requested - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(sorted(unknown))}; choose from {', '.join(SOURCES)}")
    sources = tuple(s for s in SOURCES if s in requested)
    poles = [pid for pid in dict.fromkeys(pids) if pid in catalog.poles]
    plan = MirrorPlan(poles=poles, sources=sources)
    if include_indices:
        plan.files.update(dict.fromkeys(INDEX_FILES.values(), "index"))
    for pid in poles:
        for kind, path in files_of_pole(catalog.poles[pid], sources).items():
            plan.files[path] = kind
    if "circuit" in sources:
        for did in dict.fromkeys(circuit_neurons):
            n = catalog.neurons.get(did)
            if n is None or n.circuit is None:
                continue
            plan.circuit_neurons.append(did)
            plan.files.update({neuron_path(part, did): part for part in CIRCUIT_PARTS})
    statuses = source.store.statuses(plan.files)
    for path, status in statuses.items():
        if status == PRESENT:
            plan.present += 1
        elif status == ABSENT:
            plan.absent += 1
        else:
            plan.missing.append(path)
    return plan


@dataclass
class MirrorReport:
    requested: int = 0
    fetched: int = 0
    skipped: int = 0
    absent: int = 0
    failed: int = 0
    bytes_fetched: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    #: Files left out because the run stopped on its own (see ``stopped``).
    not_attempted: int = 0
    #: Why the run stopped early: upstream failed many times in a row, or the disk is full.
    stopped: str = ""

    @property
    def nothing_worked(self) -> bool:
        """Files were due but not one could be fetched (a run that stopped early may still have fetched some)."""
        return self.failed > 0 and self.fetched == 0 and self.absent == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "fetched": self.fetched,
            "skipped": self.skipped,
            "absent": self.absent,
            "failed": self.failed,
            "not_attempted": self.not_attempted,
            "bytes_fetched": self.bytes_fetched,
            "seconds": round(self.seconds, 2),
            "errors": self.errors[:20],
            "cancelled": self.cancelled,
            "stopped": self.stopped,
        }


def run_plan(
    plan: MirrorPlan,
    source: AtlasSource,
    *,
    on_progress: Callable[[MirrorReport, FetchResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    workers: int | None = None,
) -> MirrorReport:
    """Download the missing files of a plan.  Safe to interrupt (Ctrl-C stops at once) and to repeat.

    The run stops on its own when upstream keeps failing or the disk is full;
    ``report.stopped`` then says why and the rest counts as ``not_attempted``.
    """
    report = MirrorReport(requested=len(plan.missing))
    started = time.monotonic()
    lock = threading.Lock()

    def on_result(res: FetchResult) -> None:
        with lock:
            if res.status == PRESENT:
                if res.fetched:
                    report.fetched += 1
                    report.bytes_fetched += res.raw_bytes
                else:
                    report.skipped += 1
            elif res.status == ABSENT:
                report.absent += 1
            elif res.error == "cancelled":
                report.cancelled = True
            elif res.error.startswith("not attempted: "):
                report.not_attempted += 1
                report.stopped = report.stopped or res.error.removeprefix("not attempted: ")
            else:
                report.failed += 1
                if len(report.errors) < 200:
                    report.errors.append(f"{res.relpath}: {res.error}")
            report.seconds = time.monotonic() - started
        if on_progress:
            on_progress(report, res)

    source.fetch_many(plan.missing, on_result=on_result, should_stop=should_stop, workers=workers)
    report.seconds = time.monotonic() - started
    return report


def mirrored_sources(catalog: Catalog, source: AtlasSource, pids: Iterable[str]) -> dict[str, list[str]]:
    """``pid -> data sets present in the local store`` (circuit counts when all three parts are there)."""
    pids = [pid for pid in pids if pid in catalog.poles]
    # Circuit files belong to the neuron, so one path can serve both poles.
    paths: dict[str, list[tuple[str, str]]] = {}
    for pid in pids:
        for kind, path in files_of_pole(catalog.poles[pid]).items():
            paths.setdefault(path, []).append((pid, kind))
    statuses = source.store.statuses(paths)
    have: dict[str, set[str]] = {pid: set() for pid in pids}
    for path, status in statuses.items():
        if status == PRESENT:
            for pid, kind in paths[path]:
                have[pid].add(kind)
    out: dict[str, list[str]] = {}
    for pid, kinds in have.items():
        names = [s for s in SOURCES if s in kinds]
        if all(part in kinds for part in CIRCUIT_PARTS):
            names.append("circuit")
        out[pid] = names
    return out


# --------------------------------------------------------------------------- background jobs


@dataclass
class MirrorJob:
    id: str
    label: str
    plan: MirrorPlan
    state: str = "queued"  # queued | running | done | failed | cancelled
    report: MirrorReport = field(default_factory=MirrorReport)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str = ""
    #: Follow-up for this job (e.g. indexing what was fetched); part of the job, so "done" means it ran too.
    after: Callable[[MirrorJob], None] | None = field(default=None, repr=False, compare=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def as_dict(self) -> dict[str, Any]:
        done = self.report.fetched + self.report.skipped + self.report.absent + self.report.failed
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "poles": len(self.plan.poles),
            "sources": list(self.plan.sources),
            "files_to_fetch": len(self.plan.missing),
            "files_done": done,
            "progress": round(done / len(self.plan.missing), 4) if self.plan.missing else 1.0,
            "report": self.report.as_dict(),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class MirrorJobs:
    """Runs mirror plans one at a time in a background thread (used by the API server)."""

    def __init__(
        self,
        source: AtlasSource,
        *,
        after: Callable[[MirrorJob], None] | None = None,
        keep: int = 50,
        max_queued: int = 10,
    ):
        self.source = source
        self.after = after
        self.keep = keep
        self.max_queued = max_queued
        self._jobs: dict[str, MirrorJob] = {}
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._closing = False

    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop accepting jobs, cancel what is queued or running and wait for the worker to finish."""
        with self._wake:
            self._closing = True
        self.cancel_all(timeout)

    def cancel_all(self, timeout: float = 30.0) -> None:
        """Cancel what is queued or running and wait for the worker; new jobs are still accepted afterwards."""
        with self._wake:
            for job in self._jobs.values():
                if job.state in ("queued", "running"):
                    job._stop.set()
                    if job.state == "queued":
                        job.state = "cancelled"
                        job.finished_at = time.time()
            self._queue.clear()
            thread = self._thread
            self._wake.notify_all()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    def submit(
        self, plan: MirrorPlan, label: str = "", *, after: Callable[[MirrorJob], None] | None = None
    ) -> MirrorJob:
        """Queue a plan.  ``after`` runs on the worker thread once this job has finished its downloads."""
        job = MirrorJob(uuid.uuid4().hex[:12], label, plan, after=after)
        with self._wake:
            if self._closing:
                job.state = "cancelled"
                job.error = "the server is shutting down"
                job.finished_at = time.time()
                return job
            waiting = [j for j in self._jobs.values() if j.state in ("queued", "running")]
            # The same files asked for again: hand back the job that already fetches them.
            for other in waiting:
                if set(other.plan.missing) >= set(plan.missing):
                    return other
            if sum(1 for j in waiting if j.state == "queued") >= self.max_queued:
                raise ValueError(
                    f"{self.max_queued} downloads are already waiting; try again when they are done"
                )
            self._jobs[job.id] = job
            self._queue.append(job.id)
            finished = [j for j in self._jobs.values() if j.state in ("done", "failed", "cancelled")]
            for old in sorted(finished, key=lambda j: j.created_at)[: max(0, len(finished) - self.keep)]:
                del self._jobs[old.id]
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="atlas-mirror", daemon=True)
                self._thread.start()
            self._wake.notify()
        return job

    def get(self, job_id: str) -> MirrorJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[MirrorJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: -j.created_at)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.state in ("done", "failed", "cancelled"):
            return False
        job._stop.set()
        with self._lock:
            if job.state == "queued":
                job.state = "cancelled"
                job.finished_at = time.time()
        return True

    def _work(self) -> None:
        while True:
            with self._wake:
                while not self._queue:
                    if self._closing:
                        self._thread = None
                        return
                    # Re-check the queue after a time-out: a job may have arrived just then.
                    if not self._wake.wait(timeout=30) and not self._queue:
                        self._thread = None
                        return
                job = self._jobs.get(self._queue.pop(0))
                if job is None or job.state != "queued":
                    continue
                if self._closing:
                    job.state = "cancelled"
                    job.finished_at = time.time()
                    continue
                job.state = "running"
            try:
                job.report = run_plan(
                    job.plan,
                    self.source,
                    on_progress=lambda report, _res, job=job: setattr(job, "report", report),
                    should_stop=job._stop.is_set,
                )
                if job._stop.is_set():
                    job.state = "cancelled"
                elif job.report.nothing_worked:
                    # Do not report success (or index anything) when not one file came through.
                    job.state = "failed"
                    job.error = job.report.stopped or (job.report.errors[0] if job.report.errors else "no file could be fetched")
                else:
                    # Also after an early stop: what was fetched is kept and made searchable.
                    after = job.after or self.after
                    if after and not self._closing:
                        after(job)
                    job.state = "cancelled" if job._stop.is_set() else "done"
                    if job.report.stopped:
                        job.error = f"stopped early, {job.report.not_attempted:,} files not tried: {job.report.stopped}"
            except Exception as exc:  # noqa: BLE001 - a job must never kill the worker
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
