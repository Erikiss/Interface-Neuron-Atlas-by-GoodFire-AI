// The search state and its three spellings:
//   - the page URL (hash query string, same parameter names as GET /api/v1/search),
//   - the GET parameters of the API,
//   - a SearchQuery JSON body (POST endpoints, Selection.query).

export const CATEGORIES = ['mono', 'fn', 'poly', 'dark', 'none'];
export const FEATURES = ['subcircuit', 'overview', 'landscape', 'islands', 'circuit', 'simulatability', 'headline', 'mirrored'];
export const MATCH_MODES = ['auto', 'words', 'substring', 'regex', 'semantic'];
export const SCOPES = ['labels', 'deep', 'examples'];
export const SORTS = ['relevance', 'simulatability', 'layer', 'modes', 'damage', 'share', 'random', 'id'];
export const GROUPS = ['pole', 'neuron', 'mode'];
export const SAMPLES = ['all', 'public', 'hidden'];

const DEFAULT_SCOPE = ['labels', 'deep'];

export function defaultState() {
  return {
    q: '',
    match: 'auto',
    scope: [...DEFAULT_SCOPE],
    layer: [],
    pole: '',
    cat: [],
    has: [],
    modes_min: null,
    modes_max: null,
    sim_min: null,
    sim_max: null,
    sample: 'all',
    sort: 'relevance',
    order: '',
    group_by: 'pole',
    // No dedicated control; understood from the URL and shown as removable chips.
    lacks: [],
    verdict: '',
    expert: [],
    neuron: [],
    damage_min: null,
    damage_max: null,
    layer_min: null,
    layer_max: null,
    ids: [],
  };
}

const LIST_KEYS = ['scope', 'cat', 'has', 'lacks', 'ids'];
const INT_LIST_KEYS = ['layer', 'expert', 'neuron'];
const NUM_KEYS = ['modes_min', 'modes_max', 'sim_min', 'sim_max', 'damage_min', 'damage_max', 'layer_min', 'layer_max'];
const ENUMS = { match: MATCH_MODES, sample: SAMPLES, sort: SORTS, group_by: GROUPS, pole: ['', 'pos', 'neg'], order: ['', 'asc', 'desc'], verdict: ['', 'monosemantic', 'polysemantic'] };

function csv(value) {
  return String(value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function intList(value) {
  const out = [];
  for (const item of csv(value)) {
    const range = /^(-?\d+)\.\.(-?\d+)$/.exec(item);
    if (range) {
      const lo = Number(range[1]);
      const hi = Math.min(Number(range[2]), lo + 500);
      for (let i = lo; i <= hi; i++) out.push(i);
    } else if (/^-?\d+$/.test(item)) out.push(Number(item));
  }
  return [...new Set(out)].sort((a, b) => a - b);
}

/** URLSearchParams -> state (unknown or malformed values fall back to the defaults). */
export function stateFromParams(sp) {
  const s = defaultState();
  if (!sp) return s;
  if (sp.has('q')) s.q = sp.get('q') || '';
  for (const k of LIST_KEYS) if (sp.has(k)) s[k] = csv(sp.get(k));
  for (const k of INT_LIST_KEYS) if (sp.has(k)) s[k] = intList(sp.get(k));
  for (const k of NUM_KEYS) {
    if (!sp.has(k)) continue;
    const n = Number(sp.get(k));
    s[k] = sp.get(k) !== '' && Number.isFinite(n) ? n : null;
  }
  for (const [k, allowed] of Object.entries(ENUMS)) {
    if (!sp.has(k)) continue;
    const v = sp.get(k) || '';
    if (allowed.includes(v)) s[k] = v;
  }
  s.scope = s.scope.filter((x) => SCOPES.includes(x));
  if (!s.scope.length) s.scope = [...DEFAULT_SCOPE];
  s.cat = s.cat.filter((x) => CATEGORIES.includes(x));
  s.has = s.has.filter((x) => FEATURES.includes(x));
  s.lacks = s.lacks.filter((x) => FEATURES.includes(x));
  return s;
}

function sameSet(a, b) {
  return a.length === b.length && a.every((x) => b.includes(x));
}

/** State -> GET parameters (defaults left out).  Used for the page URL and for the API alike. */
export function stateToParams(s) {
  const p = {};
  if (s.q && s.q.trim()) p.q = s.q.trim();
  if (s.match !== 'auto') p.match = s.match;
  if (!sameSet(s.scope, DEFAULT_SCOPE)) p.scope = SCOPES.filter((x) => s.scope.includes(x));
  if (s.layer.length) p.layer = s.layer;
  if (s.pole) p.pole = s.pole;
  if (s.cat.length) p.cat = CATEGORIES.filter((x) => s.cat.includes(x));
  if (s.has.length) p.has = s.has;
  if (s.lacks.length) p.lacks = s.lacks;
  for (const k of NUM_KEYS) if (s[k] != null) p[k] = s[k];
  if (s.sample !== 'all') p.sample = s.sample;
  if (s.verdict) p.verdict = s.verdict;
  if (s.expert.length) p.expert = s.expert;
  if (s.neuron.length) p.neuron = s.neuron;
  if (s.ids.length) p.ids = s.ids;
  if (s.sort !== 'relevance') p.sort = s.sort;
  if (s.order) p.order = s.order;
  if (s.group_by !== 'pole') p.group_by = s.group_by;
  return p;
}

/** Only what narrows the result set (no sort / order / grouping). */
export function filterParams(s) {
  const p = stateToParams(s);
  delete p.sort;
  delete p.order;
  delete p.group_by;
  return p;
}

/** State -> SearchQuery JSON (the field names of the POST endpoints). */
export function stateToQuery(s, extra) {
  const q = {};
  if (s.q && s.q.trim()) q.q = s.q.trim();
  if (s.match !== 'auto') q.match = s.match;
  if (!sameSet(s.scope, DEFAULT_SCOPE)) q.scope = SCOPES.filter((x) => s.scope.includes(x));
  if (s.layer.length) q.layers = s.layer;
  if (s.layer_min != null) q.layer_min = s.layer_min;
  if (s.layer_max != null) q.layer_max = s.layer_max;
  if (s.expert.length) q.experts = s.expert;
  if (s.neuron.length) q.neurons = s.neuron;
  if (s.pole) q.pole = s.pole;
  if (s.cat.length) q.categories = s.cat;
  if (s.verdict) q.verdict = s.verdict;
  if (s.has.length) q.has = s.has;
  if (s.lacks.length) q.lacks = s.lacks;
  if (s.modes_min != null) q.modes_min = s.modes_min;
  if (s.modes_max != null) q.modes_max = s.modes_max;
  if (s.sim_min != null) q.simulatability_min = s.sim_min;
  if (s.sim_max != null) q.simulatability_max = s.sim_max;
  if (s.damage_min != null) q.damage_min = s.damage_min;
  if (s.damage_max != null) q.damage_max = s.damage_max;
  if (s.sample !== 'all') q.sample = s.sample;
  if (s.ids.length) q.ids = s.ids;
  if (s.sort !== 'relevance') q.sort = s.sort;
  if (s.order) q.order = s.order;
  if (s.group_by !== 'pole') q.group_by = s.group_by;
  return { ...q, ...(extra || {}) };
}

/** True when anything narrows the result set (text or filters). */
export function hasFilters(s) {
  return Object.keys(filterParams(s)).length > 0;
}

export function stateKey(s) {
  return JSON.stringify(stateToParams(s));
}

// --------------------------------------------------------------------------- routes

/** "#/pole/L30_E47_N449__pos?tab=overview" -> { name: 'pole', arg: 'L30_E47_N449__pos', params } */
export function parseHash(hash) {
  let raw = String(hash ?? location.hash).replace(/^#/, '');
  if (raw.startsWith('!')) raw = raw.slice(1);
  const qi = raw.indexOf('?');
  const path = (qi >= 0 ? raw.slice(0, qi) : raw).replace(/^\/+/, '');
  const params = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  const parts = path.split('/').filter(Boolean);
  let name = parts[0] || 'search';
  let arg = '';
  try {
    arg = parts[1] ? decodeURIComponent(parts[1]) : '';
  } catch {
    arg = parts[1] || '';
  }
  if (!['search', 'pole', 'neuron', 'map', 'batch', 'about'].includes(name)) name = 'search';
  return { name, arg, params };
}

export function buildHash(name, arg, params) {
  let out = `#/${name}`;
  if (arg) out += `/${encodeURIComponent(arg)}`;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === '' || v === false) continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(','));
    } else sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `${out}?${qs}` : out;
}

export function poleHash(pid, params) {
  return buildHash('pole', pid, params);
}

export function neuronHash(did) {
  return buildHash('neuron', did);
}
