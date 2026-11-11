"""Flatten mirrored atlas files into tidy tables for analysis.

The per-pole JSON files are convenient for a web page and awkward for
statistics.  :func:`write_tables` turns everything that is mirrored (or a chosen
set of poles) into rectangular tables - CSV by default, Parquet with pyarrow:

``poles``                    one row per pole: ids, category, counts, θ, …
``modes``                    one row per mode of the subcircuit read
``subcircuits``              one row per subcircuit, with its readings
``subcircuit_links``         neuron → pole (gate / up, driver / opposer) and pole → neuron (reader) links
``examples``                 one row per shipped example passage
``landscape_modes``          modes of the firing landscape with shares weighted to real text
``landscape_levels``         the five rarity levels of each pole
``landscape_level_modes``    level × mode firings and damage (long format)
``mode_similarity``          mode × mode similarity inside a pole (long format, two kinds)
``firings``                  the sampled firings: strength, damage, mode, gate flag, 29-token activation profile
``circuit_groups`` / ``circuit_edges``   the neuron-level circuit (from the circuit files)

Rows are streamed, so the full atlas fits through without holding it in memory.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..details import content_end, mark_focal
from ..upstream import PRESENT, neuron_path, pole_path
from ..views import mode_numbers

if TYPE_CHECKING:  # pragma: no cover
    from ..service import Atlas

Row = dict[str, Any]

#: Activation columns of the ``firings`` table: 16 tokens before the firing, the firing, 12 after.
_ACT_COLUMNS = [f"act_m{i}" for i in range(16, 0, -1)] + ["act_0"] + [f"act_p{i}" for i in range(1, 13)]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# --------------------------------------------------------------------------- row generators (one raw file -> rows)


def pole_rows(atlas: Atlas, pids: Iterable[str]) -> Iterator[Row]:
    for pid in pids:
        p = atlas.catalog.poles[pid]
        sub = _raw(atlas, "subcircuit", pid)
        yield {
            "pid": p.pid,
            "did": p.did,
            "pole": p.pole,
            "layer": p.layer,
            "expert": p.expert,
            "neuron": p.neuron,
            "category": p.category,
            "verdict": p.verdict,
            "fn_mono": p.fn_mono,
            "n_modes": len(p.modes) if p.has_subcircuit else p.n_modes,
            "n_residue": p.n_residue,
            "n_subcircuits": p.n_subcircuits,
            "main_damage_share": p.main_damage_share,
            "top_mode_share": p.top_mode_share,
            "simulatability": p.simulatability,
            "in_public_sample": p.in_public_sample,
            "theta": _num(sub.get("theta")) if sub else None,
            "n_firings": sub.get("n_firings") if sub else None,
            "closed_gate_share": _num(sub.get("closed_share")) if sub else None,
            "kl_neuron_mean": _num(sub.get("kl_neuron_mean")) if sub else None,
            "headline": p.headline,
            "description": p.description,
        }


def mode_rows(pid: str, sub: dict[str, Any]) -> Iterator[Row]:
    for index, m in enumerate(sub.get("modes") or []):
        kl = m.get("kl") or {}
        yield {
            "pid": pid,
            "mode_index": index,
            "kind": "closed_gate" if m.get("closed_gate") else ("residue" if m.get("residue") else "open"),
            "label": m.get("label"),
            "description": m.get("description"),
            "share": _num(m.get("share")),
            "n_firings": m.get("n_firings"),
            "n_subcircuits": len(m.get("subcircuits") or []),
            "subcircuits": " ".join(str(s) for s in m.get("subcircuits") or []),
            "kl_per_firing": _num(kl.get("per_firing")),
            "damage_ratio": _num(kl.get("ratio")),
        }


def _mode_of(sub: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, m in enumerate(sub.get("modes") or []):
        for sid in m.get("subcircuits") or []:
            out.setdefault(str(sid), index)
    return out


def subcircuit_rows(pid: str, sub: dict[str, Any]) -> Iterator[Row]:
    mode_of = _mode_of(sub)
    for sid, s in (sub.get("subcircuits") or {}).items():
        profile = s.get("profile") or {}
        kl = s.get("kl") or {}
        yield {
            "pid": pid,
            "subcircuit": sid,
            "mode_index": mode_of.get(str(sid)),
            "n": s.get("n"),
            "share": _num(s.get("share")),
            "readable": s.get("readable"),
            "reading_confidence": s.get("reading_confidence"),
            "function_confidence": s.get("function_confidence"),
            "median_a_rel": _num(s.get("median_a_rel")),
            "profile_ge_theta": _num(profile.get("ge_theta")),
            "profile_mid": _num(profile.get("mid")),
            "profile_weak": _num(profile.get("weak")),
            "profile_closed": _num(profile.get("closed")),
            "kl_per_firing": _num(kl.get("per_firing")),
            "damage_ratio": _num(kl.get("ratio")),
            "kl_n_measured": kl.get("n_measured"),
            "unique_contexts": s.get("unique_contexts"),
            "function_label": s.get("function_label"),
            "meaning": s.get("meaning"),
            "token_pattern": s.get("token_pattern"),
            "function": s.get("function"),
            "gate_role": s.get("gate_role"),
            "up_role": s.get("up_role"),
        }


#: table in the subcircuit file -> (lane, role, direction)
LINK_TABLES = {
    "gate_drivers": ("gate", "driver", "in"),
    "gate_opposers": ("gate", "opposer", "in"),
    "up_drivers": ("up", "driver", "in"),
    "up_opposers": ("up", "opposer", "in"),
    "readers": ("output", "reader", "out"),
}


def subcircuit_link_rows(pid: str, sub: dict[str, Any]) -> Iterator[Row]:
    """One row per (subcircuit, table, other neuron).

    ``direction`` is ``in`` for neurons that feed this pole and ``out`` for
    neurons that read it.  ``weight`` is the unit's mean contribution over the
    subcircuit's firings: a positive magnitude for drivers and opposers (the
    role says which way it pushes), signed for readers.  ``rest`` is its mean
    over the pole's other firings.
    """
    did = pid.rsplit("__", 1)[0]
    mode_of = _mode_of(sub)
    for sid, s in (sub.get("subcircuits") or {}).items():
        circuit = s.get("circuit") or {}
        for table, (lane, role, direction) in LINK_TABLES.items():
            for rank, item in enumerate(circuit.get(table) or []):
                unit = item.get("unit")
                if not unit:
                    continue
                yield {
                    "pid": pid,
                    "subcircuit": sid,
                    "mode_index": mode_of.get(str(sid)),
                    "direction": direction,
                    "lane": lane,
                    "role": role,
                    "source": unit if direction == "in" else did,
                    "target": did if direction == "in" else unit,
                    "unit": unit,
                    "rank": rank,
                    "weight": _num(item.get("leaf")),
                    "rest": _num(item.get("rest")),
                    "unit_label": item.get("label"),
                }


def example_rows(pid: str, sub: dict[str, Any]) -> Iterator[Row]:
    mode_of = _mode_of(sub)
    groups = ("strongest", "typical", "weakest")
    for sid, s in (sub.get("subcircuits") or {}).items():
        for slot, e in enumerate(s.get("exemplars") or []):
            tokens = e.get("tokens") or []
            focal = e.get("focal")
            yield {
                "pid": pid,
                "subcircuit": sid,
                "mode_index": mode_of.get(str(sid)),
                "slot": slot,
                "group": groups[min(slot // 4, 2)],
                "activation": _num(e.get("a")),
                "a_rel": _num(e.get("a_rel")),
                "gate_closed": e.get("closed"),
                "held_out": e.get("fold") == "audit",
                "repeats": e.get("repeats"),
                "firing_token": tokens[focal][0] if isinstance(focal, int) and 0 <= focal < len(tokens) else None,
                "text": mark_focal(tokens, focal if isinstance(focal, int) else None, 0, content_end(tokens, focal if isinstance(focal, int) else None)),
                "pre": e.get("pre"),
            }


def landscape_mode_rows(pid: str, ls: dict[str, Any]) -> Iterator[Row]:
    modes = ls.get("modes") or []
    numbers = mode_numbers(ls)
    last = len(modes) - 1
    for k, m in enumerate(modes):
        unassigned = k == last and "closed" not in m
        yield {
            "pid": pid,
            "mode_index": k,
            "kind": "unassigned" if unassigned else ("closed_gate" if m.get("closed") else "open"),
            "label": m.get("label"),
            "description": m.get("description"),
            "sample_share": _num(m.get("share")),
            **numbers[k],
            "kl_per_firing": _num(m.get("kl_per_firing")),
            "relation": m.get("rel"),
            "folds_into": m.get("fold"),
        }


def landscape_level_rows(pid: str, ls: dict[str, Any]) -> Iterator[Row]:
    for i, band in enumerate(ls.get("bands") or []):
        yield {
            "pid": pid,
            "level": i,
            "name": band.get("name"),
            "median_strength": _num(band.get("a_med")),
            "min_strength": _num(band.get("a_min")),
            "max_strength": _num(band.get("a_max")),
            "token_frequency": _num(band.get("mass")),
            "sampled_firings": band.get("n"),
        }


def landscape_level_mode_rows(pid: str, ls: dict[str, Any]) -> Iterator[Row]:
    for i, band in enumerate(ls.get("bands") or []):
        counts = band.get("counts") or []
        damage = band.get("dmg_lin") or []
        compressed = band.get("dmg") or []
        for k in range(len(counts)):
            yield {
                "pid": pid,
                "level": i,
                "mode_index": k,
                "firings": counts[k],
                "damage": _num(damage[k]) if k < len(damage) else None,
                "damage_compressed": _num(compressed[k]) if k < len(compressed) else None,
            }


def mode_similarity_rows(pid: str, ls: dict[str, Any]) -> Iterator[Row]:
    for kind, key in (("description", "rel_sim"), ("firing", "sim")):
        matrix = ls.get(key)
        if not isinstance(matrix, list):
            continue
        for a, row in enumerate(matrix):
            for b, value in enumerate(row or []):
                if b >= a:  # symmetric: upper triangle with the diagonal
                    yield {"pid": pid, "kind": kind, "mode_a": a, "mode_b": b, "similarity": _num(value)}


def firing_rows(pid: str, ls: dict[str, Any], *, text: bool = True) -> Iterator[Row]:
    """One row per sampled firing of the landscape file.

    ``strength`` is in multiples of θ; ``act_*`` is the neuron's activation
    around the firing (same unit, signed to this pole; empty = the token was
    not routed to this neuron's expert, which is information, not a gap).
    Tested firings are topped up to at least five per mode, so use
    ``landscape_level_modes`` - not this table - for unbiased mode frequencies.
    """
    modes = ls.get("modes") or []
    bands = ls.get("bands") or []
    last = len(modes) - 1
    for j, dot in enumerate(ls.get("dots") or []):
        tokens = dot.get("t") or []
        f, u = dot.get("f"), dot.get("u") or 0
        if not isinstance(f, int) or not 0 <= f < len(tokens):
            continue
        m = dot.get("m")
        mode = modes[m] if isinstance(m, int) and 0 <= m < len(modes) else {}
        band = bands[dot["b"]] if isinstance(dot.get("b"), int) and 0 <= dot["b"] < len(bands) else {}
        before = [t[1] for t in tokens[u:f]][-16:]
        after = [t[1] for t in tokens[f + 1 : f + 13]]
        acts = [None] * (16 - len(before)) + before + [tokens[f][1]] + after + [None] * (12 - len(after))
        s = dot.get("s")
        row: Row = {
            "pid": pid,
            "firing": j,
            "tested": dot.get("kl") is not None,
            "level": dot.get("b"),
            "level_token_frequency": _num(band.get("mass")),
            "strength": _num(dot.get("a")),
            "mode_index": m,
            "mode_kind": "unassigned" if m == last and "closed" not in mode else ("closed_gate" if mode.get("closed") else "open"),
            "subcircuit": f"c{s}" if isinstance(s, int) and s >= 0 else None,
            "gate_closed": dot.get("c"),
            "kl_measured": _num(dot.get("kl")),
            "log10_kl": _num(dot.get("lk")),
            "weight": _num(dot.get("w")),
            "firing_token": tokens[f][0],
        }
        if text:
            row["text_before"] = "".join(str(t[0]) for t in tokens[:f])
            row["text_after"] = "".join(str(t[0]) for t in tokens[f + 1 :])
        row.update(zip(_ACT_COLUMNS, acts, strict=True))
        yield row


# --------------------------------------------------------------------------- writing


class _CsvSink:
    def __init__(self, path: Path):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115 - closed in close()
        self._writer: csv.DictWriter[str] | None = None
        self.rows = 0

    def write(self, row: Row) -> None:
        if self._writer is None:
            self._writer = csv.DictWriter(self._fh, fieldnames=list(row), extrasaction="ignore")
            self._writer.writeheader()
        self._writer.writerow({k: _cell(v) for k, v in row.items()})
        self.rows += 1

    def close(self) -> None:
        self._fh.close()
        if self.rows == 0:
            self.path.unlink(missing_ok=True)


class _ParquetSink:
    """Buffers rows and writes row groups; needs pyarrow."""

    def __init__(self, path: Path, batch: int = 20_000):
        import pyarrow as pa  # noqa: F401 - fail early with a clear message
        import pyarrow.parquet as pq  # noqa: F401

        self.path = path
        self.batch = batch
        self._rows: list[Row] = []
        self._writer: Any = None
        self._schema: Any = None
        self.rows = 0

    def write(self, row: Row) -> None:
        self._rows.append({k: (v.replace("\x00", "") if isinstance(v, str) else v) for k, v in row.items()})
        self.rows += 1
        if len(self._rows) >= self.batch:
            self._flush()

    def _flush(self) -> None:
        if not self._rows:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self._schema is None:
            columns = list(self._rows[0])
            table = pa.Table.from_pylist(self._rows)
            # Columns that are empty in the first batch would get the null type; keep them open as strings/doubles.
            fields = []
            for name in columns:
                kind = table.schema.field(name).type
                if pa.types.is_null(kind):
                    kind = pa.float64() if name.startswith("act_") or name.endswith(("share", "strength")) else pa.string()
                fields.append(pa.field(name, kind))
            self._schema = pa.schema(fields)
            self._writer = pq.ParquetWriter(self.path, self._schema, compression="zstd")
        try:
            table = pa.Table.from_pylist(self._rows, schema=self._schema)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            fixed = [{f.name: _coerce(r.get(f.name), f.type, pa) for f in self._schema} for r in self._rows]
            table = pa.Table.from_pylist(fixed, schema=self._schema)
        self._writer.write_table(table)
        self._rows = []

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()


def _coerce(value: Any, kind: Any, pa: Any) -> Any:
    if value is None:
        return None
    try:
        if pa.types.is_string(kind):
            return value if isinstance(value, str) else str(value)
        if pa.types.is_floating(kind):
            return float(value)
        if pa.types.is_integer(kind):
            return int(value)
        if pa.types.is_boolean(kind):
            return bool(value)
    except (TypeError, ValueError):
        return None
    return value


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def _raw(atlas: Atlas, kind: str, ident: str) -> dict[str, Any] | None:
    """A mirrored raw file, or ``None`` when it is not in the store (never fetches)."""
    relpath = neuron_path(kind, ident) if kind.startswith("circuit") else pole_path(kind, ident)
    if atlas.source.store.status(relpath) != PRESENT:
        return None
    data = atlas.source.get_json(relpath, remote=False)
    return data if isinstance(data, dict) else None


TABLES = (
    "poles", "modes", "subcircuits", "subcircuit_links", "examples", "landscape_modes", "landscape_levels",
    "landscape_level_modes", "mode_similarity", "firings", "circuit_groups", "circuit_edges",
)  # fmt: skip


def write_tables(
    atlas: Atlas,
    out_dir: Path | str,
    *,
    pids: Iterable[str] | None = None,
    fmt: str = "csv",
    tables: Iterable[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Write the tables for ``pids`` (default: every pole) from what is mirrored.

    Returns ``table name -> rows written``.  Poles whose files are not mirrored
    still appear in ``poles`` (from the index) and are simply missing elsewhere.
    """
    from .graph import circuit_edge_rows, circuit_group_rows

    if fmt not in ("csv", "parquet"):
        raise ValueError("format must be csv or parquet")
    wanted = set(tables) if tables is not None else set(TABLES)
    unknown = wanted - set(TABLES)
    if unknown:
        raise ValueError(f"unknown table(s): {', '.join(sorted(unknown))}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pid_list = [p for p in (pids if pids is not None else atlas.catalog.poles) if p in atlas.catalog.poles]
    pid_list = list(dict.fromkeys(pid_list))

    sinks: dict[str, Any] = {}

    def sink(name: str) -> Any:
        if name not in sinks:
            path = out_dir / f"{name}.{fmt}"
            sinks[name] = _CsvSink(path) if fmt == "csv" else _ParquetSink(path)
        return sinks[name]

    def emit(name: str, rows: Iterable[Row]) -> None:
        if name in wanted:
            target = sink(name)
            for row in rows:
                target.write(row)

    try:
        emit("poles", pole_rows(atlas, pid_list))
        done_neurons: set[str] = set()
        for i, pid in enumerate(pid_list, 1):
            if wanted & {"modes", "subcircuits", "subcircuit_links", "examples"}:
                sub = _raw(atlas, "subcircuit", pid)
                if sub:
                    emit("modes", mode_rows(pid, sub))
                    emit("subcircuits", subcircuit_rows(pid, sub))
                    emit("subcircuit_links", subcircuit_link_rows(pid, sub))
                    emit("examples", example_rows(pid, sub))
            if wanted & {"landscape_modes", "landscape_levels", "landscape_level_modes", "mode_similarity", "firings"}:
                ls = _raw(atlas, "landscape", pid)
                if ls:
                    emit("landscape_modes", landscape_mode_rows(pid, ls))
                    emit("landscape_levels", landscape_level_rows(pid, ls))
                    emit("landscape_level_modes", landscape_level_mode_rows(pid, ls))
                    emit("mode_similarity", mode_similarity_rows(pid, ls))
                    emit("firings", firing_rows(pid, ls))
            did = pid.rsplit("__", 1)[0]
            if did not in done_neurons and wanted & {"circuit_groups", "circuit_edges"}:
                done_neurons.add(did)
                meta = _raw(atlas, "circuit_meta", did)
                if meta:
                    emit("circuit_groups", circuit_group_rows(did, meta))
                    emit("circuit_edges", circuit_edge_rows(did, meta))
            if on_progress and (i % 25 == 0 or i == len(pid_list)):
                on_progress(i, len(pid_list))
    finally:
        for target in sinks.values():
            target.close()
    return {name: target.rows for name, target in sinks.items() if target.rows}
