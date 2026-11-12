// "Many queries at once": one query per line -> POST /api/v1/search/batch -> a compact table per query.

import { api, isAbort } from './api.js';
import { h, icon, replace, skeleton, fmtInt, fmtNum, fmtPct, displayId, signChar } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox, copyButton, segmented } from './ui.js';
import { MATCH_MODES, buildHash, poleHash } from './state.js';
import { catBadge, poleLink } from './parts.js';

const STORE = 'na.batch';

function loadDraft() {
  try {
    return JSON.parse(sessionStorage.getItem(STORE) || 'null') || {};
  } catch {
    return {};
  }
}

function saveDraft(draft) {
  try {
    sessionStorage.setItem(STORE, JSON.stringify(draft));
  } catch {
    /* ignore */
  }
}

/**
 * Text as literal Markdown on one line: '&' and '<' become entities, so no tag or entity in atlas text
 * reaches a renderer that keeps HTML ('Repeated <td> sibling tags' stays one table cell and shows its tag);
 * the characters of Markdown's inline syntax and of table cells are backslash-escaped; line breaks become
 * spaces (a table row is one line).
 */
function mdText(text) {
  return String(text ?? '')
    .split(/\r\n|\r|\n/)
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' ')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/[\\`*_[\]|~#$]/g, '\\$&');
}

/** A note as a paragraph of its own: also nothing at its start that would open a list, a quote or a heading. */
function mdLine(text) {
  return mdText(text).replace(/^(\d{1,9})([.)])/, '$1\\$2').replace(/^[-+>=]/, '\\$&');
}

function shorten(text, n = 180) {
  const s = String(text || '');
  return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
}

/** The text that best explains why a hit matched: its first snippet, else its description. */
function bestText(hit) {
  const m = (hit.matches || []).find((x) => x && x.text);
  if (m) return { text: m.text, mode: m.mode_index, share: m.share };
  return { text: hit.headline || hit.description || '', mode: null, share: null };
}

/** Every value from the server goes through mdText(): labels, snippets and notes are atlas text. */
function toMarkdown(queries, results, groupBy) {
  const out = [];
  results.forEach((res, i) => {
    out.push(`## ${mdText(queries[i])}`, '', `${mdText(res.total)} ${groupBy === 'mode' ? 'modes' : 'poles'} · ${fmtNum(res.took_ms, 1)} ms`, '');
    if (groupBy === 'mode') {
      out.push('| # | Pole | Mode | Share | Label |', '|---|------|------|-------|-------|');
      (res.modes || []).forEach((m, k) => out.push(`| ${k + 1} | ${mdText(`${signChar(m.pole)} ${displayId(m.pid)}`)} | ${mdText(m.mode_index)} | ${typeof m.share === 'number' ? (m.share * 100).toFixed(1) + '%' : ''} | ${mdText(m.label)} |`));
    } else {
      out.push('| # | Pole | Category | Modes | Text |', '|---|------|----------|-------|------|');
      (res.hits || []).forEach((hit, k) => {
        const b = bestText(hit);
        out.push(`| ${k + 1} | ${mdText(`${signChar(hit.pole)} ${displayId(hit.pid)}`)} | ${mdText(hit.category)} | ${mdText(hit.n_modes)} | ${mdText(`${b.mode != null ? `[mode ${b.mode}] ` : ''}${shorten(b.text, 240)}`)} |`);
      });
    }
    const notes = (res.notes || []).filter((n) => typeof n === 'string' && n.trim());
    if (notes.length) out.push('', ...notes.flatMap((n, j) => (j ? ['>', `> ${mdLine(n)}`] : [`> ${mdLine(n)}`])));
    out.push('');
  });
  return out.join('\n');
}

export function mount(container, ctx) {
  const draft = loadDraft();
  let limit = [3, 5, 10, 25].includes(draft.limit) ? draft.limit : 5;
  let match = MATCH_MODES.includes(draft.match) ? draft.match : 'auto';
  let groupBy = draft.groupBy === 'mode' ? 'mode' : 'pole';
  let abort = null;
  let destroyed = false;
  let last = null; // { queries, results, groupBy }

  const area = h('textarea', { class: 'input batch-area mono', rows: 8, spellcheck: 'false', 'aria-label': t('batch.queries'), placeholder: 'braille\n"severe pain" pole:neg\ntokeniz* OR detokeniz*\nfishing cat:mono layer:4' });
  area.value = draft.text || '';
  const limitSel = h('select', { class: 'input input-inline', 'aria-label': t('batch.limit') }, [3, 5, 10, 25].map((n) => h('option', { value: n, selected: n === limit }, String(n))));
  const matchSel = h('select', { class: 'input input-inline', 'aria-label': t('search.match') }, MATCH_MODES.map((m) => h('option', { value: m, selected: m === match }, t(`match.${m}`))));
  const groupSeg = segmented({ name: 'bgroup', label: t('search.groupBy'), value: groupBy, options: [{ value: 'pole', label: t('group.pole') }, { value: 'mode', label: t('group.mode') }], onChange: (v) => { groupBy = v; persist(); } });
  const runBtn = h('button', { type: 'button', class: 'btn btn-primary', dataset: { action: 'run-batch' } }, icon('search', 15), t('batch.run'));
  const out = h('div', { class: 'batch-out', 'aria-live': 'polite' });
  const tools = h('div', { class: 'batch-tools' });

  const persist = () => saveDraft({ text: area.value, limit, match, groupBy });
  limitSel.addEventListener('change', () => { limit = Number(limitSel.value); persist(); });
  matchSel.addEventListener('change', () => { match = matchSel.value; persist(); });
  area.addEventListener('input', persist);
  area.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      run();
    }
  });
  runBtn.addEventListener('click', () => run());

  const root = h('div', { class: 'batch-view' },
    h('div', { class: 'view-head' }, h('h1', null, t('batch.title')), h('p', { class: 'muted' }, t('batch.intro'))),
    area,
    h('div', { class: 'batch-opts' },
      h('label', { class: 'field-inline' }, h('span', null, t('batch.limit')), limitSel),
      h('label', { class: 'field-inline' }, h('span', null, t('search.match')), matchSel),
      h('div', { class: 'field-inline' }, h('span', null, t('search.groupBy')), groupSeg),
      runBtn,
      h('span', { class: 'muted small' }, t('batch.shortcut'))),
    tools,
    out);
  container.appendChild(root);
  document.title = `${t('batch.title')} · Neuron Atlas`;

  async function run() {
    const queries = area.value.split('\n').map((s) => s.trim()).filter(Boolean);
    replace(tools);
    if (!queries.length) {
      replace(out, notice('info', t('batch.empty')));
      return;
    }
    const maxBatch = (ctx.info && ctx.info.settings && ctx.info.settings.max_batch_size) || 200;
    if (queries.length > maxBatch) {
      replace(out, notice('warn', t('batch.tooMany', { n: fmtInt(queries.length), max: fmtInt(maxBatch) })));
      return;
    }
    abort?.abort();
    abort = new AbortController();
    runBtn.disabled = true;
    replace(out, skeleton(8, 'sk-panel'));
    const usedGroup = groupBy;
    try {
      const body = { queries: queries.map((q) => ({ q, limit, match, group_by: usedGroup })) };
      const res = await api('/api/v1/search/batch', { method: 'POST', body, signal: abort.signal });
      if (destroyed) return;
      last = { queries, results: res.results || [], groupBy: usedGroup };
      render(res.took_ms);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(out, errorBox(err, run));
    } finally {
      runBtn.disabled = false;
    }
  }

  function render(took) {
    const { queries, results } = last;
    replace(tools,
      h('span', { class: 'muted' }, t('batch.summary', { n: fmtInt(results.length), ms: fmtNum(took, 1) })),
      copyButton(t('batch.copyMd'), () => toMarkdown(queries, results, last.groupBy), { cls: 'btn btn-small' }));
    replace(out, results.map((res, i) => {
      const q = queries[i];
      const rows = last.groupBy === 'mode' ? res.modes || [] : res.hits || [];
      const openHash = buildHash('search', '', { q, match: match === 'auto' ? null : match, group_by: last.groupBy === 'pole' ? null : last.groupBy });
      let table;
      if (!rows.length) table = h('p', { class: 'muted' }, t('empty.title'));
      else if (last.groupBy === 'mode') {
        table = h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact batch-table batch-table-mode' },
          h('thead', null, h('tr', null, h('th', { scope: 'col', class: 'num' }, '#'), h('th', { scope: 'col' }, t('unit.pole')), h('th', { scope: 'col', class: 'num' }, t('group.mode')), h('th', { scope: 'col', class: 'num' }, t('subs.share')), h('th', { scope: 'col' }, t('graph.label')))),
          h('tbody', null, rows.map((m, k) => h('tr', null,
            h('td', { class: 'num muted' }, k + 1),
            h('td', null, poleLink(m.pid, m.pole, { mode: m.mode_index })),
            h('td', { class: 'num' }, m.mode_index),
            h('td', { class: 'num' }, fmtPct(m.share)),
            h('td', null, m.label))))));
      } else {
        table = h('div', { class: 'table-scroll' }, h('table', { class: 'table table-compact batch-table batch-table-pole' },
          h('thead', null, h('tr', null, h('th', { scope: 'col', class: 'num' }, '#'), h('th', { scope: 'col' }, t('unit.pole')), h('th', { scope: 'col' }, t('filters.category')), h('th', { scope: 'col', class: 'num' }, t('pole.modes')), h('th', { scope: 'col' }, t('batch.text')))),
          h('tbody', null, rows.map((hit, k) => {
            const b = bestText(hit);
            return h('tr', null,
              h('td', { class: 'num muted' }, k + 1),
              h('td', null, h('a', { class: 'pid', href: poleHash(hit.pid, b.mode != null ? { mode: b.mode } : null) }, h('span', { class: `sign sign-${hit.pole}` }, signChar(hit.pole)), h('span', { class: 'idtext' }, displayId(hit.pid)))),
              h('td', null, catBadge(hit.category)),
              h('td', { class: 'num' }, hit.n_modes ?? '–'),
              h('td', null, b.mode != null ? h('span', { class: 'match-field' }, t('mode.index', { n: b.mode }), b.share != null ? ` · ${fmtPct(b.share)}` : '', ' ') : null, shorten(b.text, 220)));
          }))));
      }
      // the query stays plain text (selectable); the link to the search is next to it
      return h('section', { class: 'batch-result' },
        h('header', { class: 'batch-result-head' },
          h('h2', null, q),
          h('span', { class: 'muted' }, `${fmtInt(res.total)} ${last.groupBy === 'mode' ? t('unit.modes', { n: res.total }) : t('unit.poles', { n: res.total })}`),
          h('a', { class: 'small', href: openHash }, t('batch.openInSearch'), ' →')),
        (res.notes || []).length ? h('ul', { class: 'notes' }, res.notes.map((n) => h('li', null, n))) : null,
        table);
    }));
  }

  return {
    destroy() {
      destroyed = true;
      abort?.abort();
      persist();
    },
  };
}
