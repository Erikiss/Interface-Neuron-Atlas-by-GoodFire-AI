"""Flatten mirrored atlas files into tidy tables for analysis.

The per-pole JSON files are convenient for a web page and awkward for
statistics.  :func:`write_tables` turns the mirrored files of the chosen poles
(default: every pole) into rectangular tables - CSV by default, Parquet with
pyarrow.  Every table has a fixed list of typed columns (:data:`COLUMNS`), so
the files of different exports line up:

``poles``                    one row per pole: ids, category, counts, θ, …
``modes``                    one row per mode of the subcircuit read
``subcircuits``              one row per subcircuit, with its readings
``subcircuit_links``         neuron → pole (gate / up, driver / opposer) and pole → neuron (reader) links
``examples``                 one row per shipped example passage of a subcircuit
``landscape_modes``          modes of the firing landscape with shares weighted to real text
``landscape_levels``         the five rarity levels of each pole
``landscape_level_modes``    level × mode firings and damage (long format)
``mode_similarity``          mode × mode similarity inside a pole (long format, two kinds)
``firings``                  the sampled firings: strength, damage, mode, gate flag, 29-token activation profile
``islands``                  one row per island of the older island read (the only data of islands-only poles)
``island_modes``             the island read's modes, unassigned islands as the last one
``island_examples``          one row per shipped island example, with its raw activation profile
``overview``                 one row per pole: the scientist's headline, summary, report, scores
``overview_situations``      one row per situation (mode, or subcircuit on single-mode poles) of the overview
``overview_experiments``     one row per controlled experiment of the overview
``circuit_groups`` / ``circuit_edges``   the neuron-level circuit (from the circuit files)
``circuit_exemplars``        the circuit data's examples with their gate and up pre-activations

Two mode numberings.  ``mode_index`` (modes, subcircuits, subcircuit_links,
examples, overview_situations) counts the modes of the subcircuit read: open
modes, then one closed-gate mode, then residue - the API's ``Mode.index``.
``landscape_mode_index`` (landscape_modes, landscape_level_modes, firings;
``landscape_mode_a`` / ``landscape_mode_b`` in mode_similarity) counts the
landscape's modes: open modes, then several Q3/Q4 modes, then the unassigned
bucket.  Only open modes exist in both; ``landscape_modes.pole_mode_index``
matches them by label.  A firing reaches the subcircuit read through its
subcircuit: ``firings.subcircuit`` = ``subcircuits.subcircuit``, whose
``mode_index`` is the mode.

Bounds: ``firings`` holds ~950 rows per pole, ``examples`` 12 per subcircuit,
``island_examples`` at most 12 per island, ``circuit_edges`` ~620 and
``circuit_exemplars`` ~300 per neuron; the other tables are smaller.  Not
exported: the circuit data's exemplar × group incidence matrices, the
overview's replayed long documents, and the copies of subcircuit data that the
overview file repeats (all still available through the ``raw`` endpoints).

Rows are streamed, so the full atlas fits through without holding it in memory.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..details import content_end, island_threshold, islands_view, mark_focal
from ..upstream import PRESENT, neuron_path, pole_path
from ..views import SIMULATION_SOURCES, mode_numbers, overview_view

if TYPE_CHECKING:  # pragma: no cover
    from ..service import Atlas

Row = dict[str, Any]

#: Activation columns of the ``firings`` table: 16 tokens before the firing, the firing, 12 after.
_ACT_COLUMNS = [f"act_m{i}" for i in range(16, 0, -1)] + ["act_0"] + [f"act_p{i}" for i in range(1, 13)]
#: The same window around the peak of an island example, in raw activations.
_RAW_COLUMNS = [f"raw_m{i}" for i in range(16, 0, -1)] + ["raw_0"] + [f"raw_p{i}" for i in range(1, 13)]

_S, _I, _F, _B = "str", "int", "float", "bool"

#: Columns of every table, in order, with their type (str / int / float / bool).
COLUMNS: dict[str, dict[str, str]] = {
    "poles": {
        "pid": _S, "did": _S, "pole": _S, "layer": _I, "expert": _I, "neuron": _I, "category": _S, "verdict": _S,
        "fn_mono": _B, "n_modes": _I, "n_residue": _I, "n_subcircuits": _I, "main_damage_share": _F,
        "top_mode_share": _F, "simulatability": _F, "in_public_sample": _B, "theta": _F, "n_firings": _I,
        "closed_gate_share": _F, "kl_neuron_mean": _F, "headline": _S, "description": _S,
    },
    "modes": {
        "pid": _S, "mode_index": _I, "kind": _S, "label": _S, "description": _S, "share": _F, "n_firings": _I,
        "n_subcircuits": _I, "subcircuits": _S, "kl_per_firing": _F, "damage_ratio": _F,
    },
    "subcircuits": {
        "pid": _S, "subcircuit": _S, "mode_index": _I, "n": _I, "share": _F, "readable": _B, "reading_confidence": _I,
        "function_confidence": _I, "median_a_rel": _F, "profile_ge_theta": _F, "profile_mid": _F, "profile_weak": _F,
        "profile_closed": _F, "kl_per_firing": _F, "damage_ratio": _F, "kl_n_measured": _I, "unique_contexts": _I,
        "function_label": _S, "meaning": _S, "token_pattern": _S, "function": _S, "gate_role": _S, "up_role": _S,
    },
    "subcircuit_links": {
        "pid": _S, "subcircuit": _S, "mode_index": _I, "direction": _S, "lane": _S, "role": _S, "source": _S,
        "target": _S, "unit": _S, "rank": _I, "weight": _F, "rest": _F, "unit_label": _S,
    },
    "examples": {
        "pid": _S, "subcircuit": _S, "mode_index": _I, "slot": _I, "group": _S, "activation": _F, "a_rel": _F,
        "gate_closed": _B, "held_out": _B, "repeats": _I, "firing_token": _S, "text": _S, "pre": _S,
    },
    "landscape_modes": {
        "pid": _S, "landscape_mode_index": _I, "pole_mode_index": _I, "kind": _S, "label": _S, "description": _S,
        "sample_share": _F, "firing_share": _F, "damage_share": _F, "damage_per_firing": _F, "tested_firings": _I,
        "kl_per_firing": _F, "relation": _S, "folds_into": _I,
    },
    "landscape_levels": {
        "pid": _S, "level": _I, "name": _S, "median_strength": _F, "min_strength": _F, "max_strength": _F,
        "token_frequency": _F, "sampled_firings": _I,
    },
    "landscape_level_modes": {
        "pid": _S, "level": _I, "landscape_mode_index": _I, "firings": _I, "damage": _F, "damage_compressed": _F,
    },
    "mode_similarity": {"pid": _S, "kind": _S, "landscape_mode_a": _I, "landscape_mode_b": _I, "similarity": _F},
    "firings": {
        "pid": _S, "firing": _I, "tested": _B, "level": _I, "level_token_frequency": _F, "strength": _F,
        "landscape_mode_index": _I, "mode_kind": _S, "subcircuit": _S, "gate_closed": _B, "kl_measured": _F,
        "log10_kl": _F, "weight": _F, "firing_token": _S, "window_before": _I, "window_after": _I,
        "text_before": _S, "text_after": _S, **dict.fromkeys(_ACT_COLUMNS, _F),
    },
    "islands": {
        "pid": _S, "island": _I, "number": _I, "island_mode": _I, "unassigned": _B, "n": _I, "examples_shipped": _I,
        "name": _S, "description": _S,
    },
    "island_modes": {
        "pid": _S, "island_mode": _I, "unassigned": _B, "name": _S, "description": _S, "share": _F, "n_islands": _I,
        "islands": _S,
    },
    "island_examples": {
        "pid": _S, "island": _I, "slot": _I, "ref": _S, "label": _S, "activation": _F, "a_over_theta": _F,
        "threshold": _F, "firing_token": _S, "focal_index": _I, "n_tokens": _I, "text": _S,
        **dict.fromkeys(_RAW_COLUMNS, _F),
    },
    "overview": {
        "pid": _S, "headline": _S, "summary": _S, "open": _S, "not_this": _S, "final_description": _S, "report": _S,
        "situations_refer_to": _S, "n_situations": _I, "n_experiments": _I, "threshold": _F,
        **{f"kl_recovered_{source}": _F for source in SIMULATION_SOURCES}, "scientist_model": _S,
    },
    "overview_situations": {
        "pid": _S, "situation": _I, "refers_to": _S, "mode_index": _I, "subcircuit": _S, "name": _S, "explain": _S,
        "typical_strength": _S, "cue": _S, "from_experiments": _S,
    },
    "overview_experiments": {"pid": _S, "experiment": _I, "claim": _S, "test": _S, "result": _S},
    "circuit_groups": {
        "hub": _S, "kind": _S, "group": _S, "name": _S, "lane_tag": _S, "pole": _S, "role": _S, "members": _I,
        "gate": _F, "up": _F, "examples": _I, "mean_effect": _F, "examples_other_pole": _I,
        "mean_effect_other_pole": _F, "description": _S,
    },
    "circuit_edges": {
        "hub": _S, "kind": _S, "source": _S, "source_pole": _S, "target": _S, "target_pole": _S, "group": _S,
        "group_name": _S, "role": _S, "effect": _S, "gate": _F, "up": _F, "weight": _F, "layer_distance": _I,
        "examples_active": _I, "label": _S,
    },
    "circuit_exemplars": {
        "hub": _S, "ex_id": _S, "pole": _S, "band": _S, "activation": _F, "strength": _F, "gate_pre": _F,
        "up_pre": _F, "router_weight": _F, "route_rank": _I, "firing_token": _S, "next_token": _S,
        "next_token_prob": _F, "model_top1": _S, "text": _S,
    },
}  # fmt: skip

TABLES = tuple(COLUMNS)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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
            "n_firings": _int(sub.get("n_firings")) if sub else None,
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
            "n_firings": _int(m.get("n_firings")),
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
            "n": _int(s.get("n")),
            "share": _num(s.get("share")),
            "readable": s.get("readable"),
            "reading_confidence": _int(s.get("reading_confidence")),
            "function_confidence": _int(s.get("function_confidence")),
            "median_a_rel": _num(s.get("median_a_rel")),
            "profile_ge_theta": _num(profile.get("ge_theta")),
            "profile_mid": _num(profile.get("mid")),
            "profile_weak": _num(profile.get("weak")),
            "profile_closed": _num(profile.get("closed")),
            "kl_per_firing": _num(kl.get("per_firing")),
            "damage_ratio": _num(kl.get("ratio")),
            "kl_n_measured": _int(kl.get("n_measured")),
            "unique_contexts": _int(s.get("unique_contexts")),
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
    role says which way it pushes: drivers push the lane towards the pole's
    firing direction - the gate open, the up signal towards the pole's sign),
    signed for readers (the push on the reader's own activation).  ``rest`` is
    its mean over the pole's other firings.
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
            focal = e.get("focal") if isinstance(e.get("focal"), int) else None
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
                "repeats": _int(e.get("repeats")),
                "firing_token": tokens[focal][0] if focal is not None and 0 <= focal < len(tokens) else None,
                "text": mark_focal(tokens, focal, 0, content_end(tokens, focal)),
                "pre": e.get("pre"),
            }


def landscape_mode_rows(pid: str, ls: dict[str, Any], pole_modes: dict[str, int] | None = None) -> Iterator[Row]:
    """One row per landscape mode.  ``pole_modes`` maps open-mode labels to the pole's ``mode_index``."""
    modes = ls.get("modes") or []
    numbers = mode_numbers(ls)
    last = len(modes) - 1
    for k, m in enumerate(modes):
        unassigned = k == last and "closed" not in m
        kind = "unassigned" if unassigned else ("closed_gate" if m.get("closed") else "open")
        nums = numbers[k] if k < len(numbers) else {}
        yield {
            "pid": pid,
            "landscape_mode_index": k,
            "pole_mode_index": (pole_modes or {}).get(m.get("label")) if kind == "open" else None,
            "kind": kind,
            "label": m.get("label"),
            "description": m.get("description"),
            "sample_share": _num(m.get("share")),
            "firing_share": nums.get("firing_share"),
            "damage_share": nums.get("damage_share"),
            "damage_per_firing": nums.get("damage_per_firing"),
            "tested_firings": nums.get("tested_firings"),
            "kl_per_firing": _num(m.get("kl_per_firing")),
            "relation": m.get("rel"),
            "folds_into": _int(m.get("fold")),
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
            "sampled_firings": _int(band.get("n")),
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
                "landscape_mode_index": k,
                "firings": _int(counts[k]),
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
                    yield {"pid": pid, "kind": kind, "landscape_mode_a": a, "landscape_mode_b": b, "similarity": _num(value)}


def firing_rows(pid: str, ls: dict[str, Any], *, text: bool = True) -> Iterator[Row]:
    """One row per sampled firing of the landscape file.

    ``strength`` is in multiples of θ; ``act_*`` is the neuron's activation
    around the firing (same unit, signed to this pole).  Inside the document an
    empty ``act_*`` cell means the token was not routed to this neuron's
    expert, which is information, not a gap.  Near the document edges the
    window is padded with empty cells as well: only the ``window_before``
    cells next to the firing (``act_m1`` …) and the ``window_after`` cells
    after it (``act_p1`` …) lie inside the document (16 and 12 unless the
    firing is close to its start or end).  Tested firings are topped up to at
    least five per mode, so use ``landscape_level_modes`` - not this table -
    for unbiased mode frequencies.
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
            "level": _int(dot.get("b")),
            "level_token_frequency": _num(band.get("mass")),
            "strength": _num(dot.get("a")),
            "landscape_mode_index": _int(m),
            "mode_kind": "unassigned" if m == last and "closed" not in mode else ("closed_gate" if mode.get("closed") else "open"),
            "subcircuit": f"c{s}" if isinstance(s, int) and s >= 0 else None,
            "gate_closed": dot.get("c"),
            "kl_measured": _num(dot.get("kl")),
            "log10_kl": _num(dot.get("lk")),
            "weight": _num(dot.get("w")),
            "firing_token": tokens[f][0],
            "window_before": len(before),
            "window_after": len(after),
        }
        if text:
            row["text_before"] = "".join(str(t[0]) for t in tokens[:f])
            row["text_after"] = "".join(str(t[0]) for t in tokens[f + 1 :])
        row.update(zip(_ACT_COLUMNS, acts, strict=True))
        yield row


def island_rows(pid: str, isl: dict[str, Any]) -> Iterator[Row]:
    """One row per island.  ``island_mode`` points into ``island_modes`` (unassigned islands: its last row)."""
    view = islands_view(isl)
    mode_of: dict[int, int] = {}
    for k, m in enumerate(view.modes):
        for i in m.islands:
            mode_of.setdefault(i, k)
    shipped = {
        _int(x.get("id")): len(x.get("examples") or []) + len(x.get("examples_more") or [])
        for x in isl.get("islands") or []
        if isinstance(x, dict)
    }
    for island in view.islands:
        k = mode_of.get(island.id)
        yield {
            "pid": pid,
            "island": island.id,
            "number": island.number,
            "island_mode": k,
            "unassigned": k is None or view.modes[k].unassigned,
            "n": island.n,
            "examples_shipped": shipped.get(island.id, 0),
            "name": island.name,
            "description": island.description,
        }


def island_mode_rows(pid: str, isl: dict[str, Any]) -> Iterator[Row]:
    """One row per island mode, in file order, the merged unassigned islands last; ``share`` is "firing X%"."""
    for k, m in enumerate(islands_view(isl).modes):
        yield {
            "pid": pid,
            "island_mode": k,
            "unassigned": m.unassigned,
            "name": m.name,
            "description": m.description,
            "share": m.share,
            "n_islands": len(m.islands),
            "islands": " ".join(str(i) for i in m.islands),
        }


def island_example_rows(pid: str, isl: dict[str, Any]) -> Iterator[Row]:
    """One row per shipped island example, numbered like the API's refs (``island<id>:<slot>``, file order).

    ``activation`` is raw (signed like the neuron's output); ``a_over_theta`` is in multiples of the
    islands data's own ``threshold`` (one per neuron, the circuit data's θ - not ``poles.theta``).
    ``raw_*`` is the raw activation around the peak (16 tokens before, the peak, 12 after; empty
    beyond the passage).  The islands data has no empty values: a 0 there may also be a token that
    was not routed to the neuron's expert.
    """
    theta = island_threshold(isl)
    for island in isl.get("islands") or []:
        iid = _int(island.get("id")) if isinstance(island, dict) else None
        if iid is None:
            continue
        pool = list(island.get("examples") or []) + list(island.get("examples_more") or [])
        for slot, e in enumerate(pool):
            tokens = e.get("tokens") or []
            focal = _int(e.get("focal_idx"))
            inside = focal is not None and 0 <= focal < len(tokens)
            row: Row = {
                "pid": pid,
                "island": iid,
                "slot": slot,
                "ref": f"island{iid}:{slot}",
                "label": e.get("label"),
                "activation": _num(e.get("act")),
                "a_over_theta": _num(e.get("a_over_theta")),
                "threshold": theta,
                "firing_token": str(tokens[focal][0]) if inside else None,
                "focal_index": focal,
                "n_tokens": len(tokens),
                "text": mark_focal(tokens, focal),
            }
            for name, offset in zip(_RAW_COLUMNS, range(-16, 13), strict=True):
                i = (focal + offset) if inside else -1
                item = tokens[i] if 0 <= i < len(tokens) else None
                row[name] = _num(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else None
            yield row


def overview_rows(pid: str, ov: dict[str, Any]) -> Iterator[Row]:
    """The overview of one pole in one row; ``not_this`` holds one look-alike per line."""
    view = overview_view(ov)
    scores = {s["source"]: s["kl_recovered"] for s in view["simulatability"]}
    yield {
        "pid": pid,
        "headline": view["headline"],
        "summary": view["summary"],
        "open": view["open"],
        "not_this": "\n".join(view["not_this"]),
        "final_description": view["final_description"],
        "report": view["report_markdown"],
        "situations_refer_to": view["situations_refer_to"],
        "n_situations": len(view["situations"]),
        "n_experiments": len(view["experiments"]),
        "threshold": view["threshold"],
        **{f"kl_recovered_{source}": scores.get(source) for source in SIMULATION_SOURCES},
        "scientist_model": view["scientist_model"] or None,
    }


def overview_situation_rows(pid: str, ov: dict[str, Any]) -> Iterator[Row]:
    """One row per situation: ``mode_index`` on multi-mode poles, ``subcircuit`` on single-mode poles."""
    view = overview_view(ov)
    for i, s in enumerate(view["situations"]):
        yield {
            "pid": pid,
            "situation": i,
            "refers_to": view["situations_refer_to"],
            "mode_index": s.get("mode_index"),
            "subcircuit": s.get("subcircuit"),
            "name": s["name"],
            "explain": s["explain"],
            "typical_strength": s["typical_strength"],
            "cue": s["cue"],
            "from_experiments": s["from_experiments"],
        }


def overview_experiment_rows(pid: str, ov: dict[str, Any]) -> Iterator[Row]:
    for i, h in enumerate(overview_view(ov)["experiments"]):
        yield {"pid": pid, "experiment": i, "claim": h["claim"], "test": h["test"], "result": h["result"]}


def circuit_exemplar_rows(did: str, exemplars: list[Any], meta: dict[str, Any] | None = None) -> Iterator[Row]:
    """One row per stored example of the circuit data (both poles: ``pole`` follows the sign of ``activation``).

    ``activation = silu(gate_pre) × up_pre``; ``strength`` is ``|activation|`` in multiples of the
    pole's θ of the circuit data (from ``meta``), as the circuit page prints it.
    """
    view = (meta or {}).get("pole_view") or {}
    first = ((meta or {}).get("theta_first") or {}).get("poles") or {}
    theta = {
        word: _num((view.get(sign) or {}).get("theta")) or _num((first.get(long) or {}).get("theta"))
        for sign, word, long in (("+", "pos", "positive"), ("-", "neg", "negative"))
    }
    for e in exemplars:
        if not isinstance(e, dict):
            continue
        act = _num(e.get("act"))
        pole = "pos" if (act or 0.0) > 0 else ("neg" if (act or 0.0) < 0 else None)
        window = e.get("window") if isinstance(e.get("window"), dict) else {}
        tokens = [str(t) for t in window.get("tokens") or []]
        focal = _int(window.get("focal_rel"))
        nxt = e.get("next_token") if isinstance(e.get("next_token"), dict) else {}
        th = theta.get(pole) if pole else None
        yield {
            "hub": did,
            "ex_id": e.get("ex_id"),
            "pole": pole,
            "band": e.get("band"),
            "activation": act,
            "strength": abs(act) / th if act is not None and th else None,
            "gate_pre": _num(e.get("gate_pre")),
            "up_pre": _num(e.get("up_pre")),
            "router_weight": _num(e.get("router_weight")),
            "route_rank": _int(e.get("route_rank")),
            "firing_token": tokens[focal] if focal is not None and 0 <= focal < len(tokens) else None,
            "next_token": nxt.get("str"),
            "next_token_prob": _num(nxt.get("model_prob")),
            "model_top1": nxt.get("model_top1"),
            "text": mark_focal(tokens, focal),
        }


# --------------------------------------------------------------------------- writing


class _CsvSink:
    def __init__(self, path: Path, columns: dict[str, str]):
        self.path = path
        self.columns = list(columns)
        self._fh = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115 - closed in close()
        self._writer: csv.DictWriter[str] | None = None
        self.rows = 0

    def write(self, row: Row) -> None:
        if self._writer is None:
            self._writer = csv.DictWriter(self._fh, fieldnames=self.columns, extrasaction="ignore")
            self._writer.writeheader()
        self._writer.writerow({k: _cell(v) for k, v in row.items()})
        self.rows += 1

    def close(self) -> None:
        self._fh.close()
        if self.rows == 0:
            self.path.unlink(missing_ok=True)


class _ParquetSink:
    """Buffers rows and writes row groups with the table's declared column types; needs pyarrow."""

    def __init__(self, path: Path, columns: dict[str, str], batch: int = 20_000):
        import pyarrow as pa
        import pyarrow.parquet as pq  # noqa: F401 - fail early with a clear message

        self.path = path
        self.batch = batch
        types = {_S: pa.string(), _I: pa.int64(), _F: pa.float64(), _B: pa.bool_()}
        self._schema = pa.schema([pa.field(name, types[kind]) for name, kind in columns.items()])
        self._rows: list[Row] = []
        self._writer: Any = None
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

        if self._writer is None:
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
    except (TypeError, ValueError, OverflowError):
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
    data = _raw_any(atlas, kind, ident)
    return data if isinstance(data, dict) else None


def _raw_any(atlas: Atlas, kind: str, ident: str) -> Any:
    relpath = neuron_path(kind, ident) if kind.startswith("circuit") else pole_path(kind, ident)
    if atlas.source.store.status(relpath) != PRESENT:
        return None
    return atlas.source.get_json(relpath, remote=False)


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

    Returns ``table name -> rows written``; tables without rows are not written.
    Poles whose files are not mirrored still appear in ``poles`` (from the
    index) and are simply missing elsewhere.
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
            sinks[name] = _CsvSink(path, COLUMNS[name]) if fmt == "csv" else _ParquetSink(path, COLUMNS[name])
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
                    pole_modes = {m.label: m.index for m in atlas.catalog.poles[pid].modes}
                    emit("landscape_modes", landscape_mode_rows(pid, ls, pole_modes))
                    emit("landscape_levels", landscape_level_rows(pid, ls))
                    emit("landscape_level_modes", landscape_level_mode_rows(pid, ls))
                    emit("mode_similarity", mode_similarity_rows(pid, ls))
                    emit("firings", firing_rows(pid, ls))
            if wanted & {"islands", "island_modes", "island_examples"}:
                isl = _raw(atlas, "islands", pid)
                if isl:
                    emit("islands", island_rows(pid, isl))
                    emit("island_modes", island_mode_rows(pid, isl))
                    emit("island_examples", island_example_rows(pid, isl))
            if wanted & {"overview", "overview_situations", "overview_experiments"}:
                ov = _raw(atlas, "overview", pid)
                if ov:
                    emit("overview", overview_rows(pid, ov))
                    emit("overview_situations", overview_situation_rows(pid, ov))
                    emit("overview_experiments", overview_experiment_rows(pid, ov))
            did = pid.rsplit("__", 1)[0]
            if did not in done_neurons and wanted & {"circuit_groups", "circuit_edges", "circuit_exemplars"}:
                done_neurons.add(did)
                meta = _raw(atlas, "circuit_meta", did)
                if meta:
                    emit("circuit_groups", circuit_group_rows(did, meta))
                    emit("circuit_edges", circuit_edge_rows(did, meta))
                if "circuit_exemplars" in wanted:
                    exemplars = _raw_any(atlas, "circuit_exemplars", did)
                    if isinstance(exemplars, list):
                        emit("circuit_exemplars", circuit_exemplar_rows(did, exemplars, meta))
            if on_progress and (i % 25 == 0 or i == len(pid_list)):
                on_progress(i, len(pid_list))
    finally:
        for target in sinks.values():
            target.close()
    return {name: target.rows for name, target in sinks.items() if target.rows}
