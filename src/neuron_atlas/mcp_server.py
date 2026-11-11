"""The atlas as MCP tools, so that chatbots can search and read it.

Two ways to run it:

* ``neuron-atlas mcp``            - stdio, for desktop clients (Claude Desktop, Claude Code, …)
* mounted at ``/mcp`` by the API  - streamable HTTP, for remote clients

Tool results are written for a language model: compact, with the firing token
marked «like this», and capped in length (``max_chars``) so that one call never
floods the context window.
"""

from __future__ import annotations

from typing import Any

import anyio

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # SDK 1.x: same class under its old name
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]

from .config import Settings
from .glossary import GLOSSARY
from .models import MirrorRequest, SearchQuery, Selection
from .service import Atlas

INSTRUCTIONS = (
    "Search and read Goodfire's Qwen3.6 Neuron Atlas: what individual neurons of the language model "
    "Qwen3.6-35B-A3B respond to. A neuron (e.g. L30_E47_N449) has a positive and a negative pole "
    "(L30_E47_N449__pos / __neg); a pole fires in one or several modes (meanings). "
    "Typical flow: search_neurons -> get_pole -> find_similar. All descriptions are in English, so search "
    "in English. Call glossary once if a term is unclear. Quote ids exactly and name the pole."
)

_DEFAULT_MAX_CHARS = 14_000


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n\n[… cut at {max_chars} characters; ask for fewer sections or examples, or raise max_chars]"


def _hit(hit: Any) -> dict[str, Any]:
    """A search hit reduced to what a model needs to decide what to open."""
    out: dict[str, Any] = {
        "id": hit.pid,
        "display": hit.display,
        "category": hit.category_words,
        "description": hit.description if len(hit.description) <= 600 else hit.description[:600] + " …",
    }
    if hit.headline:
        out["headline"] = hit.headline
    if hit.n_modes is not None:
        out["modes"] = hit.n_modes
    if hit.simulatability is not None:
        out["simulatability"] = hit.simulatability
    if hit.matches:
        out["matched"] = [
            {k: v for k, v in (("in", m.field), ("mode", m.mode_index), ("share", m.share), ("text", m.text)) if v is not None}
            for m in hit.matches[:3]
        ]
    if hit.atlas_url:
        out["url"] = hit.atlas_url
    return out


def build_server(atlas: Atlas) -> MCPServer:
    """Create the MCP server for an :class:`Atlas`."""
    server = MCPServer("neuron-atlas", instructions=INSTRUCTIONS)
    settings = atlas.settings

    async def run(fn: Any, *args: Any) -> Any:
        return await anyio.to_thread.run_sync(fn, *args)

    @server.tool()
    async def search_neurons(
        query: str,
        limit: int = 10,
        offset: int = 0,
        match: str = "auto",
        group_by: str = "pole",
        sort: str = "relevance",
    ) -> dict[str, Any]:
        """Search the atlas for poles (one sign of a neuron) by what they respond to.

        query: words, "exact phrases", -exclusions, prefix*, OR - or a neuron/pole id.
          Inline filters can be added: layer:30 expert:47 pole:neg cat:mono,fn,poly,dark
          modes:>=3 sim:>0.8 has:overview sample:public   (sample:public = the 3,000 poles on goodfire.com)
        match: auto (words, then substring) | semantic (rank by meaning) | substring | regex
        group_by: pole | neuron (both poles folded) | mode (one hit per matching mode)
        sort: relevance | simulatability | layer | modes | damage | share | random | id
        Returns hits with ids to pass to get_pole, the total count and next_offset for paging.
        """
        limit = max(1, min(int(limit), 50))

        def work() -> dict[str, Any]:
            res = atlas.search(
                SearchQuery(q=query, limit=limit, offset=max(0, offset), match=match, group_by=group_by, sort=sort)  # type: ignore[arg-type]
            )
            out: dict[str, Any] = {"total": res.total, "next_offset": res.next_offset}
            if res.hits:
                out["hits"] = [_hit(h) for h in res.hits]
            if res.neurons:
                out["neurons"] = [
                    {"id": n.did, "display": n.display, "poles": [_hit(p) for p in n.poles]} for n in res.neurons
                ]
            if res.modes:
                out["modes"] = [
                    {"pole": m.pid, "display": m.display, "mode": m.mode_index, "label": m.label, "share": m.share}
                    for m in res.modes
                ]
            if res.notes:
                out["notes"] = res.notes
            return out

        return await run(work)

    @server.tool()
    async def batch_search(queries: list[str], limit: int = 5, match: str = "auto") -> dict[str, Any]:
        """Run several searches at once (same query syntax as search_neurons). Returns one result list per query."""
        limit = max(1, min(int(limit), 25))
        if len(queries) > min(settings.max_batch_size, 50):
            raise ValueError(f"at most {min(settings.max_batch_size, 50)} queries per call")

        def work() -> dict[str, Any]:
            results = atlas.batch_search([SearchQuery(q=q, limit=limit, match=match) for q in queries])  # type: ignore[arg-type]
            return {
                "results": [
                    {"query": q, "total": r.total, "hits": [_hit(h) for h in r.hits]}
                    for q, r in zip(queries, results, strict=True)
                ]
            }

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
        max_examples: examples per mode (0-12). max_chars: length cap of the answer.
        """

        def work() -> str:
            from .render import pole_markdown

            parts = [p.strip() for p in include.split(",") if p.strip()] or ["modes"]
            detail = atlas.pole(id, parts, max_examples=max(0, min(int(max_examples), 12)))
            return _clip(pole_markdown(detail), max_chars)

        return await run(work)

    @server.tool()
    async def get_poles(ids: list[str], include: str = "modes", max_examples: int = 1, max_chars: int = 24_000) -> str:
        """Read several poles at once (Markdown). A neuron id without a pole returns both poles."""

        def work() -> str:
            from .render import pole_markdown

            parts = [p.strip() for p in include.split(",") if p.strip()] or ["modes"]
            res = atlas.poles(ids[:25], parts, max_examples=max(0, min(int(max_examples), 6)))
            text = "\n\n---\n\n".join(pole_markdown(d) for d in res.poles)
            if res.not_found:
                text += "\n\nNot found: " + ", ".join(res.not_found)
            return _clip(text, max_chars)

        return await run(work)

    @server.tool()
    async def get_neuron(id: str, include_circuit: bool = False, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
        """Read one neuron (both poles side by side) as Markdown; optionally with its circuit (inputs and readers)."""

        def work() -> str:
            from .render import neuron_markdown

            return _clip(neuron_markdown(atlas.neuron(id, ("circuit",) if include_circuit else ())), max_chars)

        return await run(work)

    @server.tool()
    async def find_similar(
        pole: str | None = None, mode: int | None = None, text: str | None = None, top: int = 10
    ) -> dict[str, Any]:
        """Poles or modes with a similar meaning.

        Give `pole` (whole pole), `pole` + `mode` (one mode of it), or `text` (free text, English).
        Returns ids with a similarity score (cosine; 1 = same wording).
        """

        def work() -> dict[str, Any]:
            res = atlas.similar(pole=pole, mode=mode, text=text, top=max(1, min(int(top), 50)))
            return {
                "anchor": res.anchor,
                "method": res.method,
                "items": [
                    {
                        k: v
                        for k, v in (
                            ("id", i.pid), ("display", i.display), ("score", i.score), ("mode", i.mode_index),
                            ("label", i.label or None),
                            ("description", (i.description[:300] + " …") if len(i.description) > 300 else i.description),
                        )  # fmt: skip
                        if v is not None
                    }
                    for i in res.items
                ],
                "notes": res.notes,
            }

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
            if not settings.allow_mirror or not atlas.source.can_fetch():
                out["download"] = "not permitted on this server"
            elif len(plan.poles) > settings.max_mirror_poles:
                out["download"] = (
                    f"refused: {len(plan.poles)} poles exceed this server's limit of {settings.max_mirror_poles} "
                    "per job; narrow the region or use the command line (neuron-atlas mirror)"
                )
            else:
                out["download"] = atlas.mirror_in_background(request, label="mcp").as_dict()
            return out

        return await run(work)

    return server


def http_app(server: MCPServer, settings: Settings) -> Any:
    """Starlette app that serves the MCP server at ``/mcp`` (stateless, JSON responses)."""
    kwargs: dict[str, Any] = {"streamable_http_path": "/mcp", "stateless_http": True, "json_response": True}
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        if "*" in settings.allowed_hosts:
            security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        else:
            local = ["localhost", "127.0.0.1", "[::1]"]
            hosts = [h for name in [*local, *settings.allowed_hosts] for h in (name, f"{name}:*")]
            origins = [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
            )
        kwargs["transport_security"] = security
    except ImportError:  # older SDKs have no such setting
        pass
    try:
        return server.streamable_http_app(**kwargs)
    except TypeError:  # SDK 1.x: options live on the server's settings object
        for key in ("streamable_http_path", "stateless_http", "json_response"):
            if hasattr(server.settings, key):
                setattr(server.settings, key, kwargs[key])
        return server.streamable_http_app()


def run_stdio(atlas: Atlas) -> None:
    """Serve over stdin/stdout until the client disconnects."""
    build_server(atlas).run("stdio")
