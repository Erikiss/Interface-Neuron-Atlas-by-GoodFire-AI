// Same-origin API client: API key handling (401), rate-limit notices (429), aborts.

const KEY_STORAGE = 'na.apiKey';

export class ApiError extends Error {
  constructor(status, detail, { code = 'error', retryAfter = null } = {}) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail || '';
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

let apiKey = '';
try {
  apiKey = localStorage.getItem(KEY_STORAGE) || '';
} catch {
  apiKey = '';
}

const hooks = {
  /** async () => string | null : ask the user for a key (set by the UI). */
  askKey: null,
  /** (seconds) => void : called on 429. */
  onRateLimit: null,
};

export function configure(next) {
  Object.assign(hooks, next);
}

export function getApiKey() {
  return apiKey;
}

export function setApiKey(key) {
  apiKey = key || '';
  try {
    if (apiKey) localStorage.setItem(KEY_STORAGE, apiKey);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* private mode: keep it for this page only */
  }
}

/** Serialise a plain object into a query string; arrays become comma lists, empty values are dropped. */
export function toQuery(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === '' || v === false) continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(','));
    } else sp.set(k, v === true ? 'true' : String(v));
  }
  return sp.toString();
}

/** URL for links (downloads, print pages); carries the API key as ?api_key= when one is set. */
export function apiUrl(path, params, { withKey = true } = {}) {
  const p = { ...(params || {}) };
  if (withKey && apiKey) p.api_key = apiKey;
  const qs = toQuery(p);
  return qs ? `${path}?${qs}` : path;
}

let keyPrompt = null; // one prompt shared by all requests that hit a 401 at the same time

async function promptForKey() {
  if (!hooks.askKey) return null;
  if (!keyPrompt) {
    keyPrompt = Promise.resolve(hooks.askKey()).finally(() => {
      keyPrompt = null;
    });
  }
  return keyPrompt;
}

/**
 * Call the API.  `as`: json (default) | text.  Throws ApiError with the server's
 * `detail` message; AbortError passes through untouched.
 */
export async function api(path, { params, method = 'GET', body, signal, as = 'json', retried = false } = {}) {
  const qs = toQuery(params);
  const url = qs ? `${path}?${qs}` : path;
  const headers = { Accept: as === 'json' ? 'application/json' : '*/*' };
  if (apiKey) headers['X-API-Key'] = apiKey;
  const init = { method, headers, signal };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError(0, String((err && err.message) || err), { code: 'network' });
  }
  if (res.status === 401 && !retried) {
    const key = await promptForKey();
    if (key) {
      setApiKey(key);
      return api(path, { params, method, body, signal, as, retried: true });
    }
  }
  if (!res.ok) {
    let detail = '';
    let code = 'error';
    try {
      const data = await res.json();
      code = data.error || code;
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      detail = res.statusText || `HTTP ${res.status}`;
    }
    const retryAfter = Number(res.headers.get('Retry-After')) || null;
    if (res.status === 429 && hooks.onRateLimit) hooks.onRateLimit(retryAfter);
    throw new ApiError(res.status, detail, { code, retryAfter });
  }
  return as === 'json' ? res.json() : res.text();
}

export function isAbort(err) {
  return !!err && err.name === 'AbortError';
}
