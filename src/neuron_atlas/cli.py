"""Command line: ``neuron-atlas`` (alias ``atlas``).

Every command is a thin layer over :class:`neuron_atlas.service.Atlas`, the
same object the HTTP API and the MCP server use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from .config import Settings
from .models import MirrorRequest, SearchQuery, Selection
from .service import Atlas, NotFound
from .upstream import AtlasError

app = typer.Typer(
    name="neuron-atlas",
    help="Search, read, export and mirror Goodfire's Qwen3.6 Neuron Atlas.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
geometry_app = typer.Typer(help="Tables, maps and graphs for analysing regions of the atlas.", no_args_is_help=True)
app.add_typer(geometry_app, name="geometry")

out = Console()
err = Console(stderr=True)

_state: dict[str, Any] = {}

POLITENESS = (
    "Downloads are rate limited and resumable. The atlas data belongs to Goodfire AI and is subject to "
    "Goodfire's terms of use (https://www.goodfire.com/legal/websitetos): keep mirrors for personal, "
    "non-commercial research and ask Goodfire before redistributing anything."
)


@app.callback()
def main(
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Where the mirror and the indices live.", envvar="ATLAS_DATA_DIR")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Never touch the network; use only what is mirrored.")] = False,
    release_url: Annotated[str | None, typer.Option("--release-url", help="Pin a release instead of discovering the current one.")] = None,
    rps: Annotated[float | None, typer.Option("--rps", help="Upstream requests per second (politeness).")] = None,
    concurrency: Annotated[int | None, typer.Option("--concurrency", help="Upstream requests in flight at once.")] = None,
) -> None:
    settings = Settings.from_env().with_overrides(
        data_dir=data_dir,
        release_url=release_url,
        requests_per_second=rps,
        max_concurrency=concurrency,
        offline=True if offline else None,
    )
    _state["settings"] = settings


def _atlas() -> Atlas:
    return Atlas(_state.get("settings") or Settings.from_env())


def _fail(message: str, code: int = 1) -> typer.Exit:
    err.print(f"[red]error:[/red] {message}")
    return typer.Exit(code)


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        err.print(f"wrote {output} ({len(text.encode('utf-8')):,} bytes)")


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _query(
    q: str,
    *,
    limit: int = 25,
    offset: int = 0,
    layer: str | None = None,
    cat: str | None = None,
    pole: str | None = None,
    sample: str = "all",
    match: str = "auto",
    sort: str = "relevance",
    group_by: str = "pole",
    scope: str | None = None,
) -> SearchQuery:
    data: dict[str, Any] = {
        "q": q, "limit": limit, "offset": offset, "pole": pole, "sample": sample, "match": match, "sort": sort,
        "group_by": group_by, "categories": _csv(cat),
        "layers": [int(x) for x in _csv(layer) or []] or None,
    }  # fmt: skip
    if scope:
        data["scope"] = _csv(scope)
    return SearchQuery.model_validate({k: v for k, v in data.items() if v is not None})


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.0f}%"


# --------------------------------------------------------------------------- info / search / show


@app.command()
def info(as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False) -> None:
    """What is in the atlas and what is mirrored locally."""
    with _atlas() as atlas:
        data = atlas.info()
    if as_json:
        out.print_json(json.dumps(data))
        return
    a = data["atlas"]
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("release", data["release"]["url"])
    table.add_row("model", a["model"])
    table.add_row("neurons", f"{a['neurons']:,} ({a['neurons_in_atlas_index']:,} in the atlas index, {a['neurons_with_circuit']:,} with circuit data)")
    table.add_row("poles", f"{a['poles']:,} ({a['poles_in_public_sample']:,} listed on goodfire.com)")
    table.add_row("modes", f"{a['modes']:,}")
    table.add_row("categories", ", ".join(f"{k} {v:,}" for k, v in a["categories"].items()))
    table.add_row("layers", ", ".join(f"L{k} {v:,}" for k, v in a["layers"].items()))
    m = data["mirror"]
    table.add_row("mirror", f"{m['files']:,} files, {m['stored_bytes'] / 1e6:,.1f} MB on disk ({m['raw_bytes'] / 1e6:,.1f} MB uncompressed)")
    s = data["search_index"]
    table.add_row("search index", f"{s['label_texts']:,} label texts, {s['deep_texts']:,} deep texts, {s['example_texts']:,} example texts ({s['poles_with_deep_texts']:,} poles)")
    out.print(table)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help='Words, "phrases", -exclusions, prefix*, OR, an id, inline filters (layer:30 cat:fn …).')] = "",
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    offset: int = 0,
    layer: Annotated[str | None, typer.Option(help="Comma list of layers.")] = None,
    cat: Annotated[str | None, typer.Option(help="Comma list of mono,fn,poly,dark,none.")] = None,
    pole: Annotated[str | None, typer.Option(help="pos or neg.")] = None,
    sample: Annotated[str, typer.Option(help="all | public | hidden")] = "all",
    match: Annotated[str, typer.Option(help="auto | words | substring | regex | semantic")] = "auto",
    sort: Annotated[str, typer.Option(help="relevance | simulatability | layer | modes | damage | share | random | id")] = "relevance",
    group_by: Annotated[str, typer.Option("--group-by", help="pole | neuron | mode")] = "pole",
    scope: Annotated[str | None, typer.Option(help="Comma list of labels,deep,examples.")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="table | json | md")] = "table",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Search the atlas."""
    try:
        q = _query(query, limit=limit, offset=offset, layer=layer, cat=cat, pole=pole, sample=sample, match=match, sort=sort, group_by=group_by, scope=scope)
        with _atlas() as atlas:
            res = atlas.search(q)
    except (ValueError, AtlasError) as exc:
        raise _fail(str(exc)) from None
    if fmt == "json":
        _write(res.model_dump_json(indent=2, exclude_none=True), output)
        return
    if fmt == "md":
        from .render import search_markdown

        _write(search_markdown(res), output)
        return
    table = Table(title=f"{res.total:,} hits · {res.took_ms} ms", title_justify="left", show_lines=False)
    if res.modes:
        for col in ("pole", "mode", "share", "label"):
            table.add_column(col)
        for m in res.modes:
            table.add_row(m.display, str(m.mode_index), _pct(m.share), m.label)
    else:
        for col in ("pole", "cat", "modes", "what it fires on"):
            table.add_column(col, overflow="fold")
        hits = res.hits or [p for n in res.neurons for p in n.poles]
        for h in hits:
            best = h.matches[0] if h.matches else None
            text = h.headline or h.description
            if best and best.field in ("mode", "mode_description", "subcircuit", "example", "island", "overview"):
                where = f"mode {best.mode_index}" if best.mode_index is not None else best.field
                share = f" · {_pct(best.share)}" if best.share is not None else ""
                text = f"[dim]{where}{share}:[/dim] {best.text}\n[dim]{text[:160]}[/dim]"
            table.add_row(h.display, h.category, "" if h.n_modes is None else str(h.n_modes), text)
    out.print(table)
    for note in res.notes:
        err.print(f"[yellow]note:[/yellow] {note}")
    if res.next_offset is not None:
        err.print(f"[dim]more: --offset {res.next_offset}[/dim]")


@app.command()
def show(
    ref: Annotated[str, typer.Argument(help='Pole id, e.g. L30_E47_N449__pos or "+ L30/E47/N449".')],
    include: Annotated[str, typer.Option(help="Comma list: modes,examples,subcircuits,overview,landscape,circuit,islands,tokens,research - or all.")] = "modes,examples",
    examples: Annotated[int, typer.Option("--examples", "-e", help="Examples per mode / subcircuit / island.")] = 3,
    fmt: Annotated[str, typer.Option("--format", "-f", help="md | txt | html | json")] = "md",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Print one pole as Markdown, text, print-ready HTML or JSON."""
    from . import render
    from .details import ALL_INCLUDES

    parts = [i for i in ALL_INCLUDES if i not in ("tokens", "research")] if include.strip() == "all" else (_csv(include) or [])
    if fmt == "html" and "tokens" not in parts:
        parts.append("tokens")  # the print page shades tokens
    try:
        with _atlas() as atlas:
            detail = atlas.pole(ref, parts, max_examples=examples)
    except NotFound as exc:
        raise _fail(str(exc), 2) from None
    except (ValueError, AtlasError) as exc:
        raise _fail(str(exc)) from None
    if fmt == "json":
        _write(detail.model_dump_json(indent=2, exclude_none=True), output)
    elif fmt in ("md", "markdown"):
        _write(render.pole_markdown(detail), output)
    elif fmt in ("txt", "text"):
        _write(render.pole_text(detail), output)
    elif fmt == "html":
        _write(render.pole_html(detail), output)
    else:
        raise _fail("format must be one of md, txt, html, json")


@app.command()
def neuron(
    ref: Annotated[str, typer.Argument(help="Neuron id, e.g. L30_E47_N449.")],
    circuit: Annotated[bool, typer.Option("--circuit", help="Include inputs and readers.")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Print one neuron (both poles)."""
    from .render import neuron_markdown

    try:
        with _atlas() as atlas:
            detail = atlas.neuron(ref, ("circuit",) if circuit else ())
    except NotFound as exc:
        raise _fail(str(exc), 2) from None
    _write(detail.model_dump_json(indent=2, exclude_none=True) if as_json else neuron_markdown(detail), output)


@app.command()
def similar(
    pole: Annotated[str | None, typer.Option(help="Pole to start from.")] = None,
    mode: Annotated[int | None, typer.Option(help="Mode index of that pole.")] = None,
    text: Annotated[str | None, typer.Option(help="Or: free text (English).")] = None,
    top: int = 20,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Poles or modes with a similar meaning."""
    try:
        with _atlas() as atlas:
            res = atlas.similar(pole=pole, mode=mode, text=text, top=top)
    except (ValueError, NotFound) as exc:
        raise _fail(str(exc)) from None
    if as_json:
        out.print_json(res.model_dump_json(exclude_none=True))
        return
    table = Table(title=f"similar to {res.anchor} ({res.method})", title_justify="left")
    for col in ("score", "pole", "mode", "text"):
        table.add_column(col, overflow="fold")
    for i in res.items:
        table.add_row(f"{i.score:.3f}", i.display, "" if i.mode_index is None else str(i.mode_index), i.label or i.description[:200])
    out.print(table)
    for note in res.notes:
        err.print(f"[yellow]note:[/yellow] {note}")


@app.command()
def export(
    query: Annotated[str, typer.Argument(help="Search query (same syntax as `search`); empty = everything.")] = "",
    fmt: Annotated[str, typer.Option("--format", "-f", help="ndjson | csv | md")] = "ndjson",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="File to write (default: stdout).")] = None,
    include: Annotated[str | None, typer.Option(help="Detail sections to add for mirrored poles, e.g. modes,examples.")] = None,
    examples: Annotated[int, typer.Option("--examples", "-e")] = 2,
    layer: str | None = None,
    cat: str | None = None,
    pole: str | None = None,
    sample: str = "all",
    match: str = "auto",
) -> None:
    """Write every hit of a search to a file (NDJSON, CSV or Markdown)."""
    from . import render

    if fmt not in ("ndjson", "csv", "md"):
        raise _fail("format must be one of ndjson, csv, md")
    parts = _csv(include) or []
    q = _query(query, layer=layer, cat=cat, pole=pole, sample=sample, match=match)
    handle = sys.stdout if output is None else open(output, "w", encoding="utf-8", newline="")  # noqa: SIM115
    count = 0
    try:
        with _atlas() as atlas:
            if fmt == "csv":
                handle.write(render.csv_header())
            for hit in atlas.iter_search(q):
                detail = None
                if parts:
                    try:
                        detail = atlas.details.pole(hit.pid, parts, max_examples=examples, remote=False)
                    except Exception:  # noqa: BLE001 - not mirrored: export the summary
                        detail = None
                if fmt == "ndjson":
                    obj = detail.model_dump(exclude_none=True) if detail else hit.model_dump(exclude_none=True)
                    handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
                elif fmt == "csv":
                    handle.write(render.csv_row(hit))
                else:
                    handle.write((render.pole_markdown(detail, heading=2) if detail else render.hit_markdown(hit)) + "\n\n")
                count += 1
    finally:
        if output is not None:
            handle.close()
    err.print(f"exported {count:,} poles" + (f" to {output}" if output else ""))


# --------------------------------------------------------------------------- mirror / index


@app.command()
def mirror(
    everything: Annotated[bool, typer.Option("--all", help="Every pole and every circuit of the release (about 32,000 files, 5 GB).")] = False,
    public_sample: Annotated[bool, typer.Option("--public-sample", help="The 3,000 poles goodfire.com lists.")] = False,
    query: Annotated[str | None, typer.Option("--query", "-q", help="All poles this search finds.")] = None,
    similar_to: Annotated[str | None, typer.Option("--similar-to", help="Poles closest in meaning to this text.")] = None,
    like: Annotated[str | None, typer.Option("--like", help="Poles closest in meaning to this pole.")] = None,
    ids: Annotated[str | None, typer.Option("--ids", help="Comma list of neuron or pole ids.")] = None,
    top: Annotated[int, typer.Option(help="How many poles --similar-to / --like take.")] = 200,
    neighbours: Annotated[int, typer.Option(help="Also follow circuit links this many steps (uses mirrored circuit files).")] = 0,
    sources: Annotated[str, typer.Option(help="Comma list of subcircuit,overview,landscape,islands,circuit.")] = "subcircuit,overview,landscape,islands,circuit",
    max_poles: Annotated[int | None, typer.Option(help="Cap the selection.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only show what would be downloaded.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before large downloads.")] = False,
    no_index: Annotated[bool, typer.Option("--no-index", help="Do not make the fetched texts searchable.")] = False,
    index_examples: Annotated[bool, typer.Option("--index-examples", help="Also index example passages (bigger index).")] = False,
) -> None:
    """Download a region of the atlas (or all of it) into the local mirror. Resumable."""
    selection = Selection(
        all=everything,
        public_sample=public_sample,
        query=SearchQuery(q=query) if query is not None else None,
        similar_to_text=similar_to,
        similar_to_pole=like,
        ids=_csv(ids),
        top=top,
        neighbours=neighbours,
        max_poles=max_poles,
    )
    if not any([everything, public_sample, query is not None, similar_to, like, ids]):
        raise _fail("choose what to mirror: --all, --public-sample, --query, --similar-to, --like or --ids")
    request = MirrorRequest(selection=selection, sources=_csv(sources) or [], index=not no_index, index_examples=index_examples, dry_run=dry_run)  # type: ignore[arg-type]
    with _atlas() as atlas:
        try:
            plan = atlas.plan_mirror(request)
        except (ValueError, NotFound, AtlasError) as exc:
            raise _fail(str(exc)) from None
        est = plan.estimate()
        extra = f" (+{est['circuit_only_neurons']:,} neurons known only by their circuit)" if est["circuit_only_neurons"] else ""
        out.print(
            f"[bold]{est['poles']:,}[/bold] poles of [bold]{est['neurons']:,}[/bold] neurons{extra} · "
            f"{est['files_total']:,} files, {est['files_present']:,} already mirrored, "
            f"[bold]{est['files_to_fetch']:,}[/bold] to fetch "
            f"(about {est['estimated_download_bytes'] / 1e6:,.0f} MB download, "
            f"{est['estimated_uncompressed_bytes'] / 1e6:,.0f} MB of JSON)"
        )
        if est["to_fetch_by_kind"]:
            out.print("[dim]" + ", ".join(f"{k} {v:,}" for k, v in est["to_fetch_by_kind"].items()) + "[/dim]")
        if dry_run:
            return
        if est["files_to_fetch"] == 0:
            out.print("nothing to download")
        else:
            rate = atlas.settings.requests_per_second
            seconds = est["files_to_fetch"] / max(rate, 0.1)
            duration = f"{seconds / 60:,.0f} min" if seconds >= 90 else f"{max(1, round(seconds))} s"
            err.print(f"[dim]{POLITENESS}[/dim]")
            err.print(f"[dim]at {rate:g} requests/s this takes at least {duration}[/dim]")
            if est["files_to_fetch"] > 500 and not yes and not typer.confirm("Start the download?", default=False, err=True):
                raise typer.Exit(1)
            progress = Progress(
                TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
                console=err,
            )  # fmt: skip
            with progress:
                task = progress.add_task("fetching", total=est["files_to_fetch"])
                try:
                    report = _run_plan(
                        atlas, plan,
                        lambda rep, _res: progress.update(task, completed=rep.fetched + rep.skipped + rep.absent + rep.failed + rep.not_attempted),
                    )  # fmt: skip
                except KeyboardInterrupt:
                    err.print("\ninterrupted - run the same command again to continue")
                    raise typer.Exit(130) from None
            out.print(
                f"fetched {report.fetched:,} files ({report.bytes_fetched / 1e6:,.1f} MB of JSON) in {report.seconds:,.0f} s"
                + (f", {report.absent:,} not present upstream" if report.absent else "")
                + (f", [red]{report.failed:,} failed[/red]" if report.failed else "")
            )
            for line in report.errors[:5]:
                err.print(f"[red]{line}[/red]")
            if report.stopped:
                err.print(
                    f"[red]stopped early: {report.stopped}[/red]\n"
                    f"{report.not_attempted:,} files were not tried; run the same command again later to continue"
                )
            if report.nothing_worked:
                raise typer.Exit(1)
        if request.index:
            added = _index(atlas, plan.poles, index_examples)
            out.print(f"indexed {added:,} texts for search")


def _run_plan(atlas: Atlas, plan: Any, on_progress: Any) -> Any:
    from .mirror import run_plan

    return run_plan(plan, atlas.source, on_progress=on_progress)


def _index(atlas: Atlas, pids: list[str] | None, examples: bool) -> int:
    total = len(pids) if pids is not None else len(atlas.catalog.poles)
    progress = Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), console=err)
    with progress:
        task = progress.add_task("indexing", total=total)
        return atlas.index_mirrored(pids, examples=examples, on_progress=lambda done, _n: progress.update(task, completed=done))


@app.command()
def index(
    examples: Annotated[bool, typer.Option("--examples", help="Also index example passages.")] = False,
) -> None:
    """Make the texts of all mirrored files searchable (scope 'deep', optionally 'examples')."""
    with _atlas() as atlas:
        added = _index(atlas, None, examples)
        out.print(f"indexed {added:,} new texts · {atlas.search_index.stats()}")


# --------------------------------------------------------------------------- servers


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind; 0.0.0.0 makes the server reachable from other machines.")] = "127.0.0.1",
    port: int = 8000,
    reload: Annotated[bool, typer.Option(help="Restart on code changes (development).")] = False,
    log_level: str = "info",
) -> None:
    """Run the web page, the HTTP API and the MCP endpoint (one process; download jobs and limits live in it)."""
    import os

    import uvicorn

    settings: Settings = _state.get("settings") or Settings.from_env()
    # The server builds its Atlas from the environment, so hand the command-line options on that way.
    os.environ["ATLAS_DATA_DIR"] = str(settings.data_dir)
    if settings.release_url:
        os.environ["ATLAS_RELEASE_URL"] = settings.release_url
    if settings.offline:
        os.environ["ATLAS_OFFLINE"] = "1"
    os.environ["ATLAS_REQUESTS_PER_SECOND"] = str(settings.requests_per_second)
    os.environ["ATLAS_MAX_CONCURRENCY"] = str(settings.max_concurrency)
    if host not in ("127.0.0.1", "localhost", "::1"):
        if not settings.allowed_hosts:
            err.print(
                "[yellow]note:[/yellow] the server answers only to the names localhost, 127.0.0.1 and ::1. "
                "To reach it under another name or address, list that name: "
                "ATLAS_ALLOWED_HOSTS=atlas.example.org (or * for any; not advisable without an API key)."
            )
        if not settings.api_keys:
            err.print(
                "[yellow]warning:[/yellow] the server is reachable from other machines and has no API key "
                "(set ATLAS_API_KEYS). Re-serving the atlas publicly needs Goodfire's permission."
            )
    err.print(f"Neuron Atlas Interface on http://{host}:{port}  (API docs: /docs, MCP: /mcp)")
    uvicorn.run("neuron_atlas.api:create_app", factory=True, host=host, port=port, reload=reload, log_level=log_level)


@app.command()
def mcp() -> None:
    """Run the MCP server on stdin/stdout (for Claude Desktop, Claude Code and other MCP clients)."""
    try:
        from .mcp_server import run_stdio
    except ImportError:
        raise _fail('the MCP SDK is not installed: pip install "neuron-atlas-interface[mcp]"') from None
    run_stdio(_atlas())


# --------------------------------------------------------------------------- geometry


@geometry_app.command("tables")
def geometry_tables(
    out_dir: Annotated[Path, typer.Argument(help="Directory for the tables.")],
    query: Annotated[str | None, typer.Option("--query", "-q", help="Restrict to poles this search finds (default: all mirrored).")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="csv | parquet (needs pyarrow)")] = "csv",
) -> None:
    """Flatten mirrored files into tidy tables: poles, modes, subcircuits, firings, mode similarities, circuit edges."""
    from .geometry.tables import write_tables

    with _atlas() as atlas:
        pids = [h.pid for h in atlas.iter_search(SearchQuery(q=query))] if query is not None else None
        written = write_tables(atlas, out_dir, pids=pids, fmt=fmt)
    for name, rows in written.items():
        out.print(f"{name}: {rows:,} rows")


@geometry_app.command("map")
def geometry_map(
    output: Annotated[Path, typer.Argument(help="HTML file to write (self-contained, opens in any browser).")],
    query: Annotated[str | None, typer.Option("--query", "-q", help="Restrict to poles this search finds.")] = None,
    unit: Annotated[str, typer.Option(help="pole | mode")] = "mode",
    method: Annotated[str, typer.Option(help="pca | umap | tsne")] = "pca",
    clusters: Annotated[int, typer.Option(help="k-means clusters to label (0 = none).")] = 30,
) -> None:
    """Write an interactive 2-D semantic map of poles or modes."""
    from .geometry.maps import semantic_map, write_map_html

    with _atlas() as atlas:
        pids = [h.pid for h in atlas.iter_search(SearchQuery(q=query or ""))]
        data = semantic_map(atlas, pids, unit=unit, method=method, clusters=clusters)
    write_map_html(data, output, title=f"Neuron Atlas map · {query or 'all poles'}")
    out.print(f"wrote {output} ({len(data.get('points', [])):,} points)")
    for note in data.get("notes", []):
        err.print(f"[yellow]note:[/yellow] {note}")


@geometry_app.command("graph")
def geometry_graph(
    output: Annotated[Path, typer.Argument(help="File to write: .csv (edge list), .json or .graphml")],
    query: Annotated[str | None, typer.Option("--query", "-q", help="Restrict to neurons of poles this search finds (default: all mirrored).")] = None,
) -> None:
    """Export the neuron-to-neuron circuit graph of the mirrored region."""
    from .geometry.graph import write_graph

    with _atlas() as atlas:
        dids = None
        if query is not None:
            dids = list(dict.fromkeys(h.did for h in atlas.iter_search(SearchQuery(q=query))))
        stats = write_graph(atlas, output, dids=dids)
    out.print(f"wrote {output}: {stats['nodes']:,} neurons, {stats['edges']:,} edges")


if __name__ == "__main__":  # pragma: no cover
    app()
