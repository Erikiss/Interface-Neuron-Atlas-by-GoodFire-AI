# Neuron Atlas — LANDSCAPE view and data (`neuron_landscape-mobile-v3.html`, `neuron_landscape_data/`)

Scope: the "firing landscape" of one neuron pole (one sign of one neuron's output) and the JSON that feeds it.
Sources read (local copy only, nothing executed, no network):

- `explorer/neuron_landscape-mobile-v3.html` (43.8 KB, 456 lines; whole `<script>` read)
- `explorer/neuron_landscape_data/atlas_index.json` (115 KB)
- `explorer/neuron_landscape_data/{L30_E47_N449__pos, L4_E1_N177__neg, L4_E1_N177__pos, L4_E1_N271__pos, L4_E1_N345__neg, L4_E1_N372__neg}.json` (1.40–1.86 MB each)
- cross-checks against `explorer/subcircuit_modes_data/<pid>.json`, `explorer/neuron_overview-mobile-v3.html` (embeds the landscape and reads the same JSON), `explorer/shell.js`, and the local copy of the article `neurons-as-an-interpretable-basis.html` (quadrant definitions).

Evidence tags used below:

- **[JS]** proven by the landscape page's JavaScript / HTML / comments
- **[OV]** proven by the Overview page's JavaScript (second consumer of the same file)
- **[DATA]** verified numerically on all 6 sample files
- **[XREF]** verified by joining with `subcircuit_modes_data/<pid>.json` samples
- **[ART]** stated in the local copy of the Goodfire article
- **[INF]** my inference, not proven

---

## 0. The single most important fact

**The landscape is NOT a gate × up scatter plot and the JSON contains no 2-D coordinates.**
It is a *swarm / lane chart*: [JS header comment]

> "One pole of one neuron as a swarm of its firings. Height = how strong and rare a firing is (5 sampled rarity levels, interpolated). Width = average damage per firing (default), how common firing is, or total damage. Lanes = modes (subcircuit modes + Q3/Q4 modes), split by their share of the damage at that height, similar modes side by side. Dots = sampled firings, all the same size; hover for the text. Data: neuron_landscape_data/<pid>.json (analysis/neuron_pyramid/build_pyramid_data.py)."

Every pixel position is computed in the browser from (a) five per-level aggregate rows (`bands`), (b) the mode order, and (c) a deterministic pseudo-random jitter. A dot's x position and its y position inside its level carry **no information**. The file has no gate value, no up value, no embedding vector and no ids of other neurons. The only gate information is one boolean per firing (`dots[].c` = gate input negative) and one boolean per mode (`modes[].closed`).

What the file *does* contain that is usable as geometry: a 5 × K level-by-mode count/damage tensor, two K′ × K′ mode-similarity matrices, a 1-D mode seriation, a fold graph between modes, ~950 sampled firings each with strength, damage, mode, subcircuit id, gate flag and a 29-token activation profile. See section 7.

---

## 1. Files

| upstream path | keyed by | size | role |
|---|---|---|---|
| `neuron_landscape-mobile-v3.html` | – | 44 KB | the view (header comment still calls it `neuron_landscape.html`) |
| `neuron_landscape_data/atlas_index.json` | global | 115 KB | list of every pid that has a landscape |
| `neuron_landscape_data/{pid}.json` | pid = `L{layer}_E{expert}_N{neuron}__{pos\|neg}` | 1.4–1.9 MB (mean 1.54 MB over 6 samples) | everything for one pole |
| `neuron_landscape_data/index.json` | global | not in local copy | list used only by the *standalone* page (sidebar). Schema known from JS only, see 2.3 |
| `neuron_landscape_data/ctx/{pid}.json` | pid | not in local copy (local `ctx/` dir is empty) | full-context examples read by the Overview page, not by the landscape page. Schema known from Overview JS only, see 2.4 |

All sampled JSON files are single-line minified UTF-8 (no newline). `{pid}.json` uses compact separators (`{"pid":"L30…`), `atlas_index.json` uses `", "` separators.

Bulk-download estimate [INF]: 5,477 pole files × ~1.54 MB ≈ 8.4 GB uncompressed.

---

## 2. Schemas

### 2.1 `neuron_landscape_data/atlas_index.json` [DATA]

Top level: **JSON array of 5,477 strings**, each a pid, sorted ascending as plain strings (so `L14…` sorts before `L4…`, and `N95` after `N452`), all unique.

| fact | value |
|---|---|
| pattern | `^L(\d+)_E(\d+)_N(\d+)__(pos\|neg)$`, no exceptions |
| poles | 2,740 `pos`, 2,737 `neg` |
| distinct neurons (did) | 2,742; 2,735 have both poles |
| layers present | 4 (674), 9 (722), 14 (665), 19 (692), 24 (656), 30 (698), 36 (724), 39 (646) |
| expert range | 0–255; neuron range 0–511 |
| same key set as | `subcircuit_modes_index.json` → `.poles` keys (5,477, identical set) |

Consumer [shell.js]: `S.ls=new Set(Array.isArray(li)?li:(li.pids||[]))` — the shell accepts either a bare array or `{pids:[…]}`; the published file is a bare array. A pole has an Overview/landscape tab iff its pid is in this set (`hasLs`, `hasOv`). Example: `"L14_E100_N452__neg"`.

### 2.2 `neuron_landscape_data/{pid}.json`

Notation: `K = modes.length`; `o = K − 1` = index of the last mode, which is always the **unassigned** bucket; modes `0 … o−1` are "real" modes.

#### 2.2.1 Top level

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `pid` | string | no | pole id `<did>__pos\|neg` | `"L30_E47_N449__pos"` |
| `layer` | int | no | layer index | `30` |
| `expert` | int | no | MoE expert index | `47` |
| `neuron` | int | no | neuron index inside the expert | `449` |
| `sign` | int, `1` or `-1` | no | pole sign. `>0` → "positive pole", drawn in orange `--pos`; else blue `--neg` [JS] | `1` |
| `verdict` | string | present in all samples | `"polysemantic"` \| `"monosemantic"` (only values seen; same field as `subcircuit_modes_data.verdict`). Not read by either page [INF: mode-count verdict of the subcircuit pipeline] | `"polysemantic"` |
| `n_firings` | int | read as optional by [OV] (`d.n_firings?…`) | number of sampled firings behind the aggregates; 5000 in all samples = 5 levels × 1000 | `5000` |
| `modes` | array[K] of object | no | see 2.2.2 | |
| `bands` | array[5] of object | no | the five rarity levels, weakest first; see 2.2.3 | |
| `kl_fit` | object | read as optional by [OV] (`S.L.kl_fit?.n\|\|200`) | damage-vs-strength fit; see 2.2.4 | `{"slope":3.095,"r2":0.56,"n":179,"q":0.25}` |
| `order` | array[o] of int | optional in JS (`d.order\|\|[0..o-1]`) | older lane order: a permutation of `0…o−1`. Used only when `rel_order` is absent or `?rel=off` [JS]. [INF] seriation of `sim` | `[2,0,9,1,7,6,4,3,8,10,5]` |
| `sim` | array[o][o] of float, or `null` | nullable (`null` in the two samples with o = 2) | mode × mode similarity, symmetric, diagonal 1.0, 3 decimals stored as float32 (`0.15600000321865082`). Off-diagonal range in samples 0.011–0.561. **Not read by any page** [INF: similarity that produced `order`] | |
| `rel_sim` | array[o][o] of float | present in all samples (also when o = 2) | "topic-embedding relatedness of the mode descriptions" [JS comment], symmetric, diagonal 1.0, 4 decimals, off-diagonal range in samples 0.40–0.92. **Not read by any page** (only its products `rel_order`, `rel_col` are) | `[[1.0,0.9184],[0.9184,1.0]]` |
| `rel_order` | array[o] of int | optional in JS | lane order used by default: permutation of `0…o−1`, "related meanings … sit side by side" [JS] | `[2,3,4,5,7,10,8,6,0,9,1]` |
| `rel_col` | array[o] of string `#rrggbb` | optional in JS | one colour per real mode, "related meanings get nearby hues"; shared with the Overview's mode list "so a mode has one colour in every tab" [JS] | `"#008b85"` |
| `dots` | array[~930–1170] of object | no | sampled firings; see 2.2.5 | |
| `has_kl` | bool | **absent in all atlas samples**; JS tests `d.has_kl===false` | `false` only for random-baseline units without damage measurements; then damage controls are disabled and width is forced to Frequency [JS] | – |
| `set` | string | absent in atlas samples | `"random baseline"` for baseline directions; anything else/absent = atlas neuron [JS] | – |
| `kind`, `kind_name`, `uid` | string | absent in atlas samples | baseline kind key (`onehot`, `expert_random`, `expert_plain`, `expert_scaled`, `layer_random`, `layer_plain`, `layer_scaled`, `resid_random`), its display name, and unit id [JS] | – |

Key order in the samples: `pid, layer, expert, neuron, sign, verdict, n_firings, modes, bands, kl_fit, order, sim, dots, rel_sim, rel_order, rel_col`.

There is **no `theta`** in this file. All strengths are already in multiples of the pole's firing threshold θ; the raw θ lives in `subcircuit_modes_data/{pid}.json → theta` (e.g. 0.3192 for L30_E47_N449__pos) [XREF].

#### 2.2.2 `modes[k]`

Index semantics [shell.js comment + JS]: "index into the pole's modes: open subcircuit modes in published order (= the Overview pill index), then gate-closed modes, then unassigned". The same integer is the `mode` URL parameter, the `mode` field of all postMessages, `dots[].m`, the column index of `bands[].counts/dmg/dmg_lin`, and the row/column index of `sim`, `rel_sim`, `rel_col` (for k < o).

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `modes[k].label` | string | no | short name of the mode. May contain pipeline markup: `«…»` marks the firing token and `\|` a token boundary (the Overview renders them, the landscape page prints them raw) [OV] | `"Wikipedia category-footer death-year digits ('1848 births / 19«2»5 deaths')"`, `"Sports club and team name pieces (Hot\|spur, Can\|ary, West Ham, Orlando Magic)"` |
| `modes[k].description` | string | no (empty string for unassigned) | one-paragraph reading of the mode (29–408 chars in samples). Shown by the Overview as "the mode's reading" [OV]; not shown by the landscape page | `"Fires on a single character mid-way through a random machine string, …"` |
| `modes[k].share` | float \| null | null for unassigned | **unweighted** share of the pole's sampled firings (Σ over levels of `counts[k]` ÷ ~5000) [DATA]. Open modes: 4 decimals, identical to `subcircuit_modes_data.modes[k].share` [XREF]; closed modes: full precision count/N. Used by the legend only when Width = Frequency or there are no damage numbers; unassigned is then `1 − Σ others` [JS] | `0.4421`, `0.28143832864604257` |
| `modes[k].kl_per_firing` | float \| null | null for closed modes and unassigned | mean KL per firing of the mode as published by the subcircuit pipeline; equals `subcircuit_modes_data.modes[k].kl.per_firing` exactly [XREF]. **Not read by any page** | `0.0006528853573386932` |
| `modes[k].closed` | bool | **key absent on the unassigned mode** | `true` = "Q3/Q4 mode": a mode read from the firings whose gate input is negative. `false` = open mode [JS] | `true` |
| `modes[k].rel` | string \| null | absent on unassigned; `null` on open modes | relation of a closed mode to the other modes. Values seen: `"NEW"`, `"SAME"`, `"SUBCASE"`, `"SUPERSET"` [DATA]; rendering in [JS] below | `"SUBCASE"` |
| `modes[k].fold` | int \| null | absent on unassigned | for a closed mode: index of the mode it is folded onto (same colour, drawn next to it). In samples always points to an open mode. `null` otherwise | `0` |

`relTxt` [JS] — the exact text shown under a closed mode:

- `fold != null` and `rel === "SAME"` → `Same meaning as “<label of modes[fold]>”`
- `fold != null` and any other `rel` (in practice `SUBCASE`) → `A narrower case of “<label of modes[fold]>”`
- `fold == null` and `rel === "SUPERSET"` → `A broader version of another mode`
- `fold == null` otherwise (`NEW`, or `SAME` without a fold target) → `A meaning of its own`

The unassigned mode is literally `{"label":"Unassigned examples (fit no mode)","description":"","share":null,"kl_per_firing":null}` in all 6 samples.

Because `closed` is absent on the unassigned mode, the page counts open modes as `modes.filter(m=>!m.closed).length − 1` [JS].

#### 2.2.3 `bands[i]` — the five rarity levels (index 0 = weakest)

| JSON path | type | meaning | example (L30_E47_N449__pos, i = 4) |
|---|---|---|---|
| `bands[i].name` | string | pipeline name: `"p95"`, `"p99"`, `"p99.9"`, `"p99.99"`, `"top 1,000"` [DATA]. The UI prefers its own labels `LEVEL = ["Top 5%","Top 1%","Top 0.1%","Top 0.01%","Strongest 1,000"]` and only falls back to `name` [JS] | `"top 1,000"` |
| `bands[i].a_med` | float (3 dp) | median firing strength of the level's sampled firings, in × θ. Printed as the big left-axis number `"5.9×"` (`toFixed(1)` below 10, rounded above) [JS] | `5.912` |
| `bands[i].a_min`, `a_max` | float | min / max strength in the level. Only `a_max` of the last level is printed: `"up to 15×"` [JS]. All dots satisfy `a_min ≤ a ≤ a_max` [DATA] | `3.392`, `14.72` |
| `bands[i].mass` | float | fraction of all tokens that fall in this level, i.e. its real-world frequency. Samples: `0.04, 0.009, 0.0009, 1e-4 − m4, m4`, sum = 0.05 [DATA]. `m4` = 2.0416e-6 for all four L4_E1 poles, 6.998e-6 for L30_E47. Printed as "1 in N tokens" (`1/mass`) [JS]. [INF] `m4 = 1000 / (tokens this unit sees)`: ≈ 490 M tokens for L4_E1, ≈ 143 M for L30_E47 | `6.998108026504496e-06` |
| `bands[i].n` | int | number of sampled firings in the level; 1000 in all samples | `1000` |
| `bands[i].counts` | array[K] of int | how many of the level's `n` firings belong to each mode (index = mode index, last = unassigned). Sums to `n` [DATA] | `[791,45,44,26,3,22,0,31,14,2,0,22]` |
| `bands[i].dmg_lin` | array[K] of float | Σ over the level's firings in that mode of the firing's KL damage (measured where tested, otherwise the estimate `10^lk`) [DATA: reproduced exactly from dots on every (level, mode) cell whose firings are all present]. Linear units (nats presumably; unit not stated). Optional in JS (`b.dmg_lin` tested for presence) | `[0.1624,0.00029,…]` |
| `bands[i].dmg` | array[K] of float | Σ of `kl^q` with `q = kl_fit.q = 0.25` over the same firings (compressed damage) [DATA: reproduced exactly on fully covered cells]. **Not read by any page** | `[83.04,2.20,…]` |

Derived quantities the pages compute from `bands` (reimplement these in an API) [JS, OV]:

```
w_i        = bands[i].mass / bands[i].n                    # population weight of one sampled firing of level i
meanD(i)   = Σ_k bands[i].dmg_lin[k] / bands[i].n          # average damage of one firing at level i
F_k        = Σ_i w_i · counts[i][k]                         # population-weighted firings of mode k
D_k        = Σ_i w_i · dmg_lin[i][k]                        # population-weighted damage of mode k
fshare_k   = F_k / Σ_k F_k        # "share of firings"   (weighted to real text)
dshare_k   = D_k / Σ_k D_k        # "share of damage"
dpf_k      = (D_k / F_k) / (Σ D / Σ F)   # "× the pole's average damage per firing"
meas_k     = #dots with m == k and kl > 0   # "rough: N tested firings" when < 10
```

Note the trap: `modes[k].share` is unweighted (each level counts equally), `fshare_k` is weighted by `mass`. They differ wildly, e.g. L30_E47_N449__pos mode 0: `share` 0.4421, `fshare` 0.078, `dshare` 0.616.

#### 2.2.4 `kl_fit`

| JSON path | type | meaning [DATA unless noted] | example |
|---|---|---|---|
| `kl_fit.slope` | float | OLS slope of `lk` on `log10(a)` over all tested firings (measured KL of exactly 0 entered as `lk = −10`). I.e. damage ∝ strength^slope. Reproduced to 3 dp on all samples | `3.095` |
| `kl_fit.r2` | float | R² of the model "common slope + one intercept per mode" on the tested firings. Reproduced to 3 dp (not the R² of the pooled line) | `0.56` |
| `kl_fit.n` | int | number of tested firings = number of dots with `kl != null`. The Overview prints "measured on about {n} firings" [OV] | `179` |
| `kl_fit.q` | float | exponent used for `bands[].dmg` (Σ kl^q); 0.25 in all samples | `0.25` |

Estimation model for untested firings, recovered exactly from the data [DATA]:

```
lk_est = kl_fit.slope · log10(a) + c_mode
c_mode = mean over the mode's tested firings of ( lk − slope · log10(a) )     # every mode has ≥ 5 tested firings in the samples
```

This is what the page text means by "damage for the rest is estimated from their strength and mode".

#### 2.2.5 `dots[j]` — sampled firings

All 11 keys are present on every dot in all samples: `b, a, m, s, c, kl, lk, w, t, f, u`.

| JSON path | type | meaning | example |
|---|---|---|---|
| `dots[j].b` | int 0–4 | level index into `bands` | `0` |
| `dots[j].a` | float (≤ 3 dp), > 0 | firing strength in × θ, always positive (also for negative poles). Card prints `"{a.toFixed(1)}× threshold"` [JS]. Equals `abs(a_rel)` of the same firing in `subcircuit_modes_data` exemplars [XREF, 127 matched firings, max diff 0] | `0.449` |
| `dots[j].m` | int 0…o | mode index (o = unassigned) | `7` |
| `dots[j].s` | int ≥ −1 | subcircuit index: the firing belongs to subcircuit `"c{s}"` of `subcircuit_modes_data/{pid}.json → subcircuits`; `−1` = none. Each `s` maps to exactly one `m` [XREF, DATA]. **Not read by either page** | `0` |
| `dots[j].c` | bool | `true` = gate input negative at this firing (card shows " · gate input negative") [JS]; equals `closed` of the matching subcircuit exemplar [XREF]. This is the per-firing Q3/Q4 flag | `true` |
| `dots[j].kl` | float (4 sig. digits) \| null | measured damage: KL between the model's next-token predictions with and without the neuron zeroed at the firing token, "summed over 16 tokens" [JS key text] / "over the next 16 tokens" [OV]. `null` = not tested. Can be exactly `0.0` (5–11 dots per file); the page treats only `kl > 0` as measured | `1.391e-08` |
| `dots[j].lk` | float (3 dp), always present | log10 of the firing's damage: `log10(kl)` when tested (floored at `−10.0` when kl = 0 or kl < 1e-10), otherwise the estimate from 2.2.4 (estimates can go below −10, e.g. −10.922) [DATA]. Read only by the dormant dot-size code [JS] | `-7.857` |
| `dots[j].w` | float (4 sig.) | population weight of the dot = `bands[b].mass / bands[0].mass`: 1.0, 0.225, 0.0225, 0.002449 (0.002325 for L30), 5.104e-05 (0.000175 for L30) [DATA]. Used as kernel weight in the Damage view ("weighted to real-world frequency") [JS] | `1.0` |
| `dots[j].t` | array of `[string, float\|null]` | token window, see below | `[[" of",null],[" John",0.46],…]` |
| `dots[j].f` | int | index in `t` of the firing token (boxed in the card). 100 for ~80 % of dots; smaller when the document starts less than 100 tokens before | `100` |
| `dots[j].u` | int | number of leading tokens of `t` that are plain context ("context before the atlas's activation window: no value stored") [JS]. Always `max(0, f − 16)` [DATA]; 84 when f = 100 | `84` |

**Token window `t`** [JS + DATA]:

- `t[i] = [text, value]`; always exactly 2 elements; `text` is one tokenizer token as a string.
- Layout: up to 84 plain context tokens (`i < u`, value always `null`), then 16 tokens before the firing, the firing token at `i = f`, then up to 12 tokens after. Maximum length 113; `len(t) = f + 13` except at document ends (3–11 dots per file have fewer trailing tokens). Minimum length seen 13.
- For `i ≥ u`, `value` is the neuron's activation on that token in × θ, **signed to this pole**: positive = this pole, negative = the opposite pole (range seen −22.3 … +22.8), 2 decimals (`-0.0` occurs). `null` inside the window means "the neuron's expert never received that word" (MoE routing; rendered dotted grey) [JS]. 47–75 % of window tokens are `null`.
- The values equal the subcircuit exemplar token values × `sign`, rounded to 2 dp [XREF]. The subcircuit exemplars keep 16 + 1 + 16 tokens; the landscape keeps 16 + 1 + 12 valued tokens plus the 84 plain ones.
- The firing token's own value `t[f][1]` is never `null` in the samples and is **not equal to `a`**: mean |difference| 0.01–0.14 θ, max 3.4 θ. In levels 0–2 (and mostly 3) `t[f][1]` is one constant per level, equal to that level's quantile strength (≈ `a_med` to 2 dp), while `a` scatters between `a_min` and `a_max` [DATA]. [INF] `t` holds the atlas harvest value (firings sampled *at* the quantile), `a` is the strength recomputed by the subcircuit pipeline. For L30_E47_N449__pos the two disagree strongly for some firings (a = 0.515 with stored value 3.23).
- Text oddities: empty-string tokens (31–1,320 per file), control characters including NUL `\u0000` (two samples), `<|im_end|>` document separators (51–116 per file), literal `\n`, `\r\n`. No U+FFFD and no lone surrogates in the samples.

**Array order of `dots`** [DATA]: indices `0 … kl_fit.n − 1` are the tested firings (contiguous runs per level; run order is 0,1,2,3,4 on the three `pos` samples and 3,2,1,0,4 on the three `neg` samples), then exactly 750 untested firings, 150 per level, in level order 0→4. The untested 150 are a proportional (random) sample of the level's 1000 firings; the tested firings are stratified so every mode has at least 5 (so the dot table over-represents small modes — use `bands[].counts` for unbiased mode frequencies). A few near-duplicate texts exist (0–7 per file).

Per-sample overview:

| pid | sign | verdict | K | open / closed / unassigned | dots | tested (`kl_fit.n`) | `kl_fit.slope`, `r2` | `sim` |
|---|---|---|---|---|---|---|---|---|
| L30_E47_N449__pos | +1 | polysemantic | 12 | 7 / 4 / 1 | 929 | 179 | 3.095, 0.56 | 11×11 |
| L4_E1_N177__neg | −1 | monosemantic | 3 | 1 / 1 / 1 | 1165 | 415 | 2.913, 0.691 | null |
| L4_E1_N177__pos | +1 | polysemantic | 3 | 2 / 0 / 1 | 937 | 187 | 2.276, 0.557 | null |
| L4_E1_N271__pos | +1 | polysemantic | 17 | 15 / 1 / 1 | 983 | 233 | 2.047, 0.354 | 16×16 |
| L4_E1_N345__neg | −1 | polysemantic | 19 | 0 / 18 / 1 | 988 | 238 | 1.761, 0.162 | 18×18 |
| L4_E1_N372__neg | −1 | polysemantic | 21 | 20 / 0 / 1 | 969 | 219 | 2.281, 0.514 | 20×20 |

### 2.3 `neuron_landscape_data/index.json` (standalone page only; NOT sampled)

Fetched as `${DATA}/index.json?t=<Date.now()>` only when `shell` is not `1`; a failed fetch silently yields an empty list. From the JS, it is an array of row objects with:

| field | use in JS |
|---|---|
| `pid` | row key, `?pid=` link, file to load |
| `layer`, `expert`, `neuron` | id text `L{layer}/E{expert}/N{neuron}`; `layer` fills the layer filter |
| `sign` | `>0` → "+" in `--pos`, else "−" in `--neg` |
| `label` | row text; searched together with `pid` |
| `n_open`, `n_closed` | "`{n_open} mode(s) + {n_closed} Q3/Q4`" |
| `coverage` | "`{round(100·coverage)}% of firings in a mode`" |
| `set` | `"atlas"` (default when absent) or `"random baseline"` |
| `kind`, `kind_name`, `uid` | baseline directions only; id text becomes `"{kind_name} · L{layer} E{expert} #{seed from uid}"` |

Whether this file is published in the public release is unknown; the public atlas uses the shell (`shell=1`), which never requests it.

### 2.4 `neuron_landscape_data/ctx/{pid}.json` (read by the Overview page; NOT sampled)

From Overview JS only: `CTX.modes[k]` is an array of examples `{a, s, f, toks}` where `s` is a subcircuit id string, `f` the firing index and `toks` an array of `[text, value|null]`. Comment: "full context (full_context.py): ~200 tokens before, 40 after, every token's activation recomputed"; "the recompute can route the firing token elsewhere (3% of examples, numerics); then show the atlas value there". For Q3/Q4 modes the Overview takes the subcircuits to list from the `s` of these examples; when the file is missing it falls back to `dots.filter(x => x.m === k)` of the landscape file.

---

## 3. Domain terms as the pages define them

- **Pole**: "one sign of a neuron's output, read on its own" [subcircuit view]; "A *negative pole* is the neuron's output with the opposite sign, read on its own; its strengths are shown as positive multiples." [OV]
- **Firing strength / threshold θ**: "Strength is the neuron's activation in multiples of this pole's firing threshold." [OV]; "θ is the pole's firing threshold, and activations here are given in units of θ" [subcircuit view]. The landscape left axis: "firing strength, × this pole's firing threshold". Note θ is not the top-5 % cutoff: the "Top 5%" level sits at 0.31–0.76 θ in the samples, and the "Top 0.1%" level at ≈ 1.55 θ in 5 of 6 samples [DATA].
- **Damage**: "how much the next-word prediction changes (KL, summed over 16 tokens) when the neuron is zeroed at the firing token" [JS key]; "About 200 firings per pole were tested directly; damage for the rest is estimated from their strength and mode." [JS caption]
- **Rarity level (band)**: "from the top 5% of tokens at the bottom to the 1,000 strongest in about a billion tokens at the top … Firings were sampled at these five levels; the shape between them is interpolated." [JS caption]
- **Mode**: a meaning of the pole; open modes are the subcircuit modes "in published order"; **Q3/Q4 modes** are "firings where the neuron's gate input is negative (quadrants 3 and 4 of the gate × up plane)" [JS tooltip + caption], "read separately from the other modes" [OV]; "it fired only through a weak leak" [subcircuit view]. The subcircuit file calls the same firings "Closed-gate firings (silu leak)".
- **Unassigned**: "examples that fit no mode", drawn "in pale grey on both edges" [JS].
- **Quadrants** — the landscape page itself only proves "Q3 and Q4 ⇔ gate input negative". The full definition is in the article [ART]: the neuron is a SwiGLU unit, activation = `silu(gate) × up` (times the expert router weight).

  | quadrant | gate g | up u | activation sign | pole it feeds |
  |---|---|---|---|---|
  | Q1 | g > 0 (open) | u > 0 | positive | positive pole, normal firing |
  | Q2 | g > 0 (open) | u < 0 | negative | negative pole, normal firing |
  | Q3 | g < 0 (closed) | u < 0 | positive (double negative, weak leak) | positive pole → "Q3/Q4 mode" |
  | Q4 | g < 0 (closed) | u > 0 | negative (weak leak) | negative pole → "Q3/Q4 mode" |

  So on a `__pos` pole a closed-gate firing is Q3, on a `__neg` pole it is Q4 [ART + INF]. The data never stores g or u, only `dots[].c` (g < 0 at that firing) and `modes[].closed`.
- `modes[].closed` is a property of the mode, not of each firing: dots with `c = true` occur in open modes (39–156 per file) and dots with `c = false` occur in closed modes (2–95 per file) [DATA]. Mode membership follows the subcircuit `s`.

---

## 4. How the UI renders it

### 4.1 Page layout

- Standalone (`shell` absent): left sidebar "Neurons" (search box, "which units" select, layer select, count line `"{n} of {N} neuron poles"`, scrolling list), intro `<h1>` "The landscape of one neuron" (or "The landscape of one direction" for non-onehot baseline units) with a sub-line `L{layer}/E{expert}/N{neuron}, positive|negative pole: {index label}. {nO} mode(s) and {nC} Q3/Q4 mode(s). Hover a dot to read it; click a mode to pick it.`
- Always: control row (Height, Width), the SVG figure, the legend "Modes" on the right (300 px column, below the figure under 900 px), the caption "How to read it.", a hover card, tooltips (`data-tip`, shown after 350 ms).
- Missing/failed data file: main area replaced by `"No landscape for this pole yet: it needs a subcircuit reading of its modes and damage measurements."`

### 4.2 Geometry of the picture (exact)

SVG is drawn at real pixel size: `W = max(520 (320 in bare/embed), round(#fig.clientWidth))`, `H = round(clamp(0.72·W, 460, 760))` (or `h`, default 420, in bare/embed). Margins `M = {l:104, r:170 (16 when W < 600), t:24, b:44}`. `CX = (M.l + W − M.r)/2`, `HW = (W − M.l − M.r)/2`. Redrawn on resize when the width changes by more than 8 px.

**Lane order** (left → right): `[unassigned × ½, …seq, unassigned × ½]` where `seq = rel_order` (or `order` when `rel=off` or `rel_order` missing, else `0…o−1`). The unassigned mode is split into two half-lanes on the outer edges.

**Height = Firing strength (default, `axis "a"`)** — vertical coordinate `v ∈ [0, 5]`, level `i` owns `[i, i+1]`, its centre is `i + 0.5`; `Y(v) = H − M.b − (H − M.t − M.b)·v/5` (weakest level at the bottom).

```
widthVal(b) = Width "p": meanD(b) = Σ dmg_lin / n        (Damage per firing, default)
              Width "n": b.mass                           (Frequency)
              Width "t": b.mass · meanD(b)                (Total damage)
lm[i]   = ln( max(widthVal_i, 1e-9·max) / max )
c = clamp(v − 0.5, 0, 4);  i = min(3, floor(c));  f = c − i
halfwidth(v) = HW · exp( 0.25 · ( lm[i] + (lm[i+1] − lm[i])·f ) )        # GAM = 0.25: width ∝ value^¼, log-linear between level centres
   Width "n" only, v > 4.5:  halfwidth *= sqrt(max(0, 1 − smoothstep(min(1,(v − 4.5)/0.5))))   # "pyramid apex"
sh[i][k] = (Width ≠ "n" and dmg_lin present ? dmg_lin[k] : counts[k]) / Σ_k
share_k(v) = sh[i][k] + (sh[i+1][k] − sh[i][k]) · smoothstep(f)          # smoothstep(t) = t²(3 − 2t)
lane k at height v = slice of [CX − halfwidth, CX + halfwidth] proportional to (½ or 1)·share_k(v)
```

Lanes are polygons through 241 grid heights. So in the default view a lane's width at a level is the mode's **share of the damage** at that level; with Width = Frequency it is its share of the **firings**.

Left axis per level: `"{a_med}×"` (large mono), the level label (`Top 5%` … `Strongest 1,000`), and on the top level `"up to {a_max}×"`. Right-hand note per level (only when W ≥ 600): Width p → `"{meanD(b)/meanD(bands[0])}× a top-5% firing"`; Width t → `"{100·mass·meanD / Σ}% of its damage"`; Width n → `"1 in {1/mass} tokens"`. Y-axis title "Firing strength".

**Height = Damage (`axis "k"`)** — only dots with `kl > 0`. `y = log10(kl)`; range `[min − 0.15, max + 0.15]`; 120 grid heights; Gaussian kernel density with bandwidth `(hi − lo)/26`, each dot weighted by `w`, accumulated per mode; half-width `= HW·sqrt(total/max)`; lanes split by the per-mode densities. Ticks at `log10(meanD(bands[0])) + e` for `e = −2…4`, labelled `0.01× … 1× … 10,000×`, sub-label "vs a top-5% firing", axis title "Damage". The Width control is hidden in this view.

**Dots**: radius 2.4 px for every dot (the damage-scaled size code `S.sz = "rel"` exists but no control enables it). Open-mode dot: filled with the mode colour, paper-coloured stroke. Closed-mode dot: "hollow" (`fill-opacity .28`, 1.4 px stroke in the mode colour).

- Firing-strength view: **not all dots are drawn**. Per level the page wants `max(24, round(area_of_level · 0.30 / (π·2.4²)))` dots and gives each lane `round(want · f · counts[k]/Σcounts)` of them, taking tested firings first, then a pseudo-random pick. Each chosen dot is thrown up to 48 times at a pseudo-random spot inside its lane and level (`v = i + 0.02 + 0.96·rnd`) and the least-overlapping spot wins. Unassigned dots go to the left or right half-lane by `rnd(j + 7) < 0.5`.
- Damage view: every dot with `kl > 0` at `y = log10(kl)`, `x` pseudo-random inside its lane (12–88 % of the lane width).
- `rnd(i) = frac(sin(i·12.9898 + 78.233)·43758.5453)` — deterministic per dot index, so the picture is stable between visits but carries no data in x.
- Changing a control animates dots to their new places over 700 ms (skipped with reduced-motion).

**Colours** [JS]:

- Default (`rel` ≠ `off` and `rel_col` present): mode k < o gets `rel_col[k]` (fallback `#8E9396`), unassigned gets `#CFD0CD`.
- Fallback palette `HUES = ["#0f766e","#7a4a8e","#6b7d1f","#b03a6e","#7a5c3e","#3d7a52","#8a6d1f","#6a4f8f"]`: handed out first to open modes in index order, then to closed modes with `fold == null`; modes past the eighth get `#8E9396`; a closed mode with `fold != null` takes the colour of `modes[fold]`; unassigned `#CFD0CD`.
- In the samples `rel_col` hues advance monotonically around the colour wheel along `rel_order`, and a folded closed mode has exactly the colour of its fold target and sits next to it [DATA].
- Lane fill: open mode = its colour at opacity .16; unassigned = .35; closed mode = 45° hatch pattern in its colour ("hatched = Q3/Q4 (gate input negative)"). Lanes have a dark outline "so close hues still read as separate meanings".
- Pole colours for text shading: positive `--pos #C94A12` / rgb(224,86,27), negative `--neg #33698A` / rgb(51,105,138).

### 4.3 Legend ("Modes")

Rows: modes k < o that have at least one firing in some level, then the unassigned row if its share ≥ 0.5 %. Each row: swatch (solid = open, white with coloured ring = closed), label, `Q3/Q4` tag on closed modes (tooltip: "Firings where the neuron's gate input is negative (quadrants 3 and 4 of the gate × up plane)"), the `relTxt` line on closed modes, `"{dpf}× the pole's average damage per firing"`, and a right-aligned percentage (1 decimal below 10 %).

Ordering and the percentage follow the Width setting:

| Width | sort key | percentage | header text |
|---|---|---|---|
| Damage per firing (p) | `dpf` desc, then share | share of damage (`dshare`) | "Ranked by damage per firing · right: share of damage" |
| Total damage (t) | `dshare` desc | `dshare` | "Ranked by share of damage (right)" |
| Frequency (n) | `modes[k].share` desc | `modes[k].share` (unweighted!) | "Ranked by share of firings (right)" |
| no `dmg_lin` | share | share | "Ranked by share of firings (no damage measurements)" |

Hovering a row dims every other lane and dot; clicking (or Enter/Space) selects the mode; a "Show all modes" button appears while one is selected. The unassigned row cannot be selected.

### 4.4 Hover card (nearest drawn dot within 12 px; on touch: tap opens, tap elsewhere closes)

```
● {mode label} [Q3/Q4]
{a.toFixed(1)}× threshold · damage {kl / meanD(bands[0])}× a top-5% firing (measured) · gate input negative
{token text, at most 3 lines, trimmed from the front, "…" prefix when trimmed}
```

- "damage … (measured)" only when `kl > 0`; "gate input negative" only when `c` is true.
- Token shading: peak = `t[f][1]` (fallback `a`); `r = min(1, |v|/|peak|)`; alpha = 0 when `r < 0.06`, else `0.85·r^0.7`, capped at 0.45 for negative values. Colour: `v ≥ 0` → this pole's colour, `v < 0` → the other pole's colour. `i < u` → plain text; `value == null` inside the window → dotted grey underline; `i == f` → boxed. `\n` is shown as `⏎ `.
- The comment above `shadeA` says "^1.6, nothing below 10% of the peak" but the code uses exponent 0.7 and a 6 % cut-off; the code is what runs.

### 4.5 Explanatory strings (verbatim)

Caption `#cap`:

> **How to read it.** Each dot is one place in real text where this pole of the neuron fired; hover it to read the text, shaded by how strongly the neuron fires on each word (boxed = the firing word; dotted grey = the neuron's expert never received that word). **Height** is how strong and rare a firing is, from the top 5% of tokens at the bottom to the 1,000 strongest in about a billion tokens at the top; the number on the left is the typical strength there, as a multiple of the firing threshold. Firings were sampled at these five levels; the shape between them is interpolated. **Width** by default is the average damage one firing at that height does: how much the model's next-word prediction changes when the neuron is switched off there. Strong firings each matter far more, so the shape is an upside-down pyramid. *How common* makes width follow how often firing at that height happens (compressed so the rare top stays visible); *total damage* multiplies the two. Each height is split into **lanes**, one per mode, as wide as the mode's share of the damage there, with similar modes side by side and examples that fit no mode in pale grey on both edges. Click a mode, in the list or in the picture, to pick it. **Q3/Q4 modes** (hatched lanes, hollow dots) are firings where the neuron's gate input is negative (quadrants 3 and 4 of the gate × up plane); one with the same meaning as another mode takes that mode's colour and sits next to it. About 200 firings per pole were tested directly; damage for the rest is estimated from their strength and mode.

Key line `#key` (shown only with `bare=1`), firing-strength view:

> Height: how strong and rare a firing is (left: firing strength, × this pole's firing threshold). Width: {p: "average damage per firing at that height — how much the next-word prediction changes (KL, summed over 16 tokens) when the neuron is zeroed at the firing token; right: relative to a top-5% firing" | n: "how common firing at that height is (compressed); right: how often" | t: "total damage at that height (how common × damage per firing); right: share of the pole's damage"}. Lanes: modes, as wide as their share of the {damage | firings} at that height; hatched = Q3/Q4 (gate input negative). Hover a dot to read it; click to pick its mode.

Damage view:

> Height: measured damage (KL) of the tested firings, relative to a typical top-5% firing. Width: how many of those firings land at that damage (weighted to real-world frequency). Lanes: modes, as wide as their share of the tested firings at that height; …

Control tooltips: Firing strength — "How strong and rare each firing is"; Damage — "Only the tested firings, stacked by how much the model's next-word prediction changes when the neuron is switched off there"; Damage per firing — "Average damage one firing at that height does when the neuron is switched off"; Frequency — "How often firing at that height happens"; Total damage — "How common times damage per firing: where the pole's overall impact lies"; disabled state — "No damage measurements for this unit".

Random-baseline sub-line: "**Random-direction baseline.** {kind_name} ({uid}), positive|negative pole, read with the same subcircuit protocol as the atlas neurons (its own corpus; "1 in N tokens" counts the tokens this unit sees); no damage measurements, so width shows how common firing is."

Overview gloss that defines the numbers [OV]: "Strength is the neuron's activation in multiples of this pole's firing threshold. Damage is how much the model's next-word prediction changes (KL divergence over the next 16 tokens) when the neuron is zeroed at the firing token; measured on about {kl_fit.n} firings, estimated for the rest. Shares are of this pole's firings above the top-5% cutoff, weighted to how often they occur in real text ({n_firings} sampled). … In mode names, a highlighted span marks the firing token and | a token boundary."

---

## 5. User controls and URL parameters

### 5.1 Controls

| control | values | effect |
|---|---|---|
| Height segmented buttons (`#hA`, `#hK`) | **Firing strength** (default) / **Damage** | switches layout (4.2). Damage hides the Width control and shows only tested firings |
| Width segmented buttons (`[data-wd]`; a `<select id="wdSel">` under 600 px) | **Damage per firing** `p` (default) / **Frequency** `n` / **Total damage** `t` | changes the outline width, the lane shares (damage vs firings) and the legend ranking; choosing one while in Damage height switches back to Firing strength; reported to the host as `landscape-width` |
| Legend row click / Enter / Space | mode index | select one mode (others dimmed); clicking it again clears |
| "Show all modes" | – | clears the selection |
| Click a lane or a dot | – | selects that dot's / lane's mode (unassigned clears) |
| Hover a dot | – | hover card + temporary highlight of its mode |
| Sidebar search `#q` (standalone) | free text | every whitespace-separated term must be a case-insensitive substring of `pid + " " + label` |
| Sidebar "which units" `#fS` (standalone) | `atlas` / one baseline kind / `""` Everything | filters by `set`/`kind`; written to `?set=` |
| Sidebar layer `#fL` (standalone) | "All layers" / "Layer N" | filters by `layer` |
| Sidebar row click (standalone) | pid | loads that pole, writes `?pid=` (history.replaceState) |
| Keys `j k r R [ 1 2 3 4 p ? /` (shell only, not in inputs, no modifier) | – | not handled here; forwarded to the parent as `shell-key` |

There is no sort control, no pagination and no free-text search over examples in this page.

### 5.2 URL parameters

| param | meaning |
|---|---|
| `shell=1` | atlas-tab mode: hides the sidebar and intro, identity comes from `did`/`pole`, enables postMessage in/out and key forwarding |
| `did` | neuron id `L{l}_E{e}_N{n}` (shell mode) |
| `pole` | `pos` (default) or `neg` (shell mode); file loaded is `{did}__{pole}.json` |
| `mode` | integer mode index to preselect (shell mode); empty/absent = none; ignored if not `< modes.length − 1` |
| `pid` | standalone: pole to open; default is `L19_E199_N127__pos`, else the first index row |
| `set` | standalone: initial value of the "which units" select (`atlas`, a baseline kind key, or empty for everything) |
| `data` | data directory, default `neuron_landscape_data` |
| `bare=1` | "embedded in the Overview, which has its own mode picker: the picture and its controls only" — hides legend and caption, shows the key line, fixed height |
| `embed=1` | "the atlas carousel": picture + mode list, no controls, caption or key; fixed settings |
| `h` | drawing height in px for `bare`/`embed`, default 420 |
| `rel=off` | use the old fixed palette and `order` instead of `rel_col`/`rel_order` |
| `v=…` | cache-buster added by the Overview (`v=20261109C`); not read |

The Overview embeds it as `neuron_landscape-mobile-v3.html?shell=1&bare=1&v=20261109C&did={did}&pole={pole}[&mode={k}]`. In the current atlas the landscape is never a top-level tab; it lives inside the Overview tab (shell comment: "the Landscape and the NS11 explainer merged into one Overview tab … Old links with tab=landscape open it").

### 5.3 postMessage protocol (all with target origin `"*"`)

Inbound, handled only when `shell=1`:

| `type` | payload | effect |
|---|---|---|
| `shell-load` | `{did, pole?, mode?}` | selection := `mode ?? null`; loads `{did}__{pole \|\| "pos"}` |
| `shell-pole` | `{pole}` with `"+"`, `"-"`, `"pos"` or `"neg"` | loads the other pole of the current did, clears the selection |
| `shell-mode` | `{mode}` (index or null) | `pick(mode, fromShell)`; no echo back. Because `pick` toggles, sending the already-selected index clears it |
| `landscape-hidecard` | – | closes a touch-opened hover card |

Outbound to `parent`:

| `type` | payload | when |
|---|---|---|
| `landscape-width` | `{pid, wd}` with `wd ∈ "p","n","t"` | after every load and width change (shell) — the host "orders and shades its mode list by the same quantity as the width" |
| `landscape-mode` | `{pid, mode, label}` — `mode` = index into `modes` or `null` (all), `label` = that mode's label or null | user picks/clears a mode (shell, not when the pick came from the shell) |
| `landscape-size` | `{pid, h}` | after each build when `(bare \|\| embed) && shell`; the host sizes its iframe to `h` |
| `shell-key` | `{key}` | key forwarding (5.1) |

---

## 6. Text fields worth indexing for search

| JSON path | content | notes |
|---|---|---|
| `modes[k].label` (k < o) | short human name of each meaning | strip `«`, `»`, `\|` for plain-text search; keep the raw string for display |
| `modes[k].description` (k < o) | one-paragraph reading of the mode | richest semantic text in the file; empty for the unassigned mode |
| `dots[j].t[*][0]` joined | up to 113 tokens of real corpus text around each firing | join with `""` (tokens carry their own leading spaces). ~290–420 chars per dot, ~950 dots per pole |
| `dots[j].t[f][0]` | the firing token itself | best field for "which tokens does this pole fire on" facets |
| `pid`, `layer`, `expert`, `neuron`, `sign`, `verdict` | identity / facets | |
| `modes[k].closed`, `modes[k].rel`, `bands[i].name` | categorical facets | |

Suggested derived documents: one per mode (`pid`, `k`, label, description, closed, rel, fold, share, fshare, dshare, dpf, colour) and one per dot (text, firing token, mode, level, strength, damage, gate flag, subcircuit).

Sanitise before storing: NUL and other C0 control characters occur in token strings; empty tokens occur; `<|im_end|>` marks document boundaries inside a window.

---

## 7. Numeric / geometric structure usable for analysis

### 7.1 Inventory

| structure | path | shape | encoding | aligned with |
|---|---|---|---|---|
| level × mode firing counts | `bands[i].counts[k]` | 5 × K int | plain arrays | columns = mode index (last = unassigned); rows = level 0…4 |
| level × mode linear damage | `bands[i].dmg_lin[k]` | 5 × K float | plain | same |
| level × mode compressed damage (Σ kl^0.25) | `bands[i].dmg[k]` | 5 × K float | plain | same |
| level scalars | `bands[i].a_med/a_min/a_max/mass/n` | 5 each | plain | rows of the above |
| mode similarity (older) | `sim[k][l]` | o × o float or null | plain, float32 text, 3 dp | modes 0…o−1 (unassigned excluded) |
| mode relatedness (description embeddings) | `rel_sim[k][l]` | o × o float | plain, 4 dp | modes 0…o−1 |
| 1-D seriations | `order`, `rel_order` | o ints | permutations | position → mode index |
| colour embedding | `rel_col[k]` | o hex strings | hue ≈ position on a relatedness circle | modes 0…o−1 |
| mode → mode edges | `modes[k].fold` + `modes[k].rel` | ≤ o edges | index + label | closed mode → target mode |
| damage/strength power law | `kl_fit.slope`, `r2`, `n`, `q` | scalars | | per pole |
| firings | `dots[j]` | ~930–1170 rows | objects | `b` → `bands`, `m` → `modes`, `s` → subcircuit `c{s}` |
| per-firing activation profile | `dots[j].t[u … ][1]` | ≤ 29 values (16 before, firing, 12 after) | float or null | aligned at index `f` |

Nothing is packed, base64-encoded or quantised beyond decimal rounding (a: 3 dp, lk: 3 dp, kl and w: 4 significant digits, token values: 2 dp, sim: 3 dp, rel_sim: 4 dp). There are no typed arrays and no coordinate arrays.

Ranges seen: `a` 0.041–22.9 θ; `kl` 0–0.141; `lk` −10.9 … −0.85; token values −22.3 … +22.8 θ; `mass` 2.0e-6 … 0.04.

### 7.2 What is *not* there

- No (gate, up) values, no pre-activations, no router weights.
- No x/y coordinates, grids, contours or density arrays; all densities are computed client-side.
- No links to other neurons (no upstream/downstream ids, no cross-neuron similarity). Cross-pole analysis must join other files by `pid`.
- No document ids or corpus offsets for the firings.

### 7.3 Links to other files

- `pid` ↔ `subcircuit_modes_data/{pid}.json`, `ns11_pages/data/{pid}.json`, `neuron_landscape_data/ctx/{pid}.json`, `subcircuit_modes_index.json → poles[pid]`.
- `dots[j].s` ↔ `subcircuit_modes_data.subcircuits["c" + s]` (circuit, gate/up roles, exemplars). [XREF]
- Open mode `k` ↔ `subcircuit_modes_data.modes.filter(m => m.subcircuits && !m.residue)[k]` [OV]; labels, descriptions, `share` and `kl_per_firing` are byte-identical for every open mode of the five sample poles that have open modes; `verdict`, `sign` and `n_firings` also equal the subcircuit file's [XREF]. The closed modes of the landscape are a re-reading of the subcircuits that the subcircuit file lumps into its single "Closed-gate firings (silu leak)" residue mode; the landscape's unassigned bucket collects the "Residue (unreadable)" subcircuits, closed-gate subcircuits that fit no mode, and `s = −1` firings [XREF].
- Mode index ↔ `ns11_pages … explain.situations[].mode` [OV].
- θ (raw activation of 1.0 on every axis here) ↔ `subcircuit_modes_data.theta`.

### 7.4 Turning one file into a (points × features) table

One row per dot (≈ 950 rows per pole, ≈ 5.2 M rows for the whole atlas [INF]):

```python
d = json.load(open(f"{pid}.json")); o = len(d["modes"]) - 1
n_in_level = Counter(x["b"] for x in d["dots"])
for j, x in enumerate(d["dots"]):
    mode = d["modes"][x["m"]]; band = d["bands"][x["b"]]
    win  = x["t"][x["u"]:]                      # valued window
    pre  = [None] * (16 - (x["f"] - x["u"])) + [v for _, v in x["t"][x["u"]:x["f"]]]   # left-pad to 16
    post = [v for _, v in x["t"][x["f"] + 1:]];  post += [None] * (12 - len(post))    # right-pad to 12
    row = dict(
        pid=d["pid"], layer=d["layer"], expert=d["expert"], neuron=d["neuron"], sign=d["sign"],
        dot=j, tested=x["kl"] is not None,
        level=x["b"], level_name=band["name"], level_mass=band["mass"],
        strength=x["a"],                         # x theta, recomputed
        strength_stored=x["t"][x["f"]][1],       # x theta, harvest value at the firing token
        mode=x["m"], mode_label=mode["label"], unassigned=(x["m"] == o),
        mode_closed=bool(mode.get("closed")), mode_rel=mode.get("rel"), mode_fold=mode.get("fold"),
        subcircuit=None if x["s"] < 0 else f"c{x['s']}",
        gate_closed=x["c"],                      # Q3 on a pos pole, Q4 on a neg pole
        kl_measured=x["kl"], log10_kl=x["lk"], kl=10 ** x["lk"],
        weight=x["w"],                           # relative population weight of one sampled firing
        pop_mass=band["mass"] / n_in_level[x["b"]],   # dot-table weight (see caveat)
        firing_token=x["t"][x["f"]][0],
        text_before="".join(t for t, _ in x["t"][:x["f"]]),
        text_after="".join(t for t, _ in x["t"][x["f"] + 1:]),
        **{f"act_m{16 - i}": v for i, v in enumerate(pre)},     # act_m16 … act_m1
        act_0=x["t"][x["f"]][1],
        **{f"act_p{i + 1}": v for i, v in enumerate(post)},     # act_p1 … act_p12
    )
```

Caveats for statistics on this table: (1) the dots are ~19 % of the 5,000 sampled firings, and tested firings are topped up to at least 5 per mode, so mode frequencies from dots are biased — use `bands[].counts` (exact) and `mass` for population numbers; (2) `kl` is measured for only `kl_fit.n` rows, the rest is the fitted estimate and therefore an exact function of (`strength`, `mode`); (3) `None` in the activation columns means "not routed to this expert", which is information, not missing data; (4) strengths of negative poles are positive.

### 7.5 Analyses the data supports directly

- **Mode geometry inside a pole**: MDS / spectral embedding of `1 − rel_sim` (or `sim`); compare the two matrices; `rel_order` is the published 1-D seriation; hue of `rel_col` is its circular coordinate.
- **Mode mix as a function of strength**: rows of `counts` (or `dmg_lin`) normalised to the simplex give a 5-point trajectory per pole in mode space; `a_med` gives the strength at each point.
- **Causal concentration**: `dshare`, `dpf`, entropy / effective number of modes by firings vs by damage.
- **Damage scaling**: `kl_fit.slope` (≈ 1.8–3.1 in the samples) and `r2` per pole; per-mode intercepts `c_mode` are recoverable from untested dots (`lk − slope·log10(a)`).
- **Gate regime**: `gate_closed` rate by level, by mode, by subcircuit; fraction of damage carried by Q3/Q4 firings.
- **Local activation shape**: the 29-wide activation profile around each firing (burstiness, opposite-pole neighbours, routing sparsity).
- **Cross-pole maps** (after bulk download): embed all `modes[].label + description` texts yourself; the file gives no cross-neuron vectors.

---

## 8. Gotchas / variation across samples

1. **Not a gate × up plot.** No coordinates in the data; x positions in the picture are pseudo-random. See section 0.
2. **Unassigned is always the last mode** and has a reduced key set (`label`, `description`, `share: null`, `kl_per_firing: null`); `closed`, `rel`, `fold` are absent, not null. All per-mode arrays in `bands` have length K (including unassigned); `sim`, `rel_sim`, `order`, `rel_order`, `rel_col` have length K − 1 (excluding it).
3. **`sim` can be `null`** (both samples with only 2 real modes); `rel_sim` is present there.
4. **Mode-kind mix varies**: poles with only open modes (L4_E1_N372__neg, L4_E1_N177__pos), only closed modes (L4_E1_N345__neg: 18 closed, 0 open), or both. For L4_E1_N345__neg the subcircuit file has 3 open modes with zero firings ("Taken from the autointerp label only") that do **not** appear in the landscape, so "open mode k ↔ k-th open mode of the subcircuit file" breaks for that pole [XREF].
5. **`rel: "SAME"` with `fold: null`** occurs (L4_E1_N345__neg modes 3 and 5); the UI then prints "A meaning of its own".
6. **Three different "shares"**: `modes[].share` (unweighted over the 5,000 samples), `fshare` (mass-weighted firings) and `dshare` (mass-weighted damage). The legend shows `dshare` by default and `modes[].share` only in Frequency mode; the Overview always shows `fshare` and `dshare`.
7. **`a` ≠ `t[f][1]`**; in the lower levels the stored firing-token value is a per-level constant. Use `a` for strength, `t[f][1]` only as the shading peak.
8. **Levels overlap in `a`** for some poles (L30_E47_N449__pos: level 2 spans 0.54–2.28 θ, level 3 spans 0.52–4.74 θ). Level membership is given by `b`, not derivable from `a`.
9. **`kl` can be exactly 0** on tested firings (`lk = −10.0`); the page treats those as untested (`kl > 0` checks), so the number of dots it calls "measured" is `kl_fit.n` minus 5–11.
10. **`dots[].c` is per firing, `modes[].closed` is per mode**; they disagree for 0.2–17 % of a pole's dots in the samples.
11. **`modes[].share` of real modes does not sum to 1** (0.50–0.985 in the samples); the remainder is the unassigned bucket.
12. **Mass of the top level differs per expert** (2.04e-6 for L4_E1, 7.00e-6 for L30_E47), and with it `w` of levels 3 and 4.
13. **Dead / unread fields**: `sim`, `rel_sim`, `bands[].dmg`, `modes[].kl_per_firing`, `dots[].s`, `verdict` are read by neither page; `dots[].lk` only by dormant code. Their meanings above come from numerical reproduction and cross-file joins, not from UI text.
14. **Dormant UI**: dot-size modes (`data-sz`, `S.sz = "rel"`) have code and legend text ("Dot area", "Damage vs a typical firing of the same strength", "Solid: measured by switching the neuron off (about 200 firings). Paler: estimated from strength and mode.") but no button; dots are always 2.4 px.
15. **Stale comment**: shading comment says `^1.6` / 10 %, code uses `^0.7` / 6 %.
16. **Baseline-only fields** (`has_kl`, `set`, `kind`, `kind_name`, `uid`) and `index.json` are known from the JS only; none of the 6 atlas samples has them, and `atlas_index.json` contains only `L…_E…_N…__pos|neg` ids.
17. **`ctx/{pid}.json`** is referenced by the Overview but absent from the local copy, so its schema in 2.4 is unverified.
18. **Token text hygiene**: NUL / control characters, empty tokens, `<|im_end|>`; labels contain `«»` and `|` markup.
19. **File naming**: the page is published as `neuron_landscape-mobile-v3.html`; comments and older links say `neuron_landscape.html`.
20. **Sample bias of this study**: 5 of the 6 samples are layer 4, expert 1; one is layer 30. Value ranges quoted here are from those 6 poles only.
