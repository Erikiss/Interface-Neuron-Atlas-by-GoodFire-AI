"""A tiny synthetic atlas release for tests.

Everything here is invented: the texts are made up, the numbers are chosen so
that assertions are easy to read.  Only the *shape* follows the real files
(see docs/upstream-schema/), so the tests exercise the real parsers without
shipping any of Goodfire's data.

The release has six neurons:

``L4_E1_N10``    both poles fully described (subcircuit, overview, landscape, islands, circuit)
``L9_E2_N20``    pos: one dominant mode (fn); neg: no subcircuit read, islands only, no description (dark)
``L14_E78_N5``   pos only; islands only; belongs to the expert the public sample leaves out
``L30_E3_N7``    pos: a subcircuit read without modes (failed read); neg: two modes
``L39_E4_N1``    both poles, index level only
``L2_E5_N6``     known to the circuit index only (no poles)
"""

from __future__ import annotations

import json
from typing import Any

RELEASE_URL = "https://static.goodfire.ai/neuron-atlas/test-release/"
EXPLORER_PAGE = "https://www.goodfire.com/research/public_neuron_atlas"

RIVER = "L4_E1_N10__pos"  # mono: river names (+ closed-gate + residue)
MIXED = "L4_E1_N10__neg"  # poly: three modes
FN = "L9_E2_N20__pos"
DARK = "L9_E2_N20__neg"
ISLAND_ONLY = "L14_E78_N5__pos"
FAILED_READ = "L30_E3_N7__pos"


def _tokens(words: list[str], focal: int, peak: float, sign: int = 1) -> list[list[Any]]:
    """33-token window (16 before, firing token, 16 after) with raw-signed values in θ units."""
    left = [""] * max(0, 16 - focal) + words[max(0, focal - 16) : focal]
    right = words[focal + 1 : focal + 17]
    right += ["<|im_end|>"] * (16 - len(right))
    window = left + [words[focal]] + right
    out: list[list[Any]] = []
    for i, text in enumerate(window):
        if i == 16:
            out.append([text, round(peak * sign, 3)])
        elif i in (15, 17) and text not in ("", "<|im_end|>"):
            out.append([text, round(0.2 * sign, 3)])
        elif i == 12 and text:
            out.append([text, round(-0.4 * sign, 3)])  # the opposite pole fires a little
        else:
            out.append([text, None])
    return out


def _exemplars(sentence: str, focal_word: str, theta: float, sign: int, closed: bool = False) -> list[dict[str, Any]]:
    words = [(" " if i else "") + w for i, w in enumerate(sentence.split())]
    focal = next(i for i, w in enumerate(words) if w.strip() == focal_word)
    out = []
    strengths = [9.0, 8.0, 7.0, 6.0, 3.0, 2.8, 2.6, 2.4, 0.4, 0.5, 0.6, 0.7]
    for slot, a_rel in enumerate(strengths):
        out.append(
            {
                "a": round(a_rel * theta * sign, 4),
                "a_rel": a_rel,
                "closed": closed,
                "fold": "audit" if slot % 4 == 3 else "fit",
                "pre": f"Earlier text number {slot} that sets the scene. ",
                "tokens": _tokens(words, focal, a_rel, sign),
                "focal": 16,
                "repeats": 3 if slot == 1 else 1,
            }
        )
    return out


def _units(prefix_layer: int, n: int, base: float, signed: bool = False) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        leaf = round(base / (i + 1), 4)
        if signed and i % 2:
            leaf = -leaf
        out.append({"unit": f"L{prefix_layer}_E{7 + i}_N{100 + i}", "leaf": leaf, "rest": 0.0001, "label": f"Invented unit label {i}"})
    return out


def _subcircuit(sid: str, n: int, share: float, label: str, sentence: str, focal: str, theta: float, sign: int, layer: int, *, closed: bool = False, readable: bool = True) -> dict[str, Any]:
    return {
        "id": sid,
        "n": n,
        "share": share,
        "profile": {"ge_theta": 0.1 if closed else 0.7, "mid": 0.05 if closed else 0.2, "weak": 0.05, "closed": 0.8 if closed else 0.05},
        "median_a_rel": 0.6 if closed else 2.7,
        "meaning": f"Meaning of {label}: what these firings share.",
        "token_pattern": f"The token '{focal}' in this kind of sentence.",
        "readable": readable,
        "reading_confidence": 4,
        "function": f"Combines earlier signals to mark {label}.",
        "function_label": label,
        "gate_role": "Opens on travel writing (L2_E7_N100).",
        "up_role": "Raised by geography words.",
        "function_confidence": 3,
        "circuit_desc": None,
        "circtext_desc": None,
        "spec_flags": [{"rejected": "An alternative reading that was tested.", "p": 0.1, "p_after": 0.0}] if sid == "c0" else None,
        "circuit": {
            "gate_drivers": _units(max(0, layer - 2), 3, 0.03),
            "gate_opposers": _units(max(0, layer - 3), 2, 0.01),
            "up_drivers": _units(max(0, layer - 1), 3, 0.05),
            "up_opposers": _units(max(0, layer - 4), 2, 0.008),
            "readers": _units(layer + 5, 3, 0.02, signed=True),
        },
        "unique_contexts": n,
        "top_context_share": round(1 / n, 4),
        "exemplars": _exemplars(sentence, focal, theta, sign, closed=closed),
        "kl": {"per_firing": 1e-5 * (1 + int(sid[1:])), "ratio": round(0.5 + int(sid[1:]), 3), "n_measured": 12},
    }


def subcircuit_file(pid: str) -> dict[str, Any]:
    layer = int(pid.split("_")[0][1:])
    sign = 1 if pid.endswith("__pos") else -1
    theta = 0.25
    if pid == RIVER:
        subs = {
            "c0": _subcircuit("c0", 2400, 0.48, "River names in travel writing", "We crossed the wide Danube near the old stone bridge at dawn", "Danube", theta, sign, layer),
            "c1": _subcircuit("c1", 1100, 0.22, "Rivers named in poems", "Sing again of the quiet Loire and its slow green water", "Loire", theta, sign, layer),
            "c2": _subcircuit("c2", 1000, 0.20, "Weak leak on list items", "item four then item five then item six in the list", "five", theta, sign, layer, closed=True),
            "c3": _subcircuit("c3", 500, 0.10, "Unreadable scraps", "zz qq zz qq zz kk zz qq zz", "kk", theta, sign, layer, readable=False),
        }
        modes = [
            {"label": "River names (Danube, Loire) in travel writing and verse", "description": "Fires on the name of a river.", "subcircuits": ["c0", "c1"], "share": 0.7, "n_firings": 3500, "unique_contexts": None, "top_context_share": None, "merged_from": None, "kl": {"per_firing": 2e-5, "ratio": 1.4}},
            {"label": "Closed-gate firings (silu leak)", "description": "Subcircuits whose firings mostly occur with the neuron's gate closed.", "subcircuits": ["c2"], "share": 0.2, "residue": True, "closed_gate": True, "kl": {"per_firing": 1e-6, "ratio": 0.05}},
            {"label": "Residue (unreadable)", "description": "Subcircuits the merge set aside as unreadable.", "subcircuits": ["c3"], "share": 0.1, "residue": True, "kl": {"per_firing": 1e-6, "ratio": 0.02}},
        ]
        verdict, fn_mono, mds = "monosemantic", False, 1.0
    elif pid == MIXED:
        subs = {
            "c0": _subcircuit("c0", 2000, 0.4, "Bread baking steps", "Knead the dough and let the sourdough rest for an hour", "sourdough", theta, sign, layer),
            "c1": _subcircuit("c1", 1500, 0.3, "Chess openings", "She answered with the Sicilian defence against the king pawn", "Sicilian", theta, sign, layer),
            "c2": _subcircuit("c2", 1500, 0.3, "Hexadecimal colour codes", "Set the background to #ff8800 and the text to black", "#ff8800", theta, sign, layer),
        }
        modes = [
            {"label": "Sourdough and bread baking steps", "description": "Fires on baking vocabulary such as 'sourdough'.", "subcircuits": ["c0"], "share": 0.4, "n_firings": 2000, "kl": {"per_firing": 1e-5, "ratio": 0.5}},
            {"label": "Chess opening names (Sicilian, Caro-Kann)", "description": "Fires on the name of a chess opening.", "subcircuits": ["c1"], "share": 0.3, "n_firings": 1500, "kl": {"per_firing": 2e-5, "ratio": 1.0}},
            {"label": "Hexadecimal colour codes (#ff8800)", "description": "Fires inside hex colour codes | in stylesheets.", "subcircuits": ["c2"], "share": 0.3, "n_firings": 1500, "kl": {"per_firing": 3e-5, "ratio": 1.5}},
        ]
        verdict, fn_mono, mds = "polysemantic", False, 0.5
    else:
        raise KeyError(pid)
    return {
        "pid": pid, "layer": layer, "expert": int(pid.split("_")[1][1:]), "neuron": int(pid.split("_")[2][1:]),
        "sign": sign, "theta": theta, "label": "Automatic label of the whole neuron (invented).",
        "closed_share": 0.2, "n_firings": 5000, "verdict": verdict,
        "summary": "A written summary of this pole across its modes (invented).",
        "judge": {"opus": {}, "jev": {"all": {"n": 100, "acc": 0.6, "chance": 0.3}}},
        "partition": "leiden_test", "modes": modes, "subcircuits": subs, "fn_mono": fn_mono,
        "main_damage_share": mds, "kl_neuron_mean": 1.5e-5,
        "unified_description": "Mainly river names such as «Danube» in travel writing." if pid == RIVER else "Three unrelated things.",
    }  # fmt: skip


def landscape_file(pid: str) -> dict[str, Any]:
    sub = subcircuit_file(pid)
    sign = sub["sign"]
    open_modes = [m for m in sub["modes"] if not m.get("residue")]
    modes = [{"label": m["label"], "description": m["description"], "share": m["share"], "kl_per_firing": m["kl"]["per_firing"], "closed": False, "rel": None, "fold": None} for m in open_modes]
    if pid == RIVER:
        modes.append({"label": "List items read through the leak", "description": "A closed-gate reading.", "share": 0.2, "kl_per_firing": None, "closed": True, "rel": "SUBCASE", "fold": 0})
    modes.append({"label": "Unassigned examples (fit no mode)", "description": "", "share": None, "kl_per_firing": None})
    k = len(modes)
    names = ["p95", "p99", "p99.9", "p99.99", "top 1,000"]
    masses = [0.04, 0.009, 0.0009, 0.00009, 0.00001]
    bands = []
    for i in range(5):
        counts = [0] * k
        counts[0] = 600 + 50 * i
        for j in range(1, k):
            counts[j] = (1000 - counts[0]) // (k - 1)
        counts[-1] += 1000 - sum(counts)
        dmg = [round(c * 1e-6 * (j + 1) * (i + 1), 9) for j, c in enumerate(counts)]
        bands.append({"name": names[i], "a_med": round(0.5 * (i + 1), 3), "a_min": round(0.4 * (i + 1), 3), "a_max": round(0.9 * (i + 1), 3), "mass": masses[i], "n": 1000, "counts": counts, "dmg_lin": dmg, "dmg": [round(d**0.25, 6) for d in dmg]})
    dots = []
    words = [" one", " two", " three", " Danube", " four", " five"]
    for j in range(24):
        level = j % 5
        mode = j % k
        tested = j < 12
        window: list[list[Any]] = [[w, None] for w in words[:2]] + [[w, 0.1] for w in words[2:3]] + [[words[3], round(0.5 * (level + 1), 2)]] + [[w, None] for w in words[4:]]
        dots.append({"b": level, "a": round(0.45 * (level + 1) + 0.01 * j, 3), "m": mode, "s": mode if mode < len(sub["subcircuits"]) else -1, "c": mode == k - 2 and pid == RIVER, "kl": 1e-6 * (j + 1) if tested else None, "lk": -6.0 + 0.01 * j, "w": round(masses[level] / masses[0], 6), "t": window, "f": 3, "u": 2})
    o = k - 1
    rel = [[1.0 if a == b else round(0.5 + 0.1 * abs(a - b) / max(1, o), 4) for b in range(o)] for a in range(o)]
    return {
        "pid": pid, "layer": sub["layer"], "expert": sub["expert"], "neuron": sub["neuron"], "sign": sign,
        "verdict": sub["verdict"], "n_firings": 5000, "modes": modes, "bands": bands,
        "kl_fit": {"slope": 2.5, "r2": 0.6, "n": 12, "q": 0.25}, "order": list(range(o)),
        "sim": rel if o > 2 else None, "dots": dots, "rel_sim": rel, "rel_order": list(range(o)),
        "rel_col": ["#008b85", "#586ec0", "#7f61b6", "#a5538d"][:o],
    }  # fmt: skip


def overview_file(pid: str) -> dict[str, Any]:
    sub = subcircuit_file(pid)
    mono = sub["verdict"] == "monosemantic"
    situations = (
        [{"mode": 0, "name": "Rivers in travel writing", "explain": "Fires on river names in travel prose.", "typical": "about 6–9×", "cue": "crossed the wide ⟦Danube⟧", "scientist": "Swapping the river for a city dropped it to 0.2×."},
         {"mode": 1, "name": "Rivers in verse", "explain": "Fires on river names in poems.", "typical": "about 3×", "cue": "the quiet ⟦Loire⟧", "scientist": ""}]
        if mono
        else [{"mode": i, "name": m["label"][:30], "explain": f"Explains mode {i}.", "typical": "2–4×", "cue": "a ⟦cue⟧", "scientist": "Measured."} for i, m in enumerate(sub["modes"])]
    )
    return {
        "pid": pid, "did": pid.rsplit("__", 1)[0], "pole": pid.rsplit("__", 1)[1], "side": "positive" if sub["sign"] > 0 else "negative",
        "layer": sub["layer"], "expert": sub["expert"], "neuron": sub["neuron"], "cat": "mono" if mono else "poly",
        "cat_public": "single mode" if mono else "multiple modes", "atlas_label": "Island-level one-liner (invented)",
        "threshold_raw": sub["theta"],
        "pick": "The scientist's final description. θ is the firing threshold.",
        "first_hypothesis": "The scientist's final description. θ is the firing threshold.",
        "report": "# Report on this pole\n\n**What it is.** An invented report.\n\n1. First finding with `code`.\n2. Second finding.",
        "learned": None,
        "kl": {"own": {"label": "This description", "kl_recovered": 0.81}, "fleet": {"label": "Automatic label", "kl_recovered": 0.42}} if pid == RIVER else {},
        "explain": {
            "headline": "River names, mostly in travel writing" if mono else "Bread, chess and colour codes",
            "summary": "This side of the neuron fires on the names of rivers." if mono else "This side fires on three unrelated things.",
            "situations": situations,
            "not_this": ["City names: 'Vienna' stays near 0.", "Lakes reach at most 1×."],
            "how_we_know": [{"claim": "It is the river name that matters.", "test": "Replaced the river with a city.", "result": "Fell from 8× to 0.2×."}],
            "open": "We do not know what later layers do with it.",
            "contexts": {"0": "Travel prose", "1": "Verse"},
            "_model": "test-model",
        },
        "circuit": {"note": "", "inputs": [{"name": "Geography words", "effect": "+", "role": "drives", "share": 0.9}], "readers": [{"name": "Place names", "effect": "+", "n": 5}], "logits": [{"token": " river", "w": 0.2}], "expert_label": None},
        "examples": [], "showcase": [], "full_docs": {}, "mode_docs": [],
        "islands_view": {"verdict": sub["verdict"], "source": "subcircuit_modes", "unit": "island" if mono else "mode", "split": False, "closed_gate_share": 0.2, "modes": [], "islands": []},
        "scientist_model": "test-model",
    }  # fmt: skip


def islands_file(pid: str, dark: bool = False) -> dict[str, Any]:
    sign = 1 if pid.endswith("__pos") else -1

    def example(label: str, act: float, words: list[str], focal: int) -> dict[str, Any]:
        tokens = [[w, round(act * sign, 3) if i == focal else (0.0 if i % 3 else round(0.3 * sign, 3))] for i, w in enumerate(words)]
        return {"label": label, "act": round(act * sign, 4), "a_over_theta": round(act / 0.25, 3), "tokens": tokens, "focal_idx": focal}

    long_passage = [f" w{i}" for i in range(60)] + [" Danube"] + [f" v{i}" for i in range(60)]
    islands = [
        {"id": 0, "n": 40, "name": "Rivers in guidebooks", "description": "Guidebook sentences naming a «river».",
         "examples": [example("median", 1.0, [" the", " calm", " Rhine", " flows"], 2), example("top", 2.5, long_passage, 60), example("random", 0.8, [" a", " Volga", " cruise"], 1)],
         "examples_more": [example("band", 0.7, [" by", " the", " Elbe"], 2)]},
        {"id": 1, "n": 12, "name": "Odd fragments", "description": "No clear content.",
         "examples": [example("top", 0.9, [" zz", " kk", " qq"], 1)], "examples_more": []},
    ]  # fmt: skip
    modes = (
        [{"name": "unassigned", "predicate": "", "island_ids": [0, 1], "chips": [], "grey": True}]
        if dark
        else [{"name": "River names", "predicate": "Name of a river in running text", "island_ids": [0], "chips": ["firing 74.2%"]},
              {"name": "unassigned", "predicate": "", "island_ids": [1], "chips": ["firing 25.8%"], "grey": True}]
    )  # fmt: skip
    did, pole = pid.rsplit("__", 1)
    return {"pid": pid, "did": did, "pole": pole, "layer": int(did.split("_")[0][1:]), "expert": int(did.split("_")[1][1:]),
            "neuron": int(did.split("_")[2][1:]), "sign": sign, "islands": islands, "examples_more_n": 7, "opus": {"modes": modes}}  # fmt: skip


def circuit_meta(did: str) -> dict[str, Any]:
    layer = int(did.split("_")[0][1:])

    def member(i: int, pole: str, gate: float, up: float) -> dict[str, Any]:
        other = f"L{max(0, layer - 1 - i % 3)}_E{20 + i}_N{300 + i}"
        return {"uid": other + pole, "did": other, "pole": pole, "layer": max(0, layer - 1 - i % 3), "label": f"Upstream unit {i}",
                "label_full": f"Upstream unit {i}: an invented description.", "label_source": "fleet+", "sim": None,
                "gate_s": gate, "up_s": up, "gate_w": gate, "up_w": up, "gate_pct": 0.99, "up_pct": 0.98, "ev_n_active": 10 + i}  # fmt: skip

    def reader(i: int, pole: str, w: float) -> dict[str, Any]:
        other = f"L{layer + 1 + i}_E{40 + i}_N{500 + i}"
        return {"uid": other + pole, "did": other, "pole": pole, "layer": layer + 1 + i, "label": f"Reader unit {i}",
                "label_full": f"Reader unit {i}: an invented description.", "hop": 1 + i, "w": w, "w_s": 0.0, "ev_n_active": 5}  # fmt: skip

    inputs = [
        {"id": "g1", "name": "Geography words · gate + / up +", "description": "+ pole, drives — 2 categories: Rivers (2) · Seas (1)", "family": "pos", "effect": "+", "is_misc": False, "bucket": "+|drives",
         "subgroups": [{"name": "Rivers", "description": "Units for river names.", "units": ["a", "b"]}],
         "members": [member(0, "+", 0.05, 0.08), member(1, "-", 0.02, 0.01), member(2, "+", 0.01, 0.0)],
         "agg": {"gate": {"mean_signed": 0.08, "n_pos": 3, "n_neg": 0}, "up": {"mean_signed": 0.09, "n_pos": 2, "n_neg": 0}}},
        {"id": "g2", "name": "City names · gate-lane −", "description": "+ pole, opposes — Units for city names.", "family": "neg", "effect": "-", "is_misc": False, "bucket": "+|opposes",
         "members": [member(3, "+", -0.04, 0.0)], "agg": {"gate": {"mean_signed": -0.04, "n_pos": 0, "n_neg": 1}, "up": {"mean_signed": 0.0, "n_pos": 0, "n_neg": 0}}},
        {"id": "g3", "name": "Baking words · gate + / up −", "description": "- pole, drives — Units for baking.", "family": "neg", "effect": "-", "is_misc": False, "bucket": "-|drives",
         "members": [member(4, "+", 0.03, -0.07)], "agg": {"gate": {"mean_signed": 0.03, "n_pos": 1, "n_neg": 0}, "up": {"mean_signed": -0.07, "n_pos": 0, "n_neg": 1}}},
        {"id": "g_single_no", "name": "single units · - pole, opposes (no category with ≥2 units; by contribution)", "description": "1 units whose label matched no category.", "family": "neg", "effect": None, "is_misc": True,
         "members": [member(5, "-", -0.01, 0.02)], "agg": {"gate": {"mean_signed": -0.01, "n_pos": 0, "n_neg": 1}, "up": {"mean_signed": 0.02, "n_pos": 1, "n_neg": 0}}},
    ]  # fmt: skip
    readers = [
        {"id": "r1", "name": "Place names", "description": "+ pole, raised by — Units for places.", "effect": "+", "is_misc": False, "bucket": "+|raised", "members": [reader(0, "+", 0.02), reader(1, "-", -0.01)], "agg": {"mean": 0.01}},
        {"id": "r2", "name": "Recipe steps", "description": "- pole, lowered by — Units for recipes.", "effect": "-", "is_misc": False, "bucket": "-|lowered", "members": [reader(2, "+", 0.03)], "agg": {"mean": 0.02}},
    ]  # fmt: skip
    return {
        "schema": "moe-circuit-explorer-1.0",
        "identity": {"did": did, "layer": layer, "expert": int(did.split("_")[1][1:]), "neuron": int(did.split("_")[2][1:]),
                     "labels": {"short_title_pos": "Fires on river names.", "short_title_neg": "Fires on baking words."}},
        "pole_view": {
            "+": {"n_admitted": 120, "theta": 0.8, "pole_real": True, "line": "Fires on river names, feeding place-name readers.",
                  "inputs": [{"id": "g1", "mass_share": 0.9}, {"id": "g2", "mass_share": 0.4}],
                  "readers": [{"id": "r1", "name": "Place names", "n": 2, "effect": "+", "n_ex": 80, "n_ex_other": 3, "mean_w": 0.01},
                              {"id": "r2", "name": "Recipe steps", "n": 1, "effect": "-", "n_ex": 5, "n_ex_other": 60, "mean_w": 0.004}],
                  "top_contexts": [{"ctx": "crossed the wide ▸Danube◂ near", "act": 2.2, "ex_id": "x1"}]},
            "-": {"n_admitted": 90, "theta": 0.8, "pole_real": True, "line": "no discriminative reading: mixed content",
                  "inputs": [{"id": "g3", "mass_share": 0.7}],
                  "readers": [{"id": "r2", "name": "Recipe steps", "n": 1, "effect": "-", "n_ex": 60, "n_ex_other": 5, "mean_w": -0.02}],
                  "top_contexts": []},
        },
        "groups": {"input": inputs, "readers": readers},
        "logits": [{"token": " river", "token_id": 1, "w": 0.2, "sign": 1, "source": "static"}, {"token": " oven", "token_id": 2, "w": -0.15, "sign": -1, "source": "static"}],
    }  # fmt: skip


def build_release() -> dict[str, bytes]:
    """``relative path -> JSON bytes`` of the whole fake release."""

    def pole(pid: str, cat: str | None, desc: str, islands: bool) -> dict[str, Any]:
        return {"pid": pid, "cat": cat, "desc": desc, "islands": islands}

    neurons = [
        {"did": "L4_E1_N10", "L": 4, "E": 1, "N": 10, "poles": {"+": pole(RIVER, "mono", "River names in running text", True), "-": pole(MIXED, "poly", "Baking · Chess", True)}},
        {"did": "L9_E2_N20", "L": 9, "E": 2, "N": 20, "poles": {"+": pole(FN, "poly", "Planets · Moons", True), "-": pole(DARK, "dark", "", True)}},
        {"did": "L14_E78_N5", "L": 14, "E": 78, "N": 5, "poles": {"+": pole(ISLAND_ONLY, "mono", "Glassblowing vocabulary in craft manuals", True)}},
        {"did": "L30_E3_N7", "L": 30, "E": 3, "N": 7, "poles": {"+": pole(FAILED_READ, "mono", "Knitting stitch names", True), "-": pole("L30_E3_N7__neg", None, "", False)}},
        {"did": "L39_E4_N1", "L": 39, "E": 4, "N": 1, "poles": {"+": pole("L39_E4_N1__pos", "mono", "Old description", True), "-": pole("L39_E4_N1__neg", "mono", "Old description two", True)}},
    ]  # fmt: skip

    def read(verdict: str, fn_mono: bool, mds: float | None, modes: list[tuple[str, float]], n_sub: int = 5, n_res: int = 1) -> dict[str, Any]:
        return {"verdict": verdict, "fn_mono": fn_mono, "main_damage_share": mds, "n_modes": len(modes), "n_residue": n_res,
                "n_subcircuits": n_sub, "modes": [{"label": label, "share": share} for label, share in modes]}  # fmt: skip

    river, mixed = subcircuit_file(RIVER), subcircuit_file(MIXED)
    sc_poles = {
        RIVER: read("monosemantic", False, 1.0, [(m["label"], m["share"]) for m in river["modes"] if not m.get("residue")], 4, 2),
        MIXED: read("polysemantic", False, 0.5, [(m["label"], m["share"]) for m in mixed["modes"]], 3, 0),
        FN: read("polysemantic", True, 0.95, [("Names of planets in astronomy texts", 0.8), ("Moons of Jupiter (Europa, Io)", 0.1)]),
        FAILED_READ: read("monosemantic", False, None, [], 3, 0),
        "L30_E3_N7__neg": read("polysemantic", False, 0.6, [("Violin makers of Cremona", 0.5), ("Tide tables for harbours", 0.3)]),
        "L39_E4_N1__pos": read("monosemantic", False, 1.0, [("Latin plant names in botany keys", 0.9)]),
        "L39_E4_N1__neg": read("polysemantic", False, 0.3, [("Railway timetable abbreviations", 0.4), ("Braille cell patterns in accessibility guides", 0.2), ("Zero share mode", 0)]),
    }  # fmt: skip
    with_reads = [p for p in sc_poles if p != FAILED_READ]
    files: dict[str, Any] = {
        "public_atlas_data/index.json": {"neurons": neurons},
        "subcircuit_modes_index.json": {"source": "test", "built_at": "2026-01-01T00:00:00Z", "poles": sc_poles},
        "ns11_pages/data/index.json": {"pids": with_reads, "model": dict.fromkeys(with_reads, "opus"), "sim": {RIVER: 0.81, FN: -1.5}},
        "neuron_landscape_data/atlas_index.json": with_reads,
        "public_atlas_data/islands/index.json": {"poles": [{"pid": p["pid"], "L": n["L"], "pole": p["pid"][-3:], "name": p["desc"]} for n in neurons for p in n["poles"].values() if p["islands"]]},
        "moe_circuit_explorer_data/index.json": {"model": "Test-Model-1", "built_at": "2026-01-02T00:00:00Z", "neurons": [
            {"did": "L4_E1_N10", "layer": 4, "expert": 1, "neuron": 10, "label": "Automatic circuit label about rivers", "n_ex": 210, "act_max": 2.2, "act_min": -1.1},
            {"did": "L2_E5_N6", "layer": 2, "expert": 5, "neuron": 6, "label": "A neuron only the circuit index knows", "n_ex": 100},
            {"did": "DEMO_FIXTURE", "layer": 0, "expert": 0, "neuron": 0, "label": "synthetic"},
        ]},
        "ns11_pages/headline_overrides.json": {RIVER: "Names of rivers such as the Danube, in travel writing and verse", MIXED: "Sourdough steps, chess openings and hex colour codes"},
    }  # fmt: skip
    for pid in (RIVER, MIXED):
        files[f"subcircuit_modes_data/{pid}.json"] = subcircuit_file(pid)
        files[f"neuron_landscape_data/{pid}.json"] = landscape_file(pid)
        files[f"ns11_pages/data/{pid}.json"] = overview_file(pid)
        files[f"public_atlas_data/islands/poles/{pid}.json"] = islands_file(pid)
    files[f"public_atlas_data/islands/poles/{DARK}.json"] = islands_file(DARK, dark=True)
    files[f"public_atlas_data/islands/poles/{ISLAND_ONLY}.json"] = islands_file(ISLAND_ONLY)
    files[f"public_atlas_data/islands/poles/{FAILED_READ}.json"] = islands_file(FAILED_READ)
    files["moe_circuit_explorer_data/L4_E1_N10/meta.json"] = circuit_meta("L4_E1_N10")
    files["moe_circuit_explorer_data/L4_E1_N10/exemplars.json"] = [{"ex_id": "x1", "act": 2.2, "window": {"tokens": [" the", " Danube"], "acts": [0.0, 2.2], "focal_rel": 1}}]
    files["moe_circuit_explorer_data/L4_E1_N10/incidence.json"] = {"ex_order": ["x1"], "routes": {"in:g1:gate": [1.0]}, "quantized": False}
    return {path: json.dumps(data, ensure_ascii=False).encode("utf-8") for path, data in files.items()}


EXPLORER_HTML = (
    '<html><body><iframe id="neuron-atlas" data-explorer="' + RELEASE_URL + 'public_neuron_atlas-sample-3000-v2.html"></iframe></body></html>'
)
