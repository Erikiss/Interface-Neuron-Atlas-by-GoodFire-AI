# Qwen3.6 Neuron Atlas (Goodfire) - cross-cutting data census, join keys, sizes, numeric and text inventory

Release base: `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/` (all paths below are relative to it).
Source of this document: the local read-only mirror of 6 view pages + 7 global indices + 30 per-pole / per-neuron sample files. No network access was used.

Legend used everywhere: **[V-JS]** = verified from the view pages' JavaScript (usage or comment), **[V-DATA]** = verified by computation over the local files, **[I]** = inferred from names/values only (no page uses the field, or the page does not explain it). Abbreviations for the per-pole file types: **IS** = islands pole file, **SC** = subcircuit-modes pole file, **LS** = landscape pole file, **NS** = ns11 pole file, **MoE** = circuit-explorer bundle (meta / exemplars / incidence).

---

## 1. Dataset map and join keys

### 1.1 File inventory

| Path pattern | Keyed by | Entries | Loaded by | Content in one line |
|---|---|---|---|---|
| `public_atlas_data/index.json` | global (neuron `did`, 2 poles each) | 3,244 neurons = 6,488 poles | shell (`explorer.html`) boot | master list: did, L/E/N, per pole `pid`, `cat`, `desc`, `islands` flag |
| `public_atlas_data/islands/index.json` | global (pole `pid`) | 6,310 poles | islands page standalone list only | pid, L, pole, name (= `desc` of the master list) |
| `public_atlas_data/islands/poles/{pid}.json` | pid | 6,310 expected | Modes tab fallback (poles without subcircuit read), landing "specimen" | islands (clusters of ~300 strongest firings), examples with +-120 token windows, "opus" modes |
| `subcircuit_modes_index.json` | global (pid) | 5,477 poles | shell boot (`applySC`) | verdict, fn_mono, main_damage_share, counts, mode labels + shares |
| `subcircuit_modes_data/{pid}.json` | pid | 5,477 expected | Modes tab; Overview tab (examples, subcircuit list) | modes, subcircuits (readings, 12 exemplars each, top-10 circuit units per lane), judge test |
| `neuron_landscape_data/atlas_index.json` | global (pid list) | 5,477 pids | shell boot (decides whether the Overview tab exists) | bare JSON array of pids |
| `neuron_landscape_data/{pid}.json` | pid | 5,477 expected | Overview tab (embedded landscape + numbers) | modes (open + Q3/Q4 + unassigned), 5 rarity bands, ~1,000 sampled firings ("dots") with KL damage, mode-similarity matrices |
| `ns11_pages/data/index.json` | global (pid) | 5,477 pids, 585 `sim` scores | shell boot | pid list, scientist model per pid, simulatability score |
| `ns11_pages/headline_overrides.json` | global (pid) | 5,469 | Overview tab | rewritten headline per pole |
| `ns11_pages/data/{pid}.json` | pid | 5,477 expected | Overview tab ("neuron scientist" explanation) | explain{headline, summary, situations, not_this, how_we_know, open}, pick/report, plus a copy of SC exemplars |
| `moe_circuit_explorer_data/index.json` | global (did) | 3,418 entries | circuit page standalone picker only | did, label, n_ex, act range, verdict per pole |
| `moe_circuit_explorer_data/{did}/meta.json` | did (both poles inside) | 3,418 expected | Circuit tab | input/reader groups with member neurons, pole_view, logits, theta |
| `moe_circuit_explorer_data/{did}/exemplars.json` | did | 3,418 expected | Circuit tab | ~250-300 firings with gate_pre/up_pre, 81-token windows |
| `moe_circuit_explorer_data/{did}/incidence.json` | did | 3,418 expected | Circuit tab | exemplar x route matrix |

Referenced by the JavaScript but **absent from the local sample** (existence upstream NOT verified, do not assume):
- `neuron_landscape_data/ctx/{pid}.json` - the Overview page fetches it and tolerates a miss. Comment: "full context (full_context.py): ~200 tokens before, 40 after, every token's activation recomputed". Shape used by the JS: `{modes: {<k>: [{a, s, f, toks:[[text, value|null], ...]}, ...]}}` (k = landscape mode index, `s` = subcircuit id string, `f` = focal index). [V-JS for shape, existence unknown]
- `neuron_landscape_data/index.json` - only the standalone landscape page (no `shell=1`) loads it; rows carry `pid, layer, expert, neuron, sign, label, set, kind, kind_name, uid, n_open, n_closed, coverage`. It also lists "random baseline" units. [V-JS for shape, existence unknown]
- `public_neuron_atlas.html` (link target inside the circuit page), `ns11_pages/explainer.html` (mentioned in a comment only).

### 1.2 Id formats [V-DATA]

| Id | Format | Where |
|---|---|---|
| neuron `did` | `L{layer}_E{expert}_N{neuron}`; layer 0-39, expert 0-255, neuron 0-511 | all files |
| pole `pid` | `{did}__pos` or `{did}__neg` | IS, SC, LS, NS, indices |
| pole key | `"+"` / `"-"` | `public_atlas_data/index.json` `poles`, MoE `pole_view`, MoE `verdict` |
| pole word | `pos`/`neg` (URL `pole=`), `positive`/`negative` (NS `side`, MoE `theta_first.poles`) | - |
| signed unit `uid` | `{did}+` or `{did}-` (ASCII plus/minus appended) | MoE `groups.*.members[].uid`, `subgroups[].units[]` |
| expert id | `L{layer}_E{expert}` | MoE `pole_view.*.inputs[].dominant_expert` |
| subcircuit id | `c{k}` (k from 0) | SC `subcircuits` keys, NS `examples[].band`; integer `k` in NS `islands_view.islands[].id`, NS `explain.contexts` keys (as strings), LS `dots[].s` |
| SC/NS example id | `c{k}_{j}` = exemplar j (0-11) of subcircuit c{k} | NS `examples[].id`, `showcase`, `full_docs` keys |
| MoE example id | `{band}_{regime}_{si}_{focal}` e.g. `top1000_gateopen_4278476_114` (si = corpus sequence index, focal = token position) [V-DATA: 847/847 match] | MoE exemplars, incidence `ex_order`, `pole_view.top_contexts` |
| MoE group id | inputs `g{n}`, `g_single_pd/po/nd/no`; readers `r{n}`, `r_single_pr/pl/nr/nl` | MoE meta |
| MoE route id | `in:{gid}:gate`, `in:{gid}:up`, `rd:{rid}`, `lg:t{token_id}` | incidence |
| display id | `L9/E196/N344` (underscores replaced by slashes) | UI only |
| deep-link hash (subcircuit page) | `#L4-E1-N212-pos`, `-all`, `-m3` (1-based mode) | UI only |

Non-standard ids:
- `moe_circuit_explorer_data/index.json` holds 33 non-standard `did`s: 4 synthetic fixtures `DEMO_HUGE`, `DEMO_SPARSE`, `DEMO_FIXTURE`, `DEMO_LARGE` (labels say "DEMO fixture - synth..."; their `layer/expert/neuron` numbers are fake, e.g. neuron 15330) and 29 method variants of 6 real neurons with suffixes `_taylor`, `_taylor_dir`, `_taylor_hdb`, `_taylor_im`, `_relp_im`, `_relp_im_merged`, `_v2` (labels prefixed `[Taylor edges ...]`, `[RelP - Infomap ...]`). For these, `did != L{layer}_E{expert}_N{neuron}`. None of them is reachable from the public shell.
- "shared expert" poles: the islands page's parser accepts `^L(\d+)_(?:E(\d+)|shared)_N(\d+)__(pos|neg)$` and prints "shared expert" [V-JS], but **no `_shared_` id occurs in any local index or sample** [V-DATA]. Treat as a possible but unobserved format; make the id regex tolerant.
- Layers outside the sampled set: atlas poles live on layers 4, 9, 14, 19, 24, 30, 36, 39 (plus two hand-added neurons on 27 and 35). The MoE index additionally has 142 neurons on other layers (2-39). Circuit references (SC `circuit.*[].unit`, MoE members) span **all layers 0-39** (inputs 0-29, readers 5-39 in the samples) and are almost never atlas neurons themselves (see 3.5).

### 1.3 Exact set sizes, intersections, differences [V-DATA]

Sets: `PA` = public_atlas neurons (3,244 dids; 6,488 poles, both poles always present), `ISL` = islands index (6,310 pids; 3,145 pos + 3,165 neg; 3,244 distinct dids), `X5477` = the identical pid set of `subcircuit_modes_index.json`, `ns11_pages/data/index.json` (`pids` and `model` keys) and `neuron_landscape_data/atlas_index.json` (5,477 pids: 2,740 pos + 2,737 neg; 2,742 dids; all three lists are equal as sets and identically sorted lexicographically), `MOE` = circuit index (3,418 dids, 3,385 standard).

| Relation | Count |
|---|---|
| PA poles with `islands:true` = ISL exactly | 6,310 (ISL minus PA = 0) |
| PA poles with `islands:false` (then `cat:null`, `desc:""`) | 178 |
| PA dids = ISL dids | 3,244 = 3,244 |
| ISL dids with both poles / only neg / only pos | 3,066 / 99 / 79 |
| X5477 dids with both poles / only pos / only neg | 2,735 / 5 / 2 |
| PA poles ∩ X5477 | 5,473; PA minus X5477 = 1,015; X5477 minus PA = 4 (`L39_E120_N508__pos/neg`, `L4_E206_N33__pos/neg`) |
| ISL ∩ X5477 | 5,300; ISL minus X5477 = 1,010 (1,006 in L14_E78 + `L27_E247_N96` x2 + `L35_E88_N30` x2); X5477 minus ISL = 177 |
| PA dids ∩ MOE(std) | 3,243; PA minus MOE = 1 (`L4_E206_N45`); MOE(std) minus PA = 142 (other layers) |
| X5477 dids ∩ MOE | 2,739; missing in MOE: `L39_E120_N508`, `L4_E206_N33`, `L4_E206_N45` |
| MoE index `verdict` keys (did, pole) | 6,310 keys; 6,308 coincide with ISL pids. Only in ISL: `L4_E206_N45__pos/neg` (no MoE entry). Only in MoE: `L19_E33_N499__neg`, `L30_E75_N219__pos`. So a MoE verdict key is a near-exact (not exact) proxy for "this pole has islands"; the 142 MoE neurons outside PA all have `verdict: {}` |
| `headline_overrides.json` | 5,469, subset of X5477; 8 pids without override |
| `ns11 index.sim` keys | 585, subset of X5477 (486 `opus`, 99 `glm`) |
| `ns11 index.model` values | `opus` 5,377, `glm` 100 |

Structure of the three populations [V-DATA + V-JS]:
- **Random sample**: 2,730 neurons on layers 4/9/14/19/24/30/36/39 (336/361/326/346/328/349/362/322 after removing the special sets) = 5,460 poles. Every one of these 5,460 poles is in X5477 (0 missing). 5,287 of them also have islands.
- **Whole-expert set L14_E78**: all 512 neurons of layer 14 expert 78 are in PA and MOE; 1,019 of their poles have islands; only 13 of their poles (10 dids) are in X5477. The shell excludes them. Comment: "not part of the random neuron sample, so kept out of the public atlas (Curt 11-07): the whole-expert L14 E78 set and two hand-added neurons".
- **Hand-added**: `L27_E247_N96`, `L35_E88_N30` (islands only).
- So X5477 = 5,460 + 13 (L14_E78) + 4 (two neurons not in PA).

### 1.4 The public shell shows a fixed 3,000-pole sample [V-JS, re-implemented and V-DATA]

`explorer.html` filters PA to the 2,730 sample neurons, lists their 5,460 poles, sorts them by `rhash(pid)` and keeps the first `SAMPLE_SIZE = 3000`:

```
RSEED = 20261109
rhash(t): h = (2166136261 ^ RSEED) >>> 0
          for each char c: h ^= c; h = imul(h, 16777619) >>> 0
          h ^= h >>> 15; h = imul(h, 2246822507) >>> 0; h ^= h >>> 13; return h >>> 0
order: ascending rhash(pid), ties by pid string
```

Re-implementing this gives 3,000 poles on 2,202 neurons; first five: `L24_E31_N88__neg, L14_E227_N460__pos, L4_E247_N291__pos, L24_E103_N302__neg, L4_E164_N6__pos`. All 3,000 are in X5477; 2,906 have islands; 2,201 of the 2,202 neurons are in the MoE index (missing `L4_E206_N45`); 334 have a `sim` score. Per layer: 364/389/352/374/369/389/409/354. The default layer filter ("Layers 9-36") leaves 2,282. Categories after `applySC`: single mode 1,169, one dominant mode 922, multiple modes 892, not yet read 15, uninterpretable 2.
The other 2,477 poles of X5477 (and all 6,310 island files, all 3,418 MoE bundles) are not linked from the shell but their files follow the same path patterns.

### 1.5 How the shell derives a pole's category and description [V-JS]

```
p = index.neurons[i].poles["+"|"-"];  r = subcircuit_modes_index.poles[p.pid]
if r and r.n_modes > 0:  p.cat = r.verdict=="monosemantic" ? "mono" : r.fn_mono ? "fn" : "poly"
                         p.desc = r.modes.map(m => m.label).join(" · ")      // island values kept as cat_isl / desc_isl
if r and r.n_modes == 0: "failed read" - keeps the island category and island view   (51 poles in the index)
else:                    island cat/desc (mono | poly | dark | null)
```
Category words and definitions (verbatim): mono "single mode" - "Everything this pole fires on fits one specific meaning."; fn "one dominant mode" - "This pole has more than one mode, but the main one accounts for at least 90% of the damage when the neuron is switched off."; poly "multiple modes" - "This pole fires on two or more different, unrelated things."; dark "uninterpretable" - "No clear pattern was found in what this pole fires on."; none "not yet read" - "This pole has not been read yet."
Over all 5,477 indexed poles: monosemantic 2,122; polysemantic with `fn_mono` 1,651; polysemantic without 1,704.

---

## 2. Field tables

Columns: JSON path | type | optional? | meaning | example. "opt" = may be missing or null in the samples.

### 2.1 `public_atlas_data/index.json` (1,277,206 bytes, compact, raw UTF-8)

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `neurons[]` | list[3244] | no | one row per neuron | - |
| `.did` | str | no | neuron id [V-DATA: always `L{L}_E{E}_N{N}`] | `L4_E1_N177` |
| `.L` `.E` `.N` | int | no | layer / expert / neuron | 4, 1, 177 |
| `.poles` | dict with exactly `"+"` and `"-"` | no | both poles always present | - |
| `.poles.X.pid` | str | no | pole id | `L4_E1_N177__pos` |
| `.poles.X.cat` | str or null | null for 178 | island-era category: `mono` 4,042, `poly` 1,922, `dark` 346, null 178 [V-JS words above] | `mono` |
| `.poles.X.desc` | str | `""` for 524 (all `dark` + null) | island-era description; median 95 chars, max 423; all 5,964 non-empty values distinct | `The word "private" in any sense, strongest as the army rank ("Pvt"), ...` |
| `.poles.X.islands` | bool | no | an islands pole file exists (true for 6,310) [V-DATA equals islands index] | true |

### 2.2 `public_atlas_data/islands/index.json` (1,009,046 bytes)

`poles[]`: `{pid:str, L:int, pole:"pos"|"neg", name:str}`. `name` is byte-identical to the master list's `desc` for all 6,310 poles [V-DATA]; empty for the 346 `dark` poles.

### 2.3 `subcircuit_modes_index.json` (3,524,032 bytes)

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `source` | str | no | absolute build path on the authors' machine [I] | - |
| `built_at` | str ISO-8601 | no | build time | `2026-11-05T21:15:39Z` |
| `poles{pid}` | dict[5477] | no | one entry per pole | - |
| `.verdict` | `monosemantic`\|`polysemantic` | no | [V-JS] used for category | `polysemantic` |
| `.fn_mono` | bool | no | "functionally mono": more than one meaning but the main one carries >= 90% of KL damage [V-JS tooltip] | true |
| `.main_damage_share` | float 0-1 or null | null for 81 | share of the pole's damage carried by the main mode [V-JS] | 0.978 |
| `.n_modes` | int 0-30 | no | number of non-residue modes; = `len(modes)` for all poles; 0 for 51 "failed reads" | 3 |
| `.n_residue` | int 0-5 | no | number of residue modes (unreadable / closed-gate) | 1 |
| `.n_subcircuits` | int 1-51 | no | subcircuit count (mean 19.9, total 109,218) | 16 |
| `.modes[]` | list | may be empty | **non-residue modes only, in published order** [V-DATA equals SC file] | - |
| `.modes[].label` | str | no | mode name (10-384 chars, median 76) | `Completing terms/operands of written-out polynomials ...` |
| `.modes[].share` | float (34 ints: 0) | no | share of the pole's 5,000 sampled firings; per-pole sums range 0-1.0004, median 0.71 (the rest is residue/closed-gate) | 0.8008 |

### 2.4 `ns11_pages/data/index.json` (291,937 bytes)

| Path | Type | Meaning |
|---|---|---|
| `pids[]` | list[5477] str | poles that have an Overview [V-JS: `S.ov`] |
| `model{pid}` | str `opus` \| `glm` | which "neuron scientist" model wrote the pole's account [I; the pole file has `scientist_model: "claude-opus-5-5"` in all 6 samples, no `glm` sample available] |
| `sim{pid}` | float, 585 keys, range -33.6 .. 0.9987, median 0.61, 12 negative, 6 below -1 | **simulatability** [V-JS]: "when the neuron's activations are simulated from this description on held-out text, N% of its effect on the model's predictions is recovered"; "below zero: the simulation does more damage than switching the neuron off"; shown as "< -100%" when below -1; missing = "Not scored" |

### 2.5 `ns11_pages/headline_overrides.json` (969,588 bytes, pretty-printed one per line)

`{pid: str}` for 5,469 pids; 91-236 chars, median 148. [V-JS] the Overview shows this instead of `explain.headline`: "Rewritten to lead with what carries the damage, from the modes below. The neuron scientist's original title is in the page data."

### 2.6 `neuron_landscape_data/atlas_index.json` (115,613 bytes)

Bare JSON array of 5,477 pids (the shell also accepts `{pids:[...]}`) [V-JS].

### 2.7 `moe_circuit_explorer_data/index.json` (1,718,156 bytes, pretty-printed, `\u` escapes)

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `model` | str | no | model name | `Qwen3.6-35B-A3B` |
| `built_at` | str | no | build time | `2026-10-15T23:26:20Z` |
| `neurons[].did` | str | no | neuron id or non-standard id (1.2) | `L2_E171_N100` |
| `.layer .expert .neuron` | int | no | coordinates (fake for DEMO_*) | 2, 171, 100 |
| `.label` | str | 1 empty | = `meta.identity.labels.short_title_pos` = `pole_view["+"].line` in 3/3 samples [V-DATA]; 2,058 start "Fires on", 541 start "no discriminative reading" (UI rewrites that prefix to "Uninterpretable: ") | `Fires on the final token of "serine" ...` |
| `.n_ex` | int 5-1800 (median 283, mean 263.3) | no | number of exemplars = `len(exemplars.json)` [V-DATA 3/3] | 295 |
| `.act_max` `.act_min` | float | no | strongest positive / negative raw activation among exemplars [V-DATA matches sample exemplars] | 4.5148, -5.9223 |
| `.dark` | str `""`, `"+"`, `"-"`, `"+-"` | no | which poles are "dark" [I] | `"-"` |
| `.grouping` | str or null | null 27 | grouping method: `propose_assign_v1` 3,310, `effect_first_pole_units` 75, `taylor_modes_then_labels` 6 [I] | - |
| `.verdict` | dict, keys subset of `+`,`-` | `{}` for 175 | per-pole island-era verdict `mono` 2,839 / `unresolved` 1,406 / `dark` 1,346 / `poly` 719; UI words: mono "single mode", poly "multiple modes", dark "uninterpretable" [V-JS]; a key is present for 6,308 of the 6,310 island poles plus 2 others [V-DATA, see 1.3] | `{"+":"mono","-":"dark"}` |
| `.human` | dict or null or missing | mostly null | sparse human verdicts, values `mono`/`poly`/`uninterpretable` [I] | `{"+":"poly"}` |

Note: the MoE verdict vocabulary is not the same reading as PA `cat` (e.g. PA mono x MoE unresolved: 1,028 poles).

### 2.8 Islands pole file `public_atlas_data/islands/poles/{pid}.json`

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `pid, did, pole, layer, expert, neuron` | str/int | no | identity | - |
| `sign` | int +1/-1 | no (JS falls back to `pole`) | pole sign | 1 |
| `islands[]` | list (2-12 in samples) | may be empty [V-JS handles] | clusters of the pole's strongest firings "that occur in similar contexts" [V-JS intro] | - |
| `.id` | int | no | island id (0-based; UI shows id+1) | 1 |
| `.n` | int | no | examples in the island; sums to 270-300 per pole [V-DATA] | 112 |
| `.name` | str 19-82 | may be empty (UI "(unnamed island)") | LLM name | `Base64 blob interior characters` |
| `.description` | str 164-363 | opt | LLM description; uses «...» to mark the firing token | - |
| `.examples[]` | list[5] | no | 5 sampled examples; `label` in `top` / `median` / `random` (UI: Strongest / Typical / Random) | - |
| `.examples_more[]` | list[3-7] | opt | extra examples, `label:"band"`, "from strongest to weakest" | - |
| `..act` | float | no | raw peak activation, signed (sign matches pole in all examples) | 4.0625 |
| `..a_over_theta` | float >= 1.26 or null | opt | `abs(act)/theta` with the **per-neuron** theta (see 3.6); tooltip: "the example's peak divided by the activation at which this pole typically starts to fire" | 5.041 |
| `..tokens[]` | list of `[text:str, value:float]`, up to 241 | no | passage window, about 120 tokens each side; values are raw signed activations (not theta units), never null | `["n", 3.875]` |
| `..focal_idx` | int (120 unless the passage is short) | JS tolerates null | index of the firing token | 120 |
| `examples_more_n` | int | no | max extras per island (7) [I] | 7 |
| `opus.modes[]` | list (1-3) | no | LLM-judge grouping of islands into modes | - |
| `.name` `.predicate` | str | predicate may be `""` | mode name / description | - |
| `.island_ids[]` | list[int] | no | member island ids (an island may appear in 2 modes: "the reader split one island" [V-JS]) | `[0,1,4,5]` |
| `.chips[]` | list[str] 0-1 | no | the only place the share lives: text `"firing 74.2%"`, parsed by regex `^firing\s+([\d.]+)%` [V-JS] | `["firing 74.2%"]` |
| `.grey` | bool true | opt | the "unassigned" bucket (name `unassigned`) | true |

### 2.9 Subcircuit pole file `subcircuit_modes_data/{pid}.json`

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `pid, layer, expert, neuron, sign` | str/int | no | identity (`sign` +1/-1) | - |
| `theta` | float | no | **per-pole** firing threshold in raw activation units; "every value on this page is in units of theta" [V-JS] | 0.3192148 |
| `label` | str 359-590 | no | "Autointerp label" of the neuron (= MoE `fleet_label`, hidden in the public view) [V-DATA] | - |
| `summary` | str 570-758 | no | LLM summary of the pole | - |
| `closed_share` | float 0-1 | no | "share of the pole's firings where the gate pre-activation was negative" [V-JS] | 0.3616 |
| `n_firings` | int | no | firings sampled (5,000 in all samples) | 5000 |
| `verdict` | str | no | monosemantic / polysemantic | - |
| `fn_mono`, `main_damage_share` | bool, float or null | no | as in the index | - |
| `kl_neuron_mean` | float | no | the pole's mean KL damage per firing; `kl.ratio = kl.per_firing / kl_neuron_mean` [V-DATA] | 2.92e-4 |
| `partition` | str | no | "the partition of firings into subcircuits" (clustering config id) | `leiden_drvopp_m30__res1.0` |
| `judge.opus` | dict | empty in samples | held-out matching test with Opus as judge, per bin | `{}` |
| `judge.jev.{all,ge1,mid,weak,closed}` | `{n:int, acc:float, chance:float}` | `mid`/`weak` may be missing | "How often a blind judge assigns a held-out firing to its own subcircuit, by firing strength"; bins = all, >= theta, 0.5-1 theta, < 0.5 theta, closed gate; "Chance is the sum of squared subcircuit shares in that bin" [V-JS] | `{"n":1257,"acc":0.448,"chance":0.179}` |
| `modes[]` | list (3-21) | no | non-residue modes in published order, then residue modes | - |
| `.label` | str | no | mode name; residue labels: `Closed-gate firings (silu leak)`, `Residue (unreadable)`, `Residue: ...` (public view replaces them by "Q3/Q4 firings (gate input negative)" / "Unassigned examples") [V-JS] | - |
| `.description` | str 29-408 | no | mode description | - |
| `.subcircuits[]` | list[str] 0-38 | may be `[]` | member subcircuit ids | `["c2","c3"]` |
| `.share` | float | no | share of the 5,000 firings; sums to about 1.0 over all modes incl. residue | 0.4421 |
| `.n_firings` | int | missing on residue modes | = sum of member subcircuit `n` [V-DATA] | 2201 |
| `.unique_contexts`, `.top_context_share` | int, float or null | mostly null | distinct 16-token contexts; share of the most repeated context (UI warns at >= 0.5: "one repeated context = N% of firings") | - |
| `.merged_from[]` | list[str] or null/missing | opt | earlier mode labels combined "by the long-context review" | - |
| `.kl` | `{per_firing:float, ratio:float}` or null | null when share is 0 | damage per firing in nats, and relative to the pole's average ("KL over the next 16 tokens") [V-JS] | `{"per_firing":6.5e-4,"ratio":2.24}` |
| `.residue` | bool true | only on residue modes | - | - |
| `.closed_gate` | bool true | only on the closed-gate residue mode | - | - |
| `.residue_source` | str | rare | e.g. `long-context review` | - |
| `subcircuits{c<k>}` | dict (17-40) | no | "clusters of firings that share the same upstream circuit evidence" | - |
| `.id, .n, .share` | str, int, float | no | id, firings, share of the pole (`n/5000`) | `c2`, 366, 0.0735 |
| `.profile.{ge_theta,mid,weak,closed}` | float, sums to 1 | no | strength profile: >= theta, 0.5-1 theta, < 0.5 theta, closed gate | - |
| `.median_a_rel` | float | no | median firing strength in theta units [I] | 1.792 |
| `.meaning` | str 160-628 | no | "Text reading": what the subcircuit fires on, read from text alone | - |
| `.token_pattern` | str 98-343 | no | which token fires | - |
| `.function` | str 297-733 | no | "Circuit" reading written from contrastive circuit evidence (hidden in public view) | - |
| `.function_label` | str 40-94 | no | card title of the subcircuit | - |
| `.gate_role`, `.up_role` | str 75-298 | no | what opens the gate / sets the up signal | - |
| `.readable` | bool | no | false = "Opus marked this subcircuit as unreadable" | - |
| `.reading_confidence`, `.function_confidence` | int 1-5 | no | LLM self-rated confidence [I] | 4 |
| `.circuit_desc`, `.circtext_desc` | null in all samples | - | optional long descriptions | null |
| `.spec_flags[]` | list of `{rejected:str, p:float, p_after:0.0\|1.0}` or null | opt | rejected alternative readings with probabilities [I, not shown by the page] | - |
| `.circuit.{gate_drivers,gate_opposers,up_drivers,up_opposers,readers}[]` | list[10] each | no | top-10 other neurons per role | - |
| `..unit` | str did | no | the other neuron (any layer) | `L28_E132_N167` |
| `..leaf` | float | no | "the unit's mean contribution over this subcircuit's firings"; non-negative in the four input lanes, signed for readers [V-JS + V-DATA] | 0.0164 |
| `..rest` | float >= 0 | no | "its mean over the pole's other firings" | 0.0001 |
| `..label` | str <= 220 (truncated) or `(no label)` | no | autointerp label of that unit | - |
| `.unique_contexts`, `.top_context_share` | int, float | no | as for modes | - |
| `.kl` | `{per_firing, ratio, n_measured:int}` | no | damage for this subcircuit; `n_measured` = tested firings (5-101) | - |
| `.exemplars[]` | list[12] | no | "its 4 strongest firings, 4 around its median, and its 4 weakest, each from a distinct context" [V-JS] | - |
| `..a` | float signed | no | raw activation at the firing token | 2.6349 |
| `..a_rel` | float > 0 | no | `abs(a)/theta` [V-DATA max error 7e-4] | 8.254 |
| `..closed` | bool | no | gate pre-activation negative ("Q3/Q4") | false |
| `..fold` | `fit` \| `audit` | no | discovery fold / held-out fold | `fit` |
| `..pre` | str 0-300 chars | may be `""` | text before the token window (last <= 300 characters) | - |
| `..tokens[]` | list[33] of `[text, value\|null]` | no | 16 tokens before, the firing token, 16 after; value = **signed activation / theta** (multiply by `sign` for pole direction); null = "expert not routed"; `""` text = padding at document edges | `["d", 8.223]` |
| `..focal` | int = 16 | no | index of the firing token | 16 |
| `..repeats` | int >= 1 | no | firings of the subcircuit sharing "the same 16 tokens up to the firing token" | 1 |
| `modes_before_unify[]`, `verdict_before_unify`, `review_verdict`, `verdict_source`, `unified_description` | list/str | missing in 1 of 6 (the failed read) | pipeline history: modes before the "long-context unifier", its verdicts, and a long unified description (677-1,110 chars) [I, unused by the page] | - |

### 2.10 Landscape pole file `neuron_landscape_data/{pid}.json`

| Path | Type | Opt | Meaning | Example |
|---|---|---|---|---|
| `pid, layer, expert, neuron, sign, verdict, n_firings` | - | no | identity; `n_firings` 5,000 | - |
| `modes[]` | list K (3-21) | no | order: **open modes** (same order as the SC non-residue modes), then **Q3/Q4 ("closed") modes**, and the **last entry is always "Unassigned examples (fit no mode)"** [V-JS + V-DATA] | - |
| `.label`, `.description` | str | description may be `""` | mode name / reading. For open modes identical to SC; Q3/Q4 mode labels exist only here | - |
| `.share` | float or null (unassigned) | - | share of the 5,000 sampled firings (= sum of member subcircuit shares) [V-DATA]; **not** the population-weighted share the UI prints | 0.4421 |
| `.kl_per_firing` | float or null | null for closed/unassigned | = SC `modes[].kl.per_firing` | 6.5e-4 |
| `.closed` | bool (absent on the last entry) | - | Q3/Q4 mode: "Firings where the neuron's gate input is negative (quadrants 3 and 4 of the gate x up plane)" [V-JS] | false |
| `.rel` | null or `NEW` \| `SAME` \| `SUBCASE` \| `SUPERSET` | closed modes only | relation to an open mode: SAME "Same meaning as", SUBCASE "A narrower case of", SUPERSET "A broader version of another mode", NEW "A meaning of its own" [V-JS] | `NEW` |
| `.fold` | int or null | closed modes only | index of the open mode it shares meaning/colour with (may be null even when rel is SAME) | 0 |
| `bands[]` | list[5] | no | rarity levels, weakest first: `p95, p99, p99.9, p99.99, top 1,000`; UI names "Top 5%", "Top 1%", "Top 0.1%", "Top 0.01%", "Strongest 1,000" | - |
| `.a_med, .a_min, .a_max` | float | no | firing strength in theta units within the band | 0.455 |
| `.mass` | float | no | fraction of all tokens in the band (0.04, 0.009, 0.0009, ~9.5e-5, ~2e-6..7e-6); UI "1 in N tokens" = 1/mass [V-JS] | 0.04 |
| `.n` | int 1000 | no | firings sampled in the band | 1000 |
| `.counts[K]` | list[int], sums to n | no | sampled firings per mode | - |
| `.dmg_lin[K]` | list[float] | absent for units without damage data (`has_kl:false`, not in samples) | summed KL damage of the band's firings per mode (measured or estimated); mean damage per firing of the band = `sum(dmg_lin)/n` [V-JS] | - |
| `.dmg[K]` | list[float] | - | another damage aggregate per mode, not used by any page [I, formula unknown] | - |
| `kl_fit` | `{slope, r2, n, q}` | no | log-log fit damage vs strength over the tested firings; `n` = tested firings (179-415), `q` = 0.25 [V-JS uses n only] | - |
| `order[K-1]` | list[int], permutation of 0..K-2 | no | lane order (older) | - |
| `sim` | (K-1)x(K-1) float matrix or **null** (null when K-1 = 2 in 2 samples) | opt | symmetric, diagonal 1, range 0.011-1; an earlier mode-similarity matrix [I, unused] | - |
| `rel_sim` | (K-1)x(K-1) float matrix | no | symmetric, diagonal 1, range 0.40-1; "topic-embedding relatedness of the mode descriptions" (from the comment about `add_rel_colours.py`) [V-JS comment, matrix itself unused] | - |
| `rel_order[K-1]` | permutation | opt (JS falls back to `order`) | lane order so "related meanings ... sit side by side" | - |
| `rel_col[K-1]` | list of `#rrggbb` | opt | one colour per mode, "related meanings get nearby hues"; shared by Overview and landscape | `#008b85` |
| `dots[]` | list (929-1,165) | no | sampled firings; "Each dot is one place in real text where this pole of the neuron fired" | - |
| `.b` | int 0-4 | no | band index | 0 |
| `.a` | float | no | strength in theta units ("N x threshold") | 0.449 |
| `.m` | int 0..K-1 | no | landscape mode index | 7 |
| `.s` | int, -1 = none | no | subcircuit number (`c{s}` in SC) [V-DATA: consistent for every dot in open modes; not read by the page] | 0 |
| `.c` | bool | no | "gate input negative" | true |
| `.kl` | float or null (null for ~75%) | opt | measured KL damage (nats, 16 tokens) when the neuron is zeroed at this firing; 0 possible | 1.39e-8 |
| `.lk` | float | no | log10 damage: measured where `kl>0`, otherwise "estimated from strength and mode" | -7.857 |
| `.w` | float | no | population weight relative to band 0 = `(mass/n) / (mass0/n0)` [V-DATA] | 1.0 |
| `.t[]` | list (13-113) of `[text, value\|null]` | no | context; the first `u` tokens are plain context without values; later nulls = not routed to the expert; values in theta units, pole direction positive | `[" John", 0.46]` |
| `.f` | int (100 typical) | no | index of the firing token; normally `f-u = 16` and 12 tokens follow | 100 |
| `.u` | int (84 typical, 0 possible) | no | number of leading value-less context tokens | 84 |

Optional fields read by the JS but absent in all 6 samples: `has_kl`, `set` (`"random baseline"`), `kind`, `kind_name`, `uid`.

### 2.11 NS11 pole file `ns11_pages/data/{pid}.json` (24 top-level keys in every sample)

| Path | Type | Opt | Meaning | Used by a page? |
|---|---|---|---|---|
| `pid, did, pole, side, layer, expert, neuron` | - | no | identity (`side` = positive/negative) | - |
| `atlas_label` | str 16-97 | no | the island-era pole description (= PA `desc`; "No clear pattern" when empty) [V-DATA] | no |
| `cat`, `cat_public` | str | no | `mono`/`poly` and `single mode`/`multiple modes` | no |
| `threshold_raw` | float | no | = SC `theta` exactly [V-DATA 6/6] | no |
| `pick`, `first_hypothesis` | str 1.9-2.3 k chars | no | "The scientist's final description" (plain text with paragraphs); the two are identical in 6/6 samples | `pick` yes |
| `report` | str 2.6-3.3 k chars, Markdown | no | "The scientist's full report" | yes |
| `learned` | null | - | unused | no |
| `kl` | dict, **empty in all samples** | - | when filled: keys among `fleet, g5m, census, h1, own`, each `{label, kl_recovered}`; section "How well the description predicts the neuron" [V-JS] | yes |
| `explain.headline` | str 95-118 | no | scientist's title (replaced by the override) | yes |
| `explain.summary` | str 678-1,009 | no | plain-language summary | yes |
| `explain.situations[]` | list 2-20 of `{mode:int, name, explain, typical, cue, scientist}` | no | one paragraph per mode on multi-mode poles (`mode` = mode index); on single-mode poles the entries are facets and `mode` is a **subcircuit number** [V-JS + V-DATA] | yes (`explain`, `name`, `scientist`) |
| `explain.not_this[]` | list[4] str | no | "What does not make it fire" | yes |
| `explain.how_we_know[]` | list[5] of `{claim, test, result}` | no | "Agent experiments" | yes |
| `explain.open` | str 214-363 | no | "Still open" | yes |
| `explain.contexts{<k>}` | dict str->str (8-46 chars), may be `{}` | opt | short name per subcircuit number | no |
| `explain._model`, `scientist_model` | str | no | `claude-opus-5-5` in samples | no |
| `islands_view` | dict | no | a restatement of the SC file in "island" vocabulary: `verdict`, `split:false`, `source:"subcircuit_modes"`, `closed_gate_share` (= SC `closed_share`), `unit` (`mode` or `island`; `island` on the monosemantic sample) | no |
| `islands_view.modes[]` | `{name, predicate, island_ids[int], kind:"mode"\|"residue", share, kl_ratio, ev}` | - | = SC modes (label, description, subcircuit numbers, share, kl.ratio) **without the closed-gate residue mode**; `ev` = list of `[a_rel, island_id]` for the non-closed examples | no |
| `islands_view.islands[]` | `{id:int, n, name, description, examples[12]}` | may be `[]` | = SC subcircuits not in the closed-gate mode: `name` = `function_label`, `description` = `meaning`; examples `{si:null, pos:null, a, focal:16, tokens[33] str, act[33] float\|null, text, closed}` are the SC exemplars with the weakest four in reversed order; `text` = `pre + tokens` | no |
| `mode_docs[]` | list 0-5 of `{unit:int, si:null, a, tokens[], act[], routed[]}` | may be `[]` | longer token windows (28-133 tokens) for a few modes / subcircuits | no |
| `circuit.note` | str | `""` or e.g. "none of its firing examples passed the circuit analysis" | - | no |
| `circuit.inputs[]` | `{name, effect:"+"\|"-", role:"drives"\|"opposes", share}` | may be `[]` | = MoE `pole_view[pole].inputs` (`share` = `mass_share`) [V-DATA] | no |
| `circuit.readers[]` | `{name, effect, n}` | may be `[]` | = MoE `pole_view[pole].readers` [V-DATA] | no |
| `circuit.logits[]` | `{token, w}` 8-10 | no | the first (up to 10) MoE `logits` entries whose `sign` equals the pole sign, same order [V-DATA 3/3] | no |
| `circuit.expert_label` | null | - | - | no |
| `examples[]` | list = 12 x n_subcircuits (204-480) | no | **1:1 copy of the SC exemplars**: `id`=`c{k}_{j}`, `kind:"fire"`, `design:"subcircuit"`, `band:"c{k}"`, `where:"firing example"`, `tokens[33]` (str), `act[33]` (float or null, pole direction), `pred:null`, `routed[33]` (bool = act not null), `focal:16`, `focal_act`, `max_act`, `ctx_tail` (= SC `pre`), `ctx_len`, `doc` (= id), `gate_closed` (= SC `closed`) | no |
| `showcase[5]`, `full_docs{id}` | ids; `{tokens[], act[], routed[]}` 18-133 tokens | no | five showcased examples with longer windows, every token valued | no |

### 2.12 MoE bundle `moe_circuit_explorer_data/{did}/`

**meta.json** (schema `moe-circuit-explorer-1.0`)

| Path | Type | Meaning |
|---|---|---|
| `schema`, `provenance.{built_at, wave, git_sha, moe_relp_sha256, active_threshold_def, configs{input_max_nodes, edge_max_nodes, fidelity_dev}}` | str/int | build metadata; `active_threshold_def` = "input: \|v\|>=5% of exemplar lane mass; readers: \|v\|>=5% of exemplar \|reader\| mass; logits: v!=0" |
| `identity.{did, layer, expert, neuron}` | - | identity |
| `identity.labels.{short_title_pos, short_title_neg, fleet_label}` | str 199-265 / 491-497 | per-pole one-line readings; `fleet_label` = long autointerp label (= SC `label` for the pos pole sample) |
| `identity.quantiles.{p0_01, p0_1, p90, p95, p99, p99_9, p99_99, top50}`, `identity.gmax` | float | raw-activation quantiles of the neuron over the corpus [I] |
| `identity.jtable.{jnorm, kappa, dnorm, fp3_ids[3], fp3_tokens[3]}` | float / int / str | weight-derived scalars and 3 token ids/strings [I, undocumented] |
| `theta_first.poles.{positive,negative}.{theta, theta_source, shoulder_lo, table_flag, pole_real, structural_gate{real, frac_routed_on_pole}, measured_gate{measured_sign_share, n_events, real, reasons[]}, n_candidates, n_admitted{core, shoulder, sub_theta_context}, frac_routed_below_theta}`, `theta_first.{applied, counts{input, gate_closed_dropped, admitted, dropped_sub_theta}, doc}` | - | exemplar-selection bookkeeping; `theta` is used by the page for "N x" strengths [V-JS] and is the **same number for both poles** in 3/3 samples |
| `pole_view{"+","-"}.{n_admitted, theta, pole_real, drive_mass, oppose_mass, cancel_note, line, prior_read, top_contexts[6]{ctx, act, ex_id}}` | - | per-pole summary; `line` = one-line reading shown as title; `pole_real:false` -> "This pole of the neuron fires too rarely to have a circuit to show."; `drive_mass` = sum of `aligned_mass` of driving inputs [V-DATA]; `top_contexts[].ctx` marks the firing token with ▸...◂ |
| `pole_view.X.inputs[]` | `{id, name, n, color, effect, role, aligned_mass, lane_effect, mass_share, dominant_expert, dominant_share, mean_contrib}` | per-pole input-group summary (not used by the public page, which recomputes from `groups.input`) |
| `pole_view.X.readers[]` | `{id, name, n, effect, color, n_ex, n_ex_other, mean_w}` | downstream groups for this pole; `n_ex` = examples in which the group is active ("N neurons activated in n_ex of n_admitted examples") [V-JS] |
| `pole_view.{built_at, prior_titles{"+","-"}}` | str / str or null | earlier titles |
| `groups.input[]` | `{id, name, description, family:"pos"\|"neg", effect, color, is_misc, kind:null, subgroups[], bucket, members[], agg}` | groups of earlier neurons; `bucket` = `"+\|drives"`, `"+\|opposes"`, `"-\|drives"`, `"-\|opposes"` (pole, role); names carry a lane tag after " · " that the page strips |
| `groups.input[].agg.{gate,up}.{mean, n_active, mean_signed, n_pos, n_neg}` | float/int | `mean` = mean of the incidence column over all exemplars; `mean_signed` = sum of members' `gate_s`/`up_s` = the number printed on the card ("each group's summed contribution to that signal"); `n_pos`/`n_neg` = members raising / lowering [V-JS + V-DATA] |
| `groups.input[].subgroups[]` | `{name, description, units[uid], level:1-4\|null, agree:0-1, lane{gate_share, gate_sign, up_sign, tag}}` | themes inside a group |
| `groups.input[].members[]` | `{uid, did, pole, layer, label (<=100), label_full (<=500), label_source, sim:null, gate_w, up_w, gate_pct, up_pct, coupling_exact, q1_share, ev_n_active, ev_n_docs, cat, agree:"3/3", gate_s, up_s, relp}` | member neurons (earlier layers only); `gate_s`, `up_s` = the member's signed contribution to each lane shown in the member list [V-JS]; `gate_w`, `up_w` signed coupling values and `gate_pct`, `up_pct` percentiles [I]; `label_source` `fleet+`/`fleet-`/`short_title+`/`... (cross-pole)` |
| `groups.readers[]` | `{id, name, description, effect, color, is_misc, kind, subgroups[], bucket:"+\|raised"..., hop_min:null, hop_max:null, causal:false, members[], agg{mean, n_active, mean_signed:0, n_pos:0, n_neg:0}}` | groups of later neurons |
| `groups.readers[].members[]` | `{uid, did, pole, layer, label, label_full, label_source, hop, sim:null, w, w_s:0, gate_pct, up_pct, r_gate, coupling_exact, q1_share, ev_n_active, ev_n_docs, cat, agree, weight_coupled}` | `hop` = layer distance to this neuron [V-DATA]; `w` signed effect [I] |
| `logits[24]` | `{token, token_id, w, sign, source:"static"\|"target", n_active?}` | output-token effects; `static` = weight-derived top tokens of either sign, `target` = next tokens observed in exemplars (`n_active`) [I] |
| `quadrant_evidence` | `{}` in all samples | - |
| `taxonomy_gate.{verdict, real_score, null_p90}`, `gates_summary.{n_sampled, n_kept, skip_reasons{...}}` | - | QA bookkeeping [I] |

**exemplars.json** - list (253-299 in samples; `index.n_ex`)

| Path | Type | Meaning |
|---|---|---|
| `ex_id`, `band`, `regime` | str | band in `top1000, p99_99, p99_9` (positive firings) and `bottom1000, p0_01, p0_1` (negative firings); regime `gateopen` only |
| `si`, `focal` | int | corpus sequence index and token position (the only place a corpus coordinate is published) |
| `act` | float signed | raw activation; `act = silu(gate_pre) * up_pre` holds to 2.3e-4 for all 847 sample exemplars [V-DATA], matching the page text "its output is gate x up" |
| `gate_pre`, `up_pre` | float | gate and up pre-activations: **2-D coordinates of each firing**; gate_pre 0.43-3.07 (all positive: gate-open regime), sign(up_pre) = sign(act) |
| `router_weight`, `route_rank` | float 0.007-0.63, int 0-7 | MoE router weight of this expert on the token and its rank among the 8 routed experts [I] |
| `dup` | int 0 | duplicate marker |
| `window.{tokens[], acts[], focal_rel}` | list <= 81, int (40) | +-40 tokens; `acts` raw signed activation, 0.0 (not null) where the expert was not routed (51-67% zeros) |
| `next_token.{id, str, model_prob, model_top1}` | - | the actual next token, the model's probability for it, and the model's top-1 token |
| `flags.{routed, fidelity_ok, roundtrip_ok, showcase}`, `graph` | bool/null | QA flags, constant in samples |

**incidence.json**

| Path | Type | Meaning |
|---|---|---|
| `ex_order[]` | list[str] | exemplar ids = column order; identical to the exemplars order in 3/3 samples (the page re-maps if not) |
| `routes{route_id}` | list[float] of length n_ex, or `{q: base64, scale: float}` when quantized (value = byte/255*scale) [V-JS] | exemplar x route matrix: `in:{g}:gate` and `in:{g}:up` (>= 0, up to ~2), `rd:{r}` (signed, about +-0.1), `lg:t{token_id}` (signed; one column per distinct next token, non-zero only for the exemplars followed by that token) |
| `route_stats{route_id}` | `{n_active, mean, p90, vmax}` | column summaries (`mean` over all exemplars; reader mean = mean absolute value) |
| `quantized` | bool (false in samples) | - |
| `active_threshold_def` | str | as in meta |

---

## 3. The same pole across the per-pole files

Poles compared: `L30_E47_N449__pos`, `L4_E1_N177__pos`, `L4_E1_N177__neg`, `L4_E1_N271__pos`, `L4_E1_N372__neg` (all four file types) plus `L4_E1_N345__neg` (no islands file; an edge case).

### 3.1 What is duplicated and what is unique

| Information | IS | SC | LS | NS | Global index copy | MoE |
|---|---|---|---|---|---|---|
| identity (pid, L/E/N, sign) | yes | yes | yes | yes | - | meta.identity (neuron) |
| island-era pole description | (index only) | - | - | `atlas_label` | PA `desc`, islands index `name` | - |
| island grouping (islands, "opus" modes, shares as text) | **unique** | - | - | - | - | - |
| +-120 token raw-activation windows of the ~300 strongest firings | **unique** | - | - | - | - | partly the same firings (5-59 shared contexts) |
| subcircuit modes: label, description, share | - | master | copy (open modes) | copy (`islands_view.modes`) | index: label + share | - |
| mode kl per firing / ratio | - | master | `kl_per_firing` | `kl_ratio` | - | - |
| subcircuit readings (meaning, token_pattern, function, roles) | - | **unique** (function, token_pattern, roles) | - | `name`=function_label, `description`=meaning | - | - |
| 12 exemplars per subcircuit | - | master | - | copied **twice** (`examples`, `islands_view.islands[].examples`) | - | - |
| top-10 circuit units per lane per subcircuit (leaf/rest) | - | **unique** | - | - | - | different method, group level |
| judge test, profiles, spec_flags, before-unify history | - | **unique** | - | - | - | - |
| Q3/Q4 mode labels and relations (`rel`, `fold`) | - | - | **unique** | - | - | - |
| rarity bands, per-band counts and damage per mode | - | - | **unique** | - | - | - |
| ~1,000 sampled firings with band, weight, KL damage | - | - | **unique** | - | - | - |
| mode similarity matrices, colours, order | - | - | **unique** | - | - | - |
| scientist account (headline, summary, situations, not_this, experiments, open, pick, report) | - | - | - | **unique** | override headline | - |
| circuit summary (input/reader group names, logits) | - | - | - | copy | - | master |
| theta | implied by `act/a_over_theta` | `theta` | (values already in theta units) | `threshold_raw` | - | `theta_first`, `pole_view.theta` |
| verdict | - | yes | yes | yes | yes | different vocabulary |

Verified equalities (6/6 poles unless stated): index `modes` = SC non-residue modes (label, share, order); index counts = SC counts; LS open modes = SC non-residue modes (label, description, share, kl) in 5/6; NS `islands_view.modes` of kind `mode` = SC non-residue modes (name, predicate, share, kl_ratio, island_ids = subcircuit numbers); NS `threshold_raw` = SC `theta`; NS `closed_gate_share` = SC `closed_share`; `n_firings` 5,000 in SC and LS; the four verdict copies agree.

### 3.2 Mode index consistency ("same index = same mode"?)

- **Yes for open modes**: index k of `subcircuit_modes_index.poles[pid].modes` = k-th non-residue entry of SC `modes` = LS `modes[k]` = NS `islands_view.modes[k]` = the shell's `&mode=k` = NS `explain.situations[].mode` on multi-mode poles. The JS resolves it as `SC.modes.filter(x => x.subcircuits && !x.residue)[k]` [V-JS].
- **SC has extra entries after the open modes**: residue modes (`residue:true`; up to 2 in samples: the closed-gate one and "Residue (unreadable)"). Their raw array index continues after the open modes.
- **LS has different extra entries**: indices after the open modes are Q3/Q4 modes (0-18 in samples), and the last index is "unassigned". These indices have **no counterpart index** in SC or NS. Each LS mode (open or closed) is a set of whole subcircuits: every subcircuit maps to exactly one LS mode, and the mode share equals the sum of its subcircuits' shares [V-DATA]; LS Q3/Q4 modes regroup the subcircuits of SC's "Closed-gate firings (silu leak)" residue; "unassigned" collects the rest of that residue plus "Residue (unreadable)" subcircuits and firings with `s = -1`. The page says a Q3/Q4 mode selected in the landscape opens "All" in the Modes tab.
- **NS drops the closed-gate residue mode** and its subcircuits from `islands_view` (1, 5, 20 and 38 subcircuits missing in the four samples that have such a mode; in the last case `islands_view.islands` is empty).
- **Single-mode poles**: `explain.situations[].mode` and `mode_docs[].unit` are subcircuit numbers, not mode indices (sample `L4_E1_N177__neg`: situations modes `[7,8,1,2,3,4]` = `explain.contexts` keys; `islands_view.unit = "island"`).
- **Failed-read edge case** (`L4_E1_N345__neg`, closed share 0.992): SC has 3 non-residue modes with `share:0`, `subcircuits:[]`, `kl:null` (summary: "No subcircuit readings were provided, so this merge rests only on the autointerp label"); the index lists them (n_modes 3); LS has 0 open modes and 18 Q3/Q4 modes, so LS index k does not correspond to SC mode k there. Rule that fits all samples: LS open modes = SC non-residue modes that have at least one subcircuit.
- **IS modes are a different, older reading**: `opus.modes` (1-3 per pole, shares as text) and island ids have no index relation to subcircuit modes or subcircuit ids. Example: `L30_E47_N449__pos` has 1 island mode (74.2%) + unassigned versus 7 open subcircuit modes + 4 Q3/Q4 modes.

### 3.3 Are example ids shared across files?

There is **no example id shared across all files**. Only two id families exist: `c{k}_{j}` (SC position, reused by NS) and the MoE `ex_id` (`si`, `focal` corpus coordinates). NS carries `si:null`, `pos:null`. Cross-file matching of firings is possible only by text. Using as key the concatenated text of the firing token plus the 16 tokens before it:

| Pole | SC exemplars | = NS `examples` | SC contexts also among LS dots | IS examples (shared with SC / with LS) | MoE exemplars on the pole (shared with SC / LS / IS) |
|---|---|---|---|---|---|
| L30_E47_N449__pos | 204 | 204/204 identical | 45 of 200 (dots: 929) | 139 (14 / 26) | 124 (16 / 25 / 46) |
| L4_E1_N177__pos | 240 | 240/240 | 44 of 239 (937) | 123 (4 / 22) | 150 (9 / 23 / 59) |
| L4_E1_N177__neg | 300 | 300/300 | 92 of 300 (1,165) | 92 (8 / 24) | 149 (13 / 43 / 5) |
| L4_E1_N271__pos | 480 | 480/480 | 97 of 480 (983) | 114 (20 / 26) | no bundle sampled |
| L4_E1_N372__neg | 360 | 360/360 | 77 of 359 (969) | 69 (6 / 12) | no bundle sampled |
| L4_E1_N345__neg | 456 | 456/456 | 104 of 454 (988) | - | - |

- NS `examples` = SC exemplars exactly (tokens, `ctx_tail` = `pre`, `gate_closed` = `closed`, `act` = SC token value x pole sign, 0 numeric difference). NS `islands_view.islands[].examples` are the same exemplars again (order: 8 first identical, last 4 reversed).
- LS dots are a different, larger sample of the same 5,000 firings: 16-24% of SC exemplar contexts appear among the dots.
- IS and MoE examples come from the older "strongest firings" selection (raw activation, per-neuron theta) and overlap with SC/LS only marginally.

### 3.4 Numbers that look alike but differ

- `share` in SC/LS/index = fraction of the 5,000 sampled firings (equal weight per band). The Overview/landscape print **population-weighted** shares recomputed from `bands` (`weight = mass/n` per firing). Example `L30_E47_N449__pos` mode 0: stored 0.4421, population-weighted 0.078.
- SC closed-gate mode share (0.3777) is not `closed_share` (0.3616): the first counts firings in subcircuits assigned to the closed-gate residue, the second counts firings whose gate was negative.
- Token values at the firing position differ from the headline strength. SC: `abs(tokens[16][1])` vs `a_rel` median difference 0.03-0.10 theta, max 1.3. LS: the focal token value equals `a` within 0.02 for 26-90% of the dots depending on the pole (240/929 ... 888/988). IS: `tokens[focal_idx][1]` equals `act` within 0.05 for only 6/24, 21/139, 45/92, 47/123, 93/114, 37/69 examples, and the focal token is the window maximum in 94/139 for one pole. The likely cause is that per-token values were recomputed on the stored (truncated) window [I for the cause, V-DATA for the mismatch]. Use the scalar (`a`, `a_rel`, `act`) as the firing strength, not the token value.

### 3.5 Neuron-to-neuron references point outside the atlas [V-DATA]

SC circuit rows in 6 samples: 8,500 rows, 5,648 distinct input units and 1,534 distinct reader units; **0** of the input units and 2 of the reader units are atlas neurons. MoE members in 3 samples: 1,857 rows; 0 of 1,397 input neurons and 1 of 354 reader neurons are atlas neurons. Referenced neurons carry their own inline autointerp label, so the reference graph is "atlas pole -> labelled outside neuron". Poles of the same expert share some upstream units (8-36 shared units between pairs of L4_E1 poles; 0-1 with the L30 pole).

### 3.6 Theta is not one number [V-DATA]

| Pole | SC / NS / LS theta (per pole) | IS implied theta = abs(act)/a_over_theta | MoE theta (same for both poles) |
|---|---|---|---|
| L30_E47_N449__pos | 0.3192 | 0.8059 | 0.805838 |
| L4_E1_N177__pos | 0.2915 | 0.2658 | 0.265755 |
| L4_E1_N177__neg | 0.2658 | 0.2657 | 0.265755 |
| L4_E1_N271__pos | 0.1476 | 0.2632 | - |
| L4_E1_N372__neg | 0.2233 | 0.2664 | - |
| L14_E78_N0__pos | - | 0.4256 | 0.425605 |

"N x threshold" in the Modes/Overview tabs (SC, LS, NS) and in the Islands/Circuit views (IS, MoE) are therefore on different scales. Normalise to raw activation before comparing (`raw = a_rel * theta_SC`, `raw = a_over_theta * theta_MoE`).

---

## 4. Size census

All files are single-line compact JSON except `moe_circuit_explorer_data/index.json` and `headline_overrides.json` (pretty-printed). SC, MoE and parts of NS escape non-ASCII as `\uXXXX`; IS, LS, PA use raw UTF-8. gzip ratios measured locally (level 6).

| File type | Samples | Raw bytes mean (min - max) | gzip mean | What dominates (share of compact size) |
|---|---|---|---|---|
| IS pole | 6 | 287,111 (77,413 - 407,166); non-E78 mean 329,051; ~3.0-3.3 kB per example | 67 kB (4.3x) | token text 42%, token activation numbers 26%, array punctuation 31%; names/descriptions 1% |
| SC pole | 6 | 821,665 (474,773 - 1,187,444); ~30 kB per subcircuit | 233 kB (3.5x) | **inline labels of other neurons 41.6%**, exemplar `pre` 12.2%, exemplar token text 9.0% + values 6.6%, readings 5.5%, unit/leaf/rest 4.4% |
| LS pole | 6 | 1,540,020 (1,401,005 - 1,863,816) | 323 kB (4.8x) | dot token text 40.1%, dot token values/nulls 26.6%, punctuation 30%, dot scalars 2.3%, everything else < 1% |
| NS pole | 6 | 676,940 (407,923 - 895,361) | 194 kB (3.5x) | the two copies of SC exemplars about 77% (`examples`: ctx_tail 15.1, tokens 13.0, routed 10.5, act 10.0; `islands_view` examples: text 14.0, tokens 8.1, act 6.3); the unique scientist text (explain, pick, first_hypothesis, report) only about 2.6% (~15 kB) |
| MoE meta | 3 | 689,976 (658,060 - 707,301) | 138 kB (5.0x) | member `label_full` 43.9%, member numeric fields 34.4%, member `label` 9.7%, subgroups/aggregates 7.3% |
| MoE exemplars | 3 | 453,588 (383,382 - 504,964); 1,607 B per exemplar | 103 kB (4.4x) | window tokens 38%, window acts 32%, scalars 30% |
| MoE incidence | 3 | 428,486 (324,254 - 493,941); 1,518 B per exemplar | 33 kB (12.9x) | `lg:*` columns 56% (almost all zeros), `in:*` 27%, `rd:*` 9%, route_stats 5% |
| Global indices | 7 files | 8,905,578 total | 2.75 MB | subcircuit index 3.5 MB, MoE index 1.7 MB, PA 1.3 MB, islands index 1.0 MB, overrides 1.0 MB |

Estimated full mirror (raw bytes; the number of poles/neurons is exact, per-file sizes are extrapolated from 3-6 samples, so treat as +-25%):

| Component | Files | Estimate |
|---|---|---|
| IS poles | 6,310 | ~1.8 GB (5,291 x 329 kB + 1,019 L14_E78 poles x 77 kB; only one L14_E78 sample) |
| SC poles | 5,477 | 3.1 GB by regression on `n_subcircuits` (samples average 28 subcircuits, the index average is 19.9); 4.5 GB by naive mean |
| LS poles | 5,477 | 8.4 GB (size is nearly constant) |
| NS poles | 5,477 | 3.1 GB by regression; 3.7 GB by naive mean |
| MoE meta + exemplars + incidence | 3 x 3,418 | 2.4 + 1.45 + 1.37 = 5.2 GB (exemplars/incidence scaled by the index total of 899,902 exemplars) |
| indices + pages | 7 + ~8 | 9 MB + 0.4 MB |
| **Total** | **~33,000 files** | **about 22 GB raw (range 21-24 GB); about 5 GB if fetched with gzip** |
| optional `neuron_landscape_data/ctx/{pid}.json` | up to 5,477 | unknown (not sampled) |
| public-sample subset (3,000 poles x SC/LS/NS, 2,906 IS, 2,201 MoE bundles) | ~18,500 | about 13 GB raw by naive means |

Practical consequences: NS adds ~15 kB of unique text per pole if SC is already mirrored (the rest is duplication); 42% of SC is repeated labels of outside neurons that de-duplicate well (6,996 distinct labels in 8,500 rows); incidence matrices are sparse and compress 13x.

---

## 5. Inventory of numeric arrays, matrices, coordinates, weights and neuron references

**Raw weight vectors or embeddings of the neurons are NOT published in any file.** There are no gate/up weight vectors, no residual-stream directions, no token-embedding vectors and no description-embedding vectors. What exists are derived scalars and small matrices: per-firing gate/up pre-activations (MoE exemplars), scalar couplings between neuron pairs (`leaf`/`rest`, `gate_w`/`up_w`, `gate_s`/`up_s`, `w`), per-mode similarity matrices already reduced to K x K (`sim`, `rel_sim`), and the top-k output tokens with a scalar each (`logits`). Any embedding space has to be built by the consumer (for example from the text fields in section 6).

| # | File | JSON path | Shape / length | dtype | Range in samples | Use for analysis |
|---|---|---|---|---|---|---|
| 1 | MoE exemplars | `[].gate_pre`, `[].up_pre`, `[].act` | n_ex (5-1,800; ~280) | float | gate 0.43-3.07, up -3.86-3.20, act -6.24-6.68 | **2-D gate x up coordinates per firing**; `act = silu(gate_pre)*up_pre`; gate-open regime only |
| 2 | MoE exemplars | `[].si`, `[].focal` | n_ex | int | si 5,190-16.7 M, focal 3-1,022 | corpus coordinates; co-occurrence of different neurons on the same token (join on si, focal) |
| 3 | MoE exemplars | `[].router_weight`, `[].route_rank` | n_ex | float, int | 0.007-0.63, 0-7 | routing strength of the expert at the firing |
| 4 | MoE exemplars | `[].window.acts` | <= 81 | float (0.0 where unrouted) | -6.24-6.68 | activation profile around the firing (+-40 tokens) |
| 5 | MoE exemplars | `[].next_token.{id, model_prob}` | n_ex | int, float | id 0-217,208; prob 0-1 | relation to next-token prediction |
| 6 | MoE incidence | `routes["in:{g}:gate"]`, `["in:{g}:up"]` | n_ex each; 2 x (26-34) columns | float >= 0 | 0-2.06 | **exemplar x input-group incidence matrix** (two lanes) - cluster firings by which upstream groups drive them |
| 7 | MoE incidence | `routes["rd:{r}"]` | n_ex; 19-27 columns | float signed | -0.065-0.121 | exemplar x reader-group matrix |
| 8 | MoE incidence | `routes["lg:t{id}"]` | n_ex; 142-192 columns | float signed, about one non-zero per column | -0.55-0.50 | exemplar x next-token logit effect |
| 9 | MoE incidence | `route_stats{route}.{n_active, mean, p90, vmax}` | per route | int/float | - | column summaries |
| 10 | MoE meta | `groups.input[].members[].{gate_w, up_w, gate_s, up_s, gate_pct, up_pct, q1_share, ev_n_active, ev_n_docs}` | 1-115 members per group; ~500 per neuron | float/int | gate_w +-0.088, up_w +-0.047, gate_s -0.013-0.067, up_s -0.062-0.091, pct 0-1 | **neuron -> neuron input edges with two signed weights (gate lane, up lane)**; a 2-D point per upstream neuron |
| 11 | MoE meta | `groups.readers[].members[].{w, r_gate, gate_pct, up_pct, hop, q1_share, ev_n_active}` | 1-23 per group; ~120 per neuron | float/int | w +-0.036, hop 1-35 | neuron -> neuron output edges with layer distance |
| 12 | MoE meta | `groups.*[].members[].{uid, did, pole, layer}` | - | str/int | layers 0-39 | edge endpoints (signed pole of the other neuron) |
| 13 | MoE meta | `groups.input[].agg.{gate,up}.{mean, mean_signed, n_active, n_pos, n_neg}`; `groups.readers[].agg.{mean, n_active}` | per group | float/int | mean_signed -0.28-0.25 | group-level lane contributions (a 2-D point per input group) |
| 14 | MoE meta | `groups.*[].subgroups[].{units[], level, agree, lane.gate_share}` | 1-5 per group | - | gate_share 0.09-0.76 | hierarchical taxonomy of member units |
| 15 | MoE meta | `pole_view.X.inputs[].{aligned_mass, mass_share, dominant_share, mean_contrib, n}`, `.dominant_expert` | 11-16 per pole | float; `L#_E#` | aligned_mass -0.13-2.24 | per-pole input-group ranking; which upstream expert dominates |
| 16 | MoE meta | `pole_view.X.readers[].{n, n_ex, n_ex_other, mean_w}`, `drive_mass`, `oppose_mass`, `n_admitted` | 10-14 per pole | int/float | - | downstream group activity counts |
| 17 | MoE meta | `logits[].{token_id, w, sign, n_active}` | 24 | int/float | w -0.12-0.13 | top output tokens (id into the tokenizer vocabulary) |
| 18 | MoE meta | `identity.quantiles.*`, `identity.gmax` | 8 scalars | float | p0_01 about -1.4 ... top50 1.5-3.6 | activation distribution of the neuron (heavy tails, asymmetry) |
| 19 | MoE meta | `identity.jtable.{jnorm, kappa, dnorm, fp3_ids}` | 3 scalars + 3 ids | float/int | jnorm 0.004-0.013, kappa 0.14-1.04, dnorm 0.45-0.49 | weight-derived scalars (meaning undocumented) |
| 20 | MoE meta | `theta_first.poles.*.{theta, shoulder_lo, structural_gate.frac_routed_on_pole, measured_gate.measured_sign_share, frac_routed_below_theta}` | scalars | float | theta 0.27-0.81 | threshold and gate statistics |
| 21 | MoE index | `neurons[].{n_ex, act_max, act_min}` | 3,418 | int/float | act_max up to 83.0, act_min down to -79.2 | global activation extremes per neuron |
| 22 | SC | `subcircuits{}.circuit.{gate_drivers,gate_opposers,up_drivers,up_opposers}[].{unit, leaf, rest}` | 10 per lane per subcircuit (40 x n_sub edges) | float >= 0 | leaf 0.0001-0.91, rest 0-0.085 | **subcircuit -> upstream neuron edges**, contrastive (leaf vs rest); bipartite graph subcircuit x unit |
| 23 | SC | `subcircuits{}.circuit.readers[].{unit, leaf, rest}` | 10 per subcircuit | float signed | leaf -2.02-1.86 | subcircuit -> downstream neuron edges (signed) |
| 24 | SC | `subcircuits{}.{n, share, median_a_rel}`, `.profile.{ge_theta, mid, weak, closed}` | per subcircuit | int/float | n 20-2,040; profile sums to 1 | cluster sizes; 4-part strength composition (a point on a simplex) |
| 25 | SC | `subcircuits{}.kl.{per_firing, ratio, n_measured}`, `modes[].kl.{per_firing, ratio}`, `kl_neuron_mean` | scalars | float | per_firing 5e-8-0.019 nats, ratio 2e-4-12.7 | causal importance per cluster |
| 26 | SC | `subcircuits{}.exemplars[].{a, a_rel, closed, repeats}` | 12 per subcircuit | float/bool/int | a -5.59-6.79, a_rel 0.011-23.3 | strength of representative firings |
| 27 | SC | `subcircuits{}.exemplars[].tokens[][1]` | 33 per exemplar | float or null | -21.0-23.2 (theta units, signed) | activation profile +-16 tokens |
| 28 | SC | `modes[].share`, `.n_firings`, `.subcircuits[]` | per mode | float/int/ids | - | mode x subcircuit membership matrix |
| 29 | SC | `judge.jev.{bin}.{n, acc, chance}` | up to 5 bins | int/float | acc 0-0.71 | how separable the subcircuits are from text |
| 30 | SC | `theta`, `closed_share`, `main_damage_share`, `reading_confidence`, `function_confidence`, `spec_flags[].{p, p_after}`, `unique_contexts`, `top_context_share` | scalars | - | - | per-pole / per-cluster covariates |
| 31 | LS | `dots[].{a, kl, lk, w, b, m, s, c}` | ~1,000 per pole | float/int/bool | a 0.04-22.9; kl 0-0.14; lk -10.9 .. -0.85 | **per-firing point cloud**: strength x damage x mode x subcircuit x gate sign, with population weights |
| 32 | LS | `dots[].t[][1]` | <= 113 per dot (values on the last ~29) | float or null | -22.3-22.8 | activation profile around each firing |
| 33 | LS | `bands[].{mass, n, a_min, a_med, a_max}` | 5 | float/int | - | rarity ladder (token-frequency scale) |
| 34 | LS | `bands[].counts[k]`, `bands[].dmg_lin[k]`, `bands[].dmg[k]` | 5 x K | int / float | counts 0-1,000; dmg_lin 0-2.64; dmg 0-213 | **band x mode matrices**: how mode composition and damage change with firing strength |
| 35 | LS | `sim` | (K-1) x (K-1) or null | float | 0.011-1 | mode-mode similarity (basis undocumented) |
| 36 | LS | `rel_sim` | (K-1) x (K-1) | float | 0.40-1 | **mode-mode relatedness from topic embeddings of the mode descriptions** - the only embedding-derived geometry in the release |
| 37 | LS | `order`, `rel_order`, `rel_col` | K-1 | int perm, hex colour | - | 1-D seriation of modes and a colour embedding of relatedness |
| 38 | LS | `modes[].{share, kl_per_firing, fold}`, `kl_fit.{slope, r2, n, q}` | K / scalars | float/int | slope 1.76-3.10, r2 0.16-0.69 | damage scaling exponent per pole |
| 39 | NS | `islands_view.modes[].ev` | list of `[a_rel, subcircuit number]` | float, int | 0-23.3 | strengths of non-closed exemplars per mode |
| 40 | NS | `examples[].act`, `full_docs{}.act`, `mode_docs[].act`, `.routed` | 33 / 18-133 | float or null, bool | -13.6-23.2 | activation profiles; `full_docs` have a value on every token |
| 41 | NS | `circuit.inputs[].share`, `circuit.readers[].n`, `circuit.logits[].w`, `threshold_raw` | small lists | float/int | - | copies of MoE numbers |
| 42 | IS | `islands[].n`, `opus.modes[].island_ids` | per island / mode | int | n 8-202 | island sizes; island x mode membership |
| 43 | IS | `islands[].examples[].{act, a_over_theta, focal_idx}`, `.tokens[][1]` | up to 241 tokens | float | act -5.59-6.22; a/theta 1.26-23.4 | long raw-activation profiles (+-120 tokens) |
| 44 | subcircuit index | `poles{}.{n_modes, n_residue, n_subcircuits, main_damage_share}`, `.modes[].share` | 5,477 poles, 22,620 modes | int/float | - | atlas-wide polysemanticity statistics without downloading pole files |
| 45 | ns11 index | `sim{pid}` | 585 | float | -33.6-0.9987 | description quality score |
| 46 | all | L / E / N in every id | - | int | L 0-39, E 0-255, N 0-511 | architectural coordinates: layer depth, expert identity (shared expert = shared router decision) |

Graph edges linking to other neurons, in one list: (a) SC `subcircuits{}.circuit.*[].unit` (5 roles, 50 edges per subcircuit, weights leaf/rest); (b) MoE `groups.input[].members[]` and `groups.readers[].members[]` (signed pole, lane weights, hop); (c) MoE `subgroups[].units[]` (membership only); (d) MoE `pole_view.X.inputs[].dominant_expert` (expert level); (e) implicit same-expert and same-neuron (opposite pole) relations from the ids; (f) LS `modes[].fold` (mode -> mode inside a pole).

---

## 6. Inventory of free-text fields (for full-text search and embeddings)

All analysis text (labels, descriptions, readings, reports) is **English**; 6,091 of 22,620 index mode labels embed non-ASCII quotations (CJK in 2,858, Cyrillic 627, Greek 195, Kana ~350, Hangul 118, Arabic 96, Thai 79). Example passages are real corpus text: by dominant script about 90% Latin, 2.7-4.1% Cyrillic, 2-3% Han, 1-4% Japanese, plus Hangul/Arabic/Thai/Greek below 1% (measured over 2,040 SC exemplars, 5,971 LS dots, 561 IS examples, 847 MoE exemplars from L4/L14/L30 samples; other layers may differ). Mark-up inside labels: «...» = the firing token (449 index labels), `|` = token boundary (623 labels), ▸...◂ in MoE `top_contexts.ctx`, ⟦...⟧ in NS `cue`.

| Field | File | Count | Typical length (chars) | Notes |
|---|---|---|---|---|
| `poles{}.modes[].label` | subcircuit index | 22,620 (22,619 distinct) | 10-384, median 76, total 1.88 M | **best global search corpus**; shell search haystack = these joined by " · " |
| `neurons[].poles.X.desc` (= islands index `name`) | PA | 5,964 non-empty | median 95, max 423, total 0.61 M | island-era description; fallback haystack |
| `{pid}` headline | headline_overrides | 5,469 | 91-236, median 148, total 0.81 M | damage-first rewritten headline |
| `neurons[].label` | MoE index | 3,418 (3,333 distinct) | median 233, max 386, total 0.75 M | positive-pole one-liner; 541 "no discriminative reading" |
| `label` (autointerp) | SC | 1 per pole | 359-590 | long fleet label of the neuron |
| `summary`, `unified_description` | SC | 1 each | 570-758; 677-1,110 | pole-level prose |
| `modes[].label`, `.description`, `.merged_from[]`; `modes_before_unify[].label/description` | SC | 3-21 modes | label 15-113, description 29-408 | description is missing from the global index |
| `subcircuits{}.function_label` | SC | ~20 per pole (109,218 atlas-wide) | 40-94 | short subcircuit title |
| `subcircuits{}.meaning`, `.token_pattern`, `.function`, `.gate_role`, `.up_role` | SC | per subcircuit | 160-628; 98-343; 297-733; 75-293; 87-298 | richest semantic text; about 1.3 kB per subcircuit |
| `subcircuits{}.spec_flags[].rejected` | SC | sparse | 48-300 | rejected hypotheses |
| `subcircuits{}.circuit.*[].label` | SC | 50 per subcircuit | <= 220 (truncated), `(no label)` 2.4% | labels of **other** neurons - a way to label ~7,000 non-atlas neurons per 6 poles |
| `subcircuits{}.exemplars[].pre` + `tokens[][0]` | SC | 12 per subcircuit | pre <= 300; window ~130 | multilingual corpus text |
| `modes[].label`, `.description` (Q3/Q4 modes) | LS | 0-18 per pole | 14-133; 0-408 | only source of Q3/Q4 mode names |
| `dots[].t[][0]` | LS | ~1,000 per pole | ~430 per dot | largest passage corpus per pole |
| `explain.headline`, `.summary`, `.open` | NS | 1 each | ~100; 680-1,010; 214-363 | plain-language account |
| `explain.situations[].{name, explain, typical, cue, scientist}` | NS | 2-20 | 15-45; 87-381; 4-176; 13-84; 0-242 | per-mode explanation and evidence |
| `explain.not_this[]`, `explain.how_we_know[].{claim, test, result}` | NS | 4; 5 | 61-212; 22-235 | negative cases and experiments |
| `explain.contexts{}` | NS | 0-29 | 8-46 | short subcircuit names |
| `pick`, `report` | NS | 1 each | ~2.0 k; ~3.0 k (Markdown) | long-form scientist text |
| `islands[].name`, `.description`; `opus.modes[].name`, `.predicate` | IS | 2-12 islands; 1-3 modes | 19-82; 164-363; 10-97; 0-305 | island-era reading |
| `islands[].examples[].tokens[][0]` | IS | ~70-140 per pole | ~840 per example | longest passages (+-120 tokens) |
| `identity.labels.{short_title_pos, short_title_neg, fleet_label}`, `pole_view.X.line`, `pole_view.prior_titles`, `pole_view.X.prior_read.covering_concept` | MoE meta | per neuron | ~200-270; ~495 | per-pole one-liners |
| `groups.*[].name`, `.description`, `.subgroups[].name/description` | MoE meta | 26-34 input + 19-27 reader groups | 18-78; 85-280; 18-69; 51-202 | topical names of upstream/downstream neuron groups |
| `groups.*[].members[].label`, `.label_full`, `.cat` | MoE meta | ~620 per neuron | <= 100; <= 500; 18-69 | labels of other neurons (both poles) |
| `pole_view.X.top_contexts[].ctx`, `exemplars[].window.tokens` | MoE | 6; n_ex | 22-103; ~290 | passages |
| `logits[].token`, `next_token.str` | MoE | 24; n_ex | 1-15 | token strings |

---

## 7. Text corpus statistics from the global indices

`subcircuit_modes_index.json` mode labels: 22,620 labels over 5,477 poles (5,426 poles with at least one), 22,619 distinct (one duplicate: "Wikipedia 'List of minor planets' wikitable rows"), 1,879,764 characters, length min 10 / median 76 / mean 83.1 / p90 132 / max 384. 10,855 labels contain quoted examples, 15,155 contain parentheses. Latin-word tokens after stop-word removal: 193,354; vocabulary 42,571.

Most frequent words (lower-cased, count): names 1845, chinese 1374, code 1094, name 803, lists 707, math 675, text 668, patent 540, words 524, word 519, opening 497, titles 456, prose 441, legal 437, terms 432, spam 383, named 380, nouns 371, digits 353, numbers 346, citations 339, closing 335, next 327, slot 316, java 308, noun 306, data 296, surname 296, us 286, place 277, inside 277, repeated 277, japanese 271, pages 270, phrases 267, seo 261, lines 261, headers 254, russian 251, german 249, onset 248, reference 247, set 243, wikipedia 235, forum 234, stock 234, head 228, line 224, slots 222, title 222, value 219, verb 216, given 215, time 213, field 212, object 210, citation 209, compounds 208, year 208, subject 205, source 204, number 203, product 202, section 202, date 201, end 201, class 201, clauses 200, surnames 199, category 198, list 193, software 192, tables 188, french 186, web 184, digit 184, page 183, identifiers 183, values 182, clause 181, numeric 181, labels 180, state 180, verbs 176, court 175, brand 173, hex 173, table 172, years 171, author 170, physics 169.

Most frequent bigrams: given name 119, seo spam 103, place names 75, head nouns 70, gmat club 70, proper names 61, veterans appeals 60, source code 52, stock phrases 51, et al 49, digits inside 48, math chain-of-thought 47, us patent 45, word problems 44, reference numerals 44, brand names 42, math physics 40, wikipedia category 40, code identifiers 40, hex hashes 38, uspto office-action 37, classical chinese 37, triple dumps 36, metamath set 32, worked math 32, institution names 30.

Semantic areas this reflects: proper names (people, places, brands, institutions), code and identifiers (Java, SQL, hex hashes), mathematics and worked reasoning, patents and US legal text, web boilerplate and SEO spam, citations/reference lists, tables and listings, and language-specific text (Chinese, Japanese, Russian, German, French). Many labels describe token position ("opening", "closing", "onset", "slot", "inside", "next") rather than topic.

Other global text: PA `desc` 5,964 non-empty (614,382 chars); headline overrides 5,469 (810,681 chars); MoE labels 3,418 (747,319 chars).

---

## 8. How the UI renders it (cross-cutting)

Shell `explorer.html` (its inline script is identical to `shell.js`): left rail with one row per pole (sign, display id, sort value, description), top filters, and three tabs per pole implemented as iframes:

| Tab (key) | Iframe | Data |
|---|---|---|
| Overview (1) | `neuron_overview-mobile-v3.html?shell=1&did=&pole=&mode=` (embeds `neuron_landscape-mobile-v3.html?shell=1&bare=1`) | NS + LS + SC (+ optional ctx) + headline_overrides; disabled when the pid is not in `atlas_index.json` ("No overview for this pole yet") |
| Modes (2) | `subcircuit_modes_view-mobile-v2.html?shell=1&public=1&pole={pid}&data=subcircuit_modes_data` when the pole has a subcircuit read; otherwise `public_islands_atlas-mobile-v2.html?shell=1&pole={pid}&data=public_atlas_data/islands`; otherwise the message "No modes for the positive/negative pole of this neuron." | SC or IS |
| Circuit (3) | `public_circuit_explorer-mobile-v1.html?shell=1&did=&pole=` | MoE bundle; on 404: "No circuit is available for this neuron yet." |

Definitions quoted from the pages (verbatim):
- Landing: "This atlas shows 3,000 randomly selected neuron poles from the expert layers of Qwen3.6-35B-A3B, with examples of text that cause them to fire." / "A neuron's output can be positive or negative, and the two signs often respond to different things, so each neuron has a positive pole and a negative pole, read separately." / "For each pole, we split the firing examples into **subcircuits**: groups of examples where the same earlier parts of the model make the neuron fire. An LLM judge then grouped subcircuits that respond to similar concepts in the text into **modes**. A pole with one mode does one specific thing; a pole with several modes responds to unrelated concepts."
- Islands page: "For one pole, we take its strongest firing examples from a large text corpus and cluster them into **islands**: groups of examples that occur in similar contexts. An LLM judge then grouped islands that respond to similar concepts in the text into **modes**, and named and described each mode. Islands that fit no mode are shown as **unassigned examples**."
- Subcircuit page (research wording): "A **pole** is one sign of a neuron's output, read on its own. θ is the pole's firing threshold, and activations here are given in units of θ. The pole's firings are split into **subcircuits**: clusters of firings that share the same upstream circuit evidence." / "Opus read each subcircuit from text alone (its **meaning** and **token pattern**), then merged subcircuits with the same meaning into **modes**, setting unreadable ones aside as **residue**. Separately, it wrote each subcircuit's **function** from contrastive circuit evidence: the units that drive or oppose the gate and up lanes, and the readers downstream." / "Each subcircuit carries 12 exemplars: its 4 strongest firings, 4 around its median, and its 4 weakest, each from a distinct context. Two firings share a context when the 16 tokens up to the firing token are identical ... **fit** exemplars come from the discovery fold; **held-out** ones come from the audit fold. **closed gate** marks a firing whose gate pre-activation was negative."
- Subcircuit page (public wording): "**Damage per firing** compares how much the model's next-word prediction changes when the neuron is switched off on that mode's firings with the side's average firing." / "**Q3/Q4** firings are ones where the neuron's gate input was negative, so it fired only through a weak leak. **Unassigned examples** fit no mode."
- Evidence table: "**leaf** is the unit's mean contribution over this subcircuit's firings; **rest** is its mean over the pole's other firings. Lane inputs are the excess over the expert's typical neuron."
- Landscape caption: "**Height** is how strong and rare a firing is, from the top 5% of tokens at the bottom to the 1,000 strongest in about a billion tokens at the top; the number on the left is the typical strength there, as a multiple of the firing threshold. Firings were sampled at these five levels; the shape between them is interpolated. **Width** by default is the average damage one firing at that height does: how much the model's next-word prediction changes when the neuron is switched off there." / "About 200 firings per pole were tested directly; damage for the rest is estimated from their strength and mode." / key: "how much the next-word prediction changes (KL, summed over 16 tokens) when the neuron is zeroed at the firing token".
- Overview glossary: "Strength is the neuron's activation in multiples of this pole's firing threshold. Damage is how much the model's next-word prediction changes (KL divergence over the next 16 tokens) when the neuron is zeroed at the firing token ... Shares are of this pole's firings above the top-5% cutoff, weighted to how often they occur in real text ... A *negative pole* is the neuron's output with the opposite sign, read on its own; its strengths are shown as positive multiples. In mode names, a highlighted span marks the firing token and | a token boundary."
- Circuit page: "The neuron computes two signals and multiplies them: its output is **gate × up**. Groups of earlier neurons feed each signal; every group shows a signed number for the gate and one for up (orange positive, slate negative), and the two can differ in sign." / "The gate decides whether the neuron switches on." / "The up signal sets how strongly, and with which sign, the neuron fires once the gate is open." / "On the negative pole the gate is usually open and the up signal is negative, so groups that **lower up** are the ones pushing it to fire."

Token shading recipes differ per view: IS scales by the island's maximum absolute value (own-pole colour, opposite sign in the other colour); SC uses a fixed 0-3 theta ramp; LS/Overview shade relative to the example's own firing token (`alpha = 0.85 * (|v|/peak)^0.7`, nothing below 6%); MoE scales by the window maximum. Pole colours: positive `rgb(224,86,27)`, negative `rgb(51,105,138)`.

---

## 9. User controls and URL parameters

Shell (`explorer.html`):
- Search box `#q` (shortcut `/`): case-insensitive AND over whitespace-separated terms against `did + "L9/E196/N344" form + did with spaces + "positive"/"negative" + description` (description = subcircuit mode labels joined by " · ", else island desc). Substring match only; no stemming. Enter opens the first hit. The query itself is not written to the URL.
- Kind filter `#fCat` (URL `cat=`): All neurons / Single mode (`mono`) / One dominant mode (`fn`) / Multiple modes (`poly`) / Uninterpretable (`dark`).
- Layer filter `#fL` (URL `l=`): All layers (`""`) / "Layers 9-36" (`middle`, default; hides layers 4 and 39) / one layer.
- Sort `#fSort` (URL `sort=`, default `random` omitted): `sim-desc`, `sim-asc` (simulatability), `layer-asc`, `layer-desc`, `modes-desc`, `modes-asc`, `random` (fixed seeded order). Poles without a value sort last.
- Deep link: `?n={did}&pole=pos|neg&tab=overview|islands|circuit&mode={k}` (`tab=landscape` is accepted as overview; `mode` = open-mode index).
- Keys: `/` search, `j`/`k` next/previous pole, `r` random pole, `1`/`2`/`3` tabs, `?` help, `Esc`. Buttons: Random, copy link, show/hide list, Clear filters. List rows render in batches of 150.
- postMessage protocol between shell and tabs: `shell-load {did, pole, mode}`, `shell-pole`, `shell-mode`, `shell-key`; tabs report `landscape-mode`, `overview-mode`, `modes-mode`, `landscape-width`, `landscape-size`.

Landscape page: `shell=1`, `did`, `pole`, `mode`, `data` (data dir, default `neuron_landscape_data`), `bare=1`, `embed=1`, `h` (drawing height, default 420), `rel=off` (fixed palette), `pid`, `set` (standalone). Controls: Height = "Firing strength" | "Damage"; Width = "Damage per firing" | "Frequency" | "Total damage"; click a lane/dot/legend row to select a mode; hover a dot for its text. Standalone only: search, layer select, unit-set select.

Overview page: `shell=1`, `did`, `pole`, `mode`, `pid`, `open=none|dmg`, `rel=off`. Controls: mode picker (ordered and faded by the landscape's width quantity), example rows expand to full context, "show 8 more", arrow keys step modes, Enter expands, Esc clears.

Subcircuit page: `shell=1`, `public=1` (hides research fields), `pole={pid}`, `mode=k`, `data=`; hash `#L4-E1-N212-pos[-all|-mN]`. Controls: mode list / share strip, subcircuit filter box (searches id, function_label, meaning, token_pattern, function, gate_role, up_role, circuit unit ids and exemplar text), Collapse/Expand all, "8 more examples, typical and weakest", "Circuit evidence" tables. The standalone rail filters (layer, pole, class, mode count, search) exist but the embedded list `IDX` is empty in this release.

Islands page: `shell=1` / `embed=1`, `pole=` or `pid=` or `#pid`, `data=`. Standalone controls: search (pid or name substring), layer, pole, sort by layer / random, Random pole; list capped at 600 rows. Per pole: island map bars, mode cards, island accordions, "Show full passage · N tokens".

Circuit page: `shell=1`, `did`, `pole`, `group` (e.g. `in:g1`, `rd:r3`). Controls: neuron select, Random (`R`), pole switch, group cards (click opens members and examples), "Show all N", member list capped at 12 with "Show all N members", standalone picker filter on id or label.

---

## 10. Text fields worth indexing for search

Tier 1 (global, available without per-pole downloads, about 4 M characters): subcircuit index `modes[].label` (+ `share`, `verdict`, `fn_mono`, counts as filters), PA `desc`, headline overrides, MoE index `label`, ns11 `sim` and `model` as sort/filter fields. This already reproduces and exceeds the site search (which only substring-matches mode labels of 3,000 poles).
Tier 2 (per pole, from SC): `summary`, `label`, `modes[].description`, `subcircuits{}.function_label / meaning / token_pattern / function / gate_role / up_role`.
Tier 3 (per pole, from NS): `explain.*`, `pick`, `report`.
Tier 4 (per pole, LS): Q3/Q4 mode labels and descriptions.
Tier 5 (island-era): IS island names/descriptions, opus mode names/predicates; MoE group names/descriptions, `pole_view.X.line`.
Tier 6 (passages, multilingual, large): SC exemplars (`pre` + tokens), LS dots, IS examples, MoE windows. De-duplicate NS examples against SC.
Tier 7 (outside neurons): SC `circuit.*[].label`, MoE `members[].label_full` keyed by the other neuron's id - useful for "which atlas poles are driven by neurons about X".

---

## 11. Numeric / geometric structure usable for analysis

1. Mode geometry inside a pole: LS `rel_sim` (and `sim`) are ready-made K x K similarity matrices; `rel_order` is a seriation; `rel_col` a colour embedding. MDS or clustering on `rel_sim` gives a per-pole map of meanings.
2. Firing point clouds: LS dots give (strength a, log damage lk, band, mode, subcircuit, gate sign, weight w) for ~1,000 firings; MoE exemplars give (gate_pre, up_pre) per firing with exact `act = silu(gate)*up`. LS has the closed-gate flag but no gate/up coordinates; MoE has coordinates but only gate-open firings.
3. Strength x mode composition: LS `bands[].counts` and `dmg_lin` (5 x K) show how the mixture of meanings shifts from the top 5% to the strongest 1,000 firings; population weights are `mass/n`.
4. Bipartite graphs: subcircuit x upstream unit (SC, weights leaf/rest per lane), neuron-pole x input group x member (MoE, gate and up weights), neuron-pole x reader (MoE). Shared outside units link atlas poles (strongest within the same expert).
5. Incidence matrices: MoE `routes` (exemplar x route) allow re-clustering of firings by upstream evidence and comparison with the published groups.
6. Cross-pole text geometry: nothing is published, so embed tier-1/2 text yourself; `L`, `E`, `N`, verdict, n_modes, sim, kl ratios are covariates.
7. Atlas-wide statistics from one file: the subcircuit index alone gives the distribution of mode counts (median 2, mean 4.13, max 30; 2,114 poles with exactly one mode), residue counts, subcircuit counts and main damage shares.

---

## 12. Gotchas and variation across samples

1. Three different pole populations (6,488 / 6,310 / 5,477) and a fourth in the UI (3,000). Join on `pid`; expect 404 for combinations that are not indexed. `L4_E206_N45` has no MoE bundle; four X5477 pids are not in the master list.
2. The MoE index contains 33 non-standard ids (DEMO fixtures and method variants). Do not derive `did` from `layer/expert/neuron` there.
3. "shared" expert ids are supported by one parser but never observed.
4. Mode indices agree only for open modes; SC residue modes, LS Q3/Q4 + unassigned, and NS (no closed-gate residue) diverge afterwards; single-mode poles reuse the `mode` field for subcircuit numbers; failed reads (51 poles with `n_modes` 0, and cases like `L4_E1_N345__neg` with zero-share modes) break the open-mode alignment between SC and LS.
5. Theta differs between SC/NS/LS (per pole) and IS/MoE (per neuron, same for both poles) by up to 2.5x.
6. Three sign conventions for token values: SC exemplar tokens are signed a/theta (negative on negative poles); NS `act`, LS dots are in pole direction (positive = fires this pole); IS and MoE are raw signed activations.
7. "Not routed" is `null` in SC/NS/LS, `routed:false` in NS, but `0.0` in MoE windows; IS has no nulls.
8. Shares: stored `share` values are sample shares; the UI prints population-weighted shares from `bands`. IS shares exist only as text chips ("firing 74.2%").
9. Stored headline strength and the token value at the focal index disagree slightly (SC), for 10-74% of dots (LS) or for most examples (IS); see 3.4.
10. Padding: SC/NS token windows contain `""` tokens at document edges (the JS skips them); LS `t` has `u` leading tokens without values; windows can be shorter than nominal (IS 42-241, LS 13-113, MoE 42-81).
11. Optional or polymorphic fields: LS `sim` may be null; LS last mode lacks `closed/rel/fold` and has `share:null`; SC `modes[].kl` may be null, `n_firings` missing on residue modes, `modes_before_unify` and friends missing; SC `judge.opus` empty, `jev.mid/weak` may be missing; NS `kl`, `explain.contexts`, `mode_docs`, `islands_view.islands`, `circuit.inputs/readers` may be empty; `share` is sometimes an int (0); MoE `human` missing/null, `verdict` may be `{}`, `grouping` null; incidence columns may be arrays or `{q, scale}` base64; `pole_view.X.prior_read` null or dict.
12. Key order of MoE index rows varies (8 different orders); do not rely on positional parsing.
13. Encoding: mixed raw UTF-8 and `\u` escapes; token strings can be partial multi-byte characters shown as U+FFFD by the page (none in the samples), can be whitespace-only, and contain newlines.
14. NS duplicates SC exemplars twice (about 77% of the file); the MoE verdict vocabulary (`mono/poly/dark/unresolved`) is not the PA `cat` vocabulary (`mono/poly/dark/null`) nor the subcircuit verdict (`monosemantic/polysemantic` + `fn_mono`).
15. Files referenced by pages but not sampled (`neuron_landscape_data/ctx/{pid}.json`, `neuron_landscape_data/index.json`) may or may not exist upstream; a downloader should probe and tolerate 404.
16. Sample bias: 5 of 6 pole samples are from one expert (L4_E1); MoE and IS L14_E78 have one sample each; no `glm`-model NS file, no `has_kl:false` landscape and no quantized incidence file was available. Size and range figures are indicative.
17. Many fields are not read by any public page (NS `islands_view`, `mode_docs`, `full_docs`, `circuit`, `examples`; LS `sim`, `rel_sim`, `dmg`, `dots[].s`; SC `spec_flags`, `modes_before_unify`, confidences; most MoE meta numerics). Their meanings above are inferred unless a data identity is stated.
