"""The renderers: Markdown, plain text, print-ready HTML and CSV.

Everything here is built by hand from the pydantic models with invented text;
no atlas data and no network are involved.
"""

from __future__ import annotations

import csv
import io
import re
import time
from html import unescape

import pytest

from neuron_atlas import render
from neuron_atlas.models import (
    CircuitSides,
    Damage,
    Example,
    Island,
    IslandMode,
    IslandsView,
    Match,
    Mode,
    ModeHit,
    ModeOut,
    NeuronDetail,
    NeuronHit,
    NeuronRef,
    PoleDetail,
    PoleHit,
    SearchResponse,
    SimilarItem,
    SimilarResponse,
    StrengthProfile,
    Subcircuit,
)

ORANGE, SLATE = "224,86,27", "51,105,138"
URL = "https://www.goodfire.com/research/public_neuron_atlas?n=L7_E3_N42&pole=pos"

# --------------------------------------------------------------------------- hand-made models


def make_hit(pole: str = "pos", **changes) -> PoleHit:
    fields = {
        "pid": f"L7_E3_N42__{pole}",
        "did": "L7_E3_N42",
        "pole": pole,
        "display": ("+" if pole == "pos" else "−") + " L7/E3/N42",
        "layer": 7,
        "expert": 3,
        "neuron": 42,
        "category": "fn",
        "category_words": "one dominant mode",
        "description": "Kettle words · Teapot lids",
        "headline": "Words for kettles, then teapot lids",
        "n_modes": 2,
        "n_subcircuits": 3,
        "verdict": "polysemantic",
        "main_damage_share": 0.93,
        "simulatability": 0.61,
        "in_public_sample": True,
        "available": ["subcircuit", "overview"],
        "mirrored": ["subcircuit"],
        "modes": [ModeOut(index=0, label="Kettle words", share=0.62), ModeOut(index=1, label="Teapot lids", share=0.078)],
        "atlas_url": URL if pole == "pos" else None,
    }
    fields.update(changes)
    return PoleHit(**fields)


def make_example(**changes) -> Example:
    fields = {
        "source": "subcircuit",
        "ref": "c0:0",
        "group": "strongest",
        "act": 1.25,
        "a_rel": 3.3,
        "closed_gate": True,
        "held_out": True,
        "repeats": 4,
        "text": "put the« kettle» on",
        "pre": "She said: ",
        "focal_token": " kettle",
        "focal_index": 2,
    }
    fields.update(changes)
    return Example(**fields)


def shaded_example(sign: int = 1) -> Example:
    """Tokens with raw-signed values; multiply by ``sign`` to make the same tokens a negative pole's."""
    tokens = [("put", 1.0 * sign), (" the", -1.0 * sign), (" kettle", 2.0 * sign), (" on", None), (".", -0.05 * sign)]
    return make_example(tokens=tokens, text="put the« kettle» on.", act=0.5 * sign)


OVERVIEW = {
    "headline": "Kettles first, lids second",
    "summary": "Fires on kettles.\nA second line about lids.",
    "situations_refer_to": "mode",
    "situations": [
        {
            "name": "Kettles",
            "explain": "Any kettle will do.",
            "typical_strength": "about 3×",
            "cue": "put the ⟦ kettle⟧ `on`",
            "from_experiments": "Swapping in 'pot' gave 0.2×.",
            "mode_index": 0,
        }
    ],
    "not_this": ["Saucepans stay near 0."],
    "experiments": [{"claim": "Kettle | not pot", "test": "Swapped the noun.", "result": "3.1× against 0.2×."}],
    "open": "Why lids fire at all.",
    "subcircuit_names": {"c0": "Kettles on stoves"},
    "final_description": "Fires on kettles.\n\nSilent look-alikes: pots.",
    "report_markdown": (
        "# L7_E3_N42 report\n\nθ = 0.38. **What it is.** A kettle detector with `regex ^kett`.\n\n"
        "## Evidence\n\n1. *First.* Kettles reach 3θ.\n2. Pots stay at 0.\n\n"
        "```\n# not a heading\n```\n\nA stray <b>tag</b> and 2 < 3.\n"
    ),
    "simulatability": [
        {"source": "fleet", "label": "Fleet label", "kl_recovered": 0.25, "is_this_description": False},
        {"source": "own", "label": "Scientist", "kl_recovered": 0.61, "is_this_description": True},
    ],
    "threshold": 0.38,
    "scientist_model": "some-model",
    "circuit_digest": {
        "note": "",
        "inputs": [{"name": "Kitchen words", "effect": "+", "role": "drives", "share": 0.9}],
        "readers": [{"name": "Boiling", "effect": "-", "n": 4}],
        "output_tokens": [{"token": " tea", "weight": 0.12}],
    },
    "research": {"atlas_label": "kettles", "long_context_documents": 2},
}

LANDSCAPE = {
    "verdict": "polysemantic",
    "sampled_firings": 5000,
    "modes": [
        {"index": 0, "label": "Kettle words", "description": "Kettles.", "kind": "open", "sample_share": 0.62,
         "firing_share": 0.078, "damage_share": 0.62, "damage_per_firing": 7.9, "tested_firings": 72, "colour": "#1a7f6b"},
        {"index": 1, "label": "Lids | rims", "description": "Lids seen through the leak.", "kind": "closed_gate",
         "sample_share": 0.2, "firing_share": 0.9, "damage_share": 0.3, "damage_per_firing": 0.33, "tested_firings": 4,
         "colour": "javascript:alert(1)", "relation": {"kind": "SUBCASE", "to_mode": 0, "text": "A narrower case of “Kettle words”"}},
        {"index": 2, "label": "Unassigned examples (fit no mode)", "description": "", "kind": "unassigned",
         "sample_share": None, "firing_share": 0.022, "damage_share": 0.08, "damage_per_firing": 3.6, "tested_firings": 11},
    ],
    "top_damage_mode": 0,
    "levels": [
        {"index": 0, "name": "Top 5%", "median_strength": 0.5, "min_strength": 0.3, "max_strength": 0.7,
         "token_frequency": 0.04, "one_in_tokens": 25, "sampled_firings": 1000,
         "firings_by_mode": [100, 800, 100], "damage_by_mode": [0.5, 0.25, 0.25]},
        {"index": 4, "name": "Strongest 1,000", "median_strength": 5.9, "min_strength": 3.4, "max_strength": 12.5,
         "token_frequency": 0.0000081, "one_in_tokens": 123456, "sampled_firings": 1000,
         "firings_by_mode": [1000, 0, 0], "damage_by_mode": [2.0, 0.0, 0.0]},
    ],
    "damage_fit": {"slope": 2.5, "r2": 0.75, "tested_firings": 150, "note": "ignored"},
    "mode_order": [1, 0],
    "firings_listed": 800,
    "gate_closed_firings_listed": 120,
    "strength_unit": "multiples of the pole's firing threshold θ",
    "mode_similarity": {"by_description": [[1.0, 0.25], [0.25, 1.0]], "by_firing": None, "note": "ignored"},
}  # fmt: skip

CIRCUIT = {
    "neuron": "L7_E3_N42",
    "pole": "pos",
    "line": "Driven by kitchen context, read by boiling detectors.",
    "inputs": [
        {
            "id": "g1", "name": "Kitchen context", "description": "Earlier neurons for kitchens.", "effect": "+",
            "role": "drives", "lane": "gate", "share": 0.75, "n_members": 3,
            "members": [
                {"unit": "L5_E1_N8", "label": "Stoves | hobs", "weight": 0.0164, "in_atlas": True},
                {"unit": "L6_E2_N9", "label": "", "weight": None, "in_atlas": False},
            ],
        }
    ],
    "readers": [
        {"id": "r1", "name": "Boiling", "description": "", "effect": "-", "n_members": 1,
         "members": [{"unit": "L9_E4_N1", "label": "Steam", "weight": -0.002, "in_atlas": False}]}
    ],
    "output_tokens": [{"token": " whistle", "weight": 0.25}, {"token": "", "weight": None}],
    "notes": ["A hand-made circuit."],
}  # fmt: skip


def make_detail(pole: str = "pos") -> PoleDetail:
    """A pole with every data set filled in."""
    modes = [
        Mode(index=0, label="Kettle words", public_label="Kettle words", kind="open", description="Fires on kettles.",
             share=0.62, n_firings=3100, subcircuits=["c0", "c1"], damage=Damage(per_firing=0.0012, ratio=7.9, n_measured=72),
             examples=[make_example(), shaded_example(-1 if pole == "neg" else 1)]),
        Mode(index=1, label="Closed-gate firings (silu leak)", public_label="Q3/Q4 firings (gate input negative)",
             kind="closed_gate", description="Leaks.", share=0.3, subcircuits=["c2"], damage=Damage(ratio=10.8)),
        Mode(index=2, label="Residue (unreadable)", public_label="Unassigned examples", kind="residue", share=0.0004),
    ]  # fmt: skip
    sides = CircuitSides(
        gate_drivers=[NeuronRef(unit="L5_E1_N8", label="Stoves", weight=0.0164, rest=0.0002, role="gate_driver", in_atlas=True)],
        readers=[NeuronRef(unit="L9_E4_N1", label="", weight=-0.0007, role="reader")],
    )
    subcircuit = Subcircuit(
        id="c0", mode_index=0, n=1900, share=0.38, meaning="Kettles being put on.", token_pattern="The noun itself.",
        readable=True, reading_confidence=3, function_label="Kettle on the stove", function="Adds kitchen and verb cues.",
        gate_role="Opens on kitchens.", up_role="Writes on nouns.", function_confidence=2,
        profile=StrengthProfile(ge_theta=0.5, mid=0.25, weak=0.2, closed=0.05), median_a_rel=1.4,
        damage=Damage(per_firing=4.0e-7, ratio=0.0014, n_measured=42), circuit=sides,
        examples=[make_example(ref="c0:1", closed_gate=None, held_out=None, repeats=1)],
        research={"unique_contexts": 12},
    )  # fmt: skip
    islands = IslandsView(
        modes=[
            IslandMode(name="Kitchen things", description="things in kitchens", share=0.74, islands=[0]),
            IslandMode(name="Unassigned examples", share=0.26, unassigned=True, islands=[3]),
        ],
        islands=[Island(id=0, number=1, n=112, name="Kettles in recipes", description="Recipe steps.",
                        examples=[make_example(source="islands", ref="island0:0", group="typical", pre="")])],
    )  # fmt: skip
    return PoleDetail(
        pole=make_hit(pole),
        theta=0.4321,
        n_firings=5000,
        closed_gate_share=0.36,
        auto_label="Automatic: kettles.",
        summary="Mostly kettles.",
        unified_description="Kettles, and sometimes lids.",
        modes=modes,
        subcircuits=[subcircuit],
        islands=islands,
        overview=OVERVIEW,
        landscape=LANDSCAPE,
        circuit=CIRCUIT,
        research={"partition": "p1", "fn_mono": True},
        sources={"subcircuit": "used", "islands": "used", "overview": "used", "landscape": "used", "circuit": "used"},
        notes=["A note from the assembler."],
    )


SECTIONS = [
    "Modes",
    "Firing landscape",
    "Overview by the neuron scientist",
    "Subcircuits",
    "Circuit",
    "Islands (the older read)",
    "Research extras",
    "Data availability",
]


def parse_markdown(text: str):
    """Tokens of a CommonMark + tables parse (markdown-it-py comes with rich, a project dependency)."""
    markdown_it = pytest.importorskip("markdown_it")
    return markdown_it.MarkdownIt("commonmark").enable("table").parse(text)


# --------------------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.078, "7.8%"), (0.62, "62%"), (0.0004, "<0.1%"), (0.0, "0%"), (0.0999, "10%"), (1.0, "100%"), (None, "–")],
)
def test_shares_are_printed_like_the_overview(value, expected):
    assert render._pct(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(7.85, "7.8×"), (7.9, "7.9×"), (10.8, "11×"), (0.02, "<0.1×"), (0.0, "0×"), (None, "–")]
)
def test_multiples_are_printed_like_the_overview(value, expected):
    assert render._times(value) == expected


def test_numbers_in_a_document():
    md = render.pole_markdown(make_detail())
    assert "7.8% of firings · 62% of damage · 7.9× average damage per firing" in md
    assert "90% of firings · 30% of damage · 0.3× average damage per firing · rough: 4 tested firings" in md
    assert "damage per firing 7.9× the pole's average" in md and "damage per firing 11× the pole's average" in md
    assert "| <0.1% |" in md  # the residue mode's share in the table
    assert "simulatability 61%" in md and "main mode carries 93% of the damage" in md and "θ = 0.4321" in md
    assert "simulatability < −100%" in render.hit_markdown(make_hit(simulatability=-24.7))
    assert "simulatability -5%" in render.hit_markdown(make_hit(simulatability=-0.05))


# --------------------------------------------------------------------------- the pole document


def test_title_facts_link_and_footer():
    md = render.pole_markdown(make_detail())
    lines = md.splitlines()
    assert lines[0] == "# + L7/E3/N42 — Words for kettles, then teapot lids"
    facts = lines[2]
    for part in (
        "positive pole", "one dominant mode", "2 modes", "layer 7 / expert 3 / neuron 42", "id L7_E3_N42__pos",
        "in the public sample on goodfire.com: yes",
    ):  # fmt: skip
        assert part in facts
    assert f"Original page: <{URL}>" in md
    assert md.rstrip().splitlines()[-1] == (
        f"Source: Qwen3.6 Neuron Atlas by Goodfire, <{URL}>; rendered by Neuron Atlas Interface"
    )
    hidden = render.pole_markdown(PoleDetail(pole=make_hit("neg", in_public_sample=False, headline="")))
    assert hidden.startswith("# − L7/E3/N42 — Kettle words · Teapot lids\n")
    assert "in the public sample on goodfire.com: no" in hidden and "Original page: none" in hidden
    assert "<https://www.goodfire.com/research/public_neuron_atlas>; rendered by" in hidden


def test_every_section_appears_in_order_when_its_data_is_present():
    md = render.pole_markdown(make_detail())
    positions = [md.index(f"\n## {name}\n") for name in SECTIONS]
    assert positions == sorted(positions)
    for text in (
        "**Summary.** Mostly kettles.", "**Unified description.**", "**Automatic label of the whole neuron.**",
        "### Mode 0 — Kettle words", "### Mode 1 — Q3/Q4 firings (gate input negative)", "Closed-gate firings (Q3/Q4)",
        "Label in the data: “Closed-gate firings (silu leak)”", "### The five rarity levels", "1 in 123,456 tokens",
        "### Situations (one per mode)", "### What does not make it fire", "### Agent experiments",
        "### How well the description predicts the neuron", "### Still open", "### The scientist's final description",
        "### The scientist's full report", "### Subcircuit c0 — Kettle on the stove", "belongs to mode 0 (Kettle words)",
        "**Meaning.** Kettles being put on.", "**Token pattern.**", "**Function.**", "**Gate role.**", "**Up role.**",
        "gate open, at or above θ 50% · gate open, 0.5–1 θ 25% · gate open, below 0.5 θ 20% · gate closed (any strength) 5.0%",
        "- `L5_E1_N8` (+0.0164; elsewhere +0.0002) - Stoves \\[in the atlas\\]", "- `L9_E4_N1` (-0.0007)",
        "### Inputs", "### Readers", "- `L5_E1_N8` (+0.0164) - Stoves | hobs \\[in the atlas\\]", "` whistle` (+0.25)",
        "### Island 1 (id island0) — Kettles in recipes", "112 examples in the cluster; 1 shown", '"partition": "p1"',
        '"unique_contexts": 12', '"atlas_label": "kettles"', "- A note from the assembler.",
    ):  # fmt: skip
        assert text in md, text
    html = render.pole_html(make_detail())
    assert [html.index(f'id="{anchor}"') for anchor in ("modes", "landscape", "overview", "subcircuits", "circuit", "islands")]
    text = render.pole_text(make_detail())
    assert all(name.upper() in text for name in SECTIONS)


def test_sections_without_data_are_absent():
    bare = PoleDetail(pole=make_hit())
    for document in (render.pole_markdown(bare), render.pole_text(bare), render.pole_html(bare)):
        for name in SECTIONS:
            assert f"# {name}\n" not in document and f"{name.upper()}\n==" not in document and f">{name}</h2>" not in document
        assert "Words for kettles" in document and "rendered by Neuron Atlas Interface" in " ".join(document.split())
    modes_only = PoleDetail(pole=make_hit(), modes=make_detail().modes)
    md = render.pole_markdown(modes_only)
    assert "## Modes" in md and "## Firing landscape" not in md and "## Overview" not in md and "## Circuit" not in md
    empty = PoleDetail(pole=make_hit(), overview={}, landscape={}, circuit={}, islands=IslandsView(), subcircuits=[])
    assert render.pole_markdown(empty) == render.pole_markdown(bare)


def test_heading_level_nests_documents():
    md = render.pole_markdown(make_detail(), heading=3)
    assert md.startswith("### + L7/E3/N42")
    assert "\n#### Modes\n" in md and "\n##### Mode 0 — Kettle words\n" in md
    assert "\n###### L7\\_E3\\_N42 report\n" not in md and "\n###### L7_E3_N42 report\n" in md
    assert "\n**Evidence**\n" in md  # deeper than six levels: a bold line instead of a heading
    levels = {token.tag for token in parse_markdown(md) if token.type == "heading_open"}
    assert levels == {"h3", "h4", "h5", "h6"}


def test_landscape_numbers_are_labelled_as_the_landscape_s_own():
    md = render.pole_markdown(make_detail())
    assert "| Landscape # | Mode | Sampled share |" in md and "Landscape numbers are the landscape's own" in md
    assert "**Landscape mode 1 - Lids | rims (Q3/Q4).**" in md and "**Mode 1 -" not in md
    assert "(similar modes side by side), by landscape number: 1, 0." in md
    assert "| Landscape mode | Top 5% | Strongest 1,000 |" in md and "| Landscape mode | 0 | 1 |" in md
    assert "### Mode 1 — Q3/Q4 firings (gate input negative)" in md  # the mode list keeps its own numbers
    # An open mode that the mode list numbers differently is cross-referenced.
    detail = make_detail()
    detail.landscape = {**LANDSCAPE, "modes": [{**LANDSCAPE["modes"][0], "pole_mode_index": 3}, *LANDSCAPE["modes"][1:]]}
    assert "| 0 | Kettle words (mode 3 of the mode list) \\[most damage\\] | 62% |" in render.pole_markdown(detail)


def test_circuit_lists_every_group_before_any_member_and_shortens_member_labels():
    label = "Tokens about kettles " + "and further detail " * 30
    groups = [
        {**CIRCUIT["inputs"][0], "id": f"g{i}", "gate": 0.1 / i, "up": -0.01,
         "members": [{"unit": f"L5_E1_N{i}", "label": label, "weight": 0.01, "in_atlas": True}]}
        for i in range(1, 9)
    ]  # fmt: skip
    circuit = {**CIRCUIT, "inputs": groups}
    md = render.pole_markdown(PoleDetail(pole=make_hit(), circuit=circuit))
    first_member = md.index("- `L5_E1_N1`")
    for text in ("| Group | Name | Role | Lane | Gate | Up | Share | Neurons |", "| g8 | Kitchen context | drives | gate |",
                 "| r1 | Boiling | lowered by this pole |", "### Output tokens"):  # fmt: skip
        assert md.index(text) < first_member, text
    assert "\n### Input groups in detail\n" in md and "**g1 - Kitchen context.** 3 neurons, 1 listed. Earlier neurons" in md
    member = md[first_member : md.index("\n", first_member)]
    assert member.endswith("further detail… \\[in the atlas\\]") and len(member) < 180 and label.strip() not in md
    neuron = NeuronDetail(did="L7_E3_N42", display="L7/E3/N42", layer=7, expert=3, neuron=42, in_atlas_index=True,
                          circuits={"pos": circuit, "neg": {**CIRCUIT, "pole": "neg"}})  # fmt: skip
    md = render.neuron_markdown(neuron)
    # Both poles' groups come before either pole's member lists.
    assert md.index("## Circuit of the negative pole\n") < md.index("## Circuit of the positive pole (continued)")
    assert md.index("## Circuit of the positive pole (continued)") < md.index("## Circuit of the negative pole (continued)")
    assert md.index("| r1 | Boiling |", md.index("## Circuit of the negative pole\n")) < md.index("- `L5_E1_N1`")


def test_reader_groups_name_the_label_of_the_other_pole_and_the_circuit_threshold():
    readers = [
        {"id": "r1", "name": "Boiling", "effect": "-", "examples": 9, "mean_effect": -0.002, "n_members": 1},
        {"id": "r8", "name": "Codes", "effect": "+", "examples": 64, "mean_effect": -0.0039, "n_members": 23,
         "measured_direction": "lowered"},
    ]  # fmt: skip
    circuit = {**CIRCUIT, "readers": readers, "examples_analysed": 125, "threshold": 0.805838}
    md = render.pole_markdown(PoleDetail(pole=make_hit(), theta=0.3192, circuit=circuit))
    assert "| r1 | Boiling | lowered by this pole | 9 | -0.002 | 1 |" in md
    assert (
        "| r8 | Codes | shown as “raised” in the explorer (the label of the other pole) · measured on this pole: lowered "
        "| 64 | -0.0039 | 23 |"
    ) in md
    assert "raised by this pole" not in md
    assert "*125 examples analysed · threshold of the circuit data 0.8058 (not the pole's θ)*" in md and "θ = 0.3192" in md


def test_island_headings_name_the_id_their_examples_use():
    detail = make_detail()
    md, text, html = render.pole_markdown(detail), render.pole_text(detail), render.pole_html(detail)
    assert "### Island 1 (id island0) — Kettles in recipes" in md and "· `island0:0`" in md
    assert "Island 1 (id island0) — Kettles in recipes" in text
    assert '<span class="kicker">Island 1 (id island0)</span> Kettles in recipes' in html


def test_index_only_pole_says_that_the_long_texts_are_not_mirrored():
    detail = PoleDetail(
        pole=make_hit(),
        modes=[Mode(index=m.index, label=m.label, public_label=m.label, kind="open", share=m.share) for m in make_hit().modes],
        sources={"subcircuit": "not_mirrored", "overview": "not_mirrored", "islands": "absent", "circuit": "not_requested"},
        notes=["Only labels and shares from the index are shown."],
    )
    md = render.pole_markdown(detail)
    assert "| 0 | Kettle words | 62% |" in md and "| 1 | Teapot lids | 7.8% |" in md
    assert "### Mode" not in md  # nothing beyond label and share to print per mode
    assert "not mirrored" in md and "long texts" in md and "does not exist for this pole" in md
    assert "Only labels and shares from the index are shown." in md
    assert "not mirrored" in render.pole_text(detail) and "not mirrored" in render.pole_html(detail)


def test_unknown_and_malformed_extras_are_ignored():
    detail = make_detail()
    detail.overview = {**OVERVIEW, "zzz_future": {"a": 1}, "situations": [{"name": "Only a name", "zzz_more": 1}, "junk", None]}
    detail.landscape = {**LANDSCAPE, "zzz_future": [1, 2], "levels": [{"zzz_only": 1}, 7], "mode_similarity": "nope"}
    detail.circuit = {**CIRCUIT, "zzz_future": True, "inputs": [{"zzz_only": 1}, None], "readers": "nope", "notes": [3]}
    for document in (render.pole_markdown(detail), render.pole_text(detail), render.pole_html(detail)):
        assert "zzz" not in document and "Only a name" in document
    odd = PoleDetail(pole=make_hit(), overview={"experiments": "x", "simulatability": [{"label": 1}]}, landscape={"modes": [{}]})
    odd.circuit = {"output_tokens": [{"token": None}], "inputs": [{"members": [{}]}]}
    assert "## Circuit" in render.pole_markdown(odd) and render.pole_text(odd) and render.pole_html(odd)


# --------------------------------------------------------------------------- passages

NASTY = (
    "line `one` with ```` four backticks | a pipe * and _stars_\n"
    "<script>alert('x')</script> & <b>bold</b>\x00\x07\x1b[31m\x85 «tok»\r\n"
    + "x" * 5000
    + "\n# not a heading\n\ttabbed � ⣿⠿ ```"
)
NASTY_CLEAN = (
    "line `one` with ```` four backticks | a pipe * and _stars_\n"
    "<script>alert('x')</script> & <b>bold</b>[31m «tok»\n" + "x" * 5000 + "\n# not a heading\n\ttabbed � ⣿⠿ ```"
)


def nasty_detail() -> PoleDetail:
    example = make_example(text=NASTY, pre="before ``` the\x00 window: ", focal_token="tok", focal_index=None)
    mode = Mode(index=0, label="Kettle words", public_label="Kettle words", kind="open", share=0.5, examples=[example])
    return PoleDetail(pole=make_hit(), modes=[mode])


def test_a_hostile_passage_survives_markdown():
    md = render.pole_markdown(nasty_detail())
    assert "\x00" not in md and "\x07" not in md and "\x1b" not in md and "\x85" not in md and "\r" not in md
    fences = [line for line in md.splitlines() if re.fullmatch(r"`{3,}(text)?", line)]
    assert fences and all(len(fence.removesuffix("text")) >= 5 for fence in fences[-2:])  # longer than the run of four
    assert "x" * 5000 in md.splitlines()  # the long line is neither wrapped nor cut
    blocks = [token.content for token in parse_markdown(md) if token.type == "fence"]
    assert blocks == ["…before ``` the window: \n", NASTY_CLEAN + "\n"]
    assert not [token for token in parse_markdown(md) if token.type in ("html_block", "html_inline")]


def test_a_hostile_passage_survives_text_and_html():
    text = render.pole_text(nasty_detail())
    assert "\x00" not in text and "\x07" not in text and "        " + "x" * 5000 in text.splitlines()
    assert "        <script>alert('x')</script> & <b>bold</b>[31m «tok»" in text.splitlines()
    html = render.pole_html(nasty_detail())
    assert "<script" not in html and "<b>bold" not in html and "\x00" not in html and "\x07" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;[31m " in html
    assert '<span class="fo">«tok»</span>' in html and "x" * 5000 in html and "\ttabbed � ⣿⠿ ```" in html
    assert "white-space:pre-wrap" in html and "overflow-wrap:anywhere" in html


def test_example_header_shows_only_what_is_set():
    md = render.pole_markdown(make_detail())
    assert "**3.3× threshold** · strongest · Q3/Q4 (closed gate) · held-out · ×4 same context · peak activation +1.25 · `c0:0`" in md
    assert "**3.3× threshold** · strongest · peak activation +1.25 · `c0:1`" in md  # no flags, a single context
    bare = make_example(group=None, a_rel=None, act=-0.5, closed_gate=False, held_out=False, repeats=None, ref=None, pre="")
    mode = Mode(index=0, label="m", public_label="m", kind="open", examples=[bare])
    assert "\n**peak activation -0.50**\n\n```text\nput the« kettle» on\n```\n" in render.pole_markdown(
        PoleDetail(pole=make_hit(), modes=[mode])
    )


def test_context_is_shown_apart_and_cut_from_the_left():
    long_pre = "START " + "0123456789" * 100 + " END"
    mode = Mode(index=0, label="m", public_label="m", kind="open", examples=[make_example(pre=long_pre)])
    detail = PoleDetail(pole=make_hit(), modes=[mode])
    md = render.pole_markdown(detail)
    assert "*Context before the window (last 600 of 1,010 characters):*" in md and "*Passage:*" in md
    context = [token.content for token in parse_markdown(md) if token.type == "fence"][0]
    assert context.startswith("…") and context.endswith(" END\n") and "START" not in context and len(context) == 602
    html = render.pole_html(detail)
    assert "START" not in html and re.search(r'<span class="ctx"[^>]*>…[0-9]+ END</span>put the', html)
    text = render.pole_text(detail)
    assert "    Context before the window (last 600 of 1,010 characters):" in text and "    Passage:" in text


def test_focal_marker_is_kept_even_when_the_token_is_empty():
    tokens = [("⣌⠱", 0.2), ("", 1.5), ("⡼⣚", None)]
    example = make_example(text="⣌⠱«»⡼⣚", pre="", focal_token="", focal_index=1, tokens=tokens)
    plain = make_example(text="⣌⠱«»⡼⣚", pre="", focal_token="", focal_index=1)
    for sample in (example, plain):
        detail = PoleDetail(pole=make_hit(), modes=[Mode(index=0, label="m", public_label="m", kind="open", examples=[sample])])
        assert "```text\n⣌⠱«»⡼⣚\n```" in render.pole_markdown(detail)
        assert "        ⣌⠱«»⡼⣚" in render.pole_text(detail).splitlines()
        html = render.pole_html(detail)
        assert re.search(r'<span class="[^"]*\bfo\b[^"]*"[^>]*>«»</span>', html)
        assert re.sub(r"<[^>]+>", "", re.search(r'<div class="psg">(.*?)</div>', html, re.S).group(1)) == "⣌⠱«»⡼⣚"


def test_the_focal_token_is_found_among_other_guillemets():
    example = make_example(text="«Bonjour» dit« kettle» et «au revoir» encore", pre="", focal_token=" kettle", focal_index=None)
    html = render.pole_html(PoleDetail(pole=make_hit(), modes=[Mode(index=0, label="m", public_label="m", kind="open", examples=[example])]))
    assert '«Bonjour» dit<span class="fo">« kettle»</span> et «au revoir» encore' in html


def passage_html(example: Example, pole: str) -> str:
    mode = Mode(index=0, label="m", public_label="m", kind="open", examples=[example])
    html = render.pole_html(PoleDetail(pole=make_hit(pole), modes=[mode]))
    return re.search(r'<div class="psg">(.*?)</div>', html, re.S).group(1)


def test_token_shading_of_a_positive_pole():
    passage = passage_html(shaded_example(), "pos")
    # vmax = 2.0: own tokens get the pole colour at |v|/vmax * 0.9, white text above 0.5
    assert f'<span class="tk" style="background:rgba({ORANGE},0.45)" title="+1.00">put</span>' in passage
    assert f'style="background:rgba({ORANGE},0.90);color:#fff"' in passage and "« kettle»</span>" in passage
    assert re.search(rf'<span class="tk fo" style="background:rgba\({ORANGE},0\.90\);color:#fff"[^>]*>« kettle»</span>', passage)
    # opposite sign above 5 % of vmax: the other pole's colour at |v|/vmax * 0.6, never white
    assert f'<span class="tk" style="background:rgba({SLATE},0.30)" title="-1.00"> the</span>' in passage
    # not routed (None) and tiny opposite values carry no shading
    assert '<span class="nr" title="not routed to this neuron&#x27;s expert"> on</span>' in passage or (
        '<span class="nr" title="not routed to this neuron\'s expert"> on</span>' in passage
    )
    assert passage.endswith("</span>.") and passage.count("rgba(") == 3


def test_token_shading_of_a_negative_pole_uses_raw_signs():
    passage = passage_html(shaded_example(-1), "neg")
    assert f'<span class="tk" style="background:rgba({SLATE},0.45)" title="-1.00">put</span>' in passage
    assert f'style="background:rgba({SLATE},0.90);color:#fff"' in passage
    assert f'<span class="tk" style="background:rgba({ORANGE},0.30)" title="+1.00"> the</span>' in passage
    assert passage.count("rgba(") == 3 and '<body class="neg">' in render.pole_html(make_detail("neg"))


def test_shading_is_scaled_within_the_group_and_absent_without_tokens():
    quiet = make_example(tokens=[("a", 0.5), ("b", 0.25)], text="a«b»", focal_index=1, focal_token="b", pre="")
    loud = make_example(tokens=[("c", 4.0)], text="«c»", focal_index=0, focal_token="c", pre="")
    mode = Mode(index=0, label="m", public_label="m", kind="open", examples=[quiet, loud])
    html = render.pole_html(PoleDetail(pole=make_hit(), modes=[mode]))
    assert f"rgba({ORANGE},0.11)" in html and f"rgba({ORANGE},0.06)" in html and f"rgba({ORANGE},0.90)" in html
    assert "rgba(" not in passage_html(make_example(), "pos")  # no tokens: only the focal box
    assert "Every token is shaded" in html and "Every token is shaded" not in render.pole_html(nasty_detail())


def test_island_passages_show_the_window_of_the_text():
    words = [(f" w{i}", 0.1 * (i % 3)) for i in range(60)]
    tokens = words[:30] + [(" kettle", 3.0)] + words[30:]
    shown = "".join(text for text, _ in tokens[20:30]) + "« kettle»" + "".join(text for text, _ in tokens[31:41])
    example = make_example(source="islands", text=f"… {shown} …", pre="", focal_token=" kettle", focal_index=30, tokens=tokens)
    passage = passage_html(example, "pos")
    assert re.sub(r"<[^>]+>", "", passage) == f"… {shown} …"
    assert " w19<" not in passage and " w40<" not in passage and "« kettle»" in passage
    # Padding around a short document is in the tokens but not in the text: it is not shown, and nothing is elided.
    padded = [("", None), ("", None), ("The", 0.5), (" kettle", 3.0), (".", None), ("<end>", None), ("<end>", None)]
    edge = make_example(text="The« kettle».<end>", pre="", focal_token=" kettle", focal_index=3, tokens=padded)
    assert re.sub(r"<[^>]+>", "", passage_html(edge, "pos")) == "The« kettle».&lt;end&gt;"
    # Corpus text that itself begins with an ellipsis is not mistaken for an elided window.
    dots = [("…", 0.1), (" First", 0.2), (" kettle", 3.0)]
    natural = make_example(text="… First« kettle»", pre="", focal_token=" kettle", focal_index=2, tokens=dots)
    assert re.sub(r"<[^>]+>", "", passage_html(natural, "pos")) == "… First« kettle»"


# --------------------------------------------------------------------------- prose, tables, the report


def test_prose_is_escaped_but_typography_is_kept():
    hit = make_hit(
        headline="A <b>bold</b> *claim* with `ticks`, a_b_c and _under_ «tok» “quoted” | piped & &amp; [x](y) 1 < 2",
        description="# not a heading\n- not a list\n1. not numbered\n> not a quote",
    )
    md = render.pole_markdown(PoleDetail(pole=hit))
    title = md.splitlines()[0]
    # "<" and "&" as entities (a backslash is undone where a GFM autolink swallows it), the rest with backslashes.
    assert "&lt;b>bold&lt;/b> \\*claim\\* with \\`ticks\\`, a_b_c and \\_under\\_ «tok» “quoted” | piped & &amp;amp; [x\\](y) 1 < 2" in title
    assert "**Description.** # not a heading" in md and "\n\\- not a list\n" in md and "\n1\\. not numbered\n" in md
    assert "\n\\> not a quote\n" in md
    kinds = {token.type for token in parse_markdown(md)}
    assert not kinds & {"html_inline", "html_block", "bullet_list_open", "ordered_list_open", "blockquote_open"}
    inline = [child.type for token in parse_markdown(md) if token.type == "inline" for child in token.children or []]
    assert "em_open" not in inline and "code_inline" not in inline
    assert inline.count("link_open") == 2  # the original page and the source line, nothing made of [x](y)
    html = render.pole_html(PoleDetail(pole=hit))
    assert "<b>bold" not in html and "A &lt;b&gt;bold&lt;/b&gt; *claim* with `ticks`" in html
    assert "<title>+ L7/E3/N42 — A &lt;b&gt;bold&lt;/b&gt;" in html
    text = render.pole_text(PoleDetail(pole=hit))
    assert "A <b>bold</b> *claim* with `ticks`, a_b_c and _under_ «tok» “quoted” | piped & &amp; [x](y) 1 < 2" in text.replace("\n", " ")


def test_pipes_in_table_cells_are_escaped():
    md = render.pole_markdown(make_detail())
    assert "| Kettle \\| not pot | Swapped the noun. | 3.1× against 0.2×. |" in md
    assert "Lids \\| rims \\[Q3/Q4\\] |" in md and "**Landscape mode 1 - Lids | rims (Q3/Q4).**" in md  # only in tables
    tokens = parse_markdown(md)
    rows = [i for i, token in enumerate(tokens) if token.type == "tr_open"]
    widths = set()
    for start in rows:
        end = next(i for i in range(start, len(tokens)) if tokens[i].type == "tr_close")
        cells = [token for token in tokens[start:end] if token.type in ("th_open", "td_open")]
        widths.add(len(cells))
        if any("Kettle | not pot" in token.content for token in tokens[start:end]):
            assert len(cells) == 3
    assert widths <= {2, 3, 4, 5, 6, 7, 8}


def test_the_report_is_demoted_and_otherwise_verbatim():
    md = render.pole_markdown(make_detail())
    assert "\n#### L7_E3_N42 report\n" in md and "\n##### Evidence\n" in md and "\n# L7_E3_N42 report" not in md
    assert "θ = 0.38. **What it is.** A kettle detector with `regex ^kett`." in md
    assert "\n1. *First.* Kettles reach 3θ.\n2. Pots stay at 0.\n" in md
    assert "\n```\n# not a heading\n```\n" in md  # code inside the report is left alone
    assert "A stray &lt;b>tag&lt;/b> and 2 < 3." in md
    headings = [(token.tag, tokens[i + 1].content) for tokens in [parse_markdown(md)] for i, token in enumerate(tokens) if token.type == "heading_open"]
    assert ("h3", "The scientist's full report") in headings and ("h4", "L7_E3_N42 report") in headings
    assert ("h5", "Evidence") in headings and not [h for h in headings if h[0] == "h1"][1:]
    underlined = render._demoted_report("Title\n=====\n\ntext\n\nSub\n---\n\n- item\n---\n", 3)
    assert underlined.startswith("### Title\n") and "\n#### Sub\n" in underlined and "- item\n---" in underlined


PAYLOAD = "<img src=x onerror=alert(1)>"
HOSTILE_REPORTS = {
    "backtick in the info string": f"# R\n\n```a`b\n{PAYLOAD}\n```\n",
    "code span over two lines": f"# R\n\nfirst ` line\nsecond ` {PAYLOAD} ` end\n",
    "closing fence with text after it": f"# R\n\n```\n```x\n```\n{PAYLOAD}\n",
    "escaped backticks": f"# R\n\ntext \\`{PAYLOAD}\\` end\n",
    "unequal backtick runs": f"# R\n\nx ```{PAYLOAD}`` y\n",
    "table cells": f"# R\n\n| a | b |\n|---|---|\n| ` | {PAYLOAD} ` |\n",
    "code span over two list items": f"# R\n\n- a ` b\n- c {PAYLOAD} ` d\n",
    "already escaped": f"# R\n\ntext \\{PAYLOAD} end\n",
    "fence of a list item closed outside it": f"# R\n\n- item\n  ```\n  code\n```\ntext\n```\n{PAYLOAD}\n```\n",
    "unclosed fence": "# R\n\n```python\nprint(1)\n",
    "link definitions": (
        "# R\n\n[q3/q4]: https://evil.invalid/a\n> [most damage]: https://evil.invalid/b\n"
        "- [closed gate]:\n  https://evil.invalid/c\n\n   [in the atlas]: https://evil.invalid/d\n"
    ),
    "script links and images": (
        "# R\n\n[x](javascript:alert(1)) [y]( <javascript:alert(2)>) [z](\njavascript:alert(3))\n\n"
        "![t](https://images.invalid/t.gif) <javascript:alert(4)>\n"
    ),
}


@pytest.mark.parametrize("report", HOSTILE_REPORTS.values(), ids=HOSTILE_REPORTS.keys())
def test_the_report_cannot_reach_beyond_itself_in_markdown(report):
    detail = make_detail()
    detail.overview = {**OVERVIEW, "report_markdown": report}
    md = render.pole_markdown(detail)
    tokens = parse_markdown(md)
    assert not [token for token in tokens if token.type in ("html_block", "html_inline")]
    # The sections after the report are still sections, not swallowed by a code block it left open.
    headings = [tokens[i + 1].content for i, token in enumerate(tokens) if token.type == "heading_open"]
    assert "Subcircuits" in headings and headings[-1] == "Data availability"
    inline = [child for token in tokens if token.type == "inline" for child in token.children or []]
    assert not [child for child in inline if child.type == "image"]
    hrefs = [child.attrs["href"] for child in inline if child.type == "link_open"]
    assert all(re.match(r"https?://", href) for href in hrefs) and not [h for h in hrefs if "evil.invalid" in h]
    linked = {inline[i + 1].content for i, child in enumerate(inline) if child.type == "link_open"}
    assert not linked & {"Q3/Q4", "most damage", "closed gate", "in the atlas", "q3/q4"}
    # Other renderers keep script links that markdown-it drops: none is left unescaped in the text itself.
    assert not re.search(r"(?<!\\)\]\((?![ \t]*https?://)|(?<!\\)!\[", md)
    assert "\\[Q3/Q4\\]" in md and "\\[most damage\\]" in md  # the document's own tags cannot become links


def test_the_report_stays_as_written_where_it_is_harmless():
    report = (
        "# R\n\n2. *Ladder* A 21 > B 19\n   > C 13 > D 11.\n\n> a real quote\n> second line\n\n"
        "See [the atlas](https://www.goodfire.com/x) or [write](mailto:a@b.c), 2 < 3.\n\n~~~~py`thon x\ncode <b>\n~~~\n"
    )
    out = render._demoted_report(report, 4)
    assert out.startswith("#### R\n\n2. *Ladder* A 21 > B 19\n   \\> C 13 > D 11.\n\n> a real quote\n> second line\n")
    assert "See [the atlas](https://www.goodfire.com/x) or [write](mailto:a@b.c), 2 < 3." in out
    assert out.endswith("\n```python\ncode <b>\n~~~\n```")  # left open: closed, with a fence and info string of its own
    tokens = parse_markdown(out)
    assert [token.type for token in tokens].count("blockquote_open") == 1  # the wrapped "> C 13" chain is no quote
    assert [token.content for token in tokens if token.type == "fence"] == ["code <b>\n~~~\n"]


@pytest.mark.parametrize(
    "report",
    ["**a " * 20_000, " x ".join("`" * k for k in range(1, 300)), "# a" + " " * 20_000 + "b\n", "*a " * 20_000],
    ids=["unclosed bold", "backtick runs without partner", "heading with a long gap", "unclosed emphasis"],
)
def test_a_pathological_report_renders_without_quadratic_work(report):
    detail = PoleDetail(pole=make_hit(), overview={"report_markdown": report})
    for writer in (render.pole_markdown, render.pole_text, render.pole_html):
        started = time.perf_counter()
        writer(detail)
        assert time.perf_counter() - started < 2.0, writer.__name__  # took 4 to 16 s each before
    marked = "**b** " + "x" * 20_000  # a paragraph this long keeps its marks as text
    assert render._report_inline(marked) == [render._Span(marked)] and render._report_inline("**b**")[0].style == "b"


def test_nested_report_lists_keep_their_numbers_and_depth():
    report = "# R\n\n1. **First** a\n2. Second\n3. Third:\n   - basket: one\n   - killer: two\n4. Fourth\n\n- loose\n"
    detail = PoleDetail(pole=make_hit(), overview={"report_markdown": report})
    html = render.pole_html(detail)
    assert '<ol><li value="1"><strong>First</strong> a</li><li value="2">Second</li><li value="3">Third:</li></ol>\n' in html
    assert '\n<ul class="sub" style="padding-left:2.5rem"><li>basket: one</li><li>killer: two</li></ul>\n' in html
    assert '\n<ol><li value="4">Fourth</li></ol>\n<ul><li>loose</li></ul>' in html
    text = render.pole_text(detail)
    assert "  3. Third:\n     - basket: one\n     - killer: two\n  4. Fourth\n\n  - loose\n" in text
    assert "\n3. Third:\n   - basket: one\n" in render.pole_markdown(detail)  # Markdown keeps the report's own lines


def test_the_report_is_rendered_in_html_and_plain_in_text():
    html = render.pole_html(make_detail())
    assert "<h4>L7_E3_N42 report</h4>" in html and "<h5>Evidence</h5>" in html
    assert "<strong>What it is.</strong> A kettle detector with <code>regex ^kett</code>." in html
    assert '<ol><li value="1"><em>First.</em> Kettles reach 3θ.</li><li value="2">Pots stay at 0.</li></ol>' in html
    assert "<pre># not a heading</pre>" in html and "A stray &lt;b&gt;tag&lt;/b&gt; and 2 &lt; 3." in html
    text = render.pole_text(make_detail())
    assert "L7_E3_N42 report\n~~~~~~~~~~~~~~~~" in text and "What it is. A kettle detector with regex ^kett." in text
    assert "  1. First. Kettles reach 3θ." in text and "    # not a heading" in text


# --------------------------------------------------------------------------- plain text


def test_plain_text_has_no_markup_and_wraps_prose_only():
    text = render.pole_text(make_detail())
    assert text.startswith("=" * 20) and "\nMODES\n=====\n" in text and "\nMode 0 — Kettle words\n---------------------\n" in text
    assert "**" not in text and "```" not in text and "| --- |" not in text and "](" not in text and "<http" not in text
    assert not re.search(r"^#{1,6} \S", text, re.M)
    assert f"Original page: {URL}" in text
    header = "3.3× threshold · strongest · Q3/Q4 (closed gate) · held-out · ×4 same context · peak activation +1.25 · c0:0"
    assert header in " ".join(text.split()) and all(len(line) <= 100 for line in text.splitlines() if "threshold ·" in line)
    assert "\n  3.3× threshold · strongest · peak activation +1.25 · c0:1\n" in text
    assert "    Context before the window (10 characters):\n        …She said: \n    Passage:\n        put the« kettle» on" in text
    long = make_detail()
    long.summary = "word " * 200
    wrapped = render.pole_text(long)
    assert max(len(line) for line in wrapped.splitlines() if line.startswith("word")) <= 100
    assert re.search(r"^Claim +Test +Result\n-+  -+  -+\nKettle \| not pot +Swapped the noun\. +3\.1× against 0\.2×\.$", text, re.M)
    assert re.search(r"^0\s+Kettle words\s+62%\s+7\.9×$", text, re.M)
    rule = next(line for line in text.splitlines() if set(line) <= {"-", " "} and line.startswith("- "))
    assert len(rule) <= 100


def test_plain_text_tables_line_up_with_wide_characters():
    assert render._width("花瓶 ok") == 7 and render._width("Café") == 4 and render._width("plain") == 5
    labels = ["Kettle words", "花瓶 vases and アグレッシブ 'aggressive'", "Café au lait"]
    modes = [Mode(index=i, label=label, public_label=label, kind="open", share=0.3) for i, label in enumerate(labels)]
    text = render.pole_text(PoleDetail(pole=make_hit(), modes=modes))
    rows = [line for line in text.splitlines() if re.match(r"[012]  ", line)]
    assert len(rows) == 3 and {render._width(row) for row in rows} == {render._width(rows[0])}  # the shares line up
    # Wrapped to a narrow column, a cell with wide characters stays inside it.
    assert all(render._width(line) <= 10 for line in render._wrap_cell("花瓶花瓶花瓶 アグレッシブ vases", 10))


# --------------------------------------------------------------------------- HTML


def test_html_is_a_standalone_printable_page():
    html = render.pole_html(make_detail())
    assert html.startswith("<!DOCTYPE html>\n<html lang=\"en\">") and html.rstrip().endswith("</html>")
    assert "<title>+ L7/E3/N42 — Words for kettles, then teapot lids</title>" in html
    assert '<meta charset="utf-8">' in html and html.count("<style>") == 1 and "<script" not in html
    assert not re.search(r"""(src|href)=["'](?!#|https://www\.goodfire\.com/)""", html)  # nothing loaded, only source links
    assert "@import" not in html and "url(" not in html
    for rule in ("@page{size:A4", "@top-left{content:", "counter(page)", "print-color-adjust:exact", "break-inside:avoid",
                 "@media print", ".noprint{display:none!important}", "serif"):  # fmt: skip
        assert rule in html, rule
    assert '<button type="button" onclick="print()">Print</button>' in html and 'class="bar noprint"' in html
    assert '<table class="facts">' in html and "<th>Firing threshold θ</th><td>0.4321</td>" in html
    assert '<span class="meter"><i style="width:62.0%"></i></span>62%' in html  # share bars, pure CSS
    assert '<aside class="legend">' in html and "the opposite sign" in html
    assert "<th>Claim</th><th>Test</th><th>Result</th>" in html and "<td>Kettle | not pot</td>" in html
    assert '<i class="sw" style="background:#1a7f6b;border-color:#1a7f6b"></i>' in html and "javascript:" not in html
    for tag in ("most damage", "Q3/Q4"):  # brackets that are copied but not shown
        assert f'<span class="tag"><span class="cp">[</span>{tag}<span class="cp">]</span></span>' in html
    assert f'Source: Qwen3.6 Neuron Atlas by Goodfire, <a href="{URL.replace("&", "&amp;")}">' in html
    assert html.count("<section") == html.count("</section>") and html.count("<table") == html.count("</table>")
    assert '<section class="s1 brk" id="subcircuits">' in html  # long appendix-like sections start a page


def test_copied_html_reads_like_the_other_formats():
    html = render.pole_html(make_detail())
    # The reference of an example comes last, after a separator that is copied but not shown.
    caption = re.search(r"<figcaption>(.*?)</figcaption>", html).group(1)
    assert caption.endswith('<span class="cp">&nbsp;·&nbsp;</span><span class="ref">c0:0</span>')
    copied = unescape(re.sub(r"<[^>]+>", "", caption)).replace("\xa0", " ")
    assert copied == "3.3× threshold · strongest · Q3/Q4 (closed gate) · held-out · ×4 same context · peak activation +1.25 · c0:0"
    assert ".cp{font-size:0}" in html and "figure.ex .ref{float:right" in html
    # A share bar is no block: copying a table row does not break it in two.
    assert ".meter i{display:inline-block;vertical-align:top;" in html


def test_html_fits_a_phone_and_keeps_headings_whole_in_print():
    html = render.pole_html(make_detail())
    narrow = re.search(r"@media screen and \(max-width:40rem\)\{(.*?)\n\}", html, re.S).group(1)
    assert "table.facts th,table.facts td{display:block" in narrow and "table:not(.facts){display:block;overflow-x:auto}" in narrow
    assert "overflow-wrap:anywhere" in re.search(r"\nmain\{[^}]*\}", html).group(0)  # long hashes in prose wrap
    printed = re.search(r"@media print\{(.*?)\n\}", html, re.S).group(1)
    assert "h1,h2,h3,h4,h5,h6{break-inside:avoid;page-break-inside:avoid}" in printed


def test_token_values_name_their_unit():
    theta = passage_html(shaded_example().model_copy(update={"token_unit": "theta"}), "pos")
    raw = passage_html(shaded_example().model_copy(update={"token_unit": "raw"}), "pos")
    assert 'title="+1.00× θ">put</span>' in theta and 'title="-1.00× θ"> the</span>' in theta
    assert 'title="activation +1.00">put</span>' in raw and "not routed to this neuron" in raw
    assert "hover over a token to see its value" in render.pole_html(make_detail())


def test_html_title_and_running_header_cannot_break_out():
    hit = make_hit(headline='</title></style><script>alert("x")</script> “é” \\ end', atlas_url="javascript:alert(1)")
    html = render.pole_html(PoleDetail(pole=hit))
    assert "<script" not in html and "</style><" not in html.replace("</style>\n</head>", "") and "javascript:" not in html
    running = re.search(r"@top-left\{content:(\"[^\"]*\")", html).group(1)
    assert "\\3c " in running and "<" not in running and "\\5c " in running and running.count('"') == 2
    md = render.pole_markdown(PoleDetail(pole=hit))
    assert "javascript:" not in md and "Original page: none" in md and "<https://www.goodfire.com/research/public_neuron_atlas>; rendered by" in md
    assert "javascript:" not in render.hit_markdown(hit) and "javascript:" not in render.csv_row(make_hit(atlas_url=None))


def test_text_that_cannot_be_encoded_is_replaced():
    hit = make_hit(description="broken \ud835 escape")
    detail = PoleDetail(pole=hit, modes=[Mode(index=0, label="m", public_label="m", kind="open", examples=[make_example(text="a\udc00«b»")])])
    for document in (render.pole_markdown(detail), render.pole_text(detail), render.pole_html(detail), render.csv_row(hit)):
        assert "�" in document and document.encode("utf-8")


# --------------------------------------------------------------------------- hits, neurons, searches


def test_hit_markdown():
    hit = make_hit(
        score=11.7,
        matches=[
            Match(field="mode", text="Kettle words", mode_index=0, share=0.62, score=3.0),
            Match(field="mode_description", text="Fires | on kettles", mode_index=0, ref="c0"),
        ],
    )
    md = render.hit_markdown(hit)
    assert md.startswith("## + L7/E3/N42 — Words for kettles, then teapot lids\n")
    assert "id L7_E3_N42__pos · positive pole · one dominant mode · 2 modes · 3 subcircuits" in md and "score 11.7" in md
    assert "\nKettle words · Teapot lids\n" in md
    assert "- 0: Kettle words - 62% of firings\n- 1: Teapot lids - 7.8% of firings" in md
    assert "- *mode 0 · label · 62% of firings*: Kettle words" in md
    assert "- *mode 0 · mode description · c0*: Fires | on kettles" in md
    assert f"Original page: <{URL}>" in md and "Data sets: subcircuit, overview; mirrored here: subcircuit." in md
    assert render.hit_markdown(hit, heading=4).startswith("#### + L7/E3/N42")
    minimal = PoleHit(pid="L1_shared_N2__neg", did="L1_shared_N2", pole="neg", display="− L1/shared/N2", layer=1,
                      expert=None, neuron=2, category="none", category_words="not yet read", description="")  # fmt: skip
    assert "no expert (shared)" in render.hit_markdown(minimal) and "Modes" not in render.hit_markdown(minimal)


def test_neuron_markdown_puts_both_poles_one_after_the_other():
    detail = NeuronDetail(
        did="L7_E3_N42", display="L7/E3/N42", layer=7, expert=3, neuron=42, in_atlas_index=True,
        circuit_label="Kettle after 'the'.", circuit_summary={"n_ex": 299, "verdict": {"+": "mono", "-": "unresolved"}, "dark": ""},
        poles=[make_hit("pos"), make_hit("neg", headline="Teapot lids alone")],
        circuits={"pos": CIRCUIT, "neg": {**CIRCUIT, "pole": "neg"}}, notes=["Circuit mirrored today."],
    )  # fmt: skip
    md = render.neuron_markdown(detail)
    assert md.startswith("# Neuron — L7/E3/N42\n\nid `L7_E3_N42` · layer 7 / expert 3 / neuron 42 · listed in the atlas\n")
    assert "**Circuit label.** Kettle after 'the'." in md and "| n ex | 299 |" in md and "| verdict | +: mono, -: unresolved |" in md
    assert "dark" not in md
    first, second = md.index("## + L7/E3/N42 — Words for kettles"), md.index("## − L7/E3/N42 — Teapot lids alone")
    positive, negative = md.index("## Circuit of the positive pole"), md.index("## Circuit of the negative pole")
    assert first < second < positive < negative < md.index("## Notes") < md.index("Source: Qwen3.6 Neuron Atlas by Goodfire")
    assert "- Circuit mirrored today." in md and "### Inputs" in md
    unread = NeuronDetail(did="L1_E1_N1", display="L1/E1/N1", layer=1, expert=1, neuron=1, in_atlas_index=False)
    assert "not listed in the atlas" in render.neuron_markdown(unread) and "No pole of this neuron" in render.neuron_markdown(unread)


def test_search_markdown_for_each_grouping():
    hit = make_hit(score=2.5, matches=[Match(field="headline", text="Words for kettles")])
    by_pole = SearchResponse(
        query={"q": "kettle | pot", "limit": 2}, total=41, offset=2, limit=2, next_offset=4, group_by="pole",
        hits=[hit, make_hit("neg", score=1.0)], facets={"layer": {"7": 2}}, notes=["Substring search was used."],
    )  # fmt: skip
    md = render.search_markdown(by_pole)
    assert md.startswith("# Neuron Atlas — Search results\n")
    assert '**Query as understood:** `q="kettle | pot"` `limit=2`' in md
    assert "41 poles found; showing 3 to 4 (next page: offset=4)." in md
    assert f"| 3 | [+ L7/E3/N42]({URL}) | `L7_E3_N42__pos` | one dominant mode | 2 | Words for kettles, then teapot lids | 2.5 |" in md
    assert "| 4 | **− L7/E3/N42** | `L7_E3_N42__neg` |" in md
    assert "## Matching texts\n\n- **+ L7/E3/N42** *headline*: Words for kettles" in md
    assert "## Counts over all results\n\n- **layer:** 7: 2" in md and "## Notes\n\n- Substring search was used." in md

    neuron = NeuronHit(did="L7_E3_N42", display="L7/E3/N42", layer=7, expert=3, neuron=42, circuit_label="Kettle.", score=2.5, poles=[hit])
    by_neuron = SearchResponse(query={"q": "kettle"}, total=1, offset=0, limit=25, group_by="neuron", neurons=[neuron])
    md = render.search_markdown(by_neuron)
    assert "1 neuron found; showing 1 to 1." in md and "## 1 — L7/E3/N42" in md and "id `L7_E3_N42` · layer 7 · score 2.5" in md
    assert f"[+ L7/E3/N42]({URL}) (one dominant mode, 2 modes): Words for kettles, then teapot lids" in md
    assert "- *headline*: Words for kettles" in md

    mode = ModeHit(pid="L7_E3_N42__pos", did="L7_E3_N42", pole="pos", display="+ L7/E3/N42", layer=7, category="fn",
                   mode_index=1, label="Teapot | lids", share=0.078, score=1.5)  # fmt: skip
    by_mode = SearchResponse(query={}, total=1, offset=0, limit=25, group_by="mode", modes=[mode])
    md = render.search_markdown(by_mode)
    assert "**Query as understood:** (everything)" in md and "1 mode found" in md
    assert "| 1 | **+ L7/E3/N42** | `L7_E3_N42__pos` | 1 | Teapot \\| lids | 7.8% | 1.5 |" in md

    nothing = render.search_markdown(SearchResponse(query={"q": "zzz"}, total=0, offset=0, limit=25, group_by="pole"))
    assert "0 poles found." in nothing and "| # |" not in nothing


def test_similar_markdown():
    response = SimilarResponse(
        anchor="L7_E3_N42__pos", by="pole", method="lsa-256", notes=["Built from labels."],
        items=[
            SimilarItem(pid="L9_E1_N5__neg", display="− L9/E1/N5", score=0.80449, description="Teapots | urns", category="mono", layer=9),
            SimilarItem(pid="L4_E2_N6__pos", display="+ L4/E2/N6", score=0.5, mode_index=3, label="Samovars", category="poly",
                        layer=4, atlas_url=URL),
        ],
    )  # fmt: skip
    md = render.similar_markdown(response)
    assert md.startswith("# Neuron Atlas — Similar to the pole “L7_E3_N42__pos”\n") and "lsa-256" in md
    assert "| # | Pole | Id | Mode | Description | Category | Layer | Similarity |" in md
    assert "| 1 | **− L9/E1/N5** | `L9_E1_N5__neg` | – | Teapots \\| urns | mono | 9 | 0.804 |" in md
    assert f"| 2 | [+ L4/E2/N6]({URL}) | `L4_E2_N6__pos` | 3 | Samovars | poly | 4 | 0.500 |" in md
    assert "- Built from labels." in md
    empty = render.similar_markdown(SimilarResponse(anchor="hot water", by="text", method="lsa-256"))
    assert "Similar to the text “hot water”" in empty and "Nothing similar was found." in empty


# --------------------------------------------------------------------------- CSV


def read_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def test_csv_header_and_row():
    header = render.csv_header()
    assert header.endswith("\r\n") and header.count("\n") == 1
    columns = read_csv(header)[0]
    assert columns == [
        "pid", "did", "pole", "layer", "expert", "neuron", "category", "category_words", "verdict", "n_modes",
        "n_subcircuits", "main_damage_share", "simulatability", "in_public_sample", "headline", "description",
        "top_mode_label", "top_mode_share", "modes", "atlas_url",
    ]  # fmt: skip
    hit = make_hit(description='Kettles, "pots"\nand lids', simulatability=-0.5)
    line = render.csv_row(hit)
    assert line.endswith("\r\n") and '"Kettles, ""pots""\nand lids"' in line
    (row,) = read_csv(line)
    record = dict(zip(columns, row, strict=True))
    assert len(row) == 20 and record["pid"] == "L7_E3_N42__pos" and record["layer"] == "7" and record["expert"] == "3"
    assert record["description"] == 'Kettles, "pots"\nand lids' and record["in_public_sample"] == "true"
    assert record["main_damage_share"] == "0.93" and record["simulatability"] == "-0.5"  # a number stays a number
    assert record["top_mode_label"] == "Kettle words" and record["top_mode_share"] == "0.62"
    assert record["modes"] == "Kettle words (62%) | Teapot lids (7.8%)" and record["atlas_url"] == URL
    (bare,) = read_csv(render.csv_row(make_hit(expert=None, modes=None, n_modes=None, verdict=None, atlas_url=None)))
    assert len(bare) == 20 and bare[4] == "" and bare[16:] == ["", "", "", ""]


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_cells_cannot_become_formulas(start):
    hit = make_hit(headline=f"{start}HYPERLINK(1)", description=f"{start}cmd", modes=[ModeOut(index=0, label=f"{start}SUM(A1)", share=0.5)])
    (row,) = read_csv(render.csv_row(hit))
    assert row[14].startswith("'") and row[15].startswith("'") and row[16].startswith("'")
    assert row[14][1:].replace("\n", "\r") == f"{start}HYPERLINK(1)"  # a lone CR is written as a line feed
    assert not any(cell[:1] in ("=", "+", "-", "@", "\t", "\r") for cell in row)
    assert "\x00" not in render.csv_row(make_hit(description="nul\x00byte"))


def test_markup_in_atlas_text_stays_text_even_next_to_a_web_address():
    """A GFM autolink takes a backslash into the address, so "<" is written as an entity."""
    hit = make_hit(headline="Links such as https://example.org/<img src=x onerror=alert(1)> and www.x.org/<b>")
    md = render.pole_markdown(PoleDetail(pole=hit))
    assert "<img" not in md and "<b>" not in md and "https://example.org/&lt;img" in md
    assert len(render._md("a" + "_" * 100_000 + "a")) > 100_000  # underscore runs: linear, not quadratic
