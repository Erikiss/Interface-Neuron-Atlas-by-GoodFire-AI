"""2-D maps of poles and modes.

Every mode label has a vector in the semantic index (see
:mod:`neuron_atlas.semantic`).  A map projects those vectors to the plane so
that modes with related meanings land near each other; clusters get a few
characteristic words as a caption.  The result is plain data - the web page
draws it, and :func:`write_map_html` writes a self-contained HTML viewer.

Projections:

``pca``   fast and deterministic; shows the broad axes, clusters overlap.
``umap``  separates clusters well; needs the optional ``umap-learn`` package.
``tsne``  scikit-learn's t-SNE; slow, limited to 15,000 points.

UMAP and t-SNE take seconds to minutes and much memory, so a server runs only
one of them at a time and may cap their size (``max_slow_points``); a request
that would exceed either falls back to PCA and says so in its notes.
"""

from __future__ import annotations

import hashlib
import html
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from ..service import Atlas

METHODS = ("pca", "umap", "tsne")
_TSNE_LIMIT = 15_000

_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()
#: Held while a slow projection (UMAP, t-SNE) runs; a second one at the same time falls back to PCA.
_slow_lock = threading.Lock()
_BUSY = "is being computed"


def _line(n: int) -> np.ndarray:
    xy = np.zeros((n, 2), dtype=np.float32)
    xy[:, 0] = np.linspace(-1, 1, n) if n > 1 else 0.0
    return xy


def project(
    vectors: np.ndarray, method: str = "pca", seed: int = 0, *, max_slow_points: int | None = None
) -> tuple[np.ndarray, list[str]]:
    """Project row vectors to 2-D.  Returns the coordinates (scaled to about [-1, 1]) and notes.

    ``max_slow_points`` caps the size of a UMAP or t-SNE projection (larger
    ones use PCA); only one slow projection runs at a time in this process.
    """
    notes: list[str] = []
    if method not in METHODS:
        raise ValueError(f"unknown projection {method!r}; choose from {', '.join(METHODS)}")
    n = vectors.shape[0]
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32), notes
    if n < 3 or vectors.shape[1] < 2:
        return _line(n), notes
    vectors = np.asarray(vectors, dtype=np.float32)
    if not np.isfinite(vectors).all():
        raise ValueError("the vectors contain NaN or infinite values")
    if float(np.ptp(vectors, axis=0).max()) < 1e-9:
        notes.append("all points have the same vector; they are spread on a line only to stay visible")
        return _line(n), notes
    if method in ("umap", "tsne") and max_slow_points is not None and n > max_slow_points:
        notes.append(
            f"{method.upper()} is limited to {max_slow_points:,} points on this server ({n:,} requested); used PCA instead"
        )
        method = "pca"
    if method in ("umap", "tsne"):
        if not _slow_lock.acquire(blocking=False):
            notes.append(f"another {method.upper()} map {_BUSY}; used PCA instead (try again in a minute)")
            method = "pca"
        else:
            try:
                return _finish(_slow(vectors, method, seed, notes), vectors, seed), notes
            finally:
                _slow_lock.release()
    return _finish(None, vectors, seed), notes


def _finish(xy: np.ndarray | None, vectors: np.ndarray, seed: int) -> np.ndarray:
    """PCA when no other projection was made; then centre and scale to about [-1, 1]."""
    if xy is None:
        from sklearn.decomposition import PCA

        xy = PCA(n_components=2, random_state=seed).fit_transform(vectors)
    xy = np.asarray(xy, dtype=np.float32)
    xy -= xy.mean(axis=0, keepdims=True)
    scale = float(np.percentile(np.abs(xy), 99.5))
    return xy / (scale if np.isfinite(scale) and scale > 0 else 1.0)


def _slow(vectors: np.ndarray, method: str, seed: int, notes: list[str]) -> np.ndarray | None:
    """UMAP or t-SNE coordinates, or ``None`` (with a note) when they cannot be made."""
    n = vectors.shape[0]
    xy: np.ndarray | None = None
    if method == "umap":
        try:
            import warnings

            import umap  # type: ignore[import-not-found]

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reducer = umap.UMAP(
                    n_components=2,
                    metric="cosine",
                    n_neighbors=min(15, n - 1),
                    min_dist=0.1,
                    random_state=seed,
                )
                xy = reducer.fit_transform(vectors)
        except ImportError:
            notes.append(
                'UMAP needs the optional package: pip install "neuron-atlas-interface[umap]"; used PCA instead'
            )
    elif method == "tsne":
        if n > _TSNE_LIMIT:
            notes.append(f"t-SNE is limited to {_TSNE_LIMIT:,} points ({n:,} requested); used PCA instead")
        else:
            from sklearn.manifold import TSNE

            xy = TSNE(
                n_components=2,
                metric="cosine",
                init="pca",
                perplexity=min(30.0, max(2.0, (n - 1) / 3)),
                random_state=seed,
            ).fit_transform(vectors)
    if xy is not None and not np.isfinite(xy).all():
        notes.append(f"{method.upper()} did not converge on these points; used PCA instead")
        xy = None
    return xy


def _cluster(vectors: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    from sklearn.cluster import MiniBatchKMeans

    k = max(1, min(k, vectors.shape[0]))
    model = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=3, batch_size=2048)
    return model.fit_predict(vectors)


def _cluster_captions(
    atlas: Atlas, rows: list[int], labels: np.ndarray, k: int, words: int = 3
) -> dict[int, str]:
    """A few words that set each cluster apart (highest mean TF-IDF relative to the whole map)."""
    embedder = atlas.semantic.embedder
    tfidf = getattr(embedder, "tfidf", None)
    terms = getattr(embedder, "terms", None)
    if tfidf is None or not terms:
        return {}
    sub = tfidf[rows]
    overall = np.asarray(sub.mean(axis=0)).ravel()
    out: dict[int, str] = {}
    for c in range(k):
        members = np.flatnonzero(labels == c)
        if members.size == 0:
            continue
        mean = np.asarray(sub[members].mean(axis=0)).ravel()
        score = mean - overall
        top = [terms[int(i)] for i in np.argsort(-score)[: words * 3] if score[int(i)] > 0]
        chosen: list[str] = []
        for term in top:  # skip word pairs that only repeat a chosen word
            if not any(term in other or other in term for other in chosen):
                chosen.append(term)
            if len(chosen) >= words:
                break
        out[c] = " · ".join(chosen)
    return out


def semantic_map(
    atlas: Atlas,
    pids: list[str] | None = None,
    unit: str = "pole",
    method: str = "pca",
    clusters: int = 0,
    seed: int = 0,
    *,
    max_slow_points: int | None = None,
) -> dict[str, Any]:
    """Coordinates for a set of poles (or all of their modes).

    Returns ``{"unit", "method", "points": [...], "clusters": [...], "notes": [...]}``.
    Each point has ``pid``, ``x``, ``y``, ``layer``, ``category``, ``pole``,
    ``label``, ``cluster`` and, for ``unit="mode"``, ``mode_index`` and ``share``.
    ``max_slow_points`` caps UMAP and t-SNE (see :func:`project`).
    """
    if unit not in ("pole", "mode"):
        raise ValueError("unit must be 'pole' or 'mode'")
    if method not in METHODS:
        raise ValueError(f"unknown projection {method!r}; choose from {', '.join(METHODS)}")
    sem = atlas.semantic.ensure()
    catalog = atlas.catalog
    wanted = list(dict.fromkeys(pids)) if pids is not None else list(catalog.poles)
    key = hashlib.sha1(
        f"{atlas.source.release_id}|{sem.embedder.name}|{unit}|{method}|{clusters}|{seed}|{max_slow_points}|".encode()
        + "\n".join(wanted).encode()
    ).hexdigest()
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    notes: list[str] = []
    wanted_set = set(wanted)
    if unit == "pole":
        index = [(i, pid) for i, pid in enumerate(sem.pole_ids) if pid in wanted_set]
        rows = [i for i, _ in index]
        vectors = sem.pole_vectors[rows] if rows else np.zeros((0, 2), np.float32)
        doc_rows = [sem._doc_rows[pid][0] for _, pid in index]  # noqa: SLF001 - first text of the pole, for captions
    else:
        rows = [i for i, d in enumerate(sem.docs) if d.pid in wanted_set]
        vectors = sem.vectors[rows] if rows else np.zeros((0, 2), np.float32)
        doc_rows = rows
    missing = len(wanted_set) - len(
        {sem.pole_ids[i] for i in rows} if unit == "pole" else {sem.docs[i].pid for i in rows}
    )
    if missing:
        notes.append(f"{missing:,} poles have no description and are not on the map")

    xy, more = project(vectors, method, seed, max_slow_points=max_slow_points)
    notes.extend(more)
    labels = _cluster(vectors, clusters, seed) if clusters and len(rows) >= clusters else None
    if clusters and labels is None:
        notes.append(f"{len(rows):,} points are too few for {clusters} clusters; no clusters drawn")
    captions = _cluster_captions(atlas, doc_rows, labels, clusters) if labels is not None else {}

    points: list[dict[str, Any]] = []
    for j, row in enumerate(rows):
        if unit == "pole":
            pid = sem.pole_ids[row]
            p = catalog.poles[pid]
            label = p.headline or p.description
            extra: dict[str, Any] = {}
        else:
            doc = sem.docs[row]
            pid = doc.pid
            p = catalog.poles[pid]
            label = doc.text
            share = next((m.share for m in p.modes if m.index == doc.mode_index), None)
            extra = {"mode_index": doc.mode_index, "share": share}
        points.append(
            {
                "pid": pid,
                **extra,
                "x": round(float(xy[j, 0]), 4),
                "y": round(float(xy[j, 1]), 4),
                "layer": p.layer,
                "category": p.category,
                "pole": p.pole,
                "label": label if len(label) <= 240 else label[:240] + " …",
                "cluster": int(labels[j]) if labels is not None else None,
            }
        )
    cluster_list = []
    if labels is not None:
        for c in range(clusters):
            members = np.flatnonzero(labels == c)
            if members.size:
                cluster_list.append(
                    {
                        "id": c,
                        "size": int(members.size),
                        "label": captions.get(c, ""),
                        "x": round(float(np.median(xy[members, 0])), 4),
                        "y": round(float(np.median(xy[members, 1])), 4),
                    }
                )
    result = {
        "unit": unit,
        "method": method if not any("instead" in n for n in notes) else "pca",
        "embedding": sem.embedder.name,
        "points": points,
        "clusters": cluster_list,
        "notes": notes,
    }
    if any(_BUSY in n for n in notes):
        return result  # a stand-in while another map was computed; ask again later for the real one
    with _cache_lock:
        if len(_cache) > 16:
            _cache.pop(next(iter(_cache)))
        _cache[key] = result
    return result


# --------------------------------------------------------------------------- standalone viewer

_VIEWER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fbfaf6;--ink:#1d272a;--muted:#6b7275;--line:rgba(29,39,42,.15);--panel:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#14181a;--ink:#e8e6e1;--muted:#9aa1a4;--line:rgba(255,255,255,.16);--panel:#1d2326}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0 8px 0 0;font-weight:600}
label{color:var(--muted)}select,input{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
input{min-width:220px}#count{color:var(--muted);margin-left:auto}
#wrap{position:relative;flex:1;min-height:0}canvas{position:absolute;inset:0;width:100%;height:100%;cursor:grab}
#tip{position:absolute;max-width:380px;padding:8px 10px;background:var(--panel);border:1px solid var(--line);border-radius:8px;
 box-shadow:0 6px 24px rgba(0,0,0,.18);pointer-events:none;display:none;font-size:13px}
#tip b{font-family:ui-monospace,Menlo,monospace;font-weight:600}
footer{padding:6px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
</style></head><body>
<header><h1>__TITLE__</h1>
<label>Colour <select id="col"><option value="layer">layer</option><option value="category">category</option><option value="cluster">cluster</option><option value="pole">pole</option></select></label>
<label>Find <input id="find" type="search" placeholder="word in a label"></label>
<span id="count"></span></header>
<div id="wrap"><canvas id="c"></canvas><div id="tip"></div></div>
<footer>Each point is one __UNIT__ of the Qwen3.6 Neuron Atlas (Goodfire), placed by the meaning of its label (__METHOD__ of __EMBEDDING__ vectors). Drag to pan, wheel to zoom, hover to read. Distances are approximate.</footer>
<script>
const DATA = __DATA__;
const P = DATA.points, C = DATA.clusters || [];
const cv = document.getElementById('c'), ctx = cv.getContext('2d'), tip = document.getElementById('tip');
const PAL = ['#0f766e','#7a4a8e','#b03a6e','#6b7d1f','#3465a4','#c94a12','#8a6d1f','#3d7a52','#6a4f8f','#b5542d','#33698a','#7a5c3e'];
const CAT = {mono:'#0f766e',fn:'#3465a4',poly:'#b03a6e',dark:'#6b7275',none:'#b0b4b6'};
let view = {x:0,y:0,k:1}, W=0, H=0, hits=null, hover=-1;
const layers = [...new Set(P.map(p=>p.layer))].sort((a,b)=>a-b);
function colour(p){ const m=document.getElementById('col').value;
  if(m==='category') return CAT[p.category]||'#888';
  if(m==='pole') return p.pole==='pos'?'#c94a12':'#33698a';
  if(m==='cluster') return p.cluster==null?'#888':PAL[p.cluster%PAL.length];
  return PAL[layers.indexOf(p.layer)%PAL.length]; }
function sx(x){return W/2+(x-view.x)*view.k*Math.min(W,H)/2.2} function sy(y){return H/2-(y-view.y)*view.k*Math.min(W,H)/2.2}
function draw(){ const r=devicePixelRatio||1; W=cv.clientWidth; H=cv.clientHeight; cv.width=W*r; cv.height=H*r; ctx.setTransform(r,0,0,r,0,0);
  ctx.clearRect(0,0,W,H); const size=Math.max(1.2,Math.min(4,1.4*Math.sqrt(view.k)));
  for(let i=0;i<P.length;i++){ const p=P[i]; const X=sx(p.x),Y=sy(p.y); if(X<-5||Y<-5||X>W+5||Y>H+5) continue;
    const on=!hits||hits.has(i); ctx.globalAlpha=on?(hits?0.95:0.6):0.07; ctx.fillStyle=colour(p); ctx.beginPath(); ctx.arc(X,Y,on&&hits?size+1:size,0,6.283); ctx.fill(); }
  ctx.globalAlpha=1; ctx.fillStyle=getComputedStyle(document.body).color; ctx.font='600 12px system-ui'; ctx.textAlign='center';
  if(!hits){ const taken=[], bg=getComputedStyle(document.body).backgroundColor, ink=getComputedStyle(document.body).color;
    for(const c of [...C].sort((a,b)=>b.size-a.size)){ if(!c.label) continue; const X=sx(c.x),Y=sy(c.y); if(X<0||Y<0||X>W||Y>H) continue;
      const w=ctx.measureText(c.label).width/2+6, box=[X-w,Y-10,X+w,Y+8];
      if(taken.some(t=>box[0]<t[2]&&box[2]>t[0]&&box[1]<t[3]&&box[3]>t[1])) continue;   // bigger clusters win; zoom in to see the rest
      taken.push(box); ctx.globalAlpha=.9; ctx.lineWidth=4; ctx.lineJoin='round'; ctx.strokeStyle=bg; ctx.strokeText(c.label,X,Y); ctx.fillStyle=ink; ctx.fillText(c.label,X,Y); } }
  ctx.globalAlpha=1;
  if(hover>=0){ const p=P[hover]; ctx.strokeStyle=getComputedStyle(document.body).color; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(sx(p.x),sy(p.y),size+3,0,6.283); ctx.stroke(); }
  document.getElementById('count').textContent=(hits?hits.size.toLocaleString()+' of ':'')+P.length.toLocaleString()+' points'; }
function nearest(mx,my){ let best=-1,bd=144; for(let i=0;i<P.length;i++){ if(hits&&!hits.has(i)) continue; const dx=sx(P[i].x)-mx,dy=sy(P[i].y)-my,d=dx*dx+dy*dy; if(d<bd){bd=d;best=i;} } return best; }
let drag=null;
cv.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};cv.setPointerCapture(e.pointerId);cv.style.cursor='grabbing'});
cv.addEventListener('pointerup',()=>{drag=null;cv.style.cursor='grab'});
cv.addEventListener('pointermove',e=>{ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  if(drag){ const s=view.k*Math.min(W,H)/2.2; view.x=drag.vx-(e.clientX-drag.x)/s; view.y=drag.vy+(e.clientY-drag.y)/s; hover=-1; tip.style.display='none'; draw(); return; }
  const i=nearest(mx,my); if(i!==hover){ hover=i; draw(); }
  if(i<0){ tip.style.display='none'; return; } const p=P[i]; tip.replaceChildren();
  const b=document.createElement('b'); b.textContent=(p.pole==='pos'?'+ ':'\\u2212 ')+p.pid.replace(/__(pos|neg)$/,'').replace(/_/g,'/')+(p.mode_index!=null?' \\u00b7 mode '+p.mode_index:'');
  const d=document.createElement('div'); d.textContent=p.label; const m=document.createElement('div'); m.style.color='var(--muted)';
  m.textContent='layer '+p.layer+' \\u00b7 '+p.category+(p.share!=null?' \\u00b7 '+(p.share*100).toFixed(1)+'% of firings':''); tip.append(b,d,m);
  tip.style.display='block'; tip.style.left=Math.min(mx+14,W-390)+'px'; tip.style.top=Math.min(my+14,H-120)+'px'; });
cv.addEventListener('wheel',e=>{ e.preventDefault(); view.k=Math.max(.5,Math.min(60,view.k*Math.exp(-e.deltaY*0.0015))); draw(); },{passive:false});
document.getElementById('col').onchange=draw;
document.getElementById('find').oninput=e=>{ const q=e.target.value.trim().toLowerCase(); hits=q?new Set(P.map((p,i)=>p.label.toLowerCase().includes(q)?i:-1).filter(i=>i>=0)):null; draw(); };
addEventListener('resize',draw); draw();
</script></body></html>
"""


def write_map_html(data: dict[str, Any], path: Path | str, title: str = "Neuron Atlas map") -> None:
    """Write a self-contained interactive viewer for a :func:`semantic_map` result."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (
        _VIEWER.replace("__TITLE__", html.escape(title))
        .replace("__UNIT__", html.escape(str(data.get("unit", "pole"))))
        .replace("__METHOD__", html.escape(str(data.get("method", "pca")).upper()))
        .replace("__EMBEDDING__", html.escape(str(data.get("embedding", "semantic"))))
        .replace("__DATA__", payload)
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
