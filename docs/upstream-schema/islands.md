# Islands view ("Island Atlas") and its data: schema and semantics

Scope: `public_islands_atlas-mobile-v2.html` (view page), `public_atlas_data/islands/index.json`
(pole list) and `public_atlas_data/islands/poles/{pid}.json` (one file per neuron pole).
Release base: `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/`.

Evidence base: the full `<script>` of the view page (ground truth for semantics), the full `index.json`
(6,310 poles), and 6 sample pole files (49 islands, 561 examples, 121,505 tokens):
`L4_E1_N177__pos`, `L4_E1_N177__neg`, `L4_E1_N271__pos`, `L4_E1_N372__neg` (dark: only unassigned),
`L30_E47_N449__pos`, `L14_E78_N0__pos` (islands-only, no subcircuit read). Cross-checks used
`public_atlas_data/index.json`, `subcircuit_modes_index.json` and `shell.js`.

Marking used below: **[V]** = verified from the page's JavaScript or from the data;
**[I]** = inference (plausible, not proven by the files).

---

## 1. Concepts (as defined by the page itself)

Term summary:

| Term | Meaning | Status |
|---|---|---|
| pole / pid | One sign of one neuron. `pid = <did>__pos` or `<did>__neg`; `did = L{layer}_E{expert}_N{neuron}`. | V |
| island | A cluster of top-activating corpus examples of one pole. Has an id, a size `n`, an LLM-written name and description, and up to 12 shipped examples. | V |
| mode (islands view) | A group of islands made by an LLM judge, with a name, a predicate (description) and a firing share. Stored under the key `opus.modes`. NOTE: these are the island-based modes; the subcircuit view has its own, different mode list for the same pole. | V |
| unassigned | Islands in a `grey:true` mode, plus islands referenced by no mode. Rendered as "Unassigned examples". | V |
| scatter | A grey mode with no `island_ids` but a firing share (share from unclustered examples). Not present in the 6 samples. | V (code) |
| θ (theta) | Per-pole firing threshold (UI term "typical firing threshold"). Not stored directly; recoverable as `abs(act)/a_over_theta`. | V |
| a_over_theta | Example peak activation divided by θ (always positive). Shown as "N.N× threshold". | V |
| focal token | `tokens[focal_idx]`: the peak token of the example, shown outlined. | V |
| share ("firing X%") | Per-mode percentage parsed from `chips`; a corpus-level firing estimate, not derived from island sizes (3.4). | V |

---

## 2. File: `public_atlas_data/islands/index.json` (global, 1.0 MB, single line, UTF-8, no `\u` escapes)

Top-level shape: `{"poles": [ {pid, L, pole, name}, ... ]}`; only key is `poles`. 6,310 entries, every
entry has exactly these 4 keys **[V]**.

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `poles` | array<object> | no | One entry per pole that has an islands file. | 6,310 items |
| `poles[].pid` | string | no | Pole id, also the file stem of `poles/{pid}.json`. Form `L{layer}_E{expert}_N{neuron}__pos` or `__neg`; the page's parser also accepts `shared` in place of `E{expert}` (shown as "shared expert"), a form that never occurs here. Unique. | `"L4_E1_N177__pos"` |
| `poles[].L` | int | no | Layer. Always equals the layer in `pid`. Values present: 4, 9, 14, 19, 24, 27, 30, 35, 36, 39. Used for the layer filter and sort. | `4` |
| `poles[].pole` | string | no | `"pos"` or `"neg"`; always equals the pid suffix. 3,145 pos / 3,165 neg. | `"pos"` |
| `poles[].name` | string | no (may be `""`) | Pole summary: the names of the pole's non-grey modes joined with `" · "` (verified on all 6 samples; equal to `desc` in `public_atlas_data/index.json` for all 6,310 poles). Empty string for 346 poles = poles whose only "mode" is unassigned (category `dark` in the main index). 4,042 have one mode name (`mono`), 1,922 contain `" · "` (`poly`, 2 to 12 names). Length 0 to 423 chars, mean 97. Multilingual quotes inside (Cyrillic, CJK, etc.). | `"<mode name> · <mode name>"` |

Facts about coverage **[V]**:

- Per layer: L4 660, L9 687, L14 1,637, L19 675, L24 617, L27 2, L30 678, L35 2, L36 720, L39 632.
  256 distinct experts (E0..E255), neuron index 0..511. 3,244 distinct neurons; 3,066 have both poles.
- 1,019 of the entries are `L14_E78_*` (all 512 neurons of that expert) and 4 are `L27_E247_N96` /
  `L35_E88_N30`; `shell.js` removes exactly these from the public explorer and then shows only a seeded
  sample of 3,000 poles. The islands index and pole files still exist for all 6,310, so a bulk download
  gets more than the explorer UI exposes.
- The set of pids equals exactly the poles flagged `islands: true` in `public_atlas_data/index.json`
  (6,310 of 6,488).
- 5,300 of the 6,310 also appear in `subcircuit_modes_index.json`; 1,010 are islands-only (1,006 of them
  L14_E78).
- File order: sorted by (L, E, N, pos before neg) with one exception: the last two entries
  (`L4_E206_N45__pos/neg`) are appended after the L39 block. Do not rely on order.

---

## 3. File: `public_atlas_data/islands/poles/{pid}.json` (one per pole; samples 77 KB to 407 KB, mean 287 KB)

Compact single-line JSON, UTF-8, non-ASCII stored raw. Top-level keys in all 6 samples, in this order:
`pid, did, pole, layer, expert, neuron, sign, islands, examples_more_n, opus`.

### 3.1 Top level

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `pid` | string | no | Pole id. Used as current-pole key and in DOM ids. | `"L30_E47_N449__pos"` |
| `did` | string | present in all samples; not read by this page | Neuron id without pole suffix; link key to neuron-level files (e.g. `moe_circuit_explorer_data/{did}/`). | `"L30_E47_N449"` |
| `pole` | string | no | `"pos"` / `"neg"`. Chooses colours and the "Positive pole" / "Negative pole" label. | `"pos"` |
| `layer` | int | no | Layer, shown as "layer {layer}". | `30` |
| `expert` | int (JS also accepts null) | no | Expert index, shown as "expert {expert}"; `null` would be shown as "shared expert" (never seen). | `47` |
| `neuron` | int | no | Neuron index within the expert, shown as "neuron {neuron}". | `449` |
| `sign` | int | yes in JS (missing: derived from `pole`) | +1 for pos, -1 for neg. A token value counts as own-sign (4.4) when `value * sign > 0`. Present and consistent with `pole` in all samples. | `1` |
| `islands` | array<object> | no (JS uses it without a fallback) | The islands. In the file they are sorted by `n` descending, ties by `id` ascending (all 6 samples). 2 to 12 islands per pole in the samples. | see 3.2 |
| `examples_more_n` | int | present in all samples; not read by the page | Cap on `examples_more` per island. Always 7 in the samples; `len(examples_more) == min(7, n - 5)` holds for all 49 islands. | `7` |
| `opus` | object | yes in JS (missing = empty object) | The LLM judge's grouping of islands into modes. Key name suggests the judge model [I]. Only key: `modes`. | see 3.4 |

### 3.2 `islands[]`

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `islands[].id` | int | no | 0-based island (cluster) id, unique within the pole; ids are contiguous `0..k-1` in all samples but NOT in file order. Displayed as "Island {id+1}". Referenced by `opus.modes[].island_ids`. DOM anchor `isl-{id}`. | `10` |
| `islands[].n` | int | no | Number of examples in the island (cluster size), not the number shipped. Drives bar width in the island map and the text "Island k · n examples". Range 8 to 202 in samples. Sum over a pole's islands is at most 300 (300, 300, 293, 281, 275, 270), which suggests about 300 clustered examples per pole with the remainder being unclustered scatter [I]. | `56` |
| `islands[].name` | string | yes in JS (falls back to "(unnamed island)" / "Island {id+1}") | Short LLM-written island label. Non-empty in all 49 sample islands (19 to 82 chars). | `"<island name>"` |
| `islands[].description` | string | yes in JS (paragraph omitted if falsy) | LLM-written description (164 to 363 chars in samples). Convention: the focal token is marked with guillemets (`«…»`). May contain internal vocabulary: `θ`, `N×θ`, "events" (= examples). | `"<description>"` |
| `islands[].examples` | array<example> | no | The island's primary sample: exactly `min(5, n)` = 5 examples in all samples, labelled `top` / `median` / `random`. File order is NOT sorted; the page sorts them by `abs(act)` descending before display. | see 3.3 |
| `islands[].examples_more` | array<example> | yes in JS (missing = empty array) | Extra examples, `min(examples_more_n, n-5)` items (3 to 7 in samples), all labelled `band`, already sorted by `abs(act)` descending (all 49 islands). Shown behind a "N more examples" toggle. No duplicates with `examples` (checked by text + focal index). | see 3.3 |

Consequence: for an island with `n <= 12` every example is in the file; for larger islands only 12 of `n`.

### 3.3 Example object (`islands[].examples[]` and `islands[].examples_more[]`, same shape)

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `label` | string | no | Sampling role. `examples`: `"top"`, `"median"`, `"random"` (UI labels "Strongest", "Typical", "Random"). `examples_more`: always `"band"` (no UI label). Most common mix per island: 3 top + 1 median + 1 random (37 of 49), but other mixes occur (e.g. 2 top + 2 median + 1 random, 4 top + 1 median). | `"top"` |
| `act` | float | no | The example's peak activation of the neuron (signed; sign equals the pole's `sign` in all 561 samples). Rounded to 4 decimals; values lie on a bfloat16-like grid (e.g. 3.6562 = 3+42/64) [I for the dtype]. Displayed with 2 decimals and an explicit `+`. | `4.0625`, `-4.2812` |
| `a_over_theta` | float | yes in JS (falls back to showing raw `act`) | `abs(act) / θ`, always positive, up to 3 decimals. Shown with 1 decimal as "N.N× threshold". Range in samples 1.26 to 23.4. | `5.041` |
| `tokens` | array<[string, float]> | no | The passage as model tokens, in order. Each item is a 2-array `[token_text, activation]`. Length 42 to 241 in samples (mode 241 = 120 before + focal + 120 after). | `["<token>", 4.219]` |
| `tokens[][0]` | string | no | Decoded token text, including its leading space/newlines. Never empty in samples (JS skips empty strings). Concatenating all strings gives the passage text. | `" <token>"` |
| `tokens[][1]` | float | no | The neuron's (signed) activation on that token, rounded to 3 decimals. 72% of all sample tokens are zero; a few of those (0.3% of all tokens) are written `-0.0` (small negative rounded). Tooltip: "Activation {+v}". | `-4.031`, `0.0` |
| `focal_idx` | int | yes in JS (null-checked; if null the full passage is always shown) | 0-based index into `tokens` of the example's peak token. 120 for 75% of samples; smaller when the passage starts near the document start (min 1). | `120` |

### 3.4 `opus.modes[]`

| JSON path | Type | Optional? | Meaning | Example |
|---|---|---|---|---|
| `opus.modes` | array<object> | yes in JS (missing = empty array) | Modes in display order. In samples: named modes first in descending share, the grey mode last. 1 to 3 entries. | |
| `opus.modes[].name` | string | no | Mode name (LLM). For grey modes the literal `"unassigned"` (ignored by the UI, which prints "Unassigned examples"). | `"<mode name>"` |
| `opus.modes[].predicate` | string | no (may be `""`) | Mode description; shown under the mode name. `""` for grey modes. 25 to 305 chars in samples. | `"<predicate>"` |
| `opus.modes[].island_ids` | array<int> | yes in JS (missing = empty array) | Island ids belonging to the mode (ascending in samples). Ids missing from `islands` are dropped. Non-grey modes may list an island id that another mode also lists; a grey mode skips ids already used. No overlap in the samples. | `[0,1,4,5,6,9,10,11]` |
| `opus.modes[].chips` | array<string> | yes in JS (missing = empty array) | Display chips. Only form seen: `"firing 66.6%"` (one decimal). The page reads the share from the first chip of that form. Empty array = no share (seen on the dark pole's grey mode). | `["firing 74.2%"]` |
| `opus.modes[].grey` | bool | yes (only present, as `true`, on unassigned modes; 4 of 11 sample modes) | Marks the unassigned group. Several grey modes are merged; their shares are summed. | `true` |

Share behaviour **[V]**: shares are NOT island-size fractions (e.g. `L4_E1_N177__pos` island 1 holds 8% of
the clustered examples but its grey mode has "firing 35.2%"). Sums per pole in samples: 100.0, 100.1,
100.0, 99.9, but also 88.3 (single mode, no grey mode, remainder unreported) and none (dark pole).

---

## 4. How the UI renders it

### 4.1 Derived model (function `model(p)`) **[V]**

1. `isl` = map id -> island.
2. Walk `opus.modes` in order. Non-grey mode -> entry with `pub()`-translated name and predicate, its island
   ids and its share, numbered "Mode 1..k" in file order. Grey mode -> its ids go to the single Unassigned
   group, its share is added to the unassigned share; if it has no `island_ids` at all but has a share,
   that share is also recorded as `scatter`.
3. Any island referenced by no mode is appended to Unassigned.

### 4.2 Page layout

Standalone (no `shell`/`embed`): top bar (wordmark "Island Atlas" + "Qwen3.6 neurons", search box), left
rail "Neuron poles" (filters + list), right pane with h1 "Island Atlas", the intro, the collapsible "How to
read a page", then the pole.

Pole pane, single-column:

- Header (hidden in shell mode): `layer {L} · expert {E} · neuron {N} · Positive pole|Negative pole`,
  the pid as heading, chips "{k} mode(s)" (non-grey modes only) and "{n} island(s)".
- "Island map": a flex row of groups (modes with islands, in order, then Unassigned), each holding one bar
  for each of its islands; flex-grow is `n` for a bar and the summed `n` for a group; group label is the
  mode number or "Unassigned"; odd-numbered groups (0-based, i.e. Mode 2, 4, …) are drawn at 55% opacity;
  unassigned bars grey `#CFD0CD`. Bar tooltip: "{island name or 'Island k'} · {n} examples"; group tooltip
  "Mode k: {name}" / "Unassigned examples".
- Legend: one colour swatch per sign (shading rules in 4.4).
- Per mode: kicker "Mode k · N island(s) · X% of firing" (share omitted if absent; `<1` shown as
  "under 1%", otherwise rounded to an integer), mode name, predicate, then its islands in `island_ids`
  order. Only the island at position 0 of each group's id list renders expanded.
- Unassigned block: kicker "N island(s) · X% of firing", title "Unassigned examples", a one-line note
  (with the scatter share appended when present).
- Empty states (one fixed message each): loading, pole file fetch failed/404, embed without pole, pole
  without islands, no modes, mode without islands, start screen with the pole count, index load error.

Two-column layout (only with `?shell=1` at viewport width >= 900 px): the left column holds the island map
and a "Modes" card list (each card: kicker, name, a 3 px share bar with width `clamp(share,1,100)%`,
predicate, one row per island with name and `n`); the right column holds one pane per card (kicker, name,
legend, islands), only the selected card's pane visible. Clicking a card, a map group or an island row
selects/opens.

### 4.3 Island card

`Island {id+1} · {n} example(s)` / name (bold; "(unnamed island)" if missing) / description (clamped to 2
lines while collapsed). Body: the `examples` sorted by `abs(act)` descending, each with its label, then a
toggle "{m} more example(s)" holding `examples_more` in file order.

### 4.4 Example row (functions `exBlock`, `tokHtml`) **[V]**

- Strength line: `a_over_theta` with 1 decimal + "× threshold" (tooltip: "Peak activation", the signed
  `act` with 2 decimals, and an explanation of the ratio); if `a_over_theta` is null, the signed `act` with
  2 decimals (tooltip "Peak activation"). Then the label:
  top -> "Strongest", median -> "Typical", random -> "Random", anything else (band) -> nothing.
- Passage: serif font, whitespace preserved (newlines and runs of spaces), one `<span>` per token,
  empty-string tokens skipped.
- Collapsed window around the focal token, exact algorithm (page constant `NARROW` = 25):
  - going left from `focal_idx` (inclusive), count tokens whose text is not whitespace-only
    (JS `trim()` non-empty) until 25 are counted; `lo` = index of the last token visited;
  - going right from `focal_idx` (inclusive), count the same way until 26 are counted; `hi` = index after
    the last token visited;
  - result: 24 non-whitespace tokens before + focal + 25 after (516 of 561 samples; fewer near passage
    edges). Whitespace-only tokens inside the span are kept but not counted.
  - if `lo > 0` a grey "… " is prepended; if `hi < len(tokens)` a grey " …" is appended.
- Toggle button: "Show full passage · {N} tokens" where N = number of non-whitespace-only tokens of the
  whole passage; expanded state shows all tokens and the button text "Show less".
- Shading. `vmax` = max `abs(v)` over every token of every example (`examples` + `examples_more`) of the
  SAME island (floor 1e-6). For a token value `v`, with `r = min(1, |v|/vmax)`:
  - `v·sign > 0` (own sign): colour of this pole with alpha `0.9·r`; white text when `0.9·r > 0.5`
  - `v·sign <= 0` and `|v| > 0.05·vmax`: colour of the other pole with alpha `0.6·r`
  - otherwise no background
  - colours (RGB): positive pole `224,86,27` (orange), negative pole `51,105,138` (muted blue).
- The focal token gets class `focal` (1.5 px outline).
- Every token has the tooltip `Activation {v}` with an explicit `+` for positive values.

### 4.5 Text translation `pub()` applied before display **[V]**

Applied to: index `name` (list rows), mode `name` and `predicate`, island `name` and `description`.
Replacements, in order: `× θ` -> "× typical firing threshold"; `a / θ` -> "activation"; `θ` -> "the typical
firing threshold"; `Polysemantic` -> "Multiple modes"; `polysemantic` -> "multi-mode"; `Monosemantic` ->
"Single mode"; `monosemantic` -> "single-mode"; `events` -> "examples"; `event` -> "example".
This tells us the internal vocabulary: event = example, θ = typical firing threshold, a/θ = activation in
threshold units. It is NOT applied to passage tokens.

---

## 5. User controls and URL parameters

### 5.1 URL parameters / hash (all read once at load) **[V]**

| Parameter | Effect |
|---|---|
| `?pole=<pid>` | Open that pole. Highest precedence. |
| `?pid=<pid>` | Same; used if `pole` is absent. |
| `#<pid>` | Same; used if neither query parameter is present. The page also listens to `hashchange` (ignores hashes starting with `isl-` and the current pid). Clicking a list row sets the hash via `history.replaceState`. |
| `?shell=1` (any non-empty value) | Embedded in the atlas shell: adds body classes `embed` + `shell`; hides top bar, pole list, h1, intro and "How to read a page"; omits the pole header; enables the two-column layout at >= 900 px; forwards the keys `j k r R [ 1 2 3 4 p ? /` to the parent via `postMessage` (message type `shell-key`); does NOT fetch `index.json`. |
| `?embed=1` | Chrome hidden like shell, but the pole header stays and the layout is single-column; `index.json` not fetched. |
| `?data=<dir>` | Data directory relative to the page. Sanitised: only ASCII letters, digits, `_`, `/` and `-` are kept, then any `..` is removed. Default `public_atlas_data/islands`. Files read: `{dir}/index.json`, `{dir}/poles/{pid}.json` (pid URL-encoded). |
| `?v=<string>` | Not read by the page; cache-buster appended by the shell (constant `VIEWS_V`, `20261109C` in this release). |
| `#isl-<id>` | Href of island-map bars (click is intercepted; opens and scrolls to the island). |

How the shell embeds it (`shell.js`) **[V]**: the "Modes" tab loads
`public_islands_atlas-mobile-v2.html?shell=1&v={VIEWS_V}&pole={pid}&data=public_atlas_data/islands`
only when the pole has `islands: true` and no usable subcircuit read (no entry in
`subcircuit_modes_index.json`, or an entry with `n_modes` = 0, which the shell treats as a failed read).
Otherwise the tab shows `subcircuit_modes_view-mobile-v2.html`. Among the 5,287 island poles eligible for
the public sample this fallback applies to at most 24 poles (all zero-mode reads; fewer after the
3,000-pole sampling); the other 1,010 islands-only poles (1,006 L14_E78 poles plus the 4 poles of
`L27_E247_N96` and `L35_E88_N30`) are excluded from the public shell, yet they are listed in the islands
index and the local mirror holds one of their files (`L14_E78_N0__pos.json`), so they are downloadable.
The shell's landing-page specimen also reads pole files (`topExample`): it shows the entry of
`islands[].examples` with non-null `focal_idx` and the largest `abs(act)` (first in file order on ties).

### 5.2 Standalone controls **[V]**

| Control | Behaviour |
|---|---|
| Search box | Case-insensitive plain substring over `pid` and the raw `name` of each index entry. No tokenisation, no trimming, no search in island/mode text or passages. |
| Layer select ("All layers", "layer N") | Exact match on `L`; options built from the layers present in the index. |
| Pole select ("Both poles", "positive pole", "negative pole") | Exact match on `pole`. |
| Sort select ("Sort by layer", "Sort at random") | By layer: L, then expert (shared = -1), then neuron, then pole string (`neg` before `pos`). Random: reshuffled on every filter change. |
| "Random pole" button | Opens a random pole from the currently filtered list. |
| Counter | "{matches} of {total}". |
| List | First 600 matches only (a note gives the total match count). Row: pid, `pub(name)` (omitted when empty), "layer L · expert E · neuron N · Positive pole". |
| Island map bar / group | Opens the island and scrolls to it / to the mode. |
| Island header button | Expand/collapse the island. |
| "N more examples" | Native `<details>` toggle for `examples_more`. |
| "Show full passage · N tokens" / "Show less" | Per-example toggle between the ±25 window and the full passage. |
| Info icons, any `data-tip` element | Tooltip on hover (250 ms), focus or tap; Escape or scroll hides it. |
| Mode card (two-column shell) | Click, Enter or Space selects the mode; island rows open that island. |

The page has no pagination, no download, and no server-side API: everything is two static JSON fetches.

---

## 6. Text fields worth indexing for search

| Field | Notes |
|---|---|
| `index.json: poles[].name` | Pole-level summary (mode names joined by " · "). Split on " · " to get individual mode names. Empty for 346 "dark" poles. |
| `pole: opus.modes[].name` | Mode name (skip the literal "unassigned" of grey modes). |
| `pole: opus.modes[].predicate` | Mode description; often more formal/linguistic than the name. |
| `pole: islands[].name` | Finer-grained than modes; exists even for dark poles (a dark pole still has named islands). |
| `pole: islands[].description` | Richest text; quotes real snippets with the focal token in «guillemets»; multilingual. |
| `pole: islands[].examples[].tokens` / `examples_more[].tokens` | Passage text = concatenation of `tokens[][0]`; real corpus text in many languages, code, base64, etc. |
| focal token text `tokens[focal_idx][0]` | Useful as a facet ("which token does the pole fire on"). |
| ids: `pid`, `did`, `layer`, `expert`, `neuron`, `pole` | Exact-match filters. |

Store raw text and apply any `pub()`-style rewriting only at display time (see gotchas).

---

## 7. Numeric / structural content usable for analysis

There are no 2-D/3-D coordinates, embeddings, vectors, similarity matrices or neuron-to-neuron links in
the islands files. The "island map" is a proportional bar strip, not a spatial map. What exists:

| Path | Structure | Use |
|---|---|---|
| `islands[].n` | cluster sizes per pole | size distribution, entropy, share of clustered examples (`sum(n)` out of ~300 [I]) |
| `opus.modes[].island_ids` | bipartite graph mode -> islands (within one pole) | mode/island hierarchy; islands in no mode or in grey modes = unassigned |
| `opus.modes[].chips` ("firing X%") | corpus-level firing share per mode | mono/poly/dark-ness, dominance of the main mode |
| `examples[].act`, `a_over_theta` | signed peak activation and peak in threshold units | strength distribution; θ = `abs(act)/a_over_theta` (per pole) |
| `tokens[][1]` with `focal_idx` | activation sequence aligned at the peak (relative position -120..+120) | activation profiles around the peak, opposite-sign firing, sparsity |
| `label` | sampling stratum (top / median / random / band) | weighting when aggregating |
| `pid`, `did`, `layer`, `expert`, `neuron` | join keys | link to `public_atlas_data/index.json`, `subcircuit_modes_data/{pid}.json`, `neuron_landscape_data/{pid}.json`, `ns11_pages/data/{pid}.json`, `moe_circuit_explorer_data/{did}/` |

Measured values from the samples **[V]**: θ per pole (least squares over all examples; identical to 3
significant digits across a pole's examples): `L4_E1_N177__pos` 0.26575, `L4_E1_N177__neg` 0.26575 (same
value for both poles of this neuron), `L4_E1_N271__pos` 0.26327, `L4_E1_N372__neg` 0.26641,
`L14_E78_N0__pos` 0.4256, `L30_E47_N449__pos` 0.80585.

Example `act` values cluster into a top tail plus two narrow bands per pole (e.g. `L4_E1_N177__pos`:
33 examples at a/θ 1.7, 33 at 7.0, the rest >= 13.8), i.e. the shipped examples look like a stratified
sample (top + two activation bands) rather than only the strongest ones [I].

Any geometry (semantic maps of islands or poles) has to be built by us, e.g. by embedding
`islands[].name + description` or the passages.

---

## 8. Reconstructing an example as plain text / Markdown

- Collapsed view: take `lo`/`hi` from the window rule in 4.4, skip empty-string tokens, and add "… " /
  " …" where the window cuts the passage.
- Full passage text: `"".join(t[0] for t in tokens)`. Tokens already carry their own leading spaces and
  newlines; do not insert separators.
- Focal token: `tokens[focal_idx][0]`; its character offset in the full text is
  `len("".join(t[0] for t in tokens[:focal_idx]))`. Marking it as `«token»` reuses the convention the
  island descriptions already use (the token's leading space goes inside the marks: `« token»`).
- Activations: example level `act` (signed) and `a_over_theta` (positive, "N.N× threshold"); token level
  `tokens[i][1]` (signed, 3 decimals). For a text rendering of the shading, list the tokens with
  `|v| > 0.05·vmax` where `vmax` is the island-wide max of `|v|` (the page's own cut-off for
  showing opposite-sign shading), or simply the top-k tokens by `v·sign`.
- Header line as on the page: `"{a_over_theta:.1f}× threshold"` + label (top -> Strongest, median ->
  Typical, random -> Random) and `"Peak activation {act:+.2f}"`.
- Token count shown by the page = number of non-whitespace-only tokens.
- For one-line snippets the shell replaces each newline together with the whitespace around it by one
  space; the islands page itself keeps newlines.
- For Markdown output, escape Markdown/HTML metacharacters in the passage or put it in a fenced block:
  passages contain raw HTML, SQL, LaTeX, backticks, pipes, `*`, `_`, and literal two-character `\n`
  sequences.

---

## 9. Gotchas / variation across samples

1. `act` is not the focal token's value. `tokens[focal_idx][1] == act` in only 3 of 561 examples.
   Relative difference: <= 2% in 156, <= 10% in 212 more, <= 25% in 87, <= 50% in 39, > 50% in 67. In 45
   examples the boxed focal token has activation exactly 0.0 and in 7 it has the opposite sign (mostly
   `band` examples; 40 of these 52 are in `L30_E47_N449__pos`). The focal token is the argmax of
   `v * sign` within the passage in only 436 of 561. So `act`/`a_over_theta` and the per-token values come
   from different computations (probably the original corpus scan versus a re-run on the cut-out window
   [I]). Treat `act` as the example's strength and token values as display shading only.
2. 72% of token activations are zero, and only 0.3% of all tokens are written `-0.0`; rounding small
   values would give both signs about equally, so most zeros are true zeros, consistent with a
   mixture-of-experts neuron that is inactive when its expert is not used [I].
3. `islands[].n` is the cluster size, not the number of examples in the file (max 5 + 7 = 12 per island).
4. `islands` are ordered by size, not by id; modes list island ids in ascending id order, so the first
   (auto-opened) island of a mode is the lowest id, not the largest island. The UI prints `id + 1`.
5. Mode shares are corpus-level estimates stored only as display strings (`"firing 66.6%"`). They are not
   proportional to `n`, need not sum to 100 (88.3 in one sample), and may be absent (`chips: []`).
6. Dark poles: `index.name == ""`, a single mode `{name:"unassigned", predicate:"", grey:true, chips:[]}`
   holding all islands; the islands themselves still have names and descriptions. `grey` is simply absent
   (not `false`) on named modes.
7. The upstream `pub()` rewriting is naive and corrupts legitimate text: "event(s)" in its ordinary sense
   becomes "example(s)", and a literal θ in an index name (3 poles) becomes "the typical firing
   threshold". 26 index names contain "event(s)" or "-semantic" words. Also, the page's
   search runs on the raw name, while the list shows the rewritten name. Keep raw text in our store.
8. "Mode" is overloaded: `opus.modes` here are island-based modes; for the 5,300 poles that also have a
   subcircuit read the public shell shows a different mode list from `subcircuit_modes_data` and replaces
   the pole's category/description with the subcircuit one. `index.name` reflects the island-based modes.
   Example: `L30_E47_N449__pos` has 1 island mode here but 7 subcircuit modes.
9. Window length varies: nominally 241 tokens with `focal_idx = 120`; `focal_idx < 120` when the document
   starts earlier (min 1); fewer than 120 tokens after the focal in 104 examples; in 13 examples (all in
   `L30_E47_N449__pos`, mostly binary/mojibake text) the focal index is 111 to 119 with 121 to 128 tokens
   after it. Never assume 241 or 120; always use `focal_idx` and `len(tokens)`.
10. Special and odd tokens: the literal `<|im_end|>` occurs only as the last token of a passage (76 of 561
    examples; presumably the end-of-document marker [I]) and is rendered verbatim by the page. Tokens may
    contain U+FFFD replacement characters (split multi-byte characters; 546 tokens in the base64/mojibake
    pole, including one emoji, and 1 token in another pole), CR, TAB, DEL (U+007F) and C1 control
    characters. Whitespace-only tokens
    exist (about 5%) and one focal token is whitespace-only.
11. JS `trim()` and Python `strip()` differ on a few code points (U+FEFF is whitespace only in JS; U+001C
    to U+001F and U+0085 only in Python); irrelevant in practice but matters for byte-exact parity.
12. `sign`, `a_over_theta`, `focal_idx`, `examples_more`, `description`, `name`, `opus`, `island_ids`,
    `chips` are all treated as optional by the JavaScript even though every sample has them. Write the
    parser defensively. `examples_more_n` and `did` are present but unused by the page.
13. A missing pole file is a normal state (the page shows an empty-state message): 178 poles of the main
    index have `islands: false`.
14. Index order has one out-of-place pair at the end; the index has no `E`/`N` fields (parse the pid).
15. Size planning: mean sample pole file 287 KB; 6,310 files would be roughly 1.8 GB uncompressed if the
    samples are representative [I]. The release prefix is served `cache-control: immutable`.
16. Passages are raw web-scale corpus text (adult content, personal names, spam, etc. appear in island
    names and passages); relevant if the search API is exposed to a chatbot.
