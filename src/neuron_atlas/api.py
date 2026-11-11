"""The HTTP API (FastAPI), the web page and the MCP endpoint in one application.

``create_app()`` returns an ASGI app:

* ``/``                 the search page (static files, no build step)
* ``/api/v1/...``       the JSON API (OpenAPI at ``/openapi.json``, docs at ``/docs``)
* ``/mcp``              the same functions as MCP tools (streamable HTTP), for chatbots
* ``/llms.txt``         a plain-text guide for language models

All handlers are thin wrappers around :class:`neuron_atlas.service.Atlas`.
"""

# No ``from __future__ import annotations`` here: FastAPI has to see the real
# annotation objects of the handlers, which refer to names local to create_app().

import contextlib
import hmac
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .details import ALL_INCLUDES, DEFAULT_INCLUDES
from .glossary import GLOSSARY, llms_txt
from .models import (
    BatchPolesRequest,
    BatchPolesResponse,
    BatchSearchRequest,
    BatchSearchResponse,
    MirrorRequest,
    NeuronDetail,
    PoleDetail,
    SearchQuery,
    SearchResponse,
    SimilarResponse,
)
from .service import Atlas, NotFound
from .upstream import NotMirrored, UpstreamError

WEB_DIR = Path(__file__).parent / "web"

TAGS = [
    {"name": "search", "description": "Find poles by text, id, meaning and structured filters."},
    {"name": "poles", "description": "Everything about one pole (one sign of a neuron) or one neuron."},
    {"name": "export", "description": "Whole result sets as a stream (NDJSON, CSV, Markdown)."},
    {"name": "geometry", "description": "Maps and graphs for analysing regions of the atlas."},
    {"name": "mirror", "description": "Download parts of the atlas into the local mirror."},
    {"name": "meta", "description": "What this server is and what the words mean."},
]


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _csv_int(value: str | None, name: str) -> list[int] | None:
    items = _csv(value)
    if items is None:
        return None
    out: list[int] = []
    for item in items:
        try:
            if ".." in item:
                lo, hi = item.split("..", 1)
                out.extend(range(int(lo), int(hi) + 1))
            else:
                out.append(int(item))
        except ValueError:
            raise HTTPException(400, f"{name}: {item!r} is not a number or a range like 9..36") from None
    return out


class _ClientLimiter:
    """Per-client sliding budget (requests per minute); in-memory, per process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> tuple[bool, int]:
        if self.per_minute <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(client, ()) if now - t < 60.0]
            if len(hits) >= self.per_minute:
                self._hits[client] = hits
                return False, max(1, int(60.0 - (now - hits[0])) + 1)
            hits.append(now)
            self._hits[client] = hits
            if len(self._hits) > 20_000:  # forget idle clients
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 60.0]:
                    del self._hits[key]
        return True, 0


def create_app(atlas: Atlas | None = None, settings: Settings | None = None, *, mcp: bool = True) -> FastAPI:
    """Build the application.  Pass an :class:`Atlas` to share one, or settings to create one."""
    atlas = atlas or Atlas(settings)
    settings = atlas.settings
    limiter = _ClientLimiter(settings.client_rate_per_minute)

    mcp_server = None
    mcp_app = None
    if mcp:
        try:
            from .mcp_server import build_server, http_app

            mcp_server = build_server(atlas)
            mcp_app = http_app(mcp_server, settings)
        except ImportError:
            mcp_server = mcp_app = None  # the optional "mcp" package is not installed

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN202
        warm = threading.Thread(target=_warm, args=(atlas,), name="atlas-warm", daemon=True)
        warm.start()
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(
        title="Neuron Atlas Interface",
        version=__version__,
        summary="Search, read and export Goodfire's Qwen3.6 Neuron Atlas.",
        description=(
            "Unofficial interface to the [Qwen3.6 Neuron Atlas](https://www.goodfire.com/research/public_neuron_atlas) "
            "published by Goodfire. The atlas describes what individual neurons of the language model "
            "Qwen3.6-35B-A3B respond to. Every neuron is read on two **poles** (positive and negative output); "
            "a pole fires in one or several **modes**.\n\n"
            "Start with `GET /api/v1/search?q=…`, open a hit with `GET /api/v1/poles/{id}`, "
            "and read `GET /api/v1/glossary` for the vocabulary. Language models: see `/llms.txt` or connect to `/mcp`.\n\n"
            "The data belongs to Goodfire AI; this service reads the published files and caches them locally."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    app.state.atlas = atlas
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    # ------------------------------------------------------------------ guards

    def _presented_key(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")

    def _key_ok(key: str) -> bool:
        return any(hmac.compare_digest(key.encode(), k.encode()) for k in settings.api_keys)

    def guard(request: Request) -> None:
        key = _presented_key(request)
        if settings.api_keys and not _key_ok(key):
            raise HTTPException(401, "missing or wrong API key (send it as 'X-API-Key' or 'Authorization: Bearer …')")
        client = key or (request.client.host if request.client else "unknown")
        ok, wait = limiter.allow(client)
        if not ok:
            raise HTTPException(429, "too many requests; slow down", headers={"Retry-After": str(wait)})

    @app.middleware("http")
    async def protect_mcp(request: Request, call_next):  # noqa: ANN001, ANN202
        # The MCP endpoint is a mounted app, so the dependency above does not cover it.
        if request.url.path.startswith("/mcp") and settings.api_keys and not _key_ok(_presented_key(request)):
            return JSONResponse({"error": "unauthorized", "detail": "missing or wrong API key"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        names = {400: "bad_request", 401: "unauthorized", 404: "not_found", 409: "conflict", 429: "rate_limited"}
        body = {"error": names.get(exc.status_code, "error"), "detail": exc.detail}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"error": "not_found", "detail": str(exc)}, status_code=404)

    @app.exception_handler(NotMirrored)
    async def _not_mirrored(_request: Request, exc: NotMirrored) -> JSONResponse:
        return JSONResponse({"error": "not_mirrored", "detail": str(exc)}, status_code=409)

    @app.exception_handler(UpstreamError)
    async def _upstream(_request: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse({"error": "upstream", "detail": str(exc)}, status_code=502)

    @app.exception_handler(ValueError)
    async def _bad_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)

    api = Depends(guard)

    def _includes(value: str | None, default: tuple[str, ...] = DEFAULT_INCLUDES) -> list[str]:
        items = _csv(value)
        if items is None:
            return list(default)
        if "all" in items:
            return [i for i in ALL_INCLUDES if i not in ("tokens", "research")] + [
                i for i in ("tokens", "research") if i in items
            ]
        return items

    # ------------------------------------------------------------------ meta

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/v1/info", tags=["meta"], dependencies=[api], summary="About this server and the atlas release")
    def info() -> dict[str, Any]:
        return atlas.info()

    @app.get("/api/v1/glossary", tags=["meta"], dependencies=[api], summary="What the atlas's words mean")
    def glossary() -> dict[str, Any]:
        return GLOSSARY

    @app.get("/llms.txt", include_in_schema=False, response_class=PlainTextResponse)
    def llms(request: Request) -> str:
        return llms_txt(str(request.base_url).rstrip("/"))

    # ------------------------------------------------------------------ search

    def _query_from_params(
        q: Annotated[str, Query(description="Free text, an id, and/or inline filters such as `layer:30 cat:fn`.")] = "",
        match: Annotated[str, Query(description="auto | words | substring | regex | semantic")] = "auto",
        scope: Annotated[str | None, Query(description="Comma list of labels, deep, examples.")] = None,
        layer: Annotated[str | None, Query(description="Comma list of layers; ranges like 9..36 allowed.")] = None,
        layer_min: int | None = None,
        layer_max: int | None = None,
        expert: Annotated[str | None, Query(description="Comma list of expert indices.")] = None,
        neuron: Annotated[str | None, Query(description="Comma list of neuron indices.")] = None,
        pole: Annotated[str | None, Query(description="pos | neg")] = None,
        cat: Annotated[str | None, Query(description="Comma list of mono, fn, poly, dark, none.")] = None,
        verdict: Annotated[str | None, Query(description="monosemantic | polysemantic")] = None,
        has: Annotated[str | None, Query(description="Comma list: subcircuit, overview, landscape, islands, circuit, simulatability, headline, mirrored.")] = None,
        lacks: Annotated[str | None, Query(description="Same vocabulary as `has`.")] = None,
        modes_min: int | None = None,
        modes_max: int | None = None,
        sim_min: Annotated[float | None, Query(description="Lower bound on simulatability (fraction).")] = None,
        sim_max: float | None = None,
        damage_min: Annotated[float | None, Query(description="Lower bound on main_damage_share (0..1).")] = None,
        damage_max: float | None = None,
        sample: Annotated[str, Query(description="all | public (the 3,000 poles on goodfire.com) | hidden")] = "all",
        ids: Annotated[str | None, Query(description="Comma list of neuron or pole ids to restrict to.")] = None,
        sort: Annotated[str, Query(description="relevance | simulatability | layer | modes | damage | share | random | id")] = "relevance",
        order: Annotated[str | None, Query(description="asc | desc")] = None,
        group_by: Annotated[str, Query(description="pole | neuron | mode")] = "pole",
        limit: Annotated[int, Query(ge=0, le=5000)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
        facets: bool = False,
        snippets: bool = True,
    ) -> SearchQuery:
        data: dict[str, Any] = {
            "q": q, "match": match, "layers": _csv_int(layer, "layer"), "layer_min": layer_min, "layer_max": layer_max,
            "experts": _csv_int(expert, "expert"), "neurons": _csv_int(neuron, "neuron"), "pole": pole,
            "categories": _csv(cat), "verdict": verdict, "has": _csv(has), "lacks": _csv(lacks),
            "modes_min": modes_min, "modes_max": modes_max, "simulatability_min": sim_min,
            "simulatability_max": sim_max, "damage_min": damage_min, "damage_max": damage_max, "sample": sample,
            "ids": _csv(ids), "sort": sort, "order": order, "group_by": group_by, "limit": limit, "offset": offset,
            "facets": facets, "snippets": snippets,
        }  # fmt: skip
        if scope:
            data["scope"] = _csv(scope)
        try:
            return SearchQuery.model_validate({k: v for k, v in data.items() if v is not None})
        except Exception as exc:  # pydantic.ValidationError -> 400 with a readable message
            raise HTTPException(400, _validation_message(exc)) from None

    @app.get("/api/v1/search", tags=["search"], dependencies=[api], response_model=SearchResponse, summary="Search the atlas")
    def search_get(query: Annotated[SearchQuery, Depends(_query_from_params)]) -> SearchResponse:
        return atlas.search(query)

    @app.post("/api/v1/search", tags=["search"], dependencies=[api], response_model=SearchResponse, summary="Search the atlas (JSON body)")
    def search_post(query: SearchQuery) -> SearchResponse:
        return atlas.search(query)

    @app.post(
        "/api/v1/search/batch", tags=["search"], dependencies=[api], response_model=BatchSearchResponse,
        summary="Run many searches in one request",
    )  # fmt: skip
    def search_batch(request: BatchSearchRequest) -> BatchSearchResponse:
        started = time.perf_counter()
        results = atlas.batch_search(request.queries)
        return BatchSearchResponse(results=results, took_ms=round((time.perf_counter() - started) * 1000, 2))

    @app.get("/api/v1/similar", tags=["search"], dependencies=[api], response_model=SimilarResponse, summary="Poles or modes with a similar meaning")
    def similar(
        pole: Annotated[str | None, Query(description="Pole id to start from.")] = None,
        mode: Annotated[int | None, Query(description="Mode index of that pole; omit to compare whole poles.")] = None,
        text: Annotated[str | None, Query(description="Or: free text to find modes for.")] = None,
        top: Annotated[int, Query(ge=1, le=1000)] = 20,
    ) -> SimilarResponse:
        return atlas.similar(pole=pole, mode=mode, text=text, top=top)

    @app.get("/api/v1/random", tags=["search"], dependencies=[api], response_model=SearchResponse, summary="A few random poles (optionally filtered)")
    def random_poles(
        query: Annotated[SearchQuery, Depends(_query_from_params)],
        seed: Annotated[int | None, Query(description="Give a seed to get the same picks again.")] = None,
    ) -> SearchResponse:
        import random

        full = atlas.search(query.model_copy(update={"limit": 0, "offset": 0, "group_by": "pole"}))
        if full.total == 0:
            return full
        rng = random.Random(seed)
        count = min(max(query.limit, 1), full.total, 50)
        picks = sorted(rng.sample(range(full.total), count))
        out = full.model_copy(update={"limit": count, "notes": [*full.notes, "random picks from the matching poles"]})
        for offset in picks:
            out.hits.extend(atlas.search(query.model_copy(update={"limit": 1, "offset": offset, "group_by": "pole"})).hits)
        rng.shuffle(out.hits)
        return out

    # ------------------------------------------------------------------ poles and neurons

    @app.get(
        "/api/v1/poles/{ref}", tags=["poles"], dependencies=[api], response_model=PoleDetail,
        summary="One pole: modes, examples and more",
        responses={200: {"content": {"text/markdown": {}, "text/plain": {}, "text/html": {}}}},
    )  # fmt: skip
    def pole(
        ref: str,
        include: Annotated[str | None, Query(description="Comma list of modes, subcircuits, examples, islands, overview, landscape, circuit, tokens, research - or `all`. Default: modes,examples.")] = None,
        max_examples: Annotated[int, Query(ge=0, le=12, description="Examples per mode / subcircuit / island.")] = 3,
        format: Annotated[str, Query(description="json | md | txt | html (print-ready page)")] = "json",
    ) -> Any:
        detail = atlas.pole(ref, _includes(include), max_examples=max_examples)
        return _formatted(detail, format)

    @app.get("/api/v1/poles/{ref}/raw/{kind}", tags=["poles"], dependencies=[api], summary="Untouched upstream JSON of one data set of a pole")
    def pole_raw(ref: str, kind: str) -> Any:
        if kind not in ("subcircuit", "overview", "landscape", "islands"):
            raise HTTPException(400, "kind must be one of subcircuit, overview, landscape, islands")
        data = atlas.raw(kind, ref)
        if data is None:
            raise HTTPException(404, f"upstream has no {kind} data for {ref}")
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/api/v1/poles/batch", tags=["poles"], dependencies=[api], response_model=BatchPolesResponse, summary="Many poles in one request")
    def poles_batch(request: BatchPolesRequest) -> BatchPolesResponse:
        return atlas.poles(request.ids, request.include, max_examples=request.max_examples)

    @app.get("/api/v1/neurons/{ref}", tags=["poles"], dependencies=[api], response_model=NeuronDetail, summary="One neuron: both poles, optionally its circuit")
    def neuron(
        ref: str,
        include: Annotated[str | None, Query(description="`circuit` adds the neuron's inputs and readers.")] = None,
        format: Annotated[str, Query(description="json | md")] = "json",
    ) -> Any:
        detail = atlas.neuron(ref, _csv(include) or ())
        if format == "json":
            return detail
        if format in ("md", "markdown"):
            from .render import neuron_markdown

            return PlainTextResponse(neuron_markdown(detail), media_type="text/markdown; charset=utf-8")
        raise HTTPException(400, "format must be json or md")

    @app.get("/api/v1/neurons/{ref}/raw/{kind}", tags=["poles"], dependencies=[api], summary="Untouched upstream circuit JSON of a neuron")
    def neuron_raw(ref: str, kind: str) -> Any:
        names = {"meta": "circuit_meta", "exemplars": "circuit_exemplars", "incidence": "circuit_incidence"}
        if kind not in names:
            raise HTTPException(400, "kind must be one of meta, exemplars, incidence")
        data = atlas.raw(names[kind], ref)
        if data is None:
            raise HTTPException(404, f"upstream has no circuit {kind} for {ref}")
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=86400"})

    def _formatted(detail: PoleDetail, fmt: str) -> Any:
        fmt = fmt.lower()
        if fmt == "json":
            return detail
        from . import render

        if fmt in ("md", "markdown"):
            return PlainTextResponse(render.pole_markdown(detail), media_type="text/markdown; charset=utf-8")
        if fmt in ("txt", "text"):
            return PlainTextResponse(render.pole_text(detail), media_type="text/plain; charset=utf-8")
        if fmt == "html":
            return HTMLResponse(render.pole_html(detail))
        raise HTTPException(400, "format must be one of json, md, txt, html")

    # ------------------------------------------------------------------ export

    def _export(query: SearchQuery, fmt: str, include: list[str], max_examples: int) -> StreamingResponse:
        from . import render

        fmt = fmt.lower()
        if fmt not in ("ndjson", "csv", "md"):
            raise HTTPException(400, "format must be one of ndjson, csv, md")
        query = query.model_copy(update={"group_by": "pole", "facets": False})
        want_detail = bool(include)

        def details_of(hit: Any) -> PoleDetail | None:
            # Exports never trigger upstream downloads: details come from the mirror only.
            if not want_detail:
                return None
            try:
                return atlas.details.pole(hit.pid, include, max_examples=max_examples, remote=False)
            except Exception:  # noqa: BLE001
                return None

        def lines() -> Iterator[str]:
            if fmt == "csv":
                yield render.csv_header()
            elif fmt == "md":
                yield f"# Neuron Atlas export\n\nQuery: `{json.dumps(query.model_dump(exclude_defaults=True), ensure_ascii=False)}`\n\n"
            for hit in atlas.iter_search(query):
                detail = details_of(hit)
                if fmt == "ndjson":
                    obj = detail.model_dump(exclude_none=True) if detail else hit.model_dump(exclude_none=True)
                    yield json.dumps(obj, ensure_ascii=False) + "\n"
                elif fmt == "csv":
                    yield render.csv_row(hit)
                else:
                    yield (render.pole_markdown(detail, heading=2) if detail else render.hit_markdown(hit)) + "\n\n"

        media = {"ndjson": "application/x-ndjson", "csv": "text/csv; charset=utf-8", "md": "text/markdown; charset=utf-8"}[fmt]
        headers = {"Content-Disposition": f'attachment; filename="neuron-atlas-export.{fmt}"'}
        return StreamingResponse(lines(), media_type=media, headers=headers)

    @app.get("/api/v1/export", tags=["export"], dependencies=[api], summary="Stream every hit of a search")
    def export_get(
        query: Annotated[SearchQuery, Depends(_query_from_params)],
        format: Annotated[str, Query(description="ndjson | csv | md")] = "ndjson",
        include: Annotated[str | None, Query(description="Detail sections to add for poles that are already mirrored (e.g. modes,examples).")] = None,
        max_examples: Annotated[int, Query(ge=0, le=12)] = 2,
    ) -> StreamingResponse:
        return _export(query, format, _csv(include) or [], max_examples)

    @app.post("/api/v1/export", tags=["export"], dependencies=[api], summary="Stream every hit of a search (JSON body)")
    def export_post(
        query: SearchQuery,
        format: Annotated[str, Query(description="ndjson | csv | md")] = "ndjson",
        include: Annotated[str | None, Query()] = None,
        max_examples: Annotated[int, Query(ge=0, le=12)] = 2,
    ) -> StreamingResponse:
        return _export(query, format, _csv(include) or [], max_examples)

    # ------------------------------------------------------------------ geometry

    @app.get("/api/v1/map", tags=["geometry"], dependencies=[api], summary="2-D semantic map of poles or modes")
    def semantic_map(
        query: Annotated[SearchQuery, Depends(_query_from_params)],
        unit: Annotated[str, Query(description="pole | mode")] = "pole",
        method: Annotated[str, Query(description="pca | umap (needs the optional umap-learn package) | tsne")] = "pca",
        clusters: Annotated[int, Query(ge=0, le=200, description="Number of k-means clusters to label (0 = none).")] = 0,
    ) -> Any:
        from .geometry.maps import semantic_map as build

        pids = [hit.pid for hit in atlas.iter_search(query.model_copy(update={"group_by": "pole", "snippets": False}))]
        return build(atlas, pids, unit=unit, method=method, clusters=clusters)

    @app.get("/api/v1/graph/{ref}", tags=["geometry"], dependencies=[api], summary="Circuit neighbourhood of a neuron")
    def graph(
        ref: str,
        depth: Annotated[int, Query(ge=1, le=3, description="How many steps to follow.")] = 1,
        max_nodes: Annotated[int, Query(ge=1, le=2000)] = 300,
    ) -> Any:
        from .geometry.graph import neighbourhood

        return neighbourhood(atlas, atlas.resolve_did(ref), depth=depth, max_nodes=max_nodes)

    # ------------------------------------------------------------------ mirror

    @app.post("/api/v1/mirror/plan", tags=["mirror"], dependencies=[api], summary="What a mirror request would download")
    def mirror_plan(request: MirrorRequest) -> dict[str, Any]:
        plan = atlas.plan_mirror(request)
        return {"estimate": plan.estimate(), "sample_poles": plan.poles[:20]}

    @app.post("/api/v1/mirror", tags=["mirror"], dependencies=[api], summary="Start downloading a selection in the background")
    def mirror_start(request: MirrorRequest, label: Annotated[str, Query(max_length=200)] = "") -> dict[str, Any]:
        if not atlas.source.can_fetch():
            raise HTTPException(409, "this server does not fetch from upstream (offline / ATLAS_ALLOW_REMOTE_FETCH=0)")
        if request.dry_run:
            return mirror_plan(request)
        job = atlas.mirror_in_background(request, label)
        return job.as_dict()

    @app.get("/api/v1/mirror/jobs", tags=["mirror"], dependencies=[api], summary="Mirror jobs of this server")
    def mirror_jobs() -> dict[str, Any]:
        return {"jobs": [job.as_dict() for job in atlas.jobs.list()]}

    @app.get("/api/v1/mirror/jobs/{job_id}", tags=["mirror"], dependencies=[api], summary="Progress of one mirror job")
    def mirror_job(job_id: str) -> dict[str, Any]:
        job = atlas.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.as_dict()

    @app.delete("/api/v1/mirror/jobs/{job_id}", tags=["mirror"], dependencies=[api], summary="Cancel a mirror job")
    def mirror_cancel(job_id: str) -> dict[str, Any]:
        if not atlas.jobs.cancel(job_id):
            raise HTTPException(404, "no such running job")
        return {"cancelled": job_id}

    # ------------------------------------------------------------------ pages

    @app.get("/p/{ref}", include_in_schema=False, response_class=HTMLResponse)
    def print_page(
        request: Request,
        ref: str,
        include: str | None = None,
        max_examples: Annotated[int, Query(ge=0, le=12)] = 3,
    ) -> HTMLResponse:
        guard(request)
        from . import render

        detail = atlas.pole(ref, _includes(include, ("modes", "examples", "overview")), max_examples=max_examples)
        return HTMLResponse(render.pole_html(detail))

    if WEB_DIR.is_dir():
        app.mount("/static", _RevalidatedStatic(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> Response:
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    else:  # pragma: no cover - the web directory ships with the package

        @app.get("/", include_in_schema=False)
        def index_missing() -> dict[str, Any]:
            return {"name": "Neuron Atlas Interface", "docs": "/docs", "api": "/api/v1/info"}

    if mcp_app is not None:
        # Mounted last and at the root so that POST /mcp works without a redirect.
        app.mount("/", mcp_app)

    return app


class _RevalidatedStatic(StaticFiles):
    """Static files that browsers must revalidate (ETag), so an upgrade never mixes old and new modules."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _warm(atlas: Atlas) -> None:
    # A failure here is not fatal: the first request will report the real problem.
    with contextlib.suppress(Exception):
        atlas.warm(semantic=True)


def _validation_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        parts = []
        for err in errors():
            where = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{where}: {err.get('msg', '')}")
        return "; ".join(parts) or str(exc)
    return str(exc)

