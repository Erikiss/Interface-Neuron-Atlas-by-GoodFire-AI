// UI smoke test for the web page (src/neuron_atlas/web).
//
// Needs a running server and Playwright with Chromium:
//
//   ATLAS_DATA_DIR=... ATLAS_OFFLINE=1 uvicorn --factory neuron_atlas.api:create_app --port 8765 &
//   node tests/e2e/ui_smoke.mjs                       # ATLAS_URL defaults to http://127.0.0.1:8765
//
// Start the server with ATLAS_OFFLINE=1: the test opens pole pages, and an online
// server would fetch their files from goodfire.com.  The test adapts to what the
// mirror holds (a pole that is not mirrored must show the "not downloaded" notice
// instead of content).  Where it can, it checks what the page shows against the API
// (result ids, header figures, mode shares, token shading, copied text) rather than
// only that something is shown.  Exit code 0 = all checks passed; console errors and
// page errors fail the run.  Set ATLAS_E2E_SHOTS=<dir> to keep screenshots of failures.

import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    console.error('Playwright is not installed (npm i -D playwright, or set NODE_PATH to a global install).');
    process.exit(2);
  }
}

const BASE = (process.env.ATLAS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');
const SHOTS = process.env.ATLAS_E2E_SHOTS || '';
const POLE = process.env.ATLAS_E2E_POLE || 'L30_E47_N449__pos';
const QUERY = process.env.ATLAS_E2E_QUERY || 'braille';
const DID = POLE.replace(/__(pos|neg)$/, '');
const Q = encodeURIComponent(QUERY);

let passed = 0;
const failures = [];
const problems = []; // console errors, page errors, unexpected HTTP failures
let allowHttpErrors = false;

function ok(name) {
  passed++;
  console.log(`  ok   ${name}`);
}

async function check(name, fn, page) {
  try {
    await fn();
    ok(name);
  } catch (err) {
    failures.push(`${name}: ${err.message.split('\n')[0]}`);
    console.log(`  FAIL ${name}\n       ${err.message.split('\n').slice(0, 3).join('\n       ')}`);
    if (SHOTS && page) await page.screenshot({ path: `${SHOTS}/fail-${failures.length}.png` }).catch(() => {});
  }
}

function assert(cond, message) {
  if (!cond) throw new Error(message);
}

function watch(page) {
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (allowHttpErrors && /Failed to load resource/.test(text)) return;
    problems.push(`console error: ${text} (${msg.location().url || ''})`);
  });
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`));
  page.on('response', (res) => {
    if (res.status() >= 500) problems.push(`HTTP ${res.status()} ${res.url()}`);
  });
}

const hash = (page) => page.evaluate(() => location.hash);
const count = (page, sel) => page.locator(sel).count();
const getJson = async (page, path) => (await page.request.get(BASE + path)).json();
const getText = async (page, path) => (await page.request.get(BASE + path)).text();
/** The search list has finished loading. */
const settled = (page) => page.waitForFunction(() => !document.querySelector('.results.is-loading'), null, { timeout: 8000 });
/** pid of each row of the result list, in order. */
const rowPids = (page, sel = '.hit-pole') => page.evaluate((s) => [...document.querySelectorAll(s)].map((el) => decodeURIComponent(el.dataset.href.replace(/^#\/pole\//, '').replace(/\?.*$/, ''))), sel);

async function clipboard(page) {
  try {
    return await page.evaluate(() => navigator.clipboard.readText());
  } catch (err) {
    throw new Error(`clipboard not readable: ${err.message}`);
  }
}

/** Select the whole content of the index-th element matching `sel`, copy it (Ctrl+C), return the clipboard. */
async function copyContents(page, sel, index) {
  await page.evaluate(([s, i]) => {
    const box = document.querySelectorAll(s)[i];
    box.scrollIntoView({ block: 'center' });
    const range = document.createRange();
    range.selectNodeContents(box);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }, [sel, index]);
  await page.keyboard.press('Control+C');
  const text = await clipboard(page);
  await page.evaluate(() => window.getSelection().removeAllRanges());
  return text;
}

/**
 * Token shading of the modes tab against the API: every shown token of every example
 * (own sign: pole colour, alpha |v|/vmax * 0.9; other sign above 5 % of vmax: the other
 * pole's colour, alpha |v|/vmax * 0.6; vmax per mode), and readable text on strong tokens.
 */
function shadingAgainstApi(page, pid) {
  return page.evaluate(async (p) => {
    const d = await (await fetch(`/api/v1/poles/${encodeURIComponent(p)}?include=modes,examples,tokens&max_examples=3`)).json();
    const sign = p.endsWith('__neg') ? -1 : 1;
    const POS = [224, 86, 27];
    const NEG = [51, 105, 138];
    const own = sign > 0 ? POS : NEG;
    const other = sign > 0 ? NEG : POS;
    const parse = (s) => (String(s).match(/[\d.]+/g) || []).map(Number);
    const lin = (c) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    const contrast = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    const out = { tokens: 0, strong: 0, bad: [], lowContrast: [] };
    for (const m of d.modes || []) {
      const examples = m.examples || [];
      let vmax = 0;
      for (const e of examples) for (const [, v] of e.tokens || []) if (v != null) vmax = Math.max(vmax, Math.abs(v));
      const boxes = document.querySelectorAll(`#mode-${m.index} .example .ex-text`);
      examples.forEach((e, k) => {
        const toks = e.tokens || [];
        if (!toks.length || !boxes[k]) return;
        const focal = Number.isInteger(e.focal_index) ? e.focal_index : -1;
        const long = toks.length > 108 && focal >= 0;
        const from = long ? Math.max(0, focal - 56) : 0;
        const to = long ? Math.min(toks.length, focal + 29) : toks.length;
        const spans = boxes[k].querySelectorAll('.tok');
        if (spans.length !== to - from) out.bad.push(`mode ${m.index} example ${k}: ${spans.length} tokens shown, expected ${to - from}`);
        const surface = parse(getComputedStyle(boxes[k].closest('.example')).backgroundColor);
        for (let i = from; i < to && i - from < spans.length; i++) {
          const v = toks[i][1];
          const a = vmax > 0 && v != null ? Math.min(1, Math.abs(v) / vmax) : 0;
          let want = null;
          if (v != null && vmax > 0) {
            if (v * sign > 0) want = [...own, +(a * 0.9).toFixed(3)];
            else if (Math.abs(v) > 0.05 * vmax) want = [...other, +(a * 0.6).toFixed(3)];
          }
          const span = spans[i - from];
          const got = span.style.backgroundColor ? parse(span.style.backgroundColor) : null;
          if (got && got.length === 3) got.push(1);
          out.tokens++;
          // the browser keeps an alpha in steps of 1/255
          const same = want === null ? got === null : !!got && got.slice(0, 3).join() === want.slice(0, 3).join() && Math.abs(got[3] - want[3]) <= 1 / 255 + 0.001;
          if (!same) out.bad.push(`mode ${m.index} example ${k} token ${i} ${JSON.stringify(toks[i][0])}: ${got ? got.join(',') : 'none'} instead of ${want ? want.join(',') : 'none'}`);
          if (want && v * sign > 0 && want[3] > 0.5) {
            out.strong++;
            const bg = [0, 1, 2].map((c) => want[c] * want[3] + surface[c] * (1 - want[3]));
            const c = contrast(bg, parse(getComputedStyle(span).color));
            if (c < 4) out.lowContrast.push(`${JSON.stringify(toks[i][0])} ${c.toFixed(2)}:1`);
          }
        }
      });
    }
    return out;
  }, pid);
}

/** In print: elements that cut their content (overflow other than visible), and <details> left closed. */
function printClipping(page) {
  return page.evaluate(() => {
    window.dispatchEvent(new Event('beforeprint'));
    const clipped = new Set();
    for (const el of document.querySelectorAll('main *')) {
      if (!(el instanceof HTMLElement)) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none') continue;
      if ((cs.overflowY !== 'visible' && el.scrollHeight > el.clientHeight + 2) || (cs.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 2)) clipped.add(`${el.tagName.toLowerCase()}.${el.className}`);
    }
    const closed = document.querySelectorAll('main details:not([open])').length;
    const hiddenRows = [...document.querySelectorAll('main .unit-row')].filter((r) => getComputedStyle(r).display === 'none').length;
    const svg = document.querySelector('main svg.graph');
    const wide = svg ? svg.getBoundingClientRect().width - document.querySelector('main').clientWidth : 0;
    window.dispatchEvent(new Event('afterprint'));
    return { clipped: [...clipped], closed, hiddenRows, wide };
  });
}

/** The width of an A4 page between the 13 mm side margins of the print stylesheet's @page, in CSS px. */
const PAPER_W = Math.floor(((210 - 26) / 25.4) * 96);

/**
 * Lay the page out as printed on A4 (print media at the paper's content width): the document's width, the
 * elements whose right edge passes the page (a wider printout makes the browser shrink all of it; an
 * element clipped by an ancestor does not count), and the controls that would show on paper.
 */
async function printOverflow(page) {
  const size = page.viewportSize();
  await page.setViewportSize({ width: PAPER_W, height: 1000 });
  await page.emulateMedia({ media: 'print' });
  try {
    return await page.evaluate((W) => {
      window.dispatchEvent(new Event('beforeprint'));
      const clipped = (el) => {
        for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) if (getComputedStyle(e).overflowX !== 'visible') return true;
        return false;
      };
      const name = (el) => `${el.tagName.toLowerCase()}.${String(el.className.baseVal ?? el.className).split(' ').slice(0, 2).join('.')}`;
      const past = [];
      for (const el of document.querySelectorAll('main *')) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > W + 1 && !clipped(el)) past.push(`${name(el)} right=${Math.round(r.right)}`);
      }
      // the rows of the map legend are buttons, and content
      const controls = [...document.querySelectorAll('main :is(button, input, select, textarea)')].filter((el) => {
        const r = el.getBoundingClientRect();
        return (r.width > 0 || r.height > 0) && !el.classList.contains('legend-btn');
      }).map((el) => `${name(el)}: ${(el.textContent || el.value || '').trim().slice(0, 30)}`);
      const scrollW = document.documentElement.scrollWidth;
      window.dispatchEvent(new Event('afterprint'));
      return { W, scrollW, past: [...new Set(past)], controls };
    }, PAPER_W);
  } finally {
    await page.emulateMedia({ media: 'screen' });
    if (size) await page.setViewportSize(size);
  }
}

async function main() {
  const health = await fetch(`${BASE}/healthz`).then((r) => r.ok).catch(() => false);
  if (!health) {
    console.error(`No server at ${BASE} (GET /healthz failed). Start one first; see the header of this file.`);
    process.exit(2);
  }
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 1, locale: 'de-DE' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE }).catch(() => {});
  const page = await context.newPage();
  watch(page);
  // Never let the page reach anything but the server under test.
  await context.route((url) => !url.href.startsWith(BASE) && !url.href.startsWith('data:') && !url.href.startsWith('blob:'), (route) => {
    problems.push(`external request: ${route.request().url()}`);
    route.abort();
  });

  console.log(`UI smoke test against ${BASE}`);

  // ------------------------------------------------------------------ search
  console.log('search');
  await page.goto(`${BASE}/`);
  await check('start page lists poles', async () => {
    await page.waitForSelector('.hit-pole', { timeout: 15000 });
    const total = await page.locator('.results-summary .total').innerText();
    assert(/\d/.test(total), `no total shown: "${total}"`);
    assert(await page.locator('html').getAttribute('lang') === 'de', 'default language is not German');
  }, page);

  await check('typing searches (debounced) and writes the URL', async () => {
    await page.keyboard.press('/');
    assert(await page.evaluate(() => document.activeElement && document.activeElement.id === 'q'), '"/" did not focus the search box');
    await page.keyboard.type(QUERY, { delay: 15 });
    await page.waitForFunction((q) => location.hash.includes(`q=${encodeURIComponent(q)}`), QUERY, { timeout: 5000 });
    await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
    assert((await hash(page)).startsWith('#/search?'), 'hash is not a search route');
    assert(/ms/.test(await page.locator('.results-summary').innerText()), 'took_ms not shown');
  }, page);

  await check('the total and the first rows are the API\'s', async () => {
    await settled(page);
    const res = await getJson(page, `/api/v1/search?q=${Q}&limit=10&snippets=false`);
    const got = (await rowPids(page)).slice(0, res.hits.length);
    assert(JSON.stringify(got) === JSON.stringify(res.hits.map((x) => x.pid)), `rows ${got.join(' ')} differ from the API's ${res.hits.map((x) => x.pid).join(' ')}`);
    const total = await page.locator('.results-summary .total').innerText();
    assert(total.replace(/\D/g, '') === String(res.total), `total "${total}" is not the API's ${res.total}`);
    assert(await count(page, '.hit-pole .hit-headline a') === 0, 'headlines are links (they could not be selected by dragging)');
  }, page);

  await check('facet counts next to the filters are the API\'s', async () => {
    await page.waitForFunction(() => [...document.querySelectorAll('.filters .fcount')].some((el) => /\d/.test(el.textContent)), null, { timeout: 5000 });
    assert(await count(page, '.layer-btn') >= 1, 'no layer buttons (from /api/v1/info)');
    const res = await getJson(page, `/api/v1/search?q=${Q}&limit=0&facets=true&snippets=false`);
    const got = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.filters .check-count')].map((l) => [l.querySelector('.cat').className.replace(/^cat cat-/, ''), l.querySelector('.fcount').textContent])));
    for (const [cat, text] of Object.entries(got)) assert(text.replace(/\D/g, '') === String(res.facets.category[cat] || 0), `count for ${cat} is "${text}", the API says ${res.facets.category[cat] || 0}`);
  }, page);

  await check('a filter becomes a removable chip and part of the URL', async () => {
    const before = await page.locator('.results-summary .total').innerText();
    await page.locator('.filters .check-count', { has: page.locator('.cat-poly') }).locator('input').check();
    await page.waitForFunction(() => location.hash.includes('cat=poly'), null, { timeout: 5000 });
    await page.waitForSelector('.chips .chip');
    await settled(page);
    await page.locator('.chips .chip-x').first().click();
    await page.waitForFunction(() => !location.hash.includes('cat=poly'), null, { timeout: 5000 });
    await page.waitForFunction((t) => !document.querySelector('.results.is-loading') && document.querySelector('.results-summary .total').textContent === t, before, { timeout: 5000 });
  }, page);

  await check('pole filter and sort reach the API and the list', async () => {
    await page.locator('.filters [data-name="pole"][data-value="neg"]').click();
    await page.waitForFunction(() => location.hash.includes('pole=neg'), null, { timeout: 5000 });
    await settled(page);
    const neg = await getJson(page, `/api/v1/search?q=${Q}&pole=neg&limit=0`);
    const pids = await rowPids(page);
    assert(pids.length === Math.min(25, neg.total) && pids.every((p) => p.endsWith('__neg')), `pole filter not applied: ${pids.slice(0, 5).join(' ')}`);
    await page.locator('.sort-opt select').first().selectOption('layer');
    await page.waitForFunction(() => location.hash.includes('sort=layer'), null, { timeout: 5000 });
    await settled(page);
    const sorted = await getJson(page, `/api/v1/search?q=${Q}&pole=neg&sort=layer&limit=5&snippets=false`);
    const got = (await rowPids(page)).slice(0, sorted.hits.length);
    assert(JSON.stringify(got) === JSON.stringify(sorted.hits.map((x) => x.pid)), `sorted rows ${got.join(' ')} differ from the API's`);
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
    await settled(page);
  }, page);

  await check('group by mode has its own rows; back restores the previous view', async () => {
    if ((await hash(page)) !== `#/search?q=${Q}`) {
      await page.goto(`${BASE}/#/search?q=${Q}`);
      await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
      await settled(page);
    }
    await page.locator('.search-opts [data-name="group"][data-value="mode"]').click();
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    assert((await hash(page)).includes('group_by=mode'), 'group_by missing from URL');
    assert(await count(page, '.hit-mode .mode-index') >= 1, 'mode rows lack the mode index');
    assert(await count(page, '.hit-mode .hit-modelabel a') === 0, 'mode labels are links (they could not be selected by dragging)');
    await page.locator('.search-opts [data-name="group"][data-value="neuron"]').click();
    await page.waitForSelector('.hit-neuron .subhit', { timeout: 8000 });
    await page.goBack();
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    await page.goBack();
    await page.waitForSelector('.hit-pole', { timeout: 8000 });
    assert(!(await hash(page)).includes('group_by'), 'back did not restore the URL');
  }, page);

  await check('grouped by mode, the export button and the facet counts still count poles', async () => {
    // all modes against all poles: two different numbers on any atlas
    await page.goto(`${BASE}/#/search?group_by=mode`);
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    const poles = (await getJson(page, '/api/v1/search?limit=0')).total;
    await page.waitForFunction((n) => document.querySelector('[data-action="export"]').textContent.replace(/\D/g, '') === String(n), poles, { timeout: 5000 });
    assert(await page.locator('.facet-unit').isVisible(), 'facet counts are not said to count poles');
    // grouping typed into the query (group:mode): the export still streams every pole of the search
    const inline = encodeURIComponent(`${QUERY} group:mode`);
    await page.goto(`${BASE}/#/search?q=${inline}`);
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    await settled(page);
    const want = (await getJson(page, `/api/v1/search?q=${Q}&limit=0`)).total;
    const rows = (await getText(page, `/api/v1/export?q=${inline}&format=ndjson`)).split('\n').filter((l) => l.trim()).length;
    assert(rows === want, `the export of "${QUERY} group:mode" has ${rows} rows, the search ${want} poles`);
    await page.waitForFunction((n) => document.querySelector('[data-action="export"]').textContent.replace(/\D/g, '') === String(n), want, { timeout: 5000 })
      .catch(async () => { throw new Error(`export button says "${await page.locator('[data-action="export"]').innerText()}" for "${QUERY} group:mode", the export streams ${want} poles`); });
    // and with nothing else in the query: all modes against all poles, two different numbers on any atlas
    await page.goto(`${BASE}/#/search?q=${encodeURIComponent('group:mode')}`);
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    await page.waitForFunction((n) => document.querySelector('[data-action="export"]').textContent.replace(/\D/g, '') === String(n), poles, { timeout: 5000 })
      .catch(async () => { throw new Error(`export button says "${await page.locator('[data-action="export"]').innerText()}" for "group:mode", the export streams ${poles} poles`); });
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
    await settled(page);
  }, page);

  await check('search page in print: the search text, every match, no controls, nothing past the page', async () => {
    // a query with a filter chip and hits with more than three matches
    await page.goto(`${BASE}/#/search?q=${Q}&pole=neg`);
    await page.waitForSelector('.hit-pole', { timeout: 8000 });
    await settled(page);
    const printed = await printOverflow(page);
    assert(printed.scrollW <= printed.W && !printed.past.length, `the printout is wider than the page (${printed.W} px): ${printed.scrollW} px, ${printed.past.slice(0, 3).join('; ')}`);
    assert(!printed.controls.length, `controls on paper: ${printed.controls.slice(0, 4).join('; ')}`);
    await page.emulateMedia({ media: 'print' });
    const paper = await page.evaluate(() => ({
      query: document.querySelector('.print-query')?.textContent || '',
      queryShown: getComputedStyle(document.querySelector('.print-query')).display !== 'none',
      chips: document.querySelectorAll('.chips .chip').length,
      hiddenMatches: [...document.querySelectorAll('.hits .match:not(.match-more)')].filter((m) => getComputedStyle(m).display === 'none').length,
      extra: document.querySelectorAll('.hits .match-extra').length,
    }));
    await page.emulateMedia({ media: 'screen' });
    assert(paper.queryShown && paper.query.includes(QUERY), `the search text is not on paper: ${JSON.stringify(paper)}`);
    assert(paper.chips >= 1, 'the filters are not on paper');
    assert(paper.hiddenMatches === 0, `${paper.hiddenMatches} matches missing on paper (behind "+N more")`);
    // on screen the extra matches still wait behind "+N more"
    if (paper.extra) assert(await page.evaluate(() => [...document.querySelectorAll('.hits .match-extra')].every((m) => getComputedStyle(m).display === 'none')), 'matches past the third are shown on screen');
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
    await settled(page);
  }, page);

  await check('syntax help popover with clickable examples', async () => {
    await page.locator('.search-input .iconbtn').click();
    await page.waitForSelector('.popover-help .example-q');
    assert(await count(page, '.popover-help .example-q') >= 8, 'too few syntax examples');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.popover-help', { state: 'detached' });
  }, page);

  await check('filters read from the query text are echoed in words', async () => {
    try {
      await page.goto(`${BASE}/#/search?q=${encodeURIComponent(`${QUERY} layer:30 sim:>0.2`)}`);
      await page.waitForSelector('.results-summary .total', { timeout: 8000 });
      await settled(page);
      const echo = await page.locator('.understood').innerText();
      assert(/Schicht 30/.test(echo) && /Simulierbarkeit ≥ 0,2\b/.test(echo), `echo not in words: ${echo}`);
      assert(!/layers|simulatability_min|0\.2000/.test(echo), `raw field names in the echo: ${echo}`);
    } finally {
      await page.goto(`${BASE}/#/search?q=${Q}`);
      await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
      await settled(page);
    }
  }, page);

  await check('export menu links to /api/v1/export with the current query', async () => {
    await page.locator('[data-action="export"]').click();
    const href = await page.locator('.popover-menu a[data-format="csv"]').getAttribute('href');
    assert(href.startsWith('/api/v1/export?') && href.includes('format=csv') && href.includes(`q=${Q}`), `unexpected export link ${href}`);
    const res = await page.request.get(BASE + href);
    assert(res.ok() && /pid/.test((await res.text()).split('\n')[0]), 'CSV export did not return a header row');
    // one entry per pole of the search (NDJSON: one line per entry)
    const ndjson = (await getText(page, await page.locator('.popover-menu a[data-format="ndjson"]').getAttribute('href'))).split('\n').filter((l) => l.trim());
    const total = (await getJson(page, `/api/v1/search?q=${Q}&limit=0`)).total;
    assert(ndjson.length === total, `the export has ${ndjson.length} entries, the search ${total} poles`);
    await page.keyboard.press('Escape');
  }, page);

  await check('region dialog: the current search in words, a new estimate for each change', async () => {
    await page.locator('[data-action="region"]').click();
    await page.waitForSelector('.region-dialog .plan .stat-value', { timeout: 8000 });
    const first = await page.locator('.region-dialog .plan .stat-value').first().innerText();
    assert(/\d/.test(first), 'no pole count in the estimate');
    assert(!(await page.locator('.region-dialog').innerText()).includes('null'), 'dialog prints "null"');
    const summary = await page.locator('.region-dialog .choice-extra.muted').first().innerText();
    assert(summary.includes(QUERY) && !/\bq:/.test(summary), `search summary not in words: ${summary}`);
    await page.locator('.region-dialog input[name="region-kind"][value="text"]').check();
    // every change of the selection asks for a new estimate, and the dialog shows it
    for (const text of ['random hex hashes', 'braille dots']) {
      const [request] = await Promise.all([
        page.waitForRequest((r) => r.url().includes('/api/v1/mirror/plan') && (r.postData() || '').includes(text), { timeout: 8000 }),
        page.locator('.region-dialog input[type="text"]').first().fill(text),
      ]);
      const plan = await (await request.response()).json();
      await page.waitForFunction((n) => ((document.querySelector('.region-dialog .plan .stat-value') || {}).textContent || '').replace(/\D/g, '') === String(n), plan.estimate.poles, { timeout: 8000 });
    }
    await page.keyboard.press('Escape');
    await page.waitForSelector('.region-dialog', { state: 'detached' });
  }, page);

  await check('a bad query shows the server message (400)', async () => {
    allowHttpErrors = true;
    await page.goto(`${BASE}/#/search?q=%5Bunclosed&match=regex`);
    await page.waitForSelector('.results .notice-error', { timeout: 8000 });
    const text = await page.locator('.results .notice-error').innerText();
    assert(/regular expression|regex/i.test(text), `server detail missing: ${text}`);
    allowHttpErrors = false;
  }, page);

  await check('empty result is explained', async () => {
    await page.goto(`${BASE}/#/search?q=zzzqqqxxnotaword`);
    await page.waitForSelector('.results .notice-empty', { timeout: 8000 });
  }, page);

  await check('keyboard: j/k move, Enter opens the pole', async () => {
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole');
    await page.locator('body').click({ position: { x: 5, y: 400 } });
    await page.keyboard.press('j');
    await page.waitForSelector('.hit.is-active');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => location.hash.startsWith('#/pole/'), null, { timeout: 5000 });
    await page.waitForSelector('.pole-head .kvs', { timeout: 10000 });
    await page.goBack();
    await page.waitForSelector('.hit-pole mark', { timeout: 8000 });
  }, page);

  await check('a deep link restores every control and is sent to the API unchanged', async () => {
    const link = '#/search?q=fish*&match=substring&scope=labels&layer=4,9..14&cat=mono,poly&has=overview&modes_min=1&sim_max=1&sample=public&sort=layer&order=desc&group_by=mode';
    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().includes('/api/v1/search?') && r.url().includes('offset=0') && r.url().includes('group_by=mode'), { timeout: 8000 }),
      page.goto(`${BASE}/${link}`),
    ]);
    const sent = new URL(request.url()).searchParams;
    const expected = { q: 'fish*', match: 'substring', scope: 'labels', layer: '4,9,10,11,12,13,14', cat: 'mono,poly', has: 'overview', modes_min: '1', sim_max: '1', sample: 'public', sort: 'layer', order: 'desc', group_by: 'mode' };
    for (const [k, v] of Object.entries(expected)) assert(sent.get(k) === v, `API parameter ${k} is ${sent.get(k)}, expected ${v}`);
    await settled(page);
    const ui = await page.evaluate(() => ({
      q: document.getElementById('q').value,
      match: document.querySelector('[data-name="match"][aria-checked="true"]').dataset.value,
      group: document.querySelector('[data-name="group"][aria-checked="true"]').dataset.value,
      sample: document.querySelector('[data-name="sample"][aria-checked="true"]').dataset.value,
      layers: [...document.querySelectorAll('.layer-btn[aria-pressed="true"]')].length,
      cats: [...document.querySelectorAll('.filters .check-count input:checked')].length,
      chips: document.querySelectorAll('.chips .chip').length,
      sort: document.querySelector('.sort-opt select').value,
    }));
    assert(ui.q === 'fish*' && ui.match === 'substring' && ui.group === 'mode' && ui.sample === 'public' && ui.sort === 'layer', `controls not restored: ${JSON.stringify(ui)}`);
    assert(ui.cats === 2 && ui.layers >= 2 && ui.chips >= 6, `filters not restored: ${JSON.stringify(ui)}`);
    await page.locator('.filters-head .btn').click();
    await page.waitForFunction(() => location.hash === '#/search', null, { timeout: 5000 });
  }, page);

  await check('load more appends the next page (next_offset): the API\'s rows, none twice', async () => {
    await page.goto(`${BASE}/#/search?sort=id`);
    await page.waitForSelector('.hit-pole');
    const before = await count(page, '.hit-pole');
    if (await count(page, '[data-action="more"]') === 0) return; // fewer hits than one page
    // the button is replaced while it loads: scroll the page instead of the button into view
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForFunction((n) => document.querySelectorAll('.hit-pole').length > n, before, { timeout: 8000 });
    const hrefs = await rowPids(page);
    assert(new Set(hrefs).size === hrefs.length, 'a row appears twice after "load more"');
    const res = await getJson(page, `/api/v1/search?sort=id&limit=${hrefs.length}&snippets=false`);
    assert(JSON.stringify(hrefs) === JSON.stringify(res.hits.map((x) => x.pid)), 'the rows are not the API\'s first rows');
    assert(/\d/.test(await page.locator('.results-foot').innerText()), 'no "N of TOTAL loaded" line');
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole mark');
  }, page);

  await check('random pole opens a pole page', async () => {
    await page.locator('.searchbar > .btn').click();
    await page.waitForFunction(() => location.hash.startsWith('#/pole/'), null, { timeout: 8000 });
    await page.waitForSelector('.pole-head .kvs', { timeout: 10000 });
  }, page);

  // ------------------------------------------------------------------ pole page
  console.log('pole page');
  await page.goto(`${BASE}/#/pole/${POLE}`);
  let mirrored = false;
  await check('pole page: header, modes', async () => {
    await page.waitForSelector('.pole-head .kvs', { timeout: 10000 });
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    assert((await page.locator('.pole-id').innerText()).includes(DID.replace(/_/g, '/')), 'id not in the header');
    assert(await count(page, '#panel-modes .mode') >= 1 || await count(page, '#panel-modes .island') >= 1, 'no modes or islands listed');
    mirrored = await count(page, '#panel-modes .example') > 0;
    if (!mirrored) assert(await count(page, '#panel-modes .notice') >= 1, 'pole without examples shows no notice');
  }, page);

  await check('header figures, mode shares and the number of modes are the API\'s', async () => {
    const res = await page.evaluate(async (pid) => {
      const { fmtInt, fmtNum, fmtPct } = await import('/static/js/dom.js');
      const { t } = await import('/static/js/i18n.js');
      const d = await (await fetch(`/api/v1/poles/${encodeURIComponent(pid)}?include=modes`)).json();
      const p = d.pole;
      const want = {};
      const put = (k, v) => { if (v != null) want[k] = v; };
      put(t('pole.layer'), fmtInt(p.layer));
      put(t('pole.expert'), p.expert == null ? t('pole.shared') : fmtInt(p.expert));
      put(t('pole.neuron'), fmtInt(p.neuron));
      put(t('pole.modes'), p.n_modes == null ? null : fmtInt(p.n_modes));
      put(t('pole.subcircuits'), p.n_subcircuits == null ? null : fmtInt(p.n_subcircuits));
      put(`θ ${t('pole.threshold')}`, d.theta == null ? null : fmtNum(d.theta, 4));
      put(t('pole.simulatability'), p.simulatability == null ? null : fmtPct(p.simulatability, 1));
      put(t('pole.mainDamage'), p.main_damage_share == null ? null : fmtPct(p.main_damage_share, 1));
      put(t('pole.firings'), d.n_firings == null ? null : fmtInt(d.n_firings));
      put(t('pole.closedShare'), d.closed_gate_share == null ? null : fmtPct(d.closed_gate_share, 1));
      const got = Object.fromEntries([...document.querySelectorAll('.pole-head .kvs .kv')].map((kv) => [kv.querySelector('dt').textContent, kv.querySelector('dd').textContent]));
      const modes = d.modes || [];
      const shares = { want: modes.map((m) => (m.share == null ? null : fmtPct(m.share))), got: modes.map((m) => document.querySelector(`#mode-${m.index} .mode-stats .stat-inline strong`)?.textContent ?? null) };
      const open = modes.filter((m) => !m.kind || m.kind === 'open').length;
      const title = document.querySelector('#panel-modes .panel-tools .section-title')?.textContent || '';
      return { want, got, shares, nModes: p.n_modes, open, n: modes.length, title, titleWant: t('modes.title', { n: open }) };
    }, POLE);
    const keys = [...new Set([...Object.keys(res.want), ...Object.keys(res.got)])];
    for (const k of keys) assert(res.want[k] === res.got[k], `header "${k}" shows ${JSON.stringify(res.got[k])}, the API gives ${JSON.stringify(res.want[k])}`);
    assert(JSON.stringify(res.shares.got) === JSON.stringify(res.shares.want), `mode shares ${JSON.stringify(res.shares.got)} differ from the API's ${JSON.stringify(res.shares.want)}`);
    if (res.n) {
      assert(res.title.startsWith(res.titleWant), `modes title "${res.title}" does not count the ${res.open} modes`);
      if (res.nModes != null) assert(res.open === res.nModes, `header says ${res.nModes} modes, the list has ${res.open}`);
    }
  }, page);

  await check('examples are shaded token by token, as the API values say', async () => {
    if (!mirrored) return;
    const info = await page.evaluate(() => {
      const focal = document.querySelector('#panel-modes .tok-focal');
      const ex = document.querySelector('#panel-modes .ex-text');
      return {
        focal: !!focal, focalOutline: focal ? getComputedStyle(focal).outlineStyle : '',
        white: getComputedStyle(ex).whiteSpace, tip: !!document.querySelector('#panel-modes .tok[data-tip]'),
        html: document.querySelector('#panel-modes .ex-text').querySelectorAll('script, img, iframe').length,
        head: document.querySelector('#panel-modes .ex-head').textContent,
      };
    });
    assert(info.focal && info.focalOutline === 'solid', 'focal token has no outline box');
    assert(info.white === 'pre-wrap', `white-space is ${info.white}`);
    assert(info.tip, 'tokens carry no tooltip');
    assert(info.html === 0, 'token text was parsed as HTML');
    assert(/×/.test(info.head), `example header lacks "N× threshold": ${info.head}`);
    const shading = await shadingAgainstApi(page, POLE);
    assert(shading.tokens > 20, `only ${shading.tokens} tokens checked`);
    assert(!shading.bad.length, `${shading.bad.length} tokens shaded wrongly, e.g. ${shading.bad.slice(0, 2).join('; ')}`);
    assert(!shading.lowContrast.length, `hard to read on strong tokens: ${shading.lowContrast.slice(0, 3).join(', ')}`);
    // scroll first: the page hides the tooltip on scroll, and hover() may scroll
    const tok = page.locator('#panel-modes .example .tok[data-tip]').first();
    await tok.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await tok.hover();
    await page.waitForFunction(() => !document.getElementById('tooltip').hidden, null, { timeout: 3000 });
  }, page);

  await check('copying a passage gives exactly its text: every passage of the pole (NBSP, a final newline, no buttons)', async () => {
    if (!mirrored) return;
    // Every passage box of the modes, subcircuits and islands tabs against the API: the end of the preceding
    // context the box shows, then its tokens (the window around the firing token of a long passage).
    const bad = [];
    let copied = 0;
    for (const tab of ['modes', 'subcircuits', 'islands']) {
      await page.locator(`#tab-${tab}`).click();
      await page.waitForSelector(`#panel-${tab}[data-ready="1"]`, { timeout: 10000 });
      const want = await page.evaluate(async ([pid, tab]) => {
        const d = await (await fetch(`/api/v1/poles/${encodeURIComponent(pid)}?include=${tab === 'modes' ? 'modes' : tab},examples,tokens&max_examples=3`)).json();
        // the islands view lists the islands of each mode, then the rest
        const islandOrder = (view) => {
          const out = [];
          const byId = new Map((view.islands || []).map((i) => [i.id, i]));
          const used = new Set();
          for (const m of view.modes || []) for (const id of m.islands || []) if (byId.has(id) && !used.has(id)) { used.add(id); out.push(...(byId.get(id).examples || [])); }
          for (const i of view.islands || []) if (!used.has(i.id)) out.push(...(i.examples || []));
          return out;
        };
        let exs = [];
        // a pole without modes shows its islands on the modes tab
        if (tab === 'modes') exs = (d.modes || []).length ? d.modes.flatMap((m) => m.examples || []) : islandOrder(d.islands || {});
        if (tab === 'subcircuits') exs = (d.subcircuits || []).flatMap((s) => s.examples || []);
        if (tab === 'islands') exs = islandOrder(d.islands || {});
        return exs.map((e) => {
          let pre = e.pre || '';
          if (pre.length > 190) {
            let cut = pre.length - 150;
            const ws = pre.slice(cut, cut + 30).search(/\s/);
            if (ws >= 0) cut += ws + 1;
            pre = pre.slice(cut);
          }
          const toks = e.tokens || [];
          if (!toks.length) return pre + (e.focal_token != null ? String(e.text || '').replace(`«${e.focal_token}»`, e.focal_token) : String(e.text || ''));
          const focal = Number.isInteger(e.focal_index) ? e.focal_index : -1;
          const long = toks.length > 108 && focal >= 0;
          const from = long ? Math.max(0, focal - 56) : 0;
          const to = long ? Math.min(toks.length, focal + 29) : toks.length;
          return pre + toks.slice(from, to).map((x) => x[0]).join('');
        });
      }, [POLE, tab]);
      const boxes = await count(page, `#panel-${tab} .ex-text`);
      if (boxes !== want.length) bad.push(`${tab}: ${boxes} passages shown, the API has ${want.length}`);
      for (let i = 0; i < Math.min(boxes, want.length); i++) {
        const text = await copyContents(page, `#panel-${tab} .ex-text`, i);
        copied++;
        if (text !== want[i]) bad.push(`${tab} #${i}: copied ${JSON.stringify(text.slice(-40))} (${text.length}) instead of ${JSON.stringify(want[i].slice(-40))} (${want[i].length})`);
      }
    }
    await page.locator('#tab-modes').click();
    assert(copied > 0, 'no passage copied');
    assert(!bad.length, `${bad.length} of ${copied} passages copy inexactly, e.g. ${bad.slice(0, 2).join('; ')}`);
  }, page);

  await check('copying keeps no-break spaces, control characters and a final newline (whole box, part of it, a mouse drag)', async () => {
    // a passage of our own, so that every case is there on any atlas
    const tokens = ['A\u00a0\u00a0B', ' x\u0007y', '\u00a0', ' end', '\n'];
    const whole = tokens.join('');
    await page.evaluate(async (toks) => {
      const { renderExample } = await import('/static/js/tokens.js');
      const ex = renderExample({ a_rel: 2, text: '', focal_index: 1, tokens: toks.map((x, i) => [x, i + 1]) }, { pole: 'pos', vmax: 5 });
      ex.id = 'copy-probe';
      document.querySelector('main').prepend(ex);
    }, tokens);
    try {
      const box = '#copy-probe .ex-text';
      assert(await copyContents(page, box, 0) === whole, 'the whole box does not copy exactly');
      // from inside the first token to inside the fourth
      await page.evaluate(() => {
        const toks = document.querySelectorAll('#copy-probe .tok');
        const range = document.createRange();
        range.setStart(toks[0].firstChild, 1);
        range.setEnd(toks[3].firstChild, 3);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
      });
      await page.keyboard.press('Control+C');
      const part = await clipboard(page);
      assert(part === whole.slice(1, whole.indexOf(' end') + 3), `part of the box copies as ${JSON.stringify(part)}`);
      // a mouse drag from the first token down to the line after the final newline
      const pts = await page.evaluate((sel) => {
        const el = document.querySelector(sel);
        el.scrollIntoView({ block: 'center' });
        const a = el.querySelector('.tok').getBoundingClientRect();
        const b = el.getBoundingClientRect();
        return { x0: a.left + 1, y0: a.top + a.height / 2, x1: b.right - 4, y1: b.bottom - 6 };
      }, box);
      await page.mouse.move(pts.x0, pts.y0);
      await page.mouse.down();
      await page.mouse.move(pts.x1, pts.y1, { steps: 8 });
      await page.mouse.up();
      await page.keyboard.press('Control+C');
      const dragged = await clipboard(page);
      assert(dragged === whole, `a drag over the box copies ${JSON.stringify(dragged)} instead of ${JSON.stringify(whole)}`);
    } finally {
      await page.evaluate(() => { getSelection().removeAllRanges(); document.getElementById('copy-probe')?.remove(); });
    }
  }, page);

  await check('untrusted text never becomes markup (tokens, Markdown reports)', async () => {
    const res = await page.evaluate(async () => {
      const { renderExample } = await import('/static/js/tokens.js');
      const { renderMarkdown } = await import('/static/js/markdown.js');
      window.__xss = 0;
      const evil = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</' + 'script>';
      const ex = renderExample({ source: 'subcircuit', ref: 'c0:0', group: 'strongest', a_rel: 3.2, closed_gate: true, held_out: true, repeats: 4, text: '', pre: evil + '\nline two',
        focal_index: 2, tokens: [[evil, 1.5], ['\n\n', 0.2], ['', 3.0], ['\u0000\u0007\u0085', null], ['⠤⣿', -2.0], ['日本語', 0.01], [' tab\there', null]] }, { pole: 'neg', vmax: 3 });
      const md = renderMarkdown(`# Title ${evil}\n\nText with **bold**, *em*, \`code ${evil}\` and [a link](https://example.org) and [bad](javascript:alert(1)).\n\n- one\n  - nested L30_E47_N449 stays_plain\n- two\n\n| a | b |\n|---|---|\n| 1 | ${evil} |\n\n\`\`\`\n${evil}\n\`\`\`\n> quote`);
      document.body.append(ex, md);
      await new Promise((r) => setTimeout(r, 50));
      const out = {
        xss: window.__xss,
        bad: ex.querySelectorAll('img, script').length + md.querySelectorAll('img, script').length,
        toks: ex.querySelectorAll('.tok').length,
        focalEmpty: ex.querySelector('.tok-focal').classList.contains('tok-empty'),
        unrouted: ex.querySelectorAll('.tok-unrouted').length,
        newline: ex.querySelectorAll('.nl').length,
        controls: ex.querySelectorAll('.ctl').length,
        text: ex.querySelector('.ex-text').textContent,
        tags: ex.querySelector('.ex-head').textContent,
        otherPole: ex.querySelectorAll('.tok')[0].style.backgroundColor,
        ownPole: ex.querySelectorAll('.tok')[4].style.backgroundColor,
        md: { h: md.querySelectorAll('.md-h').length, strong: md.querySelectorAll('strong').length, li: md.querySelectorAll('li').length, nested: md.querySelectorAll('li ul').length, td: md.querySelectorAll('td').length, pre: md.querySelectorAll('pre').length, links: [...md.querySelectorAll('a')].map((a) => a.getAttribute('href')), em: md.querySelectorAll('em').length, quote: md.querySelectorAll('blockquote').length },
      };
      ex.remove();
      md.remove();
      return out;
    });
    assert(res.xss === 0 && res.bad === 0, 'markup from data was executed or parsed');
    assert(res.toks === 7 && res.focalEmpty && res.unrouted === 2 && res.newline === 2, `token rendering off: ${JSON.stringify(res)}`);
    assert(res.text.includes('<img src=x') && res.text.includes('日本語') && res.text.includes('⠤'), 'token text not preserved');
    assert(res.controls === 3 && res.text.includes('\u0000\u0007\u0085'), 'control characters are not kept as they are (each shown by its own picture)');
    assert(/Q4/.test(res.tags) && /held-out/.test(res.tags) && /4/.test(res.tags) && /3,2|3\.2/.test(res.tags), `example tags missing: ${res.tags}`);
    assert(/224, 86, 27/.test(res.otherPole) && /51, 105, 138/.test(res.ownPole), `pole colours wrong: own ${res.ownPole}, other ${res.otherPole}`);
    assert(res.md.h === 1 && res.md.strong === 1 && res.md.em === 1 && res.md.li === 3 && res.md.nested === 1 && res.md.td === 2 && res.md.pre === 1 && res.md.quote === 1, `Markdown structure off: ${JSON.stringify(res.md)}`);
    assert(res.md.links.length === 1 && res.md.links[0] === 'https://example.org', `unsafe link rendered: ${res.md.links}`);
  }, page);

  await check('the report renderer stays fast on hostile Markdown (runs of backticks, blanks, marks without a partner)', async () => {
    // before the fix: 3,000 backticks took seconds (cubic), a heading with 3,000 blanks longer still
    const ms = await page.evaluate(async () => {
      const { renderMarkdown } = await import('/static/js/markdown.js');
      const cases = {
        backticks: `x${'`'.repeat(3000)}`,
        underscores: '__a '.repeat(4000),
        links: '[a](b'.repeat(3000),
        heading: `# x${' '.repeat(3000)}y`,
        fence: `\`\`\` ${' '.repeat(3000)}!`,
        tableRule: `a | b\n|--${' '.repeat(3000)}x`,
      };
      const out = {};
      for (const [name, src] of Object.entries(cases)) {
        const t0 = performance.now();
        renderMarkdown(src);
        out[name] = Math.round(performance.now() - t0);
      }
      return out;
    });
    const slow = Object.entries(ms).filter(([, v]) => v > 300);
    assert(!slow.length, `slow: ${slow.map(([k, v]) => `${k} ${v} ms`).join(', ')}`);
  }, page);

  await check('copy as Markdown puts exactly the .md download on the clipboard', async () => {
    await page.locator('.pole-actions .btn', { hasText: /Markdown/ }).click();
    await page.waitForSelector('.pole-actions .btn.is-done', { timeout: 8000 });
    const text = await clipboard(page);
    const href = await page.locator('.pole-actions a[download$=".md"]').getAttribute('href');
    const md = await getText(page, href);
    assert(text === md, `clipboard (${text.length} characters) is not the .md download (${md.length})`);
    assert(md.includes(DID.replace(/_/g, '/')), 'the Markdown does not name the pole');
  }, page);

  await check('scope popover changes what copy / download / print include', async () => {
    await page.locator('.pole-actions button[aria-haspopup]').click();
    await page.waitForSelector('.popover-scope input[type="checkbox"]');
    const box = page.locator('.popover-scope label', { hasText: 'Landscape' }).locator('input');
    const was = await box.isChecked();
    await box.setChecked(!was);
    const href = await page.locator('.pole-actions a[download$=".md"]').getAttribute('href');
    assert(href.includes('landscape') === !was, `include set did not follow the checkbox: ${href}`);
    await box.setChecked(was);
    await page.keyboard.press('Escape');
    await page.waitForSelector('.popover-scope', { state: 'detached' });
  }, page);

  await check('download and print links carry the include set', async () => {
    const hrefs = await page.evaluate(() => [...document.querySelectorAll('.pole-actions a')].map((a) => a.getAttribute('href')));
    assert(hrefs.some((h) => h.includes('format=md') && h.includes('include=')), 'no .md download link');
    assert(hrefs.some((h) => h.includes('format=json')), 'no .json download link');
    const print = hrefs.find((h) => h.startsWith('/p/'));
    assert(print && print.includes('include='), 'no print-page link');
    const res = await page.request.get(BASE + print);
    assert(res.ok() && /html/i.test(res.headers()['content-type'] || ''), 'print page did not load');
  }, page);

  const TAB_DATA = { overview: ['overview', 'overview'], subcircuits: ['subcircuit', 'subcircuits'], landscape: ['landscape', 'landscape'], circuit: ['circuit', 'circuit'], islands: ['islands', 'islands'] };
  for (const [tab, selector] of [['overview', '.ov-headline, .ov-summary, .situations'], ['subcircuits', '.sub'], ['landscape', '.ls-table'], ['circuit', '.circuit'], ['islands', '.island']]) {
    await check(`tab ${tab} loads lazily, is linkable and shows the data the server has`, async () => {
      const [source, include] = TAB_DATA[tab];
      const used = ((await getJson(page, `/api/v1/poles/${POLE}?include=${include}`)).sources || {})[source] === 'used';
      await page.locator(`#tab-${tab}`).click();
      await page.waitForSelector(`#panel-${tab}[data-ready="1"]`, { timeout: 10000 });
      assert((await hash(page)).includes(`tab=${tab}`), 'tab not in the URL');
      assert(await count(page, `#panel-${tab} .notice-error`) === 0, 'panel shows an error');
      const hasContent = await count(page, `#panel-${tab} :is(${selector})`) > 0;
      if (used) assert(hasContent, `the server uses its ${source} data, but the panel shows none`);
      else assert(hasContent || await count(page, `#panel-${tab} .notice`) > 0, 'panel is empty');
    }, page);
  }

  await check('reader groups are marked as measured on this pole, the other pole\'s label explained (circuit tab, overview digest)', async () => {
    const d = await getJson(page, `/api/v1/poles/${POLE}?include=circuit,overview`);
    const readers = (d.circuit || {}).readers || [];
    if (!readers.length) return;
    const inPanel = (tab, groups) => page.evaluate(async ([tab, groups]) => {
      const { t } = await import('/static/js/i18n.js');
      const rows = [...document.querySelectorAll(`#panel-${tab} .cgroup-reader`)];
      const bad = [];
      groups.forEach((g, i) => {
        const row = rows[i];
        if (!row) return;
        const label = g.effect === '+' ? 'raised' : g.effect === '-' ? 'lowered' : null;
        const dir = g.measured_direction || label;
        const mark = row.querySelector('.effect');
        const got = !mark ? 'none' : mark.classList.contains('effect-plus') ? 'raised' : 'lowered';
        if (got !== (dir || 'none')) bad.push(`${g.name}: mark ${got}, measured ${g.measured_direction || '-'}, label ${g.effect}`);
        if (g.measured_direction && g.measured_direction !== label) {
          const meta = row.querySelector('.cgroup-meta')?.textContent || '';
          const measured = t('circuit.measuredHere', { dir: t(`circuit.dir.${g.measured_direction}`) });
          if (!meta.includes(t('circuit.shownAs', { dir: t(`circuit.dir.${label}`) })) || !meta.includes(measured)) bad.push(`${g.name}: meta "${meta}"`);
          if (mark && mark.title !== measured) bad.push(`${g.name}: mark title "${mark.title}"`);
        }
      });
      return { bad, rows: rows.length };
    }, [tab, groups]);
    await page.locator('#tab-circuit').click();
    await page.waitForSelector('#panel-circuit[data-ready="1"]', { timeout: 10000 });
    const circuit = await inPanel('circuit', readers);
    assert(circuit.rows === readers.length, `${circuit.rows} reader groups shown, the API lists ${readers.length}`);
    assert(!circuit.bad.length, `circuit tab: ${circuit.bad.slice(0, 3).join('; ')}`);
    // the digest names the same groups with the explorer's label only; the circuit data measures them
    const digest = ((d.overview || {}).circuit_digest || {}).readers || [];
    if (digest.length) {
      const byName = new Map(readers.map((r) => [r.name, r]));
      const groups = digest.map((g, i) => ({ ...g, measured_direction: ((readers[i] && readers[i].name === g.name ? readers[i] : byName.get(g.name)) || {}).measured_direction }));
      await page.locator('#tab-overview').click();
      await page.waitForSelector('#panel-overview[data-ready="1"]', { timeout: 10000 });
      const ov = await inPanel('overview', groups);
      assert(ov.rows === digest.length, `${ov.rows} reader groups in the digest, the API lists ${digest.length}`);
      assert(!ov.bad.length, `overview digest: ${ov.bad.slice(0, 3).join('; ')}`);
    }
    await page.locator('#tab-modes').click();
  }, page);

  await check('landscape draws the mode-similarity heatmap', async () => {
    await page.locator('#tab-landscape').click();
    if (await count(page, '#panel-landscape .ls-table') === 0) return;
    const painted = await page.evaluate(() => {
      const cell = document.querySelector('#panel-landscape .heatmap .hm-cell');
      return cell ? getComputedStyle(cell).backgroundImage : '';
    });
    assert(/gradient/.test(painted), 'heatmap cells are not painted');
  }, page);

  await check('landscape part in print fits the A4 width (else the whole printout shrinks)', async () => {
    // this pole, and the downloaded pole with the most modes (the widest table and heatmaps)
    const res = await getJson(page, '/api/v1/search?has=mirrored&sort=modes&order=desc&limit=100&snippets=false');
    const widest = (res.hits || []).find((x) => (x.mirrored || []).includes('landscape'));
    let measured = 0;
    try {
      for (const pid of [...new Set([POLE, widest && widest.pid].filter(Boolean))]) {
        await page.goto(`${BASE}/#/pole/${pid}?tab=landscape`);
        await page.waitForSelector('#panel-landscape[data-ready="1"]', { timeout: 10000 });
        if (await count(page, '#panel-landscape .ls-table') === 0) continue;
        const printed = await printOverflow(page);
        measured++;
        assert(printed.scrollW <= printed.W && !printed.past.length, `${pid}: the landscape part is wider than the page (${printed.W} px): ${printed.scrollW} px, ${printed.past.slice(0, 3).join('; ')}`);
      }
    } finally {
      await page.goto(`${BASE}/#/pole/${POLE}`);
      await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    }
    if (widest) assert(measured > 0, `no landscape table shown for ${widest.pid}, whose landscape data is downloaded`);
  }, page);

  await check('similar poles panel; a mode shows similar modes', async () => {
    await page.waitForSelector('.similar .similar-item', { timeout: 10000 });
    await page.locator('#tab-modes').click();
    await page.locator('#panel-modes .mode .mode-stats .btn').first().click();
    await page.waitForFunction(() => /\d/.test(document.querySelector('.similar-head h2').textContent), null, { timeout: 8000 });
    await page.waitForSelector('.similar .similar-item', { timeout: 8000 });
  }, page);

  await check('"examples per group" is in the URL and goes along to other poles', async () => {
    const select = page.locator('#panel-modes .panel-tools select');
    if (await select.count() === 0) return;
    await select.selectOption('6');
    await page.waitForFunction(() => location.hash.includes('ex=6'), null, { timeout: 5000 });
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    const other = await page.locator('[data-action="other-pole"]').getAttribute('href');
    assert(other.includes('ex=6'), `the other pole opens without ex=6: ${other}`);
    // a link without ex= sets it back
    await page.evaluate((pid) => { location.hash = `#/pole/${pid}`; }, POLE);
    await page.waitForFunction(() => document.querySelector('#panel-modes[data-ready="1"] .panel-tools select')?.value === '3', null, { timeout: 10000 });
  }, page);

  await check('print stylesheet: no chrome, shading kept, nothing cut off', async () => {
    await page.emulateMedia({ media: 'print' });
    const info = await page.evaluate(() => {
      const hidden = (sel) => { const el = document.querySelector(sel); return !el || getComputedStyle(el).display === 'none'; };
      const tok = [...document.querySelectorAll('#panel-modes .tok')].find((t) => t.style.backgroundColor);
      return { topbar: hidden('.topbar'), tabs: hidden('.tabs'), actions: hidden('.pole-actions'), similar: hidden('.similar'),
        adjust: tok ? getComputedStyle(tok).printColorAdjust || getComputedStyle(tok).webkitPrintColorAdjust : 'exact',
        bg: tok ? getComputedStyle(tok).backgroundColor : 'rgba(1, 1, 1, 0.5)' };
    });
    const cut = await printClipping(page);
    await page.emulateMedia({ media: 'screen' });
    assert(info.topbar && info.tabs && info.actions && info.similar, `chrome visible in print: ${JSON.stringify(info)}`);
    assert(info.adjust === 'exact', `print-color-adjust is ${info.adjust}`);
    assert(!/rgba\(0, 0, 0, 0\)|transparent/.test(info.bg), 'token shading lost in print');
    assert(!cut.clipped.length && !cut.closed, `cut off in print: ${JSON.stringify(cut)}`);
  }, page);

  await check('printing from the dark theme gives light paper', async () => {
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await page.emulateMedia({ media: 'print' });
    const vars = await page.evaluate(() => Object.fromEntries(['--surface', '--ink', '--accent-soft', '--ok', '--err-ink', '--cat-dark'].map((v) => [v, getComputedStyle(document.documentElement).getPropertyValue(v).trim()])));
    await page.emulateMedia({ media: 'screen' });
    await page.evaluate(() => { delete document.documentElement.dataset.theme; });
    const want = { '--surface': '#fff', '--ink': '#000', '--accent-soft': '#e6eefa', '--ok': '#0a6b0a', '--err-ink': '#8c1d18', '--cat-dark': '#52514e' };
    for (const [k, v] of Object.entries(want)) assert(vars[k] === v, `${k} is ${vars[k]} in print from the dark theme`);
  }, page);

  await check('"print this view" loads the chosen sections, then prints', async () => {
    await page.evaluate(() => {
      window.__printed = null;
      window.print = () => {
        window.__printed = { mode: document.querySelector('.pole-view').dataset.print, panels: [...document.querySelectorAll('.tabpanel.print-on')].map((p) => `${p.dataset.tab}:${p.dataset.ready}`) };
        window.dispatchEvent(new Event('afterprint'));
      };
    });
    await page.locator('.pole-actions [data-action="print-view"]').click();
    await page.waitForFunction(() => window.__printed, null, { timeout: 15000 });
    const printed = await page.evaluate(() => window.__printed);
    assert(printed.mode === 'scope', 'print scope not applied');
    assert(printed.panels.some((x) => x.startsWith('modes:')), `modes panel not marked for print: ${printed.panels}`);
    assert(await page.evaluate(() => document.querySelector('.pole-view').dataset.print) === 'active', 'print mode not reset afterwards');
  }, page);

  await check('a negative pole is shaded in its own colour', async () => {
    const res = await getJson(page, '/api/v1/search?pole=neg&has=mirrored&limit=100&snippets=false');
    const neg = (res.hits || []).find((x) => (x.mirrored || []).includes('subcircuit'));
    if (!neg) return; // no negative pole downloaded on this server
    await page.goto(`${BASE}/#/pole/${neg.pid}`);
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    if (await count(page, '#panel-modes .example') === 0) return;
    const shading = await shadingAgainstApi(page, neg.pid);
    assert(shading.tokens > 20, `only ${shading.tokens} tokens checked on ${neg.pid}`);
    assert(!shading.bad.length, `${neg.pid}: ${shading.bad.length} tokens shaded wrongly, e.g. ${shading.bad.slice(0, 2).join('; ')}`);
    assert(!shading.lowContrast.length, `${neg.pid}: hard to read on strong tokens: ${shading.lowContrast.slice(0, 3).join(', ')}`);
  }, page);

  await check('a pole that is not downloaded says so and offers to fetch it', async () => {
    const res = await page.request.get(`${BASE}/api/v1/search?lacks=mirrored&has=subcircuit&limit=1`);
    const hit = (await res.json()).hits[0];
    if (!hit) return; // everything is mirrored on this server
    await page.goto(`${BASE}/#/pole/${hit.pid}`);
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    await page.waitForSelector('#panel-modes .notice-warn [data-action="fetch-pole"]', { timeout: 5000 });
    assert(await count(page, '#panel-modes .mode') >= 1, 'index-only modes are not listed');
    // The button follows what POST /api/v1/mirror accepts (settings.can_mirror): this server says so,
    // and so does one that fetches nothing while pages are viewed (allow_remote_fetch off) but allows downloads.
    const settings = (await getJson(page, '/api/v1/info')).settings || {};
    const button = page.locator('#panel-modes [data-action="fetch-pole"]');
    await page.waitForFunction(() => document.querySelector('#panel-modes [data-action="fetch-pole"]'), null, { timeout: 5000 });
    const allowed = !(settings.can_mirror === false || settings.offline === true);
    await page.waitForFunction((on) => document.querySelector('#panel-modes [data-action="fetch-pole"]').disabled === !on, allowed, { timeout: 5000 })
      .catch(() => { throw new Error(`fetch button ${allowed ? 'disabled' : 'enabled'} although can_mirror is ${settings.can_mirror} and offline ${settings.offline}`); });
    await page.route('**/api/v1/info', async (route) => {
      const body = await (await route.fetch()).json();
      body.settings = { ...body.settings, offline: false, allow_remote_fetch: false, can_fetch: false, can_mirror: true };
      await route.fulfill({ json: body });
    });
    try {
      await page.reload();
      await page.waitForSelector('#panel-modes .notice-warn [data-action="fetch-pole"]', { timeout: 10000 });
      // the server info (it fills the footer) may arrive after the panel
      await page.waitForFunction(() => document.getElementById('foot-state').textContent.trim() !== '', null, { timeout: 8000 });
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
      assert(!(await button.isDisabled()), 'with downloads allowed (can_mirror) the fetch button is disabled because page views may not fetch');
    } finally {
      await page.unroute('**/api/v1/info');
    }
  }, page);

  // ------------------------------------------------------------------ other views
  console.log('other views');
  await check('neuron view: both poles, the circuit of each, the graph section', async () => {
    await page.goto(`${BASE}/#/neuron/${DID}`);
    await page.waitForSelector('.pole-card', { timeout: 10000 });
    const n = await getJson(page, `/api/v1/neurons/${DID}?include=circuit`);
    const circuits = ['pos', 'neg'].map((k) => (n.circuits || {})[k]).filter((c) => c && ((c.inputs || []).length || (c.readers || []).length || (c.output_tokens || []).length || c.line || c.note || (c.notes || []).length));
    assert(await count(page, '.pole-card') === (n.poles || []).length, `${await count(page, '.pole-card')} pole cards, the API lists ${(n.poles || []).length} poles`);
    assert(await count(page, '.neuron-circuit > h2') === circuits.length, `${await count(page, '.neuron-circuit > h2')} circuit headings, the API has ${circuits.length} circuits`);
    const head = await page.locator('.neuron-view .pole-head').innerText();
    assert(!/verdict|grouping|propose_assign|act_max/i.test(head), `raw field names in the header: ${head}`);
    // the coloured mark of an input group is its role for that pole (drives / opposes), not the net sign of its lanes
    const marks = await page.evaluate(() => [...document.querySelectorAll('.neuron-circuit > .circuit')].map((c) => [...c.querySelectorAll('.cgroup-input')].map((g) => g.querySelector('.effect')?.className || '')));
    const norm = (cls) => (cls.includes('effect-plus') ? 'effect-plus' : cls.includes('effect-minus') ? 'effect-minus' : '');
    circuits.forEach((c, i) => {
      const want = (c.inputs || []).map((g) => (g.role === 'drives' ? 'effect-plus' : g.role === 'opposes' ? 'effect-minus' : ''));
      const got = (marks[i] || []).map(norm);
      assert(JSON.stringify(got) === JSON.stringify(want), `circuit ${i + 1}: input marks ${got.join(' ')} do not follow the roles ${want.join(' ')}`);
    });
    // a reader group's mark is what this pole does to it: measured where the API measured it, never the other pole's label
    const readerMarks = await page.evaluate(() => [...document.querySelectorAll('.neuron-circuit > .circuit')].map((c) => [...c.querySelectorAll('.cgroup-reader')].map((g) => g.querySelector('.effect')?.className || '')));
    circuits.forEach((c, i) => {
      const want = (c.readers || []).map((g) => {
        const dir = g.measured_direction || (g.effect === '+' ? 'raised' : g.effect === '-' ? 'lowered' : '');
        return dir === 'raised' ? 'effect-plus' : dir === 'lowered' ? 'effect-minus' : '';
      });
      const got = (readerMarks[i] || []).map(norm);
      assert(JSON.stringify(got) === JSON.stringify(want), `circuit ${i + 1}: reader marks ${got.join(' ')} differ from the measured directions ${want.join(' ')}`);
    });
    // the output tokens listed are the ones this pole raises: their push on this pole shows as "+" on both poles
    const pushes = await page.evaluate(() => [...document.querySelectorAll('.neuron-circuit > .circuit')].map((c) => [...c.querySelectorAll('.out-tokens .out-push')].map((s) => s.textContent)));
    circuits.forEach((c, i) => {
      const n = (c.output_tokens || []).filter((x) => typeof x.weight === 'number').length;
      assert((pushes[i] || []).length === n, `circuit ${i + 1}: ${(pushes[i] || []).length} output tokens with a number, the API has ${n}`);
      const lowered = (pushes[i] || []).filter((s) => s.startsWith('−'));
      assert(!lowered.length, `circuit ${i + 1}: output tokens shown as lowered by the pole that raises them: ${lowered.slice(0, 3).join(' ')}`);
    });
    await page.waitForSelector('.graph-body :is(svg.graph, .notice)', { timeout: 10000 });
    assert(await count(page, '.graph-body .notice-error') === 0, 'graph shows an error');
  }, page);

  await check('neuron view in print: lists, graph and member rows complete', async () => {
    await page.emulateMedia({ media: 'print' });
    const cut = await printClipping(page);
    await page.emulateMedia({ media: 'screen' });
    assert(!cut.clipped.length && !cut.closed, `cut off in print: ${JSON.stringify(cut)}`);
    assert(cut.hiddenRows === 0, `${cut.hiddenRows} member rows missing in print`);
    assert(cut.wide <= 1, `the graph is ${Math.round(cut.wide)} px wider than the page`);
  }, page);

  await check('map view renders points or an explicit empty state', async () => {
    await page.goto(`${BASE}/#/map?q=${Q}`);
    await page.waitForSelector('.map-canvas');
    await page.waitForFunction(() => !document.querySelector('.map-loading'), null, { timeout: 30000 });
    const state = await page.evaluate(() => ({ empty: !!document.querySelector('.map-overlay .notice-empty'), error: !!document.querySelector('.map-overlay .notice-error'), legend: document.querySelectorAll('.legend-list li').length, status: document.querySelector('.map-status').textContent }));
    assert(!state.error, 'map shows an error');
    assert(state.empty || /\d/.test(state.status), 'neither points nor an empty state');
  }, page);

  await check('batch: many queries, one table each, copy as Markdown', async () => {
    await page.goto(`${BASE}/#/batch`);
    await page.locator('.batch-area').fill(`${QUERY}\nfishing cat:mono\n"severe pain"`);
    await page.locator('[data-action="run-batch"]').click();
    await page.waitForSelector('.batch-result', { timeout: 10000 });
    assert(await count(page, '.batch-result') === 3, 'expected three result blocks');
    assert(await count(page, '.batch-result-head h2 a') === 0, 'query headings are links (they could not be selected by dragging)');
    await page.locator('.batch-tools .btn').click();
    await page.waitForSelector('.batch-tools .btn.is-done', { timeout: 5000 });
    const text = await clipboard(page);
    assert(text.includes(`## ${QUERY}`) && text.includes('|'), 'clipboard does not hold the Markdown table');
  }, page);

  await check('batch: the copied Markdown keeps atlas text as text (one cell each, no live tags)', async () => {
    // labels and snippets as hostile as atlas text can be (a real label reads 'Repeated <td> sibling tags')
    const EVIL = 'x | <td> <img src=x onerror=alert(1)> *b* _i_ `c` [l](u) &amp; ~~s~~ $m$ #h \\';
    await page.route('**/api/v1/search/batch', async (route) => {
      const body = await (await route.fetch()).json();
      for (const r of body.results || []) {
        // (first in the matched text: the table cuts that to 240 characters)
        for (const m of r.modes || []) m.label = `${m.label} ${EVIL}`;
        for (const hit of r.hits || []) {
          for (const m of hit.matches || []) m.text = `${EVIL} ${m.text}`;
          for (const k of ['headline', 'description']) if (hit[k]) hit[k] = `${EVIL} ${hit[k]}`;
        }
        r.notes = [...(r.notes || []), `- ${EVIL}\n1. second line`];
      }
      await route.fulfill({ json: body });
    });
    try {
      for (const group of ['mode', 'pole']) {
        await page.goto(`${BASE}/#/batch`);
        await page.locator('.batch-area').fill(QUERY);
        await page.locator(`[data-name="bgroup"][data-value="${group}"]`).click();
        await page.locator('[data-action="run-batch"]').click();
        await page.waitForSelector('.batch-tools .btn', { timeout: 10000 });
        await page.locator('.batch-tools .btn').click();
        await page.waitForSelector('.batch-tools .btn.is-done', { timeout: 5000 });
        const md = await clipboard(page);
        const rows = md.split('\n').filter((l) => l.startsWith('|'));
        assert(rows.length > 2, `${group}: no table rows copied`);
        const cells = (line) => line.split(/(?<!\\)\|/).length;
        const odd = rows.filter((l) => cells(l) !== cells(rows[0]));
        assert(!odd.length, `${group}: rows with a different number of cells than the header: ${JSON.stringify(odd[0])}`);
        assert(!/<(td|img)/.test(md) && md.includes('&lt;img') && md.includes('&lt;td>'), `${group}: tags are not escaped`);
        for (const piece of ['\\*b\\*', '\\_i\\_', '\\`c\\`', '\\[l\\](u)', '&amp;amp;', '\\~\\~s\\~\\~', '\\$m\\$', '\\#h', 'x \\| &lt;td>']) assert(md.includes(piece), `${group}: ${JSON.stringify(piece)} is not in the copied Markdown`);
        assert(/^> \\- x \\\| /m.test(md) && !/^1\. second/m.test(md), `${group}: a note starts a list or leaves the quote: ${JSON.stringify(md.split('\n').filter((l) => l.startsWith('>')).slice(-2))}`);
      }
    } finally {
      await page.unroute('**/api/v1/search/batch');
      await page.evaluate(() => sessionStorage.removeItem('na.batch')).catch(() => {});
    }
  }, page);

  await check('about: unofficial notice, link to the original, glossary, server state', async () => {
    await page.goto(`${BASE}/#/about`);
    await page.waitForSelector('.glossary dt', { timeout: 10000 });
    assert(await count(page, '.glossary > dt') > 8, 'glossary too short');
    const href = await page.locator('a[data-original]').getAttribute('href');
    assert(/goodfire\.com/.test(href), 'no link to the original atlas');
    await page.waitForSelector('.about-view .kvs .kv', { timeout: 8000 });
  }, page);

  await check('language switch DE/EN and theme toggle persist', async () => {
    await page.locator('[data-lang="en"]').click();
    await page.waitForFunction(() => document.querySelector('[data-nav="search"]').textContent === 'Search');
    assert(await page.evaluate(() => localStorage.getItem('na.lang')) === 'en', 'language not stored');
    await page.locator('[data-lang="de"]').click();
    await page.waitForFunction(() => document.querySelector('[data-nav="search"]').textContent === 'Suche');
    await page.locator('#theme-toggle').click();
    const theme = await page.evaluate(() => document.documentElement.dataset.theme);
    assert(theme === 'dark' || theme === 'light', 'theme not set');
    assert(await page.evaluate(() => localStorage.getItem('na.theme')) === theme, 'theme not stored');
    await page.locator('#theme-toggle').click();
  }, page);

  await check('phone width: no horizontal scrolling, the syntax help fits', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of [`#/search?q=${Q}`, `#/pole/${POLE}`, '#/about']) {
      await page.goto(`${BASE}/${route}`);
      await page.waitForSelector(route.startsWith('#/search') ? '.hit-pole' : route.startsWith('#/pole') ? '#panel-modes[data-ready="1"]' : '.glossary dt', { timeout: 10000 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `${route} overflows by ${overflow}px`);
    }
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('.hit-pole');
    await page.locator('.search-input .iconbtn').click();
    await page.waitForSelector('.popover-help .example-q');
    const help = await page.evaluate(() => { const p = document.querySelector('.popover-help'); return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, panel: p.scrollWidth - p.clientWidth }; });
    assert(help.page <= 1 && help.panel <= 1, `the syntax help does not fit: ${JSON.stringify(help)}`);
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1360, height: 900 });
  }, page);

  await check('two-row header (narrow window): the pinned tab bar stays reachable', async () => {
    await page.setViewportSize({ width: 700, height: 800 });
    await page.goto(`${BASE}/#/pole/${POLE}`);
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    await page.evaluate(() => window.scrollTo(0, 1500));
    await page.waitForFunction(() => {
      const tabs = document.querySelector('.tabs').getBoundingClientRect();
      const hit = document.elementFromPoint(tabs.left + 30, tabs.top + tabs.height / 2);
      return !!hit && !!hit.closest('.tabs');
    }, null, { timeout: 3000 });
    await page.setViewportSize({ width: 1360, height: 900 });
  }, page);

  // ------------------------------------------------------------------ flows the offline server cannot run (mocked responses, final API shapes)
  console.log('mirror job, map and graph with mocked responses');
  await check('region download: start, progress bar, job list, cancel', async () => {
    let progress = 0;
    let cancelled = false;
    const job = () => ({ id: 'job1', label: 'search', state: cancelled ? 'cancelled' : progress >= 1 ? 'done' : 'running', poles: 3, sources: ['subcircuit'], files_to_fetch: 10, files_done: Math.round(progress * 10), progress, report: { requested: 10, fetched: Math.round(progress * 10), skipped: 0, absent: 0, failed: 0, bytes_fetched: 1000, seconds: 1, errors: [], cancelled }, created_at: Date.now() / 1000, finished_at: null, error: '' });
    // a server that fetches nothing while pages are viewed, but accepts downloads (can_mirror): the dialog offers them
    await page.route('**/api/v1/info', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.settings = { ...body.settings, offline: false, allow_remote_fetch: false, can_fetch: false, can_mirror: true };
      await route.fulfill({ json: body });
    });
    await page.route(/\/api\/v1\/mirror(\?.*)?$/, (route) => {
      progress = 0.1;
      route.fulfill({ json: job() });
    });
    await page.route(/\/api\/v1\/mirror\/jobs(\?.*)?$/, (route) => route.fulfill({ json: { jobs: progress > 0 ? [job()] : [] } }));
    await page.route(/\/api\/v1\/mirror\/jobs\/job1/, (route) => {
      if (route.request().method() === 'DELETE') {
        cancelled = true;
        route.fulfill({ json: { cancelled: 'job1' } });
      } else route.fulfill({ json: job() });
    });
    try {
      await page.goto(`${BASE}/#/search?q=${Q}`);
      await page.reload();
      await page.waitForSelector('.hit-pole');
      const planned = page.waitForResponse((r) => r.url().includes('/api/v1/mirror/plan'), { timeout: 8000 });
      await page.locator('[data-action="region"]').click();
      const plan = await (await planned).json();
      await page.waitForSelector('.region-dialog .plan .stat-value');
      const start = page.locator('.region-dialog .dialog-actions .btn-primary');
      const offlineTitle = await page.evaluate(async () => (await import('/static/js/i18n.js')).t('region.offlineTitle'));
      assert(!(await page.locator('.region-dialog').innerText()).includes(offlineTitle), 'the dialog says this server does not download, although it accepts downloads (can_mirror)');
      if ((plan.estimate || {}).files_to_fetch > 0) assert(!(await start.isDisabled()), 'the dialog does not offer the download, although the server accepts it (can_mirror)');
      if (await start.isDisabled()) {
        // nothing left to fetch for this query on this server: the button is rightly disabled
        await page.keyboard.press('Escape');
      } else {
        await start.click();
        await page.waitForSelector('.region-dialog .job .progress', { timeout: 8000 });
        assert(/10/.test(await page.locator('.region-dialog .job .progress').first().getAttribute('aria-valuenow')), 'progress bar does not show 10 %');
        assert(!(await page.locator('#region-badge').isHidden()), 'no running-job badge in the header');
        await page.locator('.region-dialog .job .btn-danger').click();
        await page.waitForSelector('.region-dialog .job.job-cancelled', { timeout: 8000 });
        await page.keyboard.press('Escape');
      }
    } finally {
      if (await count(page, 'dialog[open]')) await page.keyboard.press('Escape');
      await page.unroute('**/api/v1/info');
      await page.unroute(/\/api\/v1\/mirror(\?.*)?$/);
      await page.unroute(/\/api\/v1\/mirror\/jobs(\?.*)?$/);
      await page.unroute(/\/api\/v1\/mirror\/jobs\/job1/);
    }
  }, page);

  await check('map: points, cluster legend, highlight box, click opens the pole; the URL sets the options', async () => {
    const points = [];
    for (let i = 0; i < 400; i++) points.push({ pid: `L4_E1_N${i}__pos`, x: (i % 20) - 9.5, y: Math.floor(i / 20) - 9.5, layer: 4 + (i % 4) * 5, category: i % 3 ? 'mono' : 'poly', pole: 'pos', label: i === 0 ? 'needle label' : `hay ${i}`, cluster: i % 2 });
    const seen = [];
    await page.route('**/api/v1/map*', (route) => {
      seen.push(new URL(route.request().url()).searchParams);
      route.fulfill({ json: { unit: 'pole', method: 'pca', points: [{ pid: POLE, x: 0, y: 0, layer: 30, category: 'fn', pole: 'pos', label: 'centre point', cluster: 0 }, ...points], clusters: [{ id: 0, size: 201, label: 'even', x: -5, y: 0 }, { id: 1, size: 200, label: 'odd', x: 5, y: 0 }], notes: ['mocked'] } });
    });
    await page.goto(`${BASE}/#/map?unit=mode&clusters=0`);
    await page.waitForSelector('.legend-list li', { timeout: 10000 });
    await page.evaluate(() => { location.hash = '#/map'; });
    await page.waitForFunction(() => document.querySelector('[data-name="unit"][aria-checked="true"]').dataset.value === 'pole', null, { timeout: 5000 });
    await page.waitForFunction((n) => !document.querySelector('.map-loading'), null, { timeout: 10000 });
    const last = seen[seen.length - 1];
    assert(last.get('unit') === 'pole' && last.get('clusters') === '12', `the map did not follow the URL: unit=${last.get('unit')} clusters=${last.get('clusters')}`);
    assert(await count(page, '.legend-list li') === 2, 'cluster legend incomplete');
    assert(/401/.test(await page.locator('.map-status').innerText()), 'point count not shown');
    assert((await page.locator('.map-notes').innerText()).includes('mocked'), 'notes not shown');
    await page.locator('.map-controls input[type="search"]').nth(1).fill('needle');
    await page.waitForFunction(() => /\b1\b/.test(document.querySelector('.map-status').textContent.replace(/401/, '')), null, { timeout: 5000 });
    await page.locator('.map-controls input[type="search"]').nth(1).fill('');
    await page.locator('.map-controls [data-name="color"][data-value="layer"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.legend-list li').length === 5, null, { timeout: 5000 });
    const box = await page.locator('.map-canvas').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForFunction(() => !document.getElementById('tooltip').hidden, null, { timeout: 5000 });
    assert((await page.locator('#tooltip').innerText()).includes('centre point'), 'hover tooltip does not show the label');
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForFunction((pid) => location.hash.startsWith(`#/pole/${pid}`), POLE, { timeout: 5000 });
    await page.unroute('**/api/v1/map*');
  }, page);

  await check('neuron graph: layered picture whose nodes link to their neuron pages', async () => {
    // a neighbour that really has a page in this release, so that following the link works
    const listed = await getJson(page, '/api/v1/search?limit=3&sort=id');
    const other = listed.hits.map((hit) => hit.did).find((d) => d !== DID);
    await page.route('**/api/v1/graph/*', (route) => route.fulfill({ json: { center: DID, nodes: [
      { id: DID, layer: 30, label: 'centre', in_atlas: true, mirrored: true, distance: 0 },
      { id: other, layer: 18, label: 'an input', in_atlas: true, mirrored: false, distance: 1 },
      { id: 'L22_E11_N3', layer: 22, label: 'not in the atlas', in_atlas: false, mirrored: false, distance: 1 },
      { id: 'L36_E66_N43', layer: 36, label: 'a reader', in_atlas: true, mirrored: false, distance: 1 },
    ], edges: [
      { source: other, target: DID, weight: 0.012, sign: '+', kind: 'gate' },
      { source: other, target: DID, weight: 0.006, sign: '+', kind: 'up' },
      { source: 'L22_E11_N3', target: DID, weight: 0.004, sign: '-', kind: 'up' },
      { source: DID, target: 'L36_E66_N43', weight: 0.001, sign: '+', kind: 'reader' },
    ], notes: ['mocked'] } }));
    await page.goto(`${BASE}/#/neuron/${DID}`);
    await page.waitForSelector('svg.graph .gnode', { timeout: 10000 });
    assert(await count(page, 'svg.graph .gnode') === 4, 'expected four nodes');
    assert(await count(page, 'svg.graph path.edge') === 3, 'expected three lines (parallel gate/up links are drawn as one)');
    assert(await count(page, 'svg.graph a.gnode') === 2, 'in-atlas neighbours are not links');
    const xs = await page.evaluate(() => [...document.querySelectorAll('svg.graph .gnode rect')].map((r) => Number(r.getAttribute('x'))));
    assert(new Set(xs).size === 4, 'layers are not laid out in columns');
    await page.locator('svg.graph a.gnode').first().click();
    await page.waitForFunction((id) => location.hash === `#/neuron/${id}`, other, { timeout: 5000 });
    await page.unroute('**/api/v1/graph/*');
    await page.waitForSelector('.pole-card', { timeout: 10000 });
  }, page);

  // ------------------------------------------------------------------ API key and rate limit (mocked responses)
  console.log('API key / rate limit');
  await check('401: one prompt for parallel requests; a key entered later loads the server info', async () => {
    allowHttpErrors = true;
    const seen = [];
    const guard = (route) => {
      const key = route.request().headers()['x-api-key'] || '';
      seen.push(key);
      if (key === 'test-key') route.continue();
      else route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'unauthorized', detail: 'missing or wrong API key' }) });
    };
    await page.route('**/api/v1/glossary', guard);
    await page.route('**/api/v1/info', guard);
    try {
      await page.goto(`${BASE}/#/about`);
      await page.reload();
      await page.waitForSelector('dialog[open] input[type="password"]', { timeout: 8000 });
      assert(await count(page, 'dialog[open]') === 1, 'more than one key prompt');
      await page.keyboard.press('Escape'); // no key for now: no server info
      await page.waitForSelector('.about-view .notice-warn', { timeout: 8000 });
      assert((await page.locator('#foot-state').innerText()).trim() === '', 'server info shown without a key');
      await page.getByRole('button', { name: 'Schlüssel eintragen' }).click();
      await page.waitForSelector('dialog[open] input[type="password"]', { timeout: 8000 });
      await page.locator('dialog[open] input[type="password"]').fill('test-key');
      await page.keyboard.press('Enter');
      await page.waitForSelector('.about-view .kvs .kv', { timeout: 8000 });
      await page.waitForSelector('.glossary dt', { timeout: 8000 });
      await page.waitForFunction(() => document.getElementById('foot-state').textContent.trim() !== '', null, { timeout: 8000 });
      assert(seen.includes('test-key'), 'key was not sent');
      assert(await page.evaluate(() => localStorage.getItem('na.apiKey')) === 'test-key', 'key not stored');
    } finally {
      await page.unroute('**/api/v1/glossary');
      await page.unroute('**/api/v1/info');
    }
  }, page);

  await check('the key never goes into links people copy; downloads send it as a header', async () => {
    // (the key entered in the check before is still set)
    await page.evaluate(() => localStorage.setItem('na.apiKey', 'test-key'));
    await page.goto(`${BASE}/#/pole/${POLE}`);
    await page.reload();
    await page.waitForSelector('.pole-actions a[download$=".md"]');
    const hrefs = await page.evaluate(() => [...document.querySelectorAll('.pole-actions a[download]')].map((a) => a.getAttribute('href')));
    assert(hrefs.length >= 2 && hrefs.every((h) => !h.includes('api_key')), `download links carry the key: ${hrefs.join(' ')}`);
    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().includes('/api/v1/poles/') && r.url().includes('format=md'), { timeout: 8000 }),
      page.waitForEvent('download', { timeout: 8000 }),
      page.locator('.pole-actions a[download$=".md"]').click(),
    ]);
    assert(request.headers()['x-api-key'] === 'test-key', 'the download did not send X-API-Key');
    await page.goto(`${BASE}/#/search?q=${Q}`);
    await page.waitForSelector('[data-action="api"]');
    assert(!(await page.locator('[data-action="api"]').getAttribute('href')).includes('api_key'), 'the API link carries the key');
    await page.locator('[data-action="export"]').click();
    const exports = await page.evaluate(() => [...document.querySelectorAll('.popover-menu a[data-format]')].map((a) => a.getAttribute('href')));
    assert(exports.length && exports.every((h) => !h.includes('api_key')), 'export links carry the key');
    await page.keyboard.press('Escape');
  }, page);
  await page.evaluate(() => localStorage.removeItem('na.apiKey')).catch(() => {});
  allowHttpErrors = false;

  await check('429 shows "slow down" with the Retry-After value', async () => {
    allowHttpErrors = true;
    await page.goto(`${BASE}/#/search`);
    await page.reload();
    await page.waitForSelector('.hit-pole');
    await page.route('**/api/v1/random*', (route) => route.fulfill({ status: 429, headers: { 'Retry-After': '7', 'content-type': 'application/json' }, body: JSON.stringify({ error: 'rate_limited', detail: 'too many requests; slow down' }) }));
    await page.locator('.searchbar > .btn').click();
    await page.waitForFunction(() => /7/.test(document.getElementById('toasts').textContent), null, { timeout: 5000 });
    await page.unroute('**/api/v1/random*');
    allowHttpErrors = false;
  }, page);

  await browser.close();

  for (const p of [...new Set(problems)]) failures.push(p);
  console.log(`\n${passed} checks passed, ${failures.length} problem(s).`);
  if (failures.length) {
    for (const f of failures) console.log(`  - ${f}`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
