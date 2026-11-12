# Overview tab ("NS11 explainer" / neuron-scientist page) - schema and behaviour

Release: `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/` (paths below are relative to it).
View page: `neuron_overview-mobile-v3.html` (37.6 KB, one inline `<script>`, no external JS).
Source of truth for this document: the page's JavaScript (read, never executed), `shell.js` / `explorer.html`
(the atlas shell that embeds the page), and six sample poles
(`L30_E47_N449__pos`, `L4_E1_N177__neg`, `L4_E1_N177__pos`, `L4_E1_N271__pos`, `L4_E1_N345__neg`, `L4_E1_N372__neg`).

Evidence tags used throughout:

- **[JS]** - proven by how the view page (or the shell) reads/uses the field, or by a comment in that code.
- **[DATA]** - proven by the data itself (checked on all 6 samples and/or the two corpus-wide files).
- **[INFER]** - my reading of a name or a pattern; NOT proven. Treat as a hypothesis.

Vocabulary used by the files: `did` = neuron id `L{layer}_E{expert}_N{neuron}`; `pid` = pole id `{did}__pos` | `{did}__neg`;
`θ` (theta) = the pole's firing threshold in raw activation units; "strength" = activation / θ, sign-flipped so the pole's own
direction is positive; subcircuit ids are `c{n}` (`c0`, `c1`, ...).

---

## 1. Files at a glance

| Upstream path | Keyed by | Size (samples) | Read by | Required for the Overview? |
|---|---|---|---|---|
| `ns11_pages/data/index.json` | global | 292 KB | the shell (`shell.js` boot), NOT the overview page | no |
| `ns11_pages/headline_overrides.json` | global (map pid -> text) | 970 KB | overview page (fetched once, cached in `S.HL`) | no (falls back to `{}`) |
| `ns11_pages/data/{pid}.json` | pid | 0.41 - 0.90 MB | overview page (`S.O`) | no (page still renders without it) |
| `neuron_landscape_data/{pid}.json` | pid | 1.4 - 1.9 MB | overview page (`S.L`) and the embedded landscape iframe | **yes** - without it only a no-data message is shown (6.2) |
| `subcircuit_modes_data/{pid}.json` | pid | 0.47 - 1.2 MB | overview page (`S.SC`) | no (examples fall back to the landscape dots) |
| `neuron_landscape_data/ctx/{pid}.json` | pid | absent (HTTP 403) for all 6 samples | overview page (`S.CTX`) | no (see section 5.3) |

Loading [JS]: the four per-pole files are fetched in parallel on every pole load, `headline_overrides.json` only on the first
load. Any non-2xx status, network error or JSON parse error becomes `null`; nothing throws. All five URLs are relative to the page.

Corpus numbers [DATA]: 5,477 poles have an NS11 page (2,742 neurons; 2,735 with both poles; 2,740 pos / 2,737 neg), in layers
4, 9, 14, 19, 24, 30, 36, 39 (646 - 724 poles per layer), experts 0 - 255, neurons 0 - 511. The set is identical to
`neuron_landscape_data/atlas_index.json` (a plain JSON array of the same 5,477 pids).

---

## 2. `ns11_pages/data/index.json` (global)

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `pids` | string[5477] | no | Every pid that has `ns11_pages/data/{pid}.json`. Sorted ascending as plain strings (so `L14_...` precedes `L4_...`), unique. [DATA] | `"L14_E100_N452__neg"` |
| `model` | object pid -> string | no | Which "neuron scientist" model wrote the page. Same keys, same order as `pids`. Values: `"opus"` (5,377) or `"glm"` (100; 10 - 15 per layer). [DATA]. No local JS reads it. | `{"L14_E100_N452__neg":"opus"}` |
| `sim` | object pid -> number | no; sparse (585 of 5,477) | "Simulatability" / prediction score of the pole's description, as a fraction (1.0 = 100%). 4 decimals. Insertion order is neither by key nor by value. [JS shell + DATA] | `{"L14_E92_N459__pos":0.4648}` |

How the shell uses it [JS, `shell.js` lines 39 - 41, 78 - 99]:

- `sim` (kept in `S.sim`, `{}` when absent) drives the list sort keys `sim-desc` and `sim-asc`; unscored poles sort last
  under both.
- `pids` is stored in `S.ov` but never consulted again: tab availability uses the landscape index. `model` is never read. [JS]
- The fetch is optional; the shell works without it.

`sim` statistics [DATA]: min -33.6162, max 0.9987, median 0.6123, mean 0.4316 (dragged down by outliers);
12 values are negative, 6 are below -1 (-33.6, -24.7, -20.5, -13.4, -6.1, -1.73); none exceeds 1.
By model: opus 486 scored (median 0.6145), glm 99 of 100 scored (median 0.5973). By pole: 290 pos / 295 neg.
Histogram: <-1: 6 | -1..0: 6 | 0-0.1: 3 | 0.1-0.2: 3 | 0.2-0.3: 14 | 0.3-0.4: 33 | 0.4-0.5: 85 | 0.5-0.6: 125 | 0.6-0.7: 138 | 0.7-0.8: 83 | 0.8-0.9: 52 | 0.9-1.0: 37.

Shell list-cell display of a `sim` value `v` [JS]: missing -> a not-scored label; `v < -1` -> "< −100%"; otherwise
`round(100·v)` + "%". Each case has an explanatory tooltip (text omitted here).

Relation to the per-pole file: [INFER] `sim[pid]` is the same number as `kl.own.kl_recovered` in `ns11_pages/data/{pid}.json`
(section 4.3). This could not be checked: none of the 6 sampled poles is scored (all have `kl: {}` and no `sim` entry).

---

## 3. `ns11_pages/headline_overrides.json` (global)

Flat object `pid -> string`. 5,469 entries, all keys are in `index.json.pids`; 8 pids have no override
(`L14_E46_N1__pos`, `L30_E102_N264__neg`, `L39_E183_N259__neg`, `L39_E19_N172__pos`, `L4_E153_N188__neg`, `L4_E197_N23__neg`,
`L4_E206_N45__neg`, `L4_E7_N380__neg`). Not sorted. [DATA]

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `{pid}` | string, 91 - 236 chars (median 148), never empty, never multi-line | absent for 8 pids | Replacement for the scientist's own `explain.headline` on the page. [JS] | `"L4_E1_N177__pos": "…"` |

Plain text. A few values contain literal `«...»` (35), `|` (80), `⟦...⟧` (55) or `<` (28). The page prints the headline with
`esc()` only (it does NOT pass it through the `lab()` markup renderer used for mode names), so those characters appear literally. [JS + DATA]

Headline composition [JS]: `<h1>` = override if present, else the scientist's `explain.headline`, else empty. When an
override is used an (i) icon is appended whose tooltip quotes the scientist's original headline. `#summary` gets
`explain.summary` as plain text. The override file is fetched once per page lifetime; if the fetch fails `S.HL` becomes `{}`
and is not retried.

---

## 4. `ns11_pages/data/{pid}.json` (per pole) - the "scientist page"

All 6 samples have exactly the same 24 top-level keys [DATA]. The Overview page reads only
`explain.{headline, summary, situations, not_this, how_we_know, open}`, `kl`, `pick` and `report` [JS]. Everything else is carried
in the file but unused by any page in this release (it is a self-contained snapshot, apparently for the older stand-alone
`ns11_pages/explainer.html` mentioned in a shell comment - that page is not in the local copy). For those unused fields the
meaning is established by cross-checking against the other data files, as noted.

### 4.1 Top level

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `pid` | string | no | Pole id; equals the file name. [DATA] | `"L30_E47_N449__pos"` |
| `did` | string | no | Neuron id. | `"L30_E47_N449"` |
| `pole` | `"pos"` \| `"neg"` | no | Pole key. | `"pos"` |
| `side` | `"positive"` \| `"negative"` | no | Long form of `pole`. [DATA] | `"positive"` |
| `layer`, `expert`, `neuron` | int | no | Parsed id parts. | `30`, `47`, `449` |
| `cat` | string | no | Category code. Samples: `"mono"` (1) / `"poly"` (5); matches `verdict` of `subcircuit_modes_data` (`monosemantic` -> `mono`, `polysemantic` -> `poly`) in 6/6. [DATA]. It does NOT equal the shell's displayed category (the shell derives `mono`/`fn`/`poly` itself and e.g. puts L30_E47_N449__pos in `fn` where this file says `poly`). Unused by JS. | `"poly"` |
| `cat_public` | string | no | Public wording of `cat`: `"single mode"` / `"multiple modes"`. [DATA] | `"multiple modes"` |
| `atlas_label` | string | no | The pole's short description from the islands atlas: equals `public_atlas_data/index.json -> neurons[].poles[±].desc` in 4/4 non-empty cases; `"No clear pattern"` where that `desc` is empty (2/2). [DATA]. Unused by JS. | `"…"` |
| `threshold_raw` | float | no | θ: firing threshold in raw activation units. Equals `subcircuit_modes_data.theta` in 6/6. Sample range 0.148 - 0.319. [DATA] | `0.29145703125` |
| `pick` | string (1.9 - 2.3 KB) | no (page tolerates falsy) | Final description by the scientist: dense plain text with strengths in θ units (e.g. `19–23θ`) in a few labelled paragraphs; no Markdown. Shown as-is in a collapsed `<details>`. [JS] | `"…"` |
| `first_hypothesis` | string | no | Identical to `pick` in 6/6 samples. [DATA]. Unused by JS. [INFER: the description before revision, when it differs.] | same as `pick` |
| `report` | string (2.6 - 3.3 KB), Markdown | no (page tolerates falsy) | Full report by the scientist: Markdown (one `# ` title line, `**bold**` section leads, numbered lists, inline code). Shown as-is (NOT rendered as Markdown) in a collapsed `<details><pre>`. [JS] | `"# …\n\n…"` |
| `learned` | null | always null in samples | Unknown. Unused. | `null` |
| `kl` | object | no; `{}` in all 6 samples | Prediction ("simulatability") scores, see 4.3. [JS] | `{}` |
| `explain` | object | no (page tolerates absence) | The lay explanation, see 4.2. [JS] | |
| `circuit` | object | no | Digest of the circuit view, see 4.4. Unused by JS. | |
| `examples` | object[] (204 - 480) | no | The subcircuit exemplars, flattened, see 4.5. Unused by JS. | |
| `showcase` | string[5] | no | Five example ids: the 4 strongest `examples` by `focal_act`, plus one weak one (`focal_act` < 1). [DATA]. Unused. | `["c10_1","c10_0","c10_3","c10_2","c0_4"]` |
| `full_docs` | object id -> doc | no | Longer replayed context for each `showcase` id, see 4.6. Unused. | |
| `mode_docs` | object[] (0 - 5) | no | One long replayed document per scientist "situation" (first five), see 4.6. Unused. | |
| `islands_view` | object | no | Subcircuit modes re-expressed in the islands-atlas format, see 4.7. Unused. | |
| `scientist_model` | string | no | Full model name of the neuron scientist. `"claude-opus-5-5"` in 6/6 samples (all six are `"opus"` in `index.json.model`). The string used for `"glm"` poles is not in the samples. Unused by JS. | `"claude-opus-5-5"` |

### 4.2 `explain` - the written explanation

Keys in all 6 samples: `headline, summary, situations, not_this, how_we_know, open, contexts, _model`.

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `explain.headline` | string (95 - 118 chars) | page defaults to `""` | The scientist's original one-line title. Shown as `<h1>` only when no override exists; otherwise quoted in the h1 tooltip. [JS] | `"…"` |
| `explain.summary` | string (678 - 1009 chars) | page defaults to `""` | One-paragraph plain-language summary; printed under the headline as plain text. Strengths written as multiples of the threshold (`N×`). [JS] | `"…"` |
| `explain.situations` | object[] (2 - 20) | page defaults to `[]` | The scientist's per-mode (or per-facet) entries. See below. [JS] | |
| `explain.situations[].mode` | int | no | **Dual meaning** [JS comment + DATA]: on multi-mode poles it is the index of the open mode (same index as `neuron_landscape_data.modes[k]` and as the k-th open mode of `subcircuit_modes_data.modes`); on single-open-mode poles it is a subcircuit number `n` (subcircuit id `c{n}`) within the pole's single open mode. | `0` / `7` |
| `explain.situations[].name` | string (15 - 45) | no | Short title. Rendered only on single-mode poles, as the subcircuit's name in the subcircuit list (tagged "scientist"). [JS] | `"…"` |
| `explain.situations[].explain` | string (87 - 381) | no | Paragraph describing this mode/facet. On multi-mode poles this is THE text shown for the picked mode. On single-mode poles it goes into the subcircuit tooltip. [JS] | `"…"` |
| `explain.situations[].typical` | string (4 - 176) | no | Typical strength ranges in × threshold. Not rendered by this page. | `"…"` |
| `explain.situations[].cue` | string (13 - 84) | no | A short text snippet with the firing token wrapped in `⟦...⟧` (52 of 53 cues have exactly one pair; one has an empty `⟦⟧`); 7 of 53 are wrapped in double quotes. Not rendered by this page. [DATA] | `"… ⟦…⟧ …"` |
| `explain.situations[].scientist` | string (0 - 242; empty in 7 of 53) | may be `""` | What the scientist's experiments showed for this mode/facet. Rendered only on single-mode poles, in the subcircuit tooltip, after its `explain`. [JS] | `"…"` |
| `explain.not_this` | string[] (4 in every sample) | section omitted if empty/missing | Negative evidence: one string per near-zero case, strength included. One `<li>` each. [JS] | `"…"` |
| `explain.how_we_know` | object[] (5 in every sample) | section omitted if empty/missing | Controlled experiments. One table row each. [JS] | |
| `explain.how_we_know[].claim` | string (22 - 102) | no | The claim being tested (left cell, bold). | `"…"` |
| `explain.how_we_know[].test` | string (49 - 201) | no | What was changed/measured (right cell). | `"…"` |
| `explain.how_we_know[].result` | string (46 - 235) | no | Measured outcome (right cell, second line, grey). | `"…"` |
| `explain.open` | string (214 - 363) | section omitted if falsy | Open questions left by the scientist (heading "Still open"). [JS] | `"…"` |
| `explain.contexts` | object "n" -> string (0 - 29 entries, labels 8 - 46 chars) | may be `{}` | Short label per subcircuit: key `"n"` is the number of subcircuit `c{n}`. On multi-mode poles the keys are exactly the subcircuits of the open modes, in mode order (5/5 samples incl. the empty case); on the single-mode sample only the 6 subcircuits that have a `situations` entry. [DATA]. Not read by this page. | `{"16": "…"}` |
| `explain._model` | string | no | Model that wrote `explain`. `"claude-opus-5-5"` in 6/6. Unused. | `"claude-opus-5-5"` |

Text conventions [DATA]: `explain.*` writes strengths as "N×" (multiples of the threshold); `pick`/`report` write "Nθ" and quote
raw activations too. `⟦token⟧` marks the firing token in `cue`. The scientist text may use internal terms the public page does
not define (e.g. `Q3`, `gate_pre`, `up_pre`, subcircuit ids).

### 4.3 `kl` - prediction (simulatability) scores

Structure known ONLY from the JS (every sample has `kl: {}`).

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `kl` | object | `{}` when the pole was not scored (all 6 samples; index says 585 of 5,477 poles are scored) | Map from description source to score. Recognised keys, rendered in this fixed order: `fleet`, `g5m`, `census`, `h1`, `own`. Any subset may be present; other keys are ignored. [JS] | (no sample) |
| `kl.{key}.label` | string | required per present key | Row label naming the description that was scored. [JS] | (no sample) |
| `kl.{key}.kl_recovered` | number (fraction; 1.0 = 100%) | required per present key | The score; [INFER] 1 − KL(simulated activations) / KL(neuron zeroed). Bar width is clamped to 0 - 100%; the printed number (`round(100·kl_recovered)` %) is NOT clamped (can be negative, could exceed 100). [JS] | (no sample) |

`own` is the highlighted row (CSS class `me`, orange bar) - [INFER] the score of this page's own (scientist) description;
`fleet`, `g5m`, `census`, `h1` are [INFER] baseline/alternative description sources whose human labels are only in the data.
`examples[].pred` (always null in the samples) is [INFER] the simulator's predicted activation per token for scored poles.

### 4.4 `circuit` - digest of the Circuit tab (unused by the Overview page)

Cross-checked against `moe_circuit_explorer_data/{did}/meta.json -> pole_view["+"|"-"]` for L4_E1_N177 (both poles): same
number and order of `inputs` and `readers`; `name` = the meta name with its ` · gate ± / up ±` suffix removed;
`share` = meta `mass_share`; `readers[].n` = meta `n`. [DATA]

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `circuit.note` | string | `""` normally | Reason when there is no circuit. | `"…"` (L4_E1_N345__neg) |
| `circuit.inputs` | object[] (0 - 15) | may be `[]` | Upstream input groups (clusters of upstream neurons). | |
| `circuit.inputs[].name` | string | no | Group name. May keep a lane suffix such as `" · up-lane +"`. | `"…"` |
| `circuit.inputs[].effect` | `"+"` \| `"-"` | no | Sign of the group's effect. | `"+"` |
| `circuit.inputs[].role` | `"drives"` \| `"opposes"` | no | Usually `+`/drives and `-`/opposes, but all four combinations occur (L4_E1_N177__neg, L4_E1_N372__neg). [DATA] | `"drives"` |
| `circuit.inputs[].share` | float 0.58 - 1.0 | no | = `mass_share` of the circuit meta. Not a partition (values do not sum to 1). | `0.978` |
| `circuit.readers` | object[] (0 - 14) | may be `[]` | Downstream reader groups. | |
| `circuit.readers[].name` / `.effect` / `.n` | string / `"+"`\|`"-"` / int (2 - 23) | no | Group name, sign, and `n` of the circuit meta ([INFER] number of reader neurons in the group). | `{"name":"…","effect":"+","n":8}` |
| `circuit.logits` | object[] (8 - 10) | no | Output tokens with a weight; positive for pos poles, negative for neg poles in the samples; a mix of large (\|w\| about 0.09 - 0.53) and tiny (1e-5 - 5e-3) entries. [INFER: direct logit effect; large = most affected tokens, tiny = common tokens.] | `{"token":"…","w":0.1026}` |
| `circuit.expert_label` | null | null in 6/6 | Unknown. | `null` |

### 4.5 `examples[]` - the subcircuit exemplars, flattened (unused by the Overview page)

Exactly the exemplars of `subcircuit_modes_data/{pid}.json`: 12 per subcircuit, for every subcircuit (17 - 40 subcircuits ->
204 - 480 examples), ordered by subcircuit then exemplar index. Verified for all examples of 3 poles: same tokens,
`act` = SC token value × pole sign, `gate_closed` = SC `closed`, `ctx_tail` = last 300 chars of SC `pre`. [DATA]

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `examples[].id` | string `c{n}_{i}` | no | Subcircuit id + index into `subcircuits["c{n}"].exemplars` of the subcircuit file. Unique. | `"c11_0"` |
| `examples[].doc` | string | no | Same as `id` (6/6 files). | `"c11_0"` |
| `examples[].band` | string `c{n}` | no | Subcircuit id. | `"c11"` |
| `examples[].kind` | string | no | Always `"fire"`. | |
| `examples[].design` | string | no | Always `"subcircuit"`. | |
| `examples[].where` | string | no | Always `"firing example"`. | |
| `examples[].tokens` | string[33] | no | Token strings of the 33-token window (16 before, the firing token, 16 after). Padded with `""` at document start/end; multi-byte fragments can also decode to `""`. | `["…","…","…",...]` |
| `examples[].act` | (float\|null)[33] | no | Activation per token in multiples of θ, signed so that THIS pole is positive (negative = the opposite pole). `null` exactly where `routed` is false. 3 decimals. | `[null,null,1.253,0.7,...]` |
| `examples[].routed` | bool[33] | no | Whether the token was routed to this neuron's expert (mixture-of-experts routing). | |
| `examples[].focal` | int | no | Index of the firing token; always 16. | `16` |
| `examples[].focal_act` | float > 0 | no | `act[focal]`. Sample range 0.31 - 23.2. Can be < 1 (a "firing" is defined by the top-5% cutoff, not by θ). | `23.16` |
| `examples[].max_act` | float | no | `max(act)` over the window (verified); exceeds `focal_act` in about 15% of examples (309 of 2,040). | |
| `examples[].pred` | null | null in all samples | See 4.3 [INFER]. | |
| `examples[].ctx_tail` | string (0 - 300 chars) | may be `""` | Text preceding the window (tail of the SC `pre`). | |
| `examples[].ctx_len` | int | no | `len(ctx_tail)`. | `300` |
| `examples[].gate_closed` | bool | no | Firing with the gate input negative (a "Q3/Q4" firing). 10% - 96% of a pole's examples. | `false` |

### 4.6 `full_docs` and `mode_docs` - replayed long contexts (unused by the Overview page)

| JSON path | Type | Optional? | Meaning |
|---|---|---|---|
| `full_docs.{exampleId}` | object | one per `showcase` id | Longer window for that example. |
| `full_docs.{id}.tokens` | string[] (18 - 133) | no | Up to 100 tokens of preceding context + the 33-token window. In 25 of 30 sampled docs the example's 33 tokens are exactly the last 33, i.e. the firing token is at index `len-17`; the other 5 are four short documents (18 - 28 tokens, firing token near the start) and one whose last tokens differ from the stored window. Do not assume a fixed firing index; locate it by matching the example's tokens. [DATA] |
| `full_docs.{id}.act` | float[] (same length) | no | Activation in multiples of θ, pole-signed, recomputed for every token. `0.0` (never null) where not routed; `-0.0` occurs. The recomputed value at the firing token can differ a lot from the atlas value (e.g. 7.83 vs `focal_act` 13.90). [DATA] |
| `full_docs.{id}.routed` | bool[] | no | Routing flag per token. |
| `mode_docs[]` | object[] | 0 - 5 entries: `min(5, #situations)`; `[]` when the situations' modes have no exemplars (L4_E1_N345__neg) | One long document per scientist situation, in `situations` order. |
| `mode_docs[].unit` | int | no | = `situations[].mode` (mode index, or subcircuit number on single-mode poles). [DATA] |
| `mode_docs[].si` | null | null in all samples | Unknown. |
| `mode_docs[].a` | float | no | Atlas strength (`a_rel`) of the exemplar the document was built from: in all 22 sampled docs it is the strongest exemplar (max `a_rel`, exemplar #0 of its subcircuit) among the subcircuits of that unit. The recomputed `act` at the firing token usually differs slightly from `a` and can be 0.0 when the replay did not route the token. [DATA] |
| `mode_docs[].tokens` / `.act` / `.routed` | as `full_docs` | no | Same layout and units as `full_docs`. |

### 4.7 `islands_view` - subcircuit modes in "islands" format (unused by the Overview page)

Derived from `subcircuit_modes_data/{pid}.json` (`source: "subcircuit_modes"`). Verified on 6/6 samples: `modes` = the SC `modes`
minus those flagged `closed_gate`; `name` = SC `label`; `predicate` = SC `description`; `island_ids` = the numbers of SC
`subcircuits`; `kind` = `"residue"` iff SC `residue`; `share` equal; `islands[].n` / `name` / `description` = SC subcircuit
`n` / `function_label` / `meaning`; island examples = that subcircuit's 12 exemplars sorted by strength. [DATA]

| JSON path | Type | Meaning | Example |
|---|---|---|---|
| `islands_view.verdict` | `"polysemantic"` \| `"monosemantic"` | SC verdict. | |
| `islands_view.source` | string | Always `"subcircuit_modes"`. | |
| `islands_view.unit` | `"mode"` \| `"island"` | `"island"` on the monosemantic sample, `"mode"` on the 5 polysemantic ones. | |
| `islands_view.split` | bool | `false` in 6/6. Unknown. | |
| `islands_view.closed_gate_share` | float 0 - 1 | = SC `closed_share`: fraction of firings with the gate closed (0.18 - 0.99). | `0.3616` |
| `islands_view.modes[].name` / `.predicate` | string | Mode label / mode description (pipeline text, may contain `«»` and `\|` markup). | |
| `islands_view.modes[].kind` | `"mode"` \| `"residue"` | Residue = unreadable/unassigned group. | |
| `islands_view.modes[].island_ids` | int[] | Subcircuit numbers in the mode (may be `[]`). | `[2,3,9,10,13]` |
| `islands_view.modes[].share` | number 0 - 1 | Firing share of the mode (int `0` when empty). | `0.4421` |
| `islands_view.modes[].kl_ratio` | float \| null | = SC `modes[].kl.ratio` (54 of 54 sampled modes; null when SC `kl` is null): [INFER from the name] ratio of the mode's damage per firing to the pole-wide damage per firing (0.0002 - 8.06 in samples). | `2.238` |
| `islands_view.modes[].ev` | [float,int][] | One `[strength, island_id]` pair per open-gate example of the mode's islands (verified: equals the set of examples with `closed=false`). | `[[8.254,2],[7.626,2]]` |
| `islands_view.islands[].id` / `.n` | int / int | Subcircuit number / number of firings in it. | `2`, `366` |
| `islands_view.islands[].name` / `.description` | string | Subcircuit `function_label` / `meaning`. | |
| `islands_view.islands[].examples[]` | object[12] | `{si:null, pos:null, a (strength = SC a_rel), focal:16, tokens[33], act[33] (pole-signed, null = not routed), text (= SC pre + joined tokens; checked on one example per pole), closed (bool)}`. Sorted by `a` descending, so the order differs from `examples[]` / SC exemplars. | |

---

## 5. What the Overview reads from the other files

### 5.1 `neuron_landscape_data/{pid}.json` (`S.L`, alias `d`) - REQUIRED

| Field | Use in the overview page [JS] |
|---|---|
| `layer`, `expert`, `neuron` | Id row: neuron id, pole and number of real modes (hidden when `shell=1`). |
| `sign` (+1 / -1) | Chooses the shading colour (orange for a positive pole's own firings, blue for a negative pole's); fallback pole sign for exemplar values. |
| `modes[]` | The mode list. The LAST element is always a pseudo-mode for unassigned firings (no `closed` key) and is excluded everywhere; the real modes are `k < len(modes) - 1`. Open modes come first, then gate-closed (Q3/Q4) modes. |
| `modes[k].label` | Mode name; rendered through `lab()` (`«x»` -> highlighted firing token, `\|` -> token-boundary mark). |
| `modes[k].description` | Mode description from the pipeline; shown when no scientist text covers the mode (trailing period stripped). |
| `modes[k].closed` | true = a Q3/Q4 (gate-closed) mode: hollow swatch, "Q3/Q4" tag, never matched to scientist text. |
| `modes[k].fold`, `modes[k].rel` | For closed modes: `fold` = index of the open mode it relates to; `rel` in {`SAME`, `SUBCASE`, `SUPERSET`, `NEW`, null}. They select a lead-in sentence that names `modes[fold].label`: one wording for `rel = SAME`, another for any other `rel`; without `fold` a fixed sentence. Also colour inheritance in the fallback palette. |
| `rel_col[k]` | Precomputed colour per mode (hex); used unless `?rel=off`. Missing entry -> `#8E9396`; unassigned -> `#CFD0CD`. |
| `bands[]` (5 strength bands: p95, p99, p99.9, p99.99, top 1,000) with `mass`, `n`, `counts[k]`, `dmg_lin[k]` | Population-weighted per-mode numbers (formulas in 6.3). `dmg_lin` missing on `bands[0]` -> damage figures suppressed. A mode appears in the picker only if some band has `counts[k] > 0`. |
| `dots[]` with `m` (mode index), `kl` (measured damage, >0 = tested), `a` (strength), `t` ([token, value][]), `f` (focal index) | `kl>0` counts "tested firings" per mode (the "rough" flag); `dots` of a mode are the example fallback for Q3/Q4 modes or modes without exemplars. Dot token values are already pole-signed. |
| `kl_fit.n` | Count of tested firings stated in the glossary (200 when missing). |
| `n_firings` | Sample size stated in the glossary (5,000 in every sample). |
| `has_kl` | If strictly `false`, the picker orders by share of firings. The key is absent in all samples. |

### 5.2 `subcircuit_modes_data/{pid}.json` (`S.SC`) - optional

| Field | Use [JS] |
|---|---|
| `sign` | Multiplies exemplar token values so the pole reads positive (falls back to `L.sign`). |
| `modes[]` that have `subcircuits` and no `residue` | The k-th element of this filtered list is open mode k; its `subcircuits` (ids `c{n}`) are that mode's subcircuits. |
| `subcircuits[id].exemplars[]` -> `a_rel`, `pre`, `tokens` ([token, value\|null][]), `focal` | Examples of an open mode: strength = absolute value of `a_rel`, preceding text = `pre`, tokens, firing-token index. |
| `subcircuits[id].n` | Size of the subcircuit: ordering (largest first) and its share `n / Σ n` over the mode's subcircuits. |
| `subcircuits[id].function_label`, else `.meaning`, else the id | Subcircuit name when the scientist wrote none. |
| `subcircuits[id].meaning` | Shown in the subcircuit row tooltip. |

### 5.3 `neuron_landscape_data/ctx/{pid}.json` (`S.CTX`) - optional, absent in this release sample

Shape as read by the page [JS]: `CTX.modes[k]` = array of examples `{a, s, f, toks}` where `a` = strength (× threshold),
`s` = subcircuit id, `f` = index of the firing token, `toks` = `[token, value|null][]`. Window per a page comment: about 200
tokens before and 40 after the firing token, with recomputed per-token activations.

If present and non-empty for mode k it REPLACES all other example sources for that mode (`full:true`, no 33-token note,
and a firing token whose recomputed value is null, about 3% of examples per a page comment, is shown with `a`). It is also
the only source of the subcircuit list for Q3/Q4 modes (the distinct non-empty `s` values of its examples).

Absence handling: the 403 turns into `S.CTX = null`, so (a) open modes use the 12 exemplars per subcircuit from the
subcircuit file, (b) Q3/Q4 modes and modes without exemplars use the landscape `dots`, (c) Q3/Q4 modes show no subcircuit
list, (d) every expanded example carries the 33-token note (6.6). No error is shown.

---

## 6. How the UI renders it

### 6.1 Page skeleton

```
[#proto bar]  (stand-alone only)  title, pole select #pick, "Open with" buttons (open=dmg | none)
#wrap (2 columns: main | 300px sticky picker; one column <= 980px with the picker moved above the mode section)
  #idrow   neuron id · pole · number of modes                 (stand-alone only)
  main
    h1#h1          headline (+ (i) tooltip when rewritten)
    p#summary      explain.summary
    p#cover        coverage sentence
    details#glossd glossary -> p#gloss
    .lhead         landscape heading (i)
    iframe#land    neuron_landscape-mobile-v3.html?shell=1&bare=1&v=20261109C&did=..&pole=..[&mode=k]
    section#mode   the picked mode: name, numbers, explanation, subcircuits, ten examples
    section#whole  per-neuron blocks: not-this, experiments, prediction score, still open, final description, full report
  aside#picker     mode list (ranked)
```

### 6.2 No-data state

If the landscape file is missing: headline, summary, cover, id row, picker and the neuron-level section are emptied, the iframe
`src` is removed and `#mode` shows a fixed no-data message (class `nodata`).
If only the ns11 file is missing the page still renders: headline = override or empty, no summary, every mode falls back to
its pipeline `description`, and the neuron-level section (6.7) is empty.

### 6.3 Per-mode numbers (`numbers()`), exactly

For each band `b` of `L.bands` and each mode `k` (including the unassigned last one):

- weight `w_b = mass_b / n_b` (real-text frequency of one sampled firing of band `b`);
- damage `D_k = Σ_b w_b · dmg_lin_b[k]` (every `D_k = 0` when `bands[0]` has no `dmg_lin`); firings `F_k = Σ_b w_b · counts_b[k]`;
- totals `D = Σ_k D_k` and `F = Σ_k F_k` over ALL modes (each replaced by 1 when it is 0);
- `fshare_k = F_k / F`; `dshare_k = D_k / D`; `dpf_k = (D_k / F_k) / (D / F)` when `F_k > 0`, else 0;
- `meas_k` = number of `dots` with `m = k` and `kl > 0` (tested firings);
- `S.top` = the real mode with the largest `dshare_k` (can be a Q3/Q4 mode).

Formatting: `pct(v)`: null -> "–"; v < 0.1 -> one decimal ("7.8%"); else rounded ("62%").
`x_(v)`: v >= 10 -> rounded + "×"; 0 < v < 0.1 -> "<0.1×"; else one decimal + "×".
Number line [JS]: `pct(fshare)`, `pct(dshare)` and `x_(dpf)`, each with a short unit phrase, plus an amber "rough" flag with
`meas` when `meas < 10`. Without `dmg_lin` only `pct(fshare)` is shown.
Note: `modes[k].share` is NOT what is displayed; e.g. L30_E47_N449__pos mode 0 has `share` 0.4421 but is shown with
fshare 7.8%, dshare 62%, dpf 7.9×. [JS + recomputed from DATA]

### 6.4 Coverage sentence (`cover()`)

- `nOpen` = number of modes without `closed`, minus 1 (the unassigned pseudo-mode has no `closed` key);
- mode `k` is covered iff it is open and: `k = 0` when `nOpen = 1`; some `explain.situations[].mode` equals `k` otherwise;
- `fc`, `dc` = sums of `fshare_k`, `dshare_k` over the covered real modes.

Text [JS]: one sentence with `pct(fc)` and `pct(dc)`; when `dc < 0.6` a second clause follows, which also names the label of
`S.top` when `S.top` is not covered.
Recomputed for the samples: L30_E47_N449__pos 21% / 68%; L4_E1_N177__neg 21% / 55%; L4_E1_N177__pos 86% / 86%;
L4_E1_N271__pos 11% / 8.3% (top-damage mode is the Q3/Q4 mode 15); L4_E1_N345__neg 0.0% / 0.0% (all 18 modes are Q3/Q4);
L4_E1_N372__neg 26% / 61%.

### 6.5 Mode picker (right column)

- Header "Modes" with (i) `TIPS.modes`, and a one-line caption that names the current width quantity.
- First row "No mode picked". Then one row per real mode that has at least one firing in some band, sorted by the current
  width quantity (`dpf` for `wd="p"` (default), `fshare` for `"n"`, `dshare` for `"t"`), ties by `fshare`.
- Each row: colour swatch (filled = open mode, hollow = Q3/Q4), mode label via `lab()`, tag "Q3/Q4" for closed modes, black tag
  "most damage" on `S.top`, and the number line. Text opacity = 0.45 + 0.55·√(val / max val).
- Colours: `rel_col[k]` when present (unless `?rel=off`), else a fixed 8-colour palette (`HUES`), assigned in order to the open
  modes, then to closed modes without `fold`; a closed mode with `fold` takes the colour of `modes[fold]`; beyond 8 -> `#8E9396`.

### 6.6 The picked mode (`modeSection()`)

Nothing picked: `#mode` holds only a fixed hint (class `hint`).

Otherwise:

1. Head: swatch, mode label (`lab()`), "Q3/Q4" tag if closed; number line with (i) `TIPS.nums`.
2. Explanation - three cases:
   - single-mode case (`nOpen = 1`, `k = 0`, mode open): no paragraph; `situations` are used only by the subcircuit list
     (item 3).
   - multi-mode pole and open mode `k` with at least one situation whose `mode` is `k`: one `<p>` per matching entry containing
     `situation.explain` (only `explain`; `name`, `typical`, `cue`, `scientist` are not shown).
   - otherwise (closed mode, or no matching situation): one grey paragraph built from the `fold`/`rel` sentence (5.1), a fixed
     Q3/Q4 sentence (closed modes only), a fixed no-scientist-text note and the mode's `description` without its final period.
3. Subcircuit list (`subList`, needs the subcircuit file): a header with the subcircuit count and an (i) tooltip; one line per
   subcircuit, largest `n` first: name + share `pct(n/Σn)`. Name = the scientist's `situations[].name` when `c{mode}` equals
   the subcircuit id (single-mode poles only; tagged "scientist"), else the first non-empty of `function_label`, `meaning`, id.
   Row tooltip: for a matched situation its `name`, `explain` and (if non-empty) `scientist`, then the subcircuit's `meaning`.
   Rows are hover-only (a click-to-filter mentioned in a comment is not wired up).
4. Examples: a caption with (i) `TIPS.ex`, the shading legend, then the example cards.

Example selection (`examplesFor` + `tenFor`) [JS]:

- Source priority: ctx file -> subcircuit exemplars (open modes only) -> landscape `dots` with `m = k`.
- De-duplicated by the joined token text; sorted by strength.
- Up to 10 are taken in passes over the subcircuits (descending `n`); pass `r` adds the `r`-th strongest example of each.
  The result is sorted by strength.

Example card (`exHTML`) [JS]: collapsed row = strength `x_(a)` + a one-line snippet of tokens `f-7 .. f+8` (`f-3` on screens
narrower than 600 px) + chevron. Click toggles the expanded view: grey preceding text (an ellipsis + the last 600 chars of
`pre`), then every token of the window, then "…"; the body is cut at the start to at most 10 lines, but never at or after the
firing token; newlines are drawn as "⏎ ". Under it (unless the ctx file supplied the example) a fixed note about the
33-token window.

Token shading [JS]:

- `r = min(1, |v| / |peak|)`; alpha = 0 if `r < 0.06`, else `0.85 · r^0.7`, capped at 0.45 for opposite-sign tokens (`v < 0`).
- `peak` = absolute value at the firing token `f`, or the example strength `a` when that value is null or 0.
- colour: `v >= 0` -> the pole's colour (orange rgb(224,86,27) if `L.sign > 0`, blue rgb(51,105,138) if `L.sign < 0`); `v < 0` ->
  the other one.
- value null -> class `nr` (grey, dotted underline) and a not-routed tooltip.
- firing token -> 1.5px outline.

Token tooltip [JS]: the signed value (2 decimals, × threshold), then a marker: firing token, opposite pole (`v < 0`), or
`v/peak` as a rounded percentage.

Shading legend: five entries (pole colour, opposite colour, firing-token outline, not routed, hover hint).

### 6.7 "The neuron as a whole" (`whole()`), in this order; each block only if its data exists

| Block | Source | Rendering |
|---|---|---|
| negative evidence (i `TIPS.not`) | `explain.not_this[]` | bullet list |
| "Agent experiments" (i `TIPS.exp`) + a one-line caption | `explain.how_we_know[]` | table: `claim` \| `test` + `result` (grey) |
| prediction score (i `TIPS.score`) + a caption defining the score | `kl` (keys `fleet, g5m, census, h1, own` in that order) | one row per key: `label`, bar (0 - 100% clamp), `round(100·kl_recovered)` %; the `own` row is highlighted |
| "Still open" (i `TIPS.open`) | `explain.open` | paragraph |
| collapsed `<details>`: final description | `pick` | `<pre>`, as-is |
| collapsed `<details>`: full report | `report` | `<pre>`, as-is (Markdown not rendered) |

### 6.8 Definition tooltips - `TIPS`, verbatim

The page keeps its (i) tooltip texts in one object `TIPS` with the keys `head`, `land`, `modes`, `nums`, `ex`, `not`, `exp`,
`score` and `open`. Their wording is not reproduced here, nor that of the glossary, the subcircuit-list tooltip, the Q3/Q4
sentence, the shell's landing card or its disabled-tab tooltip. `TIPS.head` is defined but never used; the h1 tooltip is an
inline string that embeds `explain.headline`. For what "damage" measures see [`landscape.md`](landscape.md) section 2.2.5
(`dots[j].kl`).

---

## 7. User controls, URL parameters, messages

### 7.1 URL parameters of `neuron_overview-mobile-v3.html`

| Param | Values | Effect [JS] |
|---|---|---|
| `shell` | `1` | Embedded mode: hides the prototype bar and the id row (`body.shell`), reads `did`/`pole`/`mode`, listens to shell messages, reports picks to `parent`, never rewrites its own URL. |
| `did` | `L30_E47_N449` | (shell mode) neuron id. |
| `pole` | `neg` -> negative; anything else -> positive | (shell mode) pole. |
| `mode` | number | (shell mode) mode index to open on. Empty / non-numeric -> not given. Out of range (>= `len(modes) - 1`) -> no mode picked. Not given -> default per `open`. |
| `pid` | `L30_E47_N449__pos` | (stand-alone) pole to open; added to the select if not one of the 10 built-in test poles. Written back into the URL (no new history entry). |
| `open` | `none` \| anything else (= `dmg`) | Default selection when a pole opens with no explicit mode: `dmg` = the mode with the largest damage share, `none` = no mode picked. Read in both modes; written back in stand-alone mode. |
| `rel` | `off` | Ignore the precomputed `rel_col` relatedness colours and use the fixed 8-hue palette. |
| `v` | e.g. `20261109C` | Cache-buster added by the shell; ignored by the page. |

Shell (top-level `explorer.html`) grammar that leads here [JS]: `?n={did}&pole=pos|neg&tab=overview|islands|circuit[&mode=k]`
plus list filters `cat`, `l`, `sort`. `tab=landscape` is an alias for `overview`. The Overview tab is enabled (and opens first)
exactly for the pids in `neuron_landscape_data/atlas_index.json`; for every other pole it is disabled. The shell loads the
iframe as `neuron_overview-mobile-v3.html?shell=1&v={VIEWS_V}&did={did}&pole={pos|neg}[&mode={k}]`.

Stand-alone built-in test poles (`SIDES`): `L19_E199_N127__pos, L24_E121_N40__pos, L19_E242_N433__pos, L36_E176_N58__neg,
L14_E192_N4__neg, L14_E207_N122__pos, L30_E101_N179__neg, L9_E83_N277__neg, L36_E14_N67__pos, L30_E19_N104__pos`
(all in the index; three are scored: 0.5249, 0.8444, 0.2766).

### 7.2 On-page controls

| Control | Effect |
|---|---|
| Pole select `#pick` (stand-alone) | Loads another pole. |
| "Open with" segmented buttons for `open=dmg` / `none` (stand-alone) | Sets the `open` default and reloads the pole. |
| Mode picker rows (click, Enter, Space) | Pick a mode / "No mode picked". No scrolling. |
| Lanes and dots inside the landscape iframe | Pick a mode (via `landscape-mode`). |
| Landscape "Width" setting (inside the iframe) | Re-orders and re-shades the picker (via `landscape-width`; `p` -> `dpf`, `n` -> `fshare`, `t` -> `dshare`, see 6.5). |
| Example card click | Expand / collapse the example in place. |
| (i) buttons | Tooltip on hover (250 ms), focus or click; token tooltips are immediate. |
| `<details>` blocks (glossary, final description, full report) | Native disclosure. |
| Keyboard | ArrowDown / ArrowUp: next / previous picker row (wrapping, including "No mode picked"); Enter: opens the first collapsed example card; Escape: pick = none. In shell mode the keys `j k r R [ 1 2 3 4 p ? /` are forwarded to the shell (next/previous neuron, random, tabs, help, search). |
| `pointerdown` anywhere on the page | Sends `landscape-hidecard` to the landscape iframe (hides its dot card). |

A show-more button for the examples exists in the code but can never appear (all ten examples are always shown).

### 7.3 postMessage protocol [JS]

| Direction | Message | Meaning |
|---|---|---|
| shell -> overview | `{type:"shell-load", did, pole, mode}` | Load another pole in the already-loaded iframe. `pole` may be `"neg"`/`"-"`. `mode: null` opens with NO mode picked; `mode` absent opens per `open` default. |
| shell -> overview | `{type:"shell-pole", pole}` | Switch pole of the current neuron. |
| shell -> overview | `{type:"shell-mode", mode}` | Set the picked mode (null clears). |
| overview -> shell | `{type:"overview-mode", pid, mode}` | Mode picked on this page; the shell updates `&mode=` and sends `shell-mode` to its other loaded tabs. |
| overview -> shell | `{type:"shell-key", key}` | Forwarded keystroke (own, or relayed from the landscape iframe). |
| overview -> landscape iframe | `{type:"shell-mode", mode}` | Mirror the pick into the picture. |
| overview -> landscape iframe | `{type:"landscape-hidecard"}` | Hide the landscape's dot card. |
| landscape iframe -> overview | `{type:"landscape-mode", pid, mode, label}` | Reader picked a lane/dot. |
| landscape iframe -> overview | `{type:"landscape-width", pid, wd}` | Width setting changed (`p`/`n`/`t`). |
| landscape iframe -> overview | `{type:"landscape-size", h}` | Resize the iframe to height `h` px. |

---

## 8. Text fields worth indexing for search

Per pole (`ns11_pages/data/{pid}.json`):

- `explain.headline`, `explain.summary` - best short and medium descriptions (plain language).
- `explain.situations[].name`, `.explain`, `.scientist`, `.typical`, `.cue` (strip `⟦ ⟧` for matching; keep for display).
- `explain.not_this[]` (negative evidence: useful to answer "does NOT fire on ...").
- `explain.how_we_know[].claim`, `.test`, `.result`.
- `explain.open`.
- `explain.contexts{n}` (subcircuit short labels).
- `pick` (dense technical description, θ units) and `report` (Markdown lab report).
- `atlas_label` (islands-atlas one-liner; `"No clear pattern"` = none).
- `circuit.inputs[].name`, `circuit.readers[].name`, `circuit.logits[].token`, `circuit.note`.
- `islands_view.modes[].name`, `.predicate`; `islands_view.islands[].name`, `.description`; `islands_view.islands[].examples[].text`
  (full example text; duplicates the subcircuit file).
- `examples[].tokens` joined, plus `ctx_tail` (corpus text the neuron fired on).

Global: every value of `headline_overrides.json` (the headline actually shown; covers 5,469 of 5,477 poles).

Facets: `layer`, `expert`, `neuron`, `pole`, `cat` / `cat_public`, `index.json.model` (`opus`/`glm`), `scientist_model`,
`index.json.sim` (range filter; has-score flag), `kl` presence.

Cross-file text the Overview shows but that lives elsewhere: landscape `modes[].label` / `.description`, subcircuit
`function_label` / `meaning`.

---

## 9. Numeric / geometric structure usable for analysis

- **Scalar per pole**: `index.json.sim[pid]` (simulatability, 585 poles); `threshold_raw` (θ); `kl.{source}.kl_recovered`
  (per description source, scored poles only); `islands_view.closed_gate_share`.
- **Activation traces**: `examples[].act` (33-vector per exemplar, θ units, pole-signed, null = not routed) with `routed` mask,
  `focal` (=16), `focal_act`, `max_act`, `gate_closed`; `full_docs[*].act` / `mode_docs[].act` (18 - 133-token recomputed
  traces, 0.0 = not routed).
- **Bipartite / hierarchical structure**: pole -> modes -> subcircuits: `islands_view.modes[].island_ids`,
  `explain.situations[].mode`, `explain.contexts` keys, `examples[].band`, `mode_docs[].unit`. These join to
  `subcircuit_modes_data/{pid}.json` (`subcircuits["c{n}"]`, `modes[]`) and `neuron_landscape_data/{pid}.json` (`modes[k]`).
- **Weights**: `islands_view.modes[].share` (firing share), `.kl_ratio` (relative damage), `.ev` (`[strength, island]` scatter),
  `islands_view.islands[].n` (subcircuit size); `circuit.inputs[].share` with `effect`/`role` signs;
  `circuit.readers[].n` with `effect`; `circuit.logits[].w` (token -> weight, a small sparse output vector).
- **Links to other neurons**: none by id in this file (the circuit digest carries group names only). Neuron-to-neuron edges are
  in `subcircuit_modes_data` (`subcircuits[].circuit.gate_drivers[].unit`, e.g. `L28_E132_N167`) and
  `moe_circuit_explorer_data/{did}/`. The free-text `report` occasionally names other units/experts.
- **Derived per-mode quantities the page computes** (section 6.3) from landscape `bands`: population-weighted firing share,
  damage share, relative damage per firing, tested-firing count - worth precomputing in an API since they are what the UI shows.
- **Id grid**: layers {4, 9, 14, 19, 24, 30, 36, 39}, experts 0 - 255, neurons 0 - 511; 1,502 distinct (layer, expert) pairs.

---

## 10. Gotchas / variation across samples

1. **`situations[].mode` has two meanings.** Multi-mode pole: open-mode index. Single-open-mode pole: subcircuit number
   (L4_E1_N177__neg has modes `[7,8,1,2,3,4]` = subcircuits c7, c8, c1, c2, c3, c4 of its only mode). Decide with
   `nOpen = (#landscape modes without closed) - 1`; `nOpen = 1` -> subcircuit numbers.
2. **Scientist modes can be missing from the landscape.** L4_E1_N345__neg: the scientist describes modes 0 - 2, which exist in
   the subcircuit file as open modes with zero subcircuits/share, but the landscape lists only 18 Q3/Q4 modes.
   `situations[].mode` then does NOT index `landscape.modes`; the page shows none of the scientist's mode text and reports
   0.0% / 0.0% coverage (6.4). `contexts` is `{}` and `mode_docs` is `[]`.
3. **The last landscape mode is a pseudo-mode** for unassigned firings (`share: null`, no `closed`). It is excluded
   from lists and from the mode count, but its firings and damage ARE in the denominators of all shares.
4. **Displayed shares are recomputed**, population-weighted from `bands`; they differ strongly from `modes[].share` and
   `islands_view.modes[].share` (sample-based).
5. **`kl` is `{}` and `examples[].pred` is null in all six samples.** The prediction-score block and the row labels could only be
   documented from the JS. Only 585 of 5,477 poles (10.7%) are scored. Scores can be far below zero (min -33.6).
6. **`index.json.pids` and `.model` are not used by any page.** `model` is the only place distinguishing `opus` from `glm`
   pages at corpus level; per-pole files carry `scientist_model` / `explain._model` (only `claude-opus-5-5` observed). The schema
   of `glm` pages was not sampled.
7. **Missing files look like HTTP 403**, not 404 (S3/CloudFront). Treat any non-200 as "absent". The ctx folder is absent for
   all sampled poles.
8. **Headline**: 8 poles have no override (the scientist's headline shows, without the (i)). Overrides are escaped plain text;
   `«»`, `|`, `⟦⟧` inside them are literal, unlike mode labels where `«x»` and `|` are markup.
9. **Null vs 0.0 for "not routed"**: `examples[].act` and subcircuit/landscape token values use `null`; `full_docs`/`mode_docs`
   use `0.0` plus a `routed` mask. In landscape `dots[].t`, tokens before index `u` (= `f - 16`) are null because no activation
   was stored, yet the Overview's fallback rendering marks every null token as not routed.
10. **Sign conventions**: subcircuit exemplar token values are RAW-signed (multiply by `sign`); ns11 `examples[].act`,
    `islands_view` example `act`, `full_docs`, `mode_docs` and landscape `dots[].t` are already pole-signed (firing = positive).
    Strengths are in multiples of θ everywhere except `threshold_raw` and numbers quoted inside `report`.
11. **Strength < 1× exists** (`focal_act` down to 0.31): firings are defined by the pole's top-5% cutoff, not by θ.
12. **Code vs comment**: the shading comment says exponent 1.6 and a 10% floor; the code uses exponent 0.7 and a 6% floor.
    The comment on `numbers()` claims the published firing share is shown, but the code recomputes it. Trust the code.
13. **Shell quirk**: a fresh iframe load without `&mode=` opens on the largest-damage mode, but switching neuron inside an
    already-loaded iframe sends `mode:null` and opens with no mode picked.
14. `pick === first_hypothesis` and `learned === null` in all samples; `circuit.expert_label` is always null;
    `circuit.inputs`/`readers` can be empty (`note` explains); `explain.situations[].scientist` can be `""`.
15. `rel:"SAME"` with `fold:null` occurs (L4_E1_N345__neg): the page then uses the no-`fold` sentence (5.1), because only
    `fold` is tested.
16. Token windows are padded with `""` at document boundaries; `cue` may contain an empty `⟦⟧`.
17. File sizes: 0.4 - 0.9 MB per ns11 pole file, 90 - 96% of it `examples` + `islands_view`, which duplicate the
    subcircuit file. A bulk download of all 5,477 ns11 files is on the order of 3 - 4 GB uncompressed [INFER from 6 samples];
    the text needed for search (`explain` 5 - 12 KB, `pick` about 2 KB, `report` about 3 KB) is only 10 - 17 KB per pole.
