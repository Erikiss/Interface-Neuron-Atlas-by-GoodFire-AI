// Neuron Atlas Interface - web page entry point.
// Vanilla ES modules, no build step, no external resources.

import { api, configure } from './js/api.js';
import { h, icon, replace, fmtInt, fmtPct } from './js/dom.js';
import { t, getLang, setLang } from './js/i18n.js';
import { toast, promptDialog, initTooltips, closePopovers } from './js/ui.js';
import { parseHash, buildHash, stateFromParams, filterParams } from './js/state.js';
import { openRegionDialog, refreshJobs, onJobs, activeJobs } from './js/region.js';
import * as searchView from './js/search.js';
import * as poleView from './js/pole.js';
import * as neuronView from './js/neuron.js';
import * as mapView from './js/mapview.js';
import * as batchView from './js/batch.js';
import * as aboutView from './js/about.js';

const VIEWS = { search: searchView, pole: poleView, neuron: neuronView, map: mapView, batch: batchView, about: aboutView };
const main = document.getElementById('main');
let current = { name: null, view: null };
let lastSearch = '#/search';
let info = null;

// --------------------------------------------------------------------------- context shared with the views

let infoReady = null; // promise of the server info (null when it could not be loaded)
let infoFailed = false;

/**
 * Load /api/v1/info.  When that failed (a 401 before the right key was entered), it is loaded
 * again once a key is entered, and the current view, built without it, is built again.
 */
function loadInfo() {
  infoReady = api('/api/v1/info').then(
    (data) => {
      info = data;
      renderFooter();
      if (infoFailed) {
        infoFailed = false;
        rebuildView();
      }
      return data;
    },
    () => {
      infoFailed = true;
      return null;
    },
  );
  return infoReady;
}

const ctx = {
  route: parseHash(),
  get info() {
    return info;
  },
  get infoReady() {
    return infoReady;
  },
  /**
   * Navigate.  silent: only rewrite the URL (the view has already updated itself);
   * replace: do not add a history entry.
   */
  go(hash, { replace: rep = false, silent = false } = {}) {
    if (silent) {
      if (location.hash !== hash) history[rep ? 'replaceState' : 'pushState'](null, '', hash);
      markNav(parseHash(hash).name);
      return;
    }
    if (rep) {
      history.replaceState(null, '', hash);
      route();
    } else if (location.hash === hash) route();
    else location.hash = hash;
  },
  lastSearchHash: () => lastSearch,
  rememberSearch(hash) {
    lastSearch = hash;
    const link = document.querySelector('[data-nav="search"]');
    if (link) link.setAttribute('href', hash);
    const mapLink = document.querySelector('[data-nav="map"]');
    if (mapLink) mapLink.setAttribute('href', buildHash('map', '', filterParams(stateFromParams(parseHash(hash).params))));
  },
  openRegion(opts = {}) {
    const state = opts.state || stateFromParams(parseHash(lastSearch).params);
    return openRegionDialog({ ...opts, state, info });
  },
};

// --------------------------------------------------------------------------- routing

function markNav(name) {
  const nav = name === 'pole' || name === 'neuron' ? 'search' : name;
  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === nav) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

function route() {
  const r = parseHash();
  ctx.route = r;
  closePopovers();
  markNav(r.name);
  if (current.name === r.name && current.view && typeof current.view.update === 'function') {
    current.view.update(r);
    return;
  }
  const changing = current.name !== null;
  current.view?.destroy?.();
  replace(main);
  main.dataset.view = r.name;
  current = { name: r.name, view: null };
  if (changing && r.name !== 'search') window.scrollTo(0, 0);
  // keep keyboard and screen-reader focus inside the new view
  if (changing && !document.querySelector('dialog[open]')) main.focus({ preventScroll: true });
  try {
    current.view = VIEWS[r.name].mount(main, ctx);
  } catch (err) {
    console.error(err);
    replace(main, h('div', { class: 'notice notice-error', role: 'alert' }, h('div', { class: 'notice-body' }, h('strong', null, t('error.generic')), h('p', null, String((err && err.message) || err)))));
  }
}

/** Build the current view from scratch (new language, server info that arrived late). */
function rebuildView() {
  current.view?.destroy?.();
  current = { name: null, view: null };
  route();
}

// --------------------------------------------------------------------------- chrome (header, footer)

function effectiveTheme() {
  const forced = document.documentElement.dataset.theme;
  if (forced === 'light' || forced === 'dark') return forced;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function renderChrome() {
  document.documentElement.lang = getLang();
  for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll('[data-i18n-label]')) {
    el.setAttribute('aria-label', t(el.dataset.i18nLabel));
    if (el.hasAttribute('title')) el.setAttribute('title', t(el.dataset.i18nLabel));
  }
  for (const b of document.querySelectorAll('[data-lang]')) b.setAttribute('aria-pressed', String(b.dataset.lang === getLang()));
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    const dark = effectiveTheme() === 'dark';
    replace(themeBtn, icon(dark ? 'sun' : 'moon', 18));
    themeBtn.setAttribute('aria-label', dark ? t('theme.toLight') : t('theme.toDark'));
    themeBtn.setAttribute('title', dark ? t('theme.toLight') : t('theme.toDark'));
  }
  renderFooter();
}

function renderFooter() {
  const el = document.getElementById('foot-state');
  if (!el) return;
  if (!info) {
    el.textContent = '';
    return;
  }
  const a = info.atlas || {};
  const m = info.mirror || {};
  el.textContent = t('footer.state', { release: (info.release && info.release.id) || '–', poles: fmtInt(a.poles), files: fmtInt(m.files) });
}

function wireChrome() {
  for (const b of document.querySelectorAll('[data-lang]')) {
    b.addEventListener('click', () => {
      if (b.dataset.lang === getLang()) return;
      setLang(b.dataset.lang);
      renderChrome();
      rebuildView();
    });
  }
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('na.theme', next);
    } catch {
      /* ignore */
    }
    renderChrome();
    window.dispatchEvent(new CustomEvent('atlas:theme'));
  });
  const regionBtn = document.getElementById('region-open');
  regionBtn?.addEventListener('click', () => ctx.openRegion({}));
  const badge = document.getElementById('region-badge');
  onJobs(() => {
    if (!badge) return;
    const running = activeJobs();
    badge.hidden = !running.length;
    if (running.length) badge.textContent = fmtPct(running[0].progress || 0, 0);
  });
}

function wireKeys() {
  document.addEventListener('keydown', (ev) => {
    if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const el = ev.target;
    const typing = el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    if (ev.key === '/' && !typing && !document.querySelector('dialog[open]')) {
      ev.preventDefault();
      if (current.name === 'search') current.view?.focusSearch?.();
      else {
        ctx.go(lastSearch);
        requestAnimationFrame(() => current.view?.focusSearch?.());
      }
    }
  });
}

/** The header wraps to two rows on narrow screens: publish its height for the sticky parts below it. */
function wireTopbarHeight() {
  const bar = document.querySelector('.topbar');
  if (!bar || typeof ResizeObserver !== 'function') return;
  new ResizeObserver(() => {
    const height = bar.offsetHeight;
    if (height) document.documentElement.style.setProperty('--topbar-h', `${height}px`);
  }).observe(bar);
}

/** Printing shows everything: collapsed <details> are opened for the print and closed again afterwards. */
function wirePrint() {
  window.addEventListener('beforeprint', () => {
    for (const d of document.querySelectorAll('main details:not([open])')) {
      d.dataset.printOpened = '1';
      d.open = true;
    }
  });
  window.addEventListener('afterprint', () => {
    for (const d of document.querySelectorAll('main details[data-print-opened]')) {
      d.open = false;
      delete d.dataset.printOpened;
    }
  });
}

// --------------------------------------------------------------------------- start

configure({
  askKey: () => promptDialog({ title: t('key.title'), text: t('key.prompt'), label: t('key.label'), type: 'password', okLabel: t('key.save') }),
  onRateLimit: (seconds) => toast(seconds ? t('error.slowDown', { s: seconds }) : t('error.rateLimited'), { kind: 'warn', timeout: 6000 }),
  onKey: () => {
    if (infoFailed) loadInfo();
  },
});

initTooltips();
wireChrome();
wireKeys();
wirePrint();
wireTopbarHeight();
renderChrome();
loadInfo();
window.addEventListener('hashchange', route);
route();

// pick up mirror jobs that are already running on the server
refreshJobs();
