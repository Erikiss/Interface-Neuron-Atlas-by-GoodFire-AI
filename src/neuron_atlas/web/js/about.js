// About: what this is, the state of the server, the glossary, and how scripts / chatbots use the API.

import { api, isAbort, getApiKey, setApiKey } from './api.js';
import { h, icon, replace, skeleton, bar, fmtInt, fmtBytes } from './dom.js';
import { t } from './i18n.js';
import { notice, errorBox, copyButton, promptDialog, toast } from './ui.js';

function kv(label, value) {
  return value == null || value === '' ? null : h('div', { class: 'kv' }, h('dt', null, label), h('dd', null, value));
}

function countBars(counts, labelOf, order) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return null;
  if (order) entries.sort((a, b) => order(a[0]) - order(b[0]));
  const max = Math.max(...entries.map(([, n]) => Number(n) || 0), 1);
  return h('table', { class: 'table table-compact count-table' }, h('tbody', null, entries.map(([k, n]) => h('tr', null,
    h('th', { scope: 'row' }, labelOf(k)),
    h('td', { class: 'num' }, fmtInt(n)),
    h('td', { class: 'count-bar' }, bar(n, { max }))))));
}

function glossaryList(terms) {
  const dl = h('dl', { class: 'glossary' });
  for (const [term, def] of Object.entries(terms || {})) {
    dl.appendChild(h('dt', { id: `g-${term.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}` }, term));
    if (def && typeof def === 'object') {
      dl.appendChild(h('dd', null, h('dl', { class: 'glossary glossary-sub' }, Object.entries(def).map(([k, v]) => [h('dt', null, h('code', null, k)), h('dd', null, String(v))]))));
    } else {
      dl.appendChild(h('dd', null, String(def)));
    }
  }
  return dl;
}

export function mount(container, ctx) {
  let destroyed = false;
  const abort = new AbortController();
  const infoBox = h('section', { class: 'about-section' }, skeleton(6, 'sk-panel'));
  const glossBox = h('section', { class: 'about-section' }, skeleton(8, 'sk-panel'));
  const keyBox = h('section', { class: 'about-section' });
  const base = location.origin;

  const root = h('div', { class: 'about-view' },
    h('div', { class: 'view-head' }, h('h1', null, t('about.title'))),
    notice('info', t('about.unofficialTitle'),
      h('p', null, t('about.unofficialText')),
      h('p', null, h('a', { class: 'btn btn-small', href: 'https://www.goodfire.com/research/public_neuron_atlas', target: '_blank', rel: 'noopener noreferrer', dataset: { original: '1' } }, icon('external', 15), t('about.openOriginal')))),
    h('section', { class: 'about-section' },
      h('h2', null, t('about.whatTitle')),
      h('p', null, t('about.whatText')),
      h('ul', { class: 'plain-list' }, ['about.point1', 'about.point2', 'about.point3', 'about.point4'].map((k) => h('li', null, t(k))))),
    infoBox,
    h('section', { class: 'about-section' },
      h('h2', null, t('about.apiTitle')),
      h('p', null, t('about.apiText')),
      h('ul', { class: 'link-list' },
        h('li', null, h('a', { href: '/docs', target: '_blank', rel: 'noopener' }, '/docs'), ' – ', t('about.docs')),
        h('li', null, h('a', { href: '/openapi.json', target: '_blank', rel: 'noopener' }, '/openapi.json'), ' – ', t('about.openapi')),
        h('li', null, h('a', { href: '/llms.txt', target: '_blank', rel: 'noopener' }, '/llms.txt'), ' – ', t('about.llms')),
        h('li', null, h('code', null, `${base}/mcp`), ' – ', t('about.mcp'))),
      h('div', { class: 'codeblock' },
        h('pre', null, h('code', null, `curl '${base}/api/v1/search?q=braille&limit=5'\ncurl '${base}/api/v1/poles/L30_E47_N449__pos?include=modes,examples&format=md'`)),
        copyButton(t('copy'), () => `curl '${base}/api/v1/search?q=braille&limit=5'\ncurl '${base}/api/v1/poles/L30_E47_N449__pos?include=modes,examples&format=md'`, { cls: 'btn btn-small' }))),
    keyBox,
    glossBox);
  container.appendChild(root);
  document.title = `${t('about.title')} · Neuron Atlas`;

  function renderKey() {
    const has = !!getApiKey();
    replace(keyBox,
      h('h2', null, t('key.title')),
      h('p', { class: 'muted' }, has ? t('key.stored') : t('key.none')),
      h('p', null,
        h('button', { type: 'button', class: 'btn btn-small', onclick: async () => {
          const key = await promptDialog({ title: t('key.title'), text: t('key.prompt'), label: t('key.label'), type: 'password', okLabel: t('key.save') });
          if (key) {
            setApiKey(key);
            toast(t('key.saved'), { kind: 'ok' });
            renderKey();
          }
        } }, icon('key', 15), has ? t('key.change') : t('key.set')), ' ',
        has ? h('button', { type: 'button', class: 'btn btn-small', onclick: () => { setApiKey(''); renderKey(); } }, t('key.remove')) : null));
  }
  renderKey();

  function renderInfo(info) {
    const a = info.atlas || {};
    const m = info.mirror || {};
    const idx = info.search_index || {};
    const sem = info.semantic || {};
    const s = info.settings || {};
    const built = a.built_at && typeof a.built_at === 'object' ? Object.entries(a.built_at).map(([k, v]) => `${k}: ${String(v).slice(0, 10)}`).join(' · ') : '';
    replace(infoBox,
      h('h2', null, t('about.stateTitle')),
      h('dl', { class: 'kvs' },
        kv(t('about.release'), (info.release && info.release.id) || ''),
        kv(t('about.model'), a.model),
        kv(t('about.version'), info.version),
        kv(t('about.neurons'), fmtInt(a.neurons)),
        kv(t('about.poles'), fmtInt(a.poles)),
        kv(t('about.modes'), fmtInt(a.modes)),
        kv(t('about.publicSample'), fmtInt(a.poles_in_public_sample)),
        kv(t('about.withSubcircuit'), fmtInt(a.poles_with_subcircuit_read)),
        kv(t('about.withOverview'), fmtInt(a.poles_with_overview)),
        kv(t('about.withLandscape'), fmtInt(a.poles_with_landscape)),
        kv(t('about.withIslands'), fmtInt(a.poles_with_islands)),
        kv(t('about.withSim'), fmtInt(a.poles_with_simulatability))),
      built ? h('p', { class: 'muted small' }, `${t('about.built')}: ${built}`) : null,
      h('div', { class: 'about-cols' },
        h('div', null, h('h3', null, t('about.byCategory')), countBars(a.categories, (k) => t(`cat.${k}`, null, k), (k) => ['mono', 'fn', 'poly', 'dark', 'none'].indexOf(k))),
        h('div', null, h('h3', null, t('about.byLayer')), countBars(a.layers, (k) => `${t('pole.layer')} ${k}`, (k) => Number(k)))),
      h('h3', null, t('about.mirrorTitle')),
      h('p', { class: 'muted' }, t('about.mirrorText')),
      h('dl', { class: 'kvs' },
        kv(t('about.mirrorFiles'), fmtInt(m.files)),
        kv(t('about.mirrorStored'), fmtBytes(m.stored_bytes)),
        kv(t('about.mirrorRaw'), fmtBytes(m.raw_bytes)),
        kv(t('about.deepPoles'), fmtInt(idx.poles_with_deep_texts)),
        kv(t('about.labelTexts'), fmtInt(idx.label_texts)),
        kv(t('about.deepTexts'), fmtInt(idx.deep_texts)),
        kv(t('about.exampleTexts'), fmtInt(idx.example_texts)),
        kv(t('about.semantic'), `${sem.method || '–'}${sem.ready ? '' : ` (${t('about.notReady')})`}`),
        kv(t('about.fetch'), s.offline || s.allow_remote_fetch === false ? t('about.fetchOff') : t('about.fetchOn', { n: fmtInt(s.upstream_requests_per_second) }))),
      h('p', null, h('button', { type: 'button', class: 'btn btn-small', onclick: () => ctx.openRegion({}) }, icon('download', 15), t('region.open'))));
  }

  ctx.infoReady.then((info) => {
    if (destroyed) return;
    if (info) renderInfo(info);
    else replace(infoBox, h('h2', null, t('about.stateTitle')), notice('warn', t('error.generic')));
  });

  (async () => {
    try {
      const g = await api('/api/v1/glossary', { signal: abort.signal });
      if (destroyed) return;
      const conventions = g['text conventions'] || {};
      replace(glossBox,
        h('h2', null, t('about.glossary')),
        h('p', { class: 'muted' }, t('about.glossaryHint')),
        g.model ? h('p', null, g.model) : null,
        glossaryList(g.terms),
        Object.keys(conventions).length ? [h('h3', null, t('about.conventions')), h('dl', { class: 'glossary glossary-sub' }, Object.entries(conventions).map(([k, v]) => [h('dt', null, h('code', null, k)), h('dd', null, String(v))]))] : null,
        g.source ? h('p', { class: 'muted small' }, `${t('about.source')}: ${g.source}`) : null);
    } catch (err) {
      if (isAbort(err) || destroyed) return;
      replace(glossBox, h('h2', null, t('about.glossary')), errorBox(err));
    }
  })();

  return {
    destroy() {
      destroyed = true;
      abort.abort();
    },
  };
}
