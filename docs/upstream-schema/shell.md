# Qwen3.6 Neuron Atlas - the SHELL (list, search, filters, sort, tabs, landing) and its four boot indices

Scope: `explorer.html` (inline script = `shell.js`, byte-identical, checked with `diff`), `goodfire-host-back-v1.js`, and the four
index files loaded at boot. Everything here was derived from the local copy only (no network). Each statement is marked
**[V]** = verified (from the page's JavaScript / comments, or computed from the data) or **[I]** = inferred.

Release base (upstream): `https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/`
Response headers of the shell page (`headers.txt`): `cache-control: public,max-age=31536000,immutable`, `server: AmazonS3`,
`last-modified: Tue, 10 Nov 2026 16:50:06 GMT`, `etag: "637920caad3c2f4cb1b404b734c118e8"`. **[V]** the ETag equals the MD5 of the
local `explorer.html`, so ETag = MD5 can be used as an integrity check by a bulk downloader (true for this non-multipart S3 object;
**[I]** for the others). The JS comment in `boot()` says: *"the release prefix is immutable, so these load together and stay in the
browser cache (no cache-busting query)"*.

Upstream file name of the shell: the host page `https://www.goodfire.com/research/public_neuron_atlas` embeds
`<iframe id="neuron-atlas" data-explorer="https://static.goodfire.ai/neuron-atlas/2026-11-09-082204/public_neuron_atlas-sample-3000-v2.html">`
**[V]** (from the saved host page). The local copy is named `explorer.html`; whether a file of that name also exists upstream was
not checked (**unverified**) - use `public_neuron_atlas-sample-3000-v2.html` when linking to the upstream shell.

| local file | bytes | md5 | role |
|---|---|---|---|
| `explorer.html` | 75,791 | `637920caad3c2f4cb1b404b734c118e8` | the shell page (HTML + CSS + one inline script) |
| `goodfire-host-back-v1.js` | 1,545 | `a51d72dbf631a678fee8c7e03b591200` | syncs the URL with the embedding goodfire.com page; builds public deep links |
| `public_atlas_data/index.json` | 1,277,206 | `e115cfc3638f6f42494baacf0e33eb7c` | MANDATORY neuron list (identity + island-level category/description) |
| `ns11_pages/data/index.json` | 291,937 | `38237420e1d0942253d555b5eeda55d5` | optional: simulatability scores (`sim`), overview pid list, `model` |
| `neuron_landscape_data/atlas_index.json` | 115,613 | `eb4042cd4309cd1af9ebb818d7391541` | optional: pids that have a landscape = have an Overview tab |
| `subcircuit_modes_index.json` | 3,524,032 | `cc29616607c8b76f9541acf445d99267` | optional: per-pole subcircuit read (verdict, modes with labels and shares) |

All four JSON files are UTF-8, contain non-ASCII characters (labels quote Cyrillic, CJK, etc.), and are single-line.

---

## 1. Field tables

### 1.1 `public_atlas_data/index.json` (global; the only index the shell cannot start without)

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `neurons` | array (3,244) of object | no | one entry per neuron. Order is NOT fully sorted: grouped by layer in the order 4, 9, 14, 19, 24, 27, 30, 35, 36, 39 and ascending (E, N) inside a group, but the last element `L4_E206_N45` is appended out of order **[V]** | |
| `neurons[].did` | string, regex `^L\d+_E\d+_N\d+$` | no | neuron id in "data / URL form". Always equals `L{L}_E{E}_N{N}` (0 mismatches) and is unique (0 duplicates) **[V]**. Display form replaces `_` by `/` (`fid()`): `L30/E47/N449`; long form `Layer 30 / Expert 47 / Neuron 449` (`fidLong()`) | `"L4_E1_N177"` |
| `neurons[].L` | int | no | layer. Values present: 4, 9, 14, 19, 24, 27, 30, 35, 36, 39 | `4` |
| `neurons[].E` | int, 0..255 | no | expert index inside the layer (MoE expert; 256 distinct values) | `1` |
| `neurons[].N` | int, 0..511 | no | neuron index inside the expert (512 distinct values) | `177` |
| `neurons[].poles` | object with exactly the keys `"+"` and `"-"` | no | the two poles; both keys present on all 3,244 neurons, never null **[V]** (the JS tolerates a missing pole: `.filter(s=>n.poles[s])`) | |
| `neurons[].poles[s].pid` | string | present on all 6,488 poles; JS has a fallback `${did}__${s==="+"?"pos":"neg"}` | pole id = key of every per-pole data file and of the other three indices. Always `did + "__pos"` for `"+"`, `did + "__neg"` for `"-"` (0 mismatches) | `"L4_E1_N177__pos"` |
| `neurons[].poles[s].cat` | `"mono"` \| `"poly"` \| `"dark"` \| `null` | key always present, value nullable (178 null) | ISLAND-level category (the pre-subcircuit reading). Overridden in memory by `applySC` for poles with a subcircuit read (section 2.3); the original is then kept as `cat_isl` | `"mono"` |
| `neurons[].poles[s].desc` | string (may be `""`) | no | ISLAND-level description. JS comment: *"public_atlas_data: single mode = its meaning; multiple modes = the mode names"*. For `poly` it is 2-12 mode names joined by `" · "` (U+00B7 with spaces; all 1,922 poly descs contain it, no mono desc does). `""` for every `dark` and `null` pole (524 empty). Length 0-423, median 93. Overridden in memory by `applySC`; original kept as `desc_isl` | `"The word \"private\" in any sense, strongest as the army rank (\"Pvt\"), weaker on Marines, corporal"` |
| `neurons[].poles[s].islands` | bool | no | `true` = an Islands data file exists for the pole (`public_atlas_data/islands/poles/{pid}.json`, loaded by the Modes tab's island view and by the landing specimen). `false` exactly when `cat` is `null` (178 poles) **[V]** | `true` |

Raw cross-tab over all 6,488 poles (cat, desc non-empty, islands): `mono/yes/true` 4,042 - `poly/yes/true` 1,922 - `dark/no/true` 346 - `null/no/false` 178.

### 1.2 `ns11_pages/data/index.json` (global, optional for the shell)

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `pids` | array (5,477) of pid strings, sorted ascending (plain string order), unique | no | poles that have an "NS11 explainer" data file `ns11_pages/data/{pid}.json`. The shell stores it as `S.ov` but **never reads it again** (`hasOv` was redefined as `hasLs`, see 2.4) **[V]** | `"L14_E100_N452__neg"` |
| `model` | object pid -> string, 5,477 keys (= `pids`) | not used by the shell | values: `"opus"` 5,377, `"glm"` 100 (glm spread over all 8 sampled layers, 10-15 each; 99 of the 100 glm poles have a `sim`). **[I]** which LLM "scientist" wrote the explainer: the per-pole file `ns11_pages/data/L30_E47_N449__pos.json` has `scientist_model: "claude-opus-5-5"` and the index says `"opus"` for it | `"opus"` |
| `sim` | object pid -> float (4 decimals), 585 keys, all contained in `pids` | key may be absent (`S.sim=o.sim\|\|{}`); a pole without an entry is "Not scored" | **simulatability** of the pole's description (definition in section 3). Fraction, 1.0 = 100 %. Range -33.6162 .. 0.9987, median 0.6123, mean 0.4316; 12 values < 0, 6 values < -1, none > 1 | `0.4648` |

### 1.3 `neuron_landscape_data/atlas_index.json` (global, optional for the shell)

| JSON path | type | meaning |
|---|---|---|
| (root) | array (5,477) of pid strings, sorted, unique. The JS also accepts an object `{pids:[...]}`: `S.ls=new Set(Array.isArray(li)?li:(li.pids\|\|[]))` | poles that have a landscape file `neuron_landscape_data/{pid}.json` => the pole has an **Overview** tab (`hasLs` = `hasOv`). In this release the list is element-for-element identical to `ns11.pids` and to the key order of `subcircuit_modes_index.poles` **[V]** |

### 1.4 `subcircuit_modes_index.json` (global, optional for the shell: without it the shell logs `"no subcircuit index; islands only"` and keeps island categories)

| JSON path | type | optional? | meaning | example |
|---|---|---|---|---|
| `source` | string | not used by the shell | build path on the author's machine | `"/home/ctigges/subcircuit-xfer/code/rare_modes/analysis/rare_modes/subcircuit_atlas"` |
| `built_at` | string, ISO-8601 UTC | not used by the shell | build time | `"2026-11-05T21:15:39Z"` |
| `poles` | object pid -> record, 5,477 keys, sorted | no | one "subcircuit read" per pole | |
| `poles[pid].verdict` | `"monosemantic"` (2,122) \| `"polysemantic"` (3,355) | no | the reader's verdict. `applySC`: `"monosemantic"` => category `mono` | `"polysemantic"` |
| `poles[pid].fn_mono` | bool | no | "functionally mono". Only ever `true` together with `verdict:"polysemantic"` (1,651 poles). `applySC`: polysemantic + `fn_mono` => category `fn`. Defined by the subcircuit view's tooltip (verbatim): *"functionally mono: Opus reads more than one meaning, but the main meaning carries N% of the neuron's KL damage (≥ 90%); the other meanings are small and do little when the neuron is ablated"* | `true` |
| `poles[pid].main_damage_share` | float 0..1 (3 decimals) \| `null` (81 poles) | nullable | share of the "damage" (effect on the model's predictions, KL, when the neuron is switched off / ablated) carried by the main mode. Shell tooltip uses it as `Math.round(100*x)` %. Only dereferenced for `fn` poles, where it is never null. `null` for all 51 zero-mode poles, 25 one-mode, 3 two-mode, 2 three-mode poles | `0.978` |
| `poles[pid].n_modes` | int 0..30 | no | number of readable (non-residue) modes; always `== modes.length` **[V]**. `0` => "failed read" (JS comment: *"Zero-mode reads are failed reads (no readings supplied / residue only): they keep the island category and view."*) | `3` |
| `poles[pid].n_residue` | int 0..5 | not used by the shell | number of residue modes in the per-pole file (entries with `residue:true`, e.g. `"Closed-gate firings (silu leak)"`, `"Residue (unreadable)"`). **[V]** against the 6 sample files `subcircuit_modes_data/{pid}.json` | `1` |
| `poles[pid].n_subcircuits` | int 1..51 (median 19) | not used by the shell | number of subcircuits of the pole (= `subcircuits.length` in the per-pole file, **[V]** on 6 samples) | `16` |
| `poles[pid].modes` | array (0..30) of object | no (may be empty) | the readable modes in published order = the non-residue entries of the per-pole file's `modes`, same order (**[V]** on 6 samples: labels and shares identical). Always sorted by `share` descending **[V]** (5,477/5,477) | |
| `poles[pid].modes[].label` | string, 10-384 chars (median 76), never empty | no | the mode's name. The shell joins all labels of a pole with `" · "` to form the pole's description (and search text). 22,620 labels, 22,619 distinct; 6,091 contain non-ASCII; one label itself contains `" · "` | `"Squares and squaring of numbers (perfect squares, x², completing the square)"` |
| `poles[pid].modes[].share` | float 0..1.0002 (4 decimals); 34 values are the JSON integer `0` | not used by the shell | share of the pole's firings that belong to this mode. **[V]** on samples: approximately `mode.n_firings / pole.n_firings` of the per-pole file (e.g. 0.4421 vs 2201/5000 = 0.4402; exact in other files). The shares of a pole do NOT sum to 1: the remainder is the residue modes (sum is ~1 exactly for the 280 poles with `n_residue==0`; max sum 1.0004 from rounding) | `0.8008` |

Key set is identical for all 5,477 records (`verdict, fn_mono, main_damage_share, n_modes, n_residue, n_subcircuits, modes`) and for all
22,620 modes (`label, share`).

### 1.5 In-memory pole object after `boot()` (what the UI actually shows)

`applySC` mutates the pole objects of `public_atlas_data/index.json`:

| field | when present | meaning |
|---|---|---|
| `sc` | subcircuit record exists and `n_modes > 0` | the record from 1.4 |
| `sc_empty` | subcircuit record exists and `n_modes === 0` | the (failed) record; category/description stay island-level |
| `cat_isl`, `desc_isl` | together with `sc` | the island-level `cat` / `desc` before the override. Not displayed and NOT searched |
| `cat` | always | final category: see 2.3 |
| `desc` | always | final description: mode labels joined by `" · "` (with `sc`), else the island description |

---

## 2. `boot()` - how the indices are merged

### 2.1 Loading
`Promise.allSettled` over the four relative URLs `public_atlas_data/index.json`, `ns11_pages/data/index.json`,
`neuron_landscape_data/atlas_index.json`, `subcircuit_modes_index.json` (plain `fetch`, non-2xx throws). Only the first is mandatory
(its failure rejects `boot()`; the page stays empty). The other three degrade silently: no `sim`, no Overview tab for anyone, island
categories only.

### 2.2 The "sample" (what the public atlas shows)
```js
const NOT_SAMPLE=new Set(["L27_E247_N96","L35_E88_N30"]);
S.idx=j.neurons.filter(n=>!(n.L===14&&n.E===78)&&!NOT_SAMPLE.has(n.did));
S.sample=S.idx.flatMap(n=>["+","-"].filter(s=>n.poles[s]).map(s=>({n,s}))).sort(randomOrder).slice(0,SAMPLE_SIZE);   // SAMPLE_SIZE=3000
S.samplePids=new Set(S.sample.map(({n,s})=>pidOf(n,s)));
S.idx=[...new Set(S.sample.map(({n})=>n))];
```
JS comment (verbatim): *"not part of the random neuron sample, so kept out of the public atlas (Curt 11-07): the whole-expert L14 E78 set
and two hand-added neurons"* and *"Sample individual poles once, before any display filters or sort choices. Reuse the published seeded
shuffle so membership stays fixed across browsers and reloads."*

So **[V]**:
1. Drop all neurons of layer 14 / expert 78 (512 neurons = the complete expert, N 0..511) and the two hand-added neurons
   `L27_E247_N96`, `L35_E88_N30` (the only neurons of layers 27 and 35). Remaining candidate pool: 2,730 neurons = 5,460 poles.
2. Order the 5,460 poles by `randomOrder` (seeded hash of the pid, below) and keep the first 3,000 = **the sample** (`S.sample`,
   an array of `{n, s}` with `s` in `"+"`/`"-"`). The sample is of POLES, not neurons: a neuron may have one or both poles in it.
3. `S.idx` / `S.by` = the 2,202 neurons with at least one sampled pole (798 with both poles, 710 only `+`, 694 only `-`).
4. A pole that is not in the sample cannot be listed, searched or opened: `openNeuron` starts with `if(!inSample(n,s)) return;` and a
   deep link to it falls back to the landing page. The data of non-sample poles is nevertheless present in the index files.

The seeded order (also the "Random order" sort):
```js
const RSEED=20261109;
const rhash=t=>{ let h=(2166136261^RSEED)>>>0; for(let i=0;i<t.length;i++){ h^=t.charCodeAt(i); h=Math.imul(h,16777619)>>>0; } h^=h>>>15; h=Math.imul(h,2246822507)>>>0; h^=h>>>13; return h>>>0; };
const randomOrder=(a,b)=>rhash(pidOf(a.n,a.s))-rhash(pidOf(b.n,b.s))||pidOf(a.n,a.s).localeCompare(pidOf(b.n,b.s));
```
i.e. FNV-1a (32 bit) over the pid's UTF-16 code units with the offset basis XOR-ed with the seed, then a 2-step avalanche; ascending
hash, ties by pid. JS comment: *"random order: ONE fixed shuffle for everyone, a hash of (fixed seed, pole id), so every browser, reload
and shared link sees the same sample and order, and a filtered list keeps the same relative order. Change RSEED to publish a different
sample."* No hash collisions among the 5,460 candidates **[V]**.

Python port (verified equal to JS int32 semantics on 6 pids by running a re-typed copy of the arithmetic in Node; the downloaded page
itself was never executed):
```python
M = 0xFFFFFFFF
def rhash(pid, seed=20261109):
    h = (2166136261 ^ seed) & M
    for ch in pid:                      # pids are ASCII
        h ^= ord(ch); h = (h * 16777619) & M
    h ^= h >> 15; h = (h * 2246822507) & M; h ^= h >> 13
    return h & M
# sample = sorted(candidate_pids, key=lambda p: (rhash(p), p))[:3000]
```
Test vectors: `L24_E31_N88__neg` -> 1274435 (rank 1), `L14_E227_N460__pos` -> 1768506 (rank 2), `L4_E247_N291__pos` -> 3018865 (rank 3),
`L30_E47_N449__pos` -> 206476305, `L4_E1_N177__neg` -> 1067936918. Rank 3000 has hash 2353498597; rank 3001 (first excluded) is
`L30_E143_N28__neg` = 2353800160. First ten sample pids: `L24_E31_N88__neg, L14_E227_N460__pos, L4_E247_N291__pos, L24_E103_N302__neg,
L4_E164_N6__pos, L19_E233_N328__neg, L24_E32_N73__neg, L4_E29_N370__pos, L36_E174_N485__neg, L14_E212_N278__pos`.
A working re-implementation of `boot()` (sampling + applySC) lives in this repository:
`src/neuron_atlas/catalog.py` (`Catalog.build`, `sample_hash`).

### 2.3 Category and description of a pole (`applySC`)
```js
// 2026-11-05: poles with a subcircuit read take category + description from its modes; island values kept as cat_isl / desc_isl.
// Zero-mode reads are failed reads (no readings supplied / residue only): they keep the island category and view.
function applySC(idx,sc){ for(const n of idx) for(const s of ["+","-"]){ const p=n.poles[s]; const r=p&&sc[p.pid]; if(!r) continue;
  if(r.n_modes===0){ p.sc_empty=r; continue; }
  p.sc=r; p.cat_isl=p.cat; p.desc_isl=p.desc;
  p.cat=r.verdict==="monosemantic"?"mono":r.fn_mono?"fn":"poly";
  p.desc=r.modes.map(m=>m.label).join(" · "); } }
```
Decision table **[V]**:

| situation | `cat` | `desc` |
|---|---|---|
| subcircuit record with `n_modes>0`, `verdict=="monosemantic"` | `mono` | the single mode label |
| ... `verdict=="polysemantic"` and `fn_mono` | `fn` | all mode labels joined by `" · "` |
| ... `verdict=="polysemantic"` and not `fn_mono` | `poly` | all mode labels joined by `" · "` |
| record with `n_modes===0` (`sc_empty`), or no record | island `cat` (`mono`/`poly`/`dark`/`null`) | island `desc` (`""` for dark/null) |

The category depends only on `verdict` and `fn_mono`, not on the mode count (two polysemantic poles have exactly 1 mode:
`L9_E13_N359__pos` -> `poly`, `L9_E150_N510__neg` -> `fn`).

Display vocabulary (constant `CAT`, verbatim; `w` = name, `d` = tooltip):

| key | name (`w`) | tooltip (`d`) |
|---|---|---|
| `mono` | single mode | "Everything this pole fires on fits one specific meaning." |
| `fn` | one dominant mode | "This pole has more than one mode, but the main one accounts for at least 90% of the damage when the neuron is switched off." |
| `poly` | multiple modes | "This pole fires on two or more different, unrelated things." |
| `dark` | uninterpretable | "No clear pattern was found in what this pole fires on." |
| `none` (= `cat` null) | not yet read | "This pole has not been read yet." |

`fn` rule in the data **[V]**: among polysemantic poles with a non-null `main_damage_share`, `fn_mono == (main_damage_share >= 0.9)`
with one exception caused by rounding (`L14_E164_N476__pos`: 0.9 shown, `fn_mono:false`). In the sample: `fn` 0.901..1.0, `poly`
0.0..0.899, `mono` always 1.0 or null.

### 2.4 `hasLs` / `hasOv` / `hasModes` / `sim`
```js
const hasLs=(n,s)=>S.ls.has(n.poles[s].pid||`${n.did}__${s==="+"?"pos":"neg"}`);   // pid in neuron_landscape_data/atlas_index.json
const hasOv=(n,s)=>hasLs(n,s);                                                    // Overview tab exists <=> landscape exists
const hasModes=p=>!!(p.sc||p.islands||p.sc_empty);                                // something to show on the Modes tab
const simOf=(n,s)=>{ const v=S.sim[pidOf(n,s)]; return v==null?null:v; };
const modesOf=(n,s)=>{ const p=n.poles[s]; return p.sc?p.sc.modes.length:null; };
```
JS comment: *"2026-11-07 (Curt): the Landscape and the NS11 explainer merged into one Overview tab (neuron_overview.html?shell=1), the
default landing tab; it exists for poles with a landscape (neuron_landscape_data/atlas_index.json). Old links with tab=landscape open it."*
- `hasOv`: in this release true for all 3,000 sample poles (and for all 5,460 candidate poles) **[V]**.
- `hasModes`: true for all 3,000 sample poles **[V]**.
- `S.sim`: 585 poles in the file, 334 of them in the sample (all of those have `sc`).

### 2.5 After merging
- `#fL` options are rebuilt: `""` "All layers", `"middle"` "Layers 9–36" (selected), then one `"<L>"` "Layer <L>" per distinct layer
  of the sampled neurons, ascending => `4, 9, 14, 19, 24, 30, 36, 39` (10 options).
- URL filter params are applied: for `k` in `fCat, fL, fSort` the param name is `k.slice(1).toLowerCase()` = `cat`, `l`, `sort`; the
  value is used only if it is non-null and exactly equals an existing `<option value>`.
- `filter()` builds the list, `openFromURL(QP)` opens the pole named by the URL or the landing page.

---

## 3. Key concepts as defined by the page

- **Pole**: landing text: *"A neuron's output can be positive or negative, and the two signs often respond to different things, so each
  neuron has a positive pole and a negative pole, read separately."* Each pole is its own page (JS: *"opens one pole of a neuron; each
  pole is its own page"*). Colours: positive `#C94A12` (orange), negative `#33698A` (slate).
- **Subcircuit / mode**: landing text: *"For each pole, we split the firing examples into subcircuits: groups of examples where the same
  earlier parts of the model make the neuron fire. An LLM judge then grouped subcircuits that respond to similar concepts in the text
  into modes. A pole with one mode does one specific thing; a pole with several modes responds to unrelated concepts."*
- **Island**: the older, pre-subcircuit grouping (`public_atlas_data`); used only for poles whose subcircuit read failed (zero modes)
  and for the landing-page specimen. CSS comment: *"the Modes view (subcircuit_modes_view.html; public_islands_atlas.html for poles
  without a subcircuit read)"*.
- **Simulatability** (`sim`): JS comment: *"simulatability = share of the neuron's effect (KL) that a simulator recovers from the
  pole's description on held-out passages"*. Row tooltip: *"Simulatability: when the neuron's activations are simulated from this
  description on held-out text, N% of its effect on the model's predictions is recovered (below zero: the simulation does more damage
  than switching the neuron off)."* Below -1: *"Simulatability below −100%: simulating the neuron from this description changes the
  model's predictions more than switching the neuron off does (N%)."* Missing: *"This pole's description has not been scored by
  simulation yet."* The Overview page adds: *"A simulator that never saw the neuron predicts its activation on held-out text from each
  description; the score is the share of the neuron's effect on the model's predictions (KL) that those predicted activations restore.
  100% = as good as the real neuron."*
- **Damage / main_damage_share**: "damage" = change of the model's predictions (KL) when the neuron is switched off; the share
  carried by the main mode. `fn` tooltip: *"One meaning does almost all the work: the pole has more than one mode, but the main one
  accounts for N% of the damage when the neuron is switched off."*
- **Selected mode** (`S.mode`, URL `mode`): JS comment: *"S.mode is the ONE selected mode for the whole page (index into the pole's
  modes: open subcircuit modes in published order = the Overview pill index, then gate-closed modes, then unassigned); every tab
  follows it and reports the reader's picks back (landscape-mode / overview-mode / modes-mode); it is in the URL as &mode=."*
- **Sample**: the fixed 3,000-pole pseudo-random subset (2.2). Landing text: *"This atlas shows 3,000 randomly selected neuron poles
  from the expert layers of Qwen3.6-35B-A3B"*.

---

## 4. How the UI renders it

Layout: top bar (`#top`: list button, brand "Qwen3.6 Neuron Atlas" = link to landing, search box, Random button, Keyboard-shortcuts
button); left rail 300 px (`#rail`: title "Neurons", count, 3 dropdowns, the list); main pane = landing page OR pole header + tab frames.
Below 900 px width the rail becomes a slide-over drawer. `<meta name="robots" content="noindex,nofollow">`.

### 4.1 List (one row per POLE)
```html
<a class="nrow pos|neg [pair] [on]" data-did="L30_E47_N449" data-s="+" href="?n=L30_E47_N449&pole=pos" role="option">
  <div class="l1"><span class="id">[+ or −, coloured] L30/E47/N449</span> [sort value]</div>
  <div class="nm">description, clamped to 2 lines  |  "No clear pattern" when desc is empty</div></a>
```
- Sign glyphs: `+` and `−` (U+2212). Screen-reader text "Positive pole of " / "Negative pole of ".
- `pair` class when the next row is the same neuron (the two poles then read as a pair, no rule between them).
- Sort value at the right of line 1 (`sortVal`): with a `sim-*` sort: `"NN%"`, `"< −100%"` (value < -1) or `"Not scored"`; with a
  `modes-*` sort: `"N mode"`/`"N modes"` (empty for poles without `sc`); nothing for layer/random sorts.
- Rows are drawn in batches of `ROWS=150` as the list scrolls (more when within 800 px of the bottom); there is NO cap on the number
  of results.
- Count (`#cnt`): `"3,000 poles"` when no search/category/layer filter is active, else `"<n> of 3,000 poles · Clear"` (numbers via
  `toLocaleString()`). Because the layer filter defaults to `middle`, the default display is **"2,282 of 3,000 poles · Clear"**.
  "Clear" sets category = "", layer = "" (All layers), search = "".

### 4.2 Pole header (`renderHead`)
- `#nid`: sign + `L30/E47/N449`.
- `#coord`: `"Positive pole · <kind> [· One dominant mode] · Layer 30 / Expert 47 / Neuron 449"` where `<kind>` =
  `"<k> mode(s)"` if the pole has `sc`, else the capitalised category name (`"Single mode"`, `"Multiple modes"`, `"Uninterpretable"`,
  `"Not yet read"`); tooltip of `<kind>` = `CAT[c].d`. "One dominant mode" only for `cat=="fn"`.
- `#poles`: the full description (2 lines, expandable; "Show in full"/"Show less"); if empty: `"No clear pattern"` for `dark`, else
  `"Not yet described"`.
- "Copy a link to this view" button (`#bLink`), feedback "Link copied" / "Couldn't copy; use the address bar".
- `document.title = "L30/E47/N449 positive · Qwen3.6 Neuron Atlas"` (landing: `"Qwen3.6 Neuron Atlas"`).

### 4.3 Tabs -> iframes
Tab ids `TABS=["overview","islands","circuit"]`, labels **Overview / Modes / Circuit** (keys 1 / 2 / 3). `VIEWS_V="20261109C"` is a
cache-busting constant (*"bump when a view page changes so iframes skip the cache"*). All URLs are relative to the release base.

| tab (id) | iframe | URL on first load | later navigation | hidden / disabled when |
|---|---|---|---|---|
| Overview (`overview`) | `#fOverview` | `neuron_overview-mobile-v3.html?shell=1&v=20261109C&did=<did>&pole=<pos\|neg>[&mode=<k>]` | `postMessage({type:"shell-load",did,pole:"pos"\|"neg",mode})`; same pole: `{type:"shell-mode",mode}` | button `disabled` + tooltip "No overview for this pole yet" when `!hasOv`; an active/asked overview then falls back to `islands`. Never the case in this release |
| Modes (`islands`) - subcircuit view | `#fIslands` | `subcircuit_modes_view-mobile-v2.html?shell=1&public=1&v=20261109C&pole=<pid>&data=subcircuit_modes_data` (used when `P.sc \|\| (!P.islands && P.sc_empty)`); the selected mode is sent after load via `shell-mode` | frame URL is replaced (`location.replace`, no history step) | - |
| Modes (`islands`) - island view | `#fIslands` | `public_islands_atlas-mobile-v2.html?shell=1&v=20261109C&pole=<pid>&data=public_atlas_data/islands` (used when no `sc` but `P.islands`, i.e. zero-mode read with island data) | same | - |
| Modes - nothing | `#paper` | message `"No modes for the positive pole of this neuron."` + `"The negative pole has them."` (link, only if the other pole is in the sample and `hasModes`) when neither `sc`, `sc_empty` nor `islands` | | cannot occur in this release |
| Circuit (`circuit`) | `#fCircuit` | `public_circuit_explorer-mobile-v1.html?shell=1&v=20261109C&did=<did>&pole=<pos\|neg>` | other neuron: `{type:"shell-load",did,pole:"+"\|"-"}`; always `{type:"shell-pole",pole:"+"\|"-"}` | never disabled by the shell |

JS comment on `public=1`: *"the public atlas hides the research fields (the full atlas does not pass it)"*.
Per-pole data files implied (fetched by the views, paths verified in their scripts): `ns11_pages/data/{pid}.json`,
`neuron_landscape_data/{pid}.json`, `neuron_landscape_data/ctx/{pid}.json`, `subcircuit_modes_data/{pid}.json`,
`ns11_pages/headline_overrides.json` (Overview); `subcircuit_modes_data/{pid}.json` (Modes); `public_atlas_data/islands/poles/{pid}.json`
(island view + landing specimen); `moe_circuit_explorer_data/index.json`, `moe_circuit_explorer_data/{did}/{meta,exemplars,incidence}.json`
(Circuit). The Overview embeds `neuron_landscape-mobile-v3.html?shell=1&bare=1&v=20261109C&did=..&pole=..[&mode=k]`.

Default tab (`openNeuron`): a tab named in the URL wins; otherwise a NEW neuron opens on `overview` if `hasOv` else `islands`; switching
to the other pole of the same neuron keeps the current tab (unless it is `overview` and that pole has none).

Message protocol **[V]** (all `postMessage(...,"*")`):
- shell -> view: `shell-load {did,pole,mode}`, `shell-pole {pole}`, `shell-mode {mode}` (mode = integer index or `null`).
- view -> shell: `shell-key {key}` (keys `j k r R [ 1 2 3 4 p ? /` are forwarded; the shell acts on `/ j k r R Escape 1 2 3 ?`),
  `overview-mode | landscape-mode | modes-mode {pid, mode}` -> `setMode` (ignored if `pid` is not the open pole), stored in `S.mode`,
  written to the URL, and re-sent to the other loaded tab (`fOverview` / `fIslands`).

### 4.4 Landing page (`#landing`, shown when no pole is open; also via the brand link)
Verbatim text:
- H1 "Qwen3.6 Neuron Atlas", byline "By Goodfire" (link `https://www.goodfire.ai`).
- Lede: *"What do individual neurons inside a language model respond to? This atlas shows 3,000 randomly selected neuron poles from the
  expert layers of Qwen3.6-35B-A3B, with examples of text that cause them to fire."*
- Specimen card (id, "Positive pole"/"Negative pole" · category name, description, one shaded passage; "Loading an example…" while
  fetching). Caption: *"Shading shows how strongly the neuron fires. The boxed word is its peak."* Pager "1 of 4", button "Open neuron".
- *"A neuron's output can be positive or negative, and the two signs often respond to different things, so each neuron has a positive
  pole and a negative pole, read separately."*
- *"For each pole, we split the firing examples into subcircuits: groups of examples where the same earlier parts of the model make the
  neuron fire. An LLM judge then grouped subcircuits that respond to similar concepts in the text into modes. A pole with one mode does
  one specific thing; a pole with several modes responds to unrelated concepts."*
- H2 "What's on a neuron page" - *"Each pole of a neuron has a page with three tabs."*
  - **Overview** - "What it does" - *"Shows how strongly the pole fires, which mode each firing belongs to, and how much the model's
    predictions change when we switch the neuron off. Includes example text for each mode."*
  - **Modes** - "What it fires on" - *"Lists each mode and the subcircuits it combines, with example text shaded by how strongly the
    neuron fires on each word."*
  - **Circuit** - "Inputs and outputs" - *"Shows which earlier neurons turn this one on and control how strongly it fires, and which
    later neurons it boosts or suppresses."*
- H2 "Some neurons to start with": up to 4 cards (id + description).

Starter selection **[V]**: `STARTERS=[["L4_E204_N409","+"],["L4_E205_N237","+"],["L39_E63_N90","-"],["L9_E196_N344","-"]]`, each kept
only if in the sample AND `islands` AND non-empty desc (*"Preferred starter examples are used only if randomly sampled; other examples
come from the fixed sample."*); then filled up to 4 from `S.sample` in random order. Result in this release: `L4_E205_N237__pos`,
`L39_E63_N90__neg`, `L9_E196_N344__neg`, `L24_E31_N88__neg` (`L4_E204_N409__pos` is not in the sample).

Specimen passage (`topExample` + `snipHtml`): fetches `public_atlas_data/islands/poles/{pid}.json`, takes
`islands[].examples[]` with `focal_idx != null`, picks the one with the largest `|act|`; `sign = p.sign || (p.pole==="neg"?-1:1)`.
Fields used: `examples[].tokens` = array of `[text, activation]`, `examples[].focal_idx` (index of the peak token), `examples[].act`.
Window: up to 14 non-blank tokens before the peak and 7 after; each token's background alpha = `min(1,|v|/vmax)*0.9` in the pole's
colour (`rgba(224,86,27,a)` positive, `rgba(51,105,138,a)` negative) when `v*sign>0`; opposite-sign tokens get the other colour at
`0.6*a` if `|v| > 0.05*vmax`; white text when alpha > 0.5; the peak token is outlined.

Keyboard-shortcut dialog (verbatim): `/` "Search neurons"; `j` `k` "Next or previous pole in the list"; `r` "Random pole from the
list"; `1` `2` `3` "Overview, Modes, Circuit"; `?` "Show these shortcuts"; `Esc` "Close menus and this dialog".

---

## 5. User controls & URL params

### 5.1 Search (`#q`) - exact algorithm
```js
const q=$("#q").value.trim().toLowerCase();
if(q){ const hay=(n.did+" "+fid(n.did)+" "+n.did.replace(/_/g," ")+" "+(s==="+"?"positive":"negative")+" "+desc(n.poles[s])).toLowerCase();
       if(!q.split(/\s+/).every(t=>hay.includes(t))) return false; }
```
- Haystack per pole (lower-cased): `"l30_e47_n449 l30/e47/n449 l30 e47 n449 positive <final description>"`.
- Query: trimmed, lower-cased, split on whitespace; **every token must be a substring** of the haystack (AND, order-free). No stemming,
  no fuzzy matching, no diacritic/Unicode normalisation, no field weighting, **no relevance ranking**.
- There is no id parser: ids match only because three spellings are in the haystack. Works: `L30_E47_N449`, `L30/E47/N449`,
  `l30 e47 n449`, `30 47 449` (as substrings), partial ids `E47` (14 hits), `N449` (9 hits), `L30 E47`. Does NOT work: the pid
  `L30_E47_N449__pos` (0 hits), `Layer 30` as a layer selector (matches only as the substrings "layer" and "30").
- Pole side: the words `positive` / `negative` are searchable (`L30/E47/N449 positive` -> 1 hit). Short forms also hit descriptions
  (`pos` 1,744 hits, `neg` 1,528).
- Text searched = the FINAL description: for poles with a subcircuit read the mode labels joined by `" · "`; the island description
  (`desc_isl`) is no longer searchable (e.g. `клёв` occurs in the island desc of `L4_E1_N177__neg` but gives 0 hits). Mode
  descriptions, subcircuit labels, unified descriptions and example text (all in per-pole files) are not searched.
- Scope: only the 3,000 sample poles, and it is combined (AND) with the category and layer filters - with the default layer filter
  "Layers 9–36" a search silently ignores layers 4 and 39.
- Order of hits = the current sort (default seeded random order). No limit.
- Behaviour: debounced 120 ms; `Enter` opens the first hit (`S.list[0]`); `Escape` blurs; `/` focuses. Placeholder "Search neurons",
  aria-label "search neurons by id or by what they respond to". The query is NOT stored in the URL.
- Example counts over all layers: `private` 16, `wikipedia` 127, `wikipedia list` 62, `bridge köprü` 1.

### 5.2 Category filter `#fCat` (aria-label "show poles of this kind"; URL `cat`)
`""` "All neurons" (default) | `mono` "Single mode" | `fn` "One dominant mode" | `poly` "Multiple modes" | `dark` "Uninterpretable".
Test: `n.poles[s].cat !== c` => excluded (final category). There is no option for "not yet read" (`null`): those 15 poles appear only
under "All neurons".

### 5.3 Layer filter `#fL` (aria-label "filter by layer"; URL `l`)
`""` "All layers" | `middle` "Layers 9–36" (**default**) | `4`, `9`, `14`, `19`, `24`, `30`, `36`, `39` "Layer N".
```js
const XL=new Set([4,39]);   /* Show layers 9–36 by default; All layers exposes the full 3,000-pole sample. */
if(L==="middle"){ if(XL.has(n.L)) return false; } else if(L&&L!=="all"&&String(n.L)!==L) return false;
```
"Layers 9–36" = every sampled layer except the first (4) and last (39) = layers 9, 14, 19, 24, 30, 36 (2,282 poles). `"all"` is
tolerated in the test but is not an option, so `l=all` in a URL is ignored.

### 5.4 Sort `#fSort` (aria-label "sort the list"; URL `sort`)
| value | label | comparator |
|---|---|---|
| `random` (default) | Random order | ascending `rhash(pid)` (seed 20261109), ties by pid; same order for everyone |
| `sim-desc` / `sim-asc` | Simulatability, high to low / low to high | by `sim`; poles without a score last in both directions; ties and the unscored block in base order |
| `layer-asc` / `layer-desc` | Layer, first to last / last to first | by `L`, then `E`, then `N` in the same direction, positive pole before negative always |
| `modes-desc` / `modes-asc` | Modes, most first / fewest first | by `sc.modes.length`; poles without `sc` (zero-mode reads) last in both directions; ties in base order |

Base order = `L` asc, `E` asc, `N` asc, `+` before `-`. Exact code:
```js
const [k,dir]=value.split("-"), sg=dir==="asc"?1:-1, val=k==="sim"?simOf:k==="modes"?modesOf:(n=>n.L);
const base=(a,b)=>a.n.L-b.n.L||a.n.E-b.n.E||a.n.N-b.n.N||(a.s==="+"?-1:1)-(b.s==="+"?-1:1);
sort((a,b)=>{ if(a.v==null||b.v==null) return a.v==null&&b.v==null?base(a,b):a.v==null?1:-1;
  return sg*(a.v-b.v)||(k==="layer"?sg*(a.n.E-b.n.E||a.n.N-b.n.N)||(a.s==="+"?-1:1)-(b.s==="+"?-1:1):base(a,b)); });
```
JS comment: *"sort: simulatability = share of the neuron's effect (KL) that a simulator recovers from the pole's description on held-out
passages; layer; number of modes. Poles with no value (never scored / no subcircuit read) go last in either direction."*
Check values (All layers): `sim-desc` starts `L4_E1_N271__neg` 0.9983; `sim-asc` starts `L9_E143_N272__pos` -33.6162; `modes-desc`
starts `L4_E127_N163__pos` (30 modes); `layer-asc` starts `L4_E1_N177__neg`.

### 5.5 Other controls
- Random button (`#rnd`, key `r`/`R`, tooltip "Random neuron from the list"): `Math.random()` pick from the current filtered list (not
  seeded), never the open pole.
- `j` / `k`: next / previous pole in the current list (wraps around).
- Brand link: back to the landing page (filters kept). List button / scrim: open/close the list drawer on narrow screens.
- Tabs (1/2/3), description expander, copy-link button, keyboard-shortcut dialog (`?`).

### 5.6 URL grammar (query string of the shell; the host page mirrors exactly these 7 params)
| param | values | default / notes |
|---|---|---|
| `n` | a `did`, exact and case-sensitive: `L30_E47_N449` (looked up as `S.by[n]`; the `/` form is not accepted) | absent, unknown, or pole not in the sample => landing page |
| `pole` | `pos` \| `neg` | anything other than exactly `neg` means positive. If that pole is not in the sample => landing (no fallback to the other pole) |
| `tab` | `overview` \| `islands` \| `circuit`; legacy `landscape` => `overview` | default: `overview` if the pole has one, else `islands`. NOTE the Modes tab is `islands`; `tab=modes` is invalid and ignored |
| `mode` | a number (`!isNaN(+m)`, non-empty) = index of the selected mode | default none (`null` = all modes). Only applied once, to the pole named by the link |
| `cat` | `mono` \| `fn` \| `poly` \| `dark` | default "" (all); invalid values ignored; omitted from the URL when empty |
| `l` | `` (empty = All layers) \| `middle` \| `4` \| `9` \| `14` \| `19` \| `24` \| `30` \| `36` \| `39` | **absent => `middle`**; `l=` (present, empty) => all layers. Always written by `sync()` (*"Preserve an explicit All layers choice (l=) when reloading or sharing a view."*) |
| `sort` | `sim-desc` \| `sim-asc` \| `layer-asc` \| `layer-desc` \| `modes-desc` \| `modes-asc` \| `random` | default `random`, which is left out of the URL |

`sync()` writes the params in the order `n, pole, tab, mode, cat, l, sort`; e.g. landing = `?l=middle`; a pole =
`?n=L30_E47_N449&pole=pos&tab=overview&l=middle`. History: a new page (did+pole) pushes a history entry; tabs, modes and filters replace
it. `popstate` re-reads only `n/pole/tab/mode`.

Public deep link (`#bLink`): `copyText(window.goodfireAtlasPublicUrl?.() || location.href)`.
`goodfire-host-back-v1.js`: `window.goodfireAtlasPublicUrl = () => publicUrl + location.search` with
`publicUrl = 'https://www.goodfire.com/research/public_neuron_atlas'` by default => e.g.
`https://www.goodfire.com/research/public_neuron_atlas?n=L30_E47_N449&pole=pos&tab=overview&l=middle`.
The script also wraps `history.pushState/replaceState` to post `{type:'goodfire-atlas-location', search: location.search}` to the
parent, after the parent has sent `{type:'goodfire-atlas-host', url}`. Accepted parent origins: `https://goodfire.com`,
`https://www.goodfire.com`, `https://(main|pr-<n>).(d2yf7yisq2kwm5|d1rfwjs09oezrr).amplifyapp.com`, `http://127.0.0.1|localhost`, and
`url.pathname` must be `/research/public_neuron_atlas`; `publicUrl` then becomes that origin + path.
Host page side **[V]** (saved `public_neuron_atlas.html`): copies only the whitelisted params `n, pole, tab, mode, cat, l, sort` (each
value shorter than 200 chars) from its own URL to the iframe `src`, and mirrors the iframe's reported search back with
`history.replaceState` (messages longer than 4,096 chars ignored). Search text (`q`) is therefore never linkable.

---

## 6. Value statistics

### 6.1 Populations
| population | neurons | poles |
|---|---|---|
| A. everything in `public_atlas_data/index.json` | 3,244 | 6,488 |
| - layer 14 / expert 78 (whole expert, excluded from the public sample) | 512 | 1,024 |
| - hand-added `L27_E247_N96`, `L35_E88_N30` (excluded) | 2 | 4 |
| B. candidate pool | 2,730 | 5,460 |
| C. public sample (visible in the UI) | 2,202 | 3,000 (1,508 positive, 1,492 negative) |
| poles with a subcircuit record (`subcircuit_modes_index.poles`) | | 5,477 = 5,460 (all of B) + 13 of L14/E78 + 4 not in the public index |
| poles with a landscape / overview (`atlas_index.json` = `ns11.pids`) | | 5,477 (the same set) |
| poles with `sim` | | 585 (all in B); 334 in the sample |

Per layer:
| layer | A neurons | B neurons | B poles | sample neurons | sample poles | sample poles with sim | distinct experts (A) |
|---|---|---|---|---|---|---|---|
| 4 | 336 | 336 | 672 | 281 | 364 | 53 | 175 |
| 9 | 361 | 361 | 722 | 290 | 389 | 32 | 202 |
| 14 | 838 | 326 | 652 | 260 | 352 | 40 | 186 |
| 19 | 346 | 346 | 692 | 272 | 374 | 43 | 197 |
| 24 | 328 | 328 | 656 | 271 | 369 | 39 | 181 |
| 27 | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| 30 | 349 | 349 | 698 | 280 | 389 | 50 | 179 |
| 35 | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| 36 | 362 | 362 | 724 | 288 | 409 | 45 | 196 |
| 39 | 322 | 322 | 644 | 260 | 354 | 32 | 185 |

Experts: `E` 0..255 (256 distinct); neurons: `N` 0..511 (512 distinct). Apart from L14/E78 (512 neurons) an (L, E) pair has 1-7
neurons in the index. The sample covers 1,335 distinct (layer, expert) pairs. **No "shared" / non-expert neurons**: every id in all
four indices matches `L<int>_E<int>_N<int>`.

### 6.2 Categories
| | mono | fn | poly | dark | null ("not yet read") | total |
|---|---|---|---|---|---|---|
| raw island `cat`, all poles (A) | 4,042 | - | 1,922 | 346 | 178 | 6,488 |
| final (applySC applied to all), A | 2,460 | 1,651 | 2,281 | 68 | 28 | 6,488 |
| final, candidate pool B | 2,126 | 1,651 | 1,658 | 2 | 23 | 5,460 |
| **final, public sample C (what `#fCat` counts)** | **1,169** | **922** | **892** | **2** | **15** | **3,000** |
| final, sample with default layer filter (`middle`) | 923 | 691 | 653 | 1 | 14 | 2,282 |
| island `cat` of the sample poles (before override) | 2,043 | - | 710 | 153 | 94 | 3,000 |

Sample by layer (final category): L4 mono 61 / fn 136 / poly 166 / dark 1; L9 129 / 149 / 109 + 2 null; L14 145 / 87 / 115 + 1 dark +
4 null; L19 167 / 95 / 109 + 3 null; L24 113 / 120 / 132 + 4 null; L30 175 / 107 / 106 + 1 null; L36 194 / 133 / 82; L39 185 / 95 / 73 +
1 null.

Sample by data source: 2,972 poles have `sc` (>= 1 mode); 28 have `sc_empty` (13 with island data: island cat mono 8, poly 3, dark 2;
15 without: cat null, empty description). Modes-tab view: subcircuit view 2,987, island view 13, "no modes" message 0.
Island -> final transitions among the 2,972: mono->mono 1,070, mono->fn 692, mono->poly 273, poly->poly 472, poly->fn 193,
poly->mono 42, dark->poly 105, dark->fn 26, dark->mono 20, null->poly 39, null->mono 29, null->fn 11.

### 6.3 Subcircuit records (all 5,477)
- `verdict`: monosemantic 2,122, polysemantic 3,355 (of which `fn_mono` 1,651).
- `n_modes`: 0: 51, 1: 2,114, 2: 789, 3: 533, 4: 375, 5: 290, 6: 199, 7: 183, 8: 136, 9: 111, 10: 112, 11: 101, 12: 81, 13: 75,
  14: 59, 15: 66, 16: 42, 17: 53, 18: 22, 19: 27, 20: 12, 21: 8, 22: 7, 23: 6, 24: 5, 25: 9, 26: 4, 27: 6, 30: 1. Mean 4.13, median 2.
  Total modes 22,620.
- Sample (2,972 poles with `sc`): 1: 1,161, 2: 435, 3: 294, 4: 194, 5: 162, 6: 109, 7: 100, 8: 72, 9: 60, 10: 67, 11: 53, 12: 48,
  13: 40, 14: 34, 15: 33, 16: 27, 17: 27, 18: 10, 19: 15, 20: 5, 21: 4, 22: 2, 23: 3, 24: 4, 25: 7, 26: 3, 27: 2, 30: 1. Mean 4.16,
  median 2, max 30.
- `n_residue`: 0: 280, 1: 3,150, 2: 1,993, 3: 47, 4: 6, 5: 1. `n_subcircuits`: 1..51, quartiles 13 / 19 / 25, mean 19.9.
- `main_damage_share` (5,396 non-null): [0,0.1) 394, [0.1,0.2) 155, [0.2,0.3) 137, [0.3,0.4) 138, [0.4,0.5) 112, [0.5,0.6) 144,
  [0.6,0.7) 150, [0.7,0.8) 190, [0.8,0.9) 236, [0.9,0.95) 226, [0.95,0.99) 412, [0.99,1.0] 3,102 (exactly 1.0: 2,529; exactly 0: 27).
  Quantiles 10/25/50/75 %: 0.193 / 0.762 / 0.999 / 1.0. Sample: mono 1.0 (13 null), fn 0.901..1.0 (median 0.995), poly 0.0..0.899
  (median 0.417, 4 null).
- mode `share` (22,620): [0,0.001) 34 (all exactly 0), [0.001,0.005) 807, [0.005,0.01) 3,432, [0.01,0.02) 3,976, [0.02,0.05) 4,558,
  [0.05,0.1) 2,728, [0.1,0.2) 1,988, [0.2,0.3) 895, [0.3,0.5) 967, [0.5,0.7) 1,307, [0.7,0.9) 1,441, [0.9,1.0002] 487. Median 0.0365.
  Top-mode share per pole: median 0.607. Sum of shares per pole: median 0.715, 5 % quantile 0.224, max 1.0004.
- Per layer (mono / fn / poly / zero-mode): L4 113 / 246 / 306 / 9; L9 239 / 267 / 206 / 10; L14 264 / 155 / 235 / 11;
  L19 304 / 185 / 199 / 4; L24 209 / 197 / 243 / 7; L30 316 / 183 / 197 / 2; L36 352 / 232 / 140 / 0; L39 315 / 186 / 137 / 8.

### 6.4 Simulatability (`sim`, 585 poles)
290 positive, 295 negative poles; per layer 4: 87, 9: 68, 14: 67, 19: 67, 24: 74, 30: 90, 36: 75, 39: 57.
Histogram of the non-negative values: [0,0.1) 3, [0.1,0.2) 3, [0.2,0.3) 14, [0.3,0.4) 33, [0.4,0.5) 85, [0.5,0.6) 125, [0.6,0.7) 138,
[0.7,0.8) 83, [0.8,0.9) 52, [0.9,1.0) 37. Negative values: -33.6162, -24.6868, -20.4912, -13.381, -6.1294, -1.73, -0.4706, -0.3802,
-0.2896, -0.1467, -0.1397, -0.1088. Sample: 334 scored (249 in the default layers 9-36); by category mono 120, poly 113, fn 101;
range -33.6162..0.9983, median 0.620; 5 negative, 3 below -1.

### 6.5 Descriptions
Final description of sample poles: 17 empty (15 null + 2 dark), otherwise 49..2,437 characters, median 220.

---

## 7. Text fields worth indexing for search
1. `subcircuit_modes_index.json` -> `poles[pid].modes[].label` (22,620 labels; the text the official UI searches for 99 % of poles).
   Index per mode (with `share` as weight and the mode index for `&mode=` deep links) and also joined per pole.
2. `public_atlas_data/index.json` -> `neurons[].poles[s].desc` (island-level description; 5,964 non-empty). The official UI drops it
   for poles with a subcircuit read - indexing it adds vocabulary the official search cannot reach.
3. Ids: `did`, the `/` form, `pid`, and structured `L` / `E` / `N` / pole so that `L30/E47/N449`, `L30_E47_N449__pos`, `layer:30`
   style queries work (the official search has no id parser).
4. Category vocabulary for facets: `mono`/`fn`/`poly`/`dark`/`none` with the names "single mode", "one dominant mode", "multiple
   modes", "uninterpretable", "not yet read"; raw `verdict`.
5. Not in the indices, but richer text exists per pole (other parts of the atlas): mode `description`, subcircuit labels,
   `unified_description`, `label`, `summary` in `subcircuit_modes_data/{pid}.json`; island `name`/`description` and example tokens in
   `public_atlas_data/islands/poles/{pid}.json`.

## 8. Numeric / geometric structure usable for analysis (shell indices only)
- Coordinates of every neuron: `(L, E, N)` integers; pole sign.
- Per pole: the vector of mode shares `modes[].share` (descending; remainder to 1 = residue), `n_modes`, `n_residue`,
  `n_subcircuits`, `main_damage_share`, `verdict`, `fn_mono`; `sim` for 585 poles.
- The seeded rank `rhash(pid)` (sample membership = rank <= 3000 within the candidate pool).
- The indices contain NO embeddings, coordinates, edges, weights or neuron-to-neuron links. Those live in per-pole/per-neuron files:
  `neuron_landscape_data/{pid}.json` (+ `ctx/`), `subcircuit_modes_data/{pid}.json`, `ns11_pages/data/{pid}.json`,
  `moe_circuit_explorer_data/{did}/...` and `moe_circuit_explorer_data/index.json`. The shell only links to them by `pid` / `did`.

## 9. Gotchas / variation
1. The UI exposes only 3,000 of 6,488 poles. Deep links to other poles silently show the landing page. A complete download must
   iterate the index files, not the UI list.
2. Index coverage differs: 1,015 poles of the public index have no subcircuit/landscape/ns11 entry (1,011 of L14/E78 + the 4 poles of
   `L27_E247_N96`, `L35_E88_N30`); 13 L14/E78 poles do have one; 4 pids exist only in the three secondary indices
   (`L39_E120_N508__pos/neg`, `L4_E206_N33__pos/neg`, all zero-mode reads) and have no entry in `public_atlas_data/index.json`.
3. `cat` / `desc` in `public_atlas_data/index.json` are NOT what the UI shows for most poles; the shown values come from
   `subcircuit_modes_index.json` (2.3). Keep both.
4. `cat` can be JSON `null`; `desc` can be `""`; `main_damage_share` can be `null` (81); `share` can be the integer `0`; a pole can have
   modes whose shares are all 0 (e.g. `L4_E1_N345__neg`).
5. `verdict` alone is not the category (`fn` needs `fn_mono`); `verdict` of zero-mode records is meaningless (10 are "monosemantic").
6. The mode-list separator `" · "` also occurs inside one label, so do not split joined descriptions - use `modes[]`.
7. URL: the Modes tab is `tab=islands`; missing `l` means layers 9-36, empty `l=` means all; `sort=random` is implicit; the search
   text is not a URL parameter; `n` must be the underscore form.
8. `&mode=k` indexes the modes array of `neuron_landscape_data/{pid}.json` (readable modes, then gate-closed, then "Unassigned"). For
   `k < n_modes` it matched `subcircuit_modes_index` order in 5 of 6 sample poles; `L4_E1_N345__neg` differs - do not assume equality.
9. `sim` is a fraction, not a percentage, and is unbounded below (min -33.6); the UI prints "< −100%" below -1.
10. `S.ov` (`ns11.pids`) and `ns11.model` are loaded but unused by the shell; `hasOv` is driven by `atlas_index.json` only.
11. `neurons[]` is not sorted (last element out of order); `ns11.pids`, `atlas_index.json` and `poles` keys are in plain string order
    (`L14...` before `L4...`).
12. The Random button is not seeded (only the "Random order" list is).
13. UI strings use typographic characters: `−` U+2212, `–` U+2013 ("Layers 9–36"), `·` U+00B7, `…` U+2026.
14. Local file name `explorer.html` vs upstream `public_neuron_atlas-sample-3000-v2.html` (see top).
