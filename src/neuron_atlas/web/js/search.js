// The search view: one box, real filter controls, linkable state, paged results.

import { api, apiUrl, isAbort } from './api.js';
import { h, icon, replace, clear, skeleton, bar, fmtInt, fmtNum, fmtPct, displayId, makeHighlighter, clampText, checkClamps, debounce } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox, popover, segmented, toast } from './ui.js';
import { CATEGORIES, FEATURES, MATCH_MODES, SCOPES, SORTS, GROUPS, SAMPLES, defaultState, stateFromParams, stateToParams, stateToQuery, filterParams, stateKey, hasFilters, buildHash, poleHash, neuronHash } from './state.js';
import { catBadge, sampleBadge, mirrorBadge, poleLink } from './parts.js';

const PAGE = 25;
const AUTO_LOAD_LIMIT = 400;

// Results of the last search survive leaving the view, so "back" is instant and keeps its place.
const memory = { key: null, data: null, scrollY: 0 };
window.addEventListener('atlas:mirrored', () => {
  memory.key = null;
});

const SYNTAX_EXAMPLES = [
  ['braille', 'help.words'],
  ['"severe pain"', 'help.phrase'],
  ['hash -base64', 'help.exclude'],
  ['tokeniz*', 'help.prefix'],
  ['tokeniz* OR detokeniz*', 'help.or'],
  ['L30/E47/N449', 'help.id'],
  ['braille layer:30 cat:fn', 'help.inline'],
  ['"severe pain" pole:neg modes:>=3', 'help.inline2'],
  ['sim:>0.8 has:overview sample:public', 'help.inline3'],
  ['layer:9..36 fish* in:labels', 'help.inline4'],
];

export function mount(container, ctx) {
  let state = stateFromParams(ctx.route.params);
  let ctrl = null;
  let facetCtrl = null;
  let destroyed = false;
  let data = null; // { items, total, next, took, notes, groupBy, understood }
  let facets = {}; // group -> { value: count }
  let active = -1;
  let loadingMore = false;
  let highlight = makeHighlighter(state.q, state.match);

  // ------------------------------------------------------------------ skeleton of the view

  const input = h('input', {
    type: 'search', id: 'q', class: 'search-field', autocomplete: 'off', spellcheck: 'false', enterkeyhint: 'search',
    placeholder: t('search.placeholder'), 'aria-label': t('search.label'), value: state.q,
  });
  const helpBtn = h('button', { type: 'button', class: 'iconbtn', 'aria-label': t('help.title'), title: t('help.title') }, icon('help', 18));
  const randomBtn = h('button', { type: 'button', class: 'btn', title: t('search.randomTip') }, icon('dice', 16), h('span', { class: 'btn-text' }, t('search.random')));
  const filterToggle = h('button', { type: 'button', class: 'btn filter-toggle', 'aria-expanded': 'false', 'aria-controls': 'filters' }, icon('filter', 16), h('span', null, t('filters.toggle')), h('span', { class: 'count', hidden: true }));

  const searchbar = h('div', { class: 'searchbar' },
    h('div', { class: 'search-input' }, h('span', { class: 'search-ico' }, icon('search', 18)), input, h('kbd', { class: 'kbd-hint', 'aria-hidden': 'true' }, '/'), helpBtn),
    randomBtn);

  const matchSeg = segmented({ name: 'match', label: t('search.match'), value: state.match, options: MATCH_MODES.map((m) => ({ value: m, label: t(`match.${m}`), title: t(`match.${m}.tip`) })), onChange: (v) => apply({ match: v }) });
  const groupSeg = segmented({ name: 'group', label: t('search.groupBy'), value: state.group_by, options: GROUPS.map((g) => ({ value: g, label: t(`group.${g}`) })), onChange: (v) => apply({ group_by: v }) });
  const scopeInputs = {};
  const scopeBox = h('div', { class: 'check-row', role: 'group', 'aria-label': t('search.scope') }, SCOPES.map((s) => {
    const cb = h('input', { type: 'checkbox', checked: state.scope.includes(s) });
    cb.addEventListener('change', () => {
      const next = SCOPES.filter((x) => (x === s ? cb.checked : state.scope.includes(x)));
      if (!next.length) {
        cb.checked = true;
        return;
      }
      apply({ scope: next });
    });
    scopeInputs[s] = cb;
    return h('label', { class: 'check', title: t(`scope.${s}.tip`) }, cb, h('span', null, t(`scope.${s}`)));
  }));
  const sortSel = h('select', { class: 'input input-inline', 'aria-label': t('search.sort') }, SORTS.map((s) => h('option', { value: s }, t(`sort.${s}`))));
  sortSel.addEventListener('change', () => apply({ sort: sortSel.value }));
  const orderSel = h('select', { class: 'input input-inline', 'aria-label': t('search.order') }, [['', 'order.default'], ['asc', 'order.asc'], ['desc', 'order.desc']].map(([v, k]) => h('option', { value: v }, t(k))));
  orderSel.addEventListener('change', () => apply({ order: orderSel.value }));

  const options = h('div', { class: 'search-opts' },
    h('div', { class: 'opt' }, h('span', { class: 'opt-label' }, t('search.match')), matchSeg),
    h('div', { class: 'opt' }, h('span', { class: 'opt-label' }, t('search.scope')), scopeBox),
    h('div', { class: 'opt' }, h('span', { class: 'opt-label' }, t('search.groupBy')), groupSeg),
    filterToggle);
  const sortBox = h('div', { class: 'opt sort-opt' }, h('span', { class: 'opt-label' }, t('search.sort')), h('span', { class: 'opt-pair' }, sortSel, orderSel));

  // ---- filters
  const filterBox = h('aside', { class: 'filters', id: 'filters', 'aria-label': t('filters.title') });
  const catInputs = {};
  const catCounts = {};
  const layerButtons = new Map();
  const layerCounts = new Map();
  const hasInputs = {};
  const poleSeg = segmented({ name: 'pole', label: t('filters.pole'), value: state.pole, options: [{ value: '', label: t('filters.any') }, { value: 'pos', label: '+ ' + t('pole.posShort') }, { value: 'neg', label: '− ' + t('pole.negShort') }], onChange: (v) => apply({ pole: v }) });
  const sampleSeg = segmented({ name: 'sample', label: t('filters.sample'), value: state.sample, options: SAMPLES.map((s) => ({ value: s, label: t(`sample.${s}`), title: t(`sample.${s}.tip`) })), onChange: (v) => apply({ sample: v }) });
  const numInput = (key, attrs) => {
    const el = h('input', { type: 'number', class: 'input input-num', inputmode: 'decimal', ...attrs });
    const commit = debounce(() => {
      const raw = el.value.trim();
      const n = Number(raw);
      apply({ [key]: raw === '' || !Number.isFinite(n) ? null : n });
    }, 350);
    el.addEventListener('input', commit);
    return el;
  };
  const numInputs = {
    modes_min: numInput('modes_min', { min: 0, step: 1, placeholder: t('filters.min'), 'aria-label': t('filters.modesMin') }),
    modes_max: numInput('modes_max', { min: 0, step: 1, placeholder: t('filters.max'), 'aria-label': t('filters.modesMax') }),
    sim_min: numInput('sim_min', { min: -1, max: 1, step: 0.05, placeholder: t('filters.min'), 'aria-label': t('filters.simMin') }),
    sim_max: numInput('sim_max', { min: -1, max: 1, step: 0.05, placeholder: t('filters.max'), 'aria-label': t('filters.simMax') }),
  };
  const layerGrid = h('div', { class: 'layer-grid', role: 'group', 'aria-label': t('filters.layer') });
  const resetBtn = h('button', { type: 'button', class: 'btn btn-small', onclick: () => resetAll() }, icon('reset', 14), t('filters.reset'));

  function buildFilters() {
    const group = (title, body, hint) => h('section', { class: 'fgroup' }, h('h3', { class: 'fgroup-title' }, title, hint ? h('span', { class: 'muted small fgroup-hint' }, hint) : null), body);
    const cats = h('div', { class: 'stack-tight' }, CATEGORIES.map((c) => {
      const cb = h('input', { type: 'checkbox', checked: state.cat.includes(c) });
      cb.addEventListener('change', () => apply({ cat: CATEGORIES.filter((x) => (x === c ? cb.checked : state.cat.includes(x))) }));
      catInputs[c] = cb;
      catCounts[c] = h('span', { class: 'fcount' });
      return h('label', { class: 'check check-count', title: t(`cat.${c}.tip`) }, cb, h('span', { class: `cat cat-${c}` }, h('span', { class: 'cat-dot', 'aria-hidden': 'true' }), t(`cat.${c}`)), catCounts[c]);
    }));
    const has = h('div', { class: 'stack-tight' }, FEATURES.map((f) => {
      const cb = h('input', { type: 'checkbox', checked: state.has.includes(f) });
      cb.addEventListener('change', () => apply({ has: FEATURES.filter((x) => (x === f ? cb.checked : state.has.includes(x))) }));
      hasInputs[f] = cb;
      return h('label', { class: 'check', title: t(`has.${f}.tip`) }, cb, h('span', null, t(`has.${f}`)));
    }));
    replace(filterBox,
      h('div', { class: 'filters-head' }, h('h2', null, t('filters.title')), resetBtn),
      group(t('filters.category'), cats),
      group(t('filters.layer'), layerGrid),
      group(t('filters.pole'), poleSeg),
      group(t('filters.sample'), sampleSeg),
      group(t('filters.has'), has),
      group(t('filters.modes'), h('div', { class: 'range' }, numInputs.modes_min, h('span', { 'aria-hidden': 'true' }, '–'), numInputs.modes_max)),
      group(t('filters.sim'), h('div', { class: 'range' }, numInputs.sim_min, h('span', { 'aria-hidden': 'true' }, '–'), numInputs.sim_max), '0–1'));
  }

  function buildLayers(layers) {
    clear(layerGrid);
    layerButtons.clear();
    layerCounts.clear();
    for (const layer of layers) {
      const count = h('span', { class: 'fcount' });
      const btn = h('button', { type: 'button', class: 'layer-btn', 'aria-pressed': String(state.layer.includes(layer)), 'aria-label': `${t('pole.layer')} ${layer}` }, h('span', { class: 'layer-no' }, `L${layer}`), count);
      btn.addEventListener('click', () => {
        const on = !state.layer.includes(layer);
        apply({ layer: on ? [...state.layer, layer].sort((a, b) => a - b) : state.layer.filter((x) => x !== layer) });
      });
      layerButtons.set(layer, btn);
      layerCounts.set(layer, count);
      layerGrid.appendChild(btn);
    }
  }

  // ---- results column
  const summary = h('div', { class: 'results-summary', 'aria-live': 'polite' });
  const tools = h('div', { class: 'results-tools' });
  const chips = h('div', { class: 'chips', 'aria-label': t('filters.active') });
  const notes = h('div', { class: 'results-notes' });
  const list = h('div', { class: 'hits', role: 'list' });
  const foot = h('div', { class: 'results-foot' });
  const resultsBox = h('section', { class: 'results', 'aria-label': t('search.results') },
    h('div', { class: 'results-head' }, summary, sortBox, tools), chips, notes, list, foot);

  const root = h('div', { class: 'search-view' }, searchbar, options, h('div', { class: 'search-layout' }, filterBox, resultsBox));
  container.appendChild(root);
  buildFilters();

  // ------------------------------------------------------------------ state changes

  function commitUrl(push) {
    const hash = buildHash('search', '', stateToParams(state));
    ctx.go(hash, { replace: !push, silent: true });
    ctx.rememberSearch(hash);
  }

  /** Change the state, reflect it in the URL and search again. */
  function apply(patch, { push = true, sync = true } = {}) {
    state = { ...state, ...patch };
    if (sync) syncControls();
    commitUrl(push);
    run();
  }

  function resetAll() {
    state = defaultState();
    input.value = '';
    syncControls();
    commitUrl(true);
    run();
    input.focus();
  }

  function syncControls() {
    if (document.activeElement !== input && input.value !== state.q) input.value = state.q;
    matchSeg._set(state.match);
    groupSeg._set(state.group_by);
    poleSeg._set(state.pole);
    sampleSeg._set(state.sample);
    for (const s of SCOPES) scopeInputs[s].checked = state.scope.includes(s);
    for (const c of CATEGORIES) if (catInputs[c]) catInputs[c].checked = state.cat.includes(c);
    for (const f of FEATURES) if (hasInputs[f]) hasInputs[f].checked = state.has.includes(f);
    for (const [layer, btn] of layerButtons) btn.setAttribute('aria-pressed', String(state.layer.includes(layer)));
    for (const [key, el] of Object.entries(numInputs)) {
      if (document.activeElement !== el) el.value = state[key] == null ? '' : String(state[key]);
    }
    sortSel.value = state.sort;
    orderSel.value = state.order;
    const n = Object.keys(filterParams({ ...state, q: '', match: 'auto', scope: ['labels', 'deep'] })).length;
    const badge = filterToggle.querySelector('.count');
    badge.hidden = n === 0;
    badge.textContent = String(n);
    resetBtn.disabled = !hasFilters(state) && state.sort === 'relevance' && state.group_by === 'pole' && !state.order;
    renderChips();
  }

  function renderChips() {
    const items = [];
    const chip = (label, remove) => items.push(h('span', { class: 'chip' }, h('span', null, label),
      h('button', { type: 'button', class: 'chip-x', 'aria-label': t('filters.remove', { name: label }), onclick: remove }, icon('close', 12))));
    if (state.match !== 'auto') chip(`${t('search.match')}: ${t(`match.${state.match}`)}`, () => apply({ match: 'auto' }));
    if (!(state.scope.length === 2 && state.scope.includes('labels') && state.scope.includes('deep'))) chip(`${t('search.scope')}: ${state.scope.map((s) => t(`scope.${s}`)).join(' + ')}`, () => apply({ scope: ['labels', 'deep'] }));
    for (const c of state.cat) chip(t(`cat.${c}`), () => apply({ cat: state.cat.filter((x) => x !== c) }));
    for (const l of state.layer) chip(`${t('pole.layer')} ${l}`, () => apply({ layer: state.layer.filter((x) => x !== l) }));
    if (state.layer_min != null) chip(`${t('pole.layer')} ≥ ${state.layer_min}`, () => apply({ layer_min: null }));
    if (state.layer_max != null) chip(`${t('pole.layer')} ≤ ${state.layer_max}`, () => apply({ layer_max: null }));
    if (state.pole) chip(state.pole === 'pos' ? `+ ${t('pole.posShort')}` : `− ${t('pole.negShort')}`, () => apply({ pole: '' }));
    if (state.sample !== 'all') chip(t(`sample.${state.sample}.tip`), () => apply({ sample: 'all' }));
    for (const f of state.has) chip(`${t('filters.hasShort')}: ${t(`has.${f}`)}`, () => apply({ has: state.has.filter((x) => x !== f) }));
    for (const f of state.lacks) chip(`${t('filters.lacksShort')}: ${t(`has.${f}`)}`, () => apply({ lacks: state.lacks.filter((x) => x !== f) }));
    const range = (label, lo, hi, fmt, patch) => {
      if (state[lo] == null && state[hi] == null) return;
      const a = state[lo] == null ? '' : fmt(state[lo]);
      const b = state[hi] == null ? '' : fmt(state[hi]);
      chip(`${label} ${a && b ? `${a}–${b}` : a ? `≥ ${a}` : `≤ ${b}`}`, () => apply(patch));
    };
    range(t('filters.modes'), 'modes_min', 'modes_max', (v) => fmtInt(v), { modes_min: null, modes_max: null });
    range(t('filters.sim'), 'sim_min', 'sim_max', (v) => fmtNum(v, 2), { sim_min: null, sim_max: null });
    range(t('filters.damage'), 'damage_min', 'damage_max', (v) => fmtNum(v, 2), { damage_min: null, damage_max: null });
    if (state.verdict) chip(t(`verdict.${state.verdict}`), () => apply({ verdict: '' }));
    if (state.expert.length) chip(`${t('pole.expert')} ${state.expert.join(', ')}`, () => apply({ expert: [] }));
    if (state.neuron.length) chip(`${t('pole.neuron')} ${state.neuron.join(', ')}`, () => apply({ neuron: [] }));
    if (state.ids.length) chip(`IDs: ${state.ids.slice(0, 3).map(displayId).join(', ')}${state.ids.length > 3 ? ' …' : ''}`, () => apply({ ids: [] }));
    chips.hidden = !items.length;
    replace(chips, items, items.length > 1 ? h('button', { type: 'button', class: 'linkish', onclick: () => resetAll() }, t('filters.reset')) : null);
  }

  // ------------------------------------------------------------------ facets

  function paintFacets() {
    const cat = facets.category;
    for (const c of CATEGORIES) {
      const n = cat ? cat[c] || 0 : null;
      catCounts[c].textContent = n == null ? '' : fmtInt(n);
      catInputs[c].closest('label').classList.toggle('is-zero', n === 0 && !state.cat.includes(c));
    }
    const lay = facets.layer;
    for (const [layer, el] of layerCounts) {
      const n = lay ? lay[String(layer)] || 0 : null;
      el.textContent = n == null ? '' : fmtInt(n);
      layerButtons.get(layer).classList.toggle('is-zero', n === 0 && !state.layer.includes(layer));
    }
    const setTitles = (seg, counts, values) => {
      if (!counts) return;
      for (const b of seg.querySelectorAll('.seg')) {
        const v = b.dataset.value;
        if (v && values.includes(v)) b.title = `${fmtInt(counts[v] || 0)} ${t('unit.poles')}`;
      }
    };
    setTitles(poleSeg, facets.pole, ['pos', 'neg']);
    setTitles(sampleSeg, facets.sample, ['public', 'hidden']);
  }

  /**
   * The counts next to an option should say what selecting it would give, so a
   * facet group that is itself filtered is counted without its own filter.
   */
  async function loadDisjunctiveFacets(signal) {
    const groups = [];
    if (state.cat.length) groups.push(['category', { cat: [] }]);
    if (state.layer.length || state.layer_min != null || state.layer_max != null) groups.push(['layer', { layer: [], layer_min: null, layer_max: null }]);
    if (state.pole) groups.push(['pole', { pole: '' }]);
    if (state.sample !== 'all') groups.push(['sample', { sample: 'all' }]);
    await Promise.all(groups.map(async ([name, patch]) => {
      try {
        const res = await api('/api/v1/search', { params: { ...stateToParams({ ...state, ...patch }), limit: 0, facets: true, snippets: false }, signal });
        if (res.facets && res.facets[name]) {
          facets = { ...facets, [name]: res.facets[name] };
          paintFacets();
        }
      } catch {
        /* counts are a nicety; the main search reports real errors */
      }
    }));
  }

  // ------------------------------------------------------------------ searching

  function unitWord(groupBy, n) {
    return t(`unit.${groupBy === 'neuron' ? 'neurons' : groupBy === 'mode' ? 'modes' : 'poles'}`, { n });
  }

  async function run() {
    ctrl?.abort();
    facetCtrl?.abort();
    ctrl = new AbortController();
    facetCtrl = new AbortController();
    const signal = ctrl.signal;
    const key = stateKey(state);
    highlight = makeHighlighter(state.q, state.match);
    active = -1;
    if (!data) replace(list, skeletonRows());
    resultsBox.classList.add('is-loading');
    resultsBox.setAttribute('aria-busy', 'true');
    try {
      const res = await api('/api/v1/search', { params: { ...stateToParams(state), limit: PAGE, offset: 0, facets: true }, signal });
      if (destroyed) return;
      data = digest(res);
      memory.key = key;
      memory.data = data;
      facets = res.facets || {};
      paintFacets();
      renderResults();
      loadDisjunctiveFacets(facetCtrl.signal);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      data = null;
      memory.key = null;
      replace(summary);
      replace(tools);
      replace(notes);
      replace(foot);
      replace(list, errorBox(err, () => run()));
    } finally {
      if (!signal.aborted) {
        resultsBox.classList.remove('is-loading');
        resultsBox.removeAttribute('aria-busy');
      }
    }
  }

  function digest(res) {
    const items = res.group_by === 'neuron' ? res.neurons : res.group_by === 'mode' ? res.modes : res.hits;
    return { items: [...(items || [])], total: res.total, next: res.next_offset, took: res.took_ms, notes: res.notes || [], groupBy: res.group_by, understood: res.query || {} };
  }

  async function loadMore() {
    if (!data || data.next == null || loadingMore) return;
    loadingMore = true;
    const signal = ctrl ? ctrl.signal : undefined;
    renderFoot();
    try {
      const res = await api('/api/v1/search', { params: { ...stateToParams(state), limit: PAGE, offset: data.next }, signal });
      if (destroyed) return;
      const more = digest(res);
      const from = data.items.length;
      data.items.push(...more.items);
      data.next = more.next;
      appendRows(more.items, from);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      toast(err.detail || err.message, { kind: 'error' });
    } finally {
      loadingMore = false;
      renderFoot();
    }
  }

  function skeletonRows() {
    return Array.from({ length: 6 }, () => h('div', { class: 'hit hit-skeleton' }, skeleton(3)));
  }

  // ------------------------------------------------------------------ rendering

  function matchList(matches, { skipField } = {}) {
    const ms = (matches || []).filter((m) => m && m.text && m.field !== skipField);
    if (!ms.length) return null;
    const row = (m) => h('li', { class: 'match' },
      h('span', { class: 'match-field' }, t(`field.${m.field}`, null, m.field),
        m.mode_index != null ? ` ${m.mode_index}` : '', m.ref ? ` ${m.ref}` : '',
        m.share != null ? h('span', { class: 'match-share' }, ` · ${fmtPct(m.share)}`) : null),
      h('span', { class: 'match-text' }, highlight(m.text)));
    const ul = h('ul', { class: 'matches' }, ms.slice(0, 3).map(row));
    if (ms.length > 3) {
      const btn = h('button', { type: 'button', class: 'linkish' }, t('showMore', { n: ms.length - 3 }));
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        ms.slice(3).forEach((m) => ul.insertBefore(row(m), btn.parentNode));
        btn.parentNode.remove();
      });
      ul.appendChild(h('li', { class: 'match match-more' }, btn));
    }
    return ul;
  }

  function metaBits(hit) {
    const bits = [];
    if (hit.n_modes != null) bits.push(h('span', { class: 'meta' }, t('hit.modes', { n: hit.n_modes })));
    if (hit.simulatability != null) bits.push(h('span', { class: 'meta', title: t('pole.simTip') }, `${t('hit.sim')} ${fmtPct(hit.simulatability, 0)}`));
    if (hit.main_damage_share != null && state.sort === 'damage') bits.push(h('span', { class: 'meta', title: t('pole.mainDamageTip') }, `${t('pole.mainDamage')} ${fmtPct(hit.main_damage_share, 0)}`));
    return bits;
  }

  function poleRow(hit) {
    const showHeadline = hit.headline && hit.headline !== hit.description;
    return h('article', { class: 'hit hit-pole', role: 'listitem', dataset: { href: poleHash(hit.pid) } },
      h('header', { class: 'hit-head' },
        poleLink(hit.pid, hit.pole),
        catBadge(hit.category, hit.category_words),
        metaBits(hit),
        h('span', { class: 'hit-badges' }, sampleBadge(hit), mirrorBadge(hit))),
      showHeadline ? h('p', { class: 'hit-headline' }, h('a', { class: 'hit-title', href: poleHash(hit.pid), tabindex: '-1' }, highlight(hit.headline))) : null,
      hit.description ? clampText(highlight(hit.description), { lines: 2, moreLabel: t('more'), lessLabel: t('less'), cls: 'hit-desc' }) : null,
      matchList(hit.matches, { skipField: 'description' }));
  }

  function modeRow(hit) {
    return h('article', { class: 'hit hit-mode', role: 'listitem', dataset: { href: poleHash(hit.pid, { mode: hit.mode_index }) } },
      h('header', { class: 'hit-head' },
        poleLink(hit.pid, hit.pole, { mode: hit.mode_index }),
        h('span', { class: 'mode-index' }, t('mode.index', { n: hit.mode_index })),
        catBadge(hit.category),
        hit.share != null ? h('span', { class: 'meta share-meta' }, bar(hit.share), h('strong', null, fmtPct(hit.share)), ' ', t('mode.shareOf')) : null),
      h('p', { class: 'hit-modelabel' }, h('a', { class: 'hit-title', href: poleHash(hit.pid, { mode: hit.mode_index }), tabindex: '-1' }, highlight(hit.label))),
      matchList(hit.matches, { skipField: 'mode' }));
  }

  function neuronRow(hit) {
    return h('article', { class: 'hit hit-neuron', role: 'listitem', dataset: { href: neuronHash(hit.did) } },
      h('header', { class: 'hit-head' },
        h('a', { class: 'pid', href: neuronHash(hit.did) }, h('span', { class: 'idtext' }, hit.display || displayId(hit.did))),
        h('span', { class: 'meta' }, `${t('pole.layer')} ${hit.layer}`),
        h('span', { class: 'meta' }, t('hit.matchingPoles', { n: (hit.poles || []).length }))),
      hit.circuit_label ? clampText(highlight(hit.circuit_label), { lines: 1, moreLabel: t('more'), lessLabel: t('less'), cls: 'hit-circuit' }) : null,
      h('div', { class: 'neuron-poles' }, (hit.poles || []).map((p) => h('div', { class: `subhit subhit-${p.pole}` },
        h('div', { class: 'hit-head' }, poleLink(p.pid, p.pole), catBadge(p.category, p.category_words), metaBits(p), h('span', { class: 'hit-badges' }, sampleBadge(p), mirrorBadge(p))),
        p.description ? clampText(highlight(p.description), { lines: 2, moreLabel: t('more'), lessLabel: t('less'), cls: 'hit-desc' }) : null,
        matchList(p.matches, { skipField: 'description' })))));
  }

  // Rows are plain text (easy to select and copy); the id and the title are the links.
  function rowFor(item) {
    return data.groupBy === 'neuron' ? neuronRow(item) : data.groupBy === 'mode' ? modeRow(item) : poleRow(item);
  }

  function appendRows(items, from) {
    const frag = document.createDocumentFragment();
    items.forEach((item) => frag.appendChild(rowFor(item)));
    list.appendChild(frag);
    checkClamps(list);
    if (from === 0) list.scrollTop = 0;
  }

  function exportMenu(button) {
    let withDetails = false;
    popover(button, (panel) => {
      const links = h('div', { class: 'menu-links' });
      const draw = () => {
        const base = { ...stateToParams(state) };
        delete base.group_by;
        if (withDetails) {
          base.include = 'modes,examples';
          base.max_examples = 2;
        }
        replace(links, [['ndjson', 'NDJSON'], ['csv', 'CSV'], ['md', 'Markdown']].map(([fmt, label]) =>
          h('a', { class: 'menu-link', href: apiUrl('/api/v1/export', { ...base, format: fmt }), download: `neuron-atlas-export.${fmt}`, dataset: { format: fmt } }, icon('download', 15), h('span', null, label), h('span', { class: 'muted small' }, `.${fmt}`))));
      };
      const cb = h('input', { type: 'checkbox', checked: withDetails });
      cb.addEventListener('change', () => {
        withDetails = cb.checked;
        draw();
      });
      draw();
      panel.append(
        h('h3', null, t('export.allTitle', { n: fmtInt(data ? data.total : 0) })),
        h('p', { class: 'muted small' }, t('export.allHint')),
        links,
        h('label', { class: 'check', title: t('export.detailsTip') }, cb, h('span', null, t('export.details'))));
    }, { cls: 'popover-menu', align: 'end' });
  }

  function renderResults() {
    if (!data) return;
    const total = data.total;
    replace(summary,
      h('strong', { class: 'total' }, fmtInt(total)), ' ', unitWord(data.groupBy, total),
      h('span', { class: 'muted took' }, ` · ${fmtNum(data.took, data.took < 10 ? 1 : 0)} ms`));
    const exportBtn = h('button', { type: 'button', class: 'btn btn-small', dataset: { action: 'export' } }, icon('download', 14), t('export.all', { n: fmtInt(total) }));
    exportMenu(exportBtn);
    exportBtn.disabled = total === 0;
    replace(tools,
      exportBtn,
      h('button', { type: 'button', class: 'btn btn-small', dataset: { action: 'region' }, onclick: () => ctx.openRegion({ state, mode: 'search' }) }, icon('download', 14), t('region.open')),
      h('a', { class: 'btn btn-small', href: buildHash('map', '', filterParams(state)) }, icon('map', 14), t('search.onMap')),
      h('a', { class: 'btn btn-small', href: apiUrl('/api/v1/search', { ...stateToParams(state), limit: PAGE }, { withKey: false }), target: '_blank', rel: 'noopener', title: t('search.apiTip') }, icon('code', 14), 'API'));

    const noteItems = data.notes.map((n) => h('li', null, n));
    // Filters the server read out of the query text (layer:30 cat:fn …) are echoed back.
    const sent = stateToQuery(state);
    const inline = Object.entries(data.understood || {}).filter(([k, v]) => !['q', 'limit', 'offset', 'facets', 'snippets'].includes(k) && JSON.stringify(v) !== JSON.stringify(sent[k]));
    replace(notes,
      inline.length ? h('p', { class: 'understood muted small' }, t('search.understood'), ' ', inline.map(([k, v]) => h('code', null, `${k}: ${Array.isArray(v) ? v.join(', ') : v}`))) : null,
      noteItems.length ? h('ul', { class: 'notes' }, noteItems) : null);

    clear(list);
    if (!data.items.length) {
      const idx = (ctx.info && ctx.info.search_index) || {};
      const tips = [];
      if (state.match !== 'semantic' && state.q.trim()) tips.push(h('li', null, h('button', { type: 'button', class: 'linkish', onclick: () => apply({ match: 'semantic' }) }, t('empty.trySemantic'))));
      if (state.match !== 'substring' && state.match !== 'semantic' && state.q.trim()) tips.push(h('li', null, h('button', { type: 'button', class: 'linkish', onclick: () => apply({ match: 'substring' }) }, t('empty.trySubstring'))));
      if (Object.keys(filterParams({ ...state, q: '' })).length) tips.push(h('li', null, h('button', { type: 'button', class: 'linkish', onclick: () => apply({ ...defaultState(), q: state.q }) }, t('empty.dropFilters'))));
      if (state.scope.includes('examples') && !idx.example_texts) tips.push(h('li', null, t('empty.noExamples')));
      tips.push(h('li', null, t('empty.deepHint', { n: fmtInt(idx.poles_with_deep_texts ?? 0) })));
      list.appendChild(notice('empty', t('empty.title'), h('p', null, t('empty.text')), h('ul', { class: 'empty-tips' }, tips)));
    } else {
      appendRows(data.items, 0);
    }
    renderFoot();
  }

  let observer = null;
  function renderFoot() {
    observer?.disconnect();
    if (!data || !data.items.length) {
      replace(foot);
      return;
    }
    const shown = data.items.length;
    const parts = [h('span', { class: 'muted' }, t('search.shown', { shown: fmtInt(shown), total: fmtInt(data.total) }))];
    if (data.next != null) {
      const btn = h('button', { type: 'button', class: 'btn', disabled: loadingMore, dataset: { action: 'more' } }, loadingMore ? t('loading') : t('loadMore'));
      btn.addEventListener('click', loadMore);
      parts.push(btn);
      if (shown < AUTO_LOAD_LIMIT && 'IntersectionObserver' in window) {
        observer = new IntersectionObserver((entries) => {
          if (entries.some((e) => e.isIntersecting)) loadMore();
        }, { rootMargin: '400px' });
        observer.observe(btn);
      }
    }
    replace(foot, parts);
  }

  // ------------------------------------------------------------------ keyboard

  function setActive(index, scroll = true) {
    const rows = list.querySelectorAll('.hit:not(.hit-skeleton)');
    if (!rows.length) return;
    active = Math.max(0, Math.min(rows.length - 1, index));
    rows.forEach((r, i) => r.classList.toggle('is-active', i === active));
    if (scroll) rows[active].scrollIntoView({ block: 'nearest' });
  }

  function onKey(ev) {
    if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const el = ev.target;
    const typing = el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    if (typing || document.querySelector('dialog[open], .popover')) return;
    if (ev.key === 'j' || ev.key === 'k') {
      ev.preventDefault();
      setActive(active + (ev.key === 'j' ? 1 : -1));
    } else if (ev.key === 'Enter' && active >= 0 && !(el instanceof HTMLElement && /^(A|BUTTON|SUMMARY)$/.test(el.tagName))) {
      const row = list.querySelectorAll('.hit:not(.hit-skeleton)')[active];
      if (row && row.dataset.href) {
        ev.preventDefault();
        ctx.go(row.dataset.href);
      }
    }
  }
  document.addEventListener('keydown', onKey);

  // ------------------------------------------------------------------ wiring

  const typed = debounce(() => apply({ q: input.value }, { push: false, sync: false }), 220);
  input.addEventListener('input', () => {
    state.q = input.value;
    typed();
  });
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      typed.cancel();
      apply({ q: input.value }, { push: true });
    } else if (ev.key === 'Escape' && input.value) {
      ev.preventDefault();
      input.value = '';
      typed.cancel();
      apply({ q: '' });
    } else if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      input.blur();
      setActive(0);
    }
  });

  popover(helpBtn, (panel, close) => {
    panel.append(
      h('h3', null, t('help.title')),
      h('p', { class: 'muted small' }, t('help.intro')),
      h('table', { class: 'help-table' }, h('tbody', null, SYNTAX_EXAMPLES.map(([q, key]) => h('tr', null,
        h('td', null, h('button', { type: 'button', class: 'example-q', dataset: { q }, onclick: () => {
          input.value = q;
          close();
          apply({ q });
          input.focus();
        } }, q)),
        h('td', { class: 'muted' }, t(key)))))),
      h('p', { class: 'small' }, h('strong', null, t('help.keys')), ' ', h('code', null, 'layer expert neuron pole cat verdict modes sim damage has lacks sample in sort group')),
      h('p', { class: 'small muted' }, t('help.numbers')),
      h('p', { class: 'small muted' }, t('help.keyboard')));
  }, { cls: 'popover-help', align: 'end' });

  randomBtn.addEventListener('click', async () => {
    randomBtn.disabled = true;
    try {
      const res = await api('/api/v1/random', { params: { ...filterParams(state), limit: 1 } });
      const hit = (res.hits || [])[0];
      if (hit) ctx.go(poleHash(hit.pid));
      else toast(t('search.randomNone'), { kind: 'warn' });
    } catch (err) {
      toast(err.detail || err.message, { kind: 'error' });
    } finally {
      randomBtn.disabled = false;
    }
  });

  filterToggle.addEventListener('click', () => {
    const open = root.classList.toggle('filters-open');
    filterToggle.setAttribute('aria-expanded', String(open));
  });

  // layers come from /info; until then the grid shows the layers named in the URL
  buildLayers(state.layer);
  ctx.infoReady.then((info) => {
    if (destroyed) return;
    const layers = Object.keys((info && info.atlas && info.atlas.layers) || {}).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    for (const l of state.layer) if (!layers.includes(l)) layers.push(l);
    buildLayers(layers.sort((a, b) => a - b));
    paintFacets();
  });

  syncControls();
  document.title = 'Neuron Atlas Interface';
  ctx.rememberSearch(buildHash('search', '', stateToParams(state)));
  if (memory.key === stateKey(state) && memory.data) {
    data = memory.data;
    renderResults();
    // counts are cheap: refresh them in the background
    facetCtrl = new AbortController();
    api('/api/v1/search', { params: { ...stateToParams(state), limit: 0, facets: true, snippets: false }, signal: facetCtrl.signal })
      .then((res) => {
        facets = res.facets || {};
        paintFacets();
        return loadDisjunctiveFacets(facetCtrl.signal);
      })
      .catch(() => {});
    const y = memory.scrollY;
    requestAnimationFrame(() => window.scrollTo(0, y));
  } else {
    run();
  }

  return {
    update(route) {
      const next = stateFromParams(route.params);
      if (stateKey(next) === stateKey(state)) return;
      state = next;
      input.value = state.q;
      syncControls();
      ctx.rememberSearch(buildHash('search', '', stateToParams(state)));
      run();
    },
    focusSearch() {
      input.focus();
      input.select();
    },
    destroy() {
      destroyed = true;
      memory.scrollY = window.scrollY;
      typed.cancel();
      ctrl?.abort();
      facetCtrl?.abort();
      observer?.disconnect();
      document.removeEventListener('keydown', onKey);
    },
  };
}
