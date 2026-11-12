"""Normalised views of the Overview, Landscape and Circuit data sets.

Each function takes the raw upstream JSON of one file and returns a plain
dictionary with our own, documented keys.  Heavy arrays (token traces, dot
clouds, incidence matrices) are summarised here; the geometry tools and the
``raw`` endpoints give access to all of it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from typing import Any

from .search import SCOPE_DEEP, TextField

#: Order in which the Overview lists the description sources it scored.
SIMULATION_SOURCES = ("fleet", "g5m", "census", "h1", "own")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _is_hex_colour(value: Any) -> bool:
    """Upstream colours end up in style attributes of pages; accept nothing but #rgb / #rrggbb."""
    return isinstance(value, str) and re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value) is not None


# --------------------------------------------------------------------------- overview


def situation_unit(raw: dict[str, Any]) -> str:
    """What ``explain.situations[].mode`` counts: modes, or - on single-mode poles - subcircuits."""
    unit = (raw.get("islands_view") or {}).get("unit")
    return "subcircuit" if unit == "island" else "mode"


def overview_view(raw: dict[str, Any], research: bool = False) -> dict[str, Any]:
    """The written explanation of a pole (the "neuron scientist" page).

    Keys: ``headline`` and ``summary`` in plain language; ``situations`` (one
    entry per mode, or per subcircuit on single-mode poles - see
    ``situations_refer_to``); ``not_this`` (look-alikes that leave the neuron
    silent); ``experiments`` (controlled tests: claim / test / result);
    ``open`` (what stayed unexplained); ``final_description`` and
    ``report_markdown`` (the scientist's own technical write-up, strengths in
    multiples of the threshold θ); ``simulatability`` (how well each
    description predicts the neuron); ``circuit_digest`` (named input and
    reader groups and the output tokens most affected).
    """
    explain = raw.get("explain") if isinstance(raw.get("explain"), dict) else {}
    unit = situation_unit(raw)
    situations = []
    for s in explain.get("situations") or []:
        if not isinstance(s, dict):
            continue
        index = _int(s.get("mode"))
        item = {
            "name": _text(s.get("name")),
            "explain": _text(s.get("explain")),
            "typical_strength": _text(s.get("typical")),
            "cue": _text(s.get("cue")),
            "from_experiments": _text(s.get("scientist")),
        }
        if unit == "mode":
            item["mode_index"] = index
        else:
            item["subcircuit"] = f"c{index}" if index is not None else None
        situations.append(item)

    scores = []
    kl = raw.get("kl") if isinstance(raw.get("kl"), dict) else {}
    for key in SIMULATION_SOURCES:
        entry = kl.get(key)
        if isinstance(entry, dict):
            scores.append(
                {
                    "source": key,
                    "label": _text(entry.get("label")),
                    "kl_recovered": _num(entry.get("kl_recovered")),
                    "is_this_description": key == "own",
                }
            )

    circuit = raw.get("circuit") if isinstance(raw.get("circuit"), dict) else {}
    digest = {
        "note": _text(circuit.get("note")),
        "inputs": [
            {
                "name": _text(i.get("name")),
                "effect": i.get("effect"),
                "role": i.get("role"),
                "share": _num(i.get("share")),
            }
            for i in circuit.get("inputs") or []
            if isinstance(i, dict)
        ],
        "readers": [
            {"name": _text(r.get("name")), "effect": r.get("effect"), "n": _int(r.get("n"))}
            for r in circuit.get("readers") or []
            if isinstance(r, dict)
        ],
        "output_tokens": [
            {"token": _text(t.get("token")), "weight": _num(t.get("w"))}
            for t in circuit.get("logits") or []
            if isinstance(t, dict)
        ],
    }

    view: dict[str, Any] = {
        "headline": _text(explain.get("headline")),
        "summary": _text(explain.get("summary")),
        "situations_refer_to": unit,
        "situations": situations,
        "not_this": [t for t in explain.get("not_this") or [] if isinstance(t, str)],
        "experiments": [
            {"claim": _text(h.get("claim")), "test": _text(h.get("test")), "result": _text(h.get("result"))}
            for h in explain.get("how_we_know") or []
            if isinstance(h, dict)
        ],
        "open": _text(explain.get("open")),
        "subcircuit_names": {f"c{k}": v for k, v in (explain.get("contexts") or {}).items() if isinstance(v, str)},
        "final_description": _text(raw.get("pick")),
        "report_markdown": _text(raw.get("report")),
        "simulatability": scores,
        "threshold": _num(raw.get("threshold_raw")),
        "scientist_model": _text(raw.get("scientist_model")),
        "circuit_digest": digest,
    }
    if research:
        view["research"] = {
            "atlas_label": _text(raw.get("atlas_label")),
            "category": raw.get("cat"),
            "showcase_examples": raw.get("showcase") or [],
            "first_hypothesis_differs": _text(raw.get("first_hypothesis")) != _text(raw.get("pick")),
            "long_context_documents": len(raw.get("mode_docs") or []) + len(raw.get("full_docs") or {}),
        }
    return view


def overview_texts(raw: dict[str, Any]) -> Iterator[TextField]:
    """Searchable texts of an Overview file."""
    explain = raw.get("explain") if isinstance(raw.get("explain"), dict) else {}
    unit = situation_unit(raw)
    for key in ("headline", "summary", "open"):
        if _text(explain.get(key)):
            yield TextField("overview", explain[key], scope=SCOPE_DEEP)
    for s in explain.get("situations") or []:
        if not isinstance(s, dict):
            continue
        index = _int(s.get("mode"))
        mode_index = index if unit == "mode" else None
        ref = f"c{index}" if unit == "subcircuit" and index is not None else None
        text = " — ".join(t for t in (_text(s.get("name")), _text(s.get("explain")), _text(s.get("scientist"))) if t)
        if text:
            yield TextField("overview", text, mode_index=mode_index, ref=ref, scope=SCOPE_DEEP)
    for line in explain.get("not_this") or []:
        if _text(line):
            yield TextField("overview", "Does not fire on: " + line, scope=SCOPE_DEEP)
    for h in explain.get("how_we_know") or []:
        if isinstance(h, dict):
            text = " ".join(t for t in (_text(h.get("claim")), _text(h.get("test")), _text(h.get("result"))) if t)
            if text:
                yield TextField("overview", text, scope=SCOPE_DEEP)
    for key in ("pick", "report"):
        if _text(raw.get(key)):
            yield TextField("report", raw[key], scope=SCOPE_DEEP)
    circuit = raw.get("circuit") if isinstance(raw.get("circuit"), dict) else {}
    names = [_text(i.get("name")) for i in (circuit.get("inputs") or []) + (circuit.get("readers") or []) if isinstance(i, dict)]
    names = [n for n in names if n]
    if names:
        yield TextField("circuit", " · ".join(names), scope=SCOPE_DEEP)


# --------------------------------------------------------------------------- landscape

#: The Overview's names for the five rarity levels (weakest first).
LEVEL_LABELS = ("Top 5%", "Top 1%", "Top 0.1%", "Top 0.01%", "Strongest 1,000")
UNASSIGNED_LABEL = "Unassigned examples (fit no mode)"


def mode_numbers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-mode numbers exactly as the Overview computes them from the five rarity levels.

    For each mode (unassigned bucket included, as the last entry): ``firing_share``
    and ``damage_share`` weighted by the natural frequency of each rarity level,
    ``damage_per_firing`` as a ratio to the pole's mean damage per firing, and
    ``tested_firings`` (firings whose damage was measured rather than estimated).
    """
    modes = raw.get("modes") or []
    bands = raw.get("bands") or []
    k_count = len(modes)
    has_damage = bool(bands) and isinstance(bands[0].get("dmg_lin"), list)
    firings = [0.0] * k_count
    damage = [0.0] * k_count
    for band in bands:
        n = _num(band.get("n")) or 0.0
        mass = _num(band.get("mass")) or 0.0
        if n <= 0:
            continue
        weight = mass / n
        counts = band.get("counts") or []
        dmg = band.get("dmg_lin") or []
        for k in range(k_count):
            if k < len(counts):
                firings[k] += weight * (_num(counts[k]) or 0.0)
            if has_damage and k < len(dmg):
                damage[k] += weight * (_num(dmg[k]) or 0.0)
    total_f = sum(firings) or 1.0
    total_d = sum(damage) or 1.0
    average = total_d / total_f
    tested = [0] * k_count
    for dot in raw.get("dots") or []:
        m = _int(dot.get("m"))
        kl = _num(dot.get("kl"))
        if m is not None and 0 <= m < k_count and kl is not None and kl > 0:
            tested[m] += 1
    out = []
    for k in range(k_count):
        per_firing = (damage[k] / firings[k]) / average if (has_damage and firings[k] > 0 and average > 0) else None
        out.append(
            {
                "firing_share": firings[k] / total_f,
                "damage_share": damage[k] / total_d if has_damage else None,
                "damage_per_firing": per_firing,
                "tested_firings": tested[k],
            }
        )
    return out


def _relation_text(mode: dict[str, Any], modes: list[dict[str, Any]]) -> str:
    """The sentence the explorer prints under a Q3/Q4 mode."""
    fold = _int(mode.get("fold"))
    rel = mode.get("rel")
    if fold is not None and 0 <= fold < len(modes):
        target = _text(modes[fold].get("label"))
        return f"Same meaning as “{target}”" if rel == "SAME" else f"A narrower case of “{target}”"
    if rel == "SUPERSET":
        return "A broader version of another mode"
    return "A meaning of its own"


def landscape_view(raw: dict[str, Any], *, matrices: bool = True) -> dict[str, Any]:
    """The firing landscape of a pole: how its firings split over modes at five levels of rarity.

    The explorer draws this as a swarm chart; the file holds no coordinates.
    What it does hold, and what this view returns: the modes in landscape order
    (open modes, then Q3/Q4 modes, then the unassigned bucket) with the numbers
    the Overview prints (``firing_share``, ``damage_share``,
    ``damage_per_firing``), the five ``levels`` (strength, frequency in real
    text, firings and damage per mode), the damage-versus-strength fit, and -
    with ``matrices`` - the two mode × mode similarity matrices.
    """
    modes = [m for m in raw.get("modes") or [] if isinstance(m, dict)]
    numbers = mode_numbers(raw)
    last = len(modes) - 1
    colours = raw.get("rel_col") or []
    out_modes = []
    best, best_share = None, -1.0
    for k, m in enumerate(modes):
        unassigned = k == last and "closed" not in m
        kind = "unassigned" if unassigned else ("closed_gate" if m.get("closed") else "open")
        nums = numbers[k] if k < len(numbers) else {}
        item = {
            "index": k,
            "label": _text(m.get("label")),
            "description": _text(m.get("description")),
            "kind": kind,
            "sample_share": _num(m.get("share")),
            **nums,
            "colour": colours[k] if k < len(colours) and _is_hex_colour(colours[k]) else None,
        }
        if kind == "closed_gate":
            item["relation"] = {
                "kind": m.get("rel"),
                "to_mode": _int(m.get("fold")),
                "text": _relation_text(m, modes),
            }
        out_modes.append(item)
        share = nums.get("damage_share")
        if not unassigned and share is not None and share > best_share:
            best, best_share = k, share

    levels = []
    for i, band in enumerate(raw.get("bands") or []):
        mass = _num(band.get("mass"))
        levels.append(
            {
                "index": i,
                "name": LEVEL_LABELS[i] if i < len(LEVEL_LABELS) else _text(band.get("name")),
                "source_name": _text(band.get("name")),
                "median_strength": _num(band.get("a_med")),
                "min_strength": _num(band.get("a_min")),
                "max_strength": _num(band.get("a_max")),
                "token_frequency": mass,
                "one_in_tokens": round(1.0 / mass) if mass else None,
                "sampled_firings": _int(band.get("n")),
                "firings_by_mode": band.get("counts") or [],
                "damage_by_mode": band.get("dmg_lin") or [],
            }
        )

    dots = raw.get("dots") or []
    fit = raw.get("kl_fit") if isinstance(raw.get("kl_fit"), dict) else {}
    view: dict[str, Any] = {
        "verdict": raw.get("verdict"),
        "sampled_firings": _int(raw.get("n_firings")),
        "modes": out_modes,
        "top_damage_mode": best,
        "levels": levels,
        "damage_fit": {
            "slope": _num(fit.get("slope")),
            "r2": _num(fit.get("r2")),
            "tested_firings": _int(fit.get("n")),
            "note": "damage grows as strength^slope; untested firings are estimated from strength and mode",
        },
        "mode_order": raw.get("rel_order") or raw.get("order") or [],
        "firings_listed": len(dots),
        "gate_closed_firings_listed": sum(1 for d in dots if d.get("c")),
        "strength_unit": "multiples of the pole's firing threshold θ",
    }
    if matrices:
        view["mode_similarity"] = {
            "by_description": raw.get("rel_sim"),
            "by_firing": raw.get("sim"),
            "note": "rows/columns follow modes[] without the unassigned bucket",
        }
    return view


# --------------------------------------------------------------------------- circuit

#: Members listed per group in the normalised view (the raw file has them all).
CIRCUIT_MEMBERS = 12


def circuit_view(meta: dict[str, Any], pole: str, catalog: Any = None, *, members: int = CIRCUIT_MEMBERS) -> dict[str, Any]:
    """The circuit of one pole: earlier neurons that feed it, later neurons that read it.

    The neuron computes ``gate × up`` (the gate opens or closes it, ``up``
    carries magnitude and sign).  ``inputs`` are groups of earlier neurons
    with their summed signed contribution to the ``gate`` and ``up`` signals
    (positive raises the signal; the two are never added) and their ``role``
    for this pole (``drives`` / ``opposes``).  ``readers`` are groups of later
    neurons this pole ``raised`` or ``lowered``, with the number of examples in
    which that showed.  ``output_tokens`` are the vocabulary tokens whose
    logits the neuron pushes most on this pole: ``weight`` is the push on the
    logit when this pole fires (> 0 = raised, on both poles); ``static_weight``
    is the stored static weight (negative on the negative pole, whose negative
    activation turns it into a raise).
    ``example_tokens`` are the tokens the circuit data also follows on its
    stored examples (mostly frequent ones such as ``,`` or `` the``): their
    weight is the mean *absolute* logit effect over the examples of both poles,
    so it says nothing about direction and is the same on both poles.

    Shape::

        {"neuron", "pole", "line", "examples_analysed", "threshold", "notes": [...],
         "inputs":  [{"id", "name", "description", "role": "drives"|"opposes", "lane" (upstream's tag, e.g.
                      "gate + / up −"), "gate", "up" (summed signed contributions), "effect" (upstream's net
                      sign of the two), "share", "about": [{"name", "description", "n"}], "n_members",
                      "members": [{"unit", "pole", "label", "in_atlas", "gate", "up",
                                   "weight" (the larger of gate/up by magnitude), "weight_lane"}]}],
         "readers": [{"id", "name", "description", "effect": "+"|"-", "examples", "mean_effect",
                      "label_of_other_pole" (true when the group belongs to the other pole, whose label
                      ``effect`` then is), "measured_direction" ("raised"|"lowered", when the measurement on
                      this pole contradicts ``effect``), "n_members",
                      "members": [{"unit", "pole", "label", "in_atlas", "weight" (magnitude), "layers_ahead"}]}],
         "output_tokens": [{"token", "weight" (push on this pole's logit, > 0 = raised), "static_weight"}],
         "example_tokens": [{"token", "mean_abs_effect", "examples_active"}],
         "strongest_contexts": [{"text" (firing token marked «…»), "activation" (raw)}]}

    The threshold here is the circuit data's own θ (one per neuron), not the pole's θ of the mode data.
    """
    from .geometry.graph import (
        clean_group_description,
        clean_group_name,
        group_side,
        input_role,
        lane_tag,
        pole_word,
        reader_effect,
    )

    sign = "+" if pole == "pos" else "-"
    identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
    groups = meta.get("groups") if isinstance(meta.get("groups"), dict) else {}
    side = (meta.get("pole_view") or {}).get(sign) if isinstance(meta.get("pole_view"), dict) else None
    side = side if isinstance(side, dict) else {}
    known = getattr(catalog, "neurons", {}) if catalog is not None else {}
    notes: list[str] = []

    def member(m: dict[str, Any], *, reader: bool) -> dict[str, Any]:
        unit = _text(m.get("did"))
        out: dict[str, Any] = {
            "unit": unit,
            "pole": pole_word(m.get("pole")),
            "label": _text(m.get("label_full")) or _text(m.get("label")),
            "in_atlas": unit in known,
        }
        if reader:
            w = _num(m.get("w"))
            out["weight"] = abs(w) if w is not None else None
            out["layers_ahead"] = _int(m.get("hop"))
        else:
            gate, up = _num(m.get("gate_s")), _num(m.get("up_s"))
            out["gate"], out["up"] = gate, up
            # One number for compact listings: the lane on which the unit weighs most (and which lane that is).
            lanes = [(v, name) for v, name in ((gate, "gate"), (up, "up")) if v is not None]
            out["weight"], out["weight_lane"] = max(lanes, key=lambda x: abs(x[0]), default=(None, None))
        return out

    share = {str(i.get("id")): _num(i.get("mass_share")) for i in side.get("inputs") or [] if isinstance(i, dict)}
    inputs = []
    for g in groups.get("input") or []:
        if not isinstance(g, dict) or group_side(g) not in (sign, None):
            continue
        agg = g.get("agg") if isinstance(g.get("agg"), dict) else {}
        gate = _num((agg.get("gate") or {}).get("mean_signed"))
        up = _num((agg.get("up") or {}).get("mean_signed"))
        ranked = sorted(
            (m for m in g.get("members") or [] if isinstance(m, dict)),
            key=lambda m: -(abs(_num(m.get("gate_s")) or 0.0) + abs(_num(m.get("up_s")) or 0.0)),
        )
        net = (gate or 0.0) + (up or 0.0)
        inputs.append(
            {
                "id": g.get("id"),
                "name": clean_group_name(g),
                "description": clean_group_description(g),
                "role": input_role(g),
                "lane": lane_tag(g),
                "gate": gate,
                "up": up,
                "effect": "+" if net > 0 else ("-" if net < 0 else None),
                "share": share.get(str(g.get("id"))),
                "about": [
                    {"name": _text(sg.get("name")), "description": _text(sg.get("description")), "n": len(sg.get("units") or [])}
                    for sg in g.get("subgroups") or []
                    if isinstance(sg, dict) and sg.get("name")
                ],
                "n_members": len(ranked),
                "members": [member(m, reader=False) for m in ranked[:members]],
            }
        )
    inputs.sort(key=lambda g: -max(abs(g["gate"] or 0.0), abs(g["up"] or 0.0)))

    by_id = {str(g.get("id")): g for g in groups.get("readers") or [] if isinstance(g, dict)}
    readers = []
    listed = [r for r in side.get("readers") or [] if isinstance(r, dict)]
    if listed:
        for row in listed:
            g = by_id.get(str(row.get("id")))
            if g is None:
                continue
            mean = _num(row.get("mean_w"))
            ranked = sorted(
                (m for m in g.get("members") or [] if isinstance(m, dict)), key=lambda m: -abs(_num(m.get("w")) or 0.0)
            )
            item = {
                "id": g.get("id"),
                "name": clean_group_name(g),
                "description": clean_group_description(g),
                "effect": "-" if row.get("effect") == "-" else "+",
                "examples": _int(row.get("n_ex")),
                "mean_effect": mean,
                "n_members": len(ranked),
                "members": [member(m, reader=True) for m in ranked[:members]],
            }
            # A group that belongs to the other pole keeps that pole's label in the explorer; the measured
            # mean then tells the real direction on this pole.  The mean is rounded to 4 decimals, so a
            # small one is stored as a signed zero - its sign is still the direction.
            home = group_side(g)
            if home is not None and home != sign:
                item["label_of_other_pole"] = True
            if mean is not None and (mean != 0 or home not in (None, sign)):
                measured = "raised" if math.copysign(1.0, mean) > 0 else "lowered"
                if (measured == "raised") != (item["effect"] == "+"):
                    item["measured_direction"] = measured
            readers.append(item)
    else:
        for g in by_id.values():
            if g.get("is_misc") or group_side(g) != sign:
                continue
            ranked = sorted(
                (m for m in g.get("members") or [] if isinstance(m, dict)), key=lambda m: -abs(_num(m.get("w")) or 0.0)
            )
            readers.append(
                {
                    "id": g.get("id"),
                    "name": clean_group_name(g),
                    "description": clean_group_description(g),
                    "effect": "-" if reader_effect(g) == "lowered" else "+",
                    "examples": None,
                    "mean_effect": None,
                    "n_members": len(ranked),
                    "members": [member(m, reader=True) for m in ranked[:members]],
                }
            )

    if side.get("pole_real") is False:
        notes.append("This pole of the neuron fires too rarely to have a circuit to show.")
    line = _text(side.get("line")) or _text((identity.get("labels") or {}).get(f"short_title_{pole}"))
    if line.startswith("no discriminative reading"):
        line = "Uninterpretable: " + line[len("no discriminative reading") :].lstrip(" :;,-")
    want = 1 if pole == "pos" else -1
    logits = [t for t in meta.get("logits") or [] if isinstance(t, dict)]
    # Rows with source "target" carry an unsigned mean over the examples of both poles (their sign is
    # always 1): they belong to neither pole and are listed apart.
    tokens = []
    for t in logits:
        if t.get("source") == "target" or t.get("sign") not in (None, want):
            continue
        w = _num(t.get("w"))
        tokens.append({"token": _text(t.get("token")), "weight": None if w is None else w * want, "static_weight": w})
    example_tokens = [
        {"token": _text(t.get("token")), "mean_abs_effect": _num(t.get("w")), "examples_active": _int(t.get("n_active"))}
        for t in logits
        if t.get("source") == "target"
    ]
    return {
        "neuron": _text(identity.get("did")),
        "pole": pole,
        "line": line,
        "examples_analysed": _int(side.get("n_admitted")),
        "threshold": _num(side.get("theta")),
        "inputs": inputs,
        "readers": readers,
        "output_tokens": tokens,
        "example_tokens": example_tokens,
        "strongest_contexts": [
            {"text": _text(c.get("ctx")).replace("▸", "«").replace("◂", "»"), "activation": _num(c.get("act"))}
            for c in side.get("top_contexts") or []
            if isinstance(c, dict)
        ],
        "notes": notes,
    }
