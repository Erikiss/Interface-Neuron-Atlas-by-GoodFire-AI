"""The catalog: every neuron and pole of a release, built from the global index files.

Seven small JSON files describe the whole published atlas.  The explorer on
goodfire.com merges four of them in its ``boot()`` function; this module does
the same merge (same category rules, same seeded sample) and adds the three
indices the explorer's tabs load on their own, so that every pole carries all
facts that are known *without* fetching its large per-pole files.

Terminology used by the atlas (and kept here):

``pole``
    One sign of a neuron's activation.  ``pos`` and ``neg`` are read separately.
``mode``
    One distinct thing a pole fires on; a group of subcircuits with one meaning.
``category``
    ``mono`` single mode · ``fn`` one dominant mode (the main mode accounts for
    at least 90 % of the damage when the neuron is switched off) · ``poly``
    multiple modes · ``dark`` uninterpretable · ``none`` not yet read.
``simulatability``
    Share of the neuron's effect on the model's predictions (KL) that a
    simulator recovers from the pole's description on held-out text.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .config import ATLAS_PAGE_URL
from .ids import did_parts, display_did, is_did, make_pid, normalise_pole
from .upstream import AtlasSource

CATEGORIES = ("mono", "fn", "poly", "dark", "none")

CATEGORY_WORDS = {
    "mono": "single mode",
    "fn": "one dominant mode",
    "poly": "multiple modes",
    "dark": "uninterpretable",
    "none": "not yet read",
}

CATEGORY_HELP = {
    "mono": "Everything this pole fires on fits one specific meaning.",
    "fn": "This pole has more than one mode, but the main one accounts for at least 90% of the damage "
    "when the neuron is switched off.",
    "poly": "This pole fires on two or more different, unrelated things.",
    "dark": "No clear pattern was found in what this pole fires on.",
    "none": "This pole has not been read yet.",
}

#: Layers the explorer hides behind its default "Layers 9–36" filter.
OUTER_LAYERS = frozenset({4, 39})

# Constants of the published explorer shell (public_neuron_atlas-sample-3000-v2.html).
DEFAULT_SAMPLE_SEED = 20261109
DEFAULT_SAMPLE_SIZE = 3000
DEFAULT_NOT_SAMPLED_NEURONS = frozenset({"L27_E247_N96", "L35_E88_N30"})
DEFAULT_NOT_SAMPLED_EXPERTS = frozenset({(14, 78)})

_U32 = 0xFFFFFFFF


def sample_hash(text: str, seed: int = DEFAULT_SAMPLE_SEED) -> int:
    """The explorer's ``rhash``: seeded FNV-1a over UTF-16 code units plus a final mix.

    The public page orders its "random" list by this hash of the pole id and
    shows the first ``SAMPLE_SIZE`` poles, so reproducing it tells us exactly
    which poles a visitor of goodfire.com can reach.
    """
    h = (2166136261 ^ seed) & _U32
    data = text.encode("utf-16-le", "surrogatepass")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 16777619) & _U32
    h ^= h >> 15
    h = (h * 2246822507) & _U32
    h ^= h >> 13
    return h & _U32


@dataclass(frozen=True)
class ModeBrief:
    """A mode as listed in the global subcircuit index."""

    index: int
    label: str
    #: Share of the pole's firings that belong to this mode (0..1).
    share: float | None


@dataclass
class PoleEntry:
    pid: str
    did: str
    pole: str  # "pos" | "neg"
    layer: int
    expert: int | None
    neuron: int

    #: Category after the explorer's merge rules (see module docstring).
    category: str = "none"
    #: The summary the explorer shows: mode labels joined by " · ", or the island description.
    description: str = ""
    #: Category / description from the older island read (kept when a subcircuit read replaced them).
    island_category: str | None = None
    island_description: str = ""
    #: Short name of the pole in the island index.
    island_name: str = ""
    #: Plain-language headline of the Overview tab, when one was published.
    headline: str = ""

    verdict: str | None = None  # "monosemantic" | "polysemantic"
    fn_mono: bool | None = None
    #: Share of the damage (KL when the neuron is switched off) carried by the main mode.
    main_damage_share: float | None = None
    n_modes: int | None = None
    n_residue: int | None = None
    n_subcircuits: int | None = None
    modes: list[ModeBrief] = field(default_factory=list)

    simulatability: float | None = None
    #: Model that wrote the Overview explanation ("opus" / "glm").
    scientist_model: str | None = None

    has_subcircuit: bool = False
    #: A subcircuit read exists but produced no modes (failed read; the island view is used).
    subcircuit_empty: bool = False
    has_overview: bool = False
    has_landscape: bool = False
    has_islands: bool = False
    has_circuit: bool = False

    #: Is this pole in the 3,000-pole sample the public explorer lists?
    in_public_sample: bool = False
    #: Position in the explorer's fixed "random" order over all candidate poles.
    sample_rank: int = 0

    @property
    def sign(self) -> str:
        return "+" if self.pole == "pos" else "-"

    @property
    def display(self) -> str:
        return f"{'+' if self.pole == 'pos' else '−'} {display_did(self.did)}"

    @property
    def category_words(self) -> str:
        return CATEGORY_WORDS[self.category]

    @property
    def top_mode_share(self) -> float | None:
        shares = [m.share for m in self.modes if m.share is not None]
        return max(shares) if shares else None

    def atlas_url(self, tab: str | None = None, mode: int | None = None) -> str | None:
        """Deep link into the original explorer (only sampled poles can be opened there)."""
        if not self.in_public_sample:
            return None
        url = f"{ATLAS_PAGE_URL}?n={self.did}&pole={self.pole}"
        if tab:
            url += f"&tab={tab}"
        if mode is not None:
            url += f"&mode={mode}"
        return url

    def search_text(self) -> str:
        """Everything index-level that describes this pole, for text matching."""
        parts = [self.description, self.headline, self.island_name]
        if self.island_description and self.island_description != self.description:
            parts.append(self.island_description)
        return "\n".join(p for p in parts if p)


@dataclass
class NeuronEntry:
    did: str
    layer: int
    expert: int | None
    neuron: int
    poles: dict[str, PoleEntry] = field(default_factory=dict)
    #: Listed in ``public_atlas_data/index.json`` (the atlas proper).
    in_atlas_index: bool = False
    #: Entry of the circuit explorer's index (automatic label, activation range, …) or ``None``.
    circuit: dict[str, Any] | None = None

    @property
    def display(self) -> str:
        return display_did(self.did)

    @property
    def circuit_label(self) -> str:
        return str((self.circuit or {}).get("label") or "")

    @property
    def in_public_sample(self) -> bool:
        return any(p.in_public_sample for p in self.poles.values())


@dataclass(frozen=True)
class ShellConstants:
    """Values the published explorer hard-codes; they define the public sample."""

    seed: int = DEFAULT_SAMPLE_SEED
    sample_size: int = DEFAULT_SAMPLE_SIZE
    not_sampled_neurons: frozenset[str] = DEFAULT_NOT_SAMPLED_NEURONS
    not_sampled_experts: frozenset[tuple[int, int]] = DEFAULT_NOT_SAMPLED_EXPERTS

    @classmethod
    def from_shell_html(cls, html: str) -> ShellConstants:
        """Read the constants out of the explorer page; unknown parts keep their defaults."""
        seed = re.search(r"\bRSEED\s*=\s*(\d+)", html)
        size = re.search(r"\bSAMPLE_SIZE\s*=\s*(\d+)", html)
        skip = re.search(r"\bNOT_SAMPLE\s*=\s*new Set\(\[([^\]]*)\]\)", html)
        neurons = DEFAULT_NOT_SAMPLED_NEURONS
        if skip:
            neurons = frozenset(d for d in re.findall(r'"([^"]+)"', skip.group(1)) if is_did(d))
        experts = frozenset(
            (int(a), int(b)) for a, b in re.findall(r"n\.L===(\d+)&&n\.E===(\d+)", html)
        ) or DEFAULT_NOT_SAMPLED_EXPERTS
        return cls(
            seed=int(seed.group(1)) if seed else DEFAULT_SAMPLE_SEED,
            sample_size=int(size.group(1)) if size else DEFAULT_SAMPLE_SIZE,
            not_sampled_neurons=neurons,
            not_sampled_experts=experts,
        )


class Catalog:
    """All neurons and poles of one release, with the lookups the rest of the package needs."""

    def __init__(
        self,
        neurons: dict[str, NeuronEntry],
        *,
        release_url: str = "",
        built_at: dict[str, str] | None = None,
        model: str = "",
        shell: ShellConstants | None = None,
    ):
        self.neurons = neurons
        self.poles: dict[str, PoleEntry] = {
            p.pid: p for n in neurons.values() for p in n.poles.values()
        }
        self.release_url = release_url
        self.built_at = built_at or {}
        self.model = model
        self.shell = shell or ShellConstants()

    # ------------------------------------------------------------------ lookups

    def __len__(self) -> int:
        return len(self.poles)

    def pole(self, pid: str) -> PoleEntry | None:
        return self.poles.get(pid)

    def neuron(self, did: str) -> NeuronEntry | None:
        return self.neurons.get(did)

    def iter_poles(self) -> Iterator[PoleEntry]:
        return iter(self.poles.values())

    def poles_of(self, did: str) -> list[PoleEntry]:
        n = self.neurons.get(did)
        return [n.poles[k] for k in ("pos", "neg") if k in n.poles] if n else []

    def layers(self) -> list[int]:
        return sorted({p.layer for p in self.poles.values()})

    def stats(self) -> dict[str, Any]:
        poles = list(self.poles.values())
        return {
            "model": self.model,
            "release_url": self.release_url,
            "built_at": self.built_at,
            "neurons": len(self.neurons),
            "neurons_in_atlas_index": sum(n.in_atlas_index for n in self.neurons.values()),
            "neurons_with_circuit": sum(n.circuit is not None for n in self.neurons.values()),
            "poles": len(poles),
            "poles_in_public_sample": sum(p.in_public_sample for p in poles),
            "poles_with_subcircuit_read": sum(p.has_subcircuit for p in poles),
            "poles_with_overview": sum(p.has_overview for p in poles),
            "poles_with_landscape": sum(p.has_landscape for p in poles),
            "poles_with_islands": sum(p.has_islands for p in poles),
            "poles_with_simulatability": sum(p.simulatability is not None for p in poles),
            "modes": sum(len(p.modes) for p in poles),
            "categories": dict(sorted(Counter(p.category for p in poles).items())),
            "layers": dict(sorted(Counter(p.layer for p in poles).items())),
            "public_sample": {
                "seed": self.shell.seed,
                "size": self.shell.sample_size,
                "excluded_neurons": sorted(self.shell.not_sampled_neurons),
                "excluded_experts": [f"L{a}/E{b}" for a, b in sorted(self.shell.not_sampled_experts)],
            },
        }

    # ------------------------------------------------------------------ building

    @classmethod
    def load(cls, source: AtlasSource, *, remote: bool | None = None) -> Catalog:
        """Fetch (or read from the mirror) the index files and build the catalog.

        Only ``public_atlas_data/index.json`` is required; like the explorer,
        the catalog degrades gracefully when one of the others is missing.
        """

        def optional(name: str) -> Any:
            try:
                return source.get_index(name, remote=remote)
            except Exception:  # noqa: BLE001 - any failure just means "not available"
                return None

        atlas = source.get_index("atlas", remote=remote)
        if not atlas or "neurons" not in atlas:
            raise ValueError("public_atlas_data/index.json is missing or has no 'neurons'")
        return cls.build(
            atlas=atlas,
            subcircuit=optional("subcircuit"),
            overview=optional("overview"),
            landscape=optional("landscape"),
            islands=optional("islands"),
            circuit=optional("circuit"),
            headlines=optional("headlines"),
            release_url=source.release_url,
        )

    @classmethod
    def build(
        cls,
        *,
        atlas: dict[str, Any],
        subcircuit: dict[str, Any] | None = None,
        overview: dict[str, Any] | None = None,
        landscape: Any = None,
        islands: dict[str, Any] | None = None,
        circuit: dict[str, Any] | None = None,
        headlines: dict[str, Any] | None = None,
        release_url: str = "",
        shell: ShellConstants | None = None,
    ) -> Catalog:
        shell = shell or ShellConstants()
        sc_poles: dict[str, Any] = (subcircuit or {}).get("poles") or {}
        ov_pids = set((overview or {}).get("pids") or [])
        ov_sim: dict[str, Any] = (overview or {}).get("sim") or {}
        ov_model: dict[str, Any] = (overview or {}).get("model") or {}
        if isinstance(landscape, dict):
            ls_pids = set(landscape.get("pids") or [])
        else:
            ls_pids = set(landscape or [])
        island_names = {
            str(p.get("pid")): str(p.get("name") or "") for p in (islands or {}).get("poles") or []
        }
        headlines = headlines or {}
        circuit_by_did = {str(n.get("did")): n for n in (circuit or {}).get("neurons") or []}

        neurons: dict[str, NeuronEntry] = {}
        for raw in atlas.get("neurons") or []:
            did = str(raw.get("did") or "")
            if not is_did(did):
                continue
            nid = did_parts(did)
            entry = NeuronEntry(did, nid.layer, nid.expert, nid.neuron, in_atlas_index=True)
            for sign, raw_pole in (raw.get("poles") or {}).items():
                if not raw_pole:
                    continue
                pole = normalise_pole(sign)
                pid = str(raw_pole.get("pid") or make_pid(did, pole))
                p = PoleEntry(pid, did, pole, nid.layer, nid.expert, nid.neuron)
                p.category = raw_pole.get("cat") if raw_pole.get("cat") in CATEGORIES else "none"
                p.description = str(raw_pole.get("desc") or "")
                p.has_islands = bool(raw_pole.get("islands"))
                p.island_name = island_names.get(pid, "")
                _apply_subcircuit(p, sc_poles.get(pid))
                p.has_overview = pid in ov_pids
                p.has_landscape = pid in ls_pids
                sim = ov_sim.get(pid)
                p.simulatability = float(sim) if isinstance(sim, (int, float)) else None
                p.scientist_model = ov_model.get(pid)
                head = headlines.get(pid)
                p.headline = head if isinstance(head, str) else ""
                entry.poles[pole] = p
            neurons[did] = entry

        # Neurons the circuit explorer knows: attach, and add those the atlas index lacks.
        for did, raw in circuit_by_did.items():
            if not is_did(did):
                continue
            entry = neurons.get(did)
            if entry is None:
                nid = did_parts(did)
                entry = neurons[did] = NeuronEntry(did, nid.layer, nid.expert, nid.neuron)
            entry.circuit = raw
            for p in entry.poles.values():
                p.has_circuit = True

        _mark_public_sample(neurons, shell)
        built_at = {}
        if subcircuit and subcircuit.get("built_at"):
            built_at["subcircuit_modes"] = str(subcircuit["built_at"])
        if circuit and circuit.get("built_at"):
            built_at["circuit_explorer"] = str(circuit["built_at"])
        model = str((circuit or {}).get("model") or "Qwen3.6-35B-A3B")
        ordered = dict(sorted(neurons.items(), key=lambda kv: _neuron_key(kv[1])))
        return cls(ordered, release_url=release_url, built_at=built_at, model=model, shell=shell)


def _neuron_key(n: NeuronEntry) -> tuple[int, int, int]:
    return (n.layer, -1 if n.expert is None else n.expert, n.neuron)


def _apply_subcircuit(p: PoleEntry, read: dict[str, Any] | None) -> None:
    """The explorer's ``applySC``: a subcircuit read replaces island category and description."""
    if not read:
        return
    p.verdict = read.get("verdict")
    p.fn_mono = bool(read.get("fn_mono")) if read.get("fn_mono") is not None else None
    mds = read.get("main_damage_share")
    p.main_damage_share = float(mds) if isinstance(mds, (int, float)) else None
    p.n_modes = read.get("n_modes")
    p.n_residue = read.get("n_residue")
    p.n_subcircuits = read.get("n_subcircuits")
    if not read.get("n_modes"):
        # A read without modes is a failed read: island category and view stay.
        p.subcircuit_empty = True
        return
    p.has_subcircuit = True
    p.modes = [
        ModeBrief(i, str(m.get("label") or ""), float(m["share"]) if isinstance(m.get("share"), (int, float)) else None)
        for i, m in enumerate(read.get("modes") or [])
    ]
    p.island_category = p.category
    p.island_description = p.description
    p.category = "mono" if read.get("verdict") == "monosemantic" else ("fn" if read.get("fn_mono") else "poly")
    p.description = " · ".join(m.label for m in p.modes)


def _mark_public_sample(neurons: dict[str, NeuronEntry], shell: ShellConstants) -> None:
    candidates: list[PoleEntry] = []
    for n in neurons.values():
        if not n.in_atlas_index or n.did in shell.not_sampled_neurons:
            continue
        if n.expert is not None and (n.layer, n.expert) in shell.not_sampled_experts:
            continue
        candidates.extend(n.poles.values())
    # JavaScript's localeCompare on these ASCII ids agrees with plain ordering for the tie-break.
    candidates.sort(key=lambda p: (sample_hash(p.pid, shell.seed), p.pid))
    for rank, p in enumerate(candidates):
        p.sample_rank = rank
        p.in_public_sample = rank < shell.sample_size
    ranked = {p.pid for p in candidates}
    extra = len(candidates)
    for n in neurons.values():
        for p in n.poles.values():
            if p.pid not in ranked:
                p.sample_rank = extra + sample_hash(p.pid, shell.seed)


class CatalogHolder:
    """Lazily built, thread-safe catalog shared by the server's worker threads."""

    def __init__(self, source: AtlasSource):
        self.source = source
        self._catalog: Catalog | None = None
        self._lock = threading.Lock()

    def get(self) -> Catalog:
        if self._catalog is None:
            with self._lock:
                if self._catalog is None:
                    self._catalog = Catalog.load(self.source)
        return self._catalog

    def reload(self) -> Catalog:
        with self._lock:
            self._catalog = Catalog.load(self.source)
            return self._catalog


def resolve_poles(catalog: Catalog, refs: Iterable[str]) -> list[str]:
    """Turn free-form references into pole ids; a neuron without a pole yields both poles."""
    from .ids import parse_reference

    out: list[str] = []
    for ref in refs:
        parsed = parse_reference(ref)
        if not parsed:
            continue
        nid, pole = parsed
        if pole:
            out.append(nid.pid(pole))
        else:
            known = catalog.poles_of(nid.did)
            out.extend(p.pid for p in known) if known else out.extend([nid.pid("pos"), nid.pid("neg")])
    return list(dict.fromkeys(out))
