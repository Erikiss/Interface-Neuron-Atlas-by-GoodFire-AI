"""The vocabulary of the atlas, and a plain-text guide for language models.

The definitions follow the wording of the explorer on goodfire.com (its
landing page, tooltips and "how to read this page" notes); quotation marks
mark text taken from there.
"""

from __future__ import annotations

from typing import Any

GLOSSARY: dict[str, Any] = {
    "source": "Qwen3.6 Neuron Atlas by Goodfire, https://www.goodfire.com/research/public_neuron_atlas",
    "model": "Qwen3.6-35B-A3B, a mixture-of-experts language model. The atlas covers neurons of its expert layers.",
    "terms": {
        "neuron id": (
            "Layer / expert / neuron, written L30/E47/N449 or L30_E47_N449. A few units are not routed "
            "through an expert and read L4_shared_N126."
        ),
        "pole": (
            "“A neuron's output can be positive or negative, and the two signs often respond to different "
            "things, so each neuron has a positive pole and a negative pole, read separately.” Pole ids end "
            "in __pos or __neg. Strengths of a negative pole are given as positive numbers."
        ),
        "firing": "A token on which the pole's activation is in its top 5 % (not: above the threshold θ).",
        "threshold (θ)": (
            "“The activation at which this pole typically starts to fire.” Strengths are quoted in multiples "
            "of θ, e.g. “5.0× threshold”. Caution: the mode / overview / landscape data use one θ per pole, "
            "the island and circuit data another θ per neuron; the two can differ by a factor of two or more, "
            "so “N× threshold” is only comparable within one data set."
        ),
        "subcircuit": (
            "“Groups of examples where the same earlier parts of the model make the neuron fire.” "
            "Ids look like c0, c1, …"
        ),
        "mode": (
            "“An LLM judge grouped subcircuits that respond to similar concepts in the text into modes. "
            "A pole with one mode does one specific thing; a pole with several modes responds to unrelated concepts.”"
        ),
        "share": (
            "A mode's share of the pole's sampled firings (each of the five rarity levels counts equally). "
            "The Overview instead shows firing_share, weighted to how often firings occur in real text - "
            "the two can differ a lot."
        ),
        "damage": (
            "“How much the model's next-word prediction changes (KL divergence over the next 16 tokens) when "
            "the neuron is zeroed at the firing token.” About 200 firings per pole were tested directly; the "
            "rest is estimated from strength and mode."
        ),
        "damage_share": "Share of the pole's total damage carried by a mode, weighted to real-text frequency.",
        "damage_per_firing": (
            "Damage of one firing of the mode relative to the pole's average firing (1 = typical), weighted to "
            "real-text frequency (landscape / Overview). The Modes tab shows the same idea over the sampled "
            "firings instead (`damage.ratio` of a mode); the two numbers differ, often by a lot."
        ),
        "main_damage_share": (
            "The main mode's share of the pole's damage, which decides the category fn (at least 90 %). The main "
            "mode is the readable mode with the most sampled firings - not necessarily the most damaging one - and "
            "the share is taken among the readable modes only (Q3/Q4 and unassigned firings are left out), over the "
            "sampled firings without weighting to real text. Not comparable with damage_share; the damage filter "
            "and sort=damage use this number."
        ),
        "category": {
            "mono": "single mode - “Everything this pole fires on fits one specific meaning.”",
            "fn": (
                "one dominant mode - “This pole has more than one mode, but the main one accounts for at least "
                "90% of the damage when the neuron is switched off.”"
            ),
            "poly": "multiple modes - “This pole fires on two or more different, unrelated things.”",
            "dark": "uninterpretable - “No clear pattern was found in what this pole fires on.”",
            "none": "not yet read",
        },
        "Q3/Q4 (closed gate)": (
            "“Firings where the neuron's gate input is negative (quadrants 3 and 4 of its gate × up plane)”: "
            "the neuron “fired only through a weak leak”. The neuron is a gated unit, activation = silu(gate) × up; "
            "on a positive pole such a firing is Q3, on a negative pole Q4."
        ),
        "unassigned / residue": "Firings and subcircuits that fit no mode.",
        "island": (
            "The older read: “we take its strongest firing examples from a large text corpus and cluster them "
            "into islands: groups of examples that occur in similar contexts.” Used on goodfire.com only for "
            "poles without a usable subcircuit read."
        ),
        "simulatability": (
            "“A simulator that never saw the neuron predicts its activation on held-out text from each "
            "description; the score is the share of the neuron's effect on the model's predictions (KL) that "
            "those predicted activations restore. 100% = as good as the real neuron.” A fraction; it can be "
            "negative (the simulation then does more damage than switching the neuron off). Only about 590 "
            "poles are scored."
        ),
        "rarity levels": (
            "Firings are sampled at five levels: top 5 %, top 1 %, top 0.1 %, top 0.01 % of tokens and the "
            "1,000 strongest firings."
        ),
        "gate / up": (
            "The two inputs of a neuron, which writes silu(gate) × up. Gate drivers open the gate and gate "
            "opposers close it, on either pole. Up drivers push the up signal towards the pole's own sign and up "
            "opposers push it the other way: on a positive pole up drivers raise up, on a negative pole they lower "
            "it (“groups that lower up are the ones pushing it to fire”). Readers are later neurons that use the "
            "neuron's output."
        ),
        "neuron scientist": (
            "An LLM agent that wrote the Overview explanation of a pole and ran controlled experiments "
            "(“the same passage with one thing changed, activation measured on the model itself”)."
        ),
        "public sample": (
            "goodfire.com lists 3,000 randomly selected poles. The published files describe 6,488 poles; "
            "this interface searches all of them and marks which ones are in the public sample."
        ),
        "focal token": "The token on which the neuron fired; shown as «token» in example texts.",
    },
    "text conventions": {
        "«x»": "the firing token inside an example or a label",
        "|": "a token boundary inside a mode label",
        "⟦x⟧": "the firing token inside a scientist's cue",
        "N×": "N times the pole's firing threshold θ",
    },
}

_LLMS = """# Neuron Atlas Interface

> Unofficial search interface and API for the Qwen3.6 Neuron Atlas published by Goodfire
> (https://www.goodfire.com/research/public_neuron_atlas). The atlas describes what individual
> neurons of the language model Qwen3.6-35B-A3B respond to. All descriptions are in English.

Base URL: {base}

## Concepts (short)

- A neuron is named layer/expert/neuron: L30_E47_N449. It has two poles, read separately:
  L30_E47_N449__pos and L30_E47_N449__neg.
- A pole fires in one or several modes (distinct meanings). Each mode has a label, a share of the
  pole's firings, a description and example passages; the firing token is marked «like this».
- Categories: mono (single mode), fn (one dominant mode), poly (multiple modes), dark (uninterpretable),
  none (not yet read).
- Full vocabulary: GET {base}/api/v1/glossary

## How to use the API

1. Search:      GET {base}/api/v1/search?q=braille&limit=10
   - q takes words, "exact phrases", -exclusions, prefix*, OR / NOT (operators in capitals only), an id,
     and inline filters written without a space after the colon:
     layer:30 expert:47 pole:neg cat:mono,fn modes:>=3 sim:>0.8 has:overview sample:public
     in:labels,deep,examples chooses the texts searched (default labels,deep; deep and examples only
     exist for mirrored poles).
   - match=semantic ranks by meaning instead of exact words; match=substring / regex also exist.
   - Unknown parameters and malformed filters are refused with HTTP 400 and an explanation.
   - group_by=mode returns one hit per matching mode; group_by=neuron folds both poles.
   - sort=relevance|simulatability|layer|modes|damage|share|random|id, paging with limit and offset.
2. Read a pole: GET {base}/api/v1/poles/L30_E47_N449__pos?include=modes,examples&max_examples=3
   - include: modes, subcircuits, examples, islands, overview, landscape, circuit, tokens, research, or all.
   - format=md returns a Markdown document that is easy to quote; format=html is a print-ready page.
3. Related:     GET {base}/api/v1/similar?pole=L30_E47_N449__pos   (or ?text=random+hex+hashes)
4. Many at once: POST {base}/api/v1/search/batch  {{"queries": [{{"q": "…"}}, …]}}
                 POST {base}/api/v1/poles/batch   {{"ids": ["L30_E47_N449", …]}}
5. Everything matching a query as a stream: GET {base}/api/v1/export?q=…&format=ndjson|csv|md

Machine-readable description of every endpoint: {base}/openapi.json (interactive: {base}/docs).
MCP (Model Context Protocol) endpoint with the same functions as tools: {base}/mcp

## Notes for answering questions

- Quote ids exactly and say which pole you mean. Link to the original page when `atlas_url` is given.
- `share` is the share of sampled firings; `firing_share` / `damage_share` (include=landscape) are
  weighted to real text and are what the original Overview shows. Do not mix them up.
- Example passages are raw web text (any language, code, spam, adult content may occur).
- goodfire.com lists a sample of 3,000 poles; this interface covers every published pole (6,492 in the
  2026-11-09 release; `in_public_sample` tells them apart). The data belongs to Goodfire AI.
- MCP answers stay within `max_chars`; when one says it was shortened, ask for less or raise max_chars.
"""


def llms_txt(base_url: str) -> str:
    return _LLMS.format(base=base_url.rstrip("/"))
