// Example passages, shaded token by token the way the atlas does it.
//
//   own sign (value * sign > 0):  background = pole colour, alpha = min(1, |v| / vmax) * 0.9;
//                                 white text where it reads better than the ink (styles.css:
//                                 alpha > 0.5 in the dark theme, the darkest slate on a light page)
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

function isControl(c) {
  return (c < 32 && c !== 10 && c !== 9) || (c >= 127 && c < 160);
}

/** What the page shows for a control character: its Unicode picture (␀ … ␟, ␡); C1 has none, so its code (boxed by CSS). */
function picture(c) {
  if (c < 32) return String.fromCharCode(C0_PICTURE + c);
  if (c === 127) return '␡';
  return 'x' + c.toString(16);
}

/**
 * Append text to `el`, making control characters visible (newlines and tabs are kept; the
 * caller marks newlines).  The character itself stays in the DOM, so selecting and copying
 * a passage gives the original text; the picture is drawn by CSS and is never copied.
 */
function appendVisible(el, text) {
  let from = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (!isControl(c)) continue;
    if (i > from) el.appendChild(document.createTextNode(text.slice(from, i)));
    el.appendChild(h('span', { class: c >= 128 ? 'ctl ctl-hex' : 'ctl', dataset: { pic: picture(c) } }, text[i]));
    from = i + 1;
  }
  if (from < text.length) el.appendChild(document.createTextNode(from ? text.slice(from) : text));
  return el;
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
  if (text === '') {
    el.classList.add('tok-empty');
    return;
  }
  const parts = text.split('\n');
  parts.forEach((part, i) => {
    if (part) appendVisible(el, part);
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
        // on a light page only the darkest slate reads better with white text than with the ink
        if (sign < 0 && alpha > 0.85) el.classList.add('tok-deep');
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

// The passage box holds the passage and nothing else, so that selecting and copying it gives
// exactly its text: the "…" around a cut window are empty marks drawn by CSS (never copied, and
// a selection can still start or end on them), and the "full context" button sits below the box.
function renderTokens(ex, opts) {
  const box = h('div', { class: 'ex-text' });
  const tokens = ex.tokens;
  const focal = Number.isInteger(ex.focal_index) ? ex.focal_index : -1;
  const long = tokens.length > WINDOW_BEFORE + WINDOW_AFTER + 24 && focal >= 0;
  let expanded = !long;
  const more = long ? h('button', { type: 'button', class: 'linkish ex-more no-print' }) : null;

  const draw = () => {
    while (box.firstChild) box.removeChild(box.firstChild);
    if (ex.pre) box.appendChild(preSpan(ex.pre));
    const from = expanded ? 0 : Math.max(0, focal - WINDOW_BEFORE);
    const to = expanded ? tokens.length : Math.min(tokens.length, focal + WINDOW_AFTER + 1);
    if (from > 0) box.appendChild(h('span', { class: 'ex-cut ex-cut-start', 'aria-hidden': 'true' }));
    const frag = document.createDocumentFragment();
    for (let i = from; i < to; i++) frag.appendChild(tokenSpan(tokens[i][0], tokens[i][1], { ...opts, unit: ex.token_unit, focal: i === focal }));
    box.appendChild(frag);
    if (to < tokens.length) box.appendChild(h('span', { class: 'ex-cut ex-cut-end', 'aria-hidden': 'true' }));
    if (more) {
      more.textContent = expanded ? t('ex.lessContext') : t('ex.moreContext', { n: tokens.length });
      more.setAttribute('aria-expanded', String(expanded));
    }
  };
  more?.addEventListener('click', () => {
    expanded = !expanded;
    draw();
  });
  draw();
  return more ? [box, more] : box;
}

function preSpan(pre) {
  const span = h('span', { class: 'ex-pre' });
  if (pre.length <= PRE_TAIL + 40) return appendVisible(span, pre);
  let cut = pre.length - PRE_TAIL;
  const ws = pre.slice(cut, cut + 30).search(/\s/);
  if (ws >= 0) cut += ws + 1;
  const btn = h('button', { type: 'button', class: 'ex-pre-toggle no-print', title: t('ex.showPre', { n: cut }), 'aria-label': t('ex.showPre', { n: cut }) }, '…');
  btn.addEventListener('click', () => {
    span.textContent = '';
    appendVisible(span, pre);
  });
  span.appendChild(btn);
  return appendVisible(span, pre.slice(cut));
}

/** Without token data: plain text, the focal token («like this») boxed. */
function renderPlain(ex) {
  const box = h('div', { class: 'ex-text ex-plain' });
  if (ex.pre) box.appendChild(preSpan(ex.pre));
  const text = String(ex.text || '');
  const focal = ex.focal_token != null ? `«${ex.focal_token}»` : null;
  const at = focal ? text.indexOf(focal) : -1;
  if (at >= 0) {
    appendVisible(box, text.slice(0, at));
    box.appendChild(appendVisible(h('span', { class: 'tok tok-focal tok-plainfocal' }), focal.slice(1, -1)));
    appendVisible(box, text.slice(at + focal.length));
  } else {
    appendVisible(box, text);
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
