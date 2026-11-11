// The neuron view: both poles side by side and the circuit neighbourhood as a layered graph.

import { api, isAbort } from './api.js';
import { h, svg, icon, replace, skeleton, bar, fmtInt, fmtNum, fmtPct, fmtSig, displayId, clampText, checkClamps } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox } from './ui.js';
import { neuronHash, poleHash } from './state.js';
import { catBadge, sampleBadge, mirrorBadge, poleLink, unitLink } from './parts.js';
import { renderCircuit } from './pole.js';

const NODE_W = 158;
const NODE_H = 40;
const COL_GAP = 72;
const ROW_GAP = 10;
const PAD = 14;
const HEAD = 26;
const MAX_PER_COL = 14;

function layerOf(node) {
  if (typeof node.layer === 'number') return node.layer;
  const m = /^L(\d+)/.exec(node.id || '');
  return m ? Number(m[1]) : -1;
}

function truncate(text, n) {
  const s = String(text || '');
  return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
}

/** Layered node-link picture: one column per layer, earlier layers on the left. */
export function renderGraph(graph, centerId) {
  const nodes = (graph.nodes || []).filter((n) => n && n.id);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = (graph.edges || []).filter((e) => e && byId.has(e.source) && byId.has(e.target));
  if (!nodes.length) return null;

  const strength = new Map();
  let maxW = 0;
  for (const e of edges) {
    const w = Math.abs(Number(e.weight) || 0);
    if (w > maxW) maxW = w;
    strength.set(e.source, Math.max(strength.get(e.source) || 0, w));
    strength.set(e.target, Math.max(strength.get(e.target) || 0, w));
  }
  const layers = [...new Set(nodes.map(layerOf))].sort((a, b) => a - b);
  const columns = layers.map((layer) => {
    const all = nodes.filter((n) => layerOf(n) === layer).sort((a, b) =>
      (a.id === centerId ? -1 : 0) - (b.id === centerId ? -1 : 0) || (a.distance ?? 9) - (b.distance ?? 9) || (strength.get(b.id) || 0) - (strength.get(a.id) || 0) || String(a.id).localeCompare(String(b.id)));
    return { layer, shown: all.slice(0, MAX_PER_COL), hidden: all.length - Math.min(all.length, MAX_PER_COL) };
  });
  const rows = Math.max(...columns.map((c) => c.shown.length + (c.hidden ? 1 : 0)));
  const width = PAD * 2 + columns.length * NODE_W + (columns.length - 1) * COL_GAP;
  const height = HEAD + PAD + rows * (NODE_H + ROW_GAP);
  const pos = new Map();
  columns.forEach((col, ci) => {
    const colH = (col.shown.length + (col.hidden ? 1 : 0)) * (NODE_H + ROW_GAP);
    const top = HEAD + PAD / 2 + (rows * (NODE_H + ROW_GAP) - colH) / 2;
    col.x = PAD + ci * (NODE_W + COL_GAP);
    col.top = top;
    col.shown.forEach((n, ri) => pos.set(n.id, { x: col.x, y: top + ri * (NODE_H + ROW_GAP), col: ci }));
  });

  const root = svg('svg', { class: 'graph', viewBox: `0 0 ${width} ${height}`, width, height, role: 'group', 'aria-label': t('graph.aria', { n: nodes.length, e: edges.length }) });
  const edgeLayer = svg('g', { class: 'graph-edges' });
  const nodeLayer = svg('g', { class: 'graph-nodes' });
  const edgeEls = [];
  for (const e of edges) {
    const a = pos.get(e.source);
    const b = pos.get(e.target);
    if (!a || !b) continue;
    let d;
    const y1 = a.y + NODE_H / 2;
    const y2 = b.y + NODE_H / 2;
    if (a.col < b.col) {
      const x1 = a.x + NODE_W;
      const x2 = b.x;
      const dx = (x2 - x1) * 0.5;
      d = `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
    } else if (a.col > b.col) {
      const x1 = a.x;
      const x2 = b.x + NODE_W;
      const dx = (x1 - x2) * 0.5;
      d = `M${x1} ${y1}C${x1 - dx} ${y1} ${x2 + dx} ${y2} ${x2} ${y2}`;
    } else {
      const x = a.x + NODE_W;
      d = `M${x} ${y1}C${x + 34} ${y1} ${x + 34} ${y2} ${x} ${y2}`;
    }
    const w = Math.abs(Number(e.weight) || 0);
    const negative = e.sign === '-' || e.sign === '−' || Number(e.weight) < 0;
    const el = svg('path', {
      d, class: `edge ${negative ? 'edge-neg' : 'edge-pos'}`, 'stroke-width': (0.9 + (maxW > 0 ? (w / maxW) * 2.6 : 0)).toFixed(2), fill: 'none',
      dataset: { s: e.source, t: e.target, tip: `${displayId(e.source)} → ${displayId(e.target)}${e.kind ? ` · ${e.kind}` : ''} · ${negative ? '−' : '+'}${fmtSig(w, 3)}` },
    });
    edgeEls.push(el);
    edgeLayer.appendChild(el);
  }

  const nodeEls = new Map();
  columns.forEach((col) => {
    root.appendChild(svg('text', { class: 'graph-col', x: col.x + NODE_W / 2, y: 16, 'text-anchor': 'middle' }, col.layer >= 0 ? `${t('pole.layer')} ${col.layer}` : '?'));
    col.shown.forEach((n) => {
      const p = pos.get(n.id);
      const isCenter = n.id === centerId;
      const cls = `gnode ${isCenter ? 'gnode-center' : ''} ${n.in_atlas ? 'gnode-in' : 'gnode-out'} ${n.mirrored ? 'gnode-mirrored' : ''}`.trim();
      const tip = `${displayId(n.id)}${n.label ? `\n${n.label}` : ''}${n.in_atlas ? '' : `\n(${t('unit.notInAtlas')})`}`;
      const inner = [
        svg('rect', { x: p.x, y: p.y, width: NODE_W, height: NODE_H, rx: 6 }),
        svg('text', { class: 'gnode-id', x: p.x + 9, y: p.y + 16 }, displayId(n.id)),
        svg('text', { class: 'gnode-label', x: p.x + 9, y: p.y + 31 }, truncate(n.label || (n.in_atlas ? '' : t('unit.notInAtlasShort')), 27)),
      ];
      const el = n.in_atlas && !isCenter
        ? svg('a', { class: cls, href: neuronHash(n.id), dataset: { id: n.id, tip }, 'aria-label': `${displayId(n.id)} ${n.label || ''}` }, inner)
        : svg('g', { class: cls, dataset: { id: n.id, tip } }, inner);
      nodeEls.set(n.id, el);
      nodeLayer.appendChild(el);
      const hot = () => {
        root.classList.add('is-dim');
        el.classList.add('is-hot');
        for (const edge of edgeEls) {
          if (edge.dataset.s === n.id || edge.dataset.t === n.id) {
            edge.classList.add('is-hot');
            nodeEls.get(edge.dataset.s === n.id ? edge.dataset.t : edge.dataset.s)?.classList.add('is-hot');
          }
        }
      };
      const cool = () => {
        root.classList.remove('is-dim');
        root.querySelectorAll('.is-hot').forEach((x) => x.classList.remove('is-hot'));
      };
      el.addEventListener('pointerenter', hot);
      el.addEventListener('pointerleave', cool);
      el.addEventListener('focus', hot);
      el.addEventListener('blur', cool);
    });
    if (col.hidden) {
      root.appendChild(svg('text', { class: 'graph-more', x: col.x + NODE_W / 2, y: col.top + col.shown.length * (NODE_H + ROW_GAP) + 18, 'text-anchor': 'middle' }, t('graph.more', { n: col.hidden })));
    }
  });
  root.append(edgeLayer, nodeLayer);
  return root;
}

function graphTables(graph) {
  const nodes = (graph.nodes || []).filter((n) => n && n.id);
  const edges = (graph.edges || []).filter((e) => e && e.source && e.target);
  return h('details', { class: 'graph-list' },
    h('summary', null, t('graph.asList', { n: fmtInt(nodes.length), e: fmtInt(edges.length) })),
    h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('graph.node')), h('th', { scope: 'col', class: 'num' }, t('pole.layer')), h('th', { scope: 'col', class: 'num' }, t('graph.distance')), h('th', { scope: 'col' }, t('graph.label')))),
      h('tbody', null, nodes.map((n) => h('tr', null, h('td', null, unitLink(n.id, n.in_atlas)), h('td', { class: 'num' }, layerOf(n) >= 0 ? fmtInt(layerOf(n)) : '–'), h('td', { class: 'num' }, n.distance ?? '–'), h('td', null, n.label || '')))))),
    edges.length ? h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('graph.source')), h('th', { scope: 'col' }, t('graph.target')), h('th', { scope: 'col' }, t('graph.kind')), h('th', { scope: 'col', class: 'num' }, t('circuit.weight')))),
      h('tbody', null, edges.map((e) => h('tr', null, h('td', { class: 'mono' }, displayId(e.source)), h('td', { class: 'mono' }, displayId(e.target)), h('td', null, e.kind || ''), h('td', { class: 'num' }, `${e.sign === '-' || Number(e.weight) < 0 ? '−' : '+'}${fmtSig(Math.abs(Number(e.weight) || 0), 3)}`)))))) : null);
}

function poleCard(p) {
  const modes = p.modes || [];
  const maxShare = Math.max(...modes.map((m) => m.share || 0), 0.0001);
  return h('article', { class: `pole-card pole-card-${p.pole}` },
    h('header', { class: 'pole-card-head' },
      h('h2', null, poleLink(p.pid, p.pole)),
      h('span', { class: 'muted' }, p.pole === 'neg' ? t('pole.neg') : t('pole.pos')),
      h('div', { class: 'pole-badges' }, catBadge(p.category, p.category_words), sampleBadge(p), mirrorBadge(p))),
    p.headline ? h('p', { class: 'pole-headline' }, p.headline) : null,
    p.description && p.description !== p.headline ? clampText(p.description, { lines: 4, moreLabel: t('more'), lessLabel: t('less') }) : null,
    h('dl', { class: 'kvs kvs-compact' },
      p.n_modes != null ? h('div', { class: 'kv' }, h('dt', null, t('pole.modes')), h('dd', null, fmtInt(p.n_modes))) : null,
      p.n_subcircuits != null ? h('div', { class: 'kv' }, h('dt', null, t('pole.subcircuits')), h('dd', null, fmtInt(p.n_subcircuits))) : null,
      p.simulatability != null ? h('div', { class: 'kv' }, h('dt', null, t('pole.simulatability')), h('dd', null, fmtPct(p.simulatability))) : null,
      p.main_damage_share != null ? h('div', { class: 'kv' }, h('dt', null, t('pole.mainDamage')), h('dd', null, fmtPct(p.main_damage_share))) : null),
    modes.length ? h('ol', { class: 'mini-modes' }, modes.map((m) => h('li', null,
      h('a', { href: poleHash(p.pid, { mode: m.index }) }, m.label),
      h('span', { class: 'mini-share' }, bar(m.share, { max: maxShare }), h('span', { class: 'num' }, fmtPct(m.share)))))) : h('p', { class: 'muted' }, t('neuron.noModes')),
    h('p', { class: 'pole-card-foot' }, h('a', { class: 'btn btn-small', href: poleHash(p.pid) }, t('neuron.openPole'), icon('chevron', 14))));
}

export function mount(container, ctx) {
  let did = ctx.route.arg;
  let depth = 1;
  let maxNodes = 60;
  let abort = new AbortController();
  let destroyed = false;
  const root = h('div', { class: 'neuron-view' });
  const head = h('header', { class: 'pole-head' });
  const polesBox = h('div', { class: 'pole-cards' });
  const circuitBox = h('section', { class: 'neuron-circuit' });
  const graphBox = h('section', { class: 'graph-section' });
  root.append(
    h('nav', { class: 'crumbs no-print', 'aria-label': t('nav.breadcrumb') }, h('a', { href: ctx.lastSearchHash(), class: 'crumb-back' }, icon('back', 15), t('pole.backToSearch'))),
    head, polesBox, circuitBox, graphBox);
  container.appendChild(root);

  async function loadNeuron() {
    document.title = `${displayId(did)} · Neuron Atlas`;
    replace(head, h('div', { class: 'pole-title' }, h('h1', { class: 'pole-id' }, h('span', { class: 'idtext' }, displayId(did)))), skeleton(2));
    replace(polesBox, skeleton(6, 'sk-card'), skeleton(6, 'sk-card'));
    replace(circuitBox);
    try {
      const n = await api(`/api/v1/neurons/${encodeURIComponent(did)}`, { params: { include: 'circuit' }, signal: abort.signal });
      if (destroyed) return;
      did = n.did || did;
      const summary = n.circuit_summary && typeof n.circuit_summary === 'object' ? Object.entries(n.circuit_summary).filter(([k]) => !['layer', 'expert', 'neuron'].includes(k)) : [];
      replace(head,
        h('div', { class: 'pole-title' },
          h('h1', { class: 'pole-id' }, h('span', { class: 'idtext' }, n.display || displayId(did))),
          h('div', { class: 'pole-badges' }, n.in_atlas_index ? null : h('span', { class: 'badge', title: t('neuron.notInIndexTip') }, t('neuron.notInIndex')))),
        n.circuit_label ? h('p', { class: 'pole-headline' }, h('span', { class: 'label-inline' }, t('neuron.circuitLabel')), ' ', n.circuit_label) : null,
        h('dl', { class: 'kvs' },
          h('div', { class: 'kv' }, h('dt', null, t('pole.layer')), h('dd', null, fmtInt(n.layer))),
          h('div', { class: 'kv' }, h('dt', null, t('pole.expert')), h('dd', null, n.expert == null ? t('pole.shared') : fmtInt(n.expert))),
          h('div', { class: 'kv' }, h('dt', null, t('pole.neuron')), h('dd', null, fmtInt(n.neuron))),
          summary.map(([k, v]) => h('div', { class: 'kv' }, h('dt', null, k), h('dd', null, v && typeof v === 'object' ? Object.entries(v).map(([a, b]) => `${a} ${b}`).join(' · ') : typeof v === 'number' ? fmtNum(v, 3) : String(v))))));
      const poles = n.poles || [];
      replace(polesBox, poles.length ? poles.map(poleCard) : notice('info', t('neuron.noPoles'), h('p', null, t('neuron.noPolesText'))));
      const circuit = renderCircuit(n.circuit);
      replace(circuitBox, circuit ? [h('h2', { class: 'section-title' }, t('tab.circuit')), circuit] : null);
      const notes = (n.notes || []).filter(Boolean);
      if (notes.length) circuitBox.appendChild(h('ul', { class: 'notes' }, notes.map((x) => h('li', null, x))));
      checkClamps(root);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(head, h('div', { class: 'pole-title' }, h('h1', { class: 'pole-id' }, h('span', { class: 'idtext' }, displayId(did)))), errorBox(err, loadNeuron));
      replace(polesBox);
    }
  }

  let graphAbort = null;
  async function loadGraph() {
    graphAbort?.abort();
    graphAbort = new AbortController();
    const depthSel = h('select', { class: 'input input-inline', 'aria-label': t('graph.depth') }, [1, 2, 3].map((n) => h('option', { value: n, selected: n === depth }, String(n))));
    depthSel.addEventListener('change', () => { depth = Number(depthSel.value); loadGraph(); });
    const nodesSel = h('select', { class: 'input input-inline', 'aria-label': t('graph.maxNodes') }, [30, 60, 120, 300].map((n) => h('option', { value: n, selected: n === maxNodes }, String(n))));
    nodesSel.addEventListener('change', () => { maxNodes = Number(nodesSel.value); loadGraph(); });
    const body = h('div', { class: 'graph-body' }, skeleton(5, 'sk-panel'));
    replace(graphBox,
      h('div', { class: 'panel-tools' },
        h('h2', { class: 'section-title' }, t('graph.title')),
        h('div', { class: 'panel-tools-right no-print' },
          h('label', { class: 'field-inline' }, h('span', null, t('graph.depth')), depthSel),
          h('label', { class: 'field-inline' }, h('span', null, t('graph.maxNodes')), nodesSel))),
      h('p', { class: 'muted small' }, t('graph.hint')),
      body);
    try {
      const g = await api(`/api/v1/graph/${encodeURIComponent(did)}`, { params: { depth, max_nodes: maxNodes }, signal: graphAbort.signal });
      if (destroyed) return;
      const notes = (g.notes || []).filter(Boolean);
      const picture = renderGraph(g, g.center || did);
      if (!picture) {
        replace(body, notice('empty', t('graph.emptyTitle'), h('p', null, t('graph.emptyText')), notes.length ? h('ul', { class: 'notes' }, notes.map((x) => h('li', null, x))) : null));
        return;
      }
      replace(body,
        h('div', { class: 'graph-legend' },
          h('span', { class: 'legend-item' }, h('span', { class: 'legend-line edge-pos' }), t('graph.positive')),
          h('span', { class: 'legend-item' }, h('span', { class: 'legend-line edge-neg' }), t('graph.negative')),
          h('span', { class: 'legend-item' }, h('span', { class: 'legend-node gnode-in' }), t('graph.inAtlas')),
          h('span', { class: 'legend-item' }, h('span', { class: 'legend-node gnode-out' }), t('graph.notInAtlas'))),
        h('div', { class: 'graph-scroll', tabindex: '0', role: 'region', 'aria-label': t('graph.title') }, picture),
        notes.length ? h('ul', { class: 'notes' }, notes.map((x) => h('li', null, x))) : null,
        graphTables(g));
      // bring the centre neuron into view when the picture is wider than the page
      const scroller = body.querySelector('.graph-scroll');
      const centre = picture.querySelector('.gnode-center rect');
      if (scroller && centre && scroller.scrollWidth > scroller.clientWidth) {
        const cx = Number(centre.getAttribute('x')) + NODE_W / 2;
        scroller.scrollLeft = Math.max(0, cx - scroller.clientWidth / 2);
      }
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(body, errorBox(err, loadGraph));
    }
  }

  const reload = () => {
    abort.abort();
    abort = new AbortController();
    loadNeuron();
    loadGraph();
  };
  const onMirrored = () => { if (!destroyed) reload(); };
  window.addEventListener('atlas:mirrored', onMirrored);
  reload();

  return {
    update(route) {
      if (route.arg !== did) {
        did = route.arg;
        window.scrollTo(0, 0);
        reload();
      }
    },
    destroy() {
      destroyed = true;
      abort.abort();
      graphAbort?.abort();
      window.removeEventListener('atlas:mirrored', onMirrored);
    },
  };
}
