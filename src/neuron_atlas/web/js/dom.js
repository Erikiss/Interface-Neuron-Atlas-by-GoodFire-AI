// Small DOM helpers. Everything that reaches the page goes through textContent /
// createElement, never through innerHTML: atlas texts are untrusted data.

import { getLang } from './i18n.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** Create an element. `attrs` keys: class, dataset, style (object), on<event>, any attribute. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'class') el.setAttribute('class', v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
    else if (k === 'dataset') {
      for (const [dk, dv] of Object.entries(v)) if (dv != null) el.dataset[dk] = dv;
    } else if (k === 'style' && typeof v === 'object') {
      for (const [sk, sv] of Object.entries(v)) {
        if (sv == null) continue;
        if (sk.startsWith('--')) el.style.setProperty(sk, sv);
        else el.style[sk] = sv;
      }
    }
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected' || k === 'indeterminate') el[k] = v;
    else if (k === 'htmlFor') el.htmlFor = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
}

export function append(el, children) {
  for (const c of children) {
    if (c == null || c === false || c === '') continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replace(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

// --------------------------------------------------------------------------- icons

const ICONS = {
  search: 'M11 4.5a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13M16 16l5 5',
  help: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 16.6v.1',
  dice: 'M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1M8.5 8.5v.1M15.5 8.5v.1M12 12v.1M8.5 15.5v.1M15.5 15.5v.1',
  download: 'M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14',
  copy: 'M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  print: 'M7 8V4h10v4M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2M7 14h10v6H7z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  close: 'M6 6l12 12M18 6L6 18',
  sun: 'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
  filter: 'M4 5h16l-6 7.5V19l-4-2v-4.5z',
  chevron: 'M9 5l7 7-7 7',
  back: 'M11 5l-7 7 7 7M4 12h16',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  info: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 11v6M12 7.5v.1',
  warn: 'M12 4l9.5 16h-19zM12 10v4.5M12 17.4v.1',
  map: 'M9 4l6 2 6-2v14l-6 2-6-2-6 2V6zM9 4v14M15 6v14',
  list: 'M8 6h12M8 12h12M8 18h12M4 6v.1M4 12v.1M4 18v.1',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  reset: 'M4 4v5h5M4.5 9a8 8 0 1 1-.5 3',
  code: 'M8 7l-5 5 5 5M16 7l5 5-5 5',
  similar: 'M8 5a4 4 0 1 0 0 8a4 4 0 1 0 0-8M16 11a4 4 0 1 0 0 8a4 4 0 1 0 0-8',
  key: 'M15.5 4a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9M12.3 11.7 4 20M4 20h3M8 16l2 2',
  stop: 'M7 7h10v10H7z',
  sliders: 'M4 7h10M18 7h2M4 17h2M10 17h10M16 5v4M8 15v4',
  graph: 'M6 6a2 2 0 1 0 0 4a2 2 0 1 0 0-4M18 4a2 2 0 1 0 0 4a2 2 0 1 0 0-4M18 16a2 2 0 1 0 0 4a2 2 0 1 0 0-4M7.6 9.4l8.8 7.4M8 7.6l8-1.3',
  more: 'M5 12v.1M12 12v.1M19 12v.1',
};

/** Inline SVG icon (stroke style); decorative unless a label is given. */
export function icon(name, size = 16, label) {
  const el = svg(
    'svg',
    {
      class: 'icon', viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
      'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      'aria-hidden': label ? null : 'true', role: label ? 'img' : null, focusable: 'false',
    },
    svg('path', { d: ICONS[name] || ICONS.info }),
  );
  if (label) el.setAttribute('aria-label', label);
  return el;
}

// --------------------------------------------------------------------------- numbers

function locale() {
  return getLang() === 'de' ? 'de-DE' : 'en-US';
}

const nfCache = new Map();
function nf(opts) {
  const key = locale() + JSON.stringify(opts);
  let f = nfCache.get(key);
  if (!f) nfCache.set(key, (f = new Intl.NumberFormat(locale(), opts)));
  return f;
}

export function fmtInt(n) {
  return n == null || !Number.isFinite(n) ? '–' : nf({ maximumFractionDigits: 0 }).format(n);
}

export function fmtNum(n, digits = 2) {
  if (n == null || !Number.isFinite(n)) return '–';
  return nf({ maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(n);
}

/** Significant-digit formatting for values that span orders of magnitude (KL damage etc.). */
export function fmtSig(n, sig = 3) {
  if (n == null || !Number.isFinite(n)) return '–';
  if (n !== 0 && Math.abs(n) < 1e-3) return n.toExponential(sig - 1).replace('.', getLang() === 'de' ? ',' : '.');
  return nf({ maximumSignificantDigits: sig }).format(n);
}

/** 0..1 -> "44,2 %". */
export function fmtPct(x, digits = 1) {
  if (x == null || !Number.isFinite(x)) return '–';
  const v = x * 100;
  const d = Math.abs(v) > 0 && Math.abs(v) < 1 && digits < 2 ? 2 : digits;
  return nf({ maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v) + (getLang() === 'de' ? ' %' : '%');
}

export function fmtBytes(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  if (n < 1024) return fmtInt(n) + ' B';
  if (n < 1024 * 1024) return fmtNum(n / 1024, 0) + ' kB';
  if (n < 1024 ** 3) return fmtNum(n / 1024 / 1024, 1) + ' MB';
  return fmtNum(n / 1024 ** 3, 2) + ' GB';
}

export function fmtTime(epochSeconds) {
  if (!epochSeconds) return '';
  try {
    return new Date(epochSeconds * 1000).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

// --------------------------------------------------------------------------- ids

/** L30_E47_N449 -> L30/E47/N449 */
export function displayId(id) {
  return String(id || '').replace(/__(pos|neg)$/, '').replace(/_/g, '/');
}

export function poleOf(pid) {
  const m = /__(pos|neg)$/.exec(pid || '');
  return m ? m[1] : null;
}

export function didOf(pid) {
  return String(pid || '').replace(/__(pos|neg)$/, '');
}

export function signChar(pole) {
  return pole === 'neg' ? '−' : '+';
}

/** "+ L30/E47/N449" with the sign coloured by pole. */
export function poleLabel(pid, pole) {
  const p = pole || poleOf(pid) || 'pos';
  return [h('span', { class: `sign sign-${p}`, 'aria-hidden': 'true' }, signChar(p)), h('span', { class: 'idtext' }, displayId(pid))];
}

// --------------------------------------------------------------------------- text

/**
 * Build a function that wraps the query's words in <mark>.  `q` is the raw
 * search-box text (inline filters, quotes, -exclusions, prefix*, OR are understood).
 */
export function makeHighlighter(q, match) {
  const terms = [];
  const text = String(q || '').replace(/(^|\s)(layer|l|expert|e|neuron|n|pole|cat|category|verdict|modes|sim|simulatability|damage|has|lacks|sample|in|sort|group)\s*:\s*("[^"]*"|\S+)/gi, ' ');
  let re = null;
  if (match === 'regex') {
    try {
      const src = text.trim();
      if (src) re = new RegExp(src, 'giu');
    } catch {
      re = null;
    }
  } else if (match !== 'semantic' || text.trim()) {
    const tokenRe = /(-?)"([^"]*)"|(\S+)/g;
    let m;
    while ((m = tokenRe.exec(text))) {
      if (m[3] != null) {
        const raw = m[3];
        if (/^(OR|AND|NOT)$/.test(raw) || raw.startsWith('-')) continue;
        const word = raw.replace(/\*+$/, '');
        if (word.length >= 2 || /[^\x00-\x7f]/.test(word)) terms.push(word);
      } else if (!m[1] && m[2].trim()) {
        terms.push(m[2].trim());
      }
    }
    if (terms.length) {
      terms.sort((a, b) => b.length - a.length);
      const body = terms.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
      try {
        re = new RegExp(body, 'giu');
      } catch {
        re = null;
      }
    }
  }
  return function highlight(str) {
    const s = String(str ?? '');
    if (!re || !s) return document.createTextNode(s);
    const frag = document.createDocumentFragment();
    let last = 0;
    let n = 0;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(s)) && n < 200) {
      if (m[0] === '') {
        re.lastIndex++;
        continue;
      }
      if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      frag.appendChild(h('mark', null, m[0]));
      last = m.index + m[0].length;
      n++;
    }
    if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
    return frag;
  };
}

/** Text with a clamp and a "more / less" toggle that only appears when the text overflows. */
export function clampText(content, { lines = 2, moreLabel = 'more', lessLabel = 'less', cls = '' } = {}) {
  const p = h('p', { class: `clamp ${cls}`.trim(), style: { '--lines': String(lines) } }, content);
  const btn = h('button', { type: 'button', class: 'linkish clamp-toggle', hidden: true, 'aria-expanded': 'false' }, moreLabel);
  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const open = p.classList.toggle('is-open');
    btn.textContent = open ? lessLabel : moreLabel;
    btn.setAttribute('aria-expanded', String(open));
  });
  const wrap = h('div', { class: 'clamp-wrap' }, p, btn);
  wrap._check = () => {
    if (p.classList.contains('is-open')) return;
    btn.hidden = !(p.scrollHeight > p.clientHeight + 2);
  };
  return wrap;
}

/** Run the overflow check of every clampText() inside `root` (after it is in the document). */
export function checkClamps(root) {
  requestAnimationFrame(() => {
    for (const el of root.querySelectorAll('.clamp-wrap')) el._check?.();
  });
}

export function debounce(fn, ms) {
  let timer = 0;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  wrapped.flush = (...args) => {
    clearTimeout(timer);
    fn(...args);
  };
  return wrapped;
}

/** A horizontal bar for a 0..1 value (purely visual; the number is always printed next to it). */
export function bar(value, { max = 1, cls = '' } = {}) {
  const frac = value == null || !Number.isFinite(value) || max <= 0 ? 0 : Math.max(0, Math.min(1, value / max));
  return h('span', { class: `bar ${cls}`.trim(), 'aria-hidden': 'true' }, h('span', { class: 'bar-fill', style: { width: (frac * 100).toFixed(2) + '%' } }));
}

export function skeleton(lines = 3, cls = '') {
  const el = h('div', { class: `skeleton ${cls}`.trim(), 'aria-hidden': 'true' });
  for (let i = 0; i < lines; i++) el.appendChild(h('div', { class: 'sk-line', style: { width: `${[92, 78, 85, 60, 88, 70][i % 6]}%` } }));
  return el;
}
