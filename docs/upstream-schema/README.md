# Upstream data: reconstructed schemas

The Qwen3.6 Neuron Atlas is a static site: a handful of HTML view pages and JSON files below
one immutable release prefix (`https://static.goodfire.ai/neuron-atlas/<release>/`). Goodfire
publishes no schema, so these notes were reconstructed from the view pages' JavaScript (which
defines what every field means) and from a small number of sample files of release
`2026-11-09-082204`.

| Document | Covers |
|---|---|
| [`shell.md`](shell.md) | The explorer shell: the four boot indices, the 3,000-pole sample, category rules, search, filters, sorting, URL parameters |
| [`subcircuit.md`](subcircuit.md) | `subcircuit_modes_index.json`, `subcircuit_modes_data/{pid}.json`: modes, subcircuits, readings, links to other neurons, examples |
| [`overview.md`](overview.md) | `ns11_pages/data/{pid}.json`, headline overrides: the written explanation, experiments, simulatability |
| [`landscape.md`](landscape.md) | `neuron_landscape_data/{pid}.json`: rarity levels, per-mode firings and damage, sampled firings, mode similarity |
| [`islands.md`](islands.md) | `public_atlas_data/islands/…`: the older island-based read |
| [`circuit.md`](circuit.md) | `moe_circuit_explorer_data/{did}/{meta,exemplars,incidence}.json`: input and reader groups, edge extraction, column encodings |
| [`crosscut.md`](crosscut.md) | How the data sets relate: join keys, coverage, duplicated information, sizes, inventories of numeric and text fields |

Reading notes:

- Statements are marked as verified (from the JavaScript or the data) or inferred. Counts and value
  ranges given "in the samples" come from six poles and three neurons only.
- The notes describe the explorer's fields and behaviour in our own words. They contain no text,
  code or data copied from Goodfire's site beyond what is needed to name things (field names,
  paths, ids, category keys, short labels). The atlas data itself is not part of this repository.
- The parsers in `src/neuron_atlas/` follow these notes and are deliberately tolerant of missing
  or extra fields.
