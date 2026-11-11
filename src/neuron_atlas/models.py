"""Request and response shapes shared by the REST API, the MCP tools and the CLI.

These are the *stable* contract of the interface.  Raw upstream JSON keeps
Goodfire's field names and is available untouched through the ``raw``
endpoints; everything here is our own, normalised vocabulary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["mono", "fn", "poly", "dark", "none"]
Pole = Literal["pos", "neg"]
MatchMode = Literal["auto", "words", "substring", "regex", "semantic"]
SortKey = Literal[
    "relevance", "simulatability", "layer", "modes", "damage", "share", "random", "id"
]
GroupBy = Literal["pole", "neuron", "mode"]
SampleScope = Literal["all", "public", "hidden"]
Feature = Literal[
    "subcircuit", "overview", "landscape", "islands", "circuit", "simulatability", "headline", "mirrored"
]
TextScope = Literal["labels", "deep", "examples"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- search


class SearchQuery(_Model):
    """One search over the atlas.

    ``q`` is free text.  It may also carry filters inline, which is convenient
    for chatbots and for the single search box of the web page::

        braille layer:30 cat:fn
        "severe pain" pole:neg modes:>=3
        L30/E47/N449            (a neuron or pole id opens it directly)
        hash -base64            (must contain "hash", must not contain "base64")
        tokeniz* OR detokeniz*  (prefix and OR)

    Inline keys: ``layer`` ``expert`` ``neuron`` ``pole`` ``cat`` ``verdict``
    ``modes`` ``sim`` ``damage`` ``has`` ``sample`` ``in`` (text scope).
    Numeric keys accept ``5``, ``>=5``, ``<0.3`` and ranges ``9..36``.
    """

    q: str = Field("", description="Free text, an id, and/or inline filters.", max_length=2000)
    match: MatchMode = Field(
        "auto",
        description=(
            "auto: word search with prefix matching, falling back to substring for terms the word "
            "index cannot find · words: word search only · substring: every term must occur as a "
            "plain substring (what goodfire.com does) · regex: q is a regular expression · "
            "semantic: rank by meaning (latent semantic similarity) instead of exact words"
        ),
    )
    scope: list[TextScope] = Field(
        default_factory=lambda: ["labels", "deep"],
        description=(
            "Which texts to search. labels: pole summaries, mode labels, headlines (always available) · "
            "deep: mode/subcircuit/island descriptions, explanations and reports of mirrored poles · "
            "examples: the example passages of mirrored poles"
        ),
    )

    layers: list[int] | None = Field(None, description="Only these layers.")
    layer_min: int | None = None
    layer_max: int | None = None
    experts: list[int] | None = Field(None, description="Only these expert indices.")
    neurons: list[int] | None = Field(None, description="Only these neuron indices within the expert.")
    pole: Pole | None = Field(None, description="Only positive or only negative poles.")
    categories: list[Category] | None = Field(
        None, description="mono, fn (one dominant mode), poly, dark (uninterpretable), none (not yet read)."
    )
    verdict: Literal["monosemantic", "polysemantic"] | None = None
    has: list[Feature] | None = Field(None, description="Only poles that have all of these.")
    lacks: list[Feature] | None = Field(None, description="Only poles that have none of these.")
    modes_min: int | None = Field(None, ge=0)
    modes_max: int | None = Field(None, ge=0)
    simulatability_min: float | None = None
    simulatability_max: float | None = None
    damage_min: float | None = Field(None, description="Lower bound on main_damage_share (0..1).")
    damage_max: float | None = None
    sample: SampleScope = Field(
        "all",
        description="all: every pole of the release · public: the 3,000 poles goodfire.com lists · hidden: the rest",
    )
    ids: list[str] | None = Field(None, description="Restrict to these neuron or pole ids (any spelling).")

    sort: SortKey = "relevance"
    order: Literal["asc", "desc"] | None = Field(
        None, description="Default: desc for relevance/simulatability/modes/damage/share, asc otherwise."
    )
    group_by: GroupBy = Field(
        "pole",
        description="pole: one hit per pole · neuron: both poles of a neuron folded into one hit · mode: one hit per matching mode",
    )
    limit: int = Field(25, ge=0, le=5000)
    offset: int = Field(0, ge=0)
    facets: bool = Field(False, description="Also count the full result set by layer, category and pole.")
    snippets: bool = Field(True, description="Include the matching texts of each hit.")


class Match(_Model):
    """One text of a pole that matched the query."""

    field: str = Field(description="Kind of text: description, mode, headline, island, subcircuit, overview, example, …")
    text: str
    mode_index: int | None = Field(None, description="Index of the mode (as in the pole's mode list) the text belongs to.")
    share: float | None = Field(None, description="Share of the pole's firings in that mode (0..1).")
    ref: str | None = Field(None, description="Subcircuit, island or example id inside the pole.")
    score: float = 0.0


class ModeOut(_Model):
    index: int
    label: str
    share: float | None = None


class PoleHit(_Model):
    pid: str
    did: str
    pole: Pole
    display: str = Field(
        description="Human form of the id, e.g. '+ L30/E47/N449'; the negative pole is written with a minus sign (U+2212)."
    )
    layer: int
    expert: int | None
    neuron: int
    category: Category
    category_words: str
    description: str
    headline: str = ""
    n_modes: int | None = None
    n_subcircuits: int | None = None
    verdict: str | None = None
    main_damage_share: float | None = None
    simulatability: float | None = None
    in_public_sample: bool = False
    available: list[str] = Field(default_factory=list, description="Data sets that exist for this pole.")
    mirrored: list[str] = Field(default_factory=list, description="Data sets already in the local mirror.")
    score: float = 0.0
    matches: list[Match] = Field(default_factory=list)
    modes: list[ModeOut] | None = None
    atlas_url: str | None = Field(None, description="Deep link into the original explorer on goodfire.com.")


class NeuronHit(_Model):
    did: str
    display: str
    layer: int
    expert: int | None
    neuron: int
    circuit_label: str = ""
    score: float = 0.0
    poles: list[PoleHit] = Field(default_factory=list)


class ModeHit(_Model):
    pid: str
    did: str
    pole: Pole
    display: str
    layer: int
    category: Category
    mode_index: int
    label: str
    share: float | None = None
    score: float = 0.0
    matches: list[Match] = Field(default_factory=list)
    atlas_url: str | None = None


class SearchResponse(_Model):
    query: dict[str, Any] = Field(description="The query as understood, inline filters resolved.")
    total: int = Field(description="Number of hits before paging.")
    offset: int
    limit: int
    next_offset: int | None = Field(None, description="Pass as offset for the next page; null on the last page.")
    group_by: GroupBy
    hits: list[PoleHit] = Field(default_factory=list)
    neurons: list[NeuronHit] = Field(default_factory=list)
    modes: list[ModeHit] = Field(default_factory=list)
    facets: dict[str, dict[str, int]] | None = None
    notes: list[str] = Field(default_factory=list, description="Things worth knowing about how the query was run.")
    took_ms: float = 0.0


class BatchSearchRequest(_Model):
    queries: list[SearchQuery] = Field(min_length=1)


class BatchSearchResponse(_Model):
    results: list[SearchResponse]
    took_ms: float = 0.0


# --------------------------------------------------------------------------- details

Include = Literal[
    "modes", "subcircuits", "examples", "islands", "overview", "landscape", "circuit", "tokens", "research"
]


class Example(_Model):
    """One passage the pole fires on, as plain text with the firing token marked «like this»."""

    source: str = Field(description="Data set the example comes from: subcircuit or islands.")
    ref: str | None = Field(None, description="Subcircuit / island / example id inside the pole.")
    group: str | None = Field(None, description="Sampling role: strongest, typical, weakest, random, …")
    act: float | None = Field(None, description="Peak activation of the neuron in this passage (signed).")
    a_rel: float | None = Field(None, description="Peak activation divided by the pole's firing threshold θ.")
    closed_gate: bool | None = Field(
        None, description="True when the neuron's gate input was negative here (a weak 'leak' firing, Q3/Q4)."
    )
    held_out: bool | None = Field(None, description="True when the example was not used to write the reading.")
    repeats: int | None = Field(None, description="Firings of the subcircuit that share this exact context.")
    text: str = Field(description="The passage window, firing token wrapped in « ».")
    pre: str = Field("", description="Text preceding the window, when the data set ships it.")
    focal_token: str | None = None
    focal_index: int | None = None
    tokens: list[tuple[str, float | None]] | None = Field(
        None,
        description=(
            "[token text, activation] pairs; only with include=tokens. Values keep the neuron's own sign "
            "(a negative pole fires with negative values); null means the token was not routed to this "
            "neuron's expert. See token_unit for the scale."
        ),
    )
    token_unit: Literal["theta", "raw"] | None = Field(
        None,
        description=(
            "Scale of the token values: 'theta' = multiples of the pole's threshold θ (subcircuit examples), "
            "'raw' = raw activations (island examples)."
        ),
    )
    window: tuple[int, int] | None = Field(
        None, description="Token indices [start, end) that `text` covers (island examples show a window of a longer passage)."
    )


class NeuronRef(_Model):
    """Another neuron that takes part in a circuit of this one."""

    unit: str = Field(description="Neuron id, e.g. L18_E253_N170.")
    label: str = ""
    weight: float | None = Field(
        None,
        description=(
            "The unit's mean contribution over this subcircuit's firings. Drivers and opposers carry a "
            "positive magnitude (the role says which way they push); readers are signed."
        ),
    )
    rest: float | None = Field(None, description="Its mean contribution over the pole's other firings.")
    role: str | None = None
    in_atlas: bool = Field(False, description="True when the unit itself has a page in this release.")


class CircuitSides(_Model):
    gate_drivers: list[NeuronRef] = Field(default_factory=list, description="Earlier neurons that open the gate.")
    gate_opposers: list[NeuronRef] = Field(default_factory=list, description="Earlier neurons that close the gate.")
    up_drivers: list[NeuronRef] = Field(default_factory=list, description="Earlier neurons that raise the written value.")
    up_opposers: list[NeuronRef] = Field(default_factory=list, description="Earlier neurons that lower it.")
    readers: list[NeuronRef] = Field(default_factory=list, description="Later neurons that read this one.")


class Damage(_Model):
    """How much switching the neuron off on these firings changes the model's next-token predictions.

    These are averages over the *sampled* firings (the Modes tab of the explorer).  The Overview's
    "damage per firing", weighted to how often firings occur in real text, is in ``landscape.modes``.
    """

    per_firing: float | None = Field(None, description="KL divergence (nats) per sampled firing.")
    ratio: float | None = Field(None, description="Relative to the pole's average sampled firing; 1 = typical.")
    n_measured: int | None = None


class StrengthProfile(_Model):
    """Shares of a subcircuit's firings by strength relative to the threshold θ."""

    ge_theta: float | None = Field(None, description="At or above θ.")
    mid: float | None = Field(None, description="Between 0.5 θ and θ.")
    weak: float | None = Field(None, description="Below 0.5 θ.")
    closed: float | None = Field(None, description="With the gate closed (gate input negative).")


class Subcircuit(_Model):
    """A group of firings that the same earlier parts of the model cause."""

    id: str
    mode_index: int | None = Field(None, description="Mode this subcircuit was merged into.")
    n: int | None = Field(None, description="Number of firings.")
    share: float | None = Field(None, description="Share of the pole's firings (0..1).")
    meaning: str = Field("", description="What the firings have in common, read from the text.")
    token_pattern: str = Field("", description="Which token the neuron fires on.")
    readable: bool | None = None
    reading_confidence: int | None = None
    function_label: str = Field("", description="Short name of what the subcircuit computes.")
    function: str = Field("", description="How the inputs combine, read from the circuit.")
    gate_role: str = ""
    up_role: str = ""
    function_confidence: int | None = None
    profile: StrengthProfile | None = None
    median_a_rel: float | None = Field(None, description="Median firing strength in units of θ.")
    damage: Damage | None = None
    circuit: CircuitSides | None = None
    examples: list[Example] = Field(default_factory=list)
    research: dict[str, Any] | None = Field(None, description="Remaining upstream fields (include=research).")


class Mode(_Model):
    """One distinct thing the pole fires on."""

    index: int
    label: str
    public_label: str = Field(description="Label as shown on goodfire.com (special modes are renamed there).")
    kind: Literal["open", "closed_gate", "residue"] = Field(
        description=(
            "open: a readable mode · closed_gate: all subcircuits that fire mostly through the gate's weak leak "
            "(Q3/Q4), lumped together · residue: subcircuits set aside as unreadable. (The landscape regroups the "
            "last two into several Q3/Q4 modes plus one 'unassigned' bucket, so only open modes line up.)"
        )
    )
    description: str = ""
    share: float | None = Field(
        None, description="Share of the pole's sampled firings (0..1); see landscape.modes[].firing_share for real-text weighting."
    )
    n_firings: int | None = None
    subcircuits: list[str] = Field(default_factory=list)
    damage: Damage | None = None
    examples: list[Example] = Field(default_factory=list)


class Island(_Model):
    """A cluster of the pole's strongest examples that occur in similar contexts (the older read)."""

    id: int
    number: int = Field(description="Island number as printed on goodfire.com (id + 1).")
    n: int | None = Field(None, description="Examples in the cluster (not all are shipped).")
    name: str = ""
    description: str = ""
    examples: list[Example] = Field(default_factory=list)


class IslandMode(_Model):
    name: str
    description: str = ""
    share: float | None = None
    unassigned: bool = False
    islands: list[int] = Field(default_factory=list, description="Island ids.")


class IslandsView(_Model):
    modes: list[IslandMode] = Field(default_factory=list)
    islands: list[Island] = Field(default_factory=list)


class PoleDetail(_Model):
    """Everything known about one pole, assembled from the per-pole files that are available."""

    pole: PoleHit
    theta: float | None = Field(None, description="Activation at which the pole typically starts to fire.")
    n_firings: int | None = Field(None, description="Firings analysed for the subcircuit read.")
    closed_gate_share: float | None = Field(None, description="Share of firings with the gate closed.")
    auto_label: str = Field("", description="Automatic one-paragraph label of the whole neuron.")
    summary: str = Field("", description="Written summary of the pole across its modes.")
    unified_description: str = ""
    modes: list[Mode] = Field(default_factory=list)
    subcircuits: list[Subcircuit] | None = None
    islands: IslandsView | None = None
    overview: dict[str, Any] | None = None
    landscape: dict[str, Any] | None = None
    circuit: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    sources: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per data set: used, not_requested, not_mirrored, absent (does not exist upstream), "
            "or 'error: …' when it could not be read."
        ),
    )
    notes: list[str] = Field(default_factory=list)


class NeuronDetail(_Model):
    did: str
    display: str
    layer: int
    expert: int | None
    neuron: int
    in_atlas_index: bool
    circuit_label: str = ""
    circuit_summary: dict[str, Any] | None = None
    poles: list[PoleHit] = Field(default_factory=list)
    circuit: dict[str, Any] | None = Field(None, description="Circuit of the positive pole (see `circuits` for both).")
    circuits: dict[str, dict[str, Any]] | None = Field(
        None, description="With include=circuit: the circuit of each pole, keyed 'pos' and 'neg'."
    )
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- selections, mirror, similarity


class Selection(_Model):
    """A set of poles, chosen in one of several ways (combine freely; the results are united).

    This is how a *semantic region* is cut out of the atlas: by a search, by
    similarity of meaning to a text or to a pole, by explicit ids - or simply
    everything.
    """

    ids: list[str] | None = Field(None, description="Neuron or pole ids, any spelling.")
    query: SearchQuery | None = Field(None, description="All poles this search finds (its limit/offset are ignored).")
    similar_to_text: str | None = Field(None, description="Poles whose modes are closest in meaning to this text.")
    similar_to_pole: str | None = Field(None, description="Poles closest in meaning to this pole.")
    top: int = Field(200, ge=1, le=10000, description="How many poles the similarity options take.")
    min_score: float | None = Field(None, description="Drop similarity matches below this cosine.")
    neighbours: int = Field(
        0, ge=0, le=3, description="Also take poles of neurons up to this many circuit steps away (needs mirrored circuits)."
    )
    all: bool = Field(False, description="Every pole of the release.")
    public_sample: bool = Field(False, description="The 3,000 poles goodfire.com lists.")
    max_poles: int | None = Field(None, ge=1, description="Safety cap on the size of the selection.")


class MirrorRequest(_Model):
    selection: Selection
    sources: list[Literal["subcircuit", "overview", "landscape", "islands", "circuit"]] = Field(
        default_factory=lambda: ["subcircuit", "overview", "landscape", "islands", "circuit"],
        description="Data sets to fetch for every selected pole.",
    )
    index: bool = Field(True, description="Make the fetched texts searchable afterwards (scope 'deep').")
    index_examples: bool = Field(False, description="Also index example passages (scope 'examples'; larger index).")
    dry_run: bool = Field(False, description="Only report what would be fetched.")


class SimilarItem(_Model):
    pid: str
    display: str
    score: float = Field(description="Cosine similarity (1 = same meaning).")
    mode_index: int | None = None
    label: str = ""
    description: str = ""
    category: Category = "none"
    layer: int = 0
    atlas_url: str | None = None


class SimilarResponse(_Model):
    anchor: str = Field(description="What the neighbours are similar to.")
    by: Literal["pole", "mode", "text"]
    method: str = Field(description="Embedding used, e.g. lsa-256.")
    items: list[SimilarItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BatchPolesRequest(_Model):
    ids: list[str] = Field(min_length=1, description="Neuron or pole ids; a neuron id expands to both poles.")
    include: list[Include] = Field(default_factory=lambda: ["modes", "examples"])
    max_examples: int = Field(2, ge=0, le=12)


class BatchPolesResponse(_Model):
    poles: list[PoleDetail] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    took_ms: float = 0.0
