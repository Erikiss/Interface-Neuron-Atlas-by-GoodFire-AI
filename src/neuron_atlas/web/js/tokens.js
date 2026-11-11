// Example passages, shaded token by token the way the atlas does it.
//
//   own sign (value * sign > 0):  background = pole colour, alpha = min(1, |v| / vmax) * 0.9,
//                                 white text when that alpha > 0.5
//   other sign, |v| > 0.05 vmax:  background = the opposite pole's colour, alpha = 0.6 * min(1, |v| / vmax)
//   value null:                   the token was not routed to this neuron's expert (dotted underline)
//   focal token:                  outline box
//
// vmax is the largest |v| over the examples of the same mode / subcircuit / island.

import { h, fmtNum } from './dom.js';
import { t } from './i18n.js';

const POS_RGB = '224, 86, 27';
const NEG_RGB = '51, 105, 138';

/** Largest |activation| over all tokens of a group of examples (0 when no example carries tokens). */
export function groupVmax(examples) {
  let vmax = 0;
  for (const ex of examples || []) {
    if (!ex.tokens) continue;
    for (const pair of ex.tokens) {
      const v = pair[1];
      if (v != null) {
        const a = Math.abs(v);
        if (a > vmax) vmax = a;
      }
    }
  }
  return vmax;
}

const C0_PICTURE = 0x2400;

/** Make control characters visible; newlines and tabs are kept (the caller marks newlines). */
function visible(text) {
  let out = '';
  let changed = false;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c === 10 || c === 9) out += text[i];
    else if (c < 32) {
      out += String.fromCharCode(C0_PICTURE + c);
      changed = true;
    } else if (c === 127) {
      out += '␡';
      changed = true;
    } else if (c >= 128 && c < 160) {
      out += '▫';
      changed = true;
    } else out += text[i];
  }
  return changed ? out : text;
}

/** Token text as a JS-style literal for the tooltip. */
function literal(text) {
  let out = '';
  for (const ch of text) {
    const c = ch.codePointAt(0);
    if (ch === '\n') out += '\\n';
    else if (ch === '\t') out += '\\t';
    else if (ch === '\r') out += '\\r';
    else if (c < 32 || (c >= 127 && c < 160)) out += '\\x' + c.toString(16).padStart(2, '0');
    else out += ch;
  }
  return out;
}

/** Fill `el` with text, marking each newline with a (non-copied) return symbol. */
function setTokenText(el, text) {
  const shown = visible(text);
  if (shown === '') {
    el.classList.add('tok-empty');
    return;
  }
  if (!shown.includes('\n')) {
    el.textContent = shown;
    return;
  }
  const parts = shown.split('\n');
  parts.forEach((part, i) => {
    if (part) el.appendChild(document.createTextNode(part));
    if (i < parts.length - 1) {
      el.appendChild(h('span', { class: 'nl', 'aria-hidden': 'true' }));
      el.appendChild(document.createTextNode('\n'));
    }
  });
}

function tokenSpan(text, value, { sign, vmax, focal, unit }) {
  const el = document.createElement('span');
  el.className = 'tok';
  setTokenText(el, String(text ?? ''));
  if (value == null) {
    el.classList.add('tok-unrouted');
    el.dataset.tip = `${literal(String(text ?? ''))} · ${t('ex.unrouted')}`;
  } else {
    if (vmax > 0) {
      const a = Math.min(1, Math.abs(value) / vmax);
      if (value * sign > 0) {
        const alpha = a * 0.9;
        el.style.backgroundColor = `rgba(${sign > 0 ? POS_RGB : NEG_RGB}, ${alpha.toFixed(3)})`;
        if (alpha > 0.5) el.classList.add('tok-strong');
        else if (alpha > 0.12) el.classList.add('tok-lit');
      } else if (Math.abs(value) > 0.05 * vmax) {
        el.style.backgroundColor = `rgba(${sign > 0 ? NEG_RGB : POS_RGB}, ${(a * 0.6).toFixed(3)})`;
        if (a * 0.6 > 0.12) el.classList.add('tok-lit');
      }
    }
    // 'theta': multiples of the pole's threshold; 'raw': raw activation (island examples).
    el.dataset.tip = `${literal(String(text ?? ''))} · ${fmtNum(value, 3)}${unit === 'theta' ? ' × θ' : unit === 'raw' ? ` (${t('ex.rawUnit')})` : ''}`;
  }
  if (focal) el.classList.add('tok-focal');
  return el;
}

const WINDOW_BEFORE = 56;
const WINDOW_AFTER = 28;
const PRE_TAIL = 150;

function renderTokens(ex, opts) {
  const box = h('div', { class: 'ex-text' });
  const tokens = ex.tokens;
  const focal = Number.isInteger(ex.focal_index) ? ex.focal_index : -1;
  const long = tokens.length > WINDOW_BEFORE + WINDOW_AFTER + 24 && focal >= 0;
  let expanded = !long;

  const draw = () => {
    while (box.firstChild) box.removeChild(box.firstChild);
    if (ex.pre) box.appendChild(preSpan(ex.pre));
    const from = expanded ? 0 : Math.max(0, focal - WINDOW_BEFORE);
    const to = expanded ? tokens.length : Math.min(tokens.length, focal + WINDOW_AFTER + 1);
    if (from > 0) box.appendChild(h('span', { class: 'ex-ellipsis', 'aria-hidden': 'true' }, '… '));
    const frag = document.createDocumentFragment();
    for (let i = from; i < to; i++) frag.appendChild(tokenSpan(tokens[i][0], tokens[i][1], { ...opts, unit: ex.token_unit, focal: i === focal }));
    box.appendChild(frag);
    if (to < tokens.length) box.appendChild(h('span', { class: 'ex-ellipsis', 'aria-hidden': 'true' }, ' …'));
    if (long) {
      box.appendChild(h('button', { type: 'button', class: 'linkish ex-more no-print', onclick: () => { expanded = !expanded; draw(); } },
        expanded ? t('ex.lessContext') : t('ex.moreContext', { n: tokens.length })));
    }
  };
  draw();
  return box;
}

function preSpan(pre) {
  const shown = visible(pre);
  const span = h('span', { class: 'ex-pre' });
  if (shown.length <= PRE_TAIL + 40) {
    span.textContent = shown;
    return span;
  }
  let cut = shown.length - PRE_TAIL;
  const ws = shown.slice(cut, cut + 30).search(/\s/);
  if (ws >= 0) cut += ws + 1;
  const tail = shown.slice(cut);
  const btn = h('button', { type: 'button', class: 'ex-pre-toggle no-print', title: t('ex.showPre', { n: cut }), 'aria-label': t('ex.showPre', { n: cut }) }, '…');
  const text = document.createTextNode(tail);
  btn.addEventListener('click', () => {
    span.textContent = shown;
  });
  span.append(btn, text);
  return span;
}

/** Without token data: plain text, the focal token («like this») boxed. */
function renderPlain(ex) {
  const box = h('div', { class: 'ex-text ex-plain' });
  if (ex.pre) box.appendChild(preSpan(ex.pre));
  const text = visible(String(ex.text || ''));
  const focal = ex.focal_token != null ? `«${visible(String(ex.focal_token))}»` : null;
  const at = focal ? text.indexOf(focal) : -1;
  if (at >= 0) {
    box.appendChild(document.createTextNode(text.slice(0, at)));
    box.appendChild(h('span', { class: 'tok tok-focal tok-plainfocal' }, focal.slice(1, -1)));
    box.appendChild(document.createTextNode(text.slice(at + focal.length)));
  } else {
    box.appendChild(document.createTextNode(text));
  }
  return box;
}

/**
 * One example passage.  opts: { pole: 'pos'|'neg', vmax }.
 */
export function renderExample(ex, { pole = 'pos', vmax = 0 } = {}) {
  const sign = pole === 'neg' ? -1 : 1;
  const head = h('figcaption', { class: 'ex-head' });
  if (ex.a_rel != null) head.appendChild(h('span', { class: 'ex-strength', title: ex.act != null ? `${t('ex.activation')}: ${fmtNum(ex.act, 4)}` : null }, t('ex.threshold', { x: fmtNum(ex.a_rel, 1) })));
  else if (ex.act != null) head.appendChild(h('span', { class: 'ex-strength' }, `${t('ex.activation')} ${fmtNum(ex.act, 3)}`));
  if (ex.group) head.appendChild(h('span', { class: 'tag' }, t(`ex.group.${ex.group}`, null, ex.group)));
  if (ex.closed_gate) head.appendChild(h('span', { class: 'tag tag-gate', title: t('ex.closedGateTip') }, `${sign > 0 ? 'Q3' : 'Q4'} · ${t('ex.closedGate')}`));
  if (ex.held_out) head.appendChild(h('span', { class: 'tag', title: t('ex.heldOutTip') }, t('ex.heldOut')));
  if (ex.repeats != null && ex.repeats > 1) head.appendChild(h('span', { class: 'tag' }, t('ex.repeats', { n: ex.repeats })));
  if (ex.ref) head.appendChild(h('span', { class: 'ex-ref' }, ex.ref));
  const body = ex.tokens && ex.tokens.length ? renderTokens(ex, { sign, vmax }) : renderPlain(ex);
  return h('figure', { class: `example example-${pole}` }, head, body);
}

/** A list of examples sharing one vmax. */
export function renderExamples(examples, pole) {
  const list = h('div', { class: 'examples' });
  if (!examples || !examples.length) return list;
  const vmax = groupVmax(examples);
  for (const ex of examples) list.appendChild(renderExample(ex, { pole, vmax }));
  return list;
}

/** The legend that explains the shading; shown once per section. */
export function shadingLegend(pole) {
  const own = pole === 'neg' ? NEG_RGB : POS_RGB;
  const other = pole === 'neg' ? POS_RGB : NEG_RGB;
  const sw = (rgb, a) => h('span', { class: 'legend-swatch', style: { backgroundColor: `rgba(${rgb}, ${a})` } });
  return h('div', { class: 'shade-legend', 'aria-label': t('ex.legend') },
    h('span', { class: 'legend-item' }, sw(own, 0.25), sw(own, 0.55), sw(own, 0.9), t('ex.legendOwn')),
    h('span', { class: 'legend-item' }, sw(other, 0.5), t('ex.legendOther')),
    h('span', { class: 'legend-item' }, h('span', { class: 'tok tok-focal legend-tok' }, 'tok'), t('ex.legendFocal')),
    h('span', { class: 'legend-item' }, h('span', { class: 'tok tok-unrouted legend-tok' }, 'tok'), t('ex.legendUnrouted')));
}
