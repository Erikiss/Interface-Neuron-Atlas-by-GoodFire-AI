// A small Markdown renderer for the scientists' reports.  It builds DOM nodes
// directly (no innerHTML), so report text can never inject markup.
// Supported: headings, paragraphs, lists (nested), block quotes, fenced code,
// tables, rules, **bold**, *italic*, `code`, [links](https://…).

import { h } from './dom.js';

// Every mark spans at most 2,000 characters, and code starts only at the first backtick of a run (as in
// CommonMark), so that marks without a partner cost linear time, as in render.py's report parser.  An
// unbounded lazy span, tried from every backtick of a long run, took cubic time and froze the page.
const INLINE = /(?<!`)(`+)(?!`)([\s\S]{0,1999}?[^`])\1(?!`)|\*\*(?=\S)([\s\S]{1,2000}?)\*\*|(?<![\w\\])__(?=\S)([\s\S]{1,2000}?)__(?!\w)|\*(?![\s*])([^*\n]{1,2000}?)(?<!\s)\*|(?<![\w\\])_(?![\s_])([^_\n]{1,2000}?)(?<!\s)_(?!\w)|\[([^\]\n]{1,2000})\]\(([^)\s]{1,2000})(?:\s+"[^"]{0,2000}")?\)|<(https?:\/\/[^>\s]{1,2000})>|\\([\\`*_{}[\]()#+\-.!|>~])/g;

/** Text longer than this keeps its marks as literal text (real paragraphs stay far below it). */
const INLINE_LIMIT = 20000;

function inline(text, into) {
  if (text.length > INLINE_LIMIT) {
    into.appendChild(document.createTextNode(text));
    return into;
  }
  let last = 0;
  let m;
  const re = new RegExp(INLINE.source, 'g');
  while ((m = re.exec(text))) {
    if (m.index > last) into.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1] != null) into.appendChild(h('code', null, m[2].trim()));
    else if (m[3] != null) inline(m[3], into.appendChild(h('strong')));
    else if (m[4] != null) inline(m[4], into.appendChild(h('strong')));
    else if (m[5] != null) inline(m[5], into.appendChild(h('em')));
    else if (m[6] != null) inline(m[6], into.appendChild(h('em')));
    else if (m[7] != null) {
      const url = m[8];
      if (/^https?:\/\//i.test(url)) inline(m[7], into.appendChild(h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' })));
      else into.appendChild(document.createTextNode(m[0]));
    } else if (m[9] != null) into.appendChild(h('a', { href: m[9], target: '_blank', rel: 'noopener noreferrer' }, m[9]));
    else if (m[10] != null) into.appendChild(document.createTextNode(m[10]));
    last = m.index + m[0].length;
  }
  if (last < text.length) into.appendChild(document.createTextNode(text.slice(last)));
  return into;
}

// Block patterns run on single lines.  None of them may split a run of blanks between two optional parts
// (as `\s*#*\s*$` did): on a long line that backtracks in quadratic time.
const RE_HEADING = /^ {0,3}(#{1,6})\s+([\s\S]*)$/;
const RE_FENCE = /^ {0,3}(```+|~~~+)\s*(?:[\w+-]+\s*)?$/;
const RE_RULE = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const RE_LIST = /^(\s*)([-*+]|\d{1,4}[.)])\s+([\s\S]*)$/;
const RE_QUOTE = /^ {0,3}>\s?([\s\S]*)$/;
const RE_TABLE_SEP = /^\s*(?:\|\s*)?:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*(?:\|\s*)?$/;

/** A heading's text: without trailing blanks and without a closing sequence of #s (one that follows a blank). */
function headingText(raw) {
  const text = raw.trimEnd();
  let end = text.length;
  while (end > 0 && text[end - 1] === '#') end--;
  if (end === text.length || (end > 0 && !/\s/.test(text[end - 1]))) return text;
  return text.slice(0, end).trimEnd();
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function parseList(lines, start, baseIndent) {
  const first = RE_LIST.exec(lines[start]);
  const ordered = /\d/.test(first[2]);
  const list = h(ordered ? 'ol' : 'ul');
  let i = start;
  while (i < lines.length) {
    const m = RE_LIST.exec(lines[i]);
    if (!m) break;
    const indent = m[1].replace(/\t/g, '    ').length;
    if (indent < baseIndent) break;
    if (indent > baseIndent + 1) {
      // deeper than this list: belongs to the previous item
      const [sub, next] = parseList(lines, i, indent);
      (list.lastChild || list.appendChild(h('li'))).appendChild(sub);
      i = next;
      continue;
    }
    if (/\d/.test(m[2]) !== ordered && indent === baseIndent && list.childNodes.length) break;
    const parts = [m[3]];
    i++;
    // continuation lines of the item (indented text, not a new block)
    while (i < lines.length && lines[i].trim() && !RE_LIST.test(lines[i]) && /^\s+/.test(lines[i]) && !RE_FENCE.test(lines[i])) {
      parts.push(lines[i].trim());
      i++;
    }
    const li = h('li');
    inline(parts.join(' '), li);
    list.appendChild(li);
    // a blank line followed by another item of the same list keeps the list going
    if (i < lines.length && !lines[i].trim() && i + 1 < lines.length && RE_LIST.test(lines[i + 1])) {
      const peek = RE_LIST.exec(lines[i + 1]);
      if (peek[1].replace(/\t/g, '    ').length >= baseIndent) i++;
    }
  }
  return [list, i];
}

function parseBlocks(lines, into) {
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    let m;
    if ((m = RE_FENCE.exec(line))) {
      const fence = m[1][0];
      const buf = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(fence.repeat(3)) && /^[`~\s]+$/.test(lines[i].trim()))) {
        buf.push(lines[i]);
        i++;
      }
      i++;
      into.appendChild(h('pre', null, h('code', null, buf.join('\n'))));
      continue;
    }
    if ((m = RE_HEADING.exec(line))) {
      // Report headings start two levels down so they sit under the page's own headings.
      const level = Math.min(6, m[1].length + 2);
      inline(headingText(m[2]), into.appendChild(h(`h${level}`, { class: `md-h md-h${m[1].length}` })));
      i++;
      continue;
    }
    if (RE_RULE.test(line)) {
      into.appendChild(h('hr'));
      i++;
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const head = splitRow(line);
      const thead = h('thead', null, h('tr', null, head.map((c) => inline(c, h('th', { scope: 'col' })))));
      const tbody = h('tbody');
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
        const cells = splitRow(lines[i]);
        tbody.appendChild(h('tr', null, head.map((_, k) => inline(cells[k] ?? '', h('td')))));
        i++;
      }
      into.appendChild(h('div', { class: 'table-scroll' }, h('table', { class: 'table md-table' }, thead, tbody)));
      continue;
    }
    if (RE_QUOTE.test(line)) {
      const buf = [];
      while (i < lines.length && RE_QUOTE.test(lines[i])) {
        buf.push(RE_QUOTE.exec(lines[i])[1]);
        i++;
      }
      parseBlocks(buf, into.appendChild(h('blockquote')));
      continue;
    }
    if ((m = RE_LIST.exec(line))) {
      const [list, next] = parseList(lines, i, m[1].replace(/\t/g, '    ').length);
      into.appendChild(list);
      i = next;
      continue;
    }
    const buf = [];
    while (
      i < lines.length && lines[i].trim() && !RE_HEADING.test(lines[i]) && !RE_FENCE.test(lines[i]) && !RE_QUOTE.test(lines[i]) &&
      !(buf.length && RE_LIST.test(lines[i])) && !(buf.length && RE_RULE.test(lines[i]))
    ) {
      buf.push(lines[i].trim());
      i++;
    }
    inline(buf.join('\n'), into.appendChild(h('p')));
  }
  return into;
}

/** Render Markdown source into a container element. */
export function renderMarkdown(source) {
  const root = h('div', { class: 'md' });
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
  try {
    parseBlocks(lines, root);
  } catch {
    // Never lose the text because of a renderer bug: fall back to the plain source.
    while (root.firstChild) root.removeChild(root.firstChild);
    root.appendChild(h('pre', { class: 'md-fallback' }, String(source || '')));
  }
  return root;
}
