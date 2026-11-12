// Small pieces several views share: badges, id links, filter labels, heatmaps.

import { h, displayId, fmtInt, fmtNum, fmtPct, poleLabel, bar } from './dom.js';
import { t } from './i18n.js';
import { poleHash, neuronHash } from './state.js';

/**
 * The filters of a search state in words: [{ label, patch }], where `patch` removes that filter.
 * One wording for the search chips, the map chips, the region summary and the filters the
 * server read out of the query text.  Fields missing from `s` count as unset.
 */
export function filterChips(s) {
  const items = [];
  const add = (label, patch) => items.push({ label, patch });
  const list = (key) => (Array.isArray(s[key]) ? s[key] : []);
  if (s.match && s.match !== 'auto') add(`${t('search.match')}: ${t(`match.${s.match}`, null, s.match)}`, { match: 'auto' });
  const scope = list('scope');
  if (scope.length && !(scope.length === 2 && scope.includes('labels') && scope.includes('deep'))) add(`${t('search.scope')}: ${scope.map((x) => t(`scope.${x}`, null, x)).join(' + ')}`, { scope: ['labels', 'deep'] });
  for (const c of list('cat')) add(t(`cat.${c}`, null, c), { cat: list('cat').filter((x) => x !== c) });
  for (const l of list('layer')) add(`${t('pole.layer')} ${l}`, { layer: list('layer').filter((x) => x !== l) });
  if (s.layer_min != null) add(`${t('pole.layer')} ≥ ${s.layer_min}`, { layer_min: null });
  if (s.layer_max != null) add(`${t('pole.layer')} ≤ ${s.layer_max}`, { layer_max: null });
  if (s.pole) add(s.pole === 'pos' ? `+ ${t('pole.posShort')}` : `− ${t('pole.negShort')}`, { pole: '' });
  if (s.sample && s.sample !== 'all') add(t(`sample.${s.sample}.tip`, null, s.sample), { sample: 'all' });
  for (const f of list('has')) add(`${t('filters.hasShort')}: ${t(`has.${f}`, null, f)}`, { has: list('has').filter((x) => x !== f) });
  for (const f of list('lacks')) add(`${t('filters.lacksShort')}: ${t(`has.${f}`, null, f)}`, { lacks: list('lacks').filter((x) => x !== f) });
  const range = (label, lo, hi, fmt) => {
    if (s[lo] == null && s[hi] == null) return;
    const a = s[lo] == null ? '' : fmt(s[lo]);
    const b = s[hi] == null ? '' : fmt(s[hi]);
    add(`${label} ${a && b ? `${a}–${b}` : a ? `≥ ${a}` : `≤ ${b}`}`, { [lo]: null, [hi]: null });
  };
  range(t('filters.modes'), 'modes_min', 'modes_max', (v) => fmtInt(v));
  range(t('filters.sim'), 'sim_min', 'sim_max', (v) => fmtNum(v, 2));
  range(t('filters.damage'), 'damage_min', 'damage_max', (v) => fmtNum(v, 2));
  if (s.verdict) add(t(`verdict.${s.verdict}`, null, s.verdict), { verdict: '' });
  if (list('expert').length) add(`${t('pole.expert')} ${list('expert').join(', ')}`, { expert: [] });
  if (list('neuron').length) add(`${t('pole.neuron')} ${list('neuron').join(', ')}`, { neuron: [] });
  const ids = list('ids');
  if (ids.length) add(`IDs: ${ids.slice(0, 3).map(displayId).join(', ')}${ids.length > 3 ? ' …' : ''}`, { ids: [] });
  return items;
}

export function catBadge(category, words) {
  const cat = category || 'none';
  return h('span', { class: `cat cat-${cat}`, title: t(`cat.${cat}.tip`) }, h('span', { class: 'cat-dot', 'aria-hidden': 'true' }), t(`cat.${cat}`, null, words || cat));
}

export function sampleBadge(hit) {
  if (!hit.in_public_sample) return null;
  return h('span', { class: 'badge badge-public', title: t('badge.publicTip') }, t('badge.public'));
}

/** What of this pole is already in the local mirror. */
export function mirrorBadge(hit) {
  const available = hit.available || [];
  const mirrored = hit.mirrored || [];
  if (!available.length) return null;
  const tip = `${t('badge.mirroredTip')}: ${mirrored.length ? mirrored.join(', ') : '–'}\n${t('badge.availableTip')}: ${available.join(', ')}`;
  if (!mirrored.length) return h('span', { class: 'badge badge-index', title: tip }, t('badge.indexOnly'));
  const full = available.every((a) => mirrored.includes(a));
  return h('span', { class: `badge ${full ? 'badge-full' : 'badge-part'}`, title: tip }, full ? t('badge.mirrored') : t('badge.mirroredPart', { n: mirrored.length, m: available.length }));
}

export function kindBadge(kind, pole) {
  if (kind === 'closed_gate') return h('span', { class: 'kind kind-closed', title: t('kind.closedTip') }, `${pole === 'neg' ? 'Q4' : 'Q3'} · ${t('kind.closed')}`);
  if (kind === 'residue' || kind === 'unassigned') return h('span', { class: 'kind kind-residue', title: t('kind.residueTip') }, t('kind.residue'));
  return h('span', { class: 'kind kind-open' }, t('kind.open'));
}

export function poleLink(pid, pole, params) {
  return h('a', { class: 'pid', href: poleHash(pid, params) }, poleLabel(pid, pole));
}

/** A neuron id; a link when the neuron has a page in this release. */
export function unitLink(unit, inAtlas) {
  const text = displayId(unit);
  if (inAtlas) return h('a', { class: 'unit mono', href: neuronHash(unit) }, text);
  return h('span', { class: 'unit mono unit-out', title: t('unit.notInAtlas') }, text);
}

/** number + bar cell for tables */
export function barCell(value, { max = 1, text, cls = '' } = {}) {
  return h('td', { class: `num barcell ${cls}`.trim() }, h('span', { class: 'barcell-num' }, text ?? fmtPct(value)), bar(value, { max }));
}

/**
 * A square heatmap of a similarity matrix (values 0..1), one sequential hue.
 * labels[i] is used for the tooltip; the axes print the mode index.
 */
export function heatmap(matrix, labels, { title, prefix = 'M' } = {}) {
  const n = Array.isArray(matrix) ? matrix.length : 0;
  if (!n) return null;
  const showNumbers = n <= 14;
  const table = h('table', { class: `heatmap ${showNumbers ? 'heatmap-num' : ''}`, 'aria-label': title || '' });
  const head = h('tr', null, h('th', { class: 'hm-corner' }));
  for (let j = 0; j < n; j++) head.appendChild(h('th', { scope: 'col', class: 'hm-axis', title: labels[j] || '' }, `${prefix}${j}`));
  table.appendChild(h('thead', null, head));
  const body = h('tbody');
  for (let i = 0; i < n; i++) {
    const row = h('tr', null, h('th', { scope: 'row', class: 'hm-axis', title: labels[i] || '' }, `${prefix}${i}`));
    for (let j = 0; j < n; j++) {
      const v = matrix[i] ? matrix[i][j] : null;
      const ok = typeof v === 'number' && Number.isFinite(v);
      const level = ok ? Math.max(0, Math.min(1, v)) : 0;
      const cell = h('td', {
        class: `hm-cell ${ok ? '' : 'hm-na'} ${level > 0.55 ? 'hm-dark' : ''}`.trim(),
        style: ok ? { '--v': level.toFixed(3) } : null,
        dataset: { tip: `${prefix}${i} × ${prefix}${j}: ${ok ? fmtNum(v, 2) : '–'}${labels[i] ? `\n${prefix}${i}: ${labels[i]}` : ''}${labels[j] && i !== j ? `\n${prefix}${j}: ${labels[j]}` : ''}` },
      }, showNumbers && ok ? fmtNum(v, 2).replace(/^0([.,])/, '$1') : '');
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  table.appendChild(body);
  return h('figure', { class: 'heatmap-fig' }, title ? h('figcaption', null, title) : null, h('div', { class: 'table-scroll' }, table));
}
