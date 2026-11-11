// Shared widgets: toasts, dialogs, popovers, the token tooltip, clipboard, downloads, notices.

import { api, getApiKey } from './api.js';
import { h, icon, replace } from './dom.js';
import { t } from './i18n.js';

// --------------------------------------------------------------------------- toasts

export function toast(message, { kind = 'info', timeout = 3500 } = {}) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = h('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    icon(kind === 'ok' ? 'check' : kind === 'error' || kind === 'warn' ? 'warn' : 'info', 16),
    h('span', null, message));
  host.appendChild(el);
  // Put the toast host into the top layer so that messages stay visible above modal dialogs.
  if (typeof host.showPopover === 'function') {
    try {
      host.setAttribute('popover', 'manual');
      if (host.matches(':popover-open')) host.hidePopover();
      host.showPopover();
    } catch {
      /* plain fixed positioning still works */
    }
  }
  const remove = () => {
    el.remove();
    if (!host.firstChild && typeof host.hidePopover === 'function') {
      try {
        if (host.matches(':popover-open')) host.hidePopover();
      } catch {
        /* ignore */
      }
    }
  };
  if (timeout) setTimeout(remove, timeout);
  el.addEventListener('click', remove);
}

// --------------------------------------------------------------------------- dialogs

/**
 * A modal <dialog>.  Returns { el, body, close }.  Esc and the backdrop close it;
 * focus returns to the element that opened it.
 */
export function openDialog({ title, cls = '', onClose } = {}) {
  const opener = document.activeElement;
  const body = h('div', { class: 'dialog-body' });
  const titleId = `dlg-${Math.random().toString(36).slice(2, 8)}`;
  const closeBtn = h('button', { type: 'button', class: 'iconbtn', 'aria-label': t('close'), title: t('close') }, icon('close', 18));
  const el = h('dialog', { class: `dialog ${cls}`.trim(), 'aria-labelledby': titleId },
    h('header', { class: 'dialog-head' }, h('h2', { id: titleId }, title || ''), closeBtn),
    body);
  document.body.appendChild(el);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    try {
      el.close();
    } catch {
      /* already closed */
    }
    el.remove();
    onClose?.();
    if (opener && document.contains(opener)) opener.focus?.();
  };
  closeBtn.addEventListener('click', close);
  el.addEventListener('cancel', (ev) => {
    ev.preventDefault();
    close();
  });
  el.addEventListener('mousedown', (ev) => {
    if (ev.target === el) close(); // click on the backdrop
  });
  el.showModal();
  return { el, body, close };
}

/** Ask for a line of text (used for the API key).  Resolves to the text or null. */
export function promptDialog({ title, text, label, type = 'text', okLabel }) {
  return new Promise((resolve) => {
    let result = null;
    const dlg = openDialog({ title, cls: 'dialog-small', onClose: () => resolve(result) });
    const input = h('input', { type, class: 'input', autocomplete: 'off', spellcheck: 'false', 'aria-label': label });
    const form = h('form', { class: 'stack' },
      text ? h('p', { class: 'muted' }, text) : null,
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), input),
      h('div', { class: 'dialog-actions' },
        h('button', { type: 'button', class: 'btn', onclick: () => dlg.close() }, t('cancel')),
        h('button', { type: 'submit', class: 'btn btn-primary' }, okLabel || t('ok'))));
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      result = input.value.trim() || null;
      dlg.close();
    });
    dlg.body.appendChild(form);
    input.focus();
  });
}

// --------------------------------------------------------------------------- popovers

let openPopover = null;

/**
 * Attach a popover panel to a button.  `build(panel, close)` fills the panel each
 * time it opens.  Esc and a click outside close it.
 */
export function popover(button, build, { cls = '', align = 'start' } = {}) {
  const id = `pop-${Math.random().toString(36).slice(2, 8)}`;
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', id);
  let panel = null;

  const close = (refocus = false) => {
    if (!panel) return;
    panel.remove();
    panel = null;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', onDocDown, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', place);
    if (openPopover === close) openPopover = null;
    if (refocus) button.focus();
  };
  const onDocDown = (ev) => {
    if (panel && !panel.contains(ev.target) && !button.contains(ev.target)) close();
  };
  const onKey = (ev) => {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      close(true);
    }
  };
  const place = () => {
    if (!panel) return;
    const r = button.getBoundingClientRect();
    const pw = panel.offsetWidth;
    const vw = document.documentElement.clientWidth;
    let left = align === 'end' ? r.right - pw : r.left;
    left = Math.max(8, Math.min(left, vw - pw - 8));
    panel.style.left = `${left + window.scrollX}px`;
    panel.style.top = `${r.bottom + window.scrollY + 6}px`;
  };
  const open = () => {
    openPopover?.();
    panel = h('div', { class: `popover ${cls}`.trim(), id, role: 'dialog', 'aria-label': button.getAttribute('aria-label') || button.textContent || '' });
    build(panel, close);
    document.body.appendChild(panel);
    button.setAttribute('aria-expanded', 'true');
    place();
    document.addEventListener('mousedown', onDocDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', place);
    openPopover = close;
    const first = panel.querySelector('button, a[href], input, select, textarea');
    first?.focus({ preventScroll: true });
  };
  button.addEventListener('click', (ev) => {
    ev.preventDefault();
    if (panel) close();
    else open();
  });
  return { open, close, isOpen: () => !!panel };
}

export function closePopovers() {
  openPopover?.();
}

// --------------------------------------------------------------------------- tooltip

let tipEl = null;

/** One shared tooltip for dense marks (tokens, heatmap cells, graph nodes): `data-tip` on the element. */
export function initTooltips() {
  tipEl = document.getElementById('tooltip');
  if (!tipEl) return;
  let current = null;
  const hide = () => {
    current = null;
    tipEl.hidden = true;
  };
  const show = (target) => {
    current = target;
    tipEl.textContent = target.dataset.tip;
    tipEl.hidden = false;
    const r = target.getBoundingClientRect();
    const tw = tipEl.offsetWidth;
    const th = tipEl.offsetHeight;
    const vw = document.documentElement.clientWidth;
    let left = r.left + r.width / 2 - tw / 2;
    left = Math.max(6, Math.min(left, vw - tw - 6));
    let top = r.top - th - 6;
    if (top < 4) top = r.bottom + 6;
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  };
  document.addEventListener('pointerover', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('[data-tip]') : null;
    if (target === current) return;
    if (target) show(target);
    else hide();
  });
  document.addEventListener('focusin', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('[data-tip]') : null;
    if (target) show(target);
  });
  document.addEventListener('focusout', hide);
  document.addEventListener('scroll', hide, true);
  document.addEventListener('pointerdown', (ev) => {
    if (ev.pointerType !== 'mouse' && !(ev.target instanceof Element && ev.target.closest('[data-tip]'))) hide();
  });
}

/** Show the shared tooltip at a viewport position (used by the canvas map). */
export function showTipAt(text, x, y) {
  if (!tipEl) return;
  tipEl.textContent = text;
  tipEl.hidden = false;
  const tw = tipEl.offsetWidth;
  const th = tipEl.offsetHeight;
  const vw = document.documentElement.clientWidth;
  let left = Math.max(6, Math.min(x - tw / 2, vw - tw - 6));
  let top = y - th - 12;
  if (top < 4) top = y + 16;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${top}px`;
}

export function hideTip() {
  if (tipEl) tipEl.hidden = true;
}

// --------------------------------------------------------------------------- clipboard

function legacyCopy(text) {
  const ta = h('textarea', { class: 'offscreen', readonly: true, 'aria-hidden': 'true' });
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

/**
 * Copy text that may still be loading.  `source` is a string or a promise of one.
 * Uses ClipboardItem with a promise where the browser supports it (keeps the user
 * gesture alive during the fetch), then writeText, then execCommand, and finally
 * shows the text in a dialog to copy by hand.
 */
export async function copyText(source) {
  const promise = Promise.resolve(source);
  if (navigator.clipboard && window.ClipboardItem && typeof source !== 'string') {
    try {
      const item = new ClipboardItem({ 'text/plain': promise.then((s) => new Blob([s], { type: 'text/plain' })) });
      await navigator.clipboard.write([item]);
      return true;
    } catch {
      /* fall through */
    }
  }
  const text = await promise;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through */
    }
  }
  if (legacyCopy(text)) return true;
  const dlg = openDialog({ title: t('copy.manualTitle'), cls: 'dialog-wide' });
  const ta = h('textarea', { class: 'input copy-area', readonly: true, rows: 16, 'aria-label': t('copy.manualTitle') });
  ta.value = text;
  dlg.body.append(h('p', { class: 'muted' }, t('copy.manualHint')), ta);
  ta.focus();
  ta.select();
  return false;
}

// --------------------------------------------------------------------------- downloads

/**
 * Make `a` (href = an API URL, download = file name) work on a server that wants a key, without
 * the key in its URL (links get copied and shared): when this browser holds a key, a click
 * fetches the file with the key as a header and saves it from memory.
 */
export function downloadLink(a) {
  a.addEventListener('click', async (ev) => {
    if (!getApiKey() || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    try {
      const blob = await api(a.getAttribute('href'), { as: 'blob' });
      const url = URL.createObjectURL(blob);
      const save = h('a', { href: url, download: a.getAttribute('download') || '', hidden: true });
      document.body.appendChild(save);
      save.click();
      save.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      toast(`${t('download.failed')}: ${err.detail || err.message || err}`, { kind: 'error', timeout: 6000 });
    }
  });
  return a;
}

/** A button that copies and confirms visibly ("Copied"). */
export function copyButton(label, getText, { cls = 'btn', iconName = 'copy' } = {}) {
  const span = h('span', null, label);
  const ico = h('span', { class: 'btn-ico' }, icon(iconName, 15));
  const btn = h('button', { type: 'button', class: cls }, ico, span);
  let timer = 0;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const ok = await copyText(getText());
      if (ok) {
        span.textContent = t('copied');
        replace(ico, icon('check', 15));
        btn.classList.add('is-done');
        toast(t('copied'), { kind: 'ok', timeout: 1800 });
      }
    } catch (err) {
      toast(`${t('copy.failed')}: ${err.detail || err.message || err}`, { kind: 'error', timeout: 6000 });
    } finally {
      btn.disabled = false;
      clearTimeout(timer);
      timer = setTimeout(() => {
        span.textContent = label;
        replace(ico, icon(iconName, 15));
        btn.classList.remove('is-done');
      }, 2200);
    }
  });
  return btn;
}

// --------------------------------------------------------------------------- notices

/** Inline message box.  kind: info | warn | error | empty */
export function notice(kind, title, ...children) {
  return h('div', { class: `notice notice-${kind}`, role: kind === 'error' ? 'alert' : 'note' },
    h('span', { class: 'notice-ico' }, icon(kind === 'info' || kind === 'empty' ? 'info' : 'warn', 18)),
    h('div', { class: 'notice-body' }, title ? h('strong', { class: 'notice-title' }, title) : null, ...children));
}

/** Error box for a failed request, with the server's own message and a retry button. */
export function errorBox(err, retry) {
  const status = err && err.status;
  let title = t('error.generic');
  if (status === 400) title = t('error.badRequest');
  else if (status === 401) title = t('error.unauthorized');
  else if (status === 404) title = t('error.notFound');
  else if (status === 409) title = t('error.conflict');
  else if (status === 429) title = t('error.rateLimited');
  else if (status === 502) title = t('error.upstream');
  else if (status === 0) title = t('error.network');
  const detail = (err && (err.detail || err.message)) || String(err);
  return notice('error', title,
    h('p', { class: 'notice-detail' }, detail),
    status === 429 && err.retryAfter ? h('p', null, t('error.retryAfter', { s: err.retryAfter })) : null,
    retry ? h('p', null, h('button', { type: 'button', class: 'btn btn-small', onclick: retry }, t('retry'))) : null);
}

/** Segmented control (radio group).  options: [{ value, label, title }] */
export function segmented({ name, label, options, value, onChange, cls = '' }) {
  const group = h('div', { class: `segmented ${cls}`.trim(), role: 'radiogroup', 'aria-label': label });
  const buttons = [];
  const set = (v, fire) => {
    for (const b of buttons) {
      const on = b.dataset.value === v;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
    }
    if (fire) onChange?.(v);
  };
  options.forEach((opt, i) => {
    const b = h('button', { type: 'button', role: 'radio', class: 'seg', dataset: { value: opt.value, name }, title: opt.title || null }, opt.label);
    b.addEventListener('click', () => set(opt.value, true));
    b.addEventListener('keydown', (ev) => {
      const dir = ev.key === 'ArrowRight' || ev.key === 'ArrowDown' ? 1 : ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 0;
      if (!dir) return;
      ev.preventDefault();
      const next = buttons[(i + dir + buttons.length) % buttons.length];
      next.focus();
      set(next.dataset.value, true);
    });
    buttons.push(b);
    group.appendChild(b);
  });
  set(value, false);
  group._set = (v) => set(v, false);
  return group;
}
