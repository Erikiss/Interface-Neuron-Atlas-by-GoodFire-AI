// "Region herunterladen": choose a set of poles (a Selection), see what it would
// download, start a background mirror job, watch it, cancel it.

import { api, isAbort } from './api.js';
import { h, icon, replace, clear, append, fmtInt, fmtBytes, fmtPct, fmtTime, displayId, debounce } from './dom.js';
import { t } from './i18n.js';
import { openDialog, notice, errorBox, toast } from './ui.js';
import { stateToQuery, hasFilters, poleHash, defaultState } from './state.js';
import { filterChips } from './parts.js';

export const MIRROR_SOURCES = ['subcircuit', 'overview', 'landscape', 'islands', 'circuit'];
const ACTIVE = new Set(['queued', 'running']);

// --------------------------------------------------------------------------- jobs (shared watcher)

const watcher = {
  jobs: [],
  timer: 0,
  listeners: new Set(),
  known: new Map(), // id -> last state
};

function emitJobs() {
  for (const fn of watcher.listeners) {
    try {
      fn(watcher.jobs);
    } catch {
      /* a listener must not stop the others */
    }
  }
}

export function onJobs(fn) {
  watcher.listeners.add(fn);
  fn(watcher.jobs);
  return () => watcher.listeners.delete(fn);
}

/** Refresh the job list once; keeps polling while a job is queued or running. */
export async function refreshJobs() {
  clearTimeout(watcher.timer);
  let jobs;
  try {
    jobs = (await api('/api/v1/mirror/jobs')).jobs || [];
  } catch {
    return watcher.jobs;
  }
  jobs.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  let finished = false;
  for (const job of jobs) {
    const before = watcher.known.get(job.id);
    if (before && ACTIVE.has(before) && !ACTIVE.has(job.state)) finished = true;
    watcher.known.set(job.id, job.state);
  }
  watcher.jobs = jobs;
  emitJobs();
  if (finished) window.dispatchEvent(new CustomEvent('atlas:mirrored'));
  if (jobs.some((j) => ACTIVE.has(j.state))) watcher.timer = setTimeout(refreshJobs, 1200);
  return jobs;
}

export function activeJobs() {
  return watcher.jobs.filter((j) => ACTIVE.has(j.state));
}

export async function startMirror(request, label) {
  const job = await api('/api/v1/mirror', { method: 'POST', body: request, params: { label: label || '' } });
  if (job && job.id) {
    watcher.known.set(job.id, job.state);
    refreshJobs();
  }
  return job;
}

export async function cancelJob(id) {
  await api(`/api/v1/mirror/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
  return refreshJobs();
}

/** Poll one job until it leaves the queue; calls onUpdate(job) on every tick. */
export async function followJob(id, onUpdate, signal) {
  for (;;) {
    if (signal?.aborted) return null;
    const job = await api(`/api/v1/mirror/jobs/${encodeURIComponent(id)}`, { signal });
    onUpdate?.(job);
    if (!ACTIVE.has(job.state)) {
      refreshJobs();
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
}

export function progressBar(fraction, label) {
  const pct = Math.max(0, Math.min(1, fraction || 0)) * 100;
  return h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': pct.toFixed(0), 'aria-label': label || t('region.progress') },
    h('div', { class: 'progress-fill', style: { width: `${pct.toFixed(1)}%` } }));
}

export function jobRow(job) {
  const active = ACTIVE.has(job.state);
  const report = job.report || {};
  const row = h('li', { class: `job job-${job.state}` },
    h('div', { class: 'job-head' },
      h('span', { class: `state state-${job.state}` }, t(`job.state.${job.state}`, null, job.state)),
      h('span', { class: 'job-label' }, job.label || job.id),
      h('span', { class: 'muted job-time' }, fmtTime(job.created_at)),
      active ? h('button', { type: 'button', class: 'btn btn-small btn-danger', onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          await cancelJob(job.id);
        } catch (err) {
          toast(err.detail || err.message, { kind: 'error' });
        }
      } }, icon('stop', 14), t('region.cancel')) : null),
    progressBar(job.progress, `${job.label || job.id}`),
    h('div', { class: 'job-meta muted' },
      t('region.jobMeta', { poles: fmtInt(job.poles), done: fmtInt(job.files_done), total: fmtInt(job.files_to_fetch), pct: fmtPct(job.progress, 0) }),
      report.bytes_fetched ? ` · ${fmtBytes(report.bytes_fetched)}` : '',
      report.failed ? ` · ${t('region.failed', { n: fmtInt(report.failed) })}` : '',
      report.absent ? ` · ${t('region.absent', { n: fmtInt(report.absent) })}` : ''),
    job.error ? h('div', { class: 'job-error' }, job.error) : null,
    report.errors && report.errors.length ? h('details', { class: 'job-errors' }, h('summary', null, t('region.errors', { n: report.errors.length })), h('ul', null, report.errors.map((e) => h('li', null, e)))) : null);
  return row;
}

// --------------------------------------------------------------------------- dialog

/** The current search in words (the same labels as the search page's filter chips). */
function summariseState(state) {
  const parts = filterChips(state).map((c) => c.label);
  if (state.q && state.q.trim()) parts.unshift(t('region.queryText', { q: state.q.trim() }));
  return parts.join(' · ');
}

/**
 * opts: { state (search state), pole (pid to preselect "similar to this pole"),
 *         mode ('search' | 'pole' | 'text'), info }
 */
export function openRegionDialog(opts = {}) {
  const info = opts.info || {};
  const settings = info.settings || {};
  // Downloads go as far as POST /api/v1/mirror accepts them: can_mirror says so (allow_remote_fetch only
  // governs fetching while pages are viewed, not downloads someone asks for).
  const canFetch = !(settings.can_mirror === false || settings.offline === true);
  const dlg = openDialog({ title: t('region.title'), cls: 'dialog-wide region-dialog', onClose: () => { stop(); } });
  let planAbort = null;
  let unsub = null;
  const stop = () => {
    planAbort?.abort();
    unsub?.();
  };

  const form = {
    kind: opts.mode || (opts.pole ? 'pole' : 'search'),
    text: opts.text || '',
    pole: opts.pole || '',
    ids: '',
    top: 200,
    minScore: '',
    neighbours: 0,
    maxPoles: '',
    sources: new Set(MIRROR_SOURCES),
    index: true,
    indexExamples: false,
  };

  const searchSummary = opts.state && hasFilters(opts.state) ? summariseState(opts.state) : t('region.searchAll');

  function selection() {
    const sel = {};
    if (form.kind === 'search') sel.query = stateToQuery(opts.state || defaultState());
    else if (form.kind === 'text') sel.similar_to_text = form.text.trim();
    else if (form.kind === 'pole') sel.similar_to_pole = form.pole.trim();
    else if (form.kind === 'ids') sel.ids = form.ids.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
    else if (form.kind === 'public') sel.public_sample = true;
    else if (form.kind === 'all') sel.all = true;
    if (form.kind === 'text' || form.kind === 'pole') {
      sel.top = Math.max(1, Math.min(10000, Number(form.top) || 200));
      if (form.minScore !== '' && Number.isFinite(Number(form.minScore))) sel.min_score = Number(form.minScore);
    }
    if (form.neighbours) sel.neighbours = Number(form.neighbours);
    if (form.maxPoles !== '' && Number(form.maxPoles) >= 1) sel.max_poles = Math.floor(Number(form.maxPoles));
    return sel;
  }

  function request() {
    return { selection: selection(), sources: MIRROR_SOURCES.filter((s) => form.sources.has(s)), index: form.index, index_examples: form.indexExamples };
  }

  function incomplete() {
    if (form.kind === 'text' && !form.text.trim()) return t('region.needText');
    if (form.kind === 'pole' && !form.pole.trim()) return t('region.needPole');
    if (form.kind === 'ids' && !form.ids.trim()) return t('region.needIds');
    if (!form.sources.size) return t('region.needSources');
    return '';
  }

  // ---- selection
  const radio = (value, label, extra) => {
    const input = h('input', { type: 'radio', name: 'region-kind', value, checked: form.kind === value });
    input.addEventListener('change', () => {
      form.kind = value;
      syncEnabled();
      plan();
    });
    return h('div', { class: 'choice' }, h('label', { class: 'check' }, input, h('span', null, label)), extra || null);
  };
  const textInput = h('input', { type: 'text', class: 'input', value: form.text, placeholder: t('region.textPlaceholder'), 'aria-label': t('region.similarText') });
  textInput.addEventListener('input', () => { form.text = textInput.value; form.kind = 'text'; syncRadios(); plan(); });
  const poleInput = h('input', { type: 'text', class: 'input mono', value: form.pole, placeholder: 'L30_E47_N449__pos', spellcheck: 'false', 'aria-label': t('region.similarPole') });
  poleInput.addEventListener('input', () => { form.pole = poleInput.value; form.kind = 'pole'; syncRadios(); plan(); });
  const idsInput = h('textarea', { class: 'input mono', rows: 2, placeholder: 'L30_E47_N449, L4_E1_N177__neg …', spellcheck: 'false', 'aria-label': t('region.ids') });
  idsInput.addEventListener('input', () => { form.ids = idsInput.value; form.kind = 'ids'; syncRadios(); plan(); });

  const num = (key, attrs) => {
    const input = h('input', { type: 'number', class: 'input input-num', value: form[key], ...attrs });
    input.addEventListener('input', () => { form[key] = input.value; plan(); });
    return input;
  };
  const topInput = num('top', { min: 1, max: 10000, step: 10, 'aria-label': t('region.top') });
  const minScoreInput = num('minScore', { min: 0, max: 1, step: 0.05, placeholder: '0.5', 'aria-label': t('region.minScore') });
  const maxPolesInput = num('maxPoles', { min: 1, step: 100, placeholder: '—', 'aria-label': t('region.maxPoles') });
  const neighbours = h('select', { class: 'input', 'aria-label': t('region.neighbours') }, [0, 1, 2, 3].map((n) => h('option', { value: n, selected: n === form.neighbours }, n === 0 ? t('region.neighbours0') : t('region.neighboursN', { n }))));
  neighbours.addEventListener('change', () => { form.neighbours = Number(neighbours.value); plan(); });

  const atlas = info.atlas || {};
  const kindBox = h('fieldset', { class: 'fieldset' },
    h('legend', null, t('region.selection')),
    radio('search', t('region.currentSearch'), h('div', { class: 'choice-extra muted' }, searchSummary)),
    radio('text', t('region.similarText'), h('div', { class: 'choice-extra' }, textInput)),
    radio('pole', t('region.similarPole'), h('div', { class: 'choice-extra' }, poleInput)),
    radio('ids', t('region.ids'), h('div', { class: 'choice-extra' }, idsInput)),
    radio('public', t('region.public', { n: fmtInt(atlas.poles_in_public_sample ?? 3000) })),
    radio('all', t('region.all', { n: fmtInt(atlas.poles ?? 6488) })),
    h('div', { class: 'form-row' },
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('region.top')), topInput),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('region.minScore')), minScoreInput),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('region.neighbours')), neighbours),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, t('region.maxPoles')), maxPolesInput)));

  function syncRadios() {
    for (const input of kindBox.querySelectorAll('input[name="region-kind"]')) input.checked = input.value === form.kind;
    syncEnabled();
  }
  function syncEnabled() {
    const sim = form.kind === 'text' || form.kind === 'pole';
    topInput.disabled = !sim;
    minScoreInput.disabled = !sim;
  }

  // ---- data sets
  const sourceBox = h('fieldset', { class: 'fieldset' },
    h('legend', null, t('region.sources')),
    h('div', { class: 'check-row' }, MIRROR_SOURCES.map((s) => {
      const input = h('input', { type: 'checkbox', checked: form.sources.has(s) });
      input.addEventListener('change', () => { if (input.checked) form.sources.add(s); else form.sources.delete(s); plan(); });
      return h('label', { class: 'check', title: t(`source.${s}.tip`) }, input, h('span', null, t(`source.${s}`)));
    })),
    h('div', { class: 'check-row' }, [['index', 'region.index'], ['indexExamples', 'region.indexExamples']].map(([key, label]) => {
      const input = h('input', { type: 'checkbox', checked: form[key] });
      input.addEventListener('change', () => { form[key] = input.checked; });
      return h('label', { class: 'check' }, input, h('span', null, t(label)));
    })));

  // ---- estimate
  const planBox = h('div', { class: 'plan', 'aria-live': 'polite' });
  const startBtn = h('button', { type: 'button', class: 'btn btn-primary', disabled: true }, icon('download', 15), t('region.start'));
  const startMsg = h('div', { class: 'start-msg' });
  let lastPlan = null;

  const plan = debounce(async () => {
    planAbort?.abort();
    planAbort = new AbortController();
    const why = incomplete();
    if (why) {
      lastPlan = null;
      startBtn.disabled = true;
      replace(planBox, h('p', { class: 'muted' }, why));
      return;
    }
    planBox.classList.add('is-loading');
    try {
      const res = await api('/api/v1/mirror/plan', { method: 'POST', body: request(), signal: planAbort.signal });
      lastPlan = res;
      renderPlan(res);
    } catch (err) {
      if (isAbort(err)) return;
      lastPlan = null;
      startBtn.disabled = true;
      replace(planBox, errorBox(err));
    } finally {
      planBox.classList.remove('is-loading');
    }
  }, 300);

  function renderPlan(res) {
    const e = res.estimate || {};
    const byKind = Object.entries(e.to_fetch_by_kind || {});
    const stat = (label, value, sub) => h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-label' }, label), sub ? h('div', { class: 'stat-sub muted' }, sub) : null);
    replace(planBox,
      h('div', { class: 'stats' },
        stat(t('region.poles'), fmtInt(e.poles), e.neurons != null ? t('region.neurons', { n: fmtInt(e.neurons) }) : ''),
        stat(t('region.filesToFetch'), fmtInt(e.files_to_fetch), t('region.filesPresent', { present: fmtInt(e.files_present), total: fmtInt(e.files_total) })),
        stat(t('region.downloadSize'), '≈ ' + fmtBytes(e.estimated_download_bytes), t('region.uncompressed', { size: fmtBytes(e.estimated_uncompressed_bytes) }))),
      byKind.length ? h('p', { class: 'muted plan-kinds' }, t('region.byKind') + ' ', byKind.map(([k, n], i) => [i ? ' · ' : '', `${k} ${fmtInt(n)}`])) : null,
      e.files_known_absent ? h('p', { class: 'muted' }, t('region.knownAbsent', { n: fmtInt(e.files_known_absent) })) : null,
      res.sample_poles && res.sample_poles.length ? h('p', { class: 'plan-sample' }, h('span', { class: 'muted' }, t('region.sample') + ' '),
        res.sample_poles.map((pid, i) => [i ? ', ' : '', h('a', { href: poleHash(pid), class: 'mono', onclick: () => dlg.close() }, displayId(pid) + (pid.endsWith('__neg') ? ' −' : ' +'))])) : null,
      e.poles === 0 ? notice('warn', t('region.emptySelection')) : null,
      e.files_to_fetch === 0 && e.poles > 0 ? notice('info', t('region.nothingToFetch')) : null);
    startBtn.disabled = !canFetch || !e.poles || e.files_to_fetch === 0;
  }

  startBtn.addEventListener('click', async () => {
    if (incomplete()) return;
    startBtn.disabled = true;
    clear(startMsg);
    const label = form.kind === 'search' ? `search: ${searchSummary}` : form.kind === 'text' ? `similar: ${form.text}` : form.kind === 'pole' ? `similar: ${form.pole}` : form.kind;
    try {
      const job = await startMirror(request(), label.slice(0, 190));
      if (job && job.id) toast(t('region.started'), { kind: 'ok' });
      else replace(startMsg, notice('info', t('region.nothingToFetch')));
    } catch (err) {
      replace(startMsg, errorBox(err));
    } finally {
      startBtn.disabled = !canFetch || !lastPlan || !lastPlan.estimate || !lastPlan.estimate.files_to_fetch;
    }
  });

  // ---- jobs
  const jobsList = h('ul', { class: 'jobs' });
  unsub = onJobs((jobs) => {
    if (!jobs.length) replace(jobsList, h('li', { class: 'muted' }, t('region.noJobs')));
    else replace(jobsList, jobs.slice(0, 8).map(jobRow));
  });
  refreshJobs();

  const rate = settings.upstream_requests_per_second;
  append(dlg.body, [
    h('p', { class: 'lede' }, t('region.intro')),
    canFetch ? null : notice('warn', t('region.offlineTitle'), h('p', null, t('region.offlineText'))),
    kindBox,
    sourceBox,
    h('section', { class: 'plan-section' }, h('h3', null, t('region.estimate')), planBox),
    h('div', { class: 'dialog-actions dialog-actions-start' }, startBtn, h('p', { class: 'muted polite' }, rate ? t('region.politeRate', { n: fmtInt(rate) }) : t('region.polite'))),
    startMsg,
    h('section', { class: 'jobs-section' }, h('h3', null, t('region.jobs')), jobsList)]);
  syncEnabled();
  plan();
  return dlg;
}
