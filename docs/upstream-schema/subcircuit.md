# Subcircuit Modes view ("Modes" tab for poles with a subcircuit read) and its data: schema and semantics

Scope: `subcircuit_modes_view-mobile-v2.html` (85 KB, one inline `<script>`, lines 588-1105), the global index
`subcircuit_modes_index.json` (3.5 MB) and the per-pole files `subcircuit_modes_data/{pid}.json` (6 samples, 0.47-1.19 MB each).
Release base: `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/` (paths below are relative to it).

Evidence markers used throughout:

- **[V]** verified: read directly from the page's JavaScript/CSS/comments, or measured on the data (all 6 sample pole files = 170
  subcircuits, 2,040 exemplars, 67,320 tokens, 8,500 circuit rows; index = all 5,477 poles).
- **[I]** inferred: plausible from names, values or sibling pages, but not proven by this page. Treat as a hypothesis.

Sample poles profiled: `L30_E47_N449__pos`, `L4_E1_N177__neg`, `L4_E1_N177__pos`, `L4_E1_N271__pos`, `L4_E1_N345__neg`, `L4_E1_N372__neg`.

---

## 1. Concepts (as defined by the page itself)

Our summaries; the Source column names the page element that defines each term (its text is not reproduced here).

| Term | Definition | Source |
|---|---|---|
| pole | (neuron, sign) pair. pid = `L{layer}_E{expert}_N{neuron}__pos` or `__neg`; `sign` = +1 / -1. | `HOW_HTML` [V] |
| theta (θ) | Field `theta` (3.1): per-pole firing threshold, raw activation units; displayed activations (`a_rel`, token values) = raw / θ. | `HOW_HTML`, `headHtml` [V] |
| firing | One token position where the pole fired (a sampled activation event). `n_firings` = number of sampled firings (5,000 in every sample). | `headHtml` [V] |
| subcircuit | Entry of `subcircuits` (3.3); the clustering run named in `partition` (3.1) partitions the pole's firings into them. Ids `c0`, `c1`, ... per pole. | `HOW_HTML`, `HOW_PUBLIC` [V] |
| meaning / token pattern | Opus output fields `meaning`, `token_pattern` of a subcircuit (3.3); input: exemplar texts only, no circuit data. | `HOW_HTML` [V] |
| function | Opus output field `function` of a subcircuit (3.3); input: its circuit tables (3.4). | `HOW_HTML` [V] |
| mode | Entry of `modes[]` (3.2); its `subcircuits` list comes from an LLM merge over the subcircuits' `meaning` fields. | `HOW_HTML`, `HOW_PUBLIC` [V] |
| residue | Mode with `residue:true` that collects subcircuits without a usable text reading; the review stage can add one too (`residue_source`). Public UI label "Unassigned examples". | `HOW_HTML`, `HOW_PUBLIC`, `openPole` [V] |
| closed gate / Q3/Q4 | Firing with gate pre-activation < 0 (exemplar `closed`, subcircuit `profile.closed`). The automatic closed-gate mode (`closed_gate:true`, also `residue:true`) holds the majority-closed subcircuits and does not count toward `verdict`. Public UI name "Q3/Q4" (quadrants with gate < 0, landscape.md section 3). | `HOW_HTML`, `HOW_PUBLIC`, data, landscape page [V] |
| gate lane / up lane | The neuron has two input lanes, "gate" and "up" (circuit tables `gate_*` / `up_*`, 3.4). [I] this is a SwiGLU-style unit, activation ~ silu(gate) × up, which is why a negative gate still leaks. | `CT`, data [V]/[I] |
| exemplar | Element of `exemplars[]` (3.5): 12 per subcircuit in three strength groups (order in 3.5); contexts pairwise distinct. | `HOW_HTML` [V] |
| context / repeats | Context = the 16-token window ending at the firing token; equal windows = same context. Per-exemplar count: `repeats` (3.5), UI tag "×N same context". | `HOW_HTML` [V] |
| fold | Exemplar `fold` (3.5): `"fit"` = discovery split, `"audit"` = held-out split. UI tags "fit" / "held-out". | `HOW_HTML`, `exHtml` [V] |
| held-out description test (judge, "simulatability") | Field `judge` (3.1): per judge model (`opus`, `jev`) and strength bin, `acc` = fraction of held-out firings placed in the correct subcircuit, `chance` = Σ share² over the subcircuits within the bin. Shell heading "Held-out simulation test". | `HOW_HTML`, `JUDGE_CAP`, `shellTopHtml` [V] |
| damage / KL / relative causal effect | Fields `kl.per_firing` (mean zero-ablation KL per firing in nats; 16-token window after the firing) and `kl.ratio` (`per_firing / kl_neuron_mean`, 1 = pole average). UI labels "Relative causal effect" (full atlas) / "Damage per firing" (public). | `KL_TIP`, `klHtml` [V] |
| fn-mono (functionally mono, "one dominant mode") | `fn_mono:true` = polysemantic `verdict` with `main_damage_share` ≥ 0.9 (section 2). Shell category label "One dominant mode". | `railRow`/`headHtml` tooltip, `shell.js` [V] |
| verdict | `"monosemantic"` or `"polysemantic"`; closed-gate and residue modes do not count toward it. | data [V] |
| ground-truth neuron | Constant `GT_ORDER` lists two pids, `L9_E63_N383__pos` and `L4_E52_N398__pos`; badge "GT" / "ground truth". Both pids exist in the index. | JS [V] |

---

## 2. File: `subcircuit_modes_index.json` (global)

3,524,032 bytes, one line, UTF-8 with raw non-ASCII characters (only 3 `\u` escapes), strict JSON (no NaN). Loaded by the shell
(`shell.js` / `explorer.html` `boot()`), **not** by the Modes view itself (the view's own built-in index `IDX` is empty, see 9.1).

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `source` | string | no | build path of the pipeline output (provenance only) [V] | `"…"` |
| `built_at` | string, ISO-8601 UTC | no | build time [V] | `"2026-11-05T21:15:39Z"` |
| `poles` | object, 5,477 keys = pid | no | one record per pole that has a subcircuit read. 2,742 distinct neurons (did), 2,735 with both poles; 2,740 pos / 2,737 neg. Layers present: 4, 9, 14, 19, 24, 30, 36, 39 (646-724 poles each); experts 0-255; neuron index 0-511 [V] | key `"L30_E47_N449__pos"` |
| `poles[pid].verdict` | string enum | no | `"polysemantic"` (3,355) or `"monosemantic"` (2,122). Same value as the per-pole file's `verdict` (6/6) [V] | `"polysemantic"` |
| `poles[pid].fn_mono` | bool | no | functionally mono (see section 1). Only ever true for polysemantic poles (1,651). All fn_mono poles have `main_damage_share` >= 0.9; non-fn_mono polysemantic poles have <= 0.9 (one at exactly 0.9, a rounding edge) [V] | `true` |
| `poles[pid].main_damage_share` | number in [0,1], 3 dp, or `null` | nullable (81 null) | main-mode KL damage / total KL damage of the pole. Reproduced exactly on 6/6 samples as `share0*kl0 / sum(share_i*kl_i)` over the **open (non-residue)** modes of the per-pole file, where mode 0 is the first (largest-share) open mode, not the highest-damage mode [V on samples]. `null` when no open mode has measured damage (all 51 zero-mode poles, plus 30 others) [V] | `0.99` |
| `poles[pid].n_modes` | int 0..30 | no | number of open (non-residue) modes = `modes.length` here (5,477/5,477) = count of per-pole `modes[]` entries without `residue` (6/6). 0 for 51 poles: the shell treats those as failed reads (`sc_empty`) [V] | `7` |
| `poles[pid].n_residue` | int 0..5 | no | number of per-pole `modes[]` entries with `residue:true` (closed-gate mode and/or residue modes) (6/6) [V]. Not listed in the index `modes[]`. | `2` |
| `poles[pid].n_subcircuits` | int 1..51 (mean 19.9) | no | number of keys in per-pole `subcircuits` (6/6) [V] | `17` |
| `poles[pid].modes` | array, length `n_modes` | no (may be `[]`) | the open modes only, in the per-pole file's order (identical label and share to the per-pole open modes, 6/6), sorted by `share` descending (5,477/5,477) [V] | |
| `poles[pid].modes[].label` | string (10-384 chars, median 76) | no | mode name written by the LLM judge [V] | `"…"` |
| `poles[pid].modes[].share` | number in [0,1], 4 dp (JSON int `0` possible) | no | firings in the mode / firings of the pole. Sum over the index modes is < 1 (mean 0.68) because residue/closed-gate modes are excluded. 34 modes in 28 poles have share 0 (see 9.6) [V] | `0.4421` |

How the shell uses it (`shell.js` `applySC`) [V]: `n_modes` = 0 -> flag `sc_empty`, category and view stay as derived from the island data; otherwise
category = `mono` if `verdict` is monosemantic, else `fn` if `fn_mono`, else `poly`, and the list description becomes the open-mode
labels joined by " · ". Category words: mono = "single mode", fn = "one dominant mode", poly = "multiple modes".

---

## 3. File: `subcircuit_modes_data/{pid}.json` (one per pole)

Fetched from `{DATA_DIR}{pid}.json` with the pid URL-encoded (default dir `subcircuit_modes_data/`). One line, **pure ASCII with `\uXXXX`
escapes** (unlike the index), strict JSON. Sample sizes 474,773 to 1,187,444 bytes (mean 0.82 MB; roughly 55 % circuit tables, 40 %
exemplars). The same file is also read by the Overview tab (`neuron_overview-mobile-v3.html`) for open-mode examples.

### 3.1 Top level

"Used by view" = read by this page's JS. Fields not used are still in the public file.

| JSON path | type | optional? | used by view | meaning | example |
|---|---|---|---|---|---|
| `pid` | string | no | yes | pole id, equals the file name | `"L30_E47_N449__pos"` |
| `layer`, `expert`, `neuron` | int | no | yes | parsed id parts (shown in the header kicker) | `30`, `47`, `449` |
| `sign` | int, `1` or `-1` | no | yes | the pole. Token values and `a` are stored in raw sign; the UI multiplies by `sign` to orient them to the pole. Also picks the colour (pos = orange `#C94A12`, neg = slate `#33698A`) [V] | `1` |
| `theta` | float, unrounded | no | yes (standalone header only, 4 dp) | firing threshold in raw activation units. Samples: 0.148-0.319 [V] | `0.31921484375000003` |
| `label` | string (359-590 chars) | no | yes ("Autointerp label"; hidden in public) | the pre-existing automatic-interpretation label of the neuron pole | `"…"` |
| `summary` | string (570-758 chars) | no | yes | Opus's summary of the pole across its modes; shown as the lead paragraph (standalone header; in the shell at the top of "All subcircuits") | `"…"` |
| `closed_share` | float 4 dp | no | yes (standalone header) | fraction of firings with gate pre-activation < 0. Equals the n-weighted mean of `subcircuits.*.profile.closed` when all firings are assigned (4/6 exact; 2/6 differ in the 3rd decimal because 22 / 32 of the 5,000 firings are in no subcircuit) [V]. Samples 0.178-0.992 | `0.3616` |
| `n_firings` | int | no | yes (standalone header) | number of sampled firings. 5,000 in all samples. `sum(subcircuits.*.n)` is 5,000 in 4 samples, 4,978 and 4,968 in two [V] | `5000` |
| `verdict` | `"monosemantic"` / `"polysemantic"` | no | yes (chip) | final verdict (same as index) | `"polysemantic"` |
| `fn_mono` | bool | no | yes (chip "fn-mono") | see section 1 | `true` |
| `main_damage_share` | number 3 dp or `null` | nullable | yes (in fn-mono tooltip) | see index; formula verified 6/6 | `0.99` |
| `kl_neuron_mean` | float | no | no | the pole's mean KL damage per firing (nats). Equals `sum(subcircuit.share * subcircuit.kl.per_firing)` (6/6, to ~1e-4 relative), and every `kl.ratio` = `kl.per_firing / kl_neuron_mean` (0 mismatches over 170 subcircuits and 55 modes) [V] | `0.0002917253612242801` |
| `judge` | object `{opus:{...}, jev:{...}}` | no (JS tolerates missing) | yes (hidden in public) | held-out description test per judge and strength bin. `judge.opus` is `{}` in all 6 samples; `judge.jev` is populated [V] | |
| `judge.{opus,jev}.{all,ge1,mid,weak,closed}` | object | each bin optional | yes | one bin. Keys present only when the bin has held-out firings (`L4_E1_N345__neg` has only `all`, `ge1`, `closed`). `ge1`+`mid`+`weak`+`closed` n sums to `all` n (6/6), so the three strength bins are **open-gate** firings and `closed` is all closed-gate firings regardless of strength [V] | |
| `judge.*.*.n` | int | no | yes | number of held-out firings judged in the bin (1,192-1,257 in `all`) | `1257` |
| `judge.*.*.acc` | float 0..1 | no | yes | fraction of the bin's judged firings placed in the correct subcircuit | `0.4478918058870326` |
| `judge.*.*.chance` | float 0..1 | no | yes | Σ over subcircuits of (share within the bin)², i.e. the expected accuracy of guessing in proportion to the shares | `0.17878243016514045` |
| `partition` | string | no | yes (standalone header chip "partition") | the clustering run id. `"leiden_drvopp_m30__res1.0"` in all samples. [I] Leiden clustering on driver/opposer evidence, resolution 1.0 | `"leiden_drvopp_m30__res1.0"` |
| `modes` | array | no | yes | all modes in display order: open modes (share descending), then the closed-gate mode, then residue modes. See 3.2 | |
| `subcircuits` | object keyed `c0`..`cN-1` | no | yes | all subcircuits. Keys are contiguous `c0..` in insertion order (not sorted by size). See 3.3 | |
| `modes_before_unify` | array | **optional** (absent in `L4_E1_N345__neg`) | no | the mode list before the "long-context unifier/review" stage; same element shape as `modes[]` but without `merged_from`/`residue_source`, and with `unique_contexts`/`top_context_share` populated for open modes [V shape] | |
| `verdict_before_unify` | string | optional (absent in `L4_E1_N345__neg`) | no | verdict before that stage [I] | `"polysemantic"` |
| `review_verdict` | string | optional (absent in 2/6) | no | verdict given by the review stage [I] | `"polysemantic"` |
| `verdict_source` | string | optional (absent in `L4_E1_N345__neg`) | no | which stage produced `verdict`; `"long-context unifier"` in 5 samples [V value] | `"long-context unifier"` |
| `unified_description` | string (677-1,110 chars) | optional (absent in `L4_E1_N345__neg`) | no | one-paragraph unified description of the whole pole, with inline examples that mark the firing token as `«token»` [V content] | `"… «…» …"` |

### 3.2 `modes[]`

58 mode objects in the samples. Order [V]: open modes sorted by share descending, then (if any) the closed-gate mode, then
residue modes. Every subcircuit id appears in exactly one mode (6/6: no missing, no unknown, no duplicate ids).

| JSON path | type | optional? | used by view | meaning | example |
|---|---|---|---|---|---|
| `modes[].label` | string | no | yes | mode name. Fixed raw labels for the automatic set-aside modes: `"Closed-gate firings (silu leak)"`, `"Residue (unreadable)"`; a reviewed residue mode has a free label of the form `"Residue: …"`. With `?public=1` the view **overwrites** these in memory (see 4.6) [V] | `"…"` |
| `modes[].description` | string (29-408 chars) | no (JS treats as optional) | yes | what the mode fires on. The two automatic set-aside modes (closed-gate, unreadable residue) carry fixed texts, not reproduced here [V] | |
| `modes[].subcircuits` | array of subcircuit ids | no (may be `[]`) | yes | member subcircuits (keys of top-level `subcircuits`). Not sorted; the UI sorts them by `share` descending and drops ids missing from `subcircuits` | `["c2","c3","c9","c10","c13"]` |
| `modes[].share` | float 4 dp (JSON int `0` possible) | no | yes | **the percentage shown on the mode row**: firings in the mode / firings of the pole. Equals the sum of its subcircuits' `share` (all 58 modes within 0.0006). Over **all** modes (incl. closed-gate and residue) it sums to 0.9999-1.0 (6/6) [V] | `0.4421` |
| `modes[].n_firings` | int | optional: present on open modes and on reviewed residue modes; **absent** on the automatic closed-gate / `Residue (unreadable)` modes | yes (subtitle, only together with `unique_contexts`) | number of firings in the mode = sum of member `n` (all 49 present values) [V] | `2201` |
| `modes[].unique_contexts` | int or `null` | optional key; `null` in 45 of 49 | yes | distinct 16-token contexts among the mode's firings; shown in the mode subtitle when not null and `n_firings` > 0. Populated in `modes[]` only for a merged mode (3,217) and for the empty modes of `L4_E1_N345__neg` (0); populated for every open mode in `modes_before_unify[]` [V] | `3217` |
| `modes[].top_context_share` | float or `null` | optional key; `null` in 45 of 49 | yes | fraction of the mode's firings that fall in its most frequent context. If >= 0.5 the view adds a repeated-context warning tag with a tooltip (hidden in public) [V] | `0.0012` |
| `modes[].merged_from` | array of strings or `null` | optional key (46 of 58); list in 3 | yes (hidden in public) | labels of the earlier modes (from `modes_before_unify`) that the review combined into this one. Shown as "combines K earlier modes", with a tooltip listing them, when length > 1 [V] | `["…", "…"]` |
| `modes[].kl` | object or `null` | nullable (`null` for the 3 empty modes of `L4_E1_N345__neg`) | yes | damage of the mode | `{"per_firing":0.00065,"ratio":2.238}` |
| `modes[].kl.per_firing` | float (nats) | no | yes (tooltip, full atlas only) | mean KL damage per firing in this mode = n-weighted mean of member subcircuits' `kl.per_firing` (0 mismatches) [V] | `0.0006528853573386932` |
| `modes[].kl.ratio` | float | no (JS: bar omitted if `null`) | yes | `per_firing / kl_neuron_mean`: the mode's mean damage per firing as a multiple of the pole-wide mean (1 = average). `sum(share*ratio)` over all modes = 1.0 [V]. Sample range 0.00018-8.06 | `2.2380137078200457` |
| `modes[].residue` | bool (`true`) | **key absent on open modes** | yes | set-aside mode (closed-gate or residue). 10 of 58 | `true` |
| `modes[].closed_gate` | bool (`true`) | key absent unless closed-gate mode | yes (public relabel only) | the closed-gate mode; always together with `residue:true`. Its members are exactly the subcircuits with `profile.closed` > 0.5 (64/64, and no majority-closed subcircuit sits elsewhere) [V on samples] | `true` |
| `modes[].residue_source` | string | optional (1 of 58) | no | who set this residue mode aside; `"long-context review"` [V value] | `"long-context review"` |

There is no stable mode id. A mode is addressed by its **array index** in `modes[]` (URL hash, `&mode=`, postMessage). Because open
modes come first, index k < (number of open modes) is also the k-th entry of the index file's `modes[]` and the k-th open mode on
the Overview tab, which keeps only the `modes[]` entries that have `subcircuits` and no `residue` [V].

### 3.3 `subcircuits.{cK}`

170 objects in the samples; every key below is present on every one (no optional keys observed).

| JSON path | type | used by view | meaning | example |
|---|---|---|---|---|
| `.id` | string | yes | same as the key (`c0`...) | `"c6"` |
| `.n` | int (20-2,040) | yes ("N firings") | number of the pole's sampled firings in this subcircuit | `213` |
| `.share` | float 4 dp (0.004-0.408) | yes | `n / sum(n over all subcircuits)` (170/170). Note the denominator is the assigned firings, not `n_firings`, when they differ [V] | `0.0429` |
| `.profile` | object `{ge_theta, mid, weak, closed}` | **no** (function `profHtml` exists but is never called; only the legend row "Strength profile" remains) | fractions of this subcircuit's firings by strength bin; the four values sum to 1.0 (170/170). Bins in units of θ: `ge_theta` a_rel ≥ 1, `mid` 0.5 ≤ a_rel < 1, `weak` a_rel < 0.5, `closed` = gate closed. As with the judge bins, the three strength bins are open-gate firings; `closed` = closed-gate firings of any strength [V] | `{"ge_theta":0.549,"mid":0.150,"weak":0.117,"closed":0.183}` |
| `.median_a_rel` | float 3 dp (0.497-13.9) | no | median firing strength in units of θ; always inside the range of the 4 "median" exemplars (170/170) [V] | `1.541` |
| `.meaning` | string (160-628 chars) | yes | Opus's text-only reading of what the firings have in common ("Text reading"). In public mode this is the only prose shown on the card. Can open with a hedge or state that nothing is shared | |
| `.token_pattern` | string (98-343 chars) | yes ("Token pattern"; hidden in public) | describes the token at which the pole fires | |
| `.readable` | bool (143 true / 27 false) | yes | `false` = the text reading failed (card header shows "Unreadable"). Unreadable subcircuits sit in the closed-gate mode (21) or a residue mode (6); readable ones can still be in those modes (43 in closed-gate, 4 in residue) [V] | `true` |
| `.reading_confidence` | int, observed 2-5 | no | confidence of the text reading [I: 1-5 scale] | `2` |
| `.function` | string (297-733 chars) | yes ("Circuit" paragraph; hidden in public unless `meaning` is empty) | Opus's function statement, based on the circuit tables. May cite unit ids inline | |
| `.function_label` | string (40-94 chars) | yes (card title; subcircuit rows) | short name of the subcircuit; the UI shows a placeholder when it is empty | `"…"` |
| `.gate_role` | string (75-293 chars) | yes ("Gate"; hidden in public) | what opens / opposes the gate lane; may cite unit ids, e.g. "(L3_E38_N133, L29_E44_N186)" | |
| `.up_role` | string (87-298 chars) | yes ("Up"; hidden in public) | what drives / opposes the up lane; may cite unit ids | |
| `.function_confidence` | int, observed 1-5 | no | confidence of the function statement [I] | `3` |
| `.circuit_desc` | string or `null` (always `null` in samples) | yes if non-null ("Circuit description") | optional extra description | `null` |
| `.circtext_desc` | string or `null` (always `null` in samples) | yes if non-null ("Circuit and text description") | optional extra description | `null` |
| `.spec_flags` | array or `null` (list in 31 of 170, length 1-2) | no | rejected alternative readings: `[{rejected: string, p: float, p_after: float}]`. `p` observed 0.03-0.43, `p_after` 0.0-1.0. [I] `rejected` = a candidate description that was tested and rejected, `p`/`p_after` = its score before/after a re-check. `rejected` is cut at 300 chars (29 of 56 are exactly 300 long) [V] | `[{"rejected":"…","p":0.08,"p_after":0.0}]` |
| `.circuit` | object with exactly 5 keys | yes (lazy, in "Circuit evidence"; hidden in public) | see 3.4 | |
| `.unique_contexts` | int | no | distinct 16-token contexts among this subcircuit's firings (<= `n`; equal to `n` in 163/170) | `213` |
| `.top_context_share` | float 4 dp | no | share of firings in the most common context (= `round(1/n, 4)` whenever `unique_contexts == n`) [V] | `0.0047` |
| `.exemplars` | array, **always 12** | yes | see 3.5 | |
| `.kl` | object `{per_firing, ratio, n_measured}` | yes (`ratio`, `per_firing`) | damage of this subcircuit. `per_firing` in nats (5e-8 to 0.019, never negative), `ratio = per_firing / kl_neuron_mean` (0.00018-12.7). `n_measured` (int 5-101, not used by the view): [I] number of this subcircuit's firings on which the ablation KL was actually measured; per pole only 179-415 firings in total, so per-subcircuit ratios rest on few measurements | `{"per_firing":1.21e-05,"ratio":1.1098,"n_measured":6}` |

### 3.4 `subcircuits.{cK}.circuit` : links to OTHER neurons (cross-neuron graph)

Exactly five arrays, each with exactly 10 rows in every sample (850-2,000 rows per pole):

| key | UI title | direction | `leaf` sign |
|---|---|---|---|
| `gate_drivers` | Gate drivers | upstream unit -> this pole's gate | always > 0 (0.0006-0.563) |
| `gate_opposers` | Gate opposers | upstream unit -> this pole's gate | always > 0 (0.0001-0.066): stored as a magnitude |
| `up_drivers` | Up drivers | upstream unit -> this pole's up lane | always > 0 (0.0005-0.913) |
| `up_opposers` | Up opposers | upstream unit -> this pole's up lane | always > 0 (0.0001-0.088): magnitude |
| `readers` | Readers | this pole -> downstream unit | signed (-2.02 to +1.86; 880 negative, 820 positive) |

Row shape (8,500/8,500 rows have exactly these four keys):

| JSON path | type | meaning | example |
|---|---|---|---|
| `...circuit.{table}[].unit` | string `L{layer}_E{expert}_N{neuron}` (8,500/8,500) | **the other neuron's id (did form, no pole suffix)**. Upstream tables: always a strictly earlier layer than the pole (6,800/6,800; layers 0-29 seen). Readers: always a strictly later layer (1,700/1,700; layers 5-39 seen). Units come from all 40 layers (0-39), i.e. mostly from outside the 8 atlas layers: of 7,182 distinct units in the samples only 2 are neurons in `subcircuit_modes_index.json` [V] | `"L18_E253_N170"` |
| `...[].leaf` | float 4 dp | mean over f ∈ cK of the unit's contribution at firing f | `0.0164` |
| `...[].rest` | float 4 dp, always >= 0 in samples (also for readers) | same mean over f ∉ cK, f among this pole's sampled firings (baseline for `leaf`) | `0.0001` |
| `...[].label` | string | the unit's autointerp label, **truncated to 220 characters** (8,223 of 8,500 are exactly 220 long), or the literal `"(no label)"` (206 rows). UI column "Autointerp label" | `"…"` |

Further facts [V]:

- Lane-table (`gate_*`, `up_*`) values have a per-expert baseline subtracted (stated in the page's evidence note).
- Units are unique within one table, but the same unit often appears in more than one table of the same subcircuit (165 of 170
  subcircuits), e.g. as a gate driver and an up opposer, so an edge key must include the table name.
- Row order: approximately descending contrast `|leaf| - rest` (exactly so in 88 % of the upstream tables and 63 % of the reader
  tables, the rest being consistent with 4-dp rounding); not strictly descending `|leaf|` [V observation, I rule].
- Within a pole, 8-22 % of the rows reuse a unit that already appears in another table or another subcircuit of that pole (e.g. 766
  distinct units in 850 rows; one unit recurs up to 10 times), which gives a usable subcircuit-to-subcircuit similarity.
- Free-text cross references: `function`, `gate_role`, `up_role` cite unit ids inline (112 mentions in the samples; every one is also
  a row of that subcircuit's tables). A regex for the unit-id format extracts them.
- There is **no** field in this file linking to another pole (pid) or to another pole's subcircuits; links are neuron-level only.

Suggested edge records:

```
upstream:   (src = unit, dst = pid, dst_subcircuit = cK, lane = gate|up, role = driver|opposer, weight = leaf, baseline = rest, src_label = label)
downstream: (src = pid, src_subcircuit = cK, dst = unit, role = reader, weight = leaf (signed), baseline = rest, dst_label = label)
```

### 3.5 `subcircuits.{cK}.exemplars[]`

2,040 exemplars; all have exactly the keys `a, a_rel, closed, fold, pre, tokens, focal, repeats` (no optional keys).

| JSON path | type | meaning | example |
|---|---|---|---|
| `.a` | float 4 dp, signed (-5.59 to 6.79) | raw activation of the neuron at the firing token. Its sign matches the pole (2,038/2,040; the two exceptions are near-zero firings with \|a\| <= 0.01). Not used by the view [V] | `2.6349` |
| `.a_rel` | float 3 dp, always > 0 (0.011-23.29) | firing strength in units of θ in the pole's direction: `abs(a) / theta` (within rounding, 2,040/2,040). The exemplar header shows "Peak X× threshold" from this field (0 decimals if >= 10, else 1) [V] | `8.254` |
| `.closed` | bool (798 true) | gate pre-activation < 0 at this firing. Independent of strength: 373 closed exemplars have `a_rel >= 1` [V] | `false` |
| `.fold` | `"fit"` (1,548) or `"audit"` (492) | discovery fold vs held-out audit fold. The UI treats anything that is not `"audit"` as fit | `"fit"` |
| `.pre` | string, 0-300 chars | the text immediately before the token window, at most the **last 300 characters** (1,297 are exactly 300; it can start mid-word). Empty (92) when the window starts at the document start. Rendered in faint grey before the tokens | `"…"` |
| `.tokens` | array of **33** `[text, value]` pairs (2,040/2,040) | the shaded 33-token window centred on the firing token (index `focal` = 16) | `[["…",null],...,["…",8.223],...]` |
| `.tokens[i][0]` | string | decoded token text (leading spaces and `\n` kept). `""` in 1,174 tokens: left padding when the firing is < 16 tokens from the document start (leading run), or byte-level fragments of multi-byte characters. Right padding past the document end appears as the literal `"<\|im_end\|>"` (335 tokens, only after the focal index). May contain control characters (binary blobs) | `"…"` |
| `.tokens[i][1]` | float 3 dp or `null` | the neuron's activation at that token **in units of θ, raw sign** (multiply by `sign` for the pole's direction; negative product = opposite pole). `null` = "expert not routed" at that token (68 % of tokens: the MoE router did not select this expert, so the neuron has no activation there). Sample range in pole direction -13.6 to +23.2 | `8.223` |
| `.focal` | int, always `16` | index of the firing token in `tokens`. The focal token always has a non-null value (2,040/2,040) and its sign always matches the pole | `16` |
| `.repeats` | int >= 1 (1 in 2,036; 2-4 in 4) | #{f ∈ cK : context(f) = context(this exemplar)}, context as in section 1; tag "×N same context" when > 1 (hidden in public) | `1` |

Order inside the array [V, 170/170]: indices 0-3 = strongest, sorted by `a_rel` descending; 4-7 = median group, descending;
8-11 = weakest, sorted **ascending** (index 8 is the weakest). All of group 0 >= group 1 >= group 2. There is no explicit bin field on
an exemplar; derive it as: `closed` if `closed`, else `ge1` if `a_rel` >= 1, else `mid` if `a_rel` >= 0.5, else `weak`.

Example (tokens abridged, text replaced by "…"):

```json
{"a": 2.6349, "a_rel": 8.254, "closed": false, "fold": "fit",
 "pre": "…",
 "tokens": [["…", null], "... 12 more ...", ["…", -0.071], ["…", 0.309], ["…", null],
            ["…", 8.223], ["…", null], "... 15 more ..."],
 "focal": 16, "repeats": 1}
```

---

## 4. How the UI renders it

Two layouts from the same code: **standalone** (own top bar with title and model name, Qwen3.6-35B-A3B; left rail of poles,
neuron header) and **shell** (`?shell=1`: top bar, rail, id row and the whole neuron header are not rendered; two independently scrolling
columns). The public atlas always embeds it as `?shell=1&public=1`.

### 4.1 Neuron header (standalone only; `headHtml`) [V]

- Kicker with layer, expert, neuron and pole sign; id `L30_E47_N449` + faint `__pos`; chips: `verdict`, `fn-mono`
  (if `fn_mono`), `ground truth` (if in `GT_ORDER`); buttons "↑ previous" / "next ↓" and "i of N" (dead, see 9.1).
- `summary` paragraph; block "Autointerp label" = `label`; disclosure "How to read this page" = `HOW_HTML`.
- Stat chips with tooltips: `θ` (`theta`, 4 dp), `closed-gate share` (`closed_share`, pct1 format of 4.2), `firings` (`n_firings`), `modes`
  (count of non-residue modes, " + residue" if any), `subcircuits` (key count), `partition`.
- Card "Held-out matching accuracy / chance" with the caption `JUDGE_CAP` and a table: columns Strength | n | Opus | Jev; one row per
  bin of the constant `BINS` (`all`, `ge1`, `mid`, `weak`, `closed`; labelled with the θ ranges of 3.3) that exists for either judge;
  each cell `acc% / chance%` plus a bar (accuracy) with a tick (chance); "–" when the judge has no entry.

### 4.2 Modes column [V]

Shell layout (`modeListHtml`), top to bottom:

1. Heading "`K` mode(s)" where K = number of non-residue modes, with an info icon whose tooltip is `HOW_PUBLIC` (public) or `HOW_HTML`.
2. **The mode map `.mmap`**: a flex row, 16 px high (20 px standalone), 2 px gaps. One `<span>` per entry of `modes[]`, in file order,
   with width proportional to the mode's firing share (flex-grow = max(share, 0.004), CSS min-width 3 px), rounded
   2 px. Colour = the pole colour (`--pc`: pos `#C94A12`, neg `#33698A`); non-residue modes alternate between full opacity and class
   `alt` (opacity .55) so neighbours are distinguishable (the alternation counter skips residue modes); residue and closed-gate
   modes are grey (`--resid` `#CFD0CD`). Tooltip per segment: the mode label and its share (pct1). Click selects that mode. The standalone
   layout adds a one-line caption under the map.
3. Row **"All subcircuits"** / "`N` subcircuits" / **"100%"**: N = number of keys in `subcircuits`; "100%" is a hard-coded string
   (it is not computed). E.g. "All subcircuits · 17 subcircuits · 100%" for `L30_E47_N449__pos`.
4. One row per mode: `label`, "`n` subcircuit(s)" (length of `modes[i].subcircuits`), and on the right `modes[i].share` in pct1 format.
   pct1(x): `null` -> "–"; 0 < x < 0.001 -> "<0.1%"; else 100·x with one decimal and "%". These percentages are shares of the pole's
   firings and, over all rows including "Q3/Q4 firings" and "Unassigned examples", add up to ~100 % (99.99-100.0 in samples).
   Residue rows are greyed.

Standalone layout (`modesHtml`): the same content as cards: kicker "[Residue · ]n subcircuits" + share, name, a share bar, the damage
bar (4.4), the description (clamped to 3 lines unless selected) and, on the selected card, the list of its subcircuits
(`id`, `function_label`, `pct1(share)`; click scrolls to that card and opens it).

### 4.3 Subcircuit column [V]

- Title block (`subbarTitle`): for "all": "All subcircuits" + "All modes · N subcircuits". For a mode: `label`, then
  a subtitle with the residue prefix, subcircuit count and firing share, plus, where they apply, the distinct-context count, the
  merged-mode count and the repeated-context tag.
- Shell only (`modeInfoHtml`): for "all": `summary`; disclosure "Autointerp label" (`label`); block "Held-out simulation test" with
  tiles Opus / Jev / Chance from the `all` bin and a disclosure "By firing strength" (the judge table); "Ground-truth neuron" line for
  GT pids. All but `summary` are hidden in public. For a mode: its `description` and its damage bar.
- Heading "Subcircuits" + count, filter box, "Collapse all" button; then the legend:
  - "Shading": colour ramp from `0` to `3θ` (public: `3×`), plus swatches for the opposite sign, the firing token (ink outline)
    and expert not routed (diagonal hatch).
  - "Strength profile": swatches for the four bins of 3.3 (row hidden in public; no bar is drawn in this build).
- Cards: with "all" selected, grouped per mode in `modes[]` order under a heading (`label`; "n subcircuits · X% of firings"), groups
  with no visible card omitted; otherwise only the selected mode. Within a mode, subcircuits sorted by `share` descending.

### 4.4 Damage bar (`klHtml`) [V]

Shown for a mode or a subcircuit when `kl.ratio` is not null. Label "Relative causal effect" (public: "Damage per firing"). The bar is
centred on 1×: x = log2(ratio) limited to [-3, 3] (1/8× to 8×), and the fill runs from the centre to 50 % + (x/3)·50 %. Value text:
ratio >= 10 -> 0 decimals; >= 1 -> 1 decimal; >= 0.01 -> 2 decimals; else "<0.01"; suffixed "× avg" (e.g. "2.2× avg").

### 4.5 Subcircuit card and exemplars (`cardHtml`, `exHtml`, `tokHtml`) [V]

- Header: title `function_label`; right-hand line "`id` · [Unreadable · ]`n` firings · `pct1(share)`" (hidden in public); chevron to
  collapse.
- Body, full atlas: "Circuit" = `function` (or `meaning` if no function); "Gate" = `gate_role`; "Up" = `up_role`; "Text reading" =
  `meaning`; "Token pattern" = `token_pattern`; "Relative causal effect" bar. Public: only one paragraph (`meaning`, else `function`) and the
  "Damage per firing" bar.
- Exemplars: group "Strongest" (indices 0-3) always visible; a "more examples" button reveals the median group (4-7) and
  "Weakest" (8-11). If a subcircuit did not have exactly 12, the rest would be shown under "More".
- Each exemplar: header "Peak `a_rel`× threshold" + tags: `closed` -> "closed gate" (public: "Q3/Q4", with an explanatory tooltip);
  `repeats` > 1 -> "×N same context"; fold -> "held-out" / "fit"
  (the last two hidden in public). Then a text box (max-height 140 px, initially scrolled to centre the firing token vertically) containing
  `pre` in faint grey followed by the 33 tokens.
- Token rendering: empty-string tokens are skipped unless focal (then "∅"); `\n` shown as "↵" + line break; U+FFFD is marked faint
  with a tooltip. A null value -> hatched, tooltip "expert not routed". Otherwise, with p = value × `sign` (pole direction):
  opacity 0.85 · min(|p|, 3) / 3 of the pole colour (opposite-pole colour when p < 0), i.e. the shading saturates at 3θ;
  focal token gets an ink outline; tooltip "a/θ = p.pp" (public: "p.pp× threshold") plus " · firing token".
- "Circuit evidence" (collapsed `<details>`, built on first open, hidden in public): optional `circuit_desc` / `circtext_desc`,
  an evidence note defining the columns (3.4), four tables (2 x 2 on wide screens) and the Readers table. Columns
  Unit | Autointerp label | Leaf | Rest; numbers with 4 decimals; a bar under leaf = |leaf| / max |leaf| in that table; clicking a
  row un-clamps its label.

### 4.6 What `?public=1` hides compared with the full atlas [V]

`?public=1` adds the class `public` to `<body>` and hides the elements listed below. **The JSON is identical; only the presentation
changes.** Everything hidden is still in `subcircuit_modes_data/{pid}.json`.

Most of the hiding is one CSS rule (`display:none`) on elements under `body.public`, so those parts stay in the DOM; a few (e.g. the
card's "Circuit" paragraph) are not rendered at all in public mode.

| Hidden in public | Data behind it |
|---|---|
| Held-out simulation test tiles, "By firing strength" table, ground-truth line | `judge` |
| "Autointerp label" | `label` |
| Card size line (subcircuit id, "Unreadable", firings, share) | `subcircuits.*.id/readable/n/share` |
| "Circuit" paragraph, "Gate", "Up", "Text reading" heading, "Token pattern" | `function`, `gate_role`, `up_role`, `token_pattern` |
| "Circuit evidence" tables | `circuit.*`, `circuit_desc`, `circtext_desc` |
| Strength-profile legend row | `profile` |
| fold tags "fit"/"held-out", "×N same context", "one repeated context" tag | `fold`, `repeats`, `top_context_share` |
| "combines K earlier modes" | `merged_from` |

Text changes in public: mode labels are rewritten in memory, `closed_gate` -> **"Q3/Q4 firings (gate input negative)"**, other `residue`
-> **"Unassigned examples"** (so the raw labels "Closed-gate firings (silu leak)" / "Residue (unreadable)" / "Residue: ..." never
show); the "Residue · " kicker prefix is dropped; "θ" wording becomes "× threshold" / "3×"; "Relative causal effect" becomes "Damage
per firing"; "closed gate" tag becomes "Q3/Q4"; the filter placeholder no longer names token text or unit ids as searchable; reading notes switch
from `HOW_HTML` to `HOW_PUBLIC`.

`HOW_HTML` (full atlas) and `HOW_PUBLIC` (public) are the page's built-in reading notes (HTML strings); their text is not reproduced here.

---

## 5. User controls and URL parameters

### 5.1 URL parameters and hash (read once at load) [V]

| Param | Values | Effect |
|---|---|---|
| `?pole=` | pid, e.g. `L4_E1_N212__pos` | pole to open. Takes precedence over the hash. Without a pole (query or hash) the page shows only a short error message. |
| `?public=` | `1` | public presentation (4.6). Any other value = full atlas |
| `?shell=` | `1` | embedded layout; enables the postMessage protocol (5.3) |
| `?data=` | directory, default `subcircuit_modes_data` | where `{pid}.json` is fetched from (trailing slash added if missing) |
| `?mode=` | integer k | initial mode index (set by deep links from the shell); used only when the hash does not name the same pole. Out-of-range values fall back to mode 0 |
| `?v=` | any | cache buster added by the shell; not read by the page |
| `#` hash | `L30-E47-N449-pos`, `...-all`, `...-m3` | format `L{layer}-E{expert}-N{neuron}-{sign}` (sign `pos` or `neg`) with an optional suffix `-all` or `-m{K}`. No suffix = mode index 0; `-mK` = mode index K-1 (1-based in the hash, so `-m1` is never written); `-all` = all subcircuits. The page rewrites the hash on every selection (`history.replaceState`) and listens to `hashchange` |

Initial mode: hash mode if the hash names the opened pole; else `?mode=`; else `"all"` in the shell and `0` standalone.
Shell embed URL: `subcircuit_modes_view-mobile-v2.html?shell=1&public=1&v=20261109C&pole=<pid>&data=subcircuit_modes_data`.

### 5.2 On-page controls [V]

| Control | What it does |
|---|---|
| Mode rows / mode cards / mode-map segments | select a mode (index into `modes[]`) or "All subcircuits"; re-renders the title, mode info, cards; updates the hash; Enter/Space works on a focused mode |
| Info icon next to "K modes" | shows the reading notes (`HOW_PUBLIC` / `HOW_HTML`) as a tooltip |
| Filter box `#fq` | free-text **substring** filter (case-insensitive, 140 ms debounce) over the subcircuits of the current selection. Haystack per subcircuit: `id`, `function_label`, `meaning`, `token_pattern`, `function`, `gate_role`, `up_role`, all circuit `unit` ids, and the concatenated token text of the 12 exemplars (not `pre`, not unit labels). Counter "x of N"; an empty result shows a no-match message. In public mode it still searches the hidden fields |
| "Collapse all" / "Expand all" | closes or opens every visible card |
| Card header | collapse/expand one card |
| "8 more examples…" / "Fewer examples" | show exemplars 4-11 |
| "Circuit evidence" | open the tables (full atlas only); a row click expands its label |
| Subcircuit rows in a selected mode card (standalone) | scroll to that card |
| Standalone rail filters | Layer, Pole (Both / Positive / Negative), Class (All / Mono / Functionally mono / Poly), Mode count (No readable mode, 1, 2, 3, 4-5, 6-9, 10+), "Search id or label", "Clear filters". **Inoperative in this release** because the rail list is empty (9.1) |
| Keys | standalone: `j` / `k` next / previous pole (dead with an empty list), `Esc` closes the rail drawer. In the shell: `j k r R [ 1 2 3 4 p ? /` are forwarded to the parent |

There is **no strength-bin filter** for cards or exemplars in this build: the bins (`all`, `ge1`, `mid`, `weak`, `closed`) only
label the rows of the judge table and the (unused) strength profile.

### 5.3 postMessage protocol with the shell (only when `?shell=1`; target origin `"*"`, no origin check) [V]

| Direction | Message | When / effect |
|---|---|---|
| view -> parent | `{type:"modes-mode", pid:"<pid>", mode:<int or null>}` | sent on a mode selection in the view. `mode` = index into `modes[]`, or `null` when "All subcircuits" **or a residue/closed-gate mode** is selected. Not sent when the change came from the shell. The shell ignores it if `pid` is not the open pole, stores it as its selected mode (`S.mode`), puts it in its URL as `&mode=` and forwards it to the Overview tab |
| view -> parent | `{type:"shell-key", key:"<key>"}` | one of `j k r R [ 1 2 3 4 p ? /` pressed outside an input (default prevented) |
| parent -> view | `{type:"shell-mode", mode:<int or null>}` | select that mode if it is an integer within `modes[]` and **not** a residue mode; otherwise select "all". Sent by the shell after the frame loads and whenever another tab changes the mode |

The shell's selected mode (`S.mode`, shell.md) is this same `modes[]` index (order as in 3.2).

---

## 6. Text fields worth indexing for search

Ordered by value for keyword / semantic search.

| Path | Volume | Notes |
|---|---|---|
| index `poles[pid].modes[].label` | 22,620 labels for 5,477 poles | global, cheap, the best "what does this pole do" text; open modes only |
| `modes[].label`, `modes[].description` | per pole 3-21 modes | includes the set-aside modes; map the two fixed raw labels to "Q3/Q4 firings (gate input negative)" / "Unassigned examples" for display |
| `summary`, `unified_description`, `label` | 3 paragraphs per pole | `unified_description` is the densest single description (marks firing tokens with «»); `label` is the older autointerp label and can disagree with the modes |
| `subcircuits.*.function_label` | 1 short title per subcircuit | good result title |
| `subcircuits.*.meaning`, `.token_pattern`, `.function`, `.gate_role`, `.up_role` | 5 paragraphs per subcircuit | `meaning`/`token_pattern` = text view; `function`/roles = circuit view (contain unit ids) |
| `subcircuits.*.exemplars[].pre` + token texts | 12 passages per subcircuit, up to 300 chars + 33 tokens | real corpus text in many languages; index with the focal token marked |
| `subcircuits.*.circuit.*[].label` | 50 labels per subcircuit (<= 220 chars, truncated) | autointerp labels of neighbouring neurons; useful for "which poles are driven by X-like features" |
| `modes[].merged_from[]`, `modes_before_unify[].label/description` | optional | earlier, finer-grained mode names |
| `subcircuits.*.spec_flags[].rejected` | optional | rejected readings; index separately (they describe what the subcircuit is **not**) |

---

## 7. Numeric / structural content usable for analysis

No 2-D/3-D coordinates or embedding vectors exist in these files. What is available:

- **Bipartite hierarchy** pole -> modes -> subcircuits (`modes[].subcircuits` -> `subcircuits` keys), with firing mass on every node:
  `subcircuits.*.n`, `.share`, `modes[].share`, `modes[].n_firings`.
- **Cross-neuron graph** (3.4): per subcircuit 40 upstream edges (4 tables x 10; weight `leaf` > 0, baseline `rest`) from earlier-layer
  neurons and 10 signed downstream edges to later-layer neurons. `leaf - rest` is the contrast that makes a unit specific to the
  subcircuit. A subcircuit x unit matrix (per pole or global) supports clustering/similarity of subcircuits by shared drivers; an
  aggregated pole x unit matrix (weight by `share`) gives a neuron-level graph across layers 0-39.
- **Causal weights**: `kl.per_firing` (nats) and `kl.ratio` on modes and subcircuits, `kl_neuron_mean`, `main_damage_share`; damage
  share of a node = `share * kl.ratio` (sums to 1 over all modes, and over all subcircuits).
- **Strength distributions**: `subcircuits.*.profile` (4-simplex: ge_theta, mid, weak, closed), `median_a_rel`, `closed_share`,
  `theta`; exemplar `a`, `a_rel`, `closed`.
- **Activation windows**: `exemplars[].tokens` = 33-long sequences of θ-normalised activations with `null` = expert not routed
  (the null pattern is itself MoE routing information for this expert around the firing).
- **Reliability signals**: `judge.*.*.{n,acc,chance}` (accuracy minus chance per strength bin), `reading_confidence`,
  `function_confidence`, `readable`, `kl.n_measured`, `unique_contexts`, `top_context_share`, `repeats`, `fold`.
- **Global distributions** from the index alone: verdict / fn_mono / n_modes / n_subcircuits / mode-share spectra by layer and expert.

---

## 8. Reconstructing an example as plain text

```python
def exemplar_text(e, sign, theta=None):
    toks = e["tokens"]; f = e["focal"]                       # f == 16
    keep = [(i, t, v) for i, (t, v) in enumerate(toks)
            if t != "<|im_end|>" and (t != "" or i == f)]    # drop right padding and empty/pad tokens (the UI also skips "")
    left  = "".join(t for i, t, v in keep if i < f)
    focal = toks[f][0] or "∅"
    right = "".join(t for i, t, v in keep if i > f)
    plain  = e["pre"] + left + focal + right                 # tokens concatenate without separators; spaces are inside tokens
    marked = e["pre"] + left + "«" + focal + "»" + right     # same convention as the dataset's own descriptions
    strength_theta = e["a_rel"]                              # authoritative firing strength, units of θ, > 0 in pole direction
    raw_activation = e["a"]                                  # signed raw units; a_rel ≈ abs(a) / theta
    per_token = [None if v is None else v * sign for _, v in toks]   # θ units in pole direction; None = expert not routed
    return plain, marked, strength_theta, raw_activation, per_token
```

- `pre` is the text before `tokens[0]`, cut to its last 300 characters; `pre` + tokens read as continuous text (checked by eye on
  several exemplars). Prefix an ellipsis when `len(pre) == 300`.
- Use `a_rel` (or `a`) for the firing strength, **not** `tokens[focal][1]` (see 9.3).
- The exemplar belongs to subcircuit `cK`; its mode is the `modes[]` entry whose `subcircuits` contains `cK`.

---

## 9. Gotchas / variation across samples

1. **The view's built-in pole list is empty.** The constant `IDX` is an empty array, so `ORDER` is empty: the standalone rail shows no poles, its
   filters, `j`/`k` and previous/next do nothing, and the rail fields the code expects (`n_meanings`, `label`, `layer`, `expert`,
   `neuron`, `sign`, `n_residue`, `n_subcircuits` per pole) are not shipped in this page. Use `subcircuit_modes_index.json` (note it
   calls the count `n_modes`, not `n_meanings`, and has no `label`/`sign`/`layer` fields; parse them from the pid).
2. **Set-aside modes carry raw labels.** `"Closed-gate firings (silu leak)"` and `"Residue (unreadable)"` are what the JSON holds;
   "Q3/Q4 firings (gate input negative)" and "Unassigned examples" exist only as a public-mode rewrite in the browser. A pole can
   have more than one residue mode (e.g. `L4_E1_N177__pos`: a reviewed "Residue: …" mode + "Residue
   (unreadable)"), which the public UI shows as two rows both called "Unassigned examples". Index `n_residue` ranges 0-5.
3. **`tokens[focal][1]` is not the exact firing strength.** It should approximate `a_rel * sign` (median ratio 0.998) but the focal
   values are visibly coarse: a few values recur across unrelated exemplars (e.g. 0.459 / 0.783 / 1.548 in `L30_E47_N449__pos`;
   1.548 occurs in every sample file), and for weak firings the stored value can be far off (`a_rel` 0.011 with focal value 0.459).
   The card header uses `a_rel`; the shading/tooltip of the boxed token uses the token value, so the two can disagree on the page.
4. **Shares use assigned firings.** `subcircuits.*.share = n / sum(n)`; `sum(n)` can be slightly below `n_firings` (4,978 and 4,968
   of 5,000 in two samples), and `closed_share` then differs slightly from the n-weighted `profile.closed`.
5. **Index mode shares do not sum to 1** (open modes only; mean 0.68). Per-pole mode shares over all modes do (to 4-dp rounding).
6. **Label-only reads.** `L4_E1_N345__neg` (closed_share 0.992): its `summary` confirms that the merge had `label` as its
   only input. Its 3 open modes have `subcircuits: []`, `share: 0`, `n_firings: 0`,
   `unique_contexts: 0`, `top_context_share: 1.0`, `kl: null`; all 38 subcircuits sit in the closed-gate mode; `main_damage_share`
   is `null`; `modes_before_unify`, `verdict_before_unify`, `review_verdict`, `verdict_source`, `unified_description` are absent.
   The index still lists it with `n_modes: 3`. Selecting such a mode in the UI shows the filter's no-match message and (full
   atlas) a bogus repeated-context tag at 100 %. The index has 34 zero-share modes in 28 poles; treat a mode with
   empty `subcircuits` as unverified.
7. **Zero-mode poles.** 51 index entries have `n_modes: 0` (`modes: []`, `main_damage_share: null`; 10 of them even say
   `monosemantic`). The shell treats them as failed reads and shows the Islands view instead when islands exist.
8. **Optional / nullable keys**: `modes[].residue`, `.closed_gate`, `.residue_source`, `.merged_from`, `.n_firings`,
   `.unique_contexts`, `.top_context_share` (keys may be absent, not just null); `modes[].kl` may be `null`; `judge.opus` may be
   `{}` and judge bins may be missing; `review_verdict` is absent on some poles. `share` may be the JSON integer `0`.
9. **Mode identity is positional.** No ids; index k into `modes[]`. In the hash it is 1-based (`-m{k+1}`), in `?mode=` and in
   postMessage it is 0-based. Residue modes can be selected in the page but are reported to the shell as `null`.
10. **Unit labels are truncated at 220 characters** and 206 of 8,500 are `"(no label)"`; full labels must come from another source.
    Units are neuron ids without a pole, and almost all lie outside the atlas's own pole set (layers 0-39 vs atlas layers
    4/9/14/19/24/30/36/39), so most graph nodes have no atlas page.
11. **Opposer weights are positive magnitudes**; only `readers[].leaf` is signed. `rest` is never negative in the samples, even for
    readers with negative `leaf`, so it may be a magnitude; do not subtract it blindly for readers [I].
12. **Token text quirks**: `""` tokens (padding before the document start and byte fragments), literal `"<|im_end|>"` padding after
    the document end, control characters from binary content, text in many scripts. No U+FFFD and no lone surrogates were found in the
    samples, but the page has explicit handling for U+FFFD, so expect it elsewhere.
13. **`null` token values are routing, not zero.** 68 % of window tokens are `null` ("expert not routed"); do not coerce to 0 when
    computing statistics.
14. **`a` can have the "wrong" sign** for near-zero firings (2 of 2,040); `a_rel` is always positive.
15. **Per-subcircuit damage is thinly measured** (`kl.n_measured` 5-101; 179-415 measured firings per pole), so `kl.ratio` on small
    subcircuits/modes is noisy [I on interpretation of `n_measured`].
16. **Public vs full is presentation only.** The published JSON already contains every field the public view hides (`judge`, `label`,
    `theta`, `profile`, subcircuit ids, `function` / `gate_role` / `up_role` / `token_pattern`, `circuit`, `fold`).
17. **Encoding differs between files**: per-pole files are ASCII with `\uXXXX` escapes; the index is raw UTF-8. Both are single-line
    strict JSON. Strings such as "NaN" / "Infinity" occur inside labels only.
18. **Size planning** [I]: 6 samples average 0.82 MB; if representative, all 5,477 pole files are roughly 4-5 GB uncompressed.

### Sample overview

| pid | sign | theta | closed_share | sum(n) / n_firings | verdict | fn_mono | main_damage_share | modes: open + closed-gate + residue | subcircuits | file bytes |
|---|---|---|---|---|---|---|---|---|---|---|
| `L30_E47_N449__pos` | +1 | 0.3192 | 0.3616 | 4,978 / 5,000 | polysemantic | true | 0.99 | 7 + 1 + 1 | 17 | 474,773 |
| `L4_E1_N177__neg` | -1 | 0.2658 | 0.2860 | 5,000 / 5,000 | monosemantic | false | 1.0 | 1 + 1 + 1 | 25 | 737,254 |
| `L4_E1_N177__pos` | +1 | 0.2915 | 0.1778 | 5,000 / 5,000 | polysemantic | true | 1.0 | 2 + 0 + 2 | 20 | 570,637 |
| `L4_E1_N271__pos` | +1 | 0.1476 | 0.5856 | 4,968 / 5,000 | polysemantic | false | 0.188 | 15 + 1 + 1 | 40 | 1,187,444 |
| `L4_E1_N345__neg` | -1 | 0.2170 | 0.9920 | 5,000 / 5,000 | polysemantic | false | null | 3 (empty) + 1 + 0 | 38 | 1,098,066 |
| `L4_E1_N372__neg` | -1 | 0.2233 | 0.2286 | 5,000 / 5,000 | polysemantic | false | 0.344 | 20 + 0 + 1 | 30 | 861,816 |
