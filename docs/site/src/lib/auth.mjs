// Browser side of the optional GitHub sign-in (the server side is
// api/_lib/session.mjs at the repository root).
//
// Plain ESM (its only import is ./storage.mjs, also plain ESM) with no
// browser globals at import time, so `node --test` can load it and it is safe
// during server-side rendering.
//
// - The site asks /api/auth/me who is signed in (the session cookie is
//   HttpOnly). Without the functions (`npm start`, `npm run serve`) that
//   request fails: sign-in is then "unreachable" and the site falls back to
//   the profile saved in this browser, marked as not verified.
// - Signing in, switching account: a full-page navigation to
//   /api/auth/login?returnTo=<this page>, which redirects to GitHub.
// - On the way back the URL ends with #signed-in or #signin-error=<code>.

import {GUEST_ID_HEADER, GUEST_ID_STORAGE_KEY, createGuestIdSource} from './storage.mjs';

export const LOGIN_PATH = '/api/auth/login';
export const ME_PATH = '/api/auth/me';
export const SIGNOUT_PATH = '/api/auth/me?action=signout';

/** localStorage: the active profile (shown instantly on the next visit). */
export const USER_STORAGE_KEY = 'rid-github:v1:user';
/** localStorage: accounts used before in this browser (the account switcher). */
export const ACCOUNTS_STORAGE_KEY = 'rid-github:v1:accounts';
/**
 * localStorage: anonymous per-browser id for guest likes and comments
 * ('rid-community:v1:guest-id'), and the header that carries it to the
 * community API. Both come from src/lib/storage.mjs, which owns the guest id.
 */
export {GUEST_ID_HEADER, GUEST_ID_STORAGE_KEY};

export const MAX_ACCOUNTS = 8;

const LOGIN_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const NAME_MAX_LENGTH = 100;
const RETURN_TO_MAX_LENGTH = 2000;

/**
 * The messages for `#signin-error=<code>` (the codes are the closed list
 * SIGNIN_ERROR_CODES in api/_lib/session.mjs).
 */
export const SIGNIN_ERROR_MESSAGES = Object.freeze({
  access_denied: 'Sign-in was cancelled on GitHub. You can keep using the site as a guest.',
  github_error: 'GitHub could not complete the sign-in. Please try again.',
  bad_state: 'The sign-in expired or was started in another tab. Please try again.',
  missing_code: 'GitHub did not finish the sign-in. Please try again.',
  exchange_failed: 'The site could not confirm the sign-in with GitHub. Please try again.',
  profile_failed: 'The site could not read your GitHub profile. Please try again.',
  server_error: 'Sign-in failed because of a server error. Please try again.',
});

export const SIGNIN_UNKNOWN_ERROR = 'Sign-in did not complete. Please try again.';
export const SIGNIN_COOKIE_ERROR =
  'Sign-in did not complete: this browser did not keep the sign-in cookie. Allow cookies for this site and try again.';

/**
 * @typedef {object} Profile
 * @property {string} login
 * @property {string} name
 * @property {string} avatarUrl
 * @property {string} htmlUrl
 */

/**
 * The active profile saved in localStorage. `source` is 'session' when it
 * came from /api/auth/me (a verified GitHub sign-in) and 'typed' when it was
 * typed in the fallback form (not verified).
 *
 * @typedef {Profile & {source: 'session' | 'typed'}} StoredProfile
 */

/**
 * An entry of the account switcher.
 *
 * @typedef {object} Account
 * @property {string} login
 * @property {string} name
 * @property {string} avatarUrl
 */

/**
 * What /api/auth/me said.
 *
 * @typedef {{kind: 'signed-in', user: Profile}
 *   | {kind: 'signed-out', configured: boolean}
 *   | {kind: 'unreachable'}} MeResult
 */

/**
 * The return from GitHub, read from the URL fragment.
 *
 * @typedef {{kind: 'signed-in'} | {kind: 'error', code: string, message: string}} AuthHash
 */

// ---------------------------------------------------------------------------
// Profiles

/** @param {unknown} value */
function httpsUrl(value) {
  if (typeof value !== 'string' || value.length > 500) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

/**
 * Checks a profile (from the API or from localStorage, which is not trusted
 * input). Returns null when the login is not a GitHub login.
 *
 * @param {unknown} value
 * @returns {Profile | null}
 */
export function normalizeProfile(value) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = /** @type {Record<string, unknown>} */ (value);
  const login = raw.login;
  if (typeof login !== 'string' || !LOGIN_PATTERN.test(login)) {
    return null;
  }
  const name =
    typeof raw.name === 'string' && raw.name.trim()
      ? Array.from(raw.name.trim()).slice(0, NAME_MAX_LENGTH).join('')
      : login;
  return {
    login,
    name,
    avatarUrl: httpsUrl(raw.avatarUrl) ?? `https://github.com/${login}.png`,
    htmlUrl: httpsUrl(raw.htmlUrl) ?? `https://github.com/${login}`,
  };
}

/**
 * Reads the saved active profile. A profile saved before sign-in used OAuth
 * has no `source`: it was typed, so it counts as not verified.
 *
 * @param {string | null | undefined} raw
 * @returns {StoredProfile | null}
 */
export function parseStoredProfile(raw) {
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw);
    const profile = normalizeProfile(value);
    if (!profile) {
      return null;
    }
    return {...profile, source: value.source === 'session' ? 'session' : 'typed'};
  } catch {
    return null;
  }
}

/**
 * @param {Profile} profile
 * @param {'session' | 'typed'} source
 * @returns {string}
 */
export function serializeStoredProfile(profile, source) {
  const {login, name, avatarUrl, htmlUrl} = profile;
  return JSON.stringify({login, name, avatarUrl, htmlUrl, source});
}

// ---------------------------------------------------------------------------
// Account switcher

/**
 * Reads the saved account list: valid entries only, unique by login
 * (case-insensitive, first wins), at most MAX_ACCOUNTS.
 *
 * @param {string | null | undefined} raw
 * @returns {Account[]}
 */
export function parseAccounts(raw) {
  if (!raw) {
    return [];
  }
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(value)) {
    return [];
  }
  /** @type {Account[]} */
  const accounts = [];
  const seen = new Set();
  for (const item of value) {
    const profile = normalizeProfile(item);
    if (!profile || seen.has(profile.login.toLowerCase())) {
      continue;
    }
    seen.add(profile.login.toLowerCase());
    accounts.push({login: profile.login, name: profile.name, avatarUrl: profile.avatarUrl});
    if (accounts.length === MAX_ACCOUNTS) {
      break;
    }
  }
  return accounts;
}

/**
 * The account list with `profile` first (added, or moved up and refreshed).
 * Accounts are never removed by signing in or out; the oldest drops off past
 * MAX_ACCOUNTS.
 *
 * @param {readonly Account[]} accounts
 * @param {Profile | Account} profile
 * @returns {Account[]}
 */
export function rememberAccount(accounts, profile) {
  const entry = normalizeProfile(profile);
  if (!entry) {
    return [...accounts];
  }
  const key = entry.login.toLowerCase();
  return [
    {login: entry.login, name: entry.name, avatarUrl: entry.avatarUrl},
    ...accounts.filter((account) => account.login.toLowerCase() !== key),
  ].slice(0, MAX_ACCOUNTS);
}

/**
 * @param {readonly Account[]} accounts
 * @returns {string}
 */
export function serializeAccounts(accounts) {
  return JSON.stringify(accounts.map(({login, name, avatarUrl}) => ({login, name, avatarUrl})));
}

// ---------------------------------------------------------------------------
// URLs

/**
 * Same rules as sanitizeReturnTo in api/_lib/session.mjs: a path on this
 * site (one leading '/', no scheme, host, backslash or whitespace), without
 * the fragment; anything else, and /api/ paths, become '/'.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function safeReturnTo(value) {
  if (typeof value !== 'string' || !value || value.length > RETURN_TO_MAX_LENGTH) {
    return '/';
  }
  if (!value.startsWith('/') || value.startsWith('//') || /[\\\s\x00-\x1F\x7F]/.test(value)) {
    return '/';
  }
  const base = 'https://return-to.invalid';
  let url;
  try {
    url = new URL(value, base);
  } catch {
    return '/';
  }
  if (url.origin !== base) {
    return '/';
  }
  const path = `${url.pathname}${url.search}`;
  // Dot segments can normalize to a protocol-relative '//host' path.
  if (path.startsWith('//')) {
    return '/';
  }
  if (path === '/api' || path.startsWith('/api/') || path.startsWith('/api?')) {
    return '/';
  }
  return path;
}

/**
 * The sign-in URL for the page at `location` (window.location, or the
 * router's location: only pathname and search are used).
 *
 * @param {{pathname?: string, search?: string} | null | undefined} location
 * @param {{switchAccount?: boolean, login?: string | null}} [options]
 *   switchAccount: ask GitHub to show its account picker; login: suggest an account.
 * @returns {string}
 */
export function buildLoginUrl(location, {switchAccount = false, login = null} = {}) {
  const returnTo = safeReturnTo(`${location?.pathname ?? '/'}${location?.search ?? ''}`);
  const params = new URLSearchParams({returnTo});
  if (switchAccount) {
    params.set('switch', '1');
  }
  if (login && LOGIN_PATTERN.test(login)) {
    params.set('login', login);
  }
  return `${LOGIN_PATH}?${params.toString()}`;
}

/**
 * Reads the return from GitHub in the URL fragment: '#signed-in' or
 * '#signin-error=<code>'. Anything else is null (and is left alone).
 *
 * @param {string | null | undefined} hash
 * @returns {AuthHash | null}
 */
export function readAuthHash(hash) {
  if (typeof hash !== 'string') {
    return null;
  }
  const value = hash.startsWith('#') ? hash.slice(1) : hash;
  if (value === 'signed-in') {
    return {kind: 'signed-in'};
  }
  const match = /^signin-error=([A-Za-z0-9_-]{0,40})$/.exec(value);
  if (!match) {
    return null;
  }
  const code = match[1];
  const message = Object.prototype.hasOwnProperty.call(SIGNIN_ERROR_MESSAGES, code)
    ? SIGNIN_ERROR_MESSAGES[/** @type {keyof typeof SIGNIN_ERROR_MESSAGES} */ (code)]
    : SIGNIN_UNKNOWN_ERROR;
  return {kind: 'error', code, message};
}

/**
 * The URL without the sign-in fragment, for history.replaceState.
 *
 * @param {{pathname: string, search: string}} location
 * @returns {string}
 */
export function urlWithoutHash(location) {
  return `${location.pathname}${location.search}`;
}

// ---------------------------------------------------------------------------
// /api/auth/me

/**
 * Interprets an /api/auth/me answer. Anything that is not the expected JSON
 * (an HTML 404 page from a static server, a dev server's index.html) means
 * the functions are not running here.
 *
 * @param {unknown} json
 * @returns {MeResult}
 */
export function parseMeResponse(json) {
  if (!json || typeof json !== 'object') {
    return {kind: 'unreachable'};
  }
  const value = /** @type {Record<string, unknown>} */ (json);
  if (value.signedIn === true) {
    const user = normalizeProfile(value.user);
    return user ? {kind: 'signed-in', user} : {kind: 'unreachable'};
  }
  if (value.signedIn === false) {
    return {kind: 'signed-out', configured: value.configured !== false};
  }
  return {kind: 'unreachable'};
}

/**
 * Asks the site's /api/auth/me who is signed in. Never rejects.
 *
 * @param {{fetchImpl?: typeof fetch, timeoutMs?: number}} [options]
 * @returns {Promise<MeResult>}
 */
export async function fetchMe({fetchImpl, timeoutMs = 8000} = {}) {
  const doFetch = fetchImpl ?? globalThis.fetch;
  if (typeof doFetch !== 'function') {
    return {kind: 'unreachable'};
  }
  try {
    const response = await doFetch(ME_PATH, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {Accept: 'application/json'},
      signal: typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(timeoutMs) : undefined,
    });
    const type = response.headers.get('content-type') ?? '';
    if (!response.ok || !type.includes('application/json')) {
      return {kind: 'unreachable'};
    }
    return parseMeResponse(await response.json());
  } catch {
    return {kind: 'unreachable'};
  }
}

/**
 * Asks the server to clear the session cookie. Resolves false when the
 * server could not be reached; the caller then keeps the visitor signed in
 * (where sign-in is available) and asks them to try again (see
 * mustStaySignedIn).
 *
 * @param {{fetchImpl?: typeof fetch}} [options]
 * @returns {Promise<boolean>}
 */
export async function requestSignOut({fetchImpl} = {}) {
  const doFetch = fetchImpl ?? globalThis.fetch;
  if (typeof doFetch !== 'function') {
    return false;
  }
  try {
    const response = await doFetch(SIGNOUT_PATH, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {Accept: 'application/json'},
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * After a sign-out request, whether the visitor must stay signed in here
 * (and be asked to try again) instead of becoming a guest locally: the
 * request failed and the session cookie may still be valid, because sign-in
 * is available on this deployment or because /api/auth/me (awaited before
 * the request) confirmed a session. Clearing the identity in the page while
 * the cookie lives would make later "guest" writes travel with the cookie.
 * Where sign-in is unreachable or not configured there is no cookie to keep
 * in step, so a local sign-out is fine.
 *
 * @param {{ok: boolean, availability: string, me?: MeResult | null}} input
 *   `availability` as the page knows it now; `me` the awaited /api/auth/me answer.
 * @returns {boolean}
 */
export function mustStaySignedIn({ok, availability, me}) {
  return !ok && (availability === 'available' || me?.kind === 'signed-in');
}

// ---------------------------------------------------------------------------
// Guest id (for the shared community store)

/**
 * The anonymous id of this browser, created on first use (a random UUID v4 in
 * localStorage). The shared community API keys guest likes by it (sent as
 * X-Guest-Id), so a guest can remove their own like and delete their own
 * comments. A thin wrapper over createGuestIdSource in src/lib/storage.mjs,
 * the one implementation the community store uses: the same key, the same
 * rules (a saved value that is not a UUID v4 is replaced; blocked storage
 * gives an id that is not kept).
 *
 * @param {{getItem: (key: string) => string | null, setItem: (key: string, value: string) => void} | null | undefined} storage
 * @param {() => string} [makeId] Defaults to a random UUID v4.
 * @returns {string}
 */
export function getGuestId(storage, makeId) {
  return /** @type {string} */ (createGuestIdSource(() => storage, makeId)());
}
