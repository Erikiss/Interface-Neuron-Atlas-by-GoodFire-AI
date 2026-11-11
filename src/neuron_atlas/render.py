"""Documents a person can print, copy and quote, and a chatbot can read.

The explorer on goodfire.com cannot be printed and its text cannot be selected
as a whole.  This module turns the normalised models of :mod:`.models` into

* Markdown (:func:`pole_markdown`, :func:`neuron_markdown`, :func:`hit_markdown`,
  :func:`search_markdown`, :func:`similar_markdown`),
* plain text for e-mail and text editors (:func:`pole_text`),
* a standalone, print-ready HTML page (:func:`pole_html`) and
* CSV rows (:func:`csv_header`, :func:`csv_row`).

Every function is pure: models in, one string out.

How it is built.  A document is first laid out once as a flat list of *blocks*
(heading, paragraph, list, table, example passage, ...); three small writers
then turn the same blocks into Markdown, plain text or HTML.  The three formats
therefore always carry the same content in the same order.

Two kinds of text come out of the atlas and they are treated differently:

* *Prose* (labels, descriptions, explanations) is escaped so that nothing in it
  is read as markup: ``|`` inside tables, ``<`` wherever it could open a tag,
  and so on.  « », typographic quotes and other plain characters stay as they are.
* *Passages* are raw corpus text in any language - code, HTML, base64, binary
  shown as text.  They are never interpreted and never wrapped: Markdown puts
  them into fenced blocks whose fence is longer than any backtick run inside,
  HTML escapes them and keeps their white space.  Control characters that would
  corrupt a document (NUL, other C0/C1 codes) are removed; ``\\n`` and ``\\t`` stay.

Vocabulary follows :mod:`.glossary`; phrases in "typographic quotes" inside the
rendered notes are taken from the explorer itself.
"""

from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .models import (
    CircuitSides,
    Damage,
    Example,
    NeuronDetail,
    NeuronRef,
    PoleDetail,
    PoleHit,
    SearchResponse,
    SimilarResponse,
    Subcircuit,
)

_ATLAS_NAME = "Qwen3.6 Neuron Atlas by Goodfire"
_ATLAS_URL = "https://www.goodfire.com/research/public_neuron_atlas"
_RENDERER_NAME = "Neuron Atlas Interface"

#: Width of prose in the plain-text rendering.
_TEXT_WIDTH = 100
#: Characters of preceding context shown before a passage window (the explorer shows 600 as well).
_CONTEXT_CHARS = 600
#: Token shading as on goodfire.com: orange for the positive pole, slate for the negative pole.
_POLE_RGB = {"pos": "224,86,27", "neg": "51,105,138"}

_KIND_TAG = {"closed_gate": "closed gate", "residue": "unassigned"}
_KIND_WORDS = {
    "closed_gate": (
        "Closed-gate firings (Q3/Q4): the neuron's gate input was negative here, so it “fired only through a "
        "weak leak”. They are read separately from the other modes."
    ),
    "residue": "Unassigned: firings and subcircuits that fit no mode.",
}
_SOURCE_WORDS = {
    "subcircuit": "Modes and subcircuits (subcircuit read)",
    "overview": "Overview (neuron scientist)",
    "landscape": "Firing landscape",
    "islands": "Islands (the older read)",
    "circuit": "Circuit",
}
_STATUS_WORDS = {
    "used": "used in this document",
    "not_requested": "exists, but was not requested for this document",
    "not_mirrored": "exists upstream, but is not mirrored here",
    "absent": "does not exist for this pole",
}
_LEGEND_TEXT = (
    "Reading the examples: each passage is raw corpus text; the firing token is marked «like this». Strengths are "
    "multiples of the pole's firing threshold θ (“3.3× threshold”)."
)
_SIDES = (
    ("gate_drivers", "Gate drivers", "earlier neurons that open the gate"),
    ("gate_opposers", "Gate opposers", "earlier neurons that close the gate"),
    ("up_drivers", "Up drivers", "earlier neurons that raise the value the neuron writes"),
    ("up_opposers", "Up opposers", "earlier neurons that lower it"),
    ("readers", "Readers", "later neurons that use this neuron's output"),
)

# --------------------------------------------------------------------------- raw values


def _number(value: Any) -> float | None:
    """``value`` as a finite float, or None for anything that is not a real number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_SPACE = re.compile(r"\s+")


def _clean(text: Any) -> str:
    """Atlas text made safe to put into any document.

    NUL and the other C0/C1 control characters are dropped (``\\n`` and ``\\t``
    stay; ``\\r\\n`` and a lone ``\\r`` become ``\\n``), and unpaired surrogates -
    which cannot be encoded as UTF-8 - become U+FFFD.  Nothing else is touched.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _SURROGATE.sub("\ufffd", _CONTROL.sub("", text))


def _flat(text: Any) -> str:
    """Prose on one line: cleaned, runs of white space collapsed."""
    return _SPACE.sub(" ", _clean(text)).strip()


def _short(text: Any, limit: int) -> str:
    """The first words of ``text``, at most ``limit`` characters, cut at a word boundary."""
    flat = _flat(text)
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:·-") + "…"


# --------------------------------------------------------------------------- numbers as the atlas prints them


def _pct(value: Any) -> str:
    """A share (0..1) as the Overview prints it: "7.8%" below 10 %, "62%" above, "<0.1%" for tiny non-zero."""
    v = _number(value)
    if v is None:
        return "–"
    p = v * 100
    if p == 0:
        return "0%"
    if 0 < p < 0.05:
        return "<0.1%"
    return f"{p:.1f}%" if abs(p) < 9.95 else f"{p:.0f}%"


def _times(value: Any) -> str:
    """A multiple as the Overview prints it: "7.9×", "11×" from ten upwards, "<0.1×" for tiny non-zero."""
    v = _number(value)
    if v is None:
        return "–"
    if v == 0:
        return "0×"
    if 0 < v < 0.1:
        return "<0.1×"
    return f"{v:.1f}×" if abs(v) < 9.95 else f"{v:.0f}×"


def _simulatability(value: Any) -> str:
    """The simulatability score as the explorer's list prints it ("61%", "< −100%")."""
    v = _number(value)
    if v is None:
        return "not scored"
    return "< −100%" if v < -1 else f"{round(100 * v)}%"


def _count(value: Any) -> str:
    v = _number(value)
    return "–" if v is None else f"{int(v):,}"


def _plural(n: Any, word: str) -> str:
    return f"{_count(n)} {word}" if n == 1 else f"{_count(n)} {word}s"


def _num(value: Any, digits: int = 3, sign: bool = False) -> str:
    """A measurement with ``digits`` significant digits; plain decimals where readable, else "1.20e-07"."""
    v = _number(value)
    if v is None:
        return "–"
    if v == 0:
        return "0"
    magnitude = abs(v)
    if 1e-4 <= magnitude < 1e15:
        decimals = max(0, digits - 1 - math.floor(math.log10(magnitude)))
        text = f"{v:.{decimals}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    else:
        text = f"{v:.{digits - 1}e}"
    return "+" + text if sign and v > 0 else text


# --------------------------------------------------------------------------- the document model


@dataclass(frozen=True)
class _Span:
    """A run of inline text.

    ``style``: "" plain prose · "b" strong · "i" emphasis · "code" raw text shown
    verbatim · "link" (target in ``href``) · "tag" a short marker such as
    [Q3/Q4] · "swatch" / "ring" a colour chip (``text`` is the colour; HTML only).
    """

    text: str
    style: str = ""
    href: str = ""


_Inline = list[_Span]


@dataclass
class _Heading:
    """``level`` 0 is the document title, 1 a major section, 2 and 3 nest below."""

    level: int
    text: str
    kicker: str = ""  # short label in front of the text, e.g. "Mode 3" or the pole id
    anchor: str = ""  # HTML id
    new_page: bool = False  # start on a fresh page when printed


@dataclass
class _Para:
    spans: _Inline
    role: str = ""  # "" body text · "note" small print · "label" heads what follows · "footer" attribution


@dataclass
class _Facts:
    """Key facts: (label, value, phrase).  Markdown and text join the phrases into one line; HTML draws a table."""

    items: list[tuple[str, str, str]]


@dataclass
class _Items:
    """A list.  ``markers`` (one per item, e.g. "1.") makes it a numbered list; without, bullets."""

    items: list[_Inline]
    markers: list[str] | None = None


@dataclass(frozen=True)
class _Bar:
    """A table cell that holds a share: its text everywhere, plus a bar in HTML."""

    text: str
    fraction: float


_Cell = str | _Inline | _Bar


@dataclass
class _Table:
    head: list[str]
    rows: list[list[_Cell]]
    align: str = ""  # one letter per column: "l" or "r"
    matrix: bool = False  # a grid of numbers (set small in HTML)


@dataclass
class _Passage:
    """One example.  ``sign`` and ``vmax`` drive the token shading of the HTML rendering."""

    example: Example
    sign: int
    vmax: float


@dataclass
class _Legend:
    """How to read the passages (HTML: the shading legend)."""

    pole: str
    shaded: bool


@dataclass
class _Verbatim:
    """Preformatted text that is not corpus text (JSON)."""

    text: str
    info: str = "json"


@dataclass
class _Report:
    """The scientist's report: Markdown source whose top heading is to appear at ``level``."""

    markdown: str
    level: int


_Block = _Heading | _Para | _Facts | _Items | _Table | _Passage | _Legend | _Verbatim | _Report


def _spans(*parts: str | _Span) -> _Inline:
    return [part if isinstance(part, _Span) else _Span(part) for part in parts]


def _b(text: str) -> _Span:
    return _Span(text, "b")


def _code(text: str) -> _Span:
    return _Span(text, "code")


def _tag(text: str) -> _Span:
    return _Span(text, "tag")


def _para(*parts: str | _Span) -> _Para:
    return _Para(_spans(*parts))


def _note(*parts: str | _Span) -> _Para:
    return _Para(_spans(*parts), "note")


def _prose(text: Any, label: str = "") -> list[_Para]:
    """Running text as paragraphs, one per line of the source; ``label`` is set in bold before the first."""
    lines = [line.strip() for line in _clean(text).split("\n") if line.strip()]
    paras = [_para(line) for line in lines]
    if paras and label:
        paras[0].spans.insert(0, _b(label))
        paras[0].spans.insert(1, _Span(" "))
    return paras


def _joined(parts: Iterable[str | _Span | None], separator: str = " · ") -> _Inline:
    """Inline parts joined by a middle dot, empty ones left out."""
    spans: _Inline = []
    for part in parts:
        if part is None or part == "":
            continue
        if spans:
            spans.append(_Span(separator))
        spans.append(part if isinstance(part, _Span) else _Span(part))
    return spans


# --------------------------------------------------------------------------- examples


def _sign(pole: str) -> int:
    return -1 if pole == "neg" else 1


def _vmax(examples: Sequence[Example]) -> float:
    """Largest absolute token activation over a group of examples: the full shade of that group."""
    values = [abs(v) for e in examples for _, v in e.tokens or [] if v is not None]
    return max(values, default=0.0)


def _passages(examples: Sequence[Example], pole: str, label: bool = True) -> list[_Block]:
    """The examples of one mode, subcircuit or island (shading is scaled within the group)."""
    if not examples:
        return []
    vmax = _vmax(examples)
    blocks: list[_Block] = [_Para([_b("Examples" if len(examples) > 1 else "Example")], "label")] if label else []
    return blocks + [_Passage(e, _sign(pole), vmax) for e in examples]


def _example_facts(example: Example) -> tuple[str, list[str], str]:
    """Header of an example: (strength, further facts, reference) - only what is set."""
    act = _number(example.act)
    if example.a_rel is not None:
        strength = f"{_times(abs(example.a_rel))} threshold"
    elif act is not None:
        strength = f"peak activation {act:+.2f}"
    else:
        strength = "Example"
    facts = []
    if example.group:
        facts.append(_flat(example.group))
    if example.closed_gate:
        facts.append("Q3/Q4 (closed gate)")
    if example.held_out:
        facts.append("held-out")
    if example.repeats and example.repeats > 1:
        facts.append(f"×{example.repeats} same context")
    if act is not None and example.a_rel is not None:
        facts.append(f"peak activation {act:+.2f}")
    return strength, facts, _flat(example.ref)


def _context(example: Example) -> tuple[str, str]:
    """The text before the passage window, cut from the left: (label, text) or ("", "")."""
    pre = _clean(example.pre)
    if not pre.strip():
        return "", ""
    if len(pre) > _CONTEXT_CHARS:
        label = f"last {_CONTEXT_CHARS:,} of {len(pre):,} characters"
        pre = pre[-_CONTEXT_CHARS:]
    else:
        label = f"{_plural(len(pre), 'character')}"
    return f"Context before the window ({label})", "…" + pre


def _token_window(example: Example) -> tuple[int, int, bool, bool]:
    """The slice of ``example.tokens`` that ``example.text`` shows: (from, to, "… " before?, " …" after?).

    The text is not always all tokens: island examples store the whole passage
    but show about 25 tokens either side of the firing token (between "… " and
    " …"), and padding at the end of a document is left out.  The window is
    recovered by locating the shown text in the joined tokens, so that the shaded
    HTML passage and the plain passage are always the same text.
    """
    tokens = example.tokens or []
    focal = example.focal_index
    in_range = focal is not None and 0 <= focal < len(tokens)
    pieces = [f"«{text}»" if i == focal else text for i, (text, _) in enumerate(tokens)]
    starts = [0]
    for piece in pieces:
        starts.append(starts[-1] + len(piece))
    whole, text = "".join(pieces), example.text
    # First the text as it stands (corpus text may itself begin with "… "), then as a window between ellipses.
    for core in dict.fromkeys((text, text.removeprefix("… ").removesuffix(" …"))):
        position = whole.find(core) if core else -1
        while position >= 0:
            end = position + len(core)
            if not in_range or (position <= starts[focal] and starts[focal + 1] <= end):
                lo = max(i for i, start in enumerate(starts) if start <= position)
                hi = min(i for i, start in enumerate(starts) if start >= end)
                elided = core != text
                return min(lo, len(tokens)), hi, elided and text.startswith("… "), elided and text.endswith(" …")
            position = whole.find(core, position + 1)
    return 0, len(tokens), False, False


def _shade(value: float | None, sign: int, vmax: float, pole: str) -> tuple[str, bool] | None:
    """Background of one token as on goodfire.com: (css colour, white text?) or None for no shading."""
    if value is None or vmax <= 0:
        return None
    ratio = min(1.0, abs(value) / vmax)
    if value * sign > 0:
        alpha = ratio * 0.9
        return (f"rgba({_POLE_RGB[pole]},{alpha:.2f})", alpha > 0.5) if alpha >= 0.005 else None
    if abs(value) > 0.05 * vmax:
        return f"rgba({_POLE_RGB['pos' if pole == 'neg' else 'neg']},{ratio * 0.6:.2f})", False
    return None


_GUILLEMETS = re.compile(r"«[^«»]*»")


def _focal_span(example: Example) -> tuple[int, int] | None:
    """Where the «firing token» sits in ``example.text`` (corpus text may hold other « » pairs)."""
    text = example.text
    marked = f"«{example.focal_token}»" if example.focal_token is not None else ""
    pattern = re.compile(re.escape(marked)) if marked and marked in text else _GUILLEMETS
    # Windows are cut around the firing token, so of several candidates the one nearest the middle is it.
    spans = [m.span() for m in pattern.finditer(text)]
    return min(spans, key=lambda span: abs(span[0] + span[1] - len(text)), default=None)


# --------------------------------------------------------------------------- laying out a pole


def _pole_title(pole: PoleHit) -> str:
    """Headline of a pole, or the first words of its description."""
    return _flat(pole.headline) or _short(pole.description, 110)


def _where(layer: int, expert: int | None, neuron: int) -> str:
    return f"layer {layer} / " + (f"expert {expert}" if expert is not None else "no expert (shared)") + f" / neuron {neuron}"


def _pole_facts(p: PoleHit, detail: PoleDetail | None = None) -> _Facts:
    """The key facts of a pole; ``detail`` adds what only the per-pole files know."""
    side = "negative" if p.pole == "neg" else "positive"
    where = _where(p.layer, p.expert, p.neuron)
    items = [
        ("Id", _flat(p.pid), f"id {_flat(p.pid)}"),
        ("Pole", side, f"{side} pole"),
        ("Category", _flat(p.category_words), _flat(p.category_words)),
    ]
    if p.n_modes is not None:
        items.append(("Modes", _count(p.n_modes), _plural(p.n_modes, "mode")))
    if p.n_subcircuits is not None:
        items.append(("Subcircuits", _count(p.n_subcircuits), _plural(p.n_subcircuits, "subcircuit")))
    items.append(("Neuron", where, where))
    if detail is not None and detail.theta is not None:
        items.append(("Firing threshold θ", _num(detail.theta, 4), f"θ = {_num(detail.theta, 4)}"))
    if detail is not None and detail.n_firings is not None:
        items.append(("Firings analysed", _count(detail.n_firings), f"{_count(detail.n_firings)} firings analysed"))
    if detail is not None and detail.closed_gate_share is not None:
        share = _pct(detail.closed_gate_share)
        items.append(("Gate closed", f"{share} of firings", f"gate closed on {share} of firings"))
    if p.simulatability is not None:
        score = _simulatability(p.simulatability)
        items.append(("Simulatability", score, f"simulatability {score}"))
    if p.main_damage_share is not None:
        share = _pct(p.main_damage_share)
        items.append(("Damage carried by the main mode", share, f"main mode carries {share} of the damage"))
    if p.verdict:
        items.append(("Verdict", _flat(p.verdict), f"verdict: {_flat(p.verdict)}"))
    public = "yes" if p.in_public_sample else "no"
    items.append(("In the public sample on goodfire.com", public, f"in the public sample on goodfire.com: {public}"))
    if p.score:
        items.append(("Search score", _num(p.score, 4), f"score {_num(p.score, 4)}"))
    return _Facts(items)


def _original_link(pole: PoleHit) -> _Para:
    if _url(pole.atlas_url):
        return _para("Original page: ", _Span(pole.atlas_url, "link", pole.atlas_url))
    return _para(
        "Original page: none - goodfire.com lists a sample of 3,000 poles and this pole is not among them. "
        "The atlas itself: ",
        _Span(_ATLAS_URL, "link", _ATLAS_URL),
    )


def _head_blocks(detail: PoleDetail) -> list[_Block]:
    """Title, key facts, link to the original and the descriptions of the pole as a whole."""
    p = detail.pole
    blocks: list[_Block] = [_Heading(0, _pole_title(p), kicker=_flat(p.display)), _pole_facts(p, detail), _original_link(p)]
    if _flat(p.description) and _flat(p.description) != _flat(p.headline):
        blocks += _prose(p.description, "Description.")
    blocks += _prose(detail.summary, "Summary.")
    blocks += _prose(detail.unified_description, "Unified description.")
    blocks += _prose(detail.auto_label, "Automatic label of the whole neuron.")
    return blocks


def _damage_words(damage: Damage | None) -> str:
    """ "4.1× the pole's average (KL 0.0012 per firing, 72 measured)" - only what is set."""
    if damage is None:
        return ""
    extra = []
    if damage.per_firing is not None:
        extra.append(f"KL {_num(damage.per_firing)} per firing")
    if damage.n_measured is not None:
        extra.append(f"{_count(damage.n_measured)} measured")
    tail = f" ({', '.join(extra)})" if extra else ""
    if damage.ratio is None:
        return f"damage{tail}" if extra else ""
    return f"damage per firing {_times(damage.ratio)} the pole's average{tail}"


def _overview_numbers(mode: dict[str, Any]) -> str:
    """The number line of the original Overview: "7.8% of firings · 62% of damage · 7.9× average damage per firing"."""
    if _number(mode.get("firing_share")) is None:
        return ""
    parts = [f"{_pct(mode.get('firing_share'))} of firings"]
    if _number(mode.get("damage_share")) is not None:
        parts.append(f"{_pct(mode.get('damage_share'))} of damage")
    if _number(mode.get("damage_per_firing")) is not None:
        parts.append(f"{_times(mode.get('damage_per_firing'))} average damage per firing")
    tested = _number(mode.get("tested_firings"))
    if tested is not None and tested < 10 and len(parts) > 1:
        parts.append(f"rough: {int(tested)} tested firing{'' if tested == 1 else 's'}")
    return " · ".join(parts)


def _landscape_modes(detail: PoleDetail) -> list[dict[str, Any]]:
    return _dicts(_dict(detail.landscape).get("modes"))


def _mode_blocks(detail: PoleDetail) -> list[_Block]:
    """The modes: a table at a glance, then one section per mode with its numbers, description and examples."""
    modes = detail.modes
    if not modes:
        return []
    blocks: list[_Block] = [_Heading(1, "Modes", anchor="modes")]
    with_damage = any(m.damage and m.damage.ratio is not None for m in modes)
    rows: list[list[_Cell]] = []
    for m in modes:
        tag = _KIND_TAG.get(m.kind, "")
        tagged = bool(tag) and tag not in _flat(m.public_label).lower()
        row: list[_Cell] = [str(m.index), _spans(_flat(m.public_label), *([" ", _tag(tag)] if tagged else []))]
        row.append(_Bar(_pct(m.share), m.share) if m.share is not None else "–")
        if with_damage:
            row.append(_times(m.damage.ratio) if m.damage and m.damage.ratio is not None else "–")
        rows.append(row)
    head = ["#", "Mode", "Share of firings"] + (["Damage per firing"] if with_damage else [])
    blocks.append(_Table(head, rows, "llr" + ("r" if with_damage else "")))
    note = "Share: the mode's share of the pole's sampled firings."
    if with_damage:
        note += " Damage per firing is relative to the pole's average firing (1× = typical)."
    blocks.append(_note(note))

    real_text = {_str(m.get("label")): m for m in _landscape_modes(detail) if m.get("kind") == "open"}
    for m in modes:
        facts = []
        if m.share is not None:
            facts.append(f"{_pct(m.share)} of the pole's sampled firings")
        if m.n_firings is not None:
            facts.append(_plural(m.n_firings, "firing"))
        facts.append(_damage_words(m.damage))
        if m.subcircuits:
            facts.append(f"{_plural(len(m.subcircuits), 'subcircuit')}: {', '.join(_flat(s) for s in m.subcircuits)}")
        numbers = _overview_numbers(real_text.get(m.label, {})) if m.kind == "open" else ""
        if not (m.description or m.examples or m.damage or m.subcircuits or m.n_firings or numbers):
            continue  # nothing beyond the table row (a pole known from the index only)
        blocks.append(_Heading(2, _flat(m.public_label), kicker=f"Mode {m.index}", anchor=f"mode-{m.index}"))
        if any(facts):
            blocks.append(_Para(_joined(facts)))
        if numbers:
            blocks.append(_para(_b("In real text (Overview):"), " " + numbers))
        if m.kind != "open":
            words = _KIND_WORDS[m.kind]
            if _flat(m.label) and _flat(m.label) != _flat(m.public_label):
                words += f" Label in the data: “{_flat(m.label)}”."
            blocks.append(_note(words))
        blocks += _prose(m.description)
        blocks += _passages(m.examples, detail.pole.pole)
    return blocks


def _mode_number(mode: dict[str, Any], position: int) -> int:
    """The index a landscape mode is known by (its position when the data does not say)."""
    number = _number(mode.get("index"))
    return int(number) if number is not None else position


def _landscape_mode_table(modes: list[dict[str, Any]], top: float | None) -> _Table:
    """One row per landscape mode with the numbers of the original Overview."""
    rows: list[list[_Cell]] = []
    for position, m in enumerate(modes):
        closed = m.get("kind") == "closed_gate"
        name: _Inline = []
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", _str(m.get("colour"))):
            name.append(_Span(m["colour"], "ring" if closed else "swatch"))
        name.append(_Span(_flat(m.get("label")) or "(unnamed)"))
        if closed:
            name += _spans(" ", _tag("Q3/Q4"))
        if top is not None and _mode_number(m, position) == int(top):
            name += _spans(" ", _tag("most damage"))
        tested = _number(m.get("tested_firings"))
        rough = tested is not None and tested < 10 and _number(m.get("damage_share")) is not None
        share = _number(m.get("firing_share"))
        rows.append(
            [
                str(_mode_number(m, position)),
                name,
                _pct(m.get("sample_share")),
                _Bar(_pct(share), share) if share is not None else "–",
                _pct(m.get("damage_share")),
                _times(m.get("damage_per_firing")),
                "–" if tested is None else (f"{int(tested)} (rough)" if rough else str(int(tested))),
            ]
        )
    head = ["#", "Mode", "Sampled share", "Of firings", "Of damage", "Damage per firing", "Tested firings"]
    return _Table(head, rows, "llrrrrr")


def _level_blocks(levels: list[dict[str, Any]], modes: list[dict[str, Any]]) -> list[_Block]:
    """The five rarity levels, and how the firings and the damage of each level split over the modes."""
    rows: list[list[_Cell]] = []
    for level in levels:
        lo, hi = _number(level.get("min_strength")), _number(level.get("max_strength"))
        one_in = _number(level.get("one_in_tokens"))
        rows.append(
            [
                _flat(level.get("name")) or _flat(level.get("source_name")) or "–",
                _times(level.get("median_strength")),
                f"{_times(lo)} to {_times(hi)}" if lo is not None and hi is not None else "–",
                f"1 in {_count(one_in)} tokens" if one_in else "–",
                _count(level.get("sampled_firings")),
            ]
        )
    head = ["Level", "Typical strength", "Strength range", "How often in real text", "Sampled firings"]
    blocks: list[_Block] = [
        _Heading(2, "The five rarity levels", anchor="levels"),
        _Table(head, rows, "lrrrr"),
        _note(
            "Strength is “the neuron's activation in multiples of this pole's firing threshold”; the levels run "
            "“from the top 5% of tokens” to “the 1,000 strongest” firings."
        ),
    ]
    if not modes or not any(_list(level.get("firings_by_mode")) for level in levels):
        return blocks
    rows = []
    for position, m in enumerate(modes):
        row: list[_Cell] = [f"{_mode_number(m, position)} · {_short(m.get('label'), 60) or '(unnamed)'}"]
        for level in levels:
            counts, damage = _list(level.get("firings_by_mode")), _list(level.get("damage_by_mode"))
            count = _number(counts[position]) if position < len(counts) else None
            part = _number(damage[position]) if position < len(damage) else None
            total = sum(_number(d) or 0.0 for d in damage)
            cell = "–" if count is None else _count(count)
            if count and part is not None and total > 0:
                cell += f" ({_pct(part / total)})"
            row.append(cell)
        rows.append(row)
    names = [_flat(level.get("name")) or f"Level {i + 1}" for i, level in enumerate(levels)]
    blocks.append(_Table(["Mode"] + names, rows, "l" + "r" * len(levels)))
    blocks.append(
        _note(
            "Per level: the sampled firings that fall in the mode and, in brackets, the mode's share of the "
            "damage at that level - the width of its lane in the original picture."
        )
    )
    return blocks


def _matrix_table(matrix: Any, labels: list[str]) -> _Table | None:
    """A square mode × mode matrix as a table of two-decimal numbers."""
    rows = [row for row in _list(matrix) if isinstance(row, list)]
    if not rows or len(rows) > len(labels):
        return None
    body: list[list[_Cell]] = []
    for label, row in zip(labels, rows, strict=False):
        body.append([label] + [("–" if _number(v) is None else f"{v:.2f}") for v in row[: len(rows)]])
    return _Table(["Mode"] + labels[: len(rows)], body, "l" + "r" * len(rows), matrix=True)


def _landscape_blocks(detail: PoleDetail) -> list[_Block]:
    """The firing landscape in numbers (the original draws it as a picture)."""
    view = _dict(detail.landscape)
    modes = _landscape_modes(detail)
    levels = _dicts(view.get("levels"))
    if not modes and not levels:
        return []
    blocks: list[_Block] = [
        _Heading(1, "Firing landscape", anchor="landscape"),
        _note(
            "How the pole's firings split over its modes at five levels of rarity. Shares are “of this pole's firings "
            "above the top-5% cutoff, weighted to how often they occur in real text”; damage per firing is “relative "
            "to the pole's average firing”. Numbers “resting on fewer than 10 tested firings are marked rough”."
        ),
    ]
    facts = []
    if view.get("verdict"):
        facts.append(f"verdict: {_flat(view.get('verdict'))}")
    if _number(view.get("sampled_firings")) is not None:
        facts.append(f"{_count(view.get('sampled_firings'))} sampled firings")
    if _number(view.get("firings_listed")) is not None:
        listed = f"{_count(view.get('firings_listed'))} firings listed individually"
        if _number(view.get("gate_closed_firings_listed")) is not None:
            listed += f", {_count(view.get('gate_closed_firings_listed'))} of them with the gate closed"
        facts.append(listed)
    if _flat(view.get("strength_unit")):
        facts.append(f"strength in {_flat(view.get('strength_unit'))}")
    if facts:
        blocks.append(_Para(_joined(facts)))

    if modes:
        blocks.append(_landscape_mode_table(modes, _number(view.get("top_damage_mode"))))
        blocks.append(
            _note(
                "Sampled share counts each of the five rarity levels equally (the share printed in the mode list). "
                "“Of firings”, “of damage” and the “average damage per firing” are weighted to real text; they are "
                "the numbers the original Overview shows."
            )
        )
    # Modes the mode list above does not describe: the landscape splits the closed-gate firings into modes of their own.
    described = {(m.index, _flat(m.label)) for m in detail.modes if m.description}
    for position, m in enumerate(modes):
        index, label = _mode_number(m, position), _flat(m.get("label"))
        relation = _flat(_dict(m.get("relation")).get("text"))
        if not (_flat(m.get("description")) or relation) or (index, label) in described:
            continue
        spans = _spans(_b(f"Mode {index} - {label}" + (" (Q3/Q4)." if m.get("kind") == "closed_gate" else ".")))
        for sentence in (_overview_numbers(m), relation):
            if sentence:
                spans.append(_Span(f" {sentence}."))
        if _flat(m.get("description")):
            spans.append(_Span(" " + _flat(m.get("description"))))
        blocks.append(_Para(spans))

    if levels:
        blocks += _level_blocks(levels, modes)
    fit = _dict(view.get("damage_fit"))
    if _number(fit.get("slope")) is not None:
        details = []
        if _number(fit.get("r2")) is not None:
            details.append(f"R² = {_num(fit.get('r2'))}")
        if _number(fit.get("tested_firings")) is not None:
            details.append(f"{_count(fit.get('tested_firings'))} tested firings")
        text = f"Damage against strength: damage grows as strength^{_num(fit.get('slope'))}"
        text += f" ({', '.join(details)})" if details else ""
        blocks.append(_para(text + "; untested firings are estimated from strength and mode."))
    order = [str(int(i)) for i in _list(view.get("mode_order")) if _number(i) is not None]
    if order:
        blocks.append(_para(f"Order of the lanes in the original picture (similar modes side by side): {', '.join(order)}."))
    labels = [str(_mode_number(m, position)) for position, m in enumerate(modes)]
    for key, title in (("by_description", "by description"), ("by_firing", "by firing")):
        table = _matrix_table(_dict(view.get("mode_similarity")).get(key), labels)
        if table:
            blocks += [_Heading(2, f"Similarity of the modes, {title}", anchor=f"similarity-{key}"), table]
    return blocks


def _situation_blocks(detail: PoleDetail, view: dict[str, Any]) -> list[_Block]:
    """The scientist's account of each mode (on single-mode poles: of each subcircuit)."""
    situations = _dicts(view.get("situations"))
    if not situations:
        return []
    per_subcircuit = view.get("situations_refer_to") == "subcircuit"
    title = "Situations (one per subcircuit of the single mode)" if per_subcircuit else "Situations (one per mode)"
    blocks: list[_Block] = [_Heading(2, title, anchor="situations")]
    for s in situations:
        mode = _number(s.get("mode_index"))
        where = f"Mode {int(mode)}" if mode is not None else ""
        if s.get("subcircuit"):
            where = f"Subcircuit {_flat(s.get('subcircuit'))}"
        lead = " - ".join(part for part in (where, _flat(s.get("name"))) if part)
        blocks.append(_para(_b(lead + "." if lead else "Situation."), " " + _flat(s.get("explain"))))
        items: list[_Inline] = []
        if _flat(s.get("typical_strength")):
            items.append(_spans("Typical strength: " + _flat(s.get("typical_strength"))))
        if _clean(s.get("cue")).strip():
            items.append(_spans("Cue (firing token in ⟦ ⟧): ", _code(_str(s.get("cue")))))
        if _flat(s.get("from_experiments")):
            items.append(_spans("From the experiments: " + _flat(s.get("from_experiments"))))
        if items:
            blocks.append(_Items(items))
    return blocks


def _digest_blocks(digest: dict[str, Any]) -> list[_Block]:
    """The Overview's short summary of the circuit: named input and reader groups, output tokens."""
    inputs, readers = _dicts(digest.get("inputs")), _dicts(digest.get("readers"))
    tokens = _dicts(digest.get("output_tokens"))
    if not (inputs or readers or tokens or _flat(digest.get("note"))):
        return []
    blocks: list[_Block] = [_Heading(2, "Circuit digest", anchor="circuit-digest")]
    if _flat(digest.get("note")):
        blocks.append(_para("No circuit: " + _flat(digest.get("note")) + "."))
    if inputs:
        rows: list[list[_Cell]] = [
            [_flat(i.get("name")), _flat(i.get("effect")), _flat(i.get("role")), _pct(i.get("share"))] for i in inputs
        ]
        blocks.append(_Table(["Input group", "Effect", "Role", "Share"], rows, "lllr"))
    if readers:
        rows = [[_flat(r.get("name")), _flat(r.get("effect")), _count(r.get("n"))] for r in readers]
        blocks.append(_Table(["Reader group", "Effect", "Neurons"], rows, "llr"))
    if tokens:
        blocks.append(_Para(_spans(_b("Output tokens most affected:"), " ") + _output_tokens(tokens)))
    return blocks


def _output_tokens(tokens: list[dict[str, Any]]) -> _Inline:
    """ "`enders` (+0.103), `mous` (+0.109)": the tokens whose logits the pole pushes most."""
    spans: _Inline = []
    for i, token in enumerate(tokens):
        if i:
            spans.append(_Span(", "))
        spans.append(_code(_str(token.get("token"))))
        if _number(token.get("weight")) is not None:
            spans.append(_Span(f" ({_num(token.get('weight'), sign=True)})"))
    return spans


def _overview_blocks(detail: PoleDetail) -> list[_Block]:
    """The Overview: the neuron scientist's explanation, experiments, scores and report."""
    view = _dict(detail.overview)
    blocks: list[_Block] = []
    headline = _flat(view.get("headline"))
    if headline:
        blocks.append(_para(_b(headline)))
        if _flat(detail.pole.headline) and headline != _flat(detail.pole.headline):
            blocks.append(
                _note(
                    "This is the neuron scientist's original headline; the title of this document is the atlas's "
                    "rewritten one (“Rewritten to lead with what carries the damage”)."
                )
            )
    blocks += _prose(view.get("summary"))
    credits = []
    if _flat(view.get("scientist_model")):
        credits.append(f"written by the neuron scientist ({_flat(view.get('scientist_model'))})")
    if _number(view.get("threshold")) is not None:
        credits.append(f"firing threshold θ = {_num(view.get('threshold'), 4)}")
    if credits:
        blocks.append(_Para(_joined(credits), "note"))
    blocks += _situation_blocks(detail, view)

    not_this = [_flat(t) for t in _list(view.get("not_this")) if _flat(t)]
    if not_this:
        blocks.append(_Heading(2, "What does not make it fire", anchor="not-this"))
        blocks.append(_Items([_spans(t) for t in not_this]))

    experiments = _dicts(view.get("experiments"))
    if experiments:
        blocks.append(_Heading(2, "Agent experiments", anchor="experiments"))
        blocks.append(
            _note(
                "“Each row is a controlled experiment: the same passage with one thing changed, measured on the "
                "model itself.”"
            )
        )
        rows: list[list[_Cell]] = [[_flat(e.get("claim")), _flat(e.get("test")), _flat(e.get("result"))] for e in experiments]
        blocks.append(_Table(["Claim", "Test", "Result"], rows, "lll"))

    scores = [s for s in _dicts(view.get("simulatability")) if _number(s.get("kl_recovered")) is not None]
    if scores:
        blocks.append(_Heading(2, "How well the description predicts the neuron", anchor="prediction"))
        blocks.append(
            _note(
                "“A simulator that never saw the neuron predicts its activation on held-out text from each description; "
                "the score is the share of the neuron's effect on the model's predictions (KL) that those predicted "
                "activations restore. 100% = as good as the real neuron.”"
            )
        )
        rows = []
        for s in scores:
            score = s["kl_recovered"]
            name = _flat(s.get("label")) or _flat(s.get("source")) or "description"
            label = _spans(name, *([" ", _tag("this description")] if s.get("is_this_description") else []))
            rows.append([label, _Bar(f"{round(100 * score)}%", max(0.0, min(1.0, score)))])
        blocks.append(_Table(["Description", "Score"], rows, "lr"))

    if _flat(view.get("open")):
        blocks.append(_Heading(2, "Still open", anchor="open"))
        blocks += _prose(view.get("open"))

    names = {_flat(k): _flat(v) for k, v in _dict(view.get("subcircuit_names")).items() if _flat(v)}
    if names:
        blocks.append(_Heading(2, "The scientist's names for the subcircuits", anchor="subcircuit-names"))
        blocks.append(_Table(["Subcircuit", "Name"], [[k, v] for k, v in names.items()], "ll"))

    blocks += _digest_blocks(_dict(view.get("circuit_digest")))

    if _clean(view.get("final_description")).strip():
        blocks.append(_Heading(2, "The scientist's final description", anchor="final-description"))
        blocks += _prose(view.get("final_description"))
    if _clean(view.get("report_markdown")).strip():
        blocks.append(_Heading(2, "The scientist's full report", anchor="report"))
        blocks.append(_Report(_str(view.get("report_markdown")), 3))
    if not blocks:
        return []
    return [_Heading(1, "Overview by the neuron scientist", anchor="overview")] + blocks


def _ref_item(ref: NeuronRef) -> _Inline:
    """ "L18_E253_N170 (+0.0164) - label" """
    spans = _spans(_code(ref.unit))
    numbers = []
    if ref.weight is not None:
        numbers.append(_num(ref.weight, sign=True))
    if ref.rest:
        numbers.append(f"elsewhere {_num(ref.rest, sign=True)}")
    if numbers:
        spans.append(_Span(f" ({'; '.join(numbers)})"))
    if _flat(ref.label):
        spans.append(_Span(" - " + _flat(ref.label)))
    if ref.in_atlas:
        spans += _spans(" ", _tag("in the atlas"))
    return spans


def _sides_blocks(sides: CircuitSides | None) -> list[_Block]:
    """The neurons on each side of a subcircuit's circuit as compact lists."""
    blocks: list[_Block] = []
    for key, title, meaning in _SIDES if sides else ():
        refs: list[NeuronRef] = getattr(sides, key)
        if refs:
            blocks.append(_para(_b(f"{title}"), f" ({meaning}; attribution in brackets)"))
            blocks.append(_Items([_ref_item(ref) for ref in refs]))
    return blocks


def _subcircuit_blocks(detail: PoleDetail) -> list[_Block]:
    """Every subcircuit of the pole, in the order the data gives them (largest first)."""
    subs = detail.subcircuits
    if not subs:
        return []
    labels = {m.index: _flat(m.public_label) for m in detail.modes}
    names = {_flat(k): _flat(v) for k, v in _dict(_dict(detail.overview).get("subcircuit_names")).items()}
    blocks: list[_Block] = [
        _Heading(1, "Subcircuits", anchor="subcircuits", new_page=True),
        _note("A subcircuit is a group “of examples where the same earlier parts of the model make the neuron fire”."),
    ]
    for s in subs:
        blocks += _one_subcircuit(s, detail.pole.pole, labels, names)
    return blocks


def _one_subcircuit(s: Subcircuit, pole: str, labels: dict[int, str], names: dict[str, str]) -> list[_Block]:
    """One subcircuit: what its firings mean, how it computes, who feeds and reads it, its examples."""
    sid = _flat(s.id)
    anchor = "sc-" + re.sub(r"[^A-Za-z0-9_-]", "", sid)
    blocks: list[_Block] = [_Heading(2, _flat(s.function_label), kicker=f"Subcircuit {sid}", anchor=anchor)]
    facts = []
    if s.mode_index is not None:
        label = labels.get(s.mode_index)
        facts.append(f"belongs to mode {s.mode_index}" + (f" ({_short(label, 80)})" if label else ""))
    if s.share is not None:
        facts.append(f"{_pct(s.share)} of the pole's firings")
    if s.n is not None:
        facts.append(_plural(s.n, "firing"))
    if s.median_a_rel is not None:
        facts.append(f"median strength {_times(s.median_a_rel)} threshold")
    if s.readable is not None:
        facts.append("readable" if s.readable else "not readable")
    if s.reading_confidence is not None:
        facts.append(f"reading confidence {s.reading_confidence}")
    if s.function_confidence is not None:
        facts.append(f"function confidence {s.function_confidence}")
    if facts:
        blocks.append(_Para(_joined(facts)))
    if names.get(sid):
        blocks.append(_para(_b("The scientist's name."), " " + names[sid]))
    blocks += _prose(s.meaning, "Meaning.")
    blocks += _prose(s.token_pattern, "Token pattern.")
    blocks += _prose(s.function, "Function.")
    blocks += _prose(s.gate_role, "Gate role.")
    blocks += _prose(s.up_role, "Up role.")
    if s.profile is not None:
        profile = [
            ("at or above θ", s.profile.ge_theta),
            ("between 0.5 θ and θ", s.profile.mid),
            ("below 0.5 θ", s.profile.weak),
            ("gate closed", s.profile.closed),
        ]
        parts = [f"{name} {_pct(value)}" for name, value in profile if value is not None]
        if parts:
            blocks.append(_para(_b("Strength profile."), " Shares of its firings: " + " · ".join(parts) + "."))
    damage = _damage_words(s.damage)
    if damage:
        blocks.append(_para(_b("Damage."), f" {damage[0].upper()}{damage[1:]}."))
    blocks += _sides_blocks(s.circuit)
    blocks += _passages(s.examples, pole)
    return blocks


def _member_item(member: dict[str, Any]) -> _Inline:
    """ "L18_E253_N170 (+0.0164) - label", plus the member's pole and distance where the data has them."""
    spans = _spans(_code(_str(member.get("unit")) or "?"))
    numbers = []
    if member.get("pole") in ("pos", "neg"):
        numbers.append("positive pole" if member["pole"] == "pos" else "negative pole")
    if _number(member.get("weight")) is not None:
        numbers.append(_num(member.get("weight"), sign=True))
    if _number(member.get("layers_ahead")) is not None:
        numbers.append(f"{_plural(int(member['layers_ahead']), 'layer')} ahead")
    if numbers:
        spans.append(_Span(f" ({', '.join(numbers)})"))
    if _flat(member.get("label")):
        spans.append(_Span(" - " + _flat(member.get("label"))))
    if member.get("in_atlas") is True:
        spans += _spans(" ", _tag("in the atlas"))
    return spans


def _group_blocks(group: dict[str, Any], reader: bool) -> list[_Block]:
    """One named input or reader group of the circuit with its member neurons."""
    lead = " - ".join(part for part in (_flat(group.get("id")), _flat(group.get("name"))) if part) or "Group"
    effect = _flat(group.get("effect"))
    facts = []
    if reader:
        facts.append({"+": "raised by this pole", "-": "lowered by this pole"}.get(effect, effect))
        if _number(group.get("examples")) is not None:
            facts.append(f"seen in {_plural(int(group['examples']), 'example')}")
        if _number(group.get("mean_effect")) is not None:
            facts.append(f"mean effect {_num(group.get('mean_effect'), sign=True)}")
        if _flat(group.get("measured_direction")):
            facts.append(f"measured on this pole: {_flat(group.get('measured_direction'))}")
    else:
        facts.append(_flat(group.get("role")))
        facts.append({"+": "net effect positive", "-": "net effect negative"}.get(effect, effect))
        facts.append(f"lane: {_flat(group.get('lane'))}" if _flat(group.get("lane")) else "")
        for key in ("gate", "up"):
            if _number(group.get(key)) is not None:
                facts.append(f"{key} {_num(group.get(key), sign=True)}")
        if _number(group.get("share")) is not None:
            facts.append(f"share {_pct(group.get('share'))}")
    members = _dicts(group.get("members"))
    n_members = _number(group.get("n_members"))
    if n_members is not None:
        listed = f", {len(members)} listed" if members and len(members) != int(n_members) else ""
        facts.append(_plural(int(n_members), "neuron") + listed)
    spans = _spans(_b(lead + "."))
    if any(facts):
        spans.append(_Span(" " + " · ".join(fact for fact in facts if fact) + "."))
    if _flat(group.get("description")):
        spans.append(_Span(" " + _flat(group.get("description"))))
    blocks: list[_Block] = [_Para(spans)]
    about = []
    for part in _dicts(group.get("about")):
        size = f" ({_plural(int(part['n']), 'neuron')})" if _number(part.get("n")) is not None else ""
        text = _flat(part.get("name")) + size
        about.append(text + (f": {_flat(part.get('description'))}" if _flat(part.get("description")) else ""))
    if about:
        blocks.append(_para("Made up of " + "; ".join(text.rstrip(".") for text in about) + "."))
    if members:
        blocks.append(_Items([_member_item(m) for m in members]))
    return blocks


def _circuit_blocks(circuit: Any, pole: str = "pos", level: int = 1) -> list[_Block]:
    """The Circuit view: who drives the neuron, who reads it, which output tokens it moves."""
    view = _dict(circuit)
    inputs, readers = _dicts(view.get("inputs")), _dicts(view.get("readers"))
    tokens = _dicts(view.get("output_tokens"))
    contexts = [c for c in _dicts(view.get("strongest_contexts")) if _clean(c.get("text")).strip()]
    notes = [_flat(n) for n in _list(view.get("notes")) if _flat(n)]
    if not (inputs or readers or tokens or contexts or notes or _flat(view.get("line"))):
        return []
    blocks: list[_Block] = [_Heading(level, "Circuit", anchor="circuit")]
    blocks += _prose(view.get("line"))
    facts = []
    if _number(view.get("examples_analysed")) is not None:
        facts.append(f"{_plural(int(view['examples_analysed']), 'example')} analysed")
    if _number(view.get("threshold")) is not None:
        facts.append(f"threshold {_num(view.get('threshold'), 4)}")
    if facts:
        blocks.append(_Para(_joined(facts), "note"))
    if notes:
        blocks.append(_Items([_spans(n) for n in notes]))
    if inputs:
        blocks.append(_Heading(level + 1, "Inputs", anchor="circuit-inputs"))
        blocks.append(
            _note(
                "Groups of earlier neurons. Gate drivers open the gate and gate opposers close it; up drivers raise "
                "the value the neuron writes and up opposers lower it. Attribution of each neuron in brackets."
            )
        )
        for group in inputs:
            blocks += _group_blocks(group, reader=False)
    if readers:
        blocks.append(_Heading(level + 1, "Readers", anchor="circuit-readers"))
        blocks.append(_note("Groups of later neurons that use the neuron's output. Attribution of each neuron in brackets."))
        for group in readers:
            blocks += _group_blocks(group, reader=True)
    if tokens:
        blocks.append(_Heading(level + 1, "Output tokens", anchor="circuit-output"))
        blocks.append(_note("The vocabulary tokens whose logits the neuron pushes most on this pole (weight in brackets)."))
        blocks.append(_Para(_output_tokens(tokens)))
    if contexts:
        blocks.append(_Heading(level + 1, "Strongest contexts", anchor="circuit-contexts"))
        examples = [Example(source="circuit", text=_str(c.get("text")), act=_number(c.get("activation"))) for c in contexts]
        blocks += _passages(examples, pole, label=False)
    return blocks


def _island_blocks(detail: PoleDetail) -> list[_Block]:
    """The islands view: its own modes, then each island with its examples."""
    view = detail.islands
    if view is None or not (view.modes or view.islands):
        return []
    blocks: list[_Block] = [
        _Heading(1, "Islands (the older read)", anchor="islands", new_page=bool(view.islands)),
        _note(
            "For one pole, “we take its strongest firing examples from a large text corpus and cluster them into "
            "islands: groups of examples that occur in similar contexts. An LLM judge then grouped islands that "
            "respond to similar concepts in the text into modes”. These are not the modes of the subcircuit read."
        ),
    ]
    number_of = {island.id: island.number for island in view.islands}
    if view.modes:
        rows: list[list[_Cell]] = []
        for m in view.modes:
            name = _spans(_b(_flat(m.name) or "(unnamed)"))
            if _flat(m.description):
                name.append(_Span(" - " + _flat(m.description)))
            numbers = ", ".join(str(number_of.get(i, i + 1)) for i in m.islands) or "–"
            rows.append([name, _Bar(_pct(m.share), m.share) if m.share is not None else "–", numbers])
        blocks.append(_Table(["Mode (islands view)", "Share of firing", "Islands"], rows, "lrl"))
        share = "“roughly how much of this pole's firing, across a large text corpus, falls in this mode”"
        blocks.append(_note(f"Share: {share}."))
    for island in view.islands:
        title = _flat(island.name) or "(unnamed island)"
        blocks.append(_Heading(2, title, kicker=f"Island {island.number}", anchor=f"island-{island.number}"))
        if island.n is not None:
            shown = f"; {len(island.examples)} shown" if island.examples else ""
            blocks.append(_note(f"{_plural(island.n, 'example')} in the cluster{shown}."))
        blocks += _prose(island.description)
        blocks += _passages(island.examples, detail.pole.pole)
    return blocks


def _research_blocks(detail: PoleDetail) -> list[_Block]:
    """The remaining upstream fields (include=research) as one JSON block."""
    extras: dict[str, Any] = {}
    if detail.research:
        extras["pole"] = detail.research
    overview = _dict(detail.overview).get("research")
    if overview:
        extras["overview"] = overview
    subcircuits = {s.id: s.research for s in detail.subcircuits or [] if s.research}
    if subcircuits:
        extras["subcircuits"] = subcircuits
    if not extras:
        return []
    return [
        _Heading(1, "Research extras", anchor="research", new_page=True),
        _note("Remaining fields of the upstream files, as data."),
        _Verbatim(json.dumps(extras, ensure_ascii=False, indent=2, default=str)),
    ]


def _availability_blocks(detail: PoleDetail) -> list[_Block]:
    """Which data sets this document rests on, and the notes of the assembler."""
    blocks: list[_Block] = []
    if detail.sources:
        rows: list[list[_Cell]] = []
        for name, status in detail.sources.items():
            rows.append([_SOURCE_WORDS.get(name, _flat(name)), _STATUS_WORDS.get(status, _flat(status))])
        blocks.append(_Table(["Data set", "Status"], rows, "ll"))
        missing = [_SOURCE_WORDS.get(n, _flat(n)) for n, status in detail.sources.items() if status == "not_mirrored"]
        if missing:
            blocks.append(
                _para(
                    "Not mirrored: " + "; ".join(missing) + ". The long texts of these data sets (descriptions, "
                    "explanations, example passages) are not mirrored and therefore not part of this document; "
                    "labels and shares come from the index."
                )
            )
    notes = [_flat(n) for n in detail.notes if _flat(n)]
    if notes:
        blocks.append(_Items([_spans(n) for n in notes]))
    if not blocks:
        return []
    return [_Heading(1, "Data availability", anchor="data")] + blocks


def _footer(url: str | None) -> _Para:
    """The attribution line; it links the pole's own page when it has one."""
    url = url if url and _url(url) else _ATLAS_URL
    return _Para(_spans(f"Source: {_ATLAS_NAME}, ", _Span(url, "link", url), f"; rendered by {_RENDERER_NAME}"), "footer")


def _pole_blocks(detail: PoleDetail) -> list[_Block]:
    """The whole document about one pole, in reading order."""
    body = (
        _mode_blocks(detail)
        + _landscape_blocks(detail)
        + _overview_blocks(detail)
        + _subcircuit_blocks(detail)
        + _circuit_blocks(detail.circuit, detail.pole.pole)
        + _island_blocks(detail)
        + _research_blocks(detail)
    )
    passages = [b for b in body if isinstance(b, _Passage)]
    blocks = _head_blocks(detail)
    if passages:
        blocks.append(_Legend(detail.pole.pole, any(p.example.tokens for p in passages)))
    return blocks + body + _availability_blocks(detail) + [_footer(detail.pole.atlas_url)]


# --------------------------------------------------------------------------- Markdown writer

#: Characters that would start markup inside running text: "_" only where it could open or close emphasis
#: (not inside snake_case or L30_E47_N449__pos), "<" only where it could open a tag (not in "<0.1%").
_MD_INLINE = re.compile(
    r"\\(?=[!-/:-@\[-`{-~]|$)|[`*~]|<(?=[A-Za-z/!?])|(?<!\w)_+|_+(?!\w)|&(?=#?\w+;)|\](?=[(\[:])", re.M
)
#: What would turn a line into a heading, quote, list item or rule.
_MD_START = re.compile(r"^[ \t]*(#{1,6}(?=[ \t]|$)|>|[-+](?=[ \t]|$)|\d{1,9}(?=[.)](?:[ \t]|$))|(?:=+|-{2,})[ \t]*$)", re.M)
_BACKTICKS = re.compile(r"`+")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-{2,})[ \t]*$")
_PLAIN_LINE = re.compile(r"^ {0,3}(?![-*+>|#]|\d+[.)])\S")
_CODE_OR_LT = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)|<(?=[A-Za-z/!?])")


def _md(text: str) -> str:
    """Prose escaped for Markdown so that nothing in it is read as markup; «» and typographic quotes stay."""
    return _MD_INLINE.sub(lambda m: "".join("\\" + char for char in m.group(0)), text)


def _md_line_start(match: re.Match[str]) -> str:
    token = match.group(1)
    return token + "\\" if token[0].isdigit() else "\\" + token


def _md_code(text: str) -> str:
    """Raw text as an inline code span (line breaks shown as ⏎)."""
    text = _clean(text).replace("\n", "⏎")
    if not text:
        return "(empty)"
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    # A renderer strips one space from each end when both are there, and a backtick must not touch the fence.
    pad = " " if "`" in (text[0], text[-1]) or (text[0] == " " == text[-1] and text.strip()) else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _md_fence(text: str, info: str = "text") -> list[str]:
    """Raw text as a fenced block whose fence is longer than any backtick run inside."""
    fence = "`" * max(3, max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    return [fence + info, text, fence]


def _url(href: Any) -> str:
    """``href`` if it is a web address, made safe to sit inside a link; otherwise "" (never a script URL)."""
    href = _flat(href)
    if not re.match(r"https?://", href, re.I):
        return ""
    return re.sub(r"[\s<>\"'`()\\]", lambda m: f"%{ord(m.group(0)):02X}", href)


def _md_inline(spans: _Inline, cell: bool = False) -> str:
    """Inline spans as Markdown; ``cell`` escapes for a table cell instead of a paragraph."""
    parts = []
    for span in spans:
        if span.style in ("swatch", "ring"):
            continue
        if span.style == "code":
            parts.append(_md_code(span.text))
            continue
        text = _md(_clean(span.text).replace("\n", " "))
        if span.style == "tag":
            text = f"[{text}]"
        elif span.style == "link":
            url = _url(span.href)
            text = (f"<{url}>" if _flat(span.text) == _flat(span.href) else f"[{text}]({url})") if url else text
        elif span.style in ("b", "i") and text.strip():
            mark = "**" if span.style == "b" else "*"
            inner = text.strip()
            text = text[: len(text) - len(text.lstrip())] + mark + inner + mark + text[len(text.rstrip()) :]
        parts.append(text)
    line = "".join(parts)
    return line.replace("|", "\\|") if cell else _MD_START.sub(_md_line_start, line)


def _demoted_report(markdown: str, top: int) -> str:
    """The scientist's Markdown with its headings moved down so that the highest one lands on level ``top``.

    Everything else stays as written, except that "<" outside code is escaped so
    that no HTML can come alive when the document is rendered.
    """
    fence = ""
    kinds: list[tuple[str, int, str]] = []  # (kind, heading level, text)
    for line in _clean(markdown).split("\n"):
        opened = _FENCE.match(line)
        # A one-line paragraph followed by === or --- is a heading too ("setext" style).
        underlined = (
            _SETEXT.match(line)
            and kinds
            and kinds[-1][0] == "text"
            and _PLAIN_LINE.match(kinds[-1][2])
            and not (len(kinds) > 1 and kinds[-2][2].strip())
        )
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = ""
            kinds.append(("code", 0, line))
        elif opened:
            fence = opened.group(1)
            kinds.append(("code", 0, line))
        elif atx := _ATX.match(line):
            kinds.append(("heading", len(atx.group(1)), re.sub(r"[ \t]+#+$", "", atx.group(2))))
        elif underlined:
            kinds[-1] = ("heading", 1 if line.lstrip().startswith("=") else 2, kinds[-1][2].strip())
        else:
            kinds.append(("text", 0, line))
    levels = [level for kind, level, _ in kinds if kind == "heading"]
    shift = top - min(levels) if levels else 0
    out = []
    for kind, level, text in kinds:
        if kind == "code":
            out.append(text)
            continue
        text = _CODE_OR_LT.sub(lambda m: "\\<" if m.group(0) == "<" else m.group(0), text)
        if kind == "heading":
            level += shift
            text = f"{'#' * level} {text}" if level <= 6 else f"**{text}**"
        out.append(text)
    return "\n".join(out).strip("\n")


def _md_table(table: _Table) -> list[str]:
    """A table in GitHub-flavoured Markdown."""

    def cell(value: _Cell) -> str:
        if isinstance(value, _Bar):
            value = value.text
        return _md_inline(_spans(value) if isinstance(value, str) else value, cell=True) or " "

    aligns = (table.align + "l" * len(table.head))[: len(table.head)]
    lines = ["| " + " | ".join(cell(h) for h in table.head) + " |"]
    lines.append("| " + " | ".join("--:" if a == "r" else "---" for a in aligns) + " |")
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in table.rows]
    return lines


def _md_passage(block: _Passage) -> list[str]:
    """An example: its header line, the context before the window, the passage in a fenced block."""
    strength, facts, ref = _example_facts(block.example)
    head = " · ".join([f"**{_md(strength)}**"] + [_md(f) for f in facts] + ([_md_code(ref)] if ref else []))
    label, context = _context(block.example)
    lines = [head, ""]
    if context:
        lines += [f"*{label}:*", "", *_md_fence(context), "", "*Passage:*", ""]
    return lines + _md_fence(_clean(block.example.text))


def _to_markdown(blocks: Sequence[_Block], heading: int = 1) -> str:
    """Blocks as Markdown; ``heading`` is the level of the document title."""
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Heading):
            title = " — ".join(part for part in (_md(_flat(block.kicker)), _md(_flat(block.text))) if part)
            title = re.sub(r"#+$", lambda m: "\\#" * len(m.group(0)), title)  # not a closing sequence
            level = heading + block.level
            lines = [f"{'#' * level} {title}" if level <= 6 else f"**{title}**"]
        elif isinstance(block, _Para):
            lines = [_md_inline(block.spans)]
            if block.role == "note":
                lines = [f"*{lines[0].strip()}*"]
            elif block.role == "footer":
                lines = ["---", "", lines[0]]
        elif isinstance(block, _Facts):
            lines = [_md_inline([_Span(" · ".join(phrase for _, _, phrase in block.items))])]
        elif isinstance(block, _Items):
            markers = block.markers or ["-"] * len(block.items)
            lines = [f"{marker} {_md_inline(item)}" for marker, item in zip(markers, block.items, strict=False)]
        elif isinstance(block, _Table):
            lines = _md_table(block)
        elif isinstance(block, _Passage):
            lines = _md_passage(block)
        elif isinstance(block, _Legend):
            lines = [f"*{_md(_LEGEND_TEXT)}*"]
        elif isinstance(block, _Verbatim):
            lines = _md_fence(_clean(block.text), block.info)
        else:
            lines = [_demoted_report(block.markdown, heading + block.level)]
        out += [*lines, ""]
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- the scientist's report, parsed

_REPORT_SPAN = re.compile(
    r"(?P<tick>`+)(?!`)(?P<code>.+?)(?<!`)(?P=tick)(?!`)"
    r"|\*\*(?=\S)(?P<b>.+?)(?<=\S)\*\*"
    r"|(?<![*\w])\*(?=[^\s*])(?P<i>[^*]+?)(?<=[^\s*])\*(?![*\w])"
)
_REPORT_ITEM = re.compile(r"^\s*(\d{1,9}[.)]|[-*+])[ \t]+(.*)$")


def _report_inline(text: str) -> _Inline:
    """Inline Markdown of the report: code, **strong** and *emphasis*; everything else stays literal."""
    spans: _Inline = []
    position = 0
    for match in _REPORT_SPAN.finditer(text):
        if match.start() > position:
            spans.append(_Span(text[position : match.start()]))
        if match.group("code") is not None:
            spans.append(_code(match.group("code")))
        elif match.group("b") is not None:
            spans.append(_b(match.group("b")))
        else:
            spans.append(_Span(match.group("i"), "i"))
        position = match.end()
    if position < len(text):
        spans.append(_Span(text[position:]))
    return spans


def _report_blocks(markdown: str, top: int) -> list[_Block]:
    """The report as blocks for the text and HTML writers (which have no Markdown renderer to lean on).

    Understands what the reports use: headings, paragraphs, bullet and numbered
    lists, fenced code, and the inline marks of :func:`_report_inline`.  Anything
    else is kept as literal text, so nothing is lost.
    """
    blocks: list[_Block] = []
    paragraph: list[str] = []
    items: list[tuple[str, list[str]]] = []
    code: list[str] | None = None
    fence = ""
    lines = _clean(markdown).split("\n")
    levels = [len(m.group(1)) for m in map(_ATX.match, lines) if m]
    shift = top - min(levels) if levels else 0

    def flush() -> None:
        if paragraph:
            blocks.append(_Para(_report_inline(" ".join(paragraph))))
            paragraph.clear()
        if items:
            numbered = all(marker[0].isdigit() for marker, _ in items)
            content = [_report_inline(" ".join(text)) for _, text in items]
            blocks.append(_Items(content, [marker for marker, _ in items] if numbered else None))
            items.clear()

    for line in lines:
        opened = _FENCE.match(line)
        if code is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                blocks.append(_Verbatim("\n".join(code), "text"))
                code = None
            else:
                code.append(line)
            continue
        if opened:
            flush()
            code, fence = [], opened.group(1)
            continue
        heading, item = _ATX.match(line), _REPORT_ITEM.match(line)
        if heading:
            flush()
            title = re.sub(r"[ \t]+#+$", "", heading.group(2))
            blocks.append(_Heading(len(heading.group(1)) + shift, title))
        elif not line.strip():
            flush()
        elif item:
            if paragraph:
                flush()
            items.append((item.group(1), [item.group(2).strip()]))
        elif items:
            items[-1][1].append(line.strip())
        else:
            paragraph.append(line.strip())
    if code is not None:
        blocks.append(_Verbatim("\n".join(code), "text"))
    flush()
    return blocks


# --------------------------------------------------------------------------- plain-text writer


def _plain(spans: _Inline) -> str:
    """Inline spans as plain text."""
    parts = []
    for span in spans:
        if span.style in ("swatch", "ring"):
            continue
        if span.style == "code":
            parts.append(_clean(span.text).replace("\n", "⏎") or "(empty)")
        elif span.style == "tag":
            parts.append(f"[{_flat(span.text)}]")
        else:
            parts.append(_clean(span.text).replace("\n", " "))
    return "".join(parts)


def _wrap(text: str, width: int = _TEXT_WIDTH, first: str = "", rest: str = "") -> list[str]:
    """Prose wrapped to ``width`` columns; long words (URLs, ids) are never broken."""
    return textwrap.wrap(
        _SPACE.sub(" ", text).strip(),
        width,
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [first.rstrip()]


def _text_table(table: _Table) -> list[str]:
    """A table as aligned columns; cells wrap inside their column so that the table fits the page."""

    def cell(value: _Cell) -> str:
        if isinstance(value, _Bar):
            return value.text
        return _SPACE.sub(" ", value if isinstance(value, str) else _plain(value)).strip()

    columns = len(table.head)
    rows = [[cell(c) for c in row[:columns]] + [""] * (columns - len(row)) for row in table.rows]
    aligns = (table.align + "l" * columns)[:columns]
    body = [max([len(row[i]) for row in rows], default=0) for i in range(columns)]
    widths = [max(len(table.head[i]), body[i]) for i in range(columns)]
    # Too wide for the page: first let headers wrap down to their longest word, then narrow the widest column.
    floor = [max([body[i]] + [len(word) for word in table.head[i].split()]) for i in range(columns)]
    budget = _TEXT_WIDTH - 2 * (columns - 1)
    while sum(widths) > budget and widths != floor:
        slack = [widths[i] - floor[i] for i in range(columns)]
        widths[slack.index(max(slack))] -= 1
    while sum(widths) > budget and max(widths) > 8:
        widths[widths.index(max(widths))] -= 1

    def layout(cells: list[str]) -> list[str]:
        wrapped = [textwrap.wrap(c, widths[i]) or [""] for i, c in enumerate(cells)]
        lines = []
        for k in range(max(len(w) for w in wrapped)):
            parts = []
            for i, w in enumerate(wrapped):
                part = w[k] if k < len(w) else ""
                parts.append(part.rjust(widths[i]) if aligns[i] == "r" else part.ljust(widths[i]))
            lines.append("  ".join(parts).rstrip())
        return lines

    laid_out = [layout(row) for row in rows]
    spaced = any(len(lines) > 1 for lines in laid_out)
    out = layout(list(table.head)) + ["  ".join("-" * w for w in widths)]
    for lines in laid_out:
        out += lines + ([""] if spaced else [])
    return out[:-1] if spaced else out


def _text_passage(block: _Passage) -> list[str]:
    """An example in plain text: header, then context and passage indented and otherwise untouched."""
    strength, facts, ref = _example_facts(block.example)
    lines = _wrap(" · ".join([strength, *facts, *([ref] if ref else [])]), first="  ", rest="    ")
    label, context = _context(block.example)

    def quoted(text: str) -> list[str]:
        return ["        " + line if line else "" for line in text.split("\n")]

    if context:
        lines += [f"    {label}:", *quoted(context), "    Passage:"]
    return lines + quoted(_clean(block.example.text))


def _text_heading(block: _Heading) -> list[str]:
    """A heading: the title boxed, sections upper-cased and underlined, deeper levels underlined."""
    title = " — ".join(part for part in (_flat(block.kicker), _flat(block.text)) if part)
    if block.level == 1:
        title = title.upper()
    lines = _wrap(title)
    rule = max(len(line) for line in lines)
    if block.level == 0:
        return ["=" * rule, *lines, "=" * rule]
    if block.level <= 3:
        return ["", *lines, "=-~"[block.level - 1] * rule]
    return lines


def _to_text(blocks: Sequence[_Block]) -> str:
    """Blocks as plain text: no markup, prose wrapped, passages untouched."""
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Heading):
            lines = _text_heading(block)
        elif isinstance(block, _Para):
            lines = _wrap(_plain(block.spans))
            if block.role == "footer":
                lines = ["-" * _TEXT_WIDTH, *lines]
        elif isinstance(block, _Facts):
            lines = _wrap(" · ".join(phrase for _, _, phrase in block.items))
        elif isinstance(block, _Items):
            markers = block.markers or ["-"] * len(block.items)
            lines = []
            for marker, item in zip(markers, block.items, strict=False):
                lines += _wrap(_plain(item), first=f"  {marker} ", rest=" " * (len(marker) + 3))
        elif isinstance(block, _Table):
            lines = _text_table(block)
        elif isinstance(block, _Passage):
            lines = _text_passage(block)
        elif isinstance(block, _Legend):
            lines = _wrap(_LEGEND_TEXT)
        elif isinstance(block, _Verbatim):
            lines = ["    " + line if line else "" for line in _clean(block.text).split("\n")]
        else:
            lines = _to_text(_report_blocks(block.markdown, block.level)).rstrip("\n").split("\n")
        out += [*lines, ""]
    return "\n".join(out).strip("\n") + "\n"


# --------------------------------------------------------------------------- HTML writer

_FOCAL_MARK = re.compile(r"«[^«»]{0,80}»")
_HTML_STYLES = {"b": "<strong>{}</strong>", "i": "<em>{}</em>", "tag": '<span class="tag">{}</span>'}

_CSS = """
:root{--pole:224,86,27;--other:51,105,138;--ink:#1c1b19;--soft:#55524c;--muted:#7a766e;--rule:#dcd8cf;--wash:#f6f4ef;
--serif:Charter,"Bitstream Charter","Iowan Old Style","Sitka Text",Cambria,Georgia,"DejaVu Serif",serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Inter,Arial,sans-serif;
--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"JetBrains Mono","DejaVu Sans Mono","Liberation Mono",monospace}
body.neg{--pole:51,105,138;--other:224,86,27}
*{box-sizing:border-box}
html{font-size:17px;-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:var(--ink);font-family:var(--serif);line-height:1.52;
-webkit-print-color-adjust:exact;print-color-adjust:exact;text-rendering:optimizeLegibility}
main{max-width:47rem;margin:0 auto;padding:1.6rem 1.3rem 4rem}
section{scroll-margin-top:3rem}
nav.bar{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;gap:.2rem 1rem;align-items:center;
padding:.45rem 1.3rem;background:rgba(255,255,255,.94);border-bottom:1px solid var(--rule);
font-family:var(--sans);font-size:.76rem}
nav.bar a{color:var(--soft);text-decoration:none}
nav.bar a:hover{color:var(--ink);text-decoration:underline}
nav.bar button{margin-left:auto;font:inherit;color:var(--soft);background:#fff;border:1px solid var(--rule);
border-radius:4px;padding:.2rem .7rem;cursor:pointer}
nav.bar button:hover{color:var(--ink);border-color:var(--muted)}
header.doc{border-top:4px solid rgb(var(--pole));padding-top:.9rem;margin-bottom:1rem}
.kicker{font-family:var(--sans);font-size:.72rem;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
header.doc .kicker{font-size:.86rem;letter-spacing:.04em;text-transform:none;color:rgb(var(--pole));
font-variant-numeric:lining-nums}
h1{font-size:1.72rem;line-height:1.22;font-weight:600;margin:.25rem 0 0;text-wrap:balance}
h2{font-size:1.32rem;line-height:1.25;font-weight:600;margin:2.8rem 0 .8rem;padding-top:.55rem;border-top:1.5px solid var(--ink)}
h3{font-size:1.06rem;line-height:1.32;font-weight:600;margin:2rem 0 .45rem;text-wrap:balance}
h3 .kicker{display:block;margin-bottom:.1rem;color:rgb(var(--pole))}
h4,h5,h6{font-family:var(--sans);font-size:.84rem;font-weight:600;margin:1.4rem 0 .35rem}
p{margin:.55rem 0}
p.note{font-family:var(--sans);font-size:.76rem;line-height:1.45;color:var(--muted);margin:.4rem 0 .7rem}
p.label{font-family:var(--sans);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
margin:1rem 0 .3rem}
a{color:#1d5a7a}
ul,ol{margin:.45rem 0 .8rem;padding-left:1.25rem}
li{margin:.2rem 0}
section.s2 li{font-size:.9rem;line-height:1.42}
code{font-family:var(--mono);font-size:.84em;background:var(--wash);padding:.05em .3em;border-radius:3px;
white-space:pre-wrap;overflow-wrap:anywhere}
.fx{font-weight:600;box-shadow:inset 0 -.42em 0 rgba(var(--pole),.24)}
.tag{font-family:var(--sans);font-size:.66rem;font-weight:600;letter-spacing:.03em;color:var(--soft);white-space:nowrap;
border:1px solid var(--rule);border-radius:3px;padding:.05em .35em;vertical-align:.08em}
.sw{display:inline-block;width:.62rem;height:.62rem;border-radius:50%;margin-right:.4rem;border:2px solid transparent}
table{border-collapse:collapse;width:100%;margin:.7rem 0 1rem;font-size:.84rem;line-height:1.36;
font-variant-numeric:tabular-nums lining-nums;overflow-wrap:break-word}
th,td{text-align:left;vertical-align:top;padding:.34rem .75rem .34rem 0;border-bottom:1px solid var(--rule)}
th:last-child,td:last-child{padding-right:0}
thead th{font-family:var(--sans);font-size:.68rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
color:var(--muted);border-bottom:1.5px solid var(--ink);vertical-align:bottom}
thead{display:table-header-group}
.r{text-align:right}
td.r{white-space:nowrap}
.meter{display:inline-block;width:4.2rem;height:.5rem;margin-right:.5rem;background:#ebe8e1;border-radius:2px;overflow:hidden}
.meter i{display:block;height:100%;background:rgb(var(--pole))}
table.facts{width:auto;font-size:.82rem;margin:.9rem 0 .8rem}
table.facts th{font-family:var(--sans);font-size:.72rem;font-weight:500;color:var(--muted);white-space:nowrap;padding-right:.8rem}
table.facts td{padding-right:2rem;font-variant-numeric:lining-nums}
table.matrix{width:auto;font-size:.7rem}
table.matrix th,table.matrix td{padding:.2rem .45rem .2rem 0}
aside.legend{font-family:var(--sans);font-size:.76rem;line-height:1.7;color:var(--soft);background:var(--wash);
border-radius:4px;padding:.55rem .8rem;margin:1.1rem 0}
aside.legend .tk,aside.legend .fo,aside.legend .nr,aside.legend .ctx{font-family:var(--mono);font-size:.95em}
figure.ex{margin:.75rem 0 1.05rem;break-inside:avoid;page-break-inside:avoid}
figure.ex figcaption{font-family:var(--sans);font-size:.74rem;color:var(--muted);margin-bottom:.22rem}
figure.ex figcaption b{color:var(--ink);font-weight:600}
figure.ex .ref{float:right;font-family:var(--mono);font-size:.92em}
.psg,pre{font-family:var(--mono);font-size:.74rem;line-height:1.72;white-space:pre-wrap;overflow-wrap:anywhere;
word-break:break-word;tab-size:4;margin:0;background:var(--wash);border-radius:0 3px 3px 0;padding:.5rem .75rem}
.psg{border-left:3px solid rgba(var(--pole),.6);unicode-bidi:isolate}
pre{border-radius:3px;line-height:1.5;margin:.7rem 0 1rem}
.ctx{color:#a29e96}
.tk{border-radius:2px;padding:.1em 0;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.fo{outline:1.5px solid var(--ink);outline-offset:1px;border-radius:1px;font-weight:700}
.nr{color:#6f6b64;text-decoration:underline dotted #c9c5bc;text-underline-offset:.25em}
.ex-own{background:rgba(var(--pole),.72);color:#fff}
.ex-other{background:rgba(var(--other),.42)}
footer{margin-top:3rem;padding-top:.7rem;border-top:1px solid var(--rule);font-family:var(--sans);font-size:.74rem;
color:var(--muted);overflow-wrap:anywhere}
footer a{color:inherit}
@page{size:A4;margin:19mm 18mm 21mm 18mm;font:7.5pt/1.2 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#77736b;
@top-left{content:__RUNNING_TITLE__;vertical-align:bottom;padding-bottom:4mm}
@top-right{content:"Qwen3.6 Neuron Atlas";vertical-align:bottom;padding-bottom:4mm}
@bottom-right{content:counter(page) " / " counter(pages);vertical-align:top;padding-top:5mm}}
@page:first{@top-left{content:""}@top-right{content:""}}
@media print{
html{font-size:10.4pt}
main{max-width:none;padding:0}
.noprint{display:none!important}
header.doc{padding-top:.7rem}
h1,h2,h3,h4,h5,h6,figcaption,p.label,p.note{break-after:avoid;page-break-after:avoid}
h2{margin-top:2rem}
section.brk{break-before:page;page-break-before:always}
section.brk>h2{margin-top:0}
tr,li,aside.legend{break-inside:avoid;page-break-inside:avoid}
p{orphans:3;widows:3}
a{color:inherit;text-decoration:none}
}
"""


def _h(text: Any) -> str:
    """Atlas text as HTML text or attribute value."""
    return html.escape(_clean(text), quote=True)


def _css_string(text: str) -> str:
    """``text`` as a CSS string literal; everything outside plain ASCII letters is hex-escaped."""
    return '"' + "".join(c if c.isascii() and (c.isalnum() or c in " .,:/-+") else f"\\{ord(c):x} " for c in _clean(text)) + '"'


def _html_inline(spans: _Inline) -> str:
    """Inline spans as HTML; «tokens» quoted in prose are highlighted."""
    parts = []
    for span in spans:
        if span.style in ("swatch", "ring"):
            fill = "transparent" if span.style == "ring" else span.text
            parts.append(f'<i class="sw" style="background:{fill};border-color:{span.text}"></i>')
            continue
        if span.style == "code":
            parts.append(f"<code>{html.escape(_clean(span.text).replace(chr(10), '⏎')) or '(empty)'}</code>")
            continue
        text = _FOCAL_MARK.sub(lambda m: f'<span class="fx">{m.group(0)}</span>', _h(_clean(span.text).replace("\n", " ")))
        if span.style == "link":
            url = _url(span.href)
            text = f'<a href="{html.escape(url)}">{text}</a>' if url else text
        elif span.style in _HTML_STYLES:
            text = _HTML_STYLES[span.style].format(text)
        parts.append(text)
    return "".join(parts)


def _html_table(table: _Table) -> str:
    """A table; shares get a bar, numeric columns are right-aligned."""
    aligns = (table.align + "l" * len(table.head))[: len(table.head)]

    def attr(i: int) -> str:
        return ' class="r"' if aligns[i] == "r" else ""

    def cell(value: _Cell) -> str:
        if isinstance(value, _Bar):
            width = max(0.0, min(1.0, value.fraction)) * 100
            return f'<span class="meter"><i style="width:{width:.1f}%"></i></span>{_h(value.text)}'
        return _html_inline(_spans(value) if isinstance(value, str) else value)

    head = "".join(f"<th{attr(i)}>{_h(h)}</th>" for i, h in enumerate(table.head))
    rows = []
    for row in table.rows:
        rows.append("<tr>" + "".join(f"<td{attr(i)}>{cell(c)}</td>" for i, c in enumerate(row[: len(table.head)])) + "</tr>")
    kind = ' class="matrix"' if table.matrix else ""
    return f"<table{kind}><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _html_facts(block: _Facts) -> str:
    """The facts as a compact table, two facts per row."""
    rows = []
    for i in range(0, len(block.items), 2):
        cells = "".join(f"<th>{_h(label)}</th><td>{_h(value)}</td>" for label, value, _ in block.items[i : i + 2])
        rows.append(f"<tr>{cells}</tr>")
    return f'<table class="facts"><tbody>{"".join(rows)}</tbody></table>'


def _html_tokens(block: _Passage, pole: str) -> str:
    """The passage window token by token, shaded like the explorer shades it."""
    example = block.example
    tokens = example.tokens or []
    lo, hi, before, after = _token_window(example)
    parts = ['<span class="ctx">… </span>'] if before else []
    for i in range(lo, hi):
        raw, value = tokens[i]
        if raw.endswith("\r") and i + 1 < len(tokens) and tokens[i + 1][0].startswith("\n"):
            raw = raw[:-1]
        text = _h(raw)
        focal = i == example.focal_index
        if focal:
            text = f"«{text}»"
        elif not text:
            continue
        shade = _shade(value, block.sign, block.vmax, pole)
        classes = [name for name, wanted in (("tk", shade), ("fo", focal), ("nr", value is None and text.strip())) if wanted]
        if not classes:
            parts.append(text)
            continue
        style = f' style="background:{shade[0]}{";color:#fff" if shade[1] else ""}"' if shade else ""
        title = "not routed to this neuron's expert" if value is None else f"{value:+.2f}"
        parts.append(f'<span class="{" ".join(classes)}"{style} title="{title}">{text}</span>')
    if after:
        parts.append('<span class="ctx"> …</span>')
    return "".join(parts)


def _html_passage(block: _Passage, pole: str) -> str:
    """An example as a figure: caption, greyed context, then the passage (shaded when it has tokens)."""
    example = block.example
    strength, facts, ref = _example_facts(example)
    caption = " · ".join([f"<b>{_h(strength)}</b>"] + [_h(f) for f in facts])
    if ref:
        caption = f'<span class="ref">{_h(ref)}</span>' + caption
    _, context = _context(example)
    body = f'<span class="ctx" title="text before the stored window">{_h(context)}</span>' if context else ""
    if example.tokens:
        body += _html_tokens(block, pole)
    else:
        text, span = example.text, _focal_span(example)
        if span:
            body += f'{_h(text[: span[0]])}<span class="fo">{_h(text[span[0] : span[1]])}</span>{_h(text[span[1] :])}'
        else:
            body += _h(text)
    return f'<figure class="ex"><figcaption>{caption}</figcaption><div class="psg">{body}</div></figure>'


def _html_legend(block: _Legend) -> str:
    """How to read the passages: the shading, the box, the dotted and the grey text."""
    own, other = ("negative", "positive") if block.pole == "neg" else ("positive", "negative")
    parts = ["<strong>Reading the examples.</strong> Each passage is raw corpus text."]
    if block.shaded:
        parts.append(
            "Every token is shaded by the neuron's activation on it: "
            f'<span class="tk ex-own">this pole</span> ({own}; darker = stronger), '
            f'<span class="tk ex-other">the opposite sign</span> ({other}). '
            "Shading is scaled within each mode, subcircuit or island."
        )
    parts.append('The boxed <span class="fo">«token»</span> is the firing token.')
    if block.shaded:
        parts.append('<span class="nr">Dotted</span>: the neuron\'s expert did not receive the token (not routed).')
    parts.append('<span class="ctx">…Grey</span> text precedes the stored window.')
    parts.append("Strengths are multiples of the pole's firing threshold θ (“3.3× threshold”).")
    return f'<aside class="legend">{" ".join(parts)}</aside>'


def _html_body(blocks: Sequence[_Block], pole: str) -> str:
    """Blocks as HTML; headings open nested <section> elements so that print rules can address them."""
    out: list[str] = []
    open_levels: list[int] = []

    def close(down_to: int) -> None:
        while open_levels and open_levels[-1] >= down_to:
            out.append("</section>")
            open_levels.pop()

    for block in blocks:
        if isinstance(block, _Heading):
            title = _html_inline([_Span(_flat(block.text))])
            if block.level == 0:
                out.append(f'<header class="doc"><div class="kicker">{_h(_flat(block.kicker))}</div><h1>{title}</h1></header>')
                continue
            kicker = f'<span class="kicker">{_h(_flat(block.kicker))}</span> ' if block.kicker else ""
            close(block.level)
            open_levels.append(block.level)
            classes = f"s{block.level}" + (" brk" if block.new_page else "")
            anchor = f' id="{_h(block.anchor)}"' if block.anchor else ""
            tag = f"h{min(6, block.level + 1)}"
            out.append(f'<section class="{classes}"{anchor}><{tag}>{kicker}{title}</{tag}>')
        elif isinstance(block, _Para):
            if block.role == "footer":
                close(1)
                out.append(f"<footer>{_html_inline(block.spans)}</footer>")
            else:
                role = f' class="{block.role}"' if block.role else ""
                out.append(f"<p{role}>{_html_inline(block.spans)}</p>")
        elif isinstance(block, _Facts):
            out.append(_html_facts(block))
        elif isinstance(block, _Items):
            if block.markers:
                numbers = [re.sub(r"\D", "", marker) or "1" for marker in block.markers]
                numbered = zip(numbers, block.items, strict=False)
                out.append("<ol>" + "".join(f'<li value="{n}">{_html_inline(item)}</li>' for n, item in numbered) + "</ol>")
            else:
                out.append("<ul>" + "".join(f"<li>{_html_inline(item)}</li>" for item in block.items) + "</ul>")
        elif isinstance(block, _Table):
            out.append(_html_table(block))
        elif isinstance(block, _Passage):
            out.append(_html_passage(block, pole))
        elif isinstance(block, _Legend):
            out.append(_html_legend(block))
        elif isinstance(block, _Verbatim):
            out.append(f"<pre>{_h(block.text)}</pre>")
        else:
            out.append(_html_body(_report_blocks(block.markdown, block.level), pole))
    close(1)
    return "\n".join(out)


# --------------------------------------------------------------------------- public: one pole


def pole_markdown(detail: PoleDetail, heading: int = 1) -> str:
    """Everything known about one pole as a Markdown document.

    ``heading`` is the level of the title; sections nest below it, so documents
    can be put one after another inside a larger export.
    """
    return _to_markdown(_pole_blocks(detail), max(1, heading))


def pole_text(detail: PoleDetail) -> str:
    """The same document as plain text (no markup; prose wrapped to 100 columns, passages untouched)."""
    return _to_text(_pole_blocks(detail))


def pole_html(detail: PoleDetail) -> str:
    """The same document as a standalone HTML page that prints on A4 (no scripts or external files needed)."""
    blocks = _pole_blocks(detail)
    p = detail.pole
    title = " — ".join(part for part in (_flat(p.display), _pole_title(p)) if part)
    sections = [(b.anchor, b.text) for b in blocks if isinstance(b, _Heading) and b.level == 1 and b.anchor]
    links = "".join(f'<a href="#{_h(anchor)}">{_h(text)}</a>' for anchor, text in sections)
    running = _css_string(_short(title, 110))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_h(title)}</title>\n"
        f'<meta name="generator" content="{_h(_RENDERER_NAME)}">\n'
        f"<style>{_CSS.replace('__RUNNING_TITLE__', running)}</style>\n</head>\n"
        f'<body class="{"neg" if p.pole == "neg" else "pos"}">\n'
        f'<nav class="bar noprint">{links}<button type="button" onclick="print()">Print</button></nav>\n'
        f"<main>\n{_html_body(blocks, 'neg' if p.pole == 'neg' else 'pos')}\n</main>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- public: neurons, hits, searches


def _notes(notes: Iterable[Any]) -> list[_Block]:
    items = [_spans(_flat(note)) for note in notes if _flat(note)]
    return [_Heading(1, "Notes"), _Items(items)] if items else []


def _match_item(match: Any, lead: str = "") -> _Inline:
    """One matching text: (whose it is,) where it was found, then the text."""
    field = _flat(match.field).replace("_", " ")
    where = [field] if match.mode_index is None else [f"mode {match.mode_index}", "label" if field == "mode" else field]
    if match.share is not None:
        where.append(f"{_pct(match.share)} of firings")
    if match.ref:
        where.append(_flat(match.ref))
    spans = _spans(_b(lead), " ") if lead else []
    return spans + _spans(_Span(" · ".join(where), "i"), ": " + _flat(match.text))


def _hit_blocks(hit: PoleHit, level: int) -> list[_Block]:
    """A compact section for one pole as it comes out of a search."""
    blocks: list[_Block] = [_Heading(level, _pole_title(hit), kicker=_flat(hit.display)), _pole_facts(hit)]
    if _flat(hit.description) and _flat(hit.description) != _pole_title(hit):
        blocks += _prose(hit.description)
    if hit.modes:
        blocks.append(_Para([_b("Modes")], "label"))
        items = []
        for m in hit.modes:
            share = f" - {_pct(m.share)} of firings" if m.share is not None else ""
            items.append(_spans(f"{m.index}: {_flat(m.label)}{share}"))
        blocks.append(_Items(items))
    if hit.matches:
        blocks.append(_Para([_b("Matching texts")], "label"))
        blocks.append(_Items([_match_item(match) for match in hit.matches]))
    if _url(hit.atlas_url):
        blocks.append(_original_link(hit))
    if hit.available:
        mirrored = ", ".join(_flat(name) for name in hit.mirrored) or "none"
        blocks.append(_note(f"Data sets: {', '.join(_flat(name) for name in hit.available)}; mirrored here: {mirrored}."))
    return blocks


def hit_markdown(hit: PoleHit, heading: int = 2) -> str:
    """One search hit as a compact Markdown section: id, category, description, modes with shares, matches."""
    return _to_markdown(_hit_blocks(hit, 0), max(1, heading))


def neuron_markdown(detail: NeuronDetail) -> str:
    """A neuron as Markdown: both poles as consecutive sections, then its circuit and notes."""
    listed = "listed in the atlas" if detail.in_atlas_index else "not listed in the atlas (known from circuits only)"
    where = _where(detail.layer, detail.expert, detail.neuron)
    blocks: list[_Block] = [
        _Heading(0, _flat(detail.display) or _flat(detail.did), kicker="Neuron"),
        _para("id ", _code(detail.did), f" · {where} · {listed}"),
    ]
    blocks += _prose(detail.circuit_label, "Circuit label.")
    rows: list[list[_Cell]] = []
    for key, value in _dict(detail.circuit_summary).items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        if value not in (None, "", [], {}):
            rows.append([_flat(key).replace("_", " "), _flat(value)])
    if rows:
        blocks.append(_Table(["Circuit summary", "Value"], rows, "ll"))
    if not detail.poles:
        blocks.append(_para("No pole of this neuron has been read in this release."))
    for hit in detail.poles:
        blocks += _hit_blocks(hit, 1)
    blocks += _circuit_blocks(detail.circuit, _str(_dict(detail.circuit).get("pole")) or "pos")
    blocks += _notes(detail.notes)
    blocks.append(_footer(next((hit.atlas_url for hit in detail.poles if hit.atlas_url), None)))
    return _to_markdown(blocks)


def _pole_link(display: str, url: str | None) -> _Span:
    """The human form of a pole id, linked to its page on goodfire.com when it has one."""
    return _Span(_flat(display), "link", url) if url and _url(url) else _b(_flat(display))


def search_markdown(response: SearchResponse) -> str:
    """A search result page as Markdown: the query as understood, the total, the hits, notes."""
    blocks: list[_Block] = [_Heading(0, "Search results", kicker="Neuron Atlas")]
    query = [_code(f"{key}={json.dumps(value, ensure_ascii=False, default=str)}") for key, value in response.query.items()]
    blocks.append(_Para(_spans(_b("Query as understood:"), " ") + (_joined(query, " ") or _spans("(everything)"))))
    shown = len(response.hits) + len(response.neurons) + len(response.modes)
    paging = f"{_plural(response.total, response.group_by)} found"
    if shown:
        paging += f"; showing {response.offset + 1:,} to {response.offset + shown:,}"
    if response.next_offset is not None:
        paging += f" (next page: offset={response.next_offset})"
    blocks.append(_para(paging + "."))

    matched: list[_Inline] = []
    rows: list[list[_Cell]] = []
    for rank, hit in enumerate(response.hits, response.offset + 1):
        rows.append(
            [
                str(rank),
                [_pole_link(hit.display, hit.atlas_url)],
                [_code(hit.pid)],
                _flat(hit.category_words),
                _count(hit.n_modes),
                _short(hit.headline or hit.description, 220),
                _num(hit.score, 4),
            ]
        )
        matched += [_match_item(match, _flat(hit.display)) for match in hit.matches]
    if rows:
        blocks.append(_Table(["#", "Pole", "Id", "Category", "Modes", "Description", "Score"], rows, "llllrlr"))

    for rank, neuron in enumerate(response.neurons, response.offset + 1):
        blocks.append(_Heading(1, _flat(neuron.display), kicker=str(rank)))
        blocks.append(_para("id ", _code(neuron.did), f" · layer {neuron.layer} · score {_num(neuron.score, 4)}"))
        blocks += _prose(neuron.circuit_label, "Circuit label.")
        for hit in neuron.poles:
            facts = _flat(hit.category_words) + (f", {_plural(hit.n_modes, 'mode')}" if hit.n_modes is not None else "")
            text = _short(hit.headline or hit.description, 220)
            blocks.append(_para(_pole_link(hit.display, hit.atlas_url), f" ({facts}): {text}"))
            if hit.matches:
                blocks.append(_Items([_match_item(match) for match in hit.matches]))

    rows = []
    for rank, mode in enumerate(response.modes, response.offset + 1):
        rows.append(
            [
                str(rank),
                [_pole_link(mode.display, mode.atlas_url)],
                [_code(mode.pid)],
                str(mode.mode_index),
                _flat(mode.label),
                _pct(mode.share),
                _num(mode.score, 4),
            ]
        )
        matched += [_match_item(match, _flat(mode.display)) for match in mode.matches]
    if rows:
        blocks.append(_Table(["#", "Pole", "Id", "Mode", "Label", "Share of firings", "Score"], rows, "lllrlrr"))

    if matched:
        blocks += [_Heading(1, "Matching texts"), _Items(matched)]
    if response.facets:
        blocks.append(_Heading(1, "Counts over all results"))
        items = []
        for name, counts in response.facets.items():
            listed = ", ".join(f"{_flat(key)}: {_count(n)}" for key, n in _dict(counts).items())
            items.append(_spans(_b(_flat(name) + ":"), " " + listed))
        blocks.append(_Items(items))
    blocks += _notes(response.notes)
    blocks.append(_footer(None))
    return _to_markdown(blocks)


def similar_markdown(response: SimilarResponse) -> str:
    """Poles or modes that are close in meaning to an anchor, as a Markdown table."""
    anchor = {"pole": "the pole", "mode": "the mode", "text": "the text"}[response.by]
    blocks: list[_Block] = [
        _Heading(0, f"Similar to {anchor} “{_short(response.anchor, 120)}”", kicker="Neuron Atlas"),
        _note(f"Similarity of meaning (cosine; 1 = same meaning). Embedding: {_flat(response.method)}."),
    ]
    with_modes = any(item.mode_index is not None for item in response.items)
    rows: list[list[_Cell]] = []
    for rank, item in enumerate(response.items, 1):
        row: list[_Cell] = [str(rank), [_pole_link(item.display, item.atlas_url)], [_code(item.pid)]]
        if with_modes:
            row.append("–" if item.mode_index is None else str(item.mode_index))
        row += [_short(item.label or item.description, 220), _flat(item.category), str(item.layer), f"{item.score:.3f}"]
        rows.append(row)
    if rows:
        head = ["#", "Pole", "Id"] + (["Mode"] if with_modes else []) + ["Description", "Category", "Layer", "Similarity"]
        blocks.append(_Table(head, rows, "lll" + ("r" if with_modes else "") + "llrr"))
    else:
        blocks.append(_para("Nothing similar was found."))
    blocks += _notes(response.notes)
    blocks.append(_footer(None))
    return _to_markdown(blocks)


# --------------------------------------------------------------------------- public: CSV

_CSV_COLUMNS = (
    "pid", "did", "pole", "layer", "expert", "neuron", "category", "category_words", "verdict", "n_modes",
    "n_subcircuits", "main_damage_share", "simulatability", "in_public_sample", "headline", "description",
    "top_mode_label", "top_mode_share", "modes", "atlas_url",
)  # fmt: skip
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> str:
    """One cell.  Text that a spreadsheet would run as a formula is defused with a leading apostrophe.

    Numbers and booleans are written by this module, not taken from atlas text,
    so a negative number stays a number.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = _clean(value)
    return "'" + text if str(value).startswith(_FORMULA_START) or text.startswith(_FORMULA_START) else text


def _csv_line(values: Iterable[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([_csv_cell(v) for v in values])
    return buffer.getvalue()


def csv_header() -> str:
    """The header line of the pole table (RFC 4180, CRLF-terminated)."""
    return _csv_line(_CSV_COLUMNS)


def csv_row(hit: PoleHit) -> str:
    """One pole as a CSV line matching :func:`csv_header`; shares are fractions (0..1), not per cent."""
    modes = hit.modes or []
    top = max(modes, key=lambda m: m.share if m.share is not None else -1.0, default=None)
    listed = " | ".join(_flat(m.label) + (f" ({_pct(m.share)})" if m.share is not None else "") for m in modes)
    return _csv_line(
        [
            hit.pid, hit.did, hit.pole, hit.layer, hit.expert, hit.neuron, hit.category, hit.category_words,
            hit.verdict, hit.n_modes, hit.n_subcircuits, hit.main_damage_share, hit.simulatability,
            hit.in_public_sample, hit.headline, hit.description, top.label if top else None,
            top.share if top else None, listed, hit.atlas_url,
        ]  # fmt: skip
    )
