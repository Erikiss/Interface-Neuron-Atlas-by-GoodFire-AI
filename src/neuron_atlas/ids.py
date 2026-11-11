"""Neuron and pole identifiers.

The atlas names a neuron by layer / expert / neuron index, ``L30_E47_N449``
(displayed as ``L30/E47/N449``).  A few units are not routed through an expert
and read ``L4_shared_N126``.  Every neuron has two *poles*; a pole id is the
neuron id plus ``__pos`` or ``__neg``.

People and chatbots write these ids in many ways, so parsing is lenient and
formatting is strict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

POLES = ("pos", "neg")

_POLE_WORDS = {
    "pos": "pos", "+": "pos", "positive": "pos", "p": "pos", "plus": "pos",
    "neg": "neg", "-": "neg", "−": "neg", "–": "neg", "negative": "neg", "n": "neg", "minus": "neg",
}  # fmt: skip

_SEP = r"[\s_/.,:;|-]*"
_DID = re.compile(
    rf"L(?:ayer)?{_SEP}(?P<L>\d+){_SEP}"
    rf"(?:E(?:xpert)?{_SEP}(?P<E>\d+)|(?P<shared>shared)){_SEP}"
    rf"N(?:euron)?{_SEP}(?P<N>\d+)",
    re.IGNORECASE,
)
_BARE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*$")
_CANON_DID = re.compile(r"^L\d+_(?:E\d+|shared)_N\d+$")
_CANON_PID = re.compile(r"^L\d+_(?:E\d+|shared)_N\d+__(?:pos|neg)$")
_POLE_SUFFIX = re.compile(r"(?:__|[\s_/.,:;|-]+)(pos|neg|positive|negative)\s*(?:pole)?\s*$", re.IGNORECASE)
_POLE_PREFIX = re.compile(r"^\s*([+\-−–])\s*")
_POLE_TAIL_SIGN = re.compile(r"\s*([+\-−–])\s*$")


@dataclass(frozen=True, order=True)
class NeuronId:
    layer: int
    expert: int | None  # None = shared (non-expert) unit
    neuron: int

    @property
    def did(self) -> str:
        expert = "shared" if self.expert is None else f"E{self.expert}"
        return f"L{self.layer}_{expert}_N{self.neuron}"

    @property
    def display(self) -> str:
        return self.did.replace("_", "/")

    def pid(self, pole: str) -> str:
        return f"{self.did}__{normalise_pole(pole)}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.did


def normalise_pole(pole: str) -> str:
    """``+``, ``pos``, ``positive`` -> ``pos``; ``-``, ``neg``, ``negative`` -> ``neg``."""
    key = str(pole).strip().lower()
    try:
        return _POLE_WORDS[key]
    except KeyError:
        raise ValueError(f"unknown pole {pole!r} (use 'pos' or 'neg')") from None


def pole_sign(pole: str) -> str:
    return "+" if normalise_pole(pole) == "pos" else "-"


def is_did(text: str) -> bool:
    return bool(_CANON_DID.match(text))


def is_pid(text: str) -> bool:
    return bool(_CANON_PID.match(text))


def split_pid(pid: str) -> tuple[str, str]:
    """``L30_E47_N449__pos`` -> (``L30_E47_N449``, ``pos``).  Strict."""
    if not is_pid(pid):
        raise ValueError(f"not a pole id: {pid!r}")
    did, pole = pid.rsplit("__", 1)
    return did, pole


def make_pid(did: str, pole: str) -> str:
    return f"{did}__{normalise_pole(pole)}"


def other_pole(pid: str) -> str:
    did, pole = split_pid(pid)
    return make_pid(did, "neg" if pole == "pos" else "pos")


def display_did(did: str) -> str:
    return did.replace("_", "/")


def display_pid(pid: str) -> str:
    did, pole = split_pid(pid)
    return f"{'+' if pole == 'pos' else '−'} {display_did(did)}"


def parse_reference(text: str) -> tuple[NeuronId, str | None] | None:
    """Parse whatever a person might type for a neuron or a pole.

    Accepts ``L30_E47_N449``, ``L30/E47/N449``, ``l30 e47 n449``, ``30/47/449``,
    ``layer 30 expert 47 neuron 449``, ``L4_shared_N126`` and, for the pole,
    a leading or trailing sign or a ``__pos`` / ``neg`` suffix.  Returns the
    neuron id and the pole (``None`` when no pole was given), or ``None`` when
    the text is not an id at all.
    """
    if not text:
        return None
    work = str(text).strip()
    pole: str | None = None

    m = _POLE_SUFFIX.search(work)
    if m:
        pole = normalise_pole(m.group(1))
        work = work[: m.start()].rstrip()
    else:
        m = _POLE_PREFIX.match(work)
        if m and len(work) > m.end():
            pole = normalise_pole(m.group(1))
            work = work[m.end() :]
        else:
            m = _POLE_TAIL_SIGN.search(work)
            if m and m.start() > 0:
                pole = normalise_pole(m.group(1))
                work = work[: m.start()]

    m = _DID.fullmatch(work.strip())
    if m:
        expert = None if m.group("shared") else int(m.group("E"))
        return NeuronId(int(m.group("L")), expert, int(m.group("N"))), pole
    m = _BARE.match(work)
    if m:
        return NeuronId(int(m.group(1)), int(m.group(2)), int(m.group(3))), pole
    return None


def parse_did(text: str) -> str | None:
    """Canonical neuron id for ``text`` or ``None``."""
    ref = parse_reference(text)
    return ref[0].did if ref else None


def parse_pid(text: str, default_pole: str | None = None) -> str | None:
    """Canonical pole id for ``text``; ``default_pole`` fills in a missing pole."""
    ref = parse_reference(text)
    if not ref:
        return None
    nid, pole = ref
    pole = pole or (normalise_pole(default_pole) if default_pole else None)
    return nid.pid(pole) if pole else None


def did_parts(did: str) -> NeuronId:
    """Strict parse of a canonical neuron id."""
    if not is_did(did):
        raise ValueError(f"not a neuron id: {did!r}")
    layer, expert, neuron = did.split("_")
    return NeuronId(int(layer[1:]), None if expert == "shared" else int(expert[1:]), int(neuron[1:]))
