# Circuit tab ("public circuit explorer") - data schema and behaviour

Scope: `public_circuit_explorer-mobile-v1.html` (52,406 bytes, 806 lines, one inline `<script>`) and its data under
`moe_circuit_explorer_data/`. Release base: `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/`.

Sources read (local copies only, nothing fetched):

| file | bytes | role |
|---|---|---|
| `public_circuit_explorer-mobile-v1.html` | 52,406 | the view page (JS read in full) |
| `moe_circuit_explorer_data/index.json` | 1,718,156 | global list, 3,418 entries |
| `moe_circuit_explorer_data/L30_E47_N449/{meta,exemplars,incidence}.json` | 658,060 / 383,382 / 324,254 | sample 1 |
| `moe_circuit_explorer_data/L4_E1_N177/{meta,exemplars,incidence}.json` | 707,301 / 504,964 / 493,941 | sample 2 |
| `moe_circuit_explorer_data/L14_E78_N0/{meta,exemplars,incidence}.json` | 704,566 / 472,418 / 467,263 | sample 3 |
| `shell.js` lines 201-205, 327 | | how the atlas shell embeds and drives the page |

Evidence markers used below:

- **[V-JS]** proven by how the page's JavaScript reads/uses the field, or by a comment in the page.
- **[V-DATA]** checked numerically on the three samples (and on all 3,418 rows for `index.json`).
- **[I]** inferred from names / values only. Not proven. Treat as a hypothesis.
- **unused** = the public page never reads the field.

All three samples have `index.grouping == "propose_assign_v1"` and `meta.schema == "moe-circuit-explorer-1.0"`. Other bundle
variants exist in the index (section 7) and were NOT inspected.

---

## 0. The model behind the data (terms the page defines)

- One page = one **neuron** (`did`, e.g. `L30_E47_N449` = layer / expert / neuron) seen through one **pole** (`+` or `-`).
  Page text: *"A neuron's output can be positive or negative, and each sign is read separately."*
- **gate x up**. Page text: *"The neuron computes two signals and multiplies them: its output is **gate x up**."*
  JS comment: *"The neuron's activation is (gate signal) x (up signal)."*
  **[V-DATA]** in all 847 sample exemplars `act == silu(gate_pre) * up_pre` with `silu(x)=x/(1+e^-x)`, max abs error 2.3e-4
  (4-decimal rounding). So "gate" and "up" are the two branches of a SwiGLU MLP neuron.
- **Gate inputs**: *"The gate decides whether the neuron switches on."*
- **Up inputs**: *"The up signal sets how strongly, and with which sign, the neuron fires once the gate is open."*
- Negative pole: *"On the negative pole the gate is usually open and the up signal is negative, so groups that **lower up** are the
  ones pushing it to fire."*
- **Input group** = a named group of EARLIER neurons (strictly lower layer) that write into the gate and up signals.
  **Reader / downstream group** = a named group of LATER neurons (strictly higher layer) that this neuron raises or lowers.
  File header comment: *"For one pole (+ or -) of one neuron it shows the groups of earlier neurons that push it to fire or hold
  it back, and the groups of later neurons it raises or lowers."*
- **theta** = *"the threshold at which this pole starts to fire"*; strengths are shown as multiples of it.
- Architecture context, from the accompanying article `neurons-as-an-interpretable-basis.html` (not part of the atlas folder):
  *"40 layers, and experts composed of 512 neurons each. This model has 256 experts per layer, and at each step of inference 8
  experts are chosen per layer by the router (plus one shared expert that is always active)"*; *"SwiGLU MLPs have both gate and
  up"* projections. This matches the data: layers 0..39, experts 0..255, neurons 0..511, `route_rank` 0..7.

---

## 1. `moe_circuit_explorer_data/index.json` (global)

Loaded only in standalone mode (not with `?shell=1`). The page reads exactly three things from it: `neurons[].did`,
`neurons[].label`, `neurons[].verdict` **[V-JS]**. Everything else is unused by the page.

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `model` | string | no | model name. unused | `"Qwen3.6-35B-A3B"` |
| `built_at` | string, ISO-8601 UTC | no | build time. unused | `"2026-10-15T23:26:20Z"` |
| `neurons` | array, 3,418 objects | no | one row per circuit bundle. Sorted by `(layer, expert, neuron)` **[V-DATA]**. `did` unique **[V-DATA]** | |
| `neurons[].did` | string | no | bundle id = folder name under `moe_circuit_explorer_data/`. 3,385 match `^L\d+_E\d+_N\d+$`; 33 do NOT (section 7) | `"L30_E47_N449"` |
| `neurons[].layer` | int 2..39 | no | layer. Equals the `L` part of `did` for all standard ids **[V-DATA]**. unused (page parses `did` instead) | `30` |
| `neurons[].expert` | int 0..255 | no | MoE expert index | `47` |
| `neurons[].neuron` | int 0..511 (3 `DEMO_*` rows: 3877, 9042, 15330) | no | neuron index inside the expert | `449` |
| `neurons[].label` | string, 0..386 chars (median 233; 1 empty: `L13_E222_N39`) | no | one-line description shown on the picker card and searched by the picker filter **[V-JS]**. In the 3 samples it equals `meta.identity.labels.short_title_pos` and `meta.pole_view["+"].line` **[V-DATA]**, i.e. it describes the POSITIVE pole. 2,521 start with `"Fires"`; 541 start with `"no discriminative reading"` (e.g. `"no discriminative reading; recurring tokens: 'Cellular', 'SS', 'ARC'"`) | `"Fires on mixed-case letter chunks inside random base64-style alphanumeric strings ..."` |
| `neurons[].n_ex` | int 5..1800 (median 283; 7 rows < 50) | no | number of stored exemplars = `len(exemplars.json)` **[V-DATA, 3 samples]**. unused | `253` |
| `neurons[].act_max` | float (4 dp), -12.29..83.03 | no | max exemplar `act` **[V-DATA, 3 samples]**. Negative for 87 rows (no positive exemplars). unused | `4.3419` |
| `neurons[].act_min` | float (4 dp), -79.25..11.10 | no | min exemplar `act` **[V-DATA, 3 samples]**. Positive for 133 rows (no negative exemplars). unused | `-6.2426` |
| `neurons[].dark` | string, one of `""` (2,200), `"-"` (458), `"+"` (421), `"+-"` (339) | no | which poles are flagged dark. Every pole with `verdict == "dark"` is in it (1,346 poles) plus 211 poles with another verdict **[V-DATA]**. Definition **[I]**: pole judged uninterpretable. unused | `"-"` |
| `neurons[].grouping` | string or null | no | name of the grouping pipeline that built the bundle **[I]**: `"propose_assign_v1"` 3,310; `"effect_first_pole_units"` 75; `"taylor_modes_then_labels"` 6; `null` 27. unused | `"propose_assign_v1"` |
| `neurons[].verdict` | object, keys subset of `"+"`, `"-"` | no (may be `{}`) | per-pole verdict. Values: `"mono"` 2,839, `"unresolved"` 1,406, `"dark"` 1,346, `"poly"` 719. Key sets: both 3,067; `{}` 175; only `"-"` 98; only `"+"` 78. Used for the picker chips and for showing the islands link **[V-JS]**. Page mapping `VERD={mono:"single mode", poly:"multiple modes", dark:"uninterpretable"}`; `unresolved` gets no chip | `{"+":"unresolved","-":"dark"}` |
| `neurons[].human` | object, keys subset of `"+"`, `"-"` | **yes** (key present on 152 rows, non-empty on 71) | hand label per pole **[I]**: `"uninterpretable"` 42, `"mono"` 32, `"poly"` 28. unused | `{"+":"poly"}` |

Key order inside a row varies (5 orders seen); do not parse positionally.

`verdict` is NOT the category the atlas shell shows: against `public_atlas_data/index.json` `poles[s].cat` it agrees on only
3,049 of 6,486 poles (e.g. 1,028 poles are `unresolved` here but `mono` there) **[V-DATA]**. Keep them as separate fields.

---

## 2. `moe_circuit_explorer_data/{did}/meta.json` (keyed by did)

Top level: object with exactly these 10 keys in all 3 samples:
`pole_view, schema, identity, groups, logits, quadrant_evidence, taxonomy_gate, gates_summary, provenance, theta_first`.

What the page reads **[V-JS]**: `groups.input[]`, `groups.readers[]` (fields listed below), `pole_view[sign].{readers,n_admitted,
line,pole_real}`, `identity.{did,layer,expert,neuron,labels.short_title_pos,labels.short_title_neg}`,
`theta_first.poles.{positive,negative}.theta`. Nothing else.

### 2.1 Top-level scalars and `identity`

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `schema` | string | no | bundle schema id. unused | `"moe-circuit-explorer-1.0"` |
| `identity.did` | string | no | neuron id; shown as header (`id.did \|\| S.did`) **[V-JS]** | `"L30_E47_N449"` |
| `identity.layer` / `.expert` / `.neuron` | int | no | shown as `layer 30 · expert 47 · neuron 449` (standalone header) **[V-JS]** | `30` / `47` / `449` |
| `identity.labels.short_title_pos` | string | no | title of the + pole; fallback for the header line when `pole_view["+"].line` is empty **[V-JS]**. Equal to `pole_view["+"].line` in samples **[V-DATA]** | `"Fires on mixed-case letter chunks ..."` |
| `identity.labels.short_title_neg` | string | no | same for the - pole | `"Fires on individual characters ... inside long random base64-style ..."` |
| `identity.labels.fleet_label` | string (longer, ~500 chars) | no | older auto-label of the neuron **[I]**; `pole_view.prior_titles["+"]` is a shortened form of it. unused | `"Specific letters or letter sequences in technical or encoded contexts, ..."` |
| `identity.quantiles` | object: `p0_01, p0_1, p90, p95, p99, p99_9, p99_99, top50` -> float | no | activation quantiles of the neuron over the corpus **[I]** (names match the exemplar `band` names). unused | `{"p0_01":-1.2734,...,"top50":1.4844}` |
| `identity.gmax` | float | no | equals `quantiles.top50` in all samples **[V-DATA]**; meaning **[I]** unknown. unused | `1.4844` |
| `identity.jtable` | object `{jnorm, kappa, dnorm: float, fp3_ids: int[3], fp3_tokens: string[3]}` | no | static per-neuron statistics and 3 token ids/strings. Meaning not given by the page. unused | `{"jnorm":0.0128,"kappa":0.1421,"dnorm":0.4851,"fp3_ids":[23241,44968,101268],"fp3_tokens":[" COUNT","Contain","膨"]}` |
| `quadrant_evidence` | object | no | `{}` in all samples. unused | `{}` |
| `taxonomy_gate` | `{verdict: string, real_score: float, null_p90: float}` | no | quality gate of the grouping **[I]** (`"PASS"`; real 0.78-0.84 vs null 0.65-0.77). unused | `{"verdict":"PASS","real_score":0.7761,"null_p90":0.6545}` |
| `gates_summary` | `{n_sampled: int, n_kept: int, skip_reasons: {<reason>: int}}` | no | exemplar pipeline counts. `n_kept == len(exemplars.json) == index.n_ex` **[V-DATA]**. `skip_reasons` keys vary: `edge_truncated, roundtrip_drift, exception, fidelity, unrouted`. unused | `{"n_sampled":300,"n_kept":253,"skip_reasons":{...}}` |
| `provenance` | `{built_at, wave, git_sha, moe_relp_sha256, active_threshold_def: string, configs: {input_max_nodes: int, edge_max_nodes: int, fidelity_dev: float}}` | no | build info. `active_threshold_def` = `"input: \|v\|>=5% of exemplar lane mass; readers: \|v\|>=5% of exemplar \|reader\| mass; logits: v!=0"` (defines `n_active`). `configs` = 800 / 500 / 0.2. unused | `"wave":"prototype"` |
| `logits[]` | array of 24 objects | no | output-token effects. 14 with `source:"static"` (`w` about +-0.1) then 10 with `source:"target"` (small `w`, plus `n_active`). unused | |
| `logits[].token` / `.token_id` | string / int | no | token text / vocabulary id | `"plode"` / `68350` |
| `logits[].w` | float | no | static: signed logit weight **[I]**. target: equals `incidence.route_stats["lg:t<token_id>"].mean` **[V-DATA]** | `0.1302` |
| `logits[].sign` | int `1` or `-1` | no | sign of `w`. Static rows: 24 x `1`, 18 x `-1`; target rows: always `1` in samples (their `w` is an unsigned mean) **[V-DATA]** | `1` |
| `logits[].source` | `"static"` or `"target"` | no | | |
| `logits[].n_active` | int | **yes** (target rows only) | equals `route_stats["lg:t<id>"].n_active` **[V-DATA]** | `6` |

### 2.2 `theta_first` (firing threshold and exemplar admission)

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `theta_first.applied` | bool | no | unused | `true` |
| `theta_first.poles.positive.theta` / `.negative.theta` | float > 0 | no | **the pole's firing threshold**. Page: `sideTheta()` returns it if `> 0`; example strengths are shown as `\|act\| / theta` with one decimal and `x` **[V-JS]**. Same value for both poles in all 3 samples, and equal to `pole_view[s].theta` **[V-DATA]** | `0.805838` |
| `...poles.<p>.theta_source` / `.table_flag` | string | no | `"theta_table_v2"` / `"predicted"`. unused | |
| `...poles.<p>.shoulder_lo` | float | no | lower edge of the "shoulder" band; = 2/3 of theta in samples **[V-DATA]**. unused | `0.537225` |
| `...poles.<p>.pole_real` | bool | no | pole is real (fires enough). unused here (page uses `pole_view[s].pole_real`) | `true` |
| `...poles.<p>.structural_gate` | `{real: bool, frac_routed_on_pole: float}` | no | unused | |
| `...poles.<p>.measured_gate` | `{measured_sign_share: float, n_events: int, real: bool, reasons: []}` | no | unused | |
| `...poles.<p>.n_candidates` | int | no | unused | `138` |
| `...poles.<p>.n_admitted` | `{core: int, shoulder: int, sub_theta_context: int}` | no | exemplars admitted for the pole by band relative to theta **[I]**. Sum equals `pole_view[s].n_admitted` **[V-DATA]** | `{"core":115,"shoulder":4,"sub_theta_context":6}` |
| `...poles.<p>.frac_routed_below_theta` | float | no | unused | |
| `theta_first.counts` | `{input, gate_closed_dropped, admitted, dropped_sub_theta: int}` | no | `admitted` = sum of both poles' `n_admitted` **[V-DATA]** | `{"input":283,"gate_closed_dropped":3,"admitted":256,"dropped_sub_theta":24}` |
| `theta_first.doc` | string | no | internal doc path. unused | `"analysis/circuit_explorer/EXEMPLAR_SELECTION_THETA.md"` |

### 2.3 `pole_view` (per-pole summary; the page uses it for the downstream column)

Keys: `"+"`, `"-"`, `"built_at"` (ISO string), `"prior_titles"` (`{"+": string\|null, "-": string\|null}`, older titles, unused).

| JSON path (`s` = `"+"` or `"-"`) | type | optional? | meaning | example |
|---|---|---|---|---|
| `pole_view[s].n_admitted` | int | no | number of examples of this pole; denominator in *"in X of Y examples"* **[V-JS]**. Can exceed the number of side exemplars in `exemplars.json` by a few (125 vs 124, 131 vs 129, 150 vs 147) **[V-DATA]** | `125` |
| `pole_view[s].theta` | float | no | = `theta_first.poles.<p>.theta`. unused | `0.805838` |
| `pole_view[s].pole_real` | bool | no | if `false` the page shows only *"This pole of the neuron fires too rarely to have a circuit to show."* **[V-JS]** | `true` |
| `pole_view[s].line` | string | no (page falls back to `identity.labels.short_title_*` if empty) | title line of the pole **[V-JS]**; a leading `"no discriminative reading"` is displayed as `"Uninterpretable: "` | |
| `pole_view[s].readers[]` | array (10-14 in samples) | no | **the downstream groups shown for this pole** **[V-JS]**. Sorted by `n_ex` desc **[V-DATA]**. Never contains the `r_single_*` groups **[V-DATA]** | |
| `...readers[].id` | string | no | joins to `groups.readers[].id`; rows with no match are skipped **[V-JS]** | `"r8"` |
| `...readers[].effect` | `"+"` or `"-"` | no | what the page shows: `"-"` => *suppressed* (slate, dashed, down arrow), anything else => *activated* (orange, up arrow) **[V-JS]**. Always equal to the group's own `effect` **[V-DATA]** - see gotcha G4 | `"+"` |
| `...readers[].n_ex` | int | no | number of this pole's examples in which the group is touched; card text and line thickness **[V-JS]**. Equals the count of the pole's stored exemplars with a non-zero `rd:<id>` value in 57 of 74 rows, and is 1-4 larger in the other 17 **[V-DATA]** | `64` |
| `...readers[].n_ex_other` | int | no | same count on the other pole's examples **[V-DATA approx]**. unused | `56` |
| `...readers[].mean_w` | float (4 dp) | no | mean signed `rd:<id>` value over the `n_ex` touched examples of this pole: `sum(rd column over the pole's exemplars) / n_ex` reproduces it in 74 of 74 rows (max diff 5e-5) **[V-DATA]**. Its sign is the measured direction on this pole. unused | `-0.0039` |
| `...readers[].name` / `.n` / `.color` | string / int / string | no | copies of the group's `name`, member count, `color` **[V-DATA]**. unused | |
| `pole_view[s].inputs[]` | array (11-16) | no | per-pole input summary. **unused by the page** (inputs are taken from `groups.input`). Contains only the non-misc groups whose `bucket` starts with `s` **[V-DATA]** | |
| `...inputs[].id` / `.name` / `.n` / `.color` / `.effect` | | no | copies of the group fields **[V-DATA]** | `"g1"` |
| `...inputs[].role` | `"drives"` or `"opposes"` | no | = second half of the group's `bucket` **[V-DATA]** | `"drives"` |
| `...inputs[].lane_effect` | `"+"` or `"-"` | no | = `effect` in all rows **[V-DATA]** | `"+"` |
| `...inputs[].aligned_mass` | float | no | signed mass aligned with the pole **[I]**; `> 0` exactly for `drives` groups **[V-DATA]**; sums per role to `drive_mass` / `oppose_mass` **[V-DATA]** | `0.6917` |
| `...inputs[].mean_contrib` | float | no | approx. mean over this pole's examples of (signed gate + signed up contribution) **[V-DATA approx]** | `0.4703` |
| `...inputs[].mass_share` | float 0..1 | no | approx. share of the group's incidence mass on this pole's examples **[I]** | `0.736` |
| `...inputs[].dominant_expert` | string `L<layer>_E<expert>` | no | the expert contributing most members **[V-DATA]** | `"L29_E121"` |
| `...inputs[].dominant_share` | float 0..1 | no | fraction of the group's members from that expert **[V-DATA]** | `0.13` |
| `pole_view[s].drive_mass` / `.oppose_mass` | float | no | sums of `aligned_mass` over `drives` / `opposes` **[V-DATA]**. unused | `1.2438` / `-0.223` |
| `pole_view[s].cancel_note` | null | no | always null in samples. unused | |
| `pole_view[s].top_contexts[]` | array of 6 `{ctx: string, act: float, ex_id: string}` | no | the 6 strongest exemplars of the pole; `ctx` = 12 tokens before + `▸focal◂` + 4 tokens after **[V-DATA]**; `ex_id` joins to `exemplars.json`. unused | `{"ctx":"X8KiAGmp1wuF1wuV9▸n◂XWVl","act":4.3419,"ex_id":"top1000_gateopen_10967528_147"}` |
| `pole_view[s].prior_read` | null or `{verdict: string, covering_concept: string}` | no | earlier reading of the pole **[I]** (`"structural_unit"`, `"mono"`). unused | |

### 2.4 `groups.input[]` (upstream groups; 26-34 per neuron in samples)

Order in file: `+|drives` groups (`g1..`), `g_single_pd`, `+|opposes` groups, `g_single_po`, `-|drives` groups, `g_single_nd`,
`-|opposes` groups, `g_single_no` **[V-DATA]**.

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `id` | string | no | group id, unique inside the file. `g<N>` or one of the four catch-alls `g_single_pd`, `g_single_po`, `g_single_nd`, `g_single_no` (p/n = pole, d/o = drives/opposes). Card key = `"in:"+id` **[V-JS]** | `"g1"` |
| `name` | string | no | display name + internal lane tag after `" · "`. Page strips the tag with `/\s+·\s+(gate\|up-lane\|up)\b.*$/i`; empty => `"Unnamed group"` **[V-JS]**. Tags seen: `gate + / up +`, `gate + / up −`, `gate − / up +`, `gate − / up −`, `gate-lane +`, `gate-lane −`, `up-lane +`, `up-lane −` (minus is U+2212). Catch-all names are NOT stripped: `"single units · + pole, drives (no category with ≥2 units; by contribution)"` | `"Encoded and alphanumeric strings · gate + / up +"` |
| `description` | string | no | `"<s> pole, <drives\|opposes> — "` followed by either `"N categories: A (n) · B (n) ..."` or one sentence. Page (`cleanDesc`): drops the prefix up to `" — "` when it contains `pole`; returns `""` if the rest starts with `N categories:`; replaces a leading `Units` by `Neurons`. Shown only when the group has no named subgroups **[V-JS]**. Catch-alls: `"4 units whose label matched no category shared by another unit in this bucket; listed individually, strongest contribution first."` | `"+ pole, drives — 3 categories: Encoded strings and alphanumeric codes (76) · ..."` |
| `bucket` | string `"<s>\|<role>"` | **yes** (absent on the 4 catch-alls) | `s` = pole of THIS neuron the group belongs to; role = `drives` or `opposes`. Page: `groupSide` takes `bucket[0]` when it matches `^[+-]\|` **[V-JS]** | `"+\|drives"` |
| `family` | `"pos"` or `"neg"` | no | non-misc: sign of `agg.gate.mean_signed + agg.up.mean_signed` (82/82) **[V-DATA]**; catch-alls: the pole (`pos` for + pole). unused | `"pos"` |
| `effect` | `"+"`, `"-"` or null (catch-alls) | no | same sign as `family` for non-misc groups **[V-DATA]**. This is the NET lane sign, not drives/opposes. unused | `"+"` |
| `color` | string hex | no | original-explorer colour (blue ramp `#104281..#bcd6f5` = positive, red ramp `#7f1d1c..#f8cfce` = negative, `#9aa2ad` = catch-all). **unused**; the public page colours by sign only | `"#104281"` |
| `is_misc` | bool | no | `true` on the 4 catch-all groups. Not read for inputs (catch-alls ARE shown in the input lanes) **[V-JS]** | `false` |
| `kind` | null | no | always null. unused | |
| `subgroups[]` | array (1-5) | **yes** (absent on catch-alls) | named sub-categories; panel section *"What the group is about"* lists `name: description` for each with a `name` **[V-JS]** | |
| `subgroups[].name` | string | no | category name; equals `members[].cat` of its members **[V-DATA]** | `"Encoded strings and alphanumeric codes"` |
| `subgroups[].description` | string | no | one sentence; leading `Units` shown as `Neurons` **[V-JS]** | `"Units for hex digit pairs, hashes (MD5/SHA), ..."` |
| `subgroups[].units` | string[] of member `uid`s (2-102) | no | exactly the members with that `cat`; all subgroups together = all members **[V-DATA]**. unused | `["L29_E121_N166-", ...]` |
| `subgroups[].level` | int 1..4 or null | no | taxonomy level **[I]**. unused | `2` |
| `subgroups[].agree` | float 0..1 | no | fraction of the subgroup's members whose `agree` is `"3/3"` **[V-DATA]**. unused | `0.86` |
| `subgroups[].lane` | `{gate_share: float, gate_sign: "+"\|"−", up_sign: "+"\|"−", tag: string}` | no (input groups only) | lane split of the subgroup. `tag` is `gate-lane` when `gate_share >= 0.70`, `up-lane` when `<= ~0.30`, else `gate ± / up ±` **[V-DATA]**. unused | `{"gate_share":0.51,"gate_sign":"+","up_sign":"+","tag":"gate + / up +"}` |
| `agg.gate.mean_signed`, `agg.up.mean_signed` | float (5 dp) | no | **the number on the card**: the group's summed signed contribution to that signal **[V-JS]** (comment: *"the group's summed signed contribution to that lane"*; legend: *"Numbers are each group's summed contribution to that signal"*). Equals the sum of the members' `gate_s` / `up_s` (all 94 groups, within rounding) **[V-DATA]**. Positive = raises the signal, negative = lowers it | `0.17471` / `0.13832` |
| `agg.<lane>.n_pos`, `.n_neg` | int | no | members with `<lane>_s > 0` / `< 0` (exact, 94/94) **[V-DATA]**. Panel: *"69 members positive, 16 negative"* **[V-JS]** | `69` / `16` |
| `agg.<lane>.mean` | float | no | mean over ALL exemplars of the unsigned incidence column `in:<id>:<lane>`; equals `incidence.route_stats[...].mean` **[V-DATA]**. `\|mean_signed\| <= mean` always **[V-DATA]**. unused | `0.19732` |
| `agg.<lane>.n_active` | int | no | = `route_stats[...].n_active` **[V-DATA]** (see section 4). unused | `51` |
| `members[]` | array (1-115) | no | the member neurons = the graph edges. Panel *"Member neurons"* **[V-JS]** | |

`members[]` of an input group (19 keys, +2 optional):

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `uid` | string = `did + pole` **[V-DATA]** | no | neuron-pole id. Unique across ALL input groups of the file **[V-DATA]**. unused | `"L29_E121_N114-"` |
| `did` | string | no | the OTHER neuron. Shown with `_` replaced by `/` **[V-JS]**. All 1,499 sample members match `^L\d+_E\d+_N\d+$` | `"L29_E121_N114"` |
| `pole` | `"+"` or `"-"` | no (page tolerates absence: blank) | the pole of the OTHER neuron that takes part. Tooltip: *"This neuron takes part through its positive pole"* **[V-JS]** | `"-"` |
| `layer` | int | no | layer of the member = `L` in its `did`; always `<` this neuron's layer (0..29 for an L30 neuron) **[V-DATA]**. unused | `29` |
| `label` | string, max 100 chars | no | first 100 chars of `label_full` (hard cut) **[V-DATA]**. Fallback text **[V-JS]** | |
| `label_full` | string, max 500 chars (cut at 500) | no | description of the member neuron-pole; shown in the member row (`label_full \|\| label \|\| "no description"`) **[V-JS]** | `"Single-character tokens (letters, numbers, or symbols) or ..."` |
| `label_source` | string | no | where the label came from **[I]**: `fleet+`, `fleet-`, `short_title+`, `short_title-`, `fleet+ (cross-pole)`, `fleet- (cross-pole)`. unused | `"fleet-"` |
| `gate_s` | float (5 dp) | no (page prints nothing if not a number) | **signed contribution of this member to the gate signal.** Member row: `Gate +.036` coloured orange if `> 0`, slate if `< 0`, grey if 0 **[V-JS]**. Sum over members = `agg.gate.mean_signed` **[V-DATA]** | `0.0362` |
| `up_s` | float (5 dp) | no | same for the up signal: `Up +.045` **[V-JS]** | `0.04536` |
| `gate_w`, `up_w` | float (5 dp) | no | static coupling of the member to the gate / up input **[I]**. `sign(gate_s) == sign(gate_w) * (pole=="+" ? 1 : -1)` for 94.4% of the 1,499 sample members, 94.3% for up **[V-DATA]** (i.e. `_s` is `_w` seen through the member's pole, in most cases). unused | `-0.03574` / `-0.04369` |
| `gate_pct`, `up_pct` | float 0..1 | no | percentile rank of `\|gate_w\|` / `\|up_w\|`; monotone in `\|w\|` within one source expert (3 inversions in 427 pairs) **[V-DATA]**, so ranked per source expert **[I]**. unused | `0.99992` |
| `coupling_exact` | bool | no | always `true`. unused | |
| `relp` | bool | no | always `true` (contribution from the RelP attribution method, cf. `provenance.moe_relp_sha256`) **[I]**. unused | |
| `q1_share` | float 0..1 | no | unknown. unused | `0.7056` |
| `ev_n_active`, `ev_n_docs` | int (3..130), equal to each other in all rows **[V-DATA]** | no | number of exemplars in which the member is active **[I]**. unused | `47` |
| `cat` | string | **yes** (absent in catch-all groups) | subgroup name | `"Encoded strings and alphanumeric codes"` |
| `agree` | string `"k/3"` (`3/3`, `2/3`, `0/3`) | **yes** (absent in catch-all groups) | agreement of 3 category assignments **[I]**. unused | `"0/3"` |
| `sim` | null | no | always null. unused | |

### 2.5 `groups.readers[]` (downstream groups; 19-27 per neuron)

Order in file: `+|raised` (`r1..`), `r_single_pr`, `+|lowered`, `r_single_pl`, `-|raised`, `r_single_nr`, `-|lowered`,
`r_single_nl` **[V-DATA]**.

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `id` | string | no | `r<N>` or `r_single_pr / _pl / _nr / _nl`. Card key = `"rd:"+id` **[V-JS]** | `"r8"` |
| `name` | string | no | display name; no lane tag on reader groups. Catch-alls: `"single units · - pole, raised by (no category with ≥2 units; by contribution)"` | `"Alphanumeric codes"` |
| `description` | string | no | `"<s> pole, <raised by\|lowered by> — "` + `N categories: ...` or a sentence (same cleaning as inputs) | `"+ pole, raised by — 2 categories: ..."` |
| `bucket` | string `"<s>\|raised"` or `"<s>\|lowered"` | **yes** (absent on catch-alls) | `s` = pole of THIS neuron; `raised` = that pole activates the group, `lowered` = suppresses it. Used by the page only when `pole_view[s]` is missing (fallback) **[V-JS]** | `"-\|raised"` |
| `effect` | `"+"`, `"-"`, null (catch-alls) | no | `+` for `raised`, `-` for `lowered` **[V-DATA]**. unused (page takes `pole_view[s].readers[].effect`) | `"+"` |
| `color` | hex string | no | `#bcd6f5` raised, `#f8cfce` lowered, `#9aa2ad` catch-all. unused | |
| `is_misc` | bool | no | catch-alls; skipped in the fallback path **[V-JS]** | |
| `kind`, `hop_min`, `hop_max` | null | no | always null. unused | |
| `causal` | bool | no | always `false`. unused | |
| `subgroups[]` | array (1-3) of `{name, description, units, level, agree}` | **yes** (absent on catch-alls) | as for inputs but WITHOUT `lane` | |
| `agg` | `{mean: float, n_active: int, mean_signed: 0.0, n_pos: 0, n_neg: 0}` | no | `mean` = mean of `\|rd:<id>\|` over all exemplars = `route_stats.mean` **[V-DATA]**; fallback line weight **[V-JS]**. `mean_signed`, `n_pos`, `n_neg` are always 0 (not filled) | `{"mean":0.00289,"n_active":84,...}` |
| `members[]` | array (1-23) | no | the downstream neurons | |

`members[]` of a reader group (14-21 keys):

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `uid`, `did`, `pole`, `layer`, `label`, `label_full`, `label_source`, `sim`, `cat`, `agree` | as for input members | `cat`/`agree` absent in catch-alls | `layer` is always `>` this neuron's layer **[V-DATA]** | `"L38_E134_N349+"` |
| `hop` | int >= 1 | no | `member.layer - this neuron's layer` (exact, 358/358) **[V-DATA]**. unused | `8` |
| `w` | float (5 dp), may be `0.0` | no | signed coupling from this neuron to the member **[I]**. Its sign does NOT reliably give raised/lowered: `sign(w) * focalPole * memberPole == effect` holds for only 67.5% of 354 members **[V-DATA]**. unused (the page shows NO number per reader member) | `0.01576` |
| `w_s` | float | no | always `0.0` (not filled). unused | |
| `r_gate` | float | **yes** (355/358) | coupling into the member's gate signal **[I]**. unused | `0.01715` |
| `gate_pct`, `up_pct`, `coupling_exact`, `q1_share`, `ev_n_active`, `ev_n_docs` | as for inputs | **yes** (355/358; missing together with `r_gate`, on members whose `w` is 0.0) | unused | |
| `weight_coupled` | bool | no | `true` on 4 of 358 members (all with `q1_share` 0.0); meaning not given. unused | `false` |

---

## 3. `moe_circuit_explorer_data/{did}/exemplars.json` (keyed by did)

Top level: **array** of exemplar objects (253 / 299 / 295 in samples = `index.n_ex`). All 847 sample rows have the same 15 keys.
Order: all positive-`act` rows first, sorted by `act` descending, then all negative-`act` rows, most negative first **[V-DATA]**.
No row has `act == 0`.

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `[].ex_id` | string | no | unique id = `"<band>_<regime>_<si>_<focal>"` **[V-DATA]**; join key to `incidence.ex_order` **[V-JS]** and to `pole_view[s].top_contexts[].ex_id` | `"top1000_gateopen_10967528_147"` |
| `[].band` | string | no | activation band the exemplar was sampled from: `top1000`, `p99_99`, `p99_9` (all positive `act`), `bottom1000`, `p0_01`, `p0_1` (all negative) **[V-DATA]**. About 90/30/30 per side. unused | `"top1000"` |
| `[].regime` | string | no | `"gateopen"` in all rows (gate pre-activation > 0: min 0.43) **[V-DATA]**. unused | |
| `[].si` | int | no | sequence (document) index in the corpus **[I]**; unique per file. unused | `10967528` |
| `[].focal` | int 3..1022 | no | token position of the focal token in that sequence **[I]** (context length 1024). unused | `147` |
| `[].act` | float (4 dp), non-zero | no | **activation at the focal token.** Sign decides the pole: page `onSide(e)`: `-` pole uses `act < 0`, `+` pole uses `act > 0` **[V-JS]**. Displayed as `\|act\|/theta` + `x` (tooltip *"Activation <num>"*) **[V-JS]** | `4.3419` |
| `[].gate_pre` | float | no | gate pre-activation at the focal token; `act = silu(gate_pre) * up_pre` **[V-DATA]**. unused | `1.752` |
| `[].up_pre` | float | no | up pre-activation; same sign as `act` in all rows **[V-DATA]**. unused | `2.908` |
| `[].router_weight` | float 0..1 | no | MoE router weight of this expert at the token **[I]**. unused | `0.2617` |
| `[].route_rank` | int 0..7 | no | rank of this expert among the 8 experts the router picked at that token (0 = top) **[I]**; all 8 values occur. unused | `1` |
| `[].dup` | int | no | always 0. unused | |
| `[].window.tokens` | string[] (42..81, mostly 81) | no | token strings around the focal token: up to 40 before and 40 after, cut at sequence start/end **[V-DATA]**. Raw strings: may contain `\n`, `\t`, be empty `""`, or be specials like `<\|im_end\|>`. Page shows `\n` as `↵ ` and `\t` as `⇥` **[V-JS]** | `["\n","Ot","by",...]` |
| `[].window.acts` | float[] same length **[V-DATA]** | no | this neuron's activation at each window token (about half are exactly 0); drives the token tint **[V-JS]**; `acts[focal_rel] == act` **[V-DATA]** | |
| `[].window.focal_rel` | int | no | index of the focal token inside `tokens` = `min(focal, 40)` **[V-DATA]**; page default 0 if missing **[V-JS]** | `40` |
| `[].next_token` | `{id: int, str: string, model_prob: float 0..1, model_top1: string}` | no | the true next token, its model probability, and the model's top-1 prediction. unused | `{"id":55,"str":"X","model_prob":0.8241,"model_top1":"X"}` |
| `[].flags` | `{routed: true, fidelity_ok: true, roundtrip_ok: null, showcase: false}` | no | constant in samples. unused | |
| `[].graph` | bool | no | always `true`. unused | |

---

## 4. `moe_circuit_explorer_data/{did}/incidence.json` (keyed by did; optional for the page)

The page loads it with `.catch(()=>null)` and uses it only for the *"Examples where this group matters most"* list **[V-JS]**.
If the file is missing or has no `routes`, `S.INC` stays null, `groupExamples` returns `null` and that list stays a grey loading
placeholder; everything else (diagram, top examples, members) still works. If the file exists but lacks the column(s) of a group,
the list shows *"None of the stored examples shows this group clearly."*

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `ex_order` | string[] of `ex_id` | no (page tolerates absence: `[]`) | row order of every column. Identical to the order of `exemplars.json` in all 3 samples **[V-DATA]**; page remaps by `ex_id` if it differs **[V-JS]** | |
| `routes` | object route id -> column | no | one column per route, one value per exemplar. 213 / 287 / 274 routes in samples | |
| `routes["in:<gid>:gate"]`, `routes["in:<gid>:up"]` | number[] | no | **unsigned** strength with which input group `gid` (incl. `g_single_*`) writes into the gate / up signal on each exemplar. All values `>= 0` (max 2.06) **[V-DATA]**. Non-zero on exemplars of BOTH poles. Units comparable to `gate_pre` / `up_pre` **[I]** | `[1.2675, 0.0, 1.41688, ...]` |
| `routes["rd:<rid>"]` | number[] | no | **signed** effect of this neuron on reader group `rid` on each exemplar (range about -0.065..0.12). Positive = group raised, negative = lowered, per the home-bucket check in G4 **[V-DATA]** | |
| `routes["lg:t<token_id>"]` | number[] | no | signed logit effect on vocabulary token `token_id` per exemplar (142-192 columns). **unused by the page** | |
| `route_stats` | object route id -> `{n_active: int, mean: float, p90: float, vmax: float}` | no | same keys as `routes`. `mean` = mean of `\|v\|` over all exemplars, `vmax` = max `\|v\|`, `p90` = 90th percentile of `\|v\|` (linear interpolation) - exact for all 774 sample routes **[V-DATA]**. `n_active` = number of exemplars where the route is "active" per `active_threshold_def`; NOT reproducible from the published columns (the "lane mass" denominator is not the sum of the group columns) **[V-DATA negative]**. unused | `{"n_active":51,"mean":0.19732,"p90":0.72351,"vmax":1.48484}` |
| `quantized` | bool | no | `false` in all samples. unused by the page (it detects the encoding per column) | `false` |
| `active_threshold_def` | string | no | `"input: \|v\|>=5% of exemplar lane mass; readers: \|v\|>=5% of exemplar \|reader\| mass; logits: v!=0"`. unused | |

### 4.1 Column encodings and `decodeCol` (exact)

Verbatim from the page:

```js
function decodeCol(v, n){
  const out=new Float32Array(n);
  if(Array.isArray(v)){ for(let i=0;i<n;i++) out[i]=(typeof v[i]==="number"&&isFinite(v[i]))?v[i]:0; return out; }
  if(v && typeof v==="object" && typeof v.q==="string"){
    const sc=(typeof v.scale==="number"&&isFinite(v.scale))?v.scale:0, bin=atob(v.q);
    for(let i=0;i<Math.min(n,bin.length);i++) out[i]=bin.charCodeAt(i)/255*sc;
  }
  return out;
}
```

Two accepted column forms **[V-JS]**:

1. **Plain array** of numbers (the only form present in the 3 samples; values rounded to 5 decimals, about 87-91% zeros).
   Non-number / non-finite / missing entries become 0.
2. **Packed object** `{"q": "<base64>", "scale": <number>}`: standard base64 (`atob`) of one **unsigned byte per exemplar**;
   `value[i] = byte[i] / 255 * scale`. 8-bit linear quantisation, range `0..scale`, so it can only carry non-negative values
   (sign is lost; fits the `in:` columns). A missing/invalid `scale` gives all zeros. Extra bytes beyond `n` are ignored; missing
   bytes stay 0. **Not observed in any sample** (`quantized:false`); expect it where `quantized` is `true`.
3. Anything else (null, string, ...) decodes to all zeros.

Output is a `Float32Array` (values are float32-rounded).

`n` and alignment **[V-JS]**:

```js
const n=ex.length; const order=inc.ex_order||[];
const same=order.length===n && order.every((id,i)=>ex[i]&&ex[i].ex_id===id);
const pos=same?null:new Map(order.map((id,k)=>[id,k]));
for(const rid in inc.routes){
  const raw=decodeCol(inc.routes[rid], same?n:order.length);
  if(same){ S.INC[rid]=raw; continue; }
  const out=new Float32Array(n); ex.forEach((e,i)=>{ const k=pos.get(e.ex_id); if(k!=null) out[i]=raw[k]; }); S.INC[rid]=out;
}
```

So: column index `k` belongs to exemplar `ex_order[k]`; the result is always re-indexed to `exemplars.json` order; exemplars not in
`ex_order` get 0.

### 4.2 Python port (tested on the 3 samples and on a synthetic packed column)

```python
import base64, re, struct

def _f32(x): return struct.unpack('f', struct.pack('f', x))[0]
def _num(x): return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float('inf')

def decode_col(v, n):
    out = [0.0] * n
    if isinstance(v, list):
        for i in range(n):
            x = v[i] if i < len(v) else None
            out[i] = _f32(x) if _num(x) else 0.0
        return out
    if isinstance(v, dict) and isinstance(v.get('q'), str):
        sc = v['scale'] if _num(v.get('scale')) else 0
        b = base64.b64decode(v['q'])
        for i in range(min(n, len(b))):
            out[i] = _f32(b[i] / 255 * sc)
    return out

def load_incidence(inc, ex):                      # -> {route_id: [value per exemplar in exemplars.json order]} or None
    if not inc or not inc.get('routes'): return None
    n, order = len(ex), inc.get('ex_order') or []
    same = len(order) == n and all(ex[i]['ex_id'] == order[i] for i in range(n))
    pos = None if same else {eid: k for k, eid in enumerate(order)}
    INC = {}
    for rid, col in inc['routes'].items():
        raw = decode_col(col, n if same else len(order))
        INC[rid] = raw if same else [raw[pos[e['ex_id']]] if e['ex_id'] in pos else 0.0 for e in ex]
    return INC
```

Check: `decode_col({"q": base64(bytes[0,51,255,128]), "scale": 2.0}, 6)` -> `[0, 0.4, 2.0, 1.00392, 0, 0]`.

---

## 5. How the two poles are handled ("side")

State `S.side` is `"+"` or `"-"`. `parseSide(v)` returns `"-"` for `"neg"`, `"-"` or `"−"` (U+2212), `"+"` for everything else
(including missing) **[V-JS]**. Helper names: `sideKey` -> `pos`/`neg`, `sideWord` -> `positive pole`/`negative pole`.

`sideModel(sign)` builds what one pole shows **[V-JS]**:

1. **Inputs** = every `groups.input[]` whose `groupSide(g)` is `sign` or `null`:
   ```js
   function groupSide(g){
     const b=String(g.bucket||"");
     if(/^[+-]\|/.test(b)) return b[0];
     const m=/([+-]) pole/.exec(String(g.name||"")+" "+String(g.description||""));
     return m ? m[1] : null;                         // null = the bundle does not say: shown on both poles
   }
   ```
   The catch-all groups have no `bucket` but their name contains `"+ pole"` / `"- pole"`, so they land on the right pole. Each
   input group therefore belongs to exactly one pole in the samples (13+13, 17+17, 18+16 groups).
   Per group: `gate = agg.gate.mean_signed`, `up = agg.up.mean_signed` (null if not a number), plus `n_pos`, `n_neg`.
2. **Gate lane** = inputs with `gate != null && gate != 0`, sorted by `|gate|` desc; **Up lane** likewise with `up`. The SAME
   group normally appears in both lanes (one card per lane, same key), each card showing both numbers.
3. `vmax = max(1e-12, all |gate|, all |up|)` over the pole's inputs: one scale for both lanes.
4. **Readers**: if `pole_view[sign]` exists: its `readers[]` joined to `groups.readers` by id, weight `w = n_ex || 0`,
   `effect = "-"` only if the row's `effect` is exactly `"-"`. Else fallback: every non-misc `groups.readers[]` whose `bucket`
   starts with `sign + "|"`, `w = agg.mean || 0`, `effect = "-"` if the bucket ends with `lowered`. Sorted by `w` desc;
   `share = w / max(1e-9, max w)`.
5. `nEx = pole_view[sign].n_admitted`, `line = pole_view[sign].line || identity.labels.short_title_{pos|neg}`,
   `real = pole_view[sign].pole_real !== false`.

Exemplars of a pole: `act > 0` for `+`, `act < 0` for `-`.

### Sign conventions (summary)

| question | where the answer is | convention |
|---|---|---|
| Does upstream group G raise or lower the gate / up signal? | `groups.input[].agg.gate.mean_signed`, `.agg.up.mean_signed` | `> 0` raises (orange, solid), `< 0` lowers (slate, dashed) **[V-JS]** |
| Does upstream neuron U raise or lower them? | `members[].gate_s`, `members[].up_s` | same sign rule **[V-JS]**; they sum to the group number **[V-DATA]** |
| Does G help THIS pole fire? | `bucket` role (`drives` / `opposes`) | + pole: `drives` = gate + and up + (21/21 groups); `opposes` = gate - and/or up -. - pole: `drives` = gate + and up - (22/22); `opposes` = gate - and/or up + **[V-DATA]**. The page itself never prints drives/opposes; it only explains *"groups that lower up are the ones pushing it to fire"* on the - pole |
| Through which pole does U act? | `members[].pole` | `+` or `-` of the OTHER neuron **[V-JS]** |
| Does this pole raise or lower downstream group R? | `pole_view[s].readers[].effect` (what the page shows), `groups.readers[].bucket` / `.effect` (home pole) | `+` = *activated*, `-` = *suppressed* **[V-JS]**. See G4 for the cross-pole caveat |
| Per example, how much? | `incidence.routes["rd:<rid>"]` | signed; `> 0` raised, `< 0` lowered **[V-DATA]** |

---

## 6. Extracting a directed neuron -> neuron edge list from `meta.json`

`F` = `meta.identity.did` (the focal neuron). All edges of a file touch `F`.

**Upstream edges (other neuron -> F)**: for each `g` in `meta.groups.input`, for each `m` in `g.members`:

| edge field | value |
|---|---|
| source | `m.did`, source pole `m.pole` |
| target | `F`, target pole `groupSide(g)` (= `g.bucket[0]`, or parsed from the name for `g_single_*`) |
| weight (gate lane) | `m.gate_s` - signed contribution to F's gate signal; `> 0` raises |
| weight (up lane) | `m.up_s` - signed contribution to F's up signal; `> 0` raises |
| role for the target pole | `g.bucket.split("|")[1]` (`drives` / `opposes`); for catch-alls parse `/pole, (drives\|opposes)/` from `g.name` |
| extra | `m.gate_w`, `m.up_w` (static, unsigned-by-pole), `m.gate_pct`, `m.up_pct`, `m.ev_n_active`, `g.id`, cleaned `g.name`, `m.cat`, `m.label_full` |

Units of `gate_s` / `up_s` **[I, supported by data]**: pre-activation units of the gate / up branch, averaged over ALL stored
exemplars of the neuron (both poles): `agg.<lane>.mean_signed` is bounded by, and for single-signed groups nearly equal to, the
all-exemplar mean of the unsigned column (e.g. L4_E1_N177 g1 gate: `mean_signed` 0.16678 vs column mean over all 299 exemplars
0.16731, over the 150 positive ones 0.3327). Magnitudes: |gate_s| up to 0.067, |up_s| up to 0.091 in samples; 9-10 of 499 are 0.
A single scalar "pushes the target pole to fire" can be derived as `up_s * (targetPole=="+" ? 1 : -1)` plus `gate_s`, but the
page never adds the lanes (*"They are shown per lane, never added together"*). Keep both.

**Downstream edges (F -> other neuron)**: for each `g` in `meta.groups.readers`, for each `m` in `g.members`:

| edge field | value |
|---|---|
| source | `F`, source pole = `g.bucket[0]` (or from the name for `r_single_*`) |
| target | `m.did`, target pole `m.pole` |
| sign | `raised` (+) or `lowered` (-) from `g.bucket.split("|")[1]` / `g.effect`; catch-alls: parse `/pole, (raised\|lowered) by/` from `g.name` |
| weight | `m.w` (signed, static **[I]**; use `|w|` as magnitude and the group sign as direction - the sign of `w` alone agrees with the group only 2/3 of the time). `m.r_gate` if present |
| extra | `m.hop` (layer distance), `m.ev_n_active`, `g.id`, `g.name`, `pole_view[s].readers[].n_ex` / `.mean_w` at group level |

Facts for graph building **[V-DATA, 3 samples]**:

- Sizes: 499 / 500 / 500 upstream member poles (416-498 distinct neurons) and 120 / 118 / 120 downstream member poles per neuron.
  Looks capped at 500 and 120 **[I]** (`provenance.configs.edge_max_nodes = 500`). About 620 edges per file.
- A `uid` (neuron + pole) occurs in exactly ONE group per direction, hence is assigned to exactly one focal pole. A neuron can
  occur twice with different poles (83 of 416 upstream neurons in L30_E47_N449).
- Upstream members are always in strictly lower layers (down to layer 0), downstream members in strictly higher layers (up to
  layer 39). No same-layer and no self edges. The layer directly below is the largest upstream source (35%, 44%, 46% of the
  upstream members sit at `layer-1` in the three files).
- **Targets/sources are almost never in the public sample.** Of the 1,751 member neurons (distinct per file and direction:
  416+117, 498+118, 483+119), exactly 1 (`L30_E130_N211`, a reader of `L4_E1_N177`) is in `public_atlas_data/index.json`
  (3,244 neurons) or in the circuit index (3,418 rows). The
  edge list is therefore hub-and-spoke: 3,418 documented hubs, each with about 600 mostly undocumented neighbours that carry only
  `label` / `label_full`. Hubs connect mainly through shared neighbours.
- **Id format**: every member `did` in the samples (1,857 member entries) matches `^L\d+_E\d+_N\d+$`, and `dominant_expert`
  matches `^L\d+_E\d+$`. A search of every file of the local atlas copy for `L\d+_shared_N\d+` finds nothing: **no "shared" /
  non-expert unit ids were observed in the atlas data**. The id form does exist in the model: the accompanying article
  (`neurons-as-an-interpretable-basis.html`, outside the release folder) shows `L4_shared_N126` (*"shared unit (marine text
  only)"*) in a circuit figure and states that each layer has *"one shared expert that is always active"* and that *"the shared
  experts make up 20,480 of the model's 5.26 million neurons"* (= 512 per layer). Whether the circuit bundles of other neurons
  list such units cannot be told from three files. A parser should accept `^L(\d+)_(?:E(\d+)|shared)_N(\d+)$`, fall back to
  "any string", and treat the member's integer `layer` field as authoritative.
- Estimated size **[I]**: about 0.69 MB `meta.json` per neuron => roughly 2.4 GB for all 3,418; all three files about 1.6 MB
  per neuron => roughly 5.4 GB uncompressed. About 2.1 million edges in total.

Tested Python:

```python
def group_side(g):
    b = str(g.get('bucket') or '')
    if re.match(r'^[+-]\|', b): return b[0]
    m = re.search(r'([+-]) pole', str(g.get('name') or '') + ' ' + str(g.get('description') or ''))
    return m.group(1) if m else None

def clean_name(g):
    return re.sub(r'\s+·\s+(gate|up-lane|up)\b.*$', '', str(g.get('name') or ''), flags=re.I).strip() or 'Unnamed group'

def edges(meta):
    F, out = meta['identity']['did'], []
    for g in meta['groups'].get('input') or []:
        role = (g.get('bucket') or '|').split('|')[1] or None
        if role is None:
            m = re.search(r'pole, (drives|opposes)', g.get('name') or ''); role = m.group(1) if m else None
        for x in g['members']:
            out.append(dict(kind='input', src=x['did'], src_pole=x.get('pole'), dst=F, dst_pole=group_side(g),
                            group=g['id'], group_name=clean_name(g), role=role,
                            gate_s=x.get('gate_s'), up_s=x.get('up_s'), gate_w=x.get('gate_w'), up_w=x.get('up_w')))
    for g in meta['groups'].get('readers') or []:
        b = g.get('bucket')
        m = re.search(r'pole, (raised|lowered)', g.get('name') or '')
        eff = b.split('|')[1] if b else (m.group(1) if m else None)
        for x in g['members']:
            out.append(dict(kind='reader', src=F, src_pole=group_side(g), dst=x['did'], dst_pole=x.get('pole'),
                            group=g['id'], group_name=clean_name(g), effect=eff,
                            w=x.get('w'), r_gate=x.get('r_gate'), hop=x.get('hop')))
    return out
```

Result on L30_E47_N449: 619 edges = input +/drives 164, +/opposes 49, -/drives 219, -/opposes 67; reader +/raised 18,
+/lowered 25, -/raised 43, -/lowered 34.

---

## 7. The index is not "the public sample"

`index.json` has 3,418 rows **[V-DATA]**:

| subset | count | notes |
|---|---|---|
| standard ids also in `public_atlas_data/index.json` (3,244 neurons) | 3,243 | all have a non-empty `verdict`. Only `L4_E206_N45` of the public index is missing here |
| standard ids NOT in the public index | 142 | `verdict == {}` for all; `grouping` `propose_assign_v1` (83) or `effect_first_pole_units` (59); spread over layers 2..39 |
| non-standard ids | 33 | experiment variants of 6 neurons with suffixes `_taylor`, `_taylor_dir`, `_taylor_hdb`, `_taylor_im`, `_relp_im`, `_relp_im_merged`, `_v2` (labels prefixed `[Taylor edges]`, `[Taylor edges · data-driven:hdbscan]`, `[RelP · Infomap]`, ...) and 4 synthetic fixtures `DEMO_HUGE`, `DEMO_SPARSE`, `DEMO_FIXTURE`, `DEMO_LARGE` (labels end `(DEMO fixture — synthetic data)`). `verdict == {}`, `grouping` null or `taylor_modes_then_labels` |

Layers: 4, 9, 14, 19, 24, 30, 36, 39 hold 326-369 rows each (849 for layer 14, of which 519 have ids starting `L14_E78_`: the
512 neurons of that expert plus 7 variant rows); 29 other layers hold 1-15 rows; layer 20 has none. For an API: filter `^L\d+_E\d+_N\d+$` and drop `DEMO_*`; expose `in_public_index` as a separate flag.

---

## 8. How the UI renders it

Layout: optional top bar (standalone only) and one scrolling pane. With `?shell=1` the top bar and the document header are hidden
(*"the host page owns identity, pole, tabs and neuron selection"*).

**Picker** (standalone, no valid `did`): heading *"Choose a neuron"*; text *"Each page shows one neuron's circuit: the groups of
earlier neurons that make it fire or hold it back, and the groups of later neurons it affects."*; a filter box (placeholder
*"filter by id or description…"*); a grid of cards: `did` + verdict chips (`· + single mode`, `· − multiple modes`,
`· + uninterpretable`), `layer L · expert E · neuron N` (blank for non-standard ids), `label` (or `—`).

**Document header** (standalone): `did`, coordinates, then `"+ pole"` / `"− pole"` + the pole's `line`; if the index row has a
verdict for the pole, a link *"See the islands and modes of this pole ↗"* to
`public_neuron_atlas.html?n=<did>&pole=<pos|neg>&tab=islands`.

**Intro** (visible when the pole has no circuit, otherwise behind the info icon next to "Circuit"), verbatim:
*"[standalone only: A neuron's output can be positive or negative, and each sign is read separately.] This is the circuit around
the **positive pole**. The neuron computes two signals and multiplies them: its output is **gate × up**. Groups of earlier neurons
feed each signal; every group shows a signed number for the gate and one for up (orange positive, slate negative), and the two can
differ in sign. [negative pole only: On the negative pole the gate is usually open and the up signal is negative, so groups that
**lower up** are the ones pushing it to fire.] The diagram also shows groups of later neurons it **affects downstream**. Click any
group to see its member neurons and examples."*

If `!real` or there are no inputs and no readers: *"This pole of the neuron fires too rarely to have a circuit to show."*

**Section "Where it fires most strongly"** - tooltip: *"The underlined token is where the neuron fires; numbers are its strength
there, in multiples of the threshold at which this pole starts to fire."* Three examples: exemplars of the pole sorted by `|act|`
desc, de-duplicated on the joined text of tokens `[focal_rel-6, focal_rel+3)`, first 3. Each row: strength `(|act|/theta).toFixed(1)+"×"`
(or `num(act)` if no theta) and the window: tokens `focal_rel-18 .. focal_rel+10`, `… ` / ` …` when cut, each token tinted
`rgba(224,86,27,a)` for `acts >= 0` or `rgba(51,105,138,a)` for negative with `a = 0.85 * min(1, |acts|/max|acts| in the shown
slice)`, white text above 0.55, focal token in a thin ink outline box. Empty: *"No stored examples for this pole."*
Example (L30_E47_N449 +): `5.4×`, `4.8×`, `4.5×`.

**Section "Circuit"** - a 4-column diagram (stacked under 760 px):

- left column, two lanes: **"Gate inputs"** (*"The gate decides whether the neuron switches on."*) and **"Up inputs"** (*"The up
  signal sets how strongly, and with which sign, the neuron fires once the gate is open."*). Empty lane: *"No group feeds this
  signal on this pole."* Each card: cleaned group name, `N neurons`, and two rows `Gate <bar> +.175` / `Up <bar> +.138`.
  Bar: diverging from the centre, length `sqrt(|v|/vmax) * 50%`, right/orange `#C94A12` for `v >= 0`, left/slate `#33698A` for
  `v < 0`; `—` when the value is missing.
- blocks **"Gate"** and **"Up"** with `N groups feed it`, then the neuron node (`did` with `/`, *"Positive pole"* /
  *"Negative pole"*).
- right column **"What it affects downstream"**: card = arrow (up/orange or down/slate), cleaned name, and
  *"23 neurons activated in 64 of 125 examples"* or *"… suppressed in …"*; suppressed cards have a dashed border. Empty: *"No
  downstream group carries weight on this pole."*
- curves: input card -> block, width `1 + 7.5*sqrt(|v|/vmax)`, colour by sign, dashed when negative; block -> node is fixed-width
  grey (*"plumbing only, no magnitude claimed"*); node -> reader card, width `1.2 + 7*sqrt(share)`.
- each list shows the first 6 cards, then a button *"Show all N"* / *"Show fewer"*.

**Legend**, verbatim: *"Positive contribution / activates a downstream group"*, *"Negative contribution / suppresses a downstream
group"*, *"Line thickness = size of the contribution"*, *"Numbers are each group's summed contribution to that signal"*.

**Group detail panel** (click a card; click again, the X, or Esc closes): title = cleaned name; subtitle
*"Earlier neurons feeding the gate and up signals of the positive pole · 85 neurons"* or *"Later neurons that the positive pole
activates · 23 neurons"* (*"suppresses"*). Sections:

1. *"Contribution to each signal"* (inputs only): Gate and Up rows with bar, number, and *"69 members positive, 16 negative"*
   (or *"no value in this bundle"*).
2. *"What the group is about"*: `subgroups[].name: description`, or the cleaned group `description` if there are no subgroups.
3. *"Examples where this group matters most"*: up to 4 exemplars of the pole. Score per exemplar = `in:<gid>:gate + in:<gid>:up`
   for an input group, `rd:<rid>` for a reader group; only scores `> 0`; sorted desc; same de-duplication as above. Empty:
   *"None of the stored examples shows this group clearly."*
4. *"Member neurons"*: first 12 rows, button *"Show all N members"* / *"Show fewer"*. Row: pole sign of the member, member id with
   `/`, for inputs `Gate +.036 · Up +.045`, then `label_full || label || "no description"`. Empty: *"No members listed."*

Number formats: `sgn3(v)`: `0` -> `"0"`; sign is `+` or `−` (U+2212); `|v| >= 10` -> 0 decimals; `>= 1` -> 1 decimal;
`>= 1e-3` -> 3 decimals without the leading zero (`+.034`); `>= 1e-4` -> 4 decimals (`−.0004`); smaller -> exponent (`+5e-5`).
`num(x)`: `>= 100` 0 dp, `>= 10` 1 dp, `>= 1` 2 dp, else 3 dp; non-number -> `—`.

Messages: *"Loading…"*, *"No neuron selected."* (shell without `did`), *"Could not load the neuron list."*, on a 404
*"**No circuit is available for this neuron yet.** Its firing examples are on the Islands tab."*, on other errors *"**This
neuron's circuit could not be loaded.** Please try again later."*

Shell landing text for this tab (from `shell.js`): **Circuit** - *"Inputs and outputs"* - *"Shows which earlier neurons turn this
one on and control how strongly it fires, and which later neurons it boosts or suppresses."*

---

## 9. User controls, URL parameters, messaging

URL parameters of `public_circuit_explorer-mobile-v1.html` **[V-JS]**:

| param | values | effect |
|---|---|---|
| `did` | bundle id | neuron to load. Standalone: must be in `index.json`, else the picker is shown silently. Shell: loaded without any check (404 => "No circuit is available ...") |
| `pole` | `neg`, `-`, `−` => negative; anything else or missing => positive | initial pole. Written back as `pos` / `neg` |
| `shell` | any non-empty value | embedded mode: hides top bar and header, skips `index.json`, listens for messages, never touches the address bar |
| `group` | card key `in:<gid>` or `rd:<rid>` | **standalone only**: opens that group's panel on load. Ignored in shell mode. If the key is not on the current pole nothing opens |
| `v` | e.g. `20261109C` | cache-buster added by the shell; not read |

Standalone address bar is kept in sync with `history.replaceState`: `?did=<did>&pole=<pos|neg>[&group=<key>]`, or no query on the
picker.

Controls:

| control | where | effect |
|---|---|---|
| neuron `<select>` *"Pick a neuron (3418)"* | top bar | load that neuron; empty option returns to the picker |
| **Random** button (tooltip *"Random neuron (R)"*), key `r` / `R` | standalone | random other neuron from the index |
| segmented **+ pole / − pole** | top bar | switch pole (clears the selected group) |
| picker filter | picker | case-insensitive substring over `did` and `label`; 120 cards, *"show more (N not shown)"* adds 240 |
| group card click / Enter / Space | diagram | toggle the detail panel; hover highlights the card's curve and dims the others |
| *"Show all N"* / *"Show fewer"* | each of the 3 lists | lift the 6-card cap (`CAP = {gate:6, up:6, readers:6}`, *"groups shown per list before 'show all'"*) |
| *"Show all N members"* | panel | lift the 12-member cap (`MCAP=12`) |
| X button (tooltip *"Close (Esc)"*), `Esc` | panel | close panel; `Esc` also hides info tips |
| info icons | section heads | tooltip with the explanation (hover, focus, tap) |

Caps and limits in one place: 6 cards per list; 12 members; 3 top examples; 4 examples per group; window 18 tokens before / 10
after the focal token (of 40 / 40 stored); picker 120 + 240.

postMessage protocol (only with `?shell=1`) **[V-JS]**:

| direction | message | handling |
|---|---|---|
| parent -> page | `{type:"shell-load", did, pole?}` | if `pole` is given, set side first; if `did` differs from the current one load it, else re-render |
| parent -> page | `{type:"shell-pole", pole}` | if the pole changes: set it, clear the selected group, re-render |
| page -> parent | `{type:"shell-key", key}` with target `"*"` | sent for keys `j k r R [ 1 2 3 4 p ? /` pressed in the frame (no Ctrl/Meta/Alt, not in an input); default prevented |

The shell sends `pole` as `"+"` / `"-"`; `parseSide` also accepts `pos` / `neg`. The page does not check `event.origin`.
The shell creates the frame with `public_circuit_explorer-mobile-v1.html?shell=1&v=<VIEWS_V>&did=<did>&pole=<pos|neg>` and then
posts `shell-load` when the neuron changes and `shell-pole` on every tab activation.

Network: `fetch(url, {cache:"no-cache"})` for `moe_circuit_explorer_data/index.json` (standalone), then
`<did>/meta.json` first (the diagram renders immediately), then `<did>/exemplars.json` and `<did>/incidence.json` in parallel.
State is exposed as `window.__explorerState`.

---

## 10. Text fields worth indexing for search

| path | file | what | size hint |
|---|---|---|---|
| `neurons[].label` | index.json | one description per neuron (+ pole reading); the only text the official picker searches | 3,418 strings, 747 k chars |
| `pole_view["+"].line`, `pole_view["-"].line` | meta.json | per-pole title (the - pole title exists only here and in `identity.labels.short_title_neg`) | 2 per neuron |
| `identity.labels.fleet_label`, `pole_view.prior_titles[s]` | meta.json | older, longer auto-labels | 1-3 per neuron |
| `pole_view[s].prior_read.covering_concept` | meta.json | short concept phrase (nullable) | 0-2 |
| `groups.input[].name` (strip tag), `groups.readers[].name` | meta.json | group names: the semantic neighbourhood of the neuron | 45-61 per neuron |
| `groups.*[].description` | meta.json | category lists or a sentence | same |
| `groups.*[].subgroups[].name` / `.description` | meta.json | finer categories | 61-106 per neuron |
| `groups.*[].members[].label_full` (500-char cap) | meta.json | descriptions of about 600 OTHER neuron-poles per file; the only published text for neurons outside the atlas | about 620 per neuron |
| `groups.*[].members[].cat` | meta.json | category of each member | |
| `pole_view[s].top_contexts[].ctx` | meta.json | 6 short contexts per pole with `▸focal◂` marking | 12 per neuron |
| `window.tokens` (join with `""`) | exemplars.json | about 250-300 passages of up to 81 tokens per neuron (about 900 k passages in total) | large |
| `next_token.str`, `next_token.model_top1` | exemplars.json | next-token strings | |
| `logits[].token`, `identity.jtable.fp3_tokens` | meta.json | promoted / suppressed output tokens | 24 + 3 |

---

## 11. Numeric / geometric structure usable for analysis

- **Directed weighted edges** neuron-pole -> neuron-pole: section 6 (`gate_s`, `up_s`, `gate_w`, `up_w`, `w`, `r_gate`, `hop`).
- **Group-level bipartite weights**: `groups.input[].agg.{gate,up}.{mean_signed,mean,n_pos,n_neg,n_active}`;
  `pole_view[s].inputs[].{aligned_mass,mean_contrib,mass_share,dominant_expert,dominant_share}`;
  `pole_view[s].readers[].{n_ex,n_ex_other,mean_w}`; `pole_view[s].{drive_mass,oppose_mass}`.
- **Exemplar x route matrix** (`incidence.routes`): for each neuron a dense matrix of about 250-300 exemplars by 70-95 group
  routes (`in:*:gate`, `in:*:up` unsigned; `rd:*` signed) plus 140-190 `lg:t*` logit routes. Rows are exemplars, so exemplars can
  be embedded / clustered by which groups drive them (this is the raw material behind "subcircuits"). `route_stats` gives
  per-column `mean`, `p90`, `vmax`, `n_active`.
- **Per-exemplar scalars**: `act`, `gate_pre`, `up_pre` (2-D gate/up plane, `act = silu(gate_pre)*up_pre`), `router_weight`,
  `route_rank`, `next_token.model_prob`.
- **Per-token activation traces**: `window.acts` (up to 81 values per exemplar).
- **Per-neuron statistics**: `identity.quantiles`, `identity.gmax`, `identity.jtable.{jnorm,kappa,dnorm}`,
  `theta_first.poles.*.theta`, `taxonomy_gate.{real_score,null_p90}`, `index.{n_ex,act_max,act_min}`.
- **Coordinates**: only the discrete `(layer, expert, neuron)` triple and `hop`. There are NO 2-D/3-D layout coordinates, no
  embedding vectors and no similarity matrices in these files (`members[].sim` is always null).
- **Output-token vector**: `logits[]` (14 static + 10 target tokens with signed weights) and the `lg:t<token_id>` columns.

---

## 12. Gotchas / variation across samples

- **G1. Index is not the public sample.** 3,418 rows = 3,243 public neurons + 142 extra + 33 variants/fixtures (section 7).
  Non-standard ids break `^L\d+_E\d+_N\d+$` parsing; `DEMO_*` rows are synthetic.
- **G2. Only one bundle variant was inspected** (`propose_assign_v1`, schema `moe-circuit-explorer-1.0`). The page carries
  fallbacks that imply other shapes exist: no `pole_view` (readers from `bucket`, weight `agg.mean`); groups without `bucket` and
  without `± pole` in the name (shown on BOTH poles); lane values missing (*"no value in this bundle"*); members without `pole`,
  labels or `gate_s`/`up_s`; no `theta_first` (raw activations shown instead of `x`); missing `incidence.json`; packed columns;
  `ex_order` different from the exemplar order. Write tolerant parsers.
- **G3. `in:` columns are unsigned, `rd:` and `lg:` columns are signed.** A packed `{q, scale}` column cannot hold negative
  values. `quantized` was `false` everywhere here.
- **G4. Downstream effect label vs. data on the "other" pole.** `pole_view[s].readers[]` can list groups whose home `bucket`
  belongs to the other pole (13 such rows in L30_E47_N449, 1 in L14_E78_N0, 0 in L4_E1_N177). Their `effect` is copied from the
  group, but `mean_w` and the `rd:` column have the OPPOSITE sign on this pole in all 14 cases, while for all 60 home-pole rows
  `sign(mean_w)` matches `effect`. Example: `r8` (`-|raised`) appears on the + pole as *"23 neurons activated in 64 of 125
  examples"* although its `rd:r8` values there are negative in 60 examples (`mean_w` -0.0039). For a signed graph use
  `sign(mean_w)` (or the `rd:` column), not `effect`, for cross-pole rows.
- **G5. Group examples only use positive scores.** For a *suppressed* reader group the `rd:` values on the pole are mostly
  negative, so the panel lists few or none (e.g. 2 of 51).
- **G6. `n_ex` is not exactly recomputable**: it equals the count of non-zero `rd:` values on the pole's stored exemplars in 57
  of 74 rows and is 1-4 larger otherwise; `n_admitted` can exceed the stored exemplars of the pole.
- **G7. Catch-all groups** (`g_single_*`, `r_single_*`): no `bucket`, no `subgroups`, `effect: null`, members without
  `cat`/`agree`. Input catch-alls ARE shown in the lanes; reader catch-alls are never shown (absent from `pole_view.readers`,
  skipped by the fallback) but their members are real edges.
- **G8. `family` / `effect` of an input group is the net lane sign, not drives/opposes.** A `-|drives` group can have
  `effect: "+"`. Use `bucket` for the role. The `color` palette in the data (blue = positive, red = negative) is not the page's
  palette (orange = positive, slate = negative).
- **G9. Reader member `w` is not a reliable sign** (67.5% agreement with the group effect); `w_s`, `agg.mean_signed`,
  `agg.n_pos`, `agg.n_neg` of reader groups are always 0; 4 of 358 reader members have `w: 0.0`, and 3 of those also lack
  `r_gate`, `gate_pct`, `up_pct`, `coupling_exact`, `q1_share`, `ev_*`.
- **G10. Labels are truncated**: `label` = first 100 chars of `label_full`; `label_full` is cut at 500 chars (229 of 619 members
  in one file are exactly 500 long). Cuts are mid-word, no ellipsis.
- **G11. Tokens are raw**: empty strings (451 in one file), newlines, tabs, `<|im_end|>`; join with `""` to rebuild text.
  `next_token.str` equals `tokens[focal_rel+1]` in 96-100% of rows.
- **G12. `index.label` describes only the + pole**; the - pole title is in `meta.pole_view["-"].line`. `index.verdict` differs
  from the shell's category (`public_atlas_data` `cat`). Poles may be missing from `verdict` (keys are optional), and 133 / 87
  neurons have exemplars of one sign only.
- **G13. Same theta for both poles** in all samples; do not assume it in general (it is stored per pole).
- **G14. Minus characters**: lane tags and `subgroups[].lane.*_sign` use U+2212 `−`; `pole`, `bucket`, `effect`, `verdict` keys
  and catch-all names use ASCII `-`. `parseSide` accepts both.
- **G15. Key order varies** in `index.json` rows; `gates_summary.skip_reasons` has a variable key set.
- **G16. No origin check** on incoming `postMessage`; outgoing `shell-key` uses target `"*"`.
- **G17. Not verifiable offline**: that every `index.json` row has all three files upstream; the exact definitions of
  `n_active`, `aligned_mass`, `q1_share`, `weight_coupled`, `jtable`, `kappa`; whether "shared" unit ids occur in other files.
