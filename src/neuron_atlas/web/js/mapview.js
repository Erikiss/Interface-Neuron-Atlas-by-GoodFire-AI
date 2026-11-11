// The semantic map: a canvas scatter plot of /api/v1/map for the current filters.
// Built for ~25,000 points: typed arrays, one path per colour, a grid index for hover.

import { api, isAbort } from './api.js';
import { h, icon, replace, fmtInt, displayId, signChar, poleOf, debounce } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox, segmented, showTipAt, hideTip } from './ui.js';
import { CATEGORIES, stateFromParams, stateToParams, filterParams, buildHash, poleHash, hasFilters } from './state.js';
import { filterChips } from './parts.js';

// Sequential blue ramp for layer depth (light: light -> dark; dark theme: dark -> light).
const RAMP_LIGHT = ['#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'];
const RAMP_DARK = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4', '#b7d3f6', '#cde2fb'];
// Categorical: the three slots that stay apart for every pair of points, plus two neutrals.
const CAT_LIGHT = { mono: '#2a78d6', fn: '#1baf7a', poly: '#eb6834', dark: '#52514e', none: '#b5b3aa' };
const CAT_DARK = { mono: '#3987e5', fn: '#199e70', poly: '#d95926', dark: '#c3c2b7', none: '#6b6a65' };
// Clusters: four hues that hold for all pairs; neighbouring clusters never share one.
const CLUSTER_LIGHT = ['#2a78d6', '#eda100', '#e87ba4', '#008300'];
const CLUSTER_DARK = ['#3987e5', '#c98500', '#d55181', '#008300'];

function isDark() {
  const forced = document.documentElement.dataset.theme;
  if (forced) return forced === 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rampColor(ramp, f) {
  const x = Math.max(0, Math.min(1, f)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(x));
  const a = hexToRgb(ramp[i]);
  const b = hexToRgb(ramp[i + 1]);
  const u = x - i;
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * u)}, ${Math.round(a[1] + (b[1] - a[1]) * u)}, ${Math.round(a[2] + (b[2] - a[2]) * u)})`;
}

/** Give neighbouring clusters different colour slots (greedy, largest first). */
function assignClusterSlots(clusters, slots) {
  const out = new Map();
  const near = new Map();
  for (const c of clusters) {
    const others = clusters.filter((o) => o !== c).map((o) => ({ o, d: (o.x - c.x) ** 2 + (o.y - c.y) ** 2 })).sort((a, b) => a.d - b.d);
    near.set(c.id, others.slice(0, 3).map((x) => x.o.id));
  }
  for (const [id, list] of near) for (const other of list) if (!near.get(other).includes(id)) near.get(other).push(id);
  const order = [...clusters].sort((a, b) => (b.size || 0) - (a.size || 0));
  for (const c of order) {
    const taken = new Set(near.get(c.id).map((id) => out.get(id)).filter((x) => x != null));
    let slot = -1;
    for (let s = 0; s < slots; s++) {
      if (!taken.has(s)) {
        slot = s;
        break;
      }
    }
    if (slot < 0) {
      // every slot is used by a neighbour: take the one whose user is farthest away
      let best = -1;
      for (let s = 0; s < slots; s++) {
        let dmin = Infinity;
        for (const o of clusters) if (out.get(o.id) === s) dmin = Math.min(dmin, (o.x - c.x) ** 2 + (o.y - c.y) ** 2);
        if (dmin > best) {
          best = dmin;
          slot = s;
        }
      }
    }
    out.set(c.id, slot);
  }
  return out;
}

// --------------------------------------------------------------------------- scatter engine

class Scatter {
  constructor(canvas, { onHover, onClick }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onHover = onHover;
    this.onClick = onClick;
    this.n = 0;
    this.xs = new Float32Array(0);
    this.ys = new Float32Array(0);
    this.group = new Uint16Array(0); // colour group per point
    this.colors = [];
    this.mask = null; // Uint8Array: 1 = highlighted
    this.maskCount = 0;
    this.labels = []; // cluster labels: {x, y, text, size}
    this.k = 1;
    this.tx = 0;
    this.ty = 0;
    this.hover = -1;
    this.frame = 0;
    this.pointers = new Map();
    this.bind();
  }

  setData(points) {
    const n = points.length;
    this.n = n;
    this.xs = new Float32Array(n);
    this.ys = new Float32Array(n);
    let x0 = Infinity;
    let x1 = -Infinity;
    let y0 = Infinity;
    let y1 = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = Number(points[i].x) || 0;
      const y = Number(points[i].y) || 0;
      this.xs[i] = x;
      this.ys[i] = y;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
    if (!n) x0 = x1 = y0 = y1 = 0;
    this.bounds = { x0, x1, y0, y1 };
    this.buildGrid();
    this.group = new Uint16Array(n);
    this.mask = null;
    this.hover = -1;
    this.fit();
  }

  buildGrid() {
    const G = (this.G = 96);
    const { x0, x1, y0, y1 } = this.bounds;
    this.gw = (x1 - x0) / G || 1;
    this.gh = (y1 - y0) / G || 1;
    const counts = new Uint32Array(G * G + 1);
    const cellOf = new Uint32Array(this.n);
    for (let i = 0; i < this.n; i++) {
      const cx = Math.min(G - 1, Math.max(0, Math.floor((this.xs[i] - x0) / this.gw)));
      const cy = Math.min(G - 1, Math.max(0, Math.floor((this.ys[i] - y0) / this.gh)));
      cellOf[i] = cy * G + cx;
      counts[cellOf[i] + 1]++;
    }
    for (let c = 0; c < G * G; c++) counts[c + 1] += counts[c];
    const fill = counts.slice(0, G * G);
    const items = new Uint32Array(this.n);
    for (let i = 0; i < this.n; i++) items[fill[cellOf[i]]++] = i;
    this.cellStart = counts;
    this.cellItems = items;
  }

  setColors(group, colors) {
    this.group = group;
    this.colors = colors;
    this.requestDraw();
  }

  setMask(mask, count) {
    this.mask = mask;
    this.maskCount = count || 0;
    this.requestDraw();
  }

  setLabels(labels) {
    this.labels = labels || [];
    this.requestDraw();
  }

  size() {
    const r = this.canvas.getBoundingClientRect();
    return { w: Math.max(1, r.width), h: Math.max(1, r.height) };
  }

  resize() {
    const { w, h: hh } = this.size();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const pw = Math.round(w * dpr);
    const ph = Math.round(hh * dpr);
    if (this.canvas.width !== pw || this.canvas.height !== ph) {
      const first = !this.sized;
      const old = this.sized;
      this.canvas.width = pw;
      this.canvas.height = ph;
      this.sized = { w, h: hh };
      if (first) this.fit();
      else if (old) {
        // keep the centre where it was
        this.tx += (w - old.w) / 2;
        this.ty += (hh - old.h) / 2;
      }
    }
    this.dpr = dpr;
    this.requestDraw();
  }

  fit() {
    const { w, h: hh } = this.size();
    const { x0, x1, y0, y1 } = this.bounds || { x0: 0, x1: 1, y0: 0, y1: 1 };
    const pad = 28;
    const dx = x1 - x0 || 1;
    const dy = y1 - y0 || 1;
    this.k = Math.min((w - pad * 2) / dx, (hh - pad * 2) / dy);
    if (!Number.isFinite(this.k) || this.k <= 0) this.k = 1;
    this.k0 = this.k;
    this.tx = w / 2 - ((x0 + x1) / 2) * this.k;
    this.ty = hh / 2 + ((y0 + y1) / 2) * this.k; // y grows upwards in data space
    this.requestDraw();
  }

  sx(x) {
    return x * this.k + this.tx;
  }

  sy(y) {
    return -y * this.k + this.ty;
  }

  zoomAt(px, py, factor) {
    const k = Math.max(this.k0 * 0.5, Math.min(this.k0 * 400, this.k * factor));
    const f = k / this.k;
    this.tx = px - (px - this.tx) * f;
    this.ty = py - (py - this.ty) * f;
    this.k = k;
    this.requestDraw();
  }

  pan(dx, dy) {
    this.tx += dx;
    this.ty += dy;
    this.requestDraw();
  }

  nearest(px, py, radius = 9) {
    if (!this.n) return -1;
    const { x0, y0 } = this.bounds;
    const wx = (px - this.tx) / this.k;
    const wy = -(py - this.ty) / this.k;
    const wr = radius / this.k;
    const G = this.G;
    const cx0 = Math.max(0, Math.floor((wx - wr - x0) / this.gw));
    const cx1 = Math.min(G - 1, Math.floor((wx + wr - x0) / this.gw));
    const cy0 = Math.max(0, Math.floor((wy - wr - y0) / this.gh));
    const cy1 = Math.min(G - 1, Math.floor((wy + wr - y0) / this.gh));
    if (cx1 - cx0 > 40 || cy1 - cy0 > 40) return -1; // zoomed far out: the radius covers too much
    let best = -1;
    let bestD = wr * wr;
    for (let cy = cy0; cy <= cy1; cy++) {
      for (let cx = cx0; cx <= cx1; cx++) {
        const c = cy * G + cx;
        for (let j = this.cellStart[c]; j < this.cellStart[c + 1]; j++) {
          const i = this.cellItems[j];
          if (this.mask && !this.mask[i]) continue;
          const d = (this.xs[i] - wx) ** 2 + (this.ys[i] - wy) ** 2;
          if (d <= bestD) {
            bestD = d;
            best = i;
          }
        }
      }
    }
    return best;
  }

  requestDraw() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  draw() {
    const { ctx } = this;
    const dpr = this.dpr || 1;
    const { w, h: hh } = this.sized || this.size();
    const css = getComputedStyle(this.canvas);
    const surface = css.getPropertyValue('--chart-surface').trim() || '#fcfcfb';
    const ink = css.getPropertyValue('--ink').trim() || '#111';
    const muted = css.getPropertyValue('--chart-muted').trim() || '#b5b3aa';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hh);
    ctx.fillStyle = surface;
    ctx.fillRect(0, 0, w, hh);
    const n = this.n;
    if (!n) return;
    const zoom = this.k / this.k0;
    const base = n > 12000 ? 1.5 : n > 4000 ? 1.9 : n > 800 ? 2.5 : 3.4;
    const r = Math.min(7, base * Math.min(3, zoom ** 0.4));
    const { xs, ys, group, k, tx, ty, mask } = this;
    const groups = this.colors.length || 1;
    // Points are drawn in a few interleaved slices so that no colour group ends up
    // entirely on top of the others where clusters overlap.
    const SLICES = groups > 1 ? 3 : 1;
    const drawSet = (wantMasked, radius) => {
      const square = radius < 1.8;
      for (let slice = 0; slice < SLICES; slice++) {
        for (let g = 0; g < groups; g++) {
          ctx.beginPath();
          let any = false;
          for (let i = slice; i < n; i += SLICES) {
            if (group[i] !== g) continue;
            if (mask && !!mask[i] !== wantMasked) continue;
            const x = xs[i] * k + tx;
            const y = -ys[i] * k + ty;
            if (x < -8 || y < -8 || x > w + 8 || y > hh + 8) continue;
            if (square) ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
            else {
              ctx.moveTo(x + radius, y);
              ctx.arc(x, y, radius, 0, 6.2832);
            }
            any = true;
          }
          if (any) {
            ctx.fillStyle = this.colors[g] || muted;
            ctx.fill();
          }
        }
      }
    };
    if (mask) {
      ctx.globalAlpha = 0.22;
      // everything that does not match, in one muted pass
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        if (mask[i]) continue;
        const x = xs[i] * k + tx;
        const y = -ys[i] * k + ty;
        if (x < -8 || y < -8 || x > w + 8 || y > hh + 8) continue;
        ctx.rect(x - r, y - r, r * 2, r * 2);
      }
      ctx.fillStyle = muted;
      ctx.fill();
      ctx.globalAlpha = 0.95;
      drawSet(true, Math.max(r + 1, 2.6));
    } else {
      ctx.globalAlpha = n > 6000 ? 0.7 : 0.85;
      drawSet(false, r);
    }
    ctx.globalAlpha = 1;

    // cluster labels (largest first, skipping the ones that would overlap)
    if (this.labels.length) {
      ctx.font = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      const placed = [];
      for (const lab of this.labels) {
        const x = lab.x * k + tx;
        const y = -lab.y * k + ty;
        if (x < 0 || y < 0 || x > w || y > hh) continue;
        const tw = ctx.measureText(lab.text).width;
        const box = { x0: x - tw / 2 - 4, x1: x + tw / 2 + 4, y0: y - 9, y1: y + 9 };
        if (placed.some((p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0)) continue;
        placed.push(box);
        ctx.lineWidth = 4;
        ctx.strokeStyle = surface;
        ctx.strokeText(lab.text, x, y);
        ctx.fillStyle = ink;
        ctx.fillText(lab.text, x, y);
      }
    }

    if (this.hover >= 0 && this.hover < n) {
      const x = xs[this.hover] * k + tx;
      const y = -ys[this.hover] * k + ty;
      ctx.beginPath();
      ctx.arc(x, y, Math.max(r + 3, 5), 0, 6.2832);
      ctx.lineWidth = 3;
      ctx.strokeStyle = surface;
      ctx.stroke();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = ink;
      ctx.stroke();
    }
  }

  bind() {
    const c = this.canvas;
    const local = (ev) => {
      const r = c.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };
    c.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const p = local(ev);
      this.zoomAt(p.x, p.y, Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.0016)));
    }, { passive: false });
    c.addEventListener('pointerdown', (ev) => {
      c.setPointerCapture(ev.pointerId);
      const p = local(ev);
      this.pointers.set(ev.pointerId, p);
      this.dragStart = { ...p };
      this.moved = false;
      this.pinch = this.pointers.size === 2 ? this.pinchState() : null;
    });
    c.addEventListener('pointermove', (ev) => {
      const p = local(ev);
      const prev = this.pointers.get(ev.pointerId);
      if (prev) {
        this.pointers.set(ev.pointerId, p);
        if (this.pointers.size === 2 && this.pinch) {
          const now = this.pinchState();
          this.pan(now.cx - this.pinch.cx, now.cy - this.pinch.cy);
          if (this.pinch.d > 0) this.zoomAt(now.cx, now.cy, now.d / this.pinch.d);
          this.pinch = now;
          this.moved = true;
        } else if (this.pointers.size === 1) {
          if (Math.abs(p.x - this.dragStart.x) + Math.abs(p.y - this.dragStart.y) > 4) this.moved = true;
          if (this.moved) {
            this.pan(p.x - prev.x, p.y - prev.y);
            c.classList.add('is-dragging');
            hideTip();
          }
        }
        return;
      }
      if (ev.pointerType === 'mouse') this.setHover(this.nearest(p.x, p.y), ev);
    });
    const up = (ev) => {
      const had = this.pointers.has(ev.pointerId);
      this.pointers.delete(ev.pointerId);
      c.classList.remove('is-dragging');
      if (had && !this.moved && this.pointers.size === 0) {
        const p = local(ev);
        const i = this.nearest(p.x, p.y, ev.pointerType === 'mouse' ? 9 : 18);
        if (i >= 0) {
          if (ev.pointerType === 'mouse' || this.hover === i) this.onClick?.(i, ev);
          else this.setHover(i, ev); // first tap shows the label, second opens
        } else this.setHover(-1, ev);
      }
      this.pinch = null;
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('pointerleave', () => {
      if (!this.pointers.size) this.setHover(-1);
    });
    c.addEventListener('keydown', (ev) => {
      const { w, h: hh } = this.size();
      const step = 60;
      if (ev.key === '+' || ev.key === '=') this.zoomAt(w / 2, hh / 2, 1.3);
      else if (ev.key === '-' || ev.key === '_') this.zoomAt(w / 2, hh / 2, 1 / 1.3);
      else if (ev.key === '0') this.fit();
      else if (ev.key === 'ArrowLeft') this.pan(step, 0);
      else if (ev.key === 'ArrowRight') this.pan(-step, 0);
      else if (ev.key === 'ArrowUp') this.pan(0, step);
      else if (ev.key === 'ArrowDown') this.pan(0, -step);
      else return;
      ev.preventDefault();
    });
  }

  pinchState() {
    const [a, b] = [...this.pointers.values()];
    return { cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) };
  }

  setHover(i, ev) {
    if (i === this.hover) {
      if (i >= 0 && ev) this.onHover?.(i, ev);
      return;
    }
    this.hover = i;
    this.canvas.classList.toggle('is-pointing', i >= 0);
    this.onHover?.(i, ev);
    this.requestDraw();
  }
}

// --------------------------------------------------------------------------- the view

/** The map's own settings in the URL (defaults when absent or unknown). */
function mapOptions(params) {
  const opt = (name, allowed, fallback) => (allowed.includes(params.get(name)) ? params.get(name) : fallback);
  return {
    unit: opt('unit', ['pole', 'mode'], 'pole'),
    method: opt('method', ['pca', 'umap', 'tsne'], 'pca'),
    clusters: params.has('clusters') && [0, 6, 12, 20, 40].includes(Number(params.get('clusters'))) ? Number(params.get('clusters')) : 12,
    colorBy: opt('color', ['layer', 'category', 'cluster'], 'cluster'),
  };
}

export function mount(container, ctx) {
  let state = stateFromParams(ctx.route.params);
  let { unit, method, clusters, colorBy } = mapOptions(ctx.route.params);
  let points = [];
  let clusterList = [];
  let lower = null;
  let focusCluster = null;
  let abort = null;
  let destroyed = false;

  const canvas = h('canvas', { class: 'map-canvas', tabindex: '0', role: 'img', 'aria-label': t('map.aria') });
  const overlay = h('div', { class: 'map-overlay' });
  const legend = h('div', { class: 'map-legend' });
  const status = h('div', { class: 'map-status muted', 'aria-live': 'polite' });
  const notesBox = h('div', { class: 'map-notes' });
  const listBox = h('div', { class: 'map-list' });
  const chipsBox = h('div', { class: 'chips' });

  const scatter = new Scatter(canvas, {
    onHover: (i, ev) => {
      if (i < 0 || !ev) {
        hideTip();
        return;
      }
      const p = points[i];
      const pole = p.pole || poleOf(p.pid) || 'pos';
      const head = `${signChar(pole)} ${displayId(p.pid)}${p.mode_index != null ? ` · ${t('mode.index', { n: p.mode_index })}` : ''}`;
      const meta = [`L${p.layer}`, t(`cat.${p.category || 'none'}`)];
      if (p.cluster != null) meta.push(`${t('map.cluster')} ${p.cluster}`);
      showTipAt(`${head}\n${p.label || ''}\n${meta.join(' · ')}`, ev.clientX, ev.clientY);
    },
    onClick: (i) => {
      const p = points[i];
      hideTip();
      ctx.go(poleHash(p.pid, p.mode_index != null ? { mode: p.mode_index } : null));
    },
  });

  const qInput = h('input', { type: 'search', class: 'input', value: state.q, placeholder: t('map.queryPlaceholder'), 'aria-label': t('map.query'), spellcheck: 'false' });
  const hlInput = h('input', { type: 'search', class: 'input', placeholder: t('map.highlightPlaceholder'), 'aria-label': t('map.highlight'), spellcheck: 'false' });
  const unitSeg = segmented({ name: 'unit', label: t('map.unit'), value: unit, options: [{ value: 'pole', label: t('group.pole') }, { value: 'mode', label: t('group.mode') }], onChange: (v) => { unit = v; commit(); load(); } });
  const methodSel = h('select', { class: 'input input-inline', 'aria-label': t('map.method') }, [['pca', 'PCA'], ['umap', 'UMAP'], ['tsne', 't-SNE']].map(([v, l]) => h('option', { value: v, selected: v === method }, l)));
  methodSel.addEventListener('change', () => { method = methodSel.value; commit(); load(); });
  const clusterSel = h('select', { class: 'input input-inline', 'aria-label': t('map.clusters') }, [0, 6, 12, 20, 40].map((n) => h('option', { value: n, selected: n === clusters }, n === 0 ? t('map.noClusters') : String(n))));
  clusterSel.addEventListener('change', () => { clusters = Number(clusterSel.value); commit(); load(); });
  const colorSeg = segmented({ name: 'color', label: t('map.colorBy'), value: colorBy, options: [{ value: 'cluster', label: t('map.cluster') }, { value: 'layer', label: t('pole.layer') }, { value: 'category', label: t('filters.category') }], onChange: (v) => { colorBy = v; focusCluster = null; commit(); paint(); } });
  const zoomBtn = (name, label, fn) => h('button', { type: 'button', class: 'iconbtn', 'aria-label': label, title: label, onclick: fn }, icon(name, 16));

  const root = h('div', { class: 'map-view' },
    h('div', { class: 'view-head' }, h('h1', null, t('map.title')), h('p', { class: 'muted' }, t('map.intro'))),
    h('div', { class: 'map-controls' },
      h('label', { class: 'field grow' }, h('span', { class: 'field-label' }, t('map.query')), qInput),
      h('label', { class: 'field grow' }, h('span', { class: 'field-label' }, t('map.highlight')), hlInput),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, t('map.unit')), unitSeg),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('map.method')), methodSel),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('map.clusters')), clusterSel),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, t('map.colorBy')), colorSeg)),
    chipsBox,
    h('div', { class: 'map-layout' },
      h('div', { class: 'map-stage' }, canvas, overlay,
        h('div', { class: 'map-zoom no-print' },
          zoomBtn('plus', t('map.zoomIn'), () => { const s = scatter.size(); scatter.zoomAt(s.w / 2, s.h / 2, 1.4); }),
          zoomBtn('minus', t('map.zoomOut'), () => { const s = scatter.size(); scatter.zoomAt(s.w / 2, s.h / 2, 1 / 1.4); }),
          zoomBtn('reset', t('map.zoomReset'), () => scatter.fit()))),
      h('aside', { class: 'map-side' }, status, legend)),
    notesBox,
    listBox);
  container.appendChild(root);
  document.title = `${t('map.title')} · Neuron Atlas`;

  function commit() {
    const hash = buildHash('map', '', { ...filterParams(state), unit: unit === 'pole' ? null : unit, method: method === 'pca' ? null : method, clusters: clusters === 12 ? null : clusters, color: colorBy === 'cluster' ? null : colorBy });
    ctx.go(hash, { replace: true, silent: true });
  }

  function renderChips() {
    const items = filterChips(state).map((c) => h('span', { class: 'chip chip-static' }, c.label));
    replace(chipsBox,
      items,
      h('a', { class: 'linkish', href: buildHash('search', '', stateToParams(state)) }, hasFilters(state) ? t('map.editFilters') : t('map.addFilters')));
  }

  // ---- colours and legend

  function paint() {
    const n = points.length;
    const dark = isDark();
    const group = new Uint16Array(n);
    let colors = [];
    const entries = [];
    if (colorBy === 'category') {
      const pal = dark ? CAT_DARK : CAT_LIGHT;
      colors = CATEGORIES.map((c) => pal[c]);
      const counts = new Array(CATEGORIES.length).fill(0);
      for (let i = 0; i < n; i++) {
        const g = Math.max(0, CATEGORIES.indexOf(points[i].category || 'none'));
        group[i] = g;
        counts[g]++;
      }
      CATEGORIES.forEach((c, g) => { if (counts[g]) entries.push({ color: colors[g], label: t(`cat.${c}`), count: counts[g] }); });
    } else if (colorBy === 'layer') {
      const layers = [...new Set(points.map((p) => p.layer))].filter((x) => x != null).sort((a, b) => a - b);
      const lo = layers[0] ?? 0;
      const hi = layers[layers.length - 1] ?? 1;
      const ramp = dark ? RAMP_DARK : RAMP_LIGHT;
      const index = new Map(layers.map((l, i) => [l, i]));
      colors = layers.map((l) => rampColor(ramp, hi > lo ? (l - lo) / (hi - lo) : 0.5));
      const counts = new Array(layers.length).fill(0);
      for (let i = 0; i < n; i++) {
        const g = index.get(points[i].layer) ?? 0;
        group[i] = g;
        counts[g]++;
      }
      layers.forEach((l, g) => entries.push({ color: colors[g], label: `${t('pole.layer')} ${l}`, count: counts[g] }));
    } else {
      const pal = dark ? CLUSTER_DARK : CLUSTER_LIGHT;
      const neutral = dark ? '#6b6a65' : '#b5b3aa';
      colors = [...pal, neutral];
      const slots = assignClusterSlots(clusterList, pal.length);
      for (let i = 0; i < n; i++) {
        const c = points[i].cluster;
        group[i] = c != null && slots.has(c) ? slots.get(c) : pal.length;
      }
      for (const c of [...clusterList].sort((a, b) => (b.size || 0) - (a.size || 0))) {
        entries.push({ color: pal[slots.get(c.id)], label: c.label || `${t('map.cluster')} ${c.id}`, count: c.size, cluster: c.id });
      }
      const loose = points.reduce((s, p) => s + (p.cluster == null || !slots.has(p.cluster) ? 1 : 0), 0);
      if (loose && clusterList.length) entries.push({ color: neutral, label: t('map.noCluster'), count: loose });
    }
    scatter.setColors(group, colors);
    scatter.setLabels(clusterList.length ? [...clusterList].sort((a, b) => (b.size || 0) - (a.size || 0)).map((c) => ({ x: c.x, y: c.y, text: shorten(c.label || `${t('map.cluster')} ${c.id}`, 34), size: c.size })) : []);
    renderLegend(entries);
    applyHighlight();
  }

  function shorten(text, n) {
    const s = String(text);
    return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
  }

  function renderLegend(entries) {
    if (!points.length) {
      replace(legend);
      return;
    }
    const title = colorBy === 'cluster' ? t('map.legendCluster') : colorBy === 'layer' ? t('map.legendLayer') : t('map.legendCategory');
    const rows = entries.map((e) => {
      const inner = [h('span', { class: 'legend-dot', style: { backgroundColor: e.color } }), h('span', { class: 'legend-label' }, e.label), h('span', { class: 'fcount' }, fmtInt(e.count))];
      if (e.cluster == null) return h('li', { class: 'legend-row' }, inner);
      const btn = h('button', { type: 'button', class: 'legend-row legend-btn', 'aria-pressed': String(focusCluster === e.cluster), title: t('map.focusCluster') }, inner);
      btn.addEventListener('click', () => {
        focusCluster = focusCluster === e.cluster ? null : e.cluster;
        paint();
      });
      return h('li', null, btn);
    });
    replace(legend,
      h('h2', { class: 'legend-title' }, title),
      colorBy === 'cluster' && !clusterList.length ? h('p', { class: 'muted small' }, t('map.noClustersHint')) : null,
      colorBy === 'cluster' && clusterList.length ? h('p', { class: 'muted small' }, t('map.clusterHint')) : null,
      h('ul', { class: 'legend-list' }, rows));
  }

  // ---- highlight

  function applyHighlight() {
    const q = hlInput.value.trim().toLowerCase();
    const n = points.length;
    if (!q && focusCluster == null) {
      scatter.setMask(null, 0);
      setStatus(null);
      renderList(null);
      return;
    }
    if (!lower) lower = points.map((p) => `${p.label || ''} ${p.pid}`.toLowerCase());
    const terms = q.split(/\s+/).filter(Boolean);
    const mask = new Uint8Array(n);
    let count = 0;
    for (let i = 0; i < n; i++) {
      if (focusCluster != null && points[i].cluster !== focusCluster) continue;
      let ok = true;
      for (const term of terms) {
        if (!lower[i].includes(term)) {
          ok = false;
          break;
        }
      }
      if (ok) {
        mask[i] = 1;
        count++;
      }
    }
    scatter.setMask(mask, count);
    setStatus(count);
    renderList(mask);
  }

  function setStatus(matchCount) {
    if (!points.length) {
      replace(status);
      return;
    }
    replace(status,
      h('strong', null, fmtInt(points.length)), ' ', unit === 'mode' ? t('unit.modes', { n: points.length }) : t('unit.poles', { n: points.length }),
      matchCount != null ? h('span', null, ` · ${t('map.highlighted', { n: fmtInt(matchCount) })}`) : null);
  }

  function renderList(mask) {
    if (!points.length) {
      replace(listBox);
      return;
    }
    const rows = [];
    for (let i = 0; i < points.length && rows.length < 200; i++) {
      if (mask && !mask[i]) continue;
      const p = points[i];
      rows.push(h('tr', null,
        h('td', null, h('a', { class: 'pid', href: poleHash(p.pid, p.mode_index != null ? { mode: p.mode_index } : null) }, h('span', { class: `sign sign-${p.pole || poleOf(p.pid) || 'pos'}` }, signChar(p.pole || poleOf(p.pid))), h('span', { class: 'idtext' }, displayId(p.pid)))),
        unit === 'mode' ? h('td', { class: 'num' }, p.mode_index ?? '') : null,
        h('td', null, p.label || ''),
        h('td', { class: 'num' }, p.layer ?? ''),
        h('td', null, t(`cat.${p.category || 'none'}`)),
        h('td', { class: 'num' }, p.cluster ?? '')));
    }
    replace(listBox, h('details', { class: 'graph-list', open: !!mask && rows.length > 0 && rows.length <= 60 },
      h('summary', null, mask ? t('map.listMatches', { n: fmtInt(rows.length) }) : t('map.listAll', { n: fmtInt(Math.min(200, points.length)) })),
      h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact' },
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('unit.pole')), unit === 'mode' ? h('th', { scope: 'col', class: 'num' }, t('group.mode')) : null, h('th', { scope: 'col' }, t('graph.label')), h('th', { scope: 'col', class: 'num' }, t('pole.layer')), h('th', { scope: 'col' }, t('filters.category')), h('th', { scope: 'col', class: 'num' }, t('map.cluster')))),
        h('tbody', null, rows)))));
  }

  // ---- loading

  async function load() {
    abort?.abort();
    abort = new AbortController();
    hideTip();
    renderChips();
    replace(overlay, h('div', { class: 'map-loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), t('map.loading')));
    replace(notesBox);
    try {
      const res = await api('/api/v1/map', { params: { ...filterParams(state), unit, method, clusters }, signal: abort.signal });
      if (destroyed) return;
      points = Array.isArray(res.points) ? res.points.filter((p) => p && p.pid && Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y))) : [];
      lower = null;
      focusCluster = null;
      clusterList = Array.isArray(res.clusters) ? res.clusters.filter((c) => c && c.id != null) : [];
      if (!clusterList.length && points.some((p) => p.cluster != null)) {
        // the response carries cluster ids but no summary: derive centroids here
        const acc = new Map();
        for (const p of points) {
          if (p.cluster == null) continue;
          const a = acc.get(p.cluster) || { id: p.cluster, size: 0, x: 0, y: 0, label: '' };
          a.size++;
          a.x += Number(p.x);
          a.y += Number(p.y);
          acc.set(p.cluster, a);
        }
        clusterList = [...acc.values()].map((a) => ({ ...a, x: a.x / a.size, y: a.y / a.size }));
      }
      scatter.setData(points);
      scatter.resize();
      const noteItems = (res.notes || []).filter(Boolean);
      if (!points.length) {
        replace(overlay, notice('empty', t('map.emptyTitle'), h('p', null, t('map.emptyText')), noteItems.length ? h('ul', { class: 'notes' }, noteItems.map((x) => h('li', null, x))) : null));
      } else {
        replace(overlay);
        replace(notesBox, noteItems.length ? h('ul', { class: 'notes' }, noteItems.map((x) => h('li', null, x))) : null);
      }
      paint();
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      points = [];
      scatter.setData(points);
      replace(legend);
      replace(status);
      replace(listBox);
      replace(overlay, errorBox(err, load));
    }
  }

  const qChanged = debounce(() => {
    state = { ...state, q: qInput.value };
    commit();
    load();
  }, 400);
  qInput.addEventListener('input', qChanged);
  qInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') qChanged.flush();
  });
  hlInput.addEventListener('input', debounce(applyHighlight, 120));

  const ro = 'ResizeObserver' in window ? new ResizeObserver(() => scatter.resize()) : null;
  ro?.observe(canvas);
  const onTheme = () => paint();
  window.addEventListener('atlas:theme', onTheme);
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  mq.addEventListener?.('change', onTheme);

  scatter.resize();
  load();

  return {
    update(route) {
      const next = stateFromParams(route.params);
      const opts = mapOptions(route.params);
      const filtersChanged = JSON.stringify(filterParams(next)) !== JSON.stringify(filterParams(state));
      const reload = filtersChanged || opts.unit !== unit || opts.method !== method || opts.clusters !== clusters;
      const recolour = opts.colorBy !== colorBy;
      state = next;
      ({ unit, method, clusters, colorBy } = opts);
      unitSeg._set(unit);
      methodSel.value = method;
      clusterSel.value = String(clusters);
      colorSeg._set(colorBy);
      if (filtersChanged) qInput.value = state.q;
      if (reload) load();
      else if (recolour) {
        focusCluster = null;
        paint();
      }
    },
    destroy() {
      destroyed = true;
      abort?.abort();
      ro?.disconnect();
      hideTip();
      window.removeEventListener('atlas:theme', onTheme);
      mq.removeEventListener?.('change', onTheme);
    },
  };
}
