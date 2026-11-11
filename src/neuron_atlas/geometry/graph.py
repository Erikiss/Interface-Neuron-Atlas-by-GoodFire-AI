"""The neuron-to-neuron graph hidden in the circuit files.

Each neuron of the atlas ("hub") comes with a circuit file that names about
500 earlier neurons feeding its gate and up signals and about 120 later
neurons reading its output - by id, pole and signed weight.  Almost all of
these neighbours lie outside the atlas's own neuron set, so the graph is
hub-and-spoke: hubs are linked to each other mostly *through shared
neighbours*.  This module extracts the edges, walks neighbourhoods, writes
graph files and measures how similar two hubs are by the inputs they share.

Sign conventions (from the explorer):

* input edge, lane ``gate`` / ``up``: weight ``> 0`` raises that signal of the
  hub, ``< 0`` lowers it.  The two lanes are never added together.
* ``role`` says what that means for the hub's pole: ``drives`` or ``opposes``.
* reader edge: ``effect`` ``raised`` / ``lowered`` is the direction; the stored
  weight's own sign is not reliable, so its magnitude is used.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape, quoteattr

from ..upstream import PRESENT, AtlasError, neuron_path, pole_path

if TYPE_CHECKING:  # pragma: no cover
    from ..service import Atlas

Row = dict[str, Any]

_BUCKET = re.compile(r"^[+-]\|")
_POLE_IN_TEXT = re.compile(r"([+-]) pole")
_LANE_TAG = re.compile(r"\s+·\s+(gate|up-lane|up)\b.*$", re.IGNORECASE)
_INPUT_ROLE = re.compile(r"pole, (drives|opposes)")
_READER_EFFECT = re.compile(r"pole, (raised|lowered)")
_DESCRIPTION_PREFIX = re.compile(r"^[^—]*pole[^—]*—\s*")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def pole_word(sign: Any) -> str | None:
    """``+`` -> ``pos``, ``-`` / ``−`` -> ``neg``; anything else -> ``None``."""
    if sign in ("+", "pos"):
        return "pos"
    if sign in ("-", "−", "neg"):
        return "neg"
    return None


def group_side(group: dict[str, Any]) -> str | None:
    """Which pole of the hub a group belongs to (``+`` / ``-``), as the explorer decides it."""
    bucket = str(group.get("bucket") or "")
    if _BUCKET.match(bucket):
        return bucket[0]
    m = _POLE_IN_TEXT.search(f"{group.get('name') or ''} {group.get('description') or ''}")
    return m.group(1) if m else None


def clean_group_name(group: dict[str, Any]) -> str:
    """Group name without the internal lane tag (``· gate + / up +``)."""
    return _LANE_TAG.sub("", str(group.get("name") or "")).strip() or "Unnamed group"


def lane_tag(group: dict[str, Any]) -> str | None:
    m = _LANE_TAG.search(str(group.get("name") or ""))
    return m.group(0).strip(" ·").strip() if m else None


def clean_group_description(group: dict[str, Any]) -> str:
    """Description without the "<pole>, <role> —" prefix; empty when it only lists category counts."""
    text = _DESCRIPTION_PREFIX.sub("", str(group.get("description") or "")).strip()
    if re.match(r"^\d+ categories:", text):
        return ""
    return re.sub(r"^Units\b", "Neurons", text)


def input_role(group: dict[str, Any]) -> str | None:
    bucket = str(group.get("bucket") or "")
    if "|" in bucket:
        return bucket.split("|", 1)[1] or None
    m = _INPUT_ROLE.search(str(group.get("name") or ""))
    return m.group(1) if m else None


def reader_effect(group: dict[str, Any]) -> str | None:
    bucket = str(group.get("bucket") or "")
    if "|" in bucket:
        return bucket.split("|", 1)[1] or None
    m = _READER_EFFECT.search(str(group.get("name") or ""))
    return m.group(1) if m else None


def circuit_edges(meta: dict[str, Any]) -> Iterator[Row]:
    """Every neuron-to-neuron edge of one circuit file.

    Input edges run from an earlier neuron to the hub and carry ``gate`` and
    ``up`` (signed contribution to that signal).  Reader edges run from the hub
    to a later neuron and carry ``weight`` (magnitude) and ``effect``.
    """
    hub = str((meta.get("identity") or {}).get("did") or "")
    groups = meta.get("groups") or {}
    for g in groups.get("input") or []:
        side, role, name = pole_word(group_side(g)), input_role(g), clean_group_name(g)
        for m in g.get("members") or []:
            if not m.get("did"):
                continue
            yield {
                "kind": "input",
                "source": str(m["did"]),
                "source_pole": pole_word(m.get("pole")),
                "target": hub,
                "target_pole": side,
                "group": g.get("id"),
                "group_name": name,
                "role": role,
                "effect": None,
                "gate": _num(m.get("gate_s")),
                "up": _num(m.get("up_s")),
                "weight": None,
                "layer_distance": (m.get("layer") - (meta.get("identity") or {}).get("layer"))
                if isinstance(m.get("layer"), int) and isinstance((meta.get("identity") or {}).get("layer"), int)
                else None,
                "examples_active": m.get("ev_n_active"),
                "label": m.get("label_full") or m.get("label") or "",
            }
    for g in groups.get("readers") or []:
        side, effect, name = pole_word(group_side(g)), reader_effect(g), clean_group_name(g)
        for m in g.get("members") or []:
            if not m.get("did"):
                continue
            w = _num(m.get("w"))
            yield {
                "kind": "reader",
                "source": hub,
                "source_pole": side,
                "target": str(m["did"]),
                "target_pole": pole_word(m.get("pole")),
                "group": g.get("id"),
                "group_name": name,
                "role": None,
                "effect": effect,
                "gate": None,
                "up": None,
                "weight": abs(w) if w is not None else None,
                "layer_distance": m.get("hop"),
                "examples_active": m.get("ev_n_active"),
                "label": m.get("label_full") or m.get("label") or "",
            }


def neuron_links(meta: Any) -> list[str]:
    """Ids of all neurons a circuit file connects its hub to."""
    if not isinstance(meta, dict):
        return []
    hub = str((meta.get("identity") or {}).get("did") or "")
    out: dict[str, None] = {}
    for edge in circuit_edges(meta):
        other = edge["source"] if edge["target"] == hub else edge["target"]
        out[other] = None
    return list(out)


def circuit_edge_rows(did: str, meta: dict[str, Any]) -> Iterator[Row]:
    for edge in circuit_edges(meta):
        yield {"hub": did, **edge}


def circuit_group_rows(did: str, meta: dict[str, Any]) -> Iterator[Row]:
    """One row per input / reader group of a circuit file, with the numbers the explorer shows."""
    groups = meta.get("groups") or {}
    view = meta.get("pole_view") or {}
    shown: dict[tuple[str, str], dict[str, Any]] = {}
    for sign in ("+", "-"):
        for r in (view.get(sign) or {}).get("readers") or []:
            shown[(sign, str(r.get("id")))] = r
    for g in groups.get("input") or []:
        agg = g.get("agg") or {}
        yield {
            "hub": did,
            "kind": "input",
            "group": g.get("id"),
            "name": clean_group_name(g),
            "lane_tag": lane_tag(g),
            "pole": pole_word(group_side(g)),
            "role": input_role(g),
            "members": len(g.get("members") or []),
            "gate": _num((agg.get("gate") or {}).get("mean_signed")),
            "up": _num((agg.get("up") or {}).get("mean_signed")),
            "examples": None,
            "mean_effect": None,
            "description": clean_group_description(g),
        }
    for g in groups.get("readers") or []:
        side = group_side(g)
        row = shown.get((side or "", str(g.get("id")))) or {}
        yield {
            "hub": did,
            "kind": "reader",
            "group": g.get("id"),
            "name": clean_group_name(g),
            "lane_tag": None,
            "pole": pole_word(side),
            "role": reader_effect(g),
            "members": len(g.get("members") or []),
            "gate": None,
            "up": None,
            "examples": row.get("n_ex"),
            "mean_effect": _num(row.get("mean_w")),
            "description": clean_group_description(g),
        }


# --------------------------------------------------------------------------- reading from the mirror


def _meta(atlas: Atlas, did: str, remote: bool | None = False) -> dict[str, Any] | None:
    relpath = neuron_path("circuit_meta", did)
    if remote is False and atlas.source.store.status(relpath) != PRESENT:
        return None
    try:
        data = atlas.source.get_json(relpath, remote=remote)
    except AtlasError:
        return None
    return data if isinstance(data, dict) else None


def _subcircuit_edges(atlas: Atlas, did: str) -> Iterator[Row]:
    """Edges from the mirrored subcircuit files of a neuron's poles (when it has no circuit file at hand)."""
    from .tables import subcircuit_link_rows

    for p in atlas.catalog.poles_of(did):
        relpath = pole_path("subcircuit", p.pid)
        if atlas.source.store.status(relpath) != PRESENT:
            continue
        sub = atlas.source.get_json(relpath, remote=False)
        if not isinstance(sub, dict):
            continue
        best: dict[tuple[str, str, str], Row] = {}
        for row in subcircuit_link_rows(p.pid, sub):
            key = (row["unit"], row["lane"], row["role"])
            if key not in best or abs(row["weight"] or 0) > abs(best[key]["weight"] or 0):
                best[key] = row
        for row in best.values():
            weight = row["weight"]
            if row["direction"] == "in":
                signed = weight if row["role"] == "driver" else (-weight if weight is not None else None)
                yield {
                    "kind": "input", "source": row["unit"], "source_pole": None, "target": did, "target_pole": p.pole,
                    "group": row["subcircuit"], "group_name": "", "role": "drives" if row["role"] == "driver" else "opposes",
                    "effect": None, "gate": signed if row["lane"] == "gate" else None,
                    "up": signed if row["lane"] == "up" else None, "weight": None, "layer_distance": None,
                    "examples_active": None, "label": row["unit_label"] or "",
                }  # fmt: skip
            else:
                yield {
                    "kind": "reader", "source": did, "source_pole": p.pole, "target": row["unit"], "target_pole": None,
                    "group": row["subcircuit"], "group_name": "", "role": None,
                    "effect": "raised" if (weight or 0) >= 0 else "lowered", "gate": None, "up": None,
                    "weight": abs(weight) if weight is not None else None, "layer_distance": None,
                    "examples_active": None, "label": row["unit_label"] or "",
                }  # fmt: skip


def hub_edges(atlas: Atlas, did: str, remote: bool | None = False) -> tuple[list[Row], str]:
    """Edges of one hub and where they came from (``circuit`` / ``subcircuit`` / ``none``)."""
    meta = _meta(atlas, did, remote)
    if meta is not None:
        return list(circuit_edges(meta)), "circuit"
    edges = list(_subcircuit_edges(atlas, did))
    return edges, "subcircuit" if edges else "none"


def _layer_of(did: str) -> int | None:
    m = re.match(r"^L(\d+)_", did)
    return int(m.group(1)) if m else None


def _display_edges(edges: Iterable[Row]) -> Iterator[Row]:
    """Split stored edges into drawable ones: one per lane for inputs, one per reader."""
    for e in edges:
        base = {
            "source": e["source"], "target": e["target"], "source_pole": e.get("source_pole"),
            "target_pole": e.get("target_pole"), "group": e.get("group_name") or e.get("group"), "role": e.get("role"),
        }  # fmt: skip
        if e["kind"] == "input":
            for lane in ("gate", "up"):
                value = e.get(lane)
                if value:
                    yield {**base, "kind": lane, "weight": abs(value), "sign": "+" if value > 0 else "-"}
        else:
            weight = e.get("weight") or 0.0
            yield {**base, "kind": "reader", "weight": weight, "sign": "-" if e.get("effect") == "lowered" else "+"}


def neighbourhood(atlas: Atlas, did: str, depth: int = 1, max_nodes: int = 300) -> dict[str, Any]:
    """The circuit neighbourhood of a neuron as nodes and edges.

    Step one is the hub's own circuit (fetched once if the server may fetch).
    Further steps only pass through neighbours that are atlas neurons whose
    circuit files are already mirrored - nothing else is known about the rest.
    """
    notes: list[str] = []
    catalog = atlas.catalog
    labels: dict[str, str] = {}
    distance: dict[str, int] = {did: 0}
    collected: list[Row] = []
    frontier = [did]
    for step in range(1, max(1, depth) + 1):
        nxt: list[str] = []
        for hub in frontier:
            edges, origin = hub_edges(atlas, hub, remote=None if step == 1 else False)
            if origin == "none":
                if step == 1:
                    notes.append(f"No circuit data for {hub} is available (not mirrored, or it has none).")
                continue
            if origin == "subcircuit" and step == 1:
                notes.append("Built from the subcircuit files (the neuron's circuit file is not mirrored): top units only.")
            for e in edges:
                other = e["source"] if e["target"] == hub else e["target"]
                if e.get("label"):
                    labels.setdefault(other, e["label"])
                if other not in distance:
                    distance[other] = step
                    if other in catalog.neurons:
                        nxt.append(other)
            collected.extend(_display_edges(edges))
        frontier = nxt
        if not frontier:
            break

    # Keep the strongest edges until the node budget is used up.
    collected.sort(key=lambda e: -(e["weight"] or 0.0))
    keep_nodes: dict[str, None] = {did: None}
    edges_out: list[Row] = []
    dropped = 0
    for e in collected:
        new = [n for n in (e["source"], e["target"]) if n not in keep_nodes]
        if len(keep_nodes) + len(new) > max_nodes:
            dropped += 1
            continue
        keep_nodes.update(dict.fromkeys(new))
        edges_out.append({**e, "weight": round(e["weight"], 6)})
    if dropped:
        notes.append(f"{dropped:,} weaker edges were left out to stay within {max_nodes} nodes.")

    nodes = []
    for node in keep_nodes:
        neuron = catalog.neurons.get(node)
        poles = catalog.poles_of(node)
        label = labels.get(node, "")
        if neuron is not None:
            label = next((p.headline or p.description for p in poles if p.headline or p.description), "") or neuron.circuit_label or label
        nodes.append(
            {
                "id": node,
                "layer": neuron.layer if neuron else _layer_of(node),
                "label": label if len(label) <= 300 else label[:300] + " …",
                "in_atlas": bool(poles),
                "mirrored": atlas.source.store.status(neuron_path("circuit_meta", node)) == PRESENT,
                "distance": distance.get(node, 0),
            }
        )
    return {"center": did, "nodes": nodes, "edges": edges_out, "notes": notes}


# --------------------------------------------------------------------------- whole-region graph files


def region_edges(atlas: Atlas, dids: Iterable[str] | None = None) -> Iterator[Row]:
    """Edges of every mirrored hub in ``dids`` (default: all neurons of the catalog)."""
    for did in dids if dids is not None else atlas.catalog.neurons:
        meta = _meta(atlas, did, remote=False)
        if meta is not None:
            for e in circuit_edges(meta):
                yield {"hub": did, **e}


def write_graph(atlas: Atlas, path: Path | str, dids: Iterable[str] | None = None) -> dict[str, int]:
    """Write the graph of the mirrored region as ``.csv`` (edge list), ``.json`` or ``.graphml``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".json", ".graphml"):
        raise ValueError("the graph file must end in .csv, .json or .graphml")
    catalog = atlas.catalog
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[Row] = []
    for e in region_edges(atlas, dids):
        edges.append(e)
        for end in ("source", "target"):
            node = e[end]
            if node not in nodes:
                neuron = catalog.neurons.get(node)
                nodes[node] = {
                    "id": node,
                    "layer": neuron.layer if neuron else _layer_of(node),
                    "in_atlas": bool(catalog.poles_of(node)),
                    "hub": False,
                    "label": "",
                }
            if node == e["hub"]:
                nodes[node]["hub"] = True
            elif e.get("label") and not nodes[node]["label"]:
                nodes[node]["label"] = e["label"]
    for node, info in nodes.items():
        if info["in_atlas"]:
            poles = catalog.poles_of(node)
            info["label"] = next((p.headline or p.description for p in poles if p.headline or p.description), info["label"])

    columns = ["hub", "kind", "source", "source_pole", "target", "target_pole", "group", "group_name", "role",
               "effect", "gate", "up", "weight", "layer_distance", "examples_active"]  # fmt: skip
    if suffix == ".csv":
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for e in edges:
                writer.writerow({k: ("" if e.get(k) is None else e.get(k)) for k in columns})
        nodes_path = path.with_name(path.stem + "_nodes.csv")
        with open(nodes_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", "layer", "in_atlas", "hub", "label"])
            writer.writeheader()
            writer.writerows(nodes.values())
    elif suffix == ".json":
        payload = {"nodes": list(nodes.values()), "edges": [{k: e.get(k) for k in columns} for e in edges]}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    else:
        _write_graphml(path, nodes, edges)
    return {"nodes": len(nodes), "edges": len(edges)}


def _write_graphml(path: Path, nodes: dict[str, dict[str, Any]], edges: list[Row]) -> None:
    node_keys = [("layer", "int"), ("in_atlas", "boolean"), ("hub", "boolean"), ("label", "string")]
    edge_keys = [("kind", "string"), ("source_pole", "string"), ("target_pole", "string"), ("group_name", "string"),
                 ("role", "string"), ("effect", "string"), ("gate", "double"), ("up", "double"), ("weight", "double")]  # fmt: skip

    def clean(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value)
        return escape("".join(ch for ch in text if ch in "\t\n" or ord(ch) >= 32))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n')
        for name, kind in node_keys:
            fh.write(f'  <key id="n_{name}" for="node" attr.name="{name}" attr.type="{kind}"/>\n')
        for name, kind in edge_keys:
            fh.write(f'  <key id="e_{name}" for="edge" attr.name="{name}" attr.type="{kind}"/>\n')
        fh.write('  <graph id="neuron-atlas" edgedefault="directed">\n')
        for info in nodes.values():
            fh.write(f"    <node id={quoteattr(info['id'])}>")
            for name, _ in node_keys:
                if info.get(name) not in (None, ""):
                    fh.write(f'<data key="n_{name}">{clean(info[name])}</data>')
            fh.write("</node>\n")
        for i, e in enumerate(edges):
            fh.write(f'    <edge id="e{i}" source={quoteattr(e["source"])} target={quoteattr(e["target"])}>')
            for name, _ in edge_keys:
                if e.get(name) not in (None, ""):
                    fh.write(f'<data key="e_{name}">{clean(e[name])}</data>')
            fh.write("</edge>\n")
        fh.write("  </graph>\n</graphml>\n")


# --------------------------------------------------------------------------- hubs that share inputs


def shared_input_similarity(
    atlas: Atlas, dids: Iterable[str] | None = None, *, top: int = 10, lane: str = "both"
) -> list[Row]:
    """How similar are hubs by the earlier neurons that feed them?

    Every hub pole becomes a sparse vector over (source neuron, source pole,
    lane) with the signed contribution as value; the result lists, for each
    hub pole, the ``top`` other hub poles by cosine similarity.  Poles that
    are fed by the same neurons in the same direction come out close - a
    geometry of the atlas that does not depend on any text.
    """
    import numpy as np
    from scipy import sparse

    if lane not in ("both", "gate", "up"):
        raise ValueError("lane must be both, gate or up")
    rows: list[str] = []
    row_of: dict[str, int] = {}
    col_of: dict[tuple[str, str | None, str], int] = {}
    data: dict[tuple[int, int], float] = defaultdict(float)
    for e in region_edges(atlas, dids):
        if e["kind"] != "input" or not e.get("target_pole"):
            continue
        pid = f"{e['target']}__{e['target_pole']}"
        for name in ("gate", "up"):
            if lane not in ("both", name) or not e.get(name):
                continue
            r = row_of.setdefault(pid, len(row_of))
            if r == len(rows):
                rows.append(pid)
            c = col_of.setdefault((e["source"], e.get("source_pole"), name), len(col_of))
            data[(r, c)] += float(e[name])
    if len(rows) < 2:
        return []
    keys = list(data)
    matrix = sparse.csr_matrix(
        (list(data.values()), ([k[0] for k in keys], [k[1] for k in keys])), shape=(len(rows), len(col_of)), dtype=np.float32
    )
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0
    unit = sparse.diags(1.0 / norms) @ matrix
    out: list[Row] = []
    for start in range(0, len(rows), 512):
        block = (unit[start : start + 512] @ unit.T).toarray()
        for i, scores in enumerate(block, start):
            scores[i] = -2.0
            for j in np.argsort(-scores)[:top]:
                if scores[j] <= 0:
                    break
                out.append({"pole": rows[i], "other": rows[int(j)], "similarity": round(float(scores[j]), 4)})
    return out
