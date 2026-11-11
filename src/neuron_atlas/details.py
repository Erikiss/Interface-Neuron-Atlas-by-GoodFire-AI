"""Per-pole and per-neuron details, normalised from the raw atlas files.

The explorer spreads what it knows about a pole over several large files (one
per tab).  :class:`Details` reads the ones a caller asks for - from the mirror,
or once from upstream - and returns one coherent document with our own field
names.  The raw files stay available untouched through :meth:`Details.raw`.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Iterator
from typing import Any

from .catalog import Catalog, PoleEntry
from .models import (
    CircuitSides,
    Damage,
    Example,
    Island,
    IslandMode,
    IslandsView,
    Mode,
    NeuronDetail,
    NeuronRef,
    PoleDetail,
    StrengthProfile,
    Subcircuit,
)
from .search import SCOPE_DEEP, SCOPE_EXAMPLES, SearchIndex, TextField, pole_hit
from .upstream import AtlasError, AtlasSource, NotMirrored, neuron_path, pole_path
from .views import circuit_view, landscape_view, overview_texts, overview_view

ALL_INCLUDES = ("modes", "subcircuits", "examples", "islands", "overview", "landscape", "circuit", "tokens", "research")
DEFAULT_INCLUDES = ("modes", "examples")

#: Labels goodfire.com shows for the two special kinds of mode.
CLOSED_GATE_LABEL = "Q3/Q4 firings (gate input negative)"
RESIDUE_LABEL = "Unassigned examples"

#: The modes view shows a subcircuit's 12 examples in three groups of four.
_SUBCIRCUIT_GROUPS = ("strongest", "typical", "weakest")
#: Order that yields one example of each group first.
_SUBCIRCUIT_ORDER = (0, 4, 8, 1, 5, 9, 2, 6, 10, 3, 7, 11)

_ISLAND_LABELS = {"top": "strongest", "median": "typical", "random": "random", "band": None}
_FIRING_CHIP = re.compile(r"^firing\s+([\d.]+)%")
_NARROW = 25
_END = "<|im_end|>"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def mark_focal(tokens: Iterable[Any], focal: int | None, lo: int = 0, hi: int | None = None) -> str:
    """Join ``[text, activation]`` tokens into a passage, the firing token wrapped in « »."""
    toks = list(tokens)
    hi = len(toks) if hi is None else hi
    parts: list[str] = []
    for i in range(lo, hi):
        item = toks[i]
        text = item[0] if isinstance(item, (list, tuple)) else item
        if not isinstance(text, str):
            continue
        if i == focal:
            # Byte-fragment tokens decode to "": still show where the neuron fired.
            parts.append(f"«{text}»")
        elif text:
            parts.append(text)
    return "".join(parts)


def content_end(tokens: list[Any], focal: int | None) -> int:
    """Index after the last token worth printing.

    Windows that run past the end of a document are padded with ``<|im_end|>``;
    one such marker is kept (it says the document ended), the repeats are not.
    """
    end = len(tokens)
    floor = (focal + 1) if focal is not None else 0
    while end - 1 > floor and tokens[end - 1][0] == _END and tokens[end - 2][0] == _END:
        end -= 1
    return end


def island_window(tokens: list[Any], focal: int | None, narrow: int = _NARROW) -> tuple[int, int]:
    """The explorer's collapsed window: about ``narrow`` non-blank tokens either side of the peak."""
    n = len(tokens)
    if focal is None or not 0 <= focal < n:
        return 0, n
    lo, hi = 0, n
    count = 0
    for i in range(focal, -1, -1):
        if count >= narrow:
            break
        if str(tokens[i][0]).strip():
            count += 1
        lo = i
    count = 0
    for i in range(focal, n):
        if count > narrow:
            break
        if str(tokens[i][0]).strip():
            count += 1
        hi = i + 1
    return lo, hi


def _token_pairs(tokens: list[Any]) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for item in tokens:
        if isinstance(item, (list, tuple)) and item:
            out.append((str(item[0]), _num(item[1]) if len(item) > 1 else None))
        else:
            out.append((str(item), None))
    return out


def _damage(raw: Any) -> Damage | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return Damage(per_firing=_num(raw.get("per_firing")), ratio=_num(raw.get("ratio")), n_measured=_int(raw.get("n_measured")))


def island_threshold(raw: Any) -> float | None:
    """The θ that an islands file measures ``a_over_theta`` against: ``|act| / a_over_theta``.

    It is one value per neuron (the circuit data's θ), not the pole's θ of the mode data; the file
    does not store it, so it is recovered from the examples (median, robust to rounding).
    """
    values = []
    for isl in (raw.get("islands") if isinstance(raw, dict) else None) or []:
        for e in list(isl.get("examples") or []) + list(isl.get("examples_more") or []):
            act, rel = _num(e.get("act")), _num(e.get("a_over_theta"))
            if act and rel and rel > 0:
                values.append(abs(act) / rel)
    return statistics.median(values) if values else None


def island_example(raw: dict[str, Any], island_id: int, slot: int, with_tokens: bool, full: bool = False) -> Example:
    """One example of an island; ``slot`` is its position in the file (primary sample, then the "more" ones)."""
    tokens = raw.get("tokens") or []
    focal = _int(raw.get("focal_idx"))
    lo, hi = (0, len(tokens)) if full else island_window(tokens, focal)
    text = ("… " if lo > 0 else "") + mark_focal(tokens, focal, lo, hi) + (" …" if hi < len(tokens) else "")
    return Example(
        source="islands",
        ref=f"island{island_id}:{slot}",
        group=_ISLAND_LABELS.get(str(raw.get("label"))),
        act=_num(raw.get("act")),
        a_rel=_num(raw.get("a_over_theta")),
        text=text,
        focal_token=str(tokens[focal][0]) if focal is not None and 0 <= focal < len(tokens) else None,
        focal_index=focal,
        tokens=_token_pairs(tokens) if with_tokens else None,
        token_unit="raw",
        window=(lo, hi),
    )


def islands_view(raw: dict[str, Any], max_examples: int = 0, with_tokens: bool = False) -> IslandsView:
    """The island read of a pole: its islands (with up to ``max_examples`` examples each) and island modes.

    Island modes come in file order; grey modes and islands no mode names are merged into one
    unassigned group at the end, as goodfire.com shows them.
    """
    view = IslandsView(theta=island_threshold(raw))
    known: set[int] = set()
    for isl in raw.get("islands") or []:
        iid = _int(isl.get("id"))
        if iid is None:
            continue
        known.add(iid)
        examples: list[Example] = []
        if max_examples:
            # As on the page: the primary sample by strength, then the "more" examples in file order.
            # Refs keep the file position (as in the search index), whatever the display order.
            primary = list(enumerate(isl.get("examples") or []))
            primary.sort(key=lambda item: -abs(_num(item[1].get("act")) or 0.0))
            more = [(len(primary) + i, e) for i, e in enumerate(isl.get("examples_more") or [])]
            examples = [island_example(e, iid, slot, with_tokens) for slot, e in (primary + more)[:max_examples]]
        view.islands.append(
            Island(
                id=iid,
                number=iid + 1,
                n=_int(isl.get("n")),
                name=_text(isl.get("name")),
                description=_text(isl.get("description")),
                examples=examples,
            )
        )
    used: set[int] = set()
    unassigned = IslandMode(name=RESIDUE_LABEL, unassigned=True)
    any_grey = False
    for m in (raw.get("opus") or {}).get("modes") or []:
        ids = [i for i in (m.get("island_ids") or []) if i in known]
        share = None
        for chip in m.get("chips") or []:
            found = _FIRING_CHIP.match(str(chip))
            if found:
                share = float(found.group(1)) / 100.0
                break
        if m.get("grey"):
            any_grey = True
            unassigned.islands.extend(i for i in ids if i not in used and i not in unassigned.islands)
            if share is not None:
                unassigned.share = (unassigned.share or 0.0) + share
            continue
        used.update(ids)
        view.modes.append(
            IslandMode(name=_text(m.get("name")), description=_text(m.get("predicate")), share=share, islands=ids)
        )
    leftover = [i for i in sorted(known) if i not in used and i not in unassigned.islands]
    unassigned.islands.extend(leftover)
    if any_grey or unassigned.islands:
        view.modes.append(unassigned)
    return view


class Details:
    """Builds :class:`PoleDetail` / :class:`NeuronDetail` documents and feeds the deep search index."""

    def __init__(self, source: AtlasSource, catalog: Catalog, search: SearchIndex | None = None):
        self.source = source
        self.catalog = catalog
        self.search = search

    # ------------------------------------------------------------------ loading

    def _load(self, name: str, relpath: str, exists: bool, sources: dict[str, str], remote: bool | None) -> Any:
        """Read one file and record in ``sources`` what happened."""
        if not exists:
            sources[name] = "absent"
            return None
        try:
            data = self.source.get_json(relpath, remote=remote)
        except NotMirrored:
            sources[name] = "not_mirrored"
            return None
        except AtlasError as exc:
            sources[name] = f"error: {exc}"
            return None
        sources[name] = "used" if data is not None else "absent"
        return data

    def raw(self, kind: str, ident: str, *, remote: bool | None = None) -> Any:
        """The untouched upstream JSON of one file.

        ``kind`` is a per-pole data set (``subcircuit``, ``overview``, ``landscape``,
        ``islands``; ``ident`` = pole id) or a per-neuron one (``circuit_meta``,
        ``circuit_exemplars``, ``circuit_incidence``; ``ident`` = neuron id).
        """
        if kind in ("subcircuit", "overview", "landscape", "islands"):
            return self.source.get_json(pole_path(kind, ident), remote=remote)
        if kind in ("circuit_meta", "circuit_exemplars", "circuit_incidence"):
            return self.source.get_json(neuron_path(kind, ident), remote=remote)
        raise ValueError(f"unknown data set {kind!r}")

    def _header(self, p: PoleEntry) -> Any:
        from .mirror import mirrored_sources

        mirrored = mirrored_sources(self.catalog, self.source, [p.pid]).get(p.pid, [])
        return pole_hit(p, mirrored=mirrored)

    # ------------------------------------------------------------------ pole

    def pole(
        self,
        pid: str,
        include: Iterable[str] = DEFAULT_INCLUDES,
        *,
        max_examples: int = 3,
        remote: bool | None = None,
    ) -> PoleDetail:
        p = self.catalog.pole(pid)
        if p is None:
            raise KeyError(pid)
        inc = set(include)
        unknown = inc - set(ALL_INCLUDES)
        if unknown:
            raise ValueError(f"unknown include value(s): {', '.join(sorted(unknown))}")
        with_tokens = "tokens" in inc
        with_examples = "examples" in inc and max_examples > 0
        detail = PoleDetail(pole=self._header(p))
        sources = detail.sources  # filled in as files are read

        wants_sub = bool(inc & {"modes", "subcircuits", "examples", "research"})
        sub = None
        if wants_sub and (p.has_subcircuit or p.subcircuit_empty):
            sub = self._load("subcircuit", pole_path("subcircuit", pid), True, sources, remote)
        elif p.has_subcircuit or p.subcircuit_empty:
            sources["subcircuit"] = "not_requested"
        else:
            sources["subcircuit"] = "absent"

        if isinstance(sub, dict):
            self._fill_subcircuit(detail, sub, inc, max_examples if with_examples else 0, with_tokens)
        elif "modes" in inc and p.modes:
            # The index still knows labels and shares even when the file is not at hand.
            detail.modes = [
                Mode(index=m.index, label=m.label, public_label=m.label, kind="open", share=m.share) for m in p.modes
            ]
            if sources.get("subcircuit") == "not_mirrored":
                detail.notes.append(
                    "Mode descriptions and examples need subcircuit_modes_data, which is not mirrored; "
                    "only labels and shares from the index are shown."
                )

        # Poles without a usable subcircuit read are shown through their islands on goodfire.com.
        wants_islands = "islands" in inc or (bool(inc & {"modes", "examples"}) and not p.has_subcircuit)
        if wants_islands:
            isl = self._load("islands", pole_path("islands", pid), p.has_islands, sources, remote)
            if isinstance(isl, dict):
                detail.islands = islands_view(isl, max_examples if with_examples else 0, with_tokens)
                theta = island_threshold(isl)
                shown = any(i.examples for i in detail.islands.islands)
                if shown and theta and detail.theta and abs(theta - detail.theta) > 0.02 * detail.theta:
                    detail.notes.append(
                        f"Island examples give a_rel in multiples of the islands data's own threshold (θ ≈ {theta:.4g}, "
                        f"one per neuron), not of the pole's θ = {detail.theta:.4g} that the mode examples use; "
                        "compare their raw act instead."
                    )
        else:
            sources["islands"] = "not_requested" if p.has_islands else "absent"

        if "overview" in inc:
            ov = self._load("overview", pole_path("overview", pid), p.has_overview, sources, remote)
            if isinstance(ov, dict):
                detail.overview = overview_view(ov, research="research" in inc)
        else:
            sources["overview"] = "not_requested" if p.has_overview else "absent"

        if "landscape" in inc:
            ls = self._load("landscape", pole_path("landscape", pid), p.has_landscape, sources, remote)
            if isinstance(ls, dict):
                detail.landscape = landscape_view(ls)
                # Only open modes exist in both index spaces; they are matched by their label.
                by_label = {m.label: m.index for m in p.modes}
                for mode in detail.landscape["modes"]:
                    mode["pole_mode_index"] = by_label.get(mode["label"]) if mode["kind"] == "open" else None
        else:
            sources["landscape"] = "not_requested" if p.has_landscape else "absent"

        if "circuit" in inc:
            meta = self._load("circuit", neuron_path("circuit_meta", p.did), p.has_circuit, sources, remote)
            if isinstance(meta, dict):
                detail.circuit = circuit_view(meta, p.pole, self.catalog)
        else:
            sources["circuit"] = "not_requested" if p.has_circuit else "absent"

        return detail

    # ------------------------------------------------------------------ subcircuit modes

    def _subcircuit_example(self, raw: dict[str, Any], sid: str, slot: int, with_tokens: bool) -> Example:
        tokens = raw.get("tokens") or []
        focal = _int(raw.get("focal"))
        focal_token = None
        if focal is not None and 0 <= focal < len(tokens):
            focal_token = str(tokens[focal][0])
        fold = raw.get("fold")
        end = content_end(tokens, focal)
        return Example(
            source="subcircuit",
            ref=f"{sid}:{slot}",
            group=_SUBCIRCUIT_GROUPS[min(slot // 4, 2)],
            act=_num(raw.get("a")),
            a_rel=_num(raw.get("a_rel")),
            closed_gate=bool(raw.get("closed")) if raw.get("closed") is not None else None,
            held_out=(fold == "audit") if fold is not None else None,
            repeats=_int(raw.get("repeats")),
            text=mark_focal(tokens, focal, 0, end),
            pre=_text(raw.get("pre")),
            focal_token=focal_token,
            focal_index=focal,
            tokens=_token_pairs(tokens[:end]) if with_tokens else None,
            token_unit="theta",
            window=(0, end),
        )

    def _subcircuit_examples(self, raw: dict[str, Any], sid: str, limit: int, with_tokens: bool) -> list[Example]:
        exemplars = raw.get("exemplars") or []
        order = [i for i in _SUBCIRCUIT_ORDER if i < len(exemplars)] + list(range(12, len(exemplars)))
        return [self._subcircuit_example(exemplars[i], sid, i, with_tokens) for i in order[:limit]]

    def _refs(self, raw: Any, role: str) -> list[NeuronRef]:
        out = []
        for item in raw or []:
            if not isinstance(item, dict) or not item.get("unit"):
                continue
            unit = str(item["unit"])
            out.append(
                NeuronRef(
                    unit=unit,
                    label=_text(item.get("label")),
                    weight=_num(item.get("leaf")),
                    rest=_num(item.get("rest")),
                    role=role,
                    in_atlas=unit in self.catalog.neurons,
                )
            )
        return out

    def _fill_subcircuit(
        self, detail: PoleDetail, sub: dict[str, Any], inc: set[str], max_examples: int, with_tokens: bool
    ) -> None:
        detail.theta = _num(sub.get("theta"))
        detail.n_firings = _int(sub.get("n_firings"))
        detail.closed_gate_share = _num(sub.get("closed_share"))
        detail.auto_label = _text(sub.get("label"))
        detail.summary = _text(sub.get("summary"))
        detail.unified_description = _text(sub.get("unified_description"))
        raw_subs: dict[str, Any] = sub.get("subcircuits") or {}
        mode_of: dict[str, int] = {}

        modes: list[Mode] = []
        for index, m in enumerate(sub.get("modes") or []):
            sids = [str(s) for s in m.get("subcircuits") or []]
            for sid in sids:
                mode_of.setdefault(sid, index)
            kind = "closed_gate" if m.get("closed_gate") else ("residue" if m.get("residue") else "open")
            label = _text(m.get("label"))
            mode = Mode(
                index=index,
                label=label,
                public_label={"closed_gate": CLOSED_GATE_LABEL, "residue": RESIDUE_LABEL}.get(kind, label),
                kind=kind,  # type: ignore[arg-type]
                description=_text(m.get("description")),
                share=_num(m.get("share")),
                n_firings=_int(m.get("n_firings")),
                subcircuits=sids,
                damage=_damage(m.get("kl")),
            )
            if max_examples:
                mode.examples = self._mode_examples(raw_subs, sids, max_examples, with_tokens)
            modes.append(mode)
        if "modes" in inc or "examples" in inc:
            detail.modes = modes

        if "subcircuits" in inc:
            subs: list[Subcircuit] = []
            ordered = sorted(raw_subs.items(), key=lambda kv: -(_num(kv[1].get("share")) or 0.0))
            for sid, s in ordered:
                prof = s.get("profile") if isinstance(s.get("profile"), dict) else None
                circ = s.get("circuit") if isinstance(s.get("circuit"), dict) else None
                item = Subcircuit(
                    id=str(sid),
                    mode_index=mode_of.get(str(sid)),
                    n=_int(s.get("n")),
                    share=_num(s.get("share")),
                    meaning=_text(s.get("meaning")),
                    token_pattern=_text(s.get("token_pattern")),
                    readable=s.get("readable") if isinstance(s.get("readable"), bool) else None,
                    reading_confidence=_int(s.get("reading_confidence")),
                    function_label=_text(s.get("function_label")),
                    function=_text(s.get("function")),
                    gate_role=_text(s.get("gate_role")),
                    up_role=_text(s.get("up_role")),
                    function_confidence=_int(s.get("function_confidence")),
                    profile=StrengthProfile(**{k: _num(prof.get(k)) for k in ("ge_theta", "mid", "weak", "closed")})
                    if prof
                    else None,
                    median_a_rel=_num(s.get("median_a_rel")),
                    damage=_damage(s.get("kl")),
                    circuit=CircuitSides(
                        gate_drivers=self._refs(circ.get("gate_drivers"), "gate_driver"),
                        gate_opposers=self._refs(circ.get("gate_opposers"), "gate_opposer"),
                        up_drivers=self._refs(circ.get("up_drivers"), "up_driver"),
                        up_opposers=self._refs(circ.get("up_opposers"), "up_opposer"),
                        readers=self._refs(circ.get("readers"), "reader"),
                    )
                    if circ
                    else None,
                    examples=self._subcircuit_examples(s, str(sid), max_examples, with_tokens) if max_examples else [],
                )
                if "research" in inc:
                    item.research = {
                        k: s.get(k)
                        for k in ("unique_contexts", "top_context_share", "spec_flags", "circuit_desc", "circtext_desc")
                        if s.get(k) is not None
                    }
                subs.append(item)
            detail.subcircuits = subs

        if "research" in inc:
            keys = (
                "verdict", "fn_mono", "main_damage_share", "kl_neuron_mean", "partition", "judge",
                "verdict_before_unify", "modes_before_unify", "review_verdict", "verdict_source", "sign",
            )  # fmt: skip
            detail.research = {k: sub.get(k) for k in keys if sub.get(k) is not None}

    def _mode_examples(
        self, raw_subs: dict[str, Any], sids: list[str], limit: int, with_tokens: bool
    ) -> list[Example]:
        """Strongest examples of a mode, taken in turn from its subcircuits (largest first)."""
        ordered = sorted(
            (sid for sid in sids if sid in raw_subs), key=lambda sid: -(_num(raw_subs[sid].get("share")) or 0.0)
        )
        out: list[Example] = []
        for slot in range(12):
            for sid in ordered:
                exemplars = raw_subs[sid].get("exemplars") or []
                if slot < len(exemplars):
                    out.append(self._subcircuit_example(exemplars[slot], sid, slot, with_tokens))
                    if len(out) >= limit:
                        return out
        return out

    # ------------------------------------------------------------------ neuron

    def neuron(
        self, did: str, include: Iterable[str] = (), *, remote: bool | None = None
    ) -> NeuronDetail:
        n = self.catalog.neuron(did)
        if n is None:
            raise KeyError(did)
        inc = set(include)
        detail = NeuronDetail(
            did=n.did,
            display=n.display,
            layer=n.layer,
            expert=n.expert,
            neuron=n.neuron,
            in_atlas_index=n.in_atlas_index,
            circuit_label=n.circuit_label,
            circuit_summary={k: v for k, v in (n.circuit or {}).items() if k not in ("did", "label")} or None,
            poles=[self._header(p) for p in self.catalog.poles_of(did)],
        )
        if "circuit" in inc:
            if n.circuit is None:
                detail.notes.append("This neuron has no circuit data.")
            else:
                try:
                    meta = self.source.get_json(neuron_path("circuit_meta", did), remote=remote)
                except NotMirrored:
                    meta = None
                    detail.notes.append("Circuit data is not mirrored and remote fetching is disabled.")
                if isinstance(meta, dict):
                    detail.circuits = {pole: circuit_view(meta, pole, self.catalog) for pole in ("pos", "neg")}
        return detail

    # ------------------------------------------------------------------ deep search texts

    def text_fields(self, kind: str, raw: Any) -> Iterator[TextField]:
        """Searchable texts of one raw per-pole file."""
        if not isinstance(raw, dict):
            return
        if kind == "subcircuit":
            for key in ("summary", "unified_description", "label"):
                if _text(raw.get(key)):
                    yield TextField("summary", raw[key])
            # Texts are attributed to the pole's readable modes only; the Q3/Q4 and residue groups
            # are not modes of the pole's mode list (their subcircuit texts stay searchable).
            mode_of: dict[str, int] = {}
            for index, m in enumerate(raw.get("modes") or []):
                if m.get("residue"):
                    continue
                for sid in m.get("subcircuits") or []:
                    mode_of.setdefault(str(sid), index)
                if _text(m.get("description")):
                    yield TextField("mode_description", m["description"], mode_index=index)
            for sid, s in (raw.get("subcircuits") or {}).items():
                mode_index = mode_of.get(str(sid))
                for key in ("function_label", "meaning", "token_pattern", "function", "gate_role", "up_role"):
                    if _text(s.get(key)):
                        yield TextField("subcircuit", s[key], mode_index=mode_index, ref=str(sid))
                for slot, e in enumerate(s.get("exemplars") or []):
                    toks, focal = e.get("tokens") or [], _int(e.get("focal"))
                    text = (_text(e.get("pre")) + mark_focal(toks, focal, 0, content_end(toks, focal))).strip()
                    if text:
                        yield TextField("example", text, mode_index=mode_index, ref=f"{sid}:{slot}", scope=SCOPE_EXAMPLES)
        elif kind == "islands":
            for m in (raw.get("opus") or {}).get("modes") or []:
                if m.get("grey"):
                    continue
                for key in ("name", "predicate"):
                    if _text(m.get(key)):
                        yield TextField("island", m[key])
            for isl in raw.get("islands") or []:
                iid = _int(isl.get("id"))
                ref = f"island{iid}" if iid is not None else None
                for key in ("name", "description"):
                    if _text(isl.get(key)):
                        yield TextField("island", isl[key], ref=ref)
                # Example refs number the file order (primary sample, then the "more" examples), as the pole detail does.
                pool = list(isl.get("examples") or []) + list(isl.get("examples_more") or [])
                for slot, e in enumerate(pool):
                    text = mark_focal(e.get("tokens") or [], _int(e.get("focal_idx"))).strip()
                    if text:
                        yield TextField("example", text, ref=f"{ref}:{slot}" if ref else None, scope=SCOPE_EXAMPLES)
        elif kind == "overview":
            yield from overview_texts(raw)

    def index_deep(self, pid: str, *, examples: bool = False) -> int:
        """Add the texts of every mirrored file of ``pid`` to the search index.  Returns texts added."""
        if self.search is None or pid not in self.catalog.poles:
            return 0
        added = 0
        state = self.search.deep_state(pid)
        for kind in ("subcircuit", "islands", "overview"):
            relpath = pole_path(kind, pid)
            info = self.source.store.info(relpath)
            if info.status != "present":
                continue
            # Never drop example passages that were indexed before (e.g. by a mirror run with examples).
            with_examples = examples or state.get(kind, "").endswith(":ex")
            sha = info.sha256 + (":ex" if with_examples else "")
            if state.get(kind) == sha:
                continue
            raw = self.source.get_json(relpath, remote=False)
            fields = [f for f in self.text_fields(kind, raw) if with_examples or f.scope == SCOPE_DEEP]
            added += self.search.index_deep(pid, kind, sha, fields)
        return added
