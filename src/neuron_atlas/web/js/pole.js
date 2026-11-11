// The pole page: header, six lazily loaded tabs, similar poles, copy / download / print.

import { api, apiUrl, isAbort } from './api.js';
import { h, icon, replace, skeleton, bar, fmtInt, fmtNum, fmtPct, fmtSig, displayId, didOf, poleLabel, signChar, clampText, checkClamps } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox, copyButton, popover, toast, segmented } from './ui.js';
import { buildHash, poleHash, neuronHash } from './state.js';
import { renderExamples, shadingLegend } from './tokens.js';
import { renderMarkdown } from './markdown.js';
import { catBadge, sampleBadge, mirrorBadge, kindBadge, unitLink, barCell, heatmap, poleLink } from './parts.js';
import { startMirror, followJob, progressBar, MIRROR_SOURCES } from './region.js';

const TABS = ['modes', 'overview', 'subcircuits', 'landscape', 'circuit', 'islands'];
const TAB_INCLUDE = {
  modes: 'modes,examples,tokens',
  overview: 'overview',
  subcircuits: 'subcircuits,examples,tokens',
  landscape: 'landscape',
  circuit: 'circuit',
  islands: 'islands,examples,tokens',
};
const TAB_SOURCE = { modes: 'subcircuit', overview: 'overview', subcircuits: 'subcircuit', landscape: 'landscape', circuit: 'circuit', islands: 'islands' };
const EXPORT_PARTS = ['modes', 'examples', 'subcircuits', 'overview', 'landscape', 'circuit', 'islands'];
const PARTS_STORAGE = 'na.exportParts';
const EXAMPLE_CHOICES = [1, 3, 6, 12];

function loadParts() {
  try {
    const raw = JSON.parse(localStorage.getItem(PARTS_STORAGE) || 'null');
    if (raw && Array.isArray(raw.parts)) return { parts: raw.parts.filter((p) => EXPORT_PARTS.includes(p)), tokens: !!raw.tokens };
  } catch {
    /* ignore */
  }
  return { parts: ['modes', 'examples', 'overview'], tokens: false };
}

function saveParts(scope) {
  try {
    localStorage.setItem(PARTS_STORAGE, JSON.stringify(scope));
  } catch {
    /* ignore */
  }
}

// --------------------------------------------------------------------------- shared renderers

/** Text with ⟦focal⟧ marks (the scientists' cue notation). */
function cueText(text) {
  const frag = document.createDocumentFragment();
  const re = /⟦([^⟧]*)⟧/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
    frag.appendChild(h('span', { class: 'tok tok-focal tok-plainfocal' }, m[1]));
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function effectMark(effect) {
  if (effect === '+' || effect === 'drives') return h('span', { class: 'effect effect-plus', title: t('circuit.drives') }, '+');
  if (effect === '-' || effect === '−' || effect === 'opposes') return h('span', { class: 'effect effect-minus', title: t('circuit.opposes') }, '−');
  return effect ? h('span', { class: 'effect' }, String(effect)) : null;
}

function memberList(members, { limit = 8 } = {}) {
  const items = (members || []).filter((m) => m && typeof m === 'object' && m.unit);
  if (!items.length) return null;
  const maxW = Math.max(...items.map((m) => Math.abs(m.weight ?? 0)), 0);
  const list = h('ul', { class: 'units' });
  const row = (m) => h('li', { class: 'unit-row' },
    unitLink(m.unit, m.in_atlas),
    m.weight != null ? h('span', { class: `unit-weight ${m.weight < 0 ? 'neg' : 'pos'}`, title: t('circuit.weight') }, (m.weight > 0 ? '+' : m.weight < 0 ? '−' : '') + fmtSig(Math.abs(m.weight), 2)) : null,
    m.weight != null && maxW > 0 ? bar(Math.abs(m.weight), { max: maxW, cls: m.weight < 0 ? 'bar-neg' : 'bar-pos' }) : null,
    m.label ? h('span', { class: 'unit-label', title: m.label }, m.label) : null);
  items.slice(0, limit).forEach((m) => list.appendChild(row(m)));
  if (items.length > limit) {
    const more = h('button', { type: 'button', class: 'linkish no-print' }, t('showMore', { n: items.length - limit }));
    more.addEventListener('click', () => {
      items.slice(limit).forEach((m) => list.appendChild(row(m)));
      more.remove();
    });
    return h('div', null, list, more);
  }
  return list;
}

/**
 * The circuit dictionary of a pole or neuron: {line, inputs[], readers[], output_tokens[], notes[]}.
 * Every key may be missing.
 */
export function renderCircuit(circuit) {
  const c = circuit && typeof circuit === 'object' ? circuit : {};
  const inputs = Array.isArray(c.inputs) ? c.inputs.filter((x) => x && typeof x === 'object') : [];
  const readers = Array.isArray(c.readers) ? c.readers.filter((x) => x && typeof x === 'object') : [];
  const tokens = Array.isArray(c.output_tokens) ? c.output_tokens.filter((x) => x && typeof x === 'object') : [];
  const notes = Array.isArray(c.notes) ? c.notes.filter((x) => typeof x === 'string') : [];
  const line = typeof c.line === 'string' ? c.line : typeof c.note === 'string' ? c.note : '';
  if (!inputs.length && !readers.length && !tokens.length && !line && !notes.length) return null;

  const group = (g, kind) => {
    const meta = [];
    if (g.role) meta.push(String(g.role));
    if (g.lane) meta.push(`${t('circuit.lane')}: ${g.lane}`);
    if (typeof g.share === 'number') meta.push(`${t('circuit.share')} ${fmtPct(g.share, 0)}`);
    const n = g.n_members ?? g.n;
    if (n != null) meta.push(t('circuit.members', { n: fmtInt(n) }));
    return h('li', { class: `cgroup cgroup-${kind}` },
      h('div', { class: 'cgroup-head' }, effectMark(g.effect), h('span', { class: 'cgroup-name' }, g.name || '–'), meta.length ? h('span', { class: 'muted cgroup-meta' }, meta.join(' · ')) : null),
      g.description ? h('p', { class: 'cgroup-desc' }, g.description) : null,
      memberList(g.members));
  };
  const maxTok = Math.max(...tokens.map((x) => Math.abs(x.weight ?? 0)), 0);
  return h('div', { class: 'circuit' },
    line ? h('p', { class: 'circuit-line' }, line) : null,
    h('div', { class: 'circuit-cols' },
      inputs.length ? h('section', null, h('h4', null, t('circuit.inputs'), h('span', { class: 'count' }, fmtInt(inputs.length))), h('ul', { class: 'cgroups' }, inputs.map((g) => group(g, 'input')))) : null,
      readers.length ? h('section', null, h('h4', null, t('circuit.readers'), h('span', { class: 'count' }, fmtInt(readers.length))), h('ul', { class: 'cgroups' }, readers.map((g) => group(g, 'reader')))) : null),
    tokens.length ? h('section', null, h('h4', null, t('circuit.outputTokens')),
      h('ul', { class: 'out-tokens' }, tokens.map((x) => h('li', null, h('code', { class: 'out-token' }, x.token ?? ''), x.weight != null ? h('span', { class: 'num muted' }, fmtSig(x.weight, 3)) : null, x.weight != null ? bar(Math.abs(x.weight), { max: maxTok }) : null)))) : null,
    notes.length ? h('ul', { class: 'notes' }, notes.map((n) => h('li', null, n))) : null);
}

function islandsView(view, pole) {
  const modes = view.modes || [];
  const islands = view.islands || [];
  const byId = new Map(islands.map((i) => [i.id, i]));
  const used = new Set();
  const islandCard = (isl) => h('article', { class: 'island', id: `island-${isl.id}` },
    h('header', { class: 'island-head' },
      h('span', { class: 'island-no' }, t('island.number', { n: isl.number })),
      h('h4', null, isl.name || '–'),
      isl.n != null ? h('span', { class: 'muted' }, t('island.n', { n: fmtInt(isl.n) })) : null),
    isl.description ? h('p', { class: 'island-desc' }, isl.description) : null,
    renderExamples(isl.examples, pole));
  const out = h('div', { class: 'islands' });
  for (const m of modes) {
    const mine = (m.islands || []).map((id) => byId.get(id)).filter(Boolean);
    mine.forEach((i) => used.add(i.id));
    out.appendChild(h('section', { class: `island-mode ${m.unassigned ? 'is-unassigned' : ''}` },
      h('header', { class: 'mode-head' },
        h('div', { class: 'mode-title' }, h('h3', null, m.name || '–'), m.unassigned ? kindBadge('residue') : null),
        h('div', { class: 'mode-stats' }, m.share != null ? h('span', { class: 'stat-inline' }, bar(m.share), h('strong', null, fmtPct(m.share)), ' ', t('mode.shareOf')) : null,
          h('span', { class: 'muted' }, t('island.count', { n: mine.length })))),
      m.description ? h('p', { class: 'mode-desc' }, m.description) : null,
      mine.map(islandCard)));
  }
  const rest = islands.filter((i) => !used.has(i.id));
  if (rest.length) out.appendChild(h('section', { class: 'island-mode' }, modes.length ? h('h3', null, t('island.other')) : null, rest.map(islandCard)));
  return out;
}

// --------------------------------------------------------------------------- the view

export function mount(container, ctx) {
  let pid = ctx.route.arg;
  let tab = TABS.includes(ctx.route.params.get('tab')) ? ctx.route.params.get('tab') : 'modes';
  let maxExamples = EXAMPLE_CHOICES.includes(Number(ctx.route.params.get('ex'))) ? Number(ctx.route.params.get('ex')) : 3;
  let focusMode = ctx.route.params.get('mode');
  let focusSub = ctx.route.params.get('sub');
  let scope = loadParts();
  let abort = new AbortController();
  let cache = new Map();
  let base = null; // the "modes" response: header data
  let similarMode = null;
  let destroyed = false;

  const root = h('div', { class: 'pole-view', dataset: { print: 'active' } });
  const crumbs = h('nav', { class: 'crumbs no-print', 'aria-label': t('nav.breadcrumb') });
  const head = h('header', { class: 'pole-head' });
  const actions = h('div', { class: 'pole-actions no-print' });
  const tablist = h('div', { class: 'tabs no-print', role: 'tablist', 'aria-label': t('pole.sections') });
  const panels = h('div', { class: 'panels' });
  const similarBox = h('aside', { class: 'similar no-print', 'aria-label': t('similar.title') });
  root.append(crumbs, head, actions, h('div', { class: 'pole-layout' }, h('div', { class: 'pole-main' }, tablist, panels), similarBox));
  container.appendChild(root);

  const panelEls = {};
  const tabEls = {};
  for (const name of TABS) {
    const btn = h('button', { type: 'button', role: 'tab', id: `tab-${name}`, class: 'tab', 'aria-controls': `panel-${name}`, 'aria-selected': 'false', tabindex: '-1' }, h('span', { class: 'tab-label' }, t(`tab.${name}`)), h('span', { class: 'tab-state', 'aria-hidden': 'true' }));
    btn.addEventListener('click', () => selectTab(name, true));
    btn.addEventListener('keydown', (ev) => {
      const i = TABS.indexOf(name);
      let next = null;
      if (ev.key === 'ArrowRight') next = TABS[(i + 1) % TABS.length];
      else if (ev.key === 'ArrowLeft') next = TABS[(i - 1 + TABS.length) % TABS.length];
      else if (ev.key === 'Home') next = TABS[0];
      else if (ev.key === 'End') next = TABS[TABS.length - 1];
      if (next) {
        ev.preventDefault();
        selectTab(next, true);
        tabEls[next].focus();
      }
    });
    tabEls[name] = btn;
    tablist.appendChild(btn);
    const panel = h('section', { class: 'tabpanel', role: 'tabpanel', id: `panel-${name}`, 'aria-labelledby': `tab-${name}`, tabindex: '0', hidden: true, dataset: { tab: name } });
    panelEls[name] = panel;
    panels.appendChild(panel);
  }

  function hashFor(next = {}) {
    const p = { tab: (next.tab ?? tab) === 'modes' ? null : next.tab ?? tab, ex: maxExamples === 3 ? null : maxExamples };
    if (next.mode != null) p.mode = next.mode;
    if (next.sub != null) p.sub = next.sub;
    return buildHash('pole', pid, p);
  }

  function fetchSection(name) {
    const key = `${name}|${maxExamples}`;
    if (!cache.has(key)) {
      const p = api(`/api/v1/poles/${encodeURIComponent(pid)}`, { params: { include: TAB_INCLUDE[name], max_examples: maxExamples }, signal: abort.signal });
      p.catch(() => cache.delete(key));
      cache.set(key, p);
    }
    return cache.get(key);
  }

  // ------------------------------------------------------------------ header

  function renderCrumbs() {
    replace(crumbs,
      h('a', { href: ctx.lastSearchHash(), class: 'crumb-back' }, icon('back', 15), t('pole.backToSearch')),
      h('span', { class: 'crumb-sep', 'aria-hidden': 'true' }, '/'),
      h('a', { href: neuronHash(didOf(pid)) }, displayId(pid)),
      h('span', { class: 'crumb-sep', 'aria-hidden': 'true' }, '/'),
      h('span', { 'aria-current': 'page' }, /__neg$/.test(pid) ? t('pole.neg') : t('pole.pos')));
  }

  function renderHeadSkeleton() {
    replace(head, h('div', { class: 'pole-title' }, h('h1', { class: 'pole-id' }, poleLabel(pid))), skeleton(3, 'sk-head'));
    document.title = `${signChar(/__neg$/.test(pid) ? 'neg' : 'pos')} ${displayId(pid)} · Neuron Atlas`;
  }

  function renderHead(detail) {
    const p = detail.pole;
    pid = p.pid;
    const other = `${p.did}__${p.pole === 'pos' ? 'neg' : 'pos'}`;
    const stat = (label, value, title, raw) => (value == null || value === '' ? null : h('div', { class: 'kv', title: title || null }, h('dt', { class: raw ? 'kv-raw' : null }, label), h('dd', null, value)));
    const theta = detail.theta;
    replace(head,
      h('div', { class: 'pole-title' },
        h('h1', { class: 'pole-id' }, poleLabel(p.pid, p.pole)),
        h('div', { class: 'pole-badges' },
          catBadge(p.category, p.category_words),
          p.verdict ? h('span', { class: 'badge' }, t(`verdict.${p.verdict}`, null, p.verdict)) : null,
          sampleBadge(p),
          mirrorBadge(p))),
      p.headline ? h('p', { class: 'pole-headline' }, p.headline) : null,
      p.description && p.description !== p.headline ? clampText(p.description, { lines: 3, moreLabel: t('more'), lessLabel: t('less'), cls: 'pole-desc' }) : null,
      h('dl', { class: 'kvs' },
        stat(t('pole.layer'), fmtInt(p.layer)),
        stat(t('pole.expert'), p.expert == null ? t('pole.shared') : fmtInt(p.expert)),
        stat(t('pole.neuron'), fmtInt(p.neuron)),
        stat(t('pole.modes'), p.n_modes == null ? null : fmtInt(p.n_modes)),
        stat(t('pole.subcircuits'), p.n_subcircuits == null ? null : fmtInt(p.n_subcircuits)),
        stat(`θ ${t('pole.threshold')}`, theta == null ? null : fmtNum(theta, 4), t('pole.thetaTip'), true),
        stat(t('pole.simulatability'), p.simulatability == null ? null : fmtPct(p.simulatability, 1), t('pole.simTip')),
        stat(t('pole.mainDamage'), p.main_damage_share == null ? null : fmtPct(p.main_damage_share, 1), t('pole.mainDamageTip')),
        stat(t('pole.firings'), detail.n_firings == null ? null : fmtInt(detail.n_firings), t('pole.firingsTip')),
        stat(t('pole.closedShare'), detail.closed_gate_share == null ? null : fmtPct(detail.closed_gate_share, 1), t('pole.closedShareTip'))),
      h('div', { class: 'pole-links no-print' },
        h('a', { class: 'btn btn-small', href: poleHash(other, { tab: tab === 'modes' ? null : tab }) }, h('span', { class: `sign sign-${p.pole === 'pos' ? 'neg' : 'pos'}` }, signChar(p.pole === 'pos' ? 'neg' : 'pos')), t('pole.otherPole')),
        h('a', { class: 'btn btn-small', href: neuronHash(p.did) }, icon('graph', 15), t('pole.neuronView')),
        /^https:\/\//i.test(p.atlas_url || '') ? h('a', { class: 'btn btn-small', href: p.atlas_url, target: '_blank', rel: 'noopener noreferrer', dataset: { original: '1' } }, icon('external', 15), t('pole.openOriginal')) : h('span', { class: 'muted small', title: t('pole.notOnGoodfireTip') }, t('pole.notOnGoodfire'))));
    checkClamps(head);
    // which tabs have data
    for (const name of TABS) {
      const src = TAB_SOURCE[name];
      const available = (p.available || []).includes(src);
      const mirrored = (p.mirrored || []).includes(src);
      const el = tabEls[name];
      el.classList.toggle('tab-absent', !available && !(name === 'modes' && (p.available || []).includes('islands')));
      el.classList.toggle('tab-remote', available && !mirrored);
      el.title = !available ? t('source.absentShort') : !mirrored ? t('source.notMirroredShort') : '';
    }
  }

  // ------------------------------------------------------------------ export / copy / print

  function includeList(format) {
    const parts = EXPORT_PARTS.filter((p) => scope.parts.includes(p));
    if (format === 'json' && scope.tokens) parts.push('tokens');
    return parts.length ? parts : ['modes'];
  }

  function exportParams(format) {
    return { include: includeList(format), max_examples: maxExamples, format };
  }

  // The action bar is built once; changing the scope only rewrites its links (so an
  // open scope popover keeps its button).
  let syncActions = () => {};
  function renderActions() {
    const url = (format) => apiUrl(`/api/v1/poles/${encodeURIComponent(pid)}`, exportParams(format));
    const countEl = h('span', { class: 'count' });
    const scopeBtn = h('button', { type: 'button', class: 'btn', 'aria-label': t('export.scope') }, icon('sliders', 15), h('span', null, t('export.scope')), countEl);
    const mdLink = h('a', { class: 'btn', download: `${pid}.md` }, icon('download', 15), '.md');
    const jsonLink = h('a', { class: 'btn', download: `${pid}.json` }, icon('download', 15), '.json');
    const printLink = h('a', { class: 'btn', target: '_blank', rel: 'noopener', title: t('export.printPageTip') }, icon('print', 15), t('export.printPage'));
    const hint = h('span', { class: 'muted small actions-hint' });
    syncActions = () => {
      countEl.textContent = String(includeList('md').length);
      mdLink.href = url('md');
      jsonLink.href = url('json');
      printLink.href = apiUrl(`/p/${encodeURIComponent(pid)}`, { include: includeList('html'), max_examples: maxExamples });
      hint.textContent = t('export.scopeNow', { parts: includeList('md').map((p) => t(`part.${p}`)).join(', '), n: maxExamples });
    };
    popover(scopeBtn, (panel) => {
      panel.append(h('h3', null, t('export.scopeTitle')), h('p', { class: 'muted small' }, t('export.scopeHint')));
      const list = h('div', { class: 'stack-tight' });
      for (const part of EXPORT_PARTS) {
        const input = h('input', { type: 'checkbox', checked: scope.parts.includes(part) });
        input.addEventListener('change', () => {
          scope.parts = EXPORT_PARTS.filter((x) => (x === part ? input.checked : scope.parts.includes(x)));
          saveParts(scope);
          syncActions();
        });
        list.appendChild(h('label', { class: 'check' }, input, h('span', null, t(`part.${part}`))));
      }
      const tok = h('input', { type: 'checkbox', checked: scope.tokens });
      tok.addEventListener('change', () => {
        scope.tokens = tok.checked;
        saveParts(scope);
        syncActions();
      });
      list.appendChild(h('label', { class: 'check' }, tok, h('span', null, t('part.tokens'))));
      panel.append(list, examplesSelect());
    }, { cls: 'popover-scope' });

    const printBtn = h('button', { type: 'button', class: 'btn', dataset: { action: 'print-view' } }, icon('print', 15), t('export.printThis'));
    printBtn.addEventListener('click', () => printScope(printBtn));

    replace(actions,
      h('div', { class: 'btn-group', role: 'group', 'aria-label': t('export.title') },
        copyButton(t('export.copyMd'), () => api(`/api/v1/poles/${encodeURIComponent(pid)}`, { params: exportParams('md'), as: 'text' })),
        mdLink, jsonLink, printLink, printBtn, scopeBtn),
      hint);
    syncActions();
  }

  function examplesSelect() {
    const sel = h('select', { class: 'input input-inline', 'aria-label': t('pole.examplesPer') }, EXAMPLE_CHOICES.map((n) => h('option', { value: n, selected: n === maxExamples }, String(n))));
    sel.addEventListener('change', () => {
      maxExamples = Number(sel.value);
      ctx.go(hashFor(), { replace: true, silent: true });
      syncActions();
      for (const name of TABS) panelEls[name].dataset.key = '';
      showTab(tab);
    });
    return h('label', { class: 'field-inline' }, h('span', null, t('pole.examplesPer')), sel);
  }

  async function printScope(btn) {
    const wanted = TABS.filter((name) => {
      if (name === 'modes') return scope.parts.includes('modes') || scope.parts.includes('examples');
      return scope.parts.includes(name);
    });
    if (!wanted.includes(tab)) wanted.push(tab);
    btn.disabled = true;
    try {
      await Promise.all(wanted.map((name) => ensurePanel(name)));
    } catch {
      /* print what could be loaded */
    }
    btn.disabled = false;
    for (const name of TABS) panelEls[name].classList.toggle('print-on', wanted.includes(name));
    root.dataset.print = 'scope';
    const reset = () => {
      root.dataset.print = 'active';
      window.removeEventListener('afterprint', reset);
    };
    window.addEventListener('afterprint', reset);
    window.print();
  }

  // ------------------------------------------------------------------ source notices

  function sourceNotice(detail, name) {
    const src = TAB_SOURCE[name];
    const state = (detail.sources || {})[src];
    if (state === 'not_mirrored') {
      const box = h('div', { class: 'mirror-box' });
      const btn = h('button', { type: 'button', class: 'btn btn-primary btn-small', dataset: { action: 'fetch-pole' } }, icon('download', 15), t('source.fetchPole'));
      btn.addEventListener('click', () => fetchThisPole(box));
      const hint = h('span', { class: 'muted small' }, t('source.fetchHint'));
      box.append(btn, hint);
      // A server that runs offline cannot fetch: say so instead of offering a button that fails.
      ctx.infoReady.then((info) => {
        const st = (info && info.settings) || {};
        if (st.offline === true || st.allow_remote_fetch === false) {
          btn.disabled = true;
          hint.textContent = t('source.offline');
        }
      });
      return notice('warn', t('source.notMirroredTitle'), h('p', null, t('source.notMirroredText', { name: t(`source.${src}`) })), box);
    }
    if (state === 'absent') return notice('info', t('source.absentTitle'), h('p', null, t('source.absentText', { name: t(`source.${src}`) })));
    return null;
  }

  async function fetchThisPole(box) {
    const status = h('span', { class: 'muted small', 'aria-live': 'polite' }, t('source.fetching'));
    const prog = progressBar(0);
    replace(box, prog, status);
    try {
      const job = await startMirror({ selection: { ids: [pid] }, sources: MIRROR_SOURCES }, `pole ${pid}`);
      let final = job;
      if (job && job.id) {
        final = await followJob(job.id, (j) => {
          prog.firstChild.style.width = `${Math.round((j.progress || 0) * 100)}%`;
          prog.setAttribute('aria-valuenow', String(Math.round((j.progress || 0) * 100)));
          status.textContent = t('region.jobMeta', { poles: fmtInt(j.poles), done: fmtInt(j.files_done), total: fmtInt(j.files_to_fetch), pct: fmtPct(j.progress, 0) });
        }, abort.signal);
      }
      if (destroyed) return;
      if (!final || !final.state || final.state === 'done') {
        toast(t('source.fetched'), { kind: 'ok' });
        reloadAll();
      } else {
        replace(box, notice('error', t(`job.state.${final.state}`, null, final.state), h('p', null, final.error || (final.report && final.report.errors || []).join('; ') || '')));
      }
    } catch (err) {
      if (isAbort(err)) return;
      replace(box, errorBox(err, () => fetchThisPole(box)));
    }
  }

  function notesList(detail) {
    const notes = (detail.notes || []).filter(Boolean);
    return notes.length ? h('ul', { class: 'notes' }, notes.map((n) => h('li', null, n))) : null;
  }

  // ------------------------------------------------------------------ tab: modes

  function renderModes(panel, detail) {
    const pole = detail.pole.pole;
    const out = [];
    out.push(h('h2', { class: 'print-only panel-title' }, t('tab.modes')));
    const warn = sourceNotice(detail, 'modes');
    const hasSub = (detail.pole.available || []).includes('subcircuit');
    if (warn && hasSub) out.push(warn);
    else if (!hasSub && !detail.modes.length) {
      const st = (detail.sources || {}).islands;
      out.push(notice('info', t('modes.islandsOnlyTitle'), h('p', null, t('modes.islandsOnlyText'))));
      if (st === 'not_mirrored') out.push(sourceNotice({ sources: { islands: 'not_mirrored' } }, 'islands'));
    }
    if (detail.summary || detail.unified_description) {
      out.push(h('section', { class: 'prose-block' },
        detail.summary ? h('div', null, h('h3', { class: 'label' }, t('modes.summary')), h('p', null, detail.summary)) : null,
        detail.unified_description ? h('div', null, h('h3', { class: 'label' }, t('modes.unified')), clampText(detail.unified_description, { lines: 4, moreLabel: t('more'), lessLabel: t('less') })) : null,
        detail.auto_label ? h('div', null, h('h3', { class: 'label' }, t('modes.autoLabel')), clampText(detail.auto_label, { lines: 2, moreLabel: t('more'), lessLabel: t('less'), cls: 'muted' })) : null));
    }
    const hasExamples = detail.modes.some((m) => m.examples && m.examples.length);
    if (detail.modes.length) {
      out.push(h('div', { class: 'panel-tools' },
        h('h3', { class: 'section-title' }, t('modes.title', { n: detail.modes.length })),
        h('div', { class: 'panel-tools-right no-print' }, examplesSelect()),
        hasExamples ? shadingLegend(pole) : null));
    }
    const maxShare = Math.max(...detail.modes.map((m) => m.share || 0), 0.0001);
    const list = h('div', { class: 'modes' });
    for (const m of detail.modes) {
      const dmg = m.damage || {};
      const simBtn = h('button', { type: 'button', class: 'btn btn-small no-print', title: t('similar.modeTip') }, icon('similar', 14), t('similar.modes'));
      simBtn.addEventListener('click', () => {
        similarMode = m.index;
        loadSimilar();
        similarBox.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
      list.appendChild(h('article', { class: `mode mode-${m.kind} ${m.examples && m.examples.length ? 'has-examples' : ''}`.trim(), id: `mode-${m.index}` },
        h('header', { class: 'mode-head' },
          h('div', { class: 'mode-title' },
            h('span', { class: 'mode-index' }, t('mode.index', { n: m.index })),
            kindBadge(m.kind, pole),
            h('h3', null, m.public_label || m.label || '–')),
          m.label && m.public_label && m.label !== m.public_label ? h('p', { class: 'muted small' }, t('mode.sourceLabel'), ' ', h('span', { class: 'mono' }, m.label)) : null,
          h('div', { class: 'mode-stats' },
            m.share != null ? h('span', { class: 'stat-inline' }, bar(m.share, { max: maxShare }), h('strong', null, fmtPct(m.share)), ' ', t('mode.shareOf')) : null,
            m.n_firings != null ? h('span', { class: 'muted' }, t('mode.firings', { n: fmtInt(m.n_firings) })) : null,
            dmg.ratio != null ? h('span', { class: 'muted', title: t('mode.damageTip', { kl: fmtSig(dmg.per_firing, 3) }) }, t('mode.damage', { x: fmtSig(dmg.ratio, 2) })) : null,
            m.subcircuits && m.subcircuits.length ? h('span', { class: 'mode-subs' }, h('span', { class: 'muted' }, t('mode.subcircuits'), ' '), m.subcircuits.map((sid) => h('a', { class: 'chiplink mono', href: hashFor({ tab: 'subcircuits', sub: sid }) }, sid))) : null,
            simBtn)),
        m.description ? h('p', { class: 'mode-desc' }, m.description) : null,
        renderExamples(m.examples, pole)));
    }
    out.push(list);
    if (!detail.modes.length && detail.islands) out.push(islandsView(detail.islands, pole));
    const nl = notesList(detail);
    if (nl && !(warn && hasSub)) out.push(nl);
    replace(panel, out);
  }

  // ------------------------------------------------------------------ tab: overview

  function renderOverview(panel, detail) {
    const ov = detail.overview;
    const out = [h('h2', { class: 'print-only panel-title' }, t('tab.overview'))];
    const warn = sourceNotice(detail, 'overview');
    if (warn) out.push(warn);
    if (!ov) {
      if (!warn) out.push(notice('empty', t('empty.section')));
      replace(panel, out);
      return;
    }
    const pole = detail.pole.pole;
    if (ov.headline) out.push(h('p', { class: 'ov-headline' }, ov.headline));
    if (ov.summary) out.push(h('p', { class: 'ov-summary' }, ov.summary));

    const situations = ov.situations || [];
    if (situations.length) {
      out.push(h('section', null,
        h('h3', { class: 'section-title' }, t('ov.situations'), h('span', { class: 'count' }, fmtInt(situations.length))),
        h('ol', { class: 'situations' }, situations.map((s) => {
          let ref = null;
          if (s.mode_index != null) ref = h('a', { class: 'chiplink', href: hashFor({ tab: 'modes', mode: s.mode_index }) }, t('mode.index', { n: s.mode_index }));
          else if (s.subcircuit) ref = h('a', { class: 'chiplink mono', href: hashFor({ tab: 'subcircuits', sub: s.subcircuit }) }, s.subcircuit);
          return h('li', { class: 'situation' },
            h('div', { class: 'situation-head' }, h('h4', null, s.name || '–'), ref, s.typical_strength ? h('span', { class: 'muted small' }, s.typical_strength) : null),
            s.explain ? h('p', null, s.explain) : null,
            s.cue ? h('p', { class: `cue example-${pole}` }, h('span', { class: 'label-inline' }, t('ov.cue')), ' ', h('span', { class: 'cue-text' }, cueText(s.cue))) : null,
            s.from_experiments ? h('p', { class: 'from-exp' }, h('span', { class: 'label-inline' }, t('ov.fromExperiments')), ' ', s.from_experiments) : null);
        }))));
    }
    if ((ov.not_this || []).length) {
      out.push(h('section', null, h('h3', { class: 'section-title' }, t('ov.notThis')), h('ul', { class: 'not-this' }, ov.not_this.map((x) => h('li', null, x)))));
    }
    if ((ov.experiments || []).length) {
      out.push(h('section', null,
        h('h3', { class: 'section-title' }, t('ov.experiments'), h('span', { class: 'count' }, fmtInt(ov.experiments.length))),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table-prose' },
          h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('ov.claim')), h('th', { scope: 'col' }, t('ov.test')), h('th', { scope: 'col' }, t('ov.result')))),
          h('tbody', null, ov.experiments.map((e) => h('tr', null, h('td', null, e.claim), h('td', null, e.test), h('td', null, e.result))))))));
    }
    if (ov.open) out.push(h('section', null, h('h3', { class: 'section-title' }, t('ov.open')), h('p', null, ov.open)));

    const scores = ov.simulatability || [];
    if (scores.length) {
      out.push(h('section', null,
        h('h3', { class: 'section-title' }, t('ov.simulatability')),
        h('p', { class: 'muted small' }, t('ov.simulatabilityHint')),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('ov.simSource')), h('th', { scope: 'col', class: 'num' }, t('ov.klRecovered')))),
          h('tbody', null, scores.map((s) => h('tr', { class: s.is_this_description ? 'row-own' : '' },
            h('td', null, s.label || s.source, s.is_this_description ? h('span', { class: 'badge badge-own' }, t('ov.thisDescription')) : null),
            barCell(s.kl_recovered, { max: 1, text: fmtPct(s.kl_recovered, 1) }))))))));
    }
    if (ov.final_description) {
      out.push(h('section', null, h('h3', { class: 'section-title' }, t('ov.final')), h('div', { class: 'prewrap final-desc' }, ov.final_description)));
    }
    const digest = renderCircuit(ov.circuit_digest);
    if (digest) out.push(h('section', null, h('h3', { class: 'section-title' }, t('ov.digest')), digest));
    if (ov.report_markdown) {
      out.push(h('section', { class: 'report' },
        h('details', { open: true, class: 'report-details' },
          h('summary', null, h('h3', { class: 'section-title inline' }, t('ov.report'))),
          renderMarkdown(ov.report_markdown))));
    }
    const foot = [];
    if (ov.scientist_model) foot.push(`${t('ov.scientistModel')}: ${ov.scientist_model}`);
    if (ov.threshold != null) foot.push(`θ = ${fmtNum(ov.threshold, 4)}`);
    if (foot.length) out.push(h('p', { class: 'muted small' }, foot.join(' · ')));
    const nl = notesList(detail);
    if (nl) out.push(nl);
    replace(panel, out);
  }

  // ------------------------------------------------------------------ tab: subcircuits

  function profileBar(profile) {
    if (!profile) return null;
    const segs = [['ge_theta', 'p1'], ['mid', 'p2'], ['weak', 'p3'], ['closed', 'p4']].filter(([k]) => profile[k] != null);
    if (!segs.length) return null;
    const total = segs.reduce((s, [k]) => s + Math.max(0, profile[k]), 0) || 1;
    return h('div', { class: 'profile' },
      h('div', { class: 'profile-bar', 'aria-hidden': 'true' }, segs.map(([k, cls]) => h('span', { class: `profile-seg ${cls}`, style: { flexGrow: String(Math.max(0, profile[k]) / total), flexBasis: '0' }, dataset: { tip: `${t(`profile.${k}`)}: ${fmtPct(profile[k])}` } }))),
      h('ul', { class: 'profile-legend' }, segs.map(([k, cls]) => h('li', null, h('span', { class: `legend-swatch ${cls}` }), t(`profile.${k}`), ' ', h('strong', null, fmtPct(profile[k], 0))))));
  }

  function sidesBlock(circuit) {
    if (!circuit) return null;
    const sides = [['gate_drivers', 'sides.gateDrivers'], ['gate_opposers', 'sides.gateOpposers'], ['up_drivers', 'sides.upDrivers'], ['up_opposers', 'sides.upOpposers'], ['readers', 'sides.readers']];
    const total = sides.reduce((n, [k]) => n + (circuit[k] || []).length, 0);
    if (!total) return null;
    return h('details', { class: 'sides' },
      h('summary', null, t('sides.title', { n: fmtInt(total) })),
      h('div', { class: 'sides-grid' }, sides.map(([k, label]) => {
        const list = memberList(circuit[k], { limit: 6 });
        return list ? h('section', { class: 'side' }, h('h5', { title: t(`${label}Tip`) }, t(label)), list) : null;
      })));
  }

  function renderSubcircuits(panel, detail) {
    const out = [h('h2', { class: 'print-only panel-title' }, t('tab.subcircuits'))];
    const warn = sourceNotice(detail, 'subcircuits');
    if (warn) out.push(warn);
    const subs = detail.subcircuits || [];
    if (!subs.length) {
      if (!warn) out.push(notice('empty', t('empty.section')));
      replace(panel, out);
      return;
    }
    const pole = detail.pole.pole;
    const modeLabel = new Map((detail.modes || []).map((m) => [m.index, m.public_label || m.label]));
    const maxShare = Math.max(...subs.map((s) => s.share || 0), 0.0001);
    out.push(h('div', { class: 'panel-tools' },
      h('h3', { class: 'section-title' }, t('subs.title', { n: subs.length })),
      h('div', { class: 'panel-tools-right no-print' }, examplesSelect()),
      shadingLegend(pole)));
    // index table
    out.push(h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact sub-index' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'ID'), h('th', { scope: 'col' }, t('subs.function')), h('th', { scope: 'col' }, t('subs.mode')), h('th', { scope: 'col', class: 'num' }, t('subs.share')), h('th', { scope: 'col', class: 'num' }, t('subs.n')))),
      h('tbody', null, subs.map((s) => h('tr', null,
        h('td', null, h('a', { class: 'mono', href: `#sub-${s.id}`, onclick: (ev) => { ev.preventDefault(); scrollToSub(panel, s.id); } }, s.id)),
        h('td', null, s.function_label || s.meaning.slice(0, 120) || '–'),
        h('td', null, s.mode_index != null ? h('a', { href: hashFor({ tab: 'modes', mode: s.mode_index }), title: modeLabel.get(s.mode_index) || '' }, t('mode.index', { n: s.mode_index })) : '–'),
        barCell(s.share, { max: maxShare }),
        h('td', { class: 'num' }, fmtInt(s.n))))))));

    const list = h('div', { class: 'subs' });
    for (const s of subs) {
      const dmg = s.damage || {};
      const facts = (label, text) => (text ? [h('dt', null, label), h('dd', null, text)] : null);
      list.appendChild(h('article', { class: `sub ${s.examples && s.examples.length ? 'has-examples' : ''}`.trim(), id: `sub-${s.id}` },
        h('header', { class: 'mode-head' },
          h('div', { class: 'mode-title' },
            h('span', { class: 'mode-index mono' }, s.id),
            s.mode_index != null ? h('a', { class: 'chiplink', href: hashFor({ tab: 'modes', mode: s.mode_index }), title: modeLabel.get(s.mode_index) || '' }, '→ ', t('mode.index', { n: s.mode_index })) : null,
            h('h3', null, s.function_label || t('subs.unnamed'))),
          h('div', { class: 'mode-stats' },
            s.share != null ? h('span', { class: 'stat-inline' }, bar(s.share, { max: maxShare }), h('strong', null, fmtPct(s.share)), ' ', t('mode.shareOf')) : null,
            s.n != null ? h('span', { class: 'muted' }, t('mode.firings', { n: fmtInt(s.n) })) : null,
            s.median_a_rel != null ? h('span', { class: 'muted', title: t('subs.medianTip') }, t('subs.median', { x: fmtNum(s.median_a_rel, 2) })) : null,
            dmg.ratio != null ? h('span', { class: 'muted', title: t('mode.damageTip', { kl: fmtSig(dmg.per_firing, 3) }) + (dmg.n_measured != null ? ` · n=${dmg.n_measured}` : '') }, t('mode.damage', { x: fmtSig(dmg.ratio, 2) })) : null,
            s.readable === false ? h('span', { class: 'badge' }, t('subs.unreadable')) : null,
            s.reading_confidence != null ? h('span', { class: 'muted', title: t('subs.confidenceTip') }, t('subs.confidence', { a: s.reading_confidence, b: s.function_confidence ?? '–' })) : null)),
        h('dl', { class: 'facts' },
          facts(t('subs.meaning'), s.meaning),
          facts(t('subs.tokenPattern'), s.token_pattern),
          facts(t('subs.functionLong'), s.function),
          facts(t('subs.gateRole'), s.gate_role),
          facts(t('subs.upRole'), s.up_role)),
        profileBar(s.profile),
        sidesBlock(s.circuit),
        renderExamples(s.examples, pole)));
    }
    out.push(list);
    replace(panel, out);
  }

  function scrollToSub(panel, sid) {
    scrollToTarget(panel.querySelector(`#sub-${CSS.escape(String(sid))}`));
  }

  function scrollToTarget(el) {
    if (!el) return;
    root.querySelectorAll('.is-target').forEach((x) => x.classList.remove('is-target'));
    el.classList.add('is-target');
    requestAnimationFrame(() => el.scrollIntoView({ block: 'start' }));
  }

  /** Scroll to the mode / subcircuit the URL names (once its panel is rendered). */
  function applyFocus() {
    if (tab === 'modes' && focusMode != null) {
      scrollToTarget(panelEls.modes.querySelector(`#mode-${CSS.escape(String(focusMode))}`));
      focusMode = null;
    } else if (tab === 'subcircuits' && focusSub) {
      scrollToSub(panelEls.subcircuits, focusSub);
      focusSub = null;
    }
  }

  // ------------------------------------------------------------------ tab: landscape

  function renderLandscape(panel, detail) {
    const ls = detail.landscape;
    const out = [h('h2', { class: 'print-only panel-title' }, t('tab.landscape'))];
    const warn = sourceNotice(detail, 'landscape');
    if (warn) out.push(warn);
    if (!ls) {
      if (!warn) out.push(notice('empty', t('empty.section')));
      replace(panel, out);
      return;
    }
    const pole = detail.pole.pole;
    const modes = ls.modes || [];
    const fit = ls.damage_fit || {};
    const facts = [];
    if (ls.verdict) facts.push([t('ls.verdict'), t(`verdict.${ls.verdict}`, null, String(ls.verdict))]);
    if (ls.sampled_firings != null) facts.push([t('ls.sampled'), fmtInt(ls.sampled_firings)]);
    if (ls.firings_listed != null) facts.push([t('ls.listed'), fmtInt(ls.firings_listed)]);
    if (ls.gate_closed_firings_listed != null) facts.push([t('ls.closedListed'), fmtInt(ls.gate_closed_firings_listed)]);
    if (fit.slope != null) facts.push([t('ls.slope'), fmtNum(fit.slope, 2)]);
    if (fit.r2 != null) facts.push(['R²', fmtNum(fit.r2, 2)]);
    if (fit.tested_firings != null) facts.push([t('ls.tested'), fmtInt(fit.tested_firings)]);
    out.push(h('p', { class: 'muted' }, t('ls.intro')));
    out.push(h('dl', { class: 'kvs' }, facts.map(([k, v]) => h('div', { class: 'kv' }, h('dt', null, k), h('dd', null, v)))));

    // per-mode table
    const maxFire = Math.max(...modes.map((m) => m.firing_share || 0), 0.0001);
    const maxDmg = Math.max(...modes.map((m) => m.damage_share || 0), 0.0001);
    const maxPer = Math.max(...modes.map((m) => m.damage_per_firing || 0), 0.0001);
    out.push(h('section', null,
      h('h3', { class: 'section-title' }, t('ls.modes')),
      h('p', { class: 'muted small' }, t('ls.modesHint')),
      h('div', { class: 'table-scroll' }, h('table', { class: 'table ls-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, '#'),
          h('th', { scope: 'col' }, t('ls.mode')),
          h('th', { scope: 'col', class: 'num', title: t('ls.firingShareTip') }, t('ls.firingShare')),
          h('th', { scope: 'col', class: 'num', title: t('ls.damageShareTip') }, t('ls.damageShare')),
          h('th', { scope: 'col', class: 'num', title: t('ls.perFiringTip') }, t('ls.perFiring')),
          h('th', { scope: 'col', class: 'num', title: t('ls.testedTip') }, t('ls.testedShort')),
          h('th', { scope: 'col', class: 'num', title: t('ls.sampleShareTip') }, t('ls.sampleShare')))),
        h('tbody', null, modes.map((m) => h('tr', { class: m.index === ls.top_damage_mode ? 'row-own' : '' },
          h('td', { class: 'mono' }, `M${m.index}`),
          h('td', { class: 'ls-label' },
            h('span', null, m.label || '–'),
            m.kind !== 'open' ? kindBadge(m.kind, pole) : null,
            m.index === ls.top_damage_mode ? h('span', { class: 'badge badge-own' }, t('ls.topDamage')) : null,
            m.relation && m.relation.text ? h('div', { class: 'muted small' }, m.relation.text) : null),
          barCell(m.firing_share, { max: maxFire }),
          m.damage_share == null ? h('td', { class: 'num' }, '–') : barCell(m.damage_share, { max: maxDmg }),
          m.damage_per_firing == null ? h('td', { class: 'num' }, '–') : barCell(m.damage_per_firing, { max: maxPer, text: `${fmtSig(m.damage_per_firing, 2)}×` }),
          h('td', { class: 'num' }, fmtInt(m.tested_firings)),
          h('td', { class: 'num' }, m.sample_share == null ? '–' : fmtPct(m.sample_share)))))))));

    // rarity levels
    const levels = ls.levels || [];
    if (levels.length) {
      out.push(h('section', null,
        h('h3', { class: 'section-title' }, t('ls.levels')),
        h('p', { class: 'muted small' }, t('ls.levelsHint')),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, t('ls.level')),
            h('th', { scope: 'col', class: 'num' }, t('ls.median')),
            h('th', { scope: 'col', class: 'num' }, t('ls.range')),
            h('th', { scope: 'col', class: 'num' }, t('ls.frequency')),
            h('th', { scope: 'col', class: 'num' }, t('ls.sampledShort')))),
          h('tbody', null, levels.map((lv) => h('tr', null,
            h('th', { scope: 'row' }, lv.name, lv.source_name ? h('span', { class: 'muted small' }, ` (${lv.source_name})`) : null),
            h('td', { class: 'num' }, lv.median_strength == null ? '–' : `${fmtNum(lv.median_strength, 2)}×`),
            h('td', { class: 'num' }, lv.min_strength == null ? '–' : `${fmtNum(lv.min_strength, 2)}–${fmtNum(lv.max_strength, 2)}×`),
            h('td', { class: 'num' }, lv.one_in_tokens ? t('ls.oneIn', { n: fmtInt(lv.one_in_tokens) }) : '–'),
            h('td', { class: 'num' }, fmtInt(lv.sampled_firings)))))))));

      // modes x levels matrix
      const matrixBox = h('div');
      let which = 'firings_by_mode';
      const drawMatrix = () => {
        const sums = levels.map((lv) => (lv[which] || []).reduce((a, b) => a + (Number(b) || 0), 0));
        replace(matrixBox, h('div', { class: 'table-scroll' }, h('table', { class: 'heatmap heatmap-num level-matrix' },
          h('thead', null, h('tr', null, h('th', { class: 'hm-corner' }), levels.map((lv) => h('th', { scope: 'col', class: 'hm-axis' }, lv.name)))),
          h('tbody', null, modes.map((m, k) => h('tr', null,
            h('th', { scope: 'row', class: 'hm-rowlabel', title: m.label }, h('span', { class: 'mono' }, `M${m.index}`), ' ', h('span', { class: 'hm-rowtext' }, m.label)),
            levels.map((lv, i) => {
              const raw = Number((lv[which] || [])[k]) || 0;
              const v = sums[i] > 0 ? raw / sums[i] : 0;
              return h('td', { class: `hm-cell ${v > 0.55 ? 'hm-dark' : ''}`, style: { '--v': v.toFixed(3) }, dataset: { tip: `M${m.index} · ${lv.name}: ${fmtPct(v)}${which === 'firings_by_mode' ? ` (${fmtInt(raw)})` : ''}` } }, v >= 0.005 ? fmtPct(v, 0) : v > 0 ? '<1' : '·');
            })))))));
      };
      const hasDamage = levels.some((lv) => (lv.damage_by_mode || []).length);
      out.push(h('section', null,
        h('div', { class: 'panel-tools' },
          h('h3', { class: 'section-title' }, t('ls.matrix')),
          hasDamage ? h('div', { class: 'panel-tools-right no-print' }, segmented({ name: 'lsm', label: t('ls.matrix'), value: which, options: [{ value: 'firings_by_mode', label: t('ls.byFirings') }, { value: 'damage_by_mode', label: t('ls.byDamage') }], onChange: (v) => { which = v; drawMatrix(); } })) : null),
        h('p', { class: 'muted small' }, t('ls.matrixHint')),
        matrixBox));
      drawMatrix();
    }

    // similarity matrices
    const sim = ls.mode_similarity || {};
    const labels = modes.map((m) => m.label);
    const maps = [heatmap(sim.by_description, labels, { title: t('ls.simByDescription') }), heatmap(sim.by_firing, labels, { title: t('ls.simByFiring') })].filter(Boolean);
    if (maps.length) {
      out.push(h('section', null,
        h('h3', { class: 'section-title' }, t('ls.similarity')),
        h('p', { class: 'muted small' }, t('ls.similarityHint')),
        h('div', { class: 'heatmaps' }, maps),
        h('div', { class: 'hm-legend', 'aria-hidden': 'true' }, h('span', null, '0'), h('span', { class: 'hm-ramp' }), h('span', null, '1'))));
    }
    const nl = notesList(detail);
    if (nl) out.push(nl);
    replace(panel, out);
  }

  // ------------------------------------------------------------------ tab: circuit, islands

  function renderCircuitTab(panel, detail) {
    const out = [h('h2', { class: 'print-only panel-title' }, t('tab.circuit'))];
    const warn = sourceNotice(detail, 'circuit');
    if (warn) out.push(warn);
    const body = renderCircuit(detail.circuit);
    if (body) out.push(body);
    else if (!warn) {
      out.push(notice('empty', t('circuit.emptyTitle'), h('p', null, t('circuit.emptyText')),
        h('p', null, h('a', { class: 'btn btn-small', href: neuronHash(detail.pole.did) }, icon('graph', 15), t('pole.neuronView')), ' ',
          h('a', { class: 'btn btn-small', href: apiUrl(`/api/v1/neurons/${encodeURIComponent(detail.pole.did)}/raw/meta`), target: '_blank', rel: 'noopener' }, icon('code', 15), t('circuit.raw')))));
    }
    const nl = notesList(detail);
    if (nl) out.push(nl);
    replace(panel, out);
  }

  function renderIslands(panel, detail) {
    const out = [h('h2', { class: 'print-only panel-title' }, t('tab.islands'))];
    const warn = sourceNotice(detail, 'islands');
    if (warn) out.push(warn);
    const view = detail.islands;
    if (!view || (!(view.islands || []).length && !(view.modes || []).length)) {
      if (!warn) out.push(notice('empty', t('empty.section')));
      replace(panel, out);
      return;
    }
    out.push(h('div', { class: 'panel-tools' },
      h('h3', { class: 'section-title' }, t('island.title', { n: (view.islands || []).length })),
      h('div', { class: 'panel-tools-right no-print' }, examplesSelect()),
      shadingLegend(detail.pole.pole)));
    out.push(h('p', { class: 'muted small' }, t('island.hint')));
    out.push(islandsView(view, detail.pole.pole));
    replace(panel, out);
  }

  const RENDER = { modes: renderModes, overview: renderOverview, subcircuits: renderSubcircuits, landscape: renderLandscape, circuit: renderCircuitTab, islands: renderIslands };

  // ------------------------------------------------------------------ tabs

  /** Make sure a panel is rendered for the current settings; resolves when done. */
  async function ensurePanel(name) {
    const panel = panelEls[name];
    const key = `${pid}|${maxExamples}`;
    if (panel.dataset.key === key) {
      if (panel.dataset.ready === '1') applyFocus();
      return;
    }
    panel.dataset.key = key;
    panel.dataset.ready = '';
    replace(panel, skeleton(6, 'sk-panel'));
    try {
      const detail = await fetchSection(name);
      if (destroyed || panel.dataset.key !== key) return;
      RENDER[name](panel, detail);
      panel.dataset.ready = '1';
      checkClamps(panel);
      if (name === tab) applyFocus();
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      panel.dataset.key = '';
      replace(panel, errorBox(err, () => ensurePanel(name)));
    }
  }

  function showTab(name) {
    tab = name;
    for (const n of TABS) {
      const on = n === name;
      tabEls[n].setAttribute('aria-selected', String(on));
      tabEls[n].tabIndex = on ? 0 : -1;
      panelEls[n].hidden = !on;
    }
    return ensurePanel(name);
  }

  function selectTab(name, push) {
    if (name === tab && panelEls[name].dataset.key) return;
    showTab(name);
    if (push) ctx.go(hashFor({ tab: name }), { silent: true });
  }

  // ------------------------------------------------------------------ similar

  let similarAbort = null;
  async function loadSimilar(top = 12) {
    similarAbort?.abort();
    similarAbort = new AbortController();
    const byMode = similarMode != null;
    replace(similarBox,
      h('div', { class: 'similar-head' },
        h('h2', null, byMode ? t('similar.titleMode', { n: similarMode }) : t('similar.title')),
        byMode ? h('button', { type: 'button', class: 'linkish', onclick: () => { similarMode = null; loadSimilar(); } }, t('similar.backToPole')) : null),
      skeleton(8));
    try {
      const res = await api('/api/v1/similar', { params: { pole: pid, mode: byMode ? similarMode : null, top }, signal: similarAbort.signal });
      if (destroyed) return;
      const items = res.items || [];
      const list = h('ol', { class: 'similar-list' }, items.map((it) => h('li', { class: 'similar-item' },
        h('div', { class: 'similar-top' },
          poleLink(it.pid, null, it.mode_index != null ? { mode: it.mode_index } : null),
          h('span', { class: 'score', title: t('similar.scoreTip') }, bar(it.score), fmtNum(it.score, 2))),
        it.label ? h('p', { class: 'similar-label' }, it.mode_index != null ? h('span', { class: 'muted' }, t('mode.index', { n: it.mode_index }), ' · ') : null, it.label) : null,
        !it.label && it.description ? clampText(it.description, { lines: 2, moreLabel: t('more'), lessLabel: t('less'), cls: 'similar-desc' }) : null,
        h('div', { class: 'similar-meta' }, catBadge(it.category), h('span', { class: 'muted' }, `L${it.layer}`)))));
      const more = items.length >= top && top < 60 ? h('button', { type: 'button', class: 'btn btn-small', onclick: () => loadSimilar(top * 2) }, t('loadMore')) : null;
      const regionBtn = h('button', { type: 'button', class: 'btn btn-small', onclick: () => ctx.openRegion({ pole: pid, mode: 'pole' }) }, icon('download', 14), t('similar.region'));
      replace(similarBox,
        h('div', { class: 'similar-head' },
          h('h2', null, byMode ? t('similar.titleMode', { n: similarMode }) : t('similar.title')),
          byMode ? h('button', { type: 'button', class: 'linkish', onclick: () => { similarMode = null; loadSimilar(); } }, t('similar.backToPole')) : null),
        h('p', { class: 'muted small' }, t('similar.method', { method: res.method || '' })),
        items.length ? list : h('p', { class: 'muted' }, t('similar.none')),
        (res.notes || []).length ? h('ul', { class: 'notes' }, res.notes.map((n) => h('li', null, n))) : null,
        h('div', { class: 'similar-actions' }, more, regionBtn));
      checkClamps(similarBox);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(similarBox, h('div', { class: 'similar-head' }, h('h2', null, t('similar.title'))), errorBox(err, () => loadSimilar(top)));
    }
  }

  // ------------------------------------------------------------------ lifecycle

  async function start() {
    renderCrumbs();
    renderHeadSkeleton();
    renderActions();
    showTab(tab);
    loadSimilar();
    try {
      base = await fetchSection('modes');
      if (destroyed) return;
      renderHead(base);
      renderCrumbs();
      renderActions();
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(head, h('div', { class: 'pole-title' }, h('h1', { class: 'pole-id' }, poleLabel(pid))), errorBox(err, reloadAll));
    }
  }

  function reloadAll() {
    abort.abort();
    abort = new AbortController();
    cache = new Map();
    for (const name of TABS) panelEls[name].dataset.key = '';
    start();
  }

  const onMirrored = () => {
    if (!destroyed) reloadAll();
  };
  window.addEventListener('atlas:mirrored', onMirrored);
  start();

  return {
    update(route) {
      if (route.arg !== pid) {
        pid = route.arg;
        similarMode = null;
        tab = TABS.includes(route.params.get('tab')) ? route.params.get('tab') : 'modes';
        focusMode = route.params.get('mode');
        focusSub = route.params.get('sub');
        window.scrollTo(0, 0);
        reloadAll();
        return;
      }
      const nextTab = TABS.includes(route.params.get('tab')) ? route.params.get('tab') : 'modes';
      focusMode = route.params.get('mode');
      focusSub = route.params.get('sub');
      showTab(nextTab);
    },
    destroy() {
      destroyed = true;
      abort.abort();
      similarAbort?.abort();
      window.removeEventListener('atlas:mirrored', onMirrored);
    },
  };
}
