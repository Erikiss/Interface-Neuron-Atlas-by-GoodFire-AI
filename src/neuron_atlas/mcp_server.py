"""The atlas as MCP tools, so that chatbots can search and read it.

Two ways to run it:

* ``neuron-atlas mcp``            - stdio, for desktop clients (Claude Desktop, Claude Code, …)
* mounted at ``/mcp`` by the API  - streamable HTTP, for remote clients

Tool results are written for a language model: compact, with the firing token
marked «like this», and kept within a length budget (``max_chars``) so that one
call never floods the context window.  A document that is too long is first
shortened where it loses least (fewer example passages, then no preceding
context) and only then cut; every shortening is said at the end of the answer.
Mistakes in a call come back as a readable tool error, so the model can retry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import anyio
from pydantic import ValidationError

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # SDK 1.x: same classes under their old names
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from .config import Settings
from .glossary import GLOSSARY
from .models import Example, MirrorRequest, PoleDetail, SearchQuery, Selection
from .service import Atlas, NotFound
from .upstream import AtlasError

INSTRUCTIONS = (
    "Search and read Goodfire's Qwen3.6 Neuron Atlas: what individual neurons of the language model "
    "Qwen3.6-35B-A3B respond to. A neuron (e.g. L30_E47_N449) has a positive and a negative pole "
    "(L30_E47_N449__pos / __neg); a pole fires in one or several modes (meanings). "
    "Typical flow: search_neurons -> get_pole -> find_similar. All descriptions are in English, so search "
    "in English. Call glossary once if a term is unclear. Quote ids exactly and name the pole. "
    "Answers are kept within max_chars characters; when one says it was shortened, ask for less or raise max_chars."
)

#: Length budget of one answer when the call does not name one (about 3,500 tokens).
_DEFAULT_MAX_CHARS = 14_000
#: Longest answer any tool gives, whatever ``max_chars`` asks for.
_HARD_MAX_CHARS = 120_000
#: Most ids ``get_poles`` reads in one call.
_MAX_IDS = 25
#: Keys whose (long) text values are shortened first when a JSON answer is too long.
_LONG_KEYS = frozenset({"description", "text", "headline"})


# --------------------------------------------------------------------------- budgets and errors


def _budget(max_chars: int, default: int = _DEFAULT_MAX_CHARS) -> int:
    """``max_chars`` as asked; 0 or less means the default, and nothing goes above :data:`_HARD_MAX_CHARS`."""
    value = int(max_chars)
    return default if value <= 0 else min(value, _HARD_MAX_CHARS)


def _message(exc: BaseException) -> str:
    """A short reason for a failed call: what the model needs to correct it, without a traceback."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:4]:
            where = ".".join(str(p) for p in err.get("loc", ()) if p != "__root__") or "value"
            parts.append(f"{where}: {err.get('msg', 'invalid value')}")
        return "invalid argument - " + "; ".join(parts)
    return str(exc) or exc.__class__.__name__


_FENCE = re.compile(r"^(`{3,}|~{3,})")


def _clip(text: str, max_chars: int) -> str:
    """Cut a Markdown document to about ``max_chars`` without leaving a code fence open."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    note = f"\n\n[… cut at {max_chars} characters; ask for fewer sections or examples, or raise max_chars]"
    # Leave room for the note and a closing fence, so the answer stays within the budget (tiny budgets aside).
    room = max_chars - len(note) - 8 if max_chars > 4 * len(note) else max_chars
    cut = text[:room]
    # Prefer to stop at a line end rather than in the middle of a word or a passage line.
    newline = cut.rfind("\n")
    if newline > room * 0.6:
        cut = cut[:newline]
    cut = cut.rstrip()
    fence = None  # the fence that is open at the end of the cut, if any
    for line in cut.split("\n"):
        m = _FENCE.match(line)
        if not m:
            continue
        marker = m.group(1)
        if fence is None:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and line.strip() == marker:
            fence = None
    if fence is not None:
        cut += "\n" + fence
    return cut + note


# --------------------------------------------------------------------------- fitting JSON answers


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _shorten(value: Any, limit: int) -> Any:
    """``value`` with every long text (description, matched text, headline) cut to ``limit`` characters."""
    if isinstance(value, dict):
        return {
            k: _cut(v, limit) if k in _LONG_KEYS and isinstance(v, str) else _shorten(v, limit)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_shorten(v, limit) for v in value]
    return value


def _fit_list(out: dict[str, Any], key: str, budget: int) -> tuple[int, list[str]]:
    """Make ``out`` fit ``budget`` characters by shortening, then dropping trailing entries of ``out[key]``.

    Returns the number of entries kept and notes on what was shortened.
    """
    items = out.get(key) or []
    if not items or _size(out) <= budget:
        return len(items), []
    notes: list[str] = []
    for limit in (300, 120):
        out[key] = _shorten(items, limit)
        if _size(out) <= budget:
            return len(items), [f"long texts cut to {limit} characters to fit max_chars={budget}"]
    notes.append("long texts cut to 120 characters")
    shortened = out[key]
    sizes = [_size(item) + 2 for item in shortened]  # ", " between entries
    room = budget - _size({**out, key: []})
    kept = 0
    while kept < len(shortened) and room - sizes[kept] >= 0:
        room -= sizes[kept]
        kept += 1
    kept = max(1, kept)  # always answer with at least one entry
    out[key] = shortened[:kept]
    return kept, notes


# --------------------------------------------------------------------------- fitting Markdown documents


def _example_lists(detail: PoleDetail) -> list[list[Example]]:
    lists = [m.examples for m in detail.modes] + [s.examples for s in detail.subcircuits or []]
    if detail.islands:
        lists += [i.examples for i in detail.islands.islands]
    return lists


def _with_examples(detail: PoleDetail, keep: int, *, context: bool) -> PoleDetail:
    """A copy of ``detail`` with at most ``keep`` examples per list; ``context=False`` also drops preceding text and tokens."""
    copy = detail.model_copy(deep=True)

    def cut(examples: list[Example]) -> list[Example]:
        kept = examples[:keep]
        return kept if context else [e.model_copy(update={"pre": "", "tokens": None}) for e in kept]

    for mode in copy.modes:
        mode.examples = cut(mode.examples)
    for sub in copy.subcircuits or []:
        sub.examples = cut(sub.examples)
    if copy.islands:
        for island in copy.islands.islands:
            island.examples = cut(island.examples)
    return copy


def _fit_pole(detail: PoleDetail, budget: int, render: Callable[[PoleDetail], str]) -> str:
    """Render ``detail`` within ``budget`` characters, giving up example passages before cutting the document.

    Steps: fewer examples per mode, subcircuit and island (down to one), then
    without the text before each passage and the token values, then without
    examples; only if that is still too long is the document cut.
    """
    text = render(detail)
    if len(text) <= budget:
        return text
    lists = _example_lists(detail)
    most = max((len(x) for x in lists), default=0)
    has_context = any(e.pre or e.tokens for x in lists for e in x)
    steps = [(keep, True) for keep in range(most - 1, 0, -1)]
    if most and has_context:
        steps.append((1, False))
    if most:
        steps.append((0, False))
    for keep, context in steps:
        text = render(_with_examples(detail, keep, context=context))
        what = (
            f"at most {keep} example{'s' if keep != 1 else ''} per mode/subcircuit"
            if keep
            else "no example passages"
        )
        if not context and keep:
            what += ", without the text before each passage and the token values"
        note = f"\n\n[shortened to fit max_chars={budget}: {what}; ask for fewer sections or raise max_chars]"
        if len(text) + len(note) <= budget:
            return text + note
    return _clip(text, budget)


# --------------------------------------------------------------------------- tools


def _hit(hit: Any) -> dict[str, Any]:
    """A search hit reduced to what a model needs to decide what to open."""
    out: dict[str, Any] = {
        "id": hit.pid,
        "display": hit.display,
        "category": hit.category_words,
        "description": _cut(hit.description, 600),
    }
    if hit.headline:
        out["headline"] = hit.headline
    if hit.n_modes is not None:
        out["modes"] = hit.n_modes
    if hit.simulatability is not None:
        out["simulatability"] = hit.simulatability
    if hit.matches:
        out["matched"] = [
            {
                k: v
                for k, v in (("in", m.field), ("mode", m.mode_index), ("share", m.share), ("text", m.text))
                if v is not None
            }
            for m in hit.matches[:3]
        ]
    if hit.atlas_url:
        out["url"] = hit.atlas_url
    return out


def _parts(include: str) -> list[str]:
    return [p.strip() for p in include.split(",") if p.strip()] or ["modes"]


def build_server(atlas: Atlas) -> MCPServer:
    """Create the MCP server for an :class:`Atlas`."""
    from . import __version__

    try:
        server = MCPServer("neuron-atlas", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # SDK 1.x has no version argument
        server = MCPServer("neuron-atlas", instructions=INSTRUCTIONS)
    settings = atlas.settings

    async def run(fn: Any, *args: Any) -> Any:
        """Run blocking work in a thread; the caller's mistakes come back as readable tool errors."""
        try:
            return await anyio.to_thread.run_sync(fn, *args)
        except ToolError:
            raise
        except (NotFound, AtlasError, ValueError) as exc:  # unknown id, bad argument, upstream unavailable
            raise ToolError(_message(exc)) from exc

    @server.tool()
    async def search_neurons(
        query: str,
        limit: int = 10,
        offset: int = 0,
        match: str = "auto",
        group_by: str = "pole",
        sort: str = "relevance",
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        """Search the atlas for poles (one sign of a neuron) by what they respond to.

        query: words, "exact phrases", -exclusions, prefix*, OR (in capitals) - or a neuron/pole id.
          Inline filters (no space after the colon): layer:30 expert:47 pole:neg cat:mono,fn,poly,dark
          modes:>=3 sim:>0.8 has:overview sample:public   (sample:public = the 3,000 poles on goodfire.com)
        match: auto (words, then substring) | semantic (rank by meaning) | substring | regex
        group_by: pole | neuron (both poles folded) | mode (one hit per matching mode)
        sort: relevance | simulatability | layer | modes | damage | share | random | id
        Returns hits with ids to pass to get_pole, the total count and next_offset for paging.
        max_chars: length budget of the answer; long descriptions are cut first, then the page is shortened
          (next_offset then continues right after the last entry returned).
        """
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        budget = _budget(max_chars)

        def work() -> dict[str, Any]:
            res = atlas.search(
                SearchQuery(q=query, limit=limit, offset=offset, match=match, group_by=group_by, sort=sort)  # type: ignore[arg-type]
            )
            out: dict[str, Any] = {"total": res.total, "next_offset": res.next_offset}
            key = None
            if res.hits:
                key, out["hits"] = "hits", [_hit(h) for h in res.hits]
            elif res.neurons:
                key = "neurons"
                out["neurons"] = [
                    {"id": n.did, "display": n.display, "poles": [_hit(p) for p in n.poles]}
                    for n in res.neurons
                ]
            elif res.modes:
                key = "modes"
                out["modes"] = [
                    {
                        "pole": m.pid,
                        "display": m.display,
                        "mode": m.mode_index,
                        "label": m.label,
                        "share": m.share,
                    }
                    for m in res.modes
                ]
            notes = list(res.notes)
            if key:
                shown = len(out[key])
                kept, fit_notes = _fit_list(out, key, budget)
                notes += fit_notes
                if kept < shown:
                    out["next_offset"] = offset + kept
                    notes.append(
                        f"only {kept} of the {shown} entries on this page fit max_chars={budget}; "
                        f"continue with offset={offset + kept}"
                    )
            if notes:
                out["notes"] = notes
            return out

        return await run(work)

    @server.tool()
    async def batch_search(
        queries: list[str], limit: int = 5, match: str = "auto", max_chars: int = _DEFAULT_MAX_CHARS
    ) -> dict[str, Any]:
        """Run several searches at once (same query syntax as search_neurons). Returns one result list per query.

        max_chars: length budget of the whole answer; when it is too small, long texts are cut and then every
        query keeps fewer hits (its total still tells how many there are).
        """
        limit = max(1, min(int(limit), 25))
        most = min(settings.max_batch_size, 50)
        budget = _budget(max_chars)

        def work() -> dict[str, Any]:
            if len(queries) > most:
                raise ValueError(f"at most {most} queries per call; split the list")
            results = atlas.batch_search([SearchQuery(q=q, limit=limit, match=match) for q in queries])  # type: ignore[arg-type]
            out: dict[str, Any] = {
                "results": [
                    {
                        "query": q,
                        "total": r.total,
                        "hits": [_hit(h) for h in r.hits],
                        **({"notes": r.notes} if r.notes else {}),
                    }
                    for q, r in zip(queries, results, strict=True)
                ]
            }
            if _size(out) <= budget:
                return out
            notes: list[str] = []
            for cut in (300, 120):
                out["results"] = _shorten(out["results"], cut)
                notes = [f"long texts cut to {cut} characters to fit max_chars={budget}"]
                if _size(out) <= budget:
                    out["notes"] = notes
                    return out
            per_query = max((len(r["hits"]) for r in out["results"]), default=0)
            full = out["results"]
            while per_query > 1:
                per_query -= 1
                out["results"] = [{**r, "hits": r["hits"][:per_query]} for r in full]
                if _size(out) <= budget:
                    break
            notes.append(f"each query shows at most {per_query} hit(s) to fit max_chars={budget}")
            out["notes"] = notes
            return out

        return await run(work)

    @server.tool()
    async def get_pole(
        id: str,
        include: str = "modes,examples",
        max_examples: int = 2,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> str:
        """Read one pole as a Markdown document.

        id: pole id such as L30_E47_N449__pos (also accepted: "+ L30/E47/N449", "L30/E47/N449 neg").
        include: comma list of
          modes       - every mode with label, share, description
          examples    - example passages per mode, firing token marked «like this»
          subcircuits - the subcircuits behind the modes, with their readings and the neurons feeding them
          overview    - the written explanation: summary, experiments, what it does NOT fire on, open questions
          landscape   - firing and damage shares per mode weighted to real text, strength levels
          circuit     - upstream input groups and downstream readers of the neuron
          islands     - the older island-based read
          tokens      - per-token activations of the example passages
        max_examples: examples per mode (0-12).
        max_chars: length budget; a longer document first gets fewer examples, then no context, then is cut.
        """
        budget = _budget(max_chars)

        def work() -> str:
            from .render import pole_markdown

            detail = atlas.pole(id, _parts(include), max_examples=max(0, min(int(max_examples), 12)))
            return _fit_pole(detail, budget, pole_markdown)

        return await run(work)

    @server.tool()
    async def get_poles(
        ids: list[str], include: str = "modes", max_examples: int = 1, max_chars: int = 24_000
    ) -> str:
        """Read several poles at once (Markdown), at most 25 ids per call.

        A neuron id without a pole returns both poles. max_chars is the budget of the whole answer, shared
        evenly by the poles; each is shortened like get_pole does.
        """
        budget = _budget(max_chars, 24_000)

        def work() -> str:
            from .render import pole_markdown

            res = atlas.poles(ids[:_MAX_IDS], _parts(include), max_examples=max(0, min(int(max_examples), 6)))
            notes = list(res.notes)
            if len(ids) > _MAX_IDS:
                rest = ids[_MAX_IDS:]
                shown = ", ".join(rest[:10]) + (" …" if len(rest) > 10 else "")
                notes.append(f"Not read (more than {_MAX_IDS} ids per call; ask again for these): {shown}")
            if res.not_found:
                notes.append("Not found: " + ", ".join(res.not_found))
            tail = "".join(f"\n\n{note}" for note in notes)
            share = max(1_500, (budget - len(tail)) // max(1, len(res.poles)) - 7)
            text = "\n\n---\n\n".join(_fit_pole(d, share, pole_markdown) for d in res.poles)
            return _clip(text, max(0, budget - len(tail))) + tail if text else tail.strip()

        return await run(work)

    @server.tool()
    async def get_neuron(id: str, include_circuit: bool = False, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
        """Read one neuron (both poles side by side) as Markdown; optionally with its circuit (inputs and readers)."""
        budget = _budget(max_chars)

        def work() -> str:
            from .render import neuron_markdown

            return _clip(neuron_markdown(atlas.neuron(id, ("circuit",) if include_circuit else ())), budget)

        return await run(work)

    @server.tool()
    async def find_similar(
        pole: str | None = None,
        mode: int | None = None,
        text: str | None = None,
        top: int = 10,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        """Poles or modes with a similar meaning.

        Give `pole` (whole pole), `pole` + `mode` (one mode of it), or `text` (free text, English).
        Returns ids with a similarity score (cosine; 1 = same wording).
        """
        budget = _budget(max_chars)

        def work() -> dict[str, Any]:
            res = atlas.similar(pole=pole, mode=mode, text=text, top=max(1, min(int(top), 50)))
            out: dict[str, Any] = {
                "anchor": res.anchor,
                "method": res.method,
                "items": [
                    {
                        k: v
                        for k, v in (
                            ("id", i.pid),
                            ("display", i.display),
                            ("score", i.score),
                            ("mode", i.mode_index),
                            ("label", i.label or None),
                            ("description", _cut(i.description, 300)),
                        )  # fmt: skip
                        if v is not None
                    }
                    for i in res.items
                ],
                "notes": list(res.notes),
            }
            shown = len(out["items"])
            kept, fit_notes = _fit_list(out, "items", budget)
            out["notes"] += fit_notes
            if kept < shown:
                out["notes"].append(f"only the {kept} closest of {shown} fit max_chars={budget}")
            return out

        return await run(work)

    @server.tool()
    async def atlas_info() -> dict[str, Any]:
        """Size and state of the atlas: release, number of neurons/poles/modes, categories, layers, what is mirrored."""
        return await run(atlas.info)

    @server.tool()
    async def glossary() -> dict[str, Any]:
        """Definitions of the atlas vocabulary: pole, mode, subcircuit, threshold, damage, Q3/Q4, simulatability, …"""
        return GLOSSARY

    @server.tool()
    async def plan_region(
        query: str | None = None,
        similar_to_text: str | None = None,
        similar_to_pole: str | None = None,
        ids: list[str] | None = None,
        top: int = 100,
        download: bool = False,
    ) -> dict[str, Any]:
        """Select a region of the atlas and report (or start) the download of its full data.

        A region is the union of: poles found by `query` (search syntax), the `top` poles closest in meaning
        to `similar_to_text` or `similar_to_pole`, and explicit `ids`.
        With download=false (default) only the size is reported. download=true queues the download on the
        server (if it permits that); afterwards the long texts of the region are searchable.
        """

        def work() -> dict[str, Any]:
            selection = Selection(
                query=SearchQuery(q=query) if query else None,
                similar_to_text=similar_to_text,
                similar_to_pole=similar_to_pole,
                ids=ids,
                top=max(1, min(int(top), 2000)),
            )
            request = MirrorRequest(selection=selection)
            plan = atlas.plan_mirror(request)
            out: dict[str, Any] = {"estimate": plan.estimate(), "sample_poles": plan.poles[:15]}
            if not download:
                return out
            if not settings.allow_mirror:
                out["download"] = "not permitted on this server"
            elif not atlas.source.can_fetch(remote=True):
                out["download"] = "not possible: this server runs offline"
            elif plan.size > settings.max_mirror_poles:
                out["download"] = (
                    f"refused: {plan.size} poles and neurons exceed this server's limit of {settings.max_mirror_poles} "
                    "per job; narrow the region or use the command line (neuron-atlas mirror)"
                )
            else:
                try:
                    out["download"] = atlas.mirror_in_background(request, label="mcp", plan=plan).as_dict()
                except ValueError as exc:  # the job queue is full
                    out["download"] = f"refused: {exc}"
            return out

        return await run(work)

    return server


def http_app(server: MCPServer, settings: Settings) -> Any:
    """Starlette app that serves the MCP server at its root (the API mounts it at ``/mcp``).

    Stateless, with JSON responses.  The SDK's own DNS-rebinding protection is
    configured with the same host names as the API (plus the CORS origins), so
    the two never disagree about who may call.
    """
    kwargs: dict[str, Any] = {"streamable_http_path": "/", "stateless_http": True, "json_response": True}
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        if "*" in settings.allowed_hosts:
            security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        else:
            from .gate import LOCAL_HOSTS, host_name

            names = {host_name(h) for h in (*LOCAL_HOSTS, *settings.allowed_hosts) if h}
            names = {f"[{n}]" if ":" in n else n for n in names}  # IPv6 literals appear bracketed in Host
            hosts = [h for name in sorted(names) for h in (name, f"{name}:*")]
            origins = [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]
            origins += [o.rstrip("/") for o in settings.cors_origins if o and o != "*"]
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
            )
        kwargs["transport_security"] = security
    except ImportError:  # older SDKs have no such setting
        pass
    try:
        return server.streamable_http_app(**kwargs)
    except TypeError:  # SDK 1.x: options live on the server's settings object
        for key, value in kwargs.items():
            if hasattr(server.settings, key):
                setattr(server.settings, key, value)
        return server.streamable_http_app()


def run_stdio(atlas: Atlas) -> None:
    """Serve over stdin/stdout until the client disconnects."""
    build_server(atlas).run("stdio")
