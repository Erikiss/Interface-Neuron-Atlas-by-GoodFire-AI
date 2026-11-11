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
// instead of content).  Exit code 0 = all checks passed; console errors and page
// errors fail the run.  Set ATLAS_E2E_SHOTS=<dir> to keep screenshots of failures.

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
  await context.route((url) => !url.href.startsWith(BASE) && !url.href.startsWith('data:'), (route) => {
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

  await check('facet counts are shown next to the filters', async () => {
    await page.waitForFunction(() => [...document.querySelectorAll('.filters .fcount')].some((el) => /\d/.test(el.textContent)), null, { timeout: 5000 });
    assert(await count(page, '.layer-btn') >= 1, 'no layer buttons (from /api/v1/info)');
  }, page);

  await check('a filter becomes a removable chip and part of the URL', async () => {
    const before = await page.locator('.results-summary .total').innerText();
    await page.locator('.filters .check-count', { has: page.locator('.cat-poly') }).locator('input').check();
    await page.waitForFunction(() => location.hash.includes('cat=poly'), null, { timeout: 5000 });
    await page.waitForSelector('.chips .chip');
    await page.waitForFunction(() => !document.querySelector('.results.is-loading'));
    await page.locator('.chips .chip-x').first().click();
    await page.waitForFunction(() => !location.hash.includes('cat=poly'), null, { timeout: 5000 });
    await page.waitForFunction((t) => !document.querySelector('.results.is-loading') && document.querySelector('.results-summary .total').textContent === t, before, { timeout: 5000 });
  }, page);

  await check('group by mode has its own rows; back button restores the previous view', async () => {
    await page.locator('.search-opts [data-name="group"][data-value="mode"]').click();
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    assert((await hash(page)).includes('group_by=mode'), 'group_by missing from URL');
    assert(await count(page, '.hit-mode .mode-index') >= 1, 'mode rows lack the mode index');
    await page.locator('.search-opts [data-name="group"][data-value="neuron"]').click();
    await page.waitForSelector('.hit-neuron .subhit', { timeout: 8000 });
    await page.goBack();
    await page.waitForSelector('.hit-mode', { timeout: 8000 });
    await page.goBack();
    await page.waitForSelector('.hit-pole', { timeout: 8000 });
    assert(!(await hash(page)).includes('group_by'), 'back did not restore the URL');
  }, page);

  await check('syntax help popover with clickable examples', async () => {
    await page.locator('.search-input .iconbtn').click();
    await page.waitForSelector('.popover-help .example-q');
    assert(await count(page, '.popover-help .example-q') >= 8, 'too few syntax examples');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.popover-help', { state: 'detached' });
  }, page);

  await check('export menu links to /api/v1/export with the current query', async () => {
    await page.locator('[data-action="export"]').click();
    const href = await page.locator('.popover-menu a[data-format="csv"]').getAttribute('href');
    assert(href.startsWith('/api/v1/export?') && href.includes('format=csv') && href.includes(`q=${encodeURIComponent(QUERY)}`), `unexpected export link ${href}`);
    const res = await page.request.get(BASE + href);
    assert(res.ok() && /pid/.test((await res.text()).split('\n')[0]), 'CSV export did not return a header row');
    await page.keyboard.press('Escape');
  }, page);

  await check('region dialog estimates the download for the current search', async () => {
    await page.locator('[data-action="region"]').click();
    await page.waitForSelector('.region-dialog .plan .stat-value', { timeout: 8000 });
    assert(/\d/.test(await page.locator('.region-dialog .plan .stat-value').first().innerText()), 'no pole count in the estimate');
    assert(!(await page.locator('.region-dialog').innerText()).includes('null'), 'dialog prints "null"');
    await page.locator('.region-dialog input[name="region-kind"][value="text"]').check();
    await page.locator('.region-dialog input[type="text"]').first().fill('random hex hashes');
    await page.waitForFunction(() => /\d/.test((document.querySelector('.region-dialog .plan .stat-value') || {}).textContent || ''), null, { timeout: 8000 });
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
    await page.goto(`${BASE}/#/search?q=${encodeURIComponent(QUERY)}`);
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
    assert((await page.locator('.pole-id').innerText()).includes(POLE.replace(/__(pos|neg)$/, '').replace(/_/g, '/')), 'id not in the header');
    assert(await count(page, '#panel-modes .mode') >= 1 || await count(page, '#panel-modes .island') >= 1, 'no modes or islands listed');
    mirrored = await count(page, '#panel-modes .example') > 0;
    if (!mirrored) assert(await count(page, '#panel-modes .notice') >= 1, 'pole without examples shows no notice');
  }, page);

  await check('examples are shaded token by token', async () => {
    if (!mirrored) return;
    const info = await page.evaluate(() => {
      const toks = [...document.querySelectorAll('#panel-modes .ex-text .tok')];
      const shaded = toks.filter((t) => t.style.backgroundColor);
      const focal = document.querySelector('#panel-modes .tok-focal');
      const ex = document.querySelector('#panel-modes .ex-text');
      return {
        n: toks.length, shaded: shaded.length, focal: !!focal, focalOutline: focal ? getComputedStyle(focal).outlineStyle : '',
        white: getComputedStyle(ex).whiteSpace, tip: !!document.querySelector('#panel-modes .tok[data-tip]'),
        html: document.querySelector('#panel-modes .ex-text').querySelectorAll('script, img, iframe').length,
        head: document.querySelector('#panel-modes .ex-head').textContent,
      };
    });
    assert(info.n > 20, `only ${info.n} tokens`);
    assert(info.shaded > 0, 'no shaded token');
    assert(info.focal && info.focalOutline === 'solid', 'focal token has no outline box');
    assert(info.white === 'pre-wrap', `white-space is ${info.white}`);
    assert(info.tip, 'tokens carry no tooltip');
    assert(info.html === 0, 'token text was parsed as HTML');
    assert(/×/.test(info.head), `example header lacks "N× threshold": ${info.head}`);
    // scroll first: the page hides the tooltip on scroll, and hover() may scroll
    const tok = page.locator('#panel-modes .example .tok[data-tip]').first();
    await tok.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await tok.hover();
    await page.waitForFunction(() => !document.getElementById('tooltip').hidden, null, { timeout: 3000 });
  }, page);

  await check('copy as Markdown puts the pole on the clipboard and confirms', async () => {
    await page.locator('.pole-actions .btn', { hasText: /Markdown/ }).click();
    await page.waitForSelector('.pole-actions .btn.is-done', { timeout: 8000 });
    const text = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
    if (text !== null) assert(text.includes(POLE.replace(/__(pos|neg)$/, '').replace(/_/g, '/')) || text.includes(POLE), 'clipboard does not hold the pole');
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

  for (const [tab, selector] of [['overview', '.ov-headline, .ov-summary, .situations'], ['subcircuits', '.sub'], ['landscape', '.ls-table'], ['circuit', '.circuit, .notice'], ['islands', '.island']]) {
    await check(`tab ${tab} loads lazily and is linkable`, async () => {
      await page.locator(`#tab-${tab}`).click();
      await page.waitForSelector(`#panel-${tab}[data-ready="1"]`, { timeout: 10000 });
      assert((await hash(page)).includes(`tab=${tab}`), 'tab not in the URL');
      assert(await count(page, `#panel-${tab} .notice-error`) === 0, 'panel shows an error');
      const hasContent = await count(page, `#panel-${tab} :is(${selector})`) > 0;
      const hasNotice = await count(page, `#panel-${tab} .notice`) > 0;
      assert(hasContent || hasNotice, 'panel is empty');
    }, page);
  }

  await check('landscape draws the mode-similarity heatmap', async () => {
    await page.locator('#tab-landscape').click();
    if (await count(page, '#panel-landscape .ls-table') === 0) return;
    const painted = await page.evaluate(() => {
      const cell = document.querySelector('#panel-landscape .heatmap .hm-cell');
      return cell ? getComputedStyle(cell).backgroundImage : '';
    });
    assert(/gradient/.test(painted), 'heatmap cells are not painted');
  }, page);

  await check('similar poles panel; a mode shows similar modes', async () => {
    await page.waitForSelector('.similar .similar-item', { timeout: 10000 });
    await page.locator('#tab-modes').click();
    await page.locator('#panel-modes .mode .mode-stats .btn').first().click();
    await page.waitForFunction(() => /\d/.test(document.querySelector('.similar-head h2').textContent), null, { timeout: 8000 });
    await page.waitForSelector('.similar .similar-item', { timeout: 8000 });
  }, page);

  await check('print stylesheet: no chrome, shading kept', async () => {
    await page.emulateMedia({ media: 'print' });
    const info = await page.evaluate(() => {
      const hidden = (sel) => { const el = document.querySelector(sel); return !el || getComputedStyle(el).display === 'none'; };
      const tok = [...document.querySelectorAll('#panel-modes .tok')].find((t) => t.style.backgroundColor);
      return { topbar: hidden('.topbar'), tabs: hidden('.tabs'), actions: hidden('.pole-actions'), similar: hidden('.similar'),
        adjust: tok ? getComputedStyle(tok).printColorAdjust || getComputedStyle(tok).webkitPrintColorAdjust : 'exact',
        bg: tok ? getComputedStyle(tok).backgroundColor : 'rgba(1, 1, 1, 0.5)' };
    });
    await page.emulateMedia({ media: 'screen' });
    assert(info.topbar && info.tabs && info.actions && info.similar, `chrome visible in print: ${JSON.stringify(info)}`);
    assert(info.adjust === 'exact', `print-color-adjust is ${info.adjust}`);
    assert(!/rgba\(0, 0, 0, 0\)|transparent/.test(info.bg), 'token shading lost in print');
  }, page);

  await check('a pole that is not downloaded says so and offers to fetch it', async () => {
    const res = await page.request.get(`${BASE}/api/v1/search?lacks=mirrored&has=subcircuit&limit=1`);
    const hit = (await res.json()).hits[0];
    if (!hit) return; // everything is mirrored on this server
    await page.goto(`${BASE}/#/pole/${hit.pid}`);
    await page.waitForSelector('#panel-modes[data-ready="1"]', { timeout: 10000 });
    await page.waitForSelector('#panel-modes .notice-warn [data-action="fetch-pole"]', { timeout: 5000 });
    assert(await count(page, '#panel-modes .mode') >= 1, 'index-only modes are not listed');
  }, page);

  // ------------------------------------------------------------------ other views
  console.log('other views');
  await check('neuron view shows both poles and the graph section', async () => {
    await page.goto(`${BASE}/#/neuron/${POLE.replace(/__(pos|neg)$/, '')}`);
    await page.waitForSelector('.pole-card', { timeout: 10000 });
    assert(await count(page, '.pole-card') >= 1, 'no pole cards');
    await page.waitForSelector('.graph-body :is(svg.graph, .notice)', { timeout: 10000 });
    assert(await count(page, '.graph-body .notice-error') === 0, 'graph shows an error');
  }, page);

  await check('map view renders points or an explicit empty state', async () => {
    await page.goto(`${BASE}/#/map?q=${encodeURIComponent(QUERY)}`);
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
    await page.locator('.batch-tools .btn').click();
    await page.waitForSelector('.batch-tools .btn.is-done', { timeout: 5000 });
    const text = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
    if (text !== null) assert(text.includes(`## ${QUERY}`) && text.includes('|'), 'clipboard does not hold the Markdown table');
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

  await check('phone width: no horizontal scrolling', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of [`#/search?q=${encodeURIComponent(QUERY)}`, `#/pole/${POLE}`, '#/about']) {
      await page.goto(`${BASE}/${route}`);
      await page.waitForSelector(route.startsWith('#/search') ? '.hit-pole' : route.startsWith('#/pole') ? '#panel-modes[data-ready="1"]' : '.glossary dt', { timeout: 10000 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `${route} overflows by ${overflow}px`);
    }
    await page.setViewportSize({ width: 1360, height: 900 });
  }, page);

  // ------------------------------------------------------------------ API key and rate limit (mocked responses)
  console.log('API key / rate limit');
  await check('401 asks for the key once and sends it as X-API-Key', async () => {
    allowHttpErrors = true;
    const seen = [];
    await page.route('**/api/v1/glossary', (route) => {
      const key = route.request().headers()['x-api-key'] || '';
      seen.push(key);
      if (key === 'test-key') route.continue();
      else route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'unauthorized', detail: 'missing or wrong API key' }) });
    });
    await page.goto(`${BASE}/#/search`);
    await page.goto(`${BASE}/#/about`);
    await page.waitForSelector('dialog[open] input[type="password"]', { timeout: 8000 });
    await page.locator('dialog[open] input[type="password"]').fill('test-key');
    await page.keyboard.press('Enter');
    await page.waitForSelector('.glossary dt', { timeout: 8000 });
    assert(seen.includes('test-key'), 'key was not sent');
    assert(await page.evaluate(() => localStorage.getItem('na.apiKey')) === 'test-key', 'key not stored');
    await page.unroute('**/api/v1/glossary');
    await page.goto(`${BASE}/#/pole/${POLE}`);
    await page.waitForSelector('.pole-actions a');
    const href = await page.locator('.pole-actions a').first().getAttribute('href');
    assert(href.includes('api_key=test-key'), 'download links lack ?api_key=');
    await page.evaluate(() => localStorage.removeItem('na.apiKey'));
    allowHttpErrors = false;
  }, page);

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
