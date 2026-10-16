// Optional GitHub sign-in for the docs site: OAuth (web application flow) and
// a signed session cookie. Used by the Vercel functions in api/auth/ and, for
// writes that carry the signer's identity, by the community API through
// getSessionUser(req).
//
// - Nothing here is required to read the site, like or comment: visitors are
//   guests unless they sign in.
// - The GitHub access token obtained in the callback is used once, to read
//   the public profile (GET https://api.github.com/user), and then dropped. It
//   is never stored, logged, put in a cookie or sent to the browser. No OAuth
//   scope is requested, so the token could only read public data anyway.
// - The session is a cookie the browser keeps for 400 days (the longest a
//   browser allows), renewed by /api/auth/me after 30 days, so a signed-in
//   visitor stays signed in until they sign out or sign in as someone else.
//   It holds the public profile only, signed with HMAC-SHA256(SESSION_SECRET).
// - Never logs codes, tokens, cookies, secrets or request bodies.
//
// Plain ESM with no dependencies (node:crypto and the global fetch), so the
// functions deploy without an install step and node:test loads it directly.
// The `_lib` folder name starts with an underscore, so Vercel does not expose
// it as a route.

import {createHash, createHmac, randomBytes as nodeRandomBytes, timingSafeEqual} from 'node:crypto';

// ---------------------------------------------------------------------------
// Constants

export const SESSION_COOKIE = 'rid_session';
export const STATE_COOKIE = 'rid_oauth_state';
export const SESSION_VERSION = 1;
/** 400 days, the longest lifetime browsers accept for a cookie. */
export const SESSION_MAX_AGE_SECONDS = 34560000;
/** /api/auth/me re-issues the session cookie once it is older than this. */
export const SESSION_RENEW_AFTER_SECONDS = 30 * 24 * 60 * 60;
export const STATE_MAX_AGE_SECONDS = 600;
export const STATE_COOKIE_PATH = '/api/auth';
export const SESSION_SECRET_MIN_LENGTH = 32;

export const LOGIN_PATH = '/api/auth/login';
export const CALLBACK_PATH = '/api/auth/callback';
export const ME_PATH = '/api/auth/me';

export const GITHUB_AUTHORIZE_URL = 'https://github.com/login/oauth/authorize';
export const GITHUB_TOKEN_URL = 'https://github.com/login/oauth/access_token';
export const GITHUB_USER_URL = 'https://api.github.com/user';
export const USER_AGENT = 'research-intelligence-docs-site';
export const FETCH_TIMEOUT_MS = 10000;

/** GitHub logins: letters, digits and single hyphens, 1-39 characters. */
export const LOGIN_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

/**
 * The only values `#signin-error=<code>` can take. The browser maps each to a
 * message; nothing else from GitHub or the server is reflected into the URL.
 */
export const SIGNIN_ERROR_CODES = Object.freeze([
  'access_denied', // the visitor cancelled on GitHub
  'github_error', // GitHub answered the authorize request with another error
  'bad_state', // missing, expired, tampered or mismatched state
  'missing_code', // GitHub came back without a code
  'exchange_failed', // the code could not be exchanged for a token
  'profile_failed', // GET /user failed or answered something unexpected
  'server_error', // anything else
]);

export const NOT_CONFIGURED_MESSAGE = 'Sign-in is not configured on this deployment';

const NAME_MAX_LENGTH = 100;
const URL_MAX_LENGTH = 500;
const RETURN_TO_MAX_LENGTH = 2000;
const STATE_BYTES = 32;
const STATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const BASE64URL_PATTERN = /^[A-Za-z0-9_-]*$/;
const COOKIE_NAME_PATTERN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
// RFC 6265 cookie-octet: no controls, whitespace, double quote, comma, semicolon or backslash.
const COOKIE_VALUE_PATTERN = /^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*$/;
const HOST_PATTERN = /^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*(?::\d{1,5})?$/;
const LOCAL_HOST_PATTERN = /^(?:localhost|127\.0\.0\.1)(?::\d{1,5})?$/;

// ---------------------------------------------------------------------------
// Types

/**
 * The public profile kept in the session (and returned by /api/auth/me).
 *
 * @typedef {object} SessionUser
 * @property {string} login GitHub login.
 * @property {number} id GitHub numeric user id (stable across renames).
 * @property {string} name Display name (the login when GitHub has none).
 * @property {string} avatarUrl https URL of the avatar.
 * @property {string} htmlUrl https URL of the GitHub profile.
 */

/**
 * @typedef {SessionUser & {v: number, iat: number}} SessionPayload
 * `iat` is the issue time in seconds since the epoch.
 */

/**
 * @typedef {object} AuthEnv
 * @property {string} [GITHUB_OAUTH_CLIENT_ID]
 * @property {string} [GITHUB_OAUTH_CLIENT_SECRET]
 * @property {string} [SESSION_SECRET] At least 32 random characters.
 * @property {string} [SESSION_SECRET_PREVIOUS] The secret before a rotation: still accepted, never used to sign.
 */

/**
 * What a handler receives. `headers` is a Node.js-style object (lowercase
 * names) or a Fetch Headers instance.
 *
 * @typedef {object} HandlerRequest
 * @property {string} method
 * @property {Record<string, string | string[] | undefined>} [query]
 * @property {any} [headers]
 */

/**
 * What a handler returns. Header names are lowercase; 'set-cookie' is an
 * array (one entry per cookie).
 *
 * @typedef {object} HandlerResponse
 * @property {number} status
 * @property {Record<string, string | string[]>} headers
 * @property {string} body
 */

/**
 * @typedef {object} CookieOptions
 * @property {number} [maxAge] Seconds; 0 deletes the cookie.
 * @property {Date} [expires]
 * @property {string} [path]
 * @property {boolean} [httpOnly]
 * @property {boolean} [secure]
 * @property {'Lax' | 'Strict' | 'None'} [sameSite]
 */

// ---------------------------------------------------------------------------
// Small helpers

/** @param {Buffer | string} value */
export function base64url(value) {
  return Buffer.from(value).toString('base64url');
}

/** @param {string} value */
function fromBase64url(value) {
  if (typeof value !== 'string' || !BASE64URL_PATTERN.test(value)) {
    return null;
  }
  return Buffer.from(value, 'base64url');
}

/**
 * @param {string} secret
 * @param {string} data
 */
function hmac(secret, data) {
  return createHmac('sha256', secret).update(data, 'utf8').digest('base64url');
}

/**
 * Constant-time string comparison. Both sides are hashed first, so neither
 * the content nor the length of a secret leaks through timing.
 *
 * @param {unknown} a
 * @param {unknown} b
 * @returns {boolean}
 */
export function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') {
    return false;
  }
  const left = createHash('sha256').update(a, 'utf8').digest();
  const right = createHash('sha256').update(b, 'utf8').digest();
  return timingSafeEqual(left, right);
}

/** @param {unknown} value */
function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim() !== '';
}

/**
 * An https URL of reasonable length, or null.
 *
 * @param {unknown} value
 * @returns {string | null}
 */
function httpsUrl(value) {
  if (typeof value !== 'string' || value.length > URL_MAX_LENGTH) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.toString() : null;
  } catch {
    return null;
  }
}

/**
 * A display name: trimmed, control characters removed, at most 100
 * characters; the login when empty.
 *
 * @param {unknown} name
 * @param {string} login
 */
function displayName(name, login) {
  if (typeof name !== 'string') {
    return login;
  }
  const clean = Array.from(name.replace(/[\x00-\x1F\x7F]/g, ' ').trim())
    .slice(0, NAME_MAX_LENGTH)
    .join('')
    .trim();
  return clean || login;
}

/**
 * The secrets that may verify a signature: the current one first, then the
 * previous one (during a rotation). Secrets shorter than 32 characters are
 * ignored.
 *
 * @param {string | readonly (string | undefined | null)[] | undefined | null} secrets
 * @returns {string[]}
 */
function secretList(secrets) {
  const list = Array.isArray(secrets) ? secrets : [secrets];
  return list.filter(
    /** @returns {s is string} */ (s) => typeof s === 'string' && s.length >= SESSION_SECRET_MIN_LENGTH,
  );
}

/**
 * Reads one header from a Node.js headers object or a Fetch Headers.
 *
 * @param {any} headers
 * @param {string} name lowercase
 * @returns {string | undefined}
 */
export function readHeader(headers, name) {
  if (!headers) {
    return undefined;
  }
  if (typeof headers.get === 'function') {
    const value = headers.get(name);
    return value === null ? undefined : String(value);
  }
  const value = headers[name] ?? headers[name.toLowerCase()];
  if (Array.isArray(value)) {
    return value.join(name === 'cookie' ? '; ' : ', ');
  }
  return value === undefined || value === null ? undefined : String(value);
}

/**
 * First value of a query parameter.
 *
 * @param {Record<string, string | string[] | undefined> | undefined} query
 * @param {string} name
 * @returns {string | undefined}
 */
function queryValue(query, name) {
  if (!query) {
    return undefined;
  }
  const value = query[name];
  if (Array.isArray(value)) {
    return typeof value[0] === 'string' ? value[0] : undefined;
  }
  return typeof value === 'string' ? value : undefined;
}

// ---------------------------------------------------------------------------
// Configuration

/**
 * The OAuth settings, or null when sign-in is not configured (a missing
 * variable, or a SESSION_SECRET shorter than 32 characters).
 *
 * @param {AuthEnv | undefined} env
 * @returns {{clientId: string, clientSecret: string, secrets: string[]} | null}
 */
export function readAuthConfig(env) {
  const clientId = env?.GITHUB_OAUTH_CLIENT_ID?.trim();
  const clientSecret = env?.GITHUB_OAUTH_CLIENT_SECRET?.trim();
  const secrets = secretList([env?.SESSION_SECRET, env?.SESSION_SECRET_PREVIOUS]);
  const current = env?.SESSION_SECRET;
  if (!clientId || !clientSecret || typeof current !== 'string' || current.length < SESSION_SECRET_MIN_LENGTH) {
    return null;
  }
  return {clientId, clientSecret, secrets};
}

// ---------------------------------------------------------------------------
// Cookies

/**
 * Parses a Cookie header into a name → value map. Malformed pairs are
 * skipped; when a name repeats, the first value wins (the browser sends the
 * most specific cookie first). The map has no prototype.
 *
 * @param {string | string[] | undefined | null} header
 * @returns {Record<string, string>}
 */
export function parseCookies(header) {
  /** @type {Record<string, string>} */
  const cookies = Object.create(null);
  const text = Array.isArray(header) ? header.join('; ') : header;
  if (typeof text !== 'string' || !text) {
    return cookies;
  }
  for (const part of text.split(';')) {
    const index = part.indexOf('=');
    if (index <= 0) {
      continue;
    }
    const name = part.slice(0, index).trim();
    if (!COOKIE_NAME_PATTERN.test(name) || name in cookies) {
      continue;
    }
    let value = part.slice(index + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
      value = value.slice(1, -1);
    }
    try {
      value = decodeURIComponent(value);
    } catch {
      // Not percent-encoded: keep it as it is.
    }
    cookies[name] = value;
  }
  return cookies;
}

/**
 * Builds a Set-Cookie header value. Throws on a name or value that a cookie
 * cannot hold (the values used here are base64url and dots).
 *
 * @param {string} name
 * @param {string} value
 * @param {CookieOptions} [options]
 * @returns {string}
 */
export function serializeCookie(name, value, options = {}) {
  if (!COOKIE_NAME_PATTERN.test(name)) {
    throw new TypeError('invalid cookie name');
  }
  if (typeof value !== 'string' || !COOKIE_VALUE_PATTERN.test(value)) {
    throw new TypeError('invalid cookie value');
  }
  const parts = [`${name}=${value}`];
  if (options.maxAge !== undefined) {
    if (!Number.isInteger(options.maxAge)) {
      throw new TypeError('cookie maxAge must be an integer');
    }
    parts.push(`Max-Age=${Math.max(0, options.maxAge)}`);
  }
  if (options.expires) {
    parts.push(`Expires=${options.expires.toUTCString()}`);
  }
  if (options.path) {
    if (!/^\/[\x21-\x3A\x3C-\x7E]*$/.test(options.path)) {
      throw new TypeError('invalid cookie path');
    }
    parts.push(`Path=${options.path}`);
  }
  if (options.httpOnly) {
    parts.push('HttpOnly');
  }
  if (options.secure) {
    parts.push('Secure');
  }
  if (options.sameSite) {
    if (!['Lax', 'Strict', 'None'].includes(options.sameSite)) {
      throw new TypeError('invalid cookie SameSite');
    }
    parts.push(`SameSite=${options.sameSite}`);
  }
  return parts.join('; ');
}

/** @param {string} value */
function sessionCookie(value) {
  return serializeCookie(SESSION_COOKIE, value, {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
}

function clearSessionCookie() {
  return serializeCookie(SESSION_COOKIE, '', {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    path: '/',
    maxAge: 0,
    expires: new Date(0),
  });
}

/** @param {string} value */
function stateCookie(value) {
  return serializeCookie(STATE_COOKIE, value, {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    path: STATE_COOKIE_PATH,
    maxAge: STATE_MAX_AGE_SECONDS,
  });
}

function clearStateCookie() {
  return serializeCookie(STATE_COOKIE, '', {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    path: STATE_COOKIE_PATH,
    maxAge: 0,
    expires: new Date(0),
  });
}

// ---------------------------------------------------------------------------
// Session

/**
 * Checks and normalizes a session user (from GitHub or from a cookie).
 *
 * @param {any} value
 * @returns {SessionUser | null}
 */
export function normalizeSessionUser(value) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const {login, id} = value;
  if (typeof login !== 'string' || !LOGIN_PATTERN.test(login)) {
    return null;
  }
  if (!Number.isSafeInteger(id) || id <= 0) {
    return null;
  }
  return {
    login,
    id,
    name: displayName(value.name, login),
    avatarUrl: httpsUrl(value.avatarUrl) ?? `https://github.com/${login}.png`,
    htmlUrl: httpsUrl(value.htmlUrl) ?? `https://github.com/${login}`,
  };
}

/**
 * Signs a session: base64url(JSON {v: 1, login, id, name, avatarUrl, htmlUrl,
 * iat}) + '.' + base64url(HMAC-SHA256(secret, <that first part>)).
 *
 * @param {SessionUser & {iat: number}} payload
 * @param {string} secret SESSION_SECRET (at least 32 characters).
 * @returns {string}
 */
export function signSession(payload, secret) {
  if (typeof secret !== 'string' || secret.length < SESSION_SECRET_MIN_LENGTH) {
    throw new TypeError(`the session secret must be at least ${SESSION_SECRET_MIN_LENGTH} characters`);
  }
  const user = normalizeSessionUser(payload);
  if (!user) {
    throw new TypeError('invalid session user');
  }
  const iat = payload.iat;
  if (!Number.isSafeInteger(iat) || iat <= 0) {
    throw new TypeError('session iat must be a positive integer (seconds)');
  }
  /** @type {SessionPayload} */
  const body = {v: SESSION_VERSION, ...user, iat};
  const encoded = base64url(JSON.stringify(body));
  return `${encoded}.${hmac(secret, encoded)}`;
}

/**
 * Verifies a session cookie value against the current secret and, during a
 * rotation, the previous one. Returns the payload and which secret matched
 * (0 = current), or null for anything tampered, malformed or of another
 * version.
 *
 * @param {unknown} value
 * @param {string | readonly (string | undefined | null)[] | undefined | null} secrets
 * @returns {{payload: SessionPayload, secretIndex: number} | null}
 */
export function verifySessionDetailed(value, secrets) {
  if (typeof value !== 'string' || value.length > 4096) {
    return null;
  }
  const parts = value.split('.');
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    return null;
  }
  const [encoded, signature] = parts;
  const keys = secretList(secrets);
  const secretIndex = keys.findIndex((key) => safeEqual(hmac(key, encoded), signature));
  if (secretIndex === -1) {
    return null;
  }
  const raw = fromBase64url(encoded);
  if (!raw) {
    return null;
  }
  let data;
  try {
    data = JSON.parse(raw.toString('utf8'));
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || data.v !== SESSION_VERSION) {
    return null;
  }
  if (!Number.isSafeInteger(data.iat) || data.iat <= 0) {
    return null;
  }
  const user = normalizeSessionUser(data);
  if (!user) {
    return null;
  }
  return {payload: {v: SESSION_VERSION, ...user, iat: data.iat}, secretIndex};
}

/**
 * Verifies a session cookie value. Returns the payload, or null.
 *
 * @param {unknown} value
 * @param {string | readonly (string | undefined | null)[] | undefined | null} secrets
 *   SESSION_SECRET, or [SESSION_SECRET, SESSION_SECRET_PREVIOUS].
 * @returns {SessionPayload | null}
 */
export function verifySession(value, secrets) {
  return verifySessionDetailed(value, secrets)?.payload ?? null;
}

/**
 * The signed-in user of a request, or null for a guest (no cookie, a cookie
 * that does not verify, or sign-in not configured on this deployment). This is
 * what other functions (the community API) use to learn who is writing: the
 * identity comes from the signed cookie, never from the request body.
 *
 * @param {{headers?: any} | null | undefined} req A Node.js request, a Fetch Request or a HandlerRequest.
 * @param {AuthEnv} [env] Defaults to process.env.
 * @returns {SessionUser | null}
 */
export function getSessionUser(req, env = process.env) {
  const result = readSession(req, env);
  return result ? toUser(result.payload) : null;
}

/**
 * @param {{headers?: any} | null | undefined} req
 * @param {AuthEnv | undefined} env
 */
function readSession(req, env) {
  // Not configured: everyone is a guest, as the UI shows (me answers
  // {signedIn: false, configured: false}).
  const config = readAuthConfig(env);
  if (!config) {
    return null;
  }
  const cookies = parseCookies(readHeader(req?.headers, 'cookie'));
  const value = cookies[SESSION_COOKIE];
  return value ? verifySessionDetailed(value, config.secrets) : null;
}

/**
 * @param {SessionPayload} payload
 * @returns {SessionUser}
 */
function toUser({login, id, name, avatarUrl, htmlUrl}) {
  return {login, id, name, avatarUrl, htmlUrl};
}

// ---------------------------------------------------------------------------
// OAuth state

/**
 * The state cookie: <state>.<base64url(returnTo)>.<issued at, seconds>.<HMAC>.
 * The HMAC input starts with "state." so it can never be mistaken for a
 * session signature (whose input has no dot).
 *
 * @param {{state: string, returnTo: string, iat: number}} value
 * @param {string} secret
 */
export function signState({state, returnTo, iat}, secret) {
  const data = `${state}.${base64url(returnTo)}.${iat}`;
  return `${data}.${hmac(secret, `state.${data}`)}`;
}

/**
 * @param {unknown} value
 * @param {string[]} secrets
 * @param {number} nowSeconds
 * @returns {{state: string, returnTo: string} | null}
 */
export function verifyState(value, secrets, nowSeconds) {
  if (typeof value !== 'string' || value.length > 4096) {
    return null;
  }
  const parts = value.split('.');
  if (parts.length !== 4) {
    return null;
  }
  const [state, encodedReturnTo, iatText, signature] = parts;
  const data = `${state}.${encodedReturnTo}.${iatText}`;
  if (!secretList(secrets).some((key) => safeEqual(hmac(key, `state.${data}`), signature))) {
    return null;
  }
  if (!STATE_PATTERN.test(state) || !/^\d{1,12}$/.test(iatText)) {
    return null;
  }
  const iat = Number(iatText);
  if (nowSeconds - iat > STATE_MAX_AGE_SECONDS || iat - nowSeconds > 60) {
    return null;
  }
  const raw = fromBase64url(encodedReturnTo);
  if (!raw) {
    return null;
  }
  return {state, returnTo: sanitizeReturnTo(raw.toString('utf8'))};
}

// ---------------------------------------------------------------------------
// Return path

/**
 * A same-site path to come back to after sign-in: it must start with one
 * '/', with no scheme, host, backslash, whitespace or control character. The
 * fragment is dropped (the callback adds its own), and /api/ paths become
 * '/'. Anything else becomes '/'.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function sanitizeReturnTo(value) {
  if (typeof value !== 'string' || !value || value.length > RETURN_TO_MAX_LENGTH) {
    return '/';
  }
  if (!value.startsWith('/') || value.startsWith('//')) {
    return '/';
  }
  // Browsers treat '\' as '/', and the URL parser drops tabs and newlines.
  if (/[\\\s\x00-\x1F\x7F]/.test(value)) {
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

// ---------------------------------------------------------------------------
// Responses

const BASE_HEADERS = Object.freeze({
  'cache-control': 'no-store',
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
});

/**
 * @param {number} status
 * @param {unknown} json
 * @param {string[]} [cookies]
 * @returns {HandlerResponse}
 */
function jsonResponse(status, json, cookies = []) {
  /** @type {Record<string, string | string[]>} */
  const headers = {...BASE_HEADERS, 'content-type': 'application/json; charset=utf-8'};
  if (cookies.length) {
    headers['set-cookie'] = cookies;
  }
  return {status, headers, body: JSON.stringify(json)};
}

/**
 * @param {string} location
 * @param {string[]} [cookies]
 * @returns {HandlerResponse}
 */
function redirect(location, cookies = []) {
  /** @type {Record<string, string | string[]>} */
  const headers = {...BASE_HEADERS, location};
  if (cookies.length) {
    headers['set-cookie'] = cookies;
  }
  return {status: 302, headers, body: ''};
}

/** @param {string} text */
function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/**
 * A small self-contained HTML page (no scripts, inline styles only).
 *
 * @param {number} status
 * @param {string} title
 * @param {string} message
 * @param {string} backTo
 * @param {string[]} [cookies]
 * @returns {HandlerResponse}
 */
function htmlPage(status, title, message, backTo, cookies = []) {
  const body = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escapeHtml(title)}</title>
<style>
:root{color-scheme:light dark}
body{margin:0;padding:3rem 1rem;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#fff;color:#1c1e21}
main{max-width:32rem;margin:0 auto}
h1{font-size:1.375rem;margin:0 0 .75rem}
a{color:#1f5fbf}
@media (prefers-color-scheme:dark){body{background:#1b1b1d;color:#e3e3e3}a{color:#8ab4f8}}
</style>
</head>
<body>
<main>
<h1>${escapeHtml(title)}</h1>
<p>${escapeHtml(message)}</p>
<p>Reading, likes and comments keep working as a guest.</p>
<p><a href="${escapeHtml(backTo)}">Back to the site</a></p>
</main>
</body>
</html>
`;
  /** @type {Record<string, string | string[]>} */
  const headers = {
    ...BASE_HEADERS,
    'content-type': 'text/html; charset=utf-8',
    'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  };
  if (cookies.length) {
    headers['set-cookie'] = cookies;
  }
  return {status, headers, body};
}

/**
 * @param {string[]} allowed
 * @returns {HandlerResponse}
 */
function methodNotAllowed(allowed) {
  const response = jsonResponse(405, {error: 'method not allowed'});
  response.headers.allow = allowed.join(', ');
  return response;
}

// ---------------------------------------------------------------------------
// Request origin

/**
 * The site's origin as the browser sees it, for redirect_uri. On Vercel the
 * platform sets the Host and X-Forwarded-* headers. GitHub rejects any
 * redirect_uri that does not match the OAuth App's callback URL, so a wrong
 * host cannot send a code anywhere else.
 *
 * @param {any} headers
 * @returns {string | null}
 */
export function requestOrigin(headers) {
  const forwardedHost = readHeader(headers, 'x-forwarded-host')?.split(',')[0]?.trim();
  const host = forwardedHost || readHeader(headers, 'host')?.trim();
  if (!host || !HOST_PATTERN.test(host)) {
    return null;
  }
  const forwardedProto = readHeader(headers, 'x-forwarded-proto')?.split(',')[0]?.trim().toLowerCase();
  // Always https, except on a local development server (`vercel dev`),
  // which is plain http unless a proxy says otherwise.
  const proto = LOCAL_HOST_PATTERN.test(host) && forwardedProto !== 'https' ? 'http' : 'https';
  return `${proto}://${host.toLowerCase()}`;
}

/**
 * True unless the request says it comes from another site (Sec-Fetch-Site or
 * Origin). Used for the sign-out POST, so another site cannot sign a visitor
 * out.
 *
 * @param {any} headers
 */
function isSameOriginRequest(headers) {
  const site = readHeader(headers, 'sec-fetch-site');
  if (site && site !== 'same-origin') {
    return false;
  }
  const origin = readHeader(headers, 'origin');
  if (origin) {
    const expected = requestOrigin(headers);
    try {
      return Boolean(expected) && new URL(origin).origin === expected;
    } catch {
      return false;
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Handlers

/**
 * @typedef {object} AuthHandlers
 * @property {(request: HandlerRequest) => Promise<HandlerResponse>} login GET /api/auth/login
 * @property {(request: HandlerRequest) => Promise<HandlerResponse>} callback GET /api/auth/callback
 * @property {(request: HandlerRequest) => Promise<HandlerResponse>} me GET /api/auth/me, POST /api/auth/me?action=signout
 */

/**
 * The three auth endpoints as plain async functions (unit-testable without
 * a server or the network).
 *
 * @param {object} [options]
 * @param {AuthEnv} [options.env] Defaults to process.env.
 * @param {typeof fetch} [options.fetchImpl] Defaults to the global fetch.
 * @param {() => number} [options.now] Milliseconds since the epoch.
 * @param {(size: number) => Buffer} [options.randomBytes]
 * @param {(message: string) => void} [options.log] Receives short messages that never contain codes, tokens, cookies or secrets.
 * @returns {AuthHandlers}
 */
export function createAuthHandlers({
  env = process.env,
  fetchImpl,
  now = () => Date.now(),
  randomBytes = nodeRandomBytes,
  log = (message) => console.error(`[auth] ${message}`),
} = {}) {
  const config = readAuthConfig(env);
  const doFetch = fetchImpl ?? ((/** @type {any} */ input, /** @type {any} */ init) => globalThis.fetch(input, init));
  const nowSeconds = () => Math.floor(now() / 1000);

  /** @param {HandlerRequest} request */
  async function login(request) {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return methodNotAllowed(['GET']);
    }
    const returnTo = sanitizeReturnTo(queryValue(request.query, 'returnTo'));
    if (!config) {
      return htmlPage(503, 'Sign-in unavailable', `${NOT_CONFIGURED_MESSAGE}.`, returnTo);
    }
    const origin = requestOrigin(request.headers);
    if (!origin) {
      return htmlPage(400, 'Sign-in unavailable', 'The request did not say which site it is for.', returnTo);
    }
    const state = base64url(randomBytes(STATE_BYTES));
    const cookie = signState({state, returnTo, iat: nowSeconds()}, config.secrets[0]);
    const authorize = new URL(GITHUB_AUTHORIZE_URL);
    authorize.searchParams.set('client_id', config.clientId);
    authorize.searchParams.set('redirect_uri', `${origin}${CALLBACK_PATH}`);
    authorize.searchParams.set('state', state);
    authorize.searchParams.set('allow_signup', 'true');
    // No `scope`: the public profile only.
    const hint = queryValue(request.query, 'login');
    if (hint && LOGIN_PATTERN.test(hint)) {
      authorize.searchParams.set('login', hint);
    }
    if (queryValue(request.query, 'switch') === '1') {
      authorize.searchParams.set('prompt', 'select_account');
    }
    return redirect(authorize.toString(), [stateCookie(cookie)]);
  }

  /** @param {HandlerRequest} request */
  async function callback(request) {
    if (request.method !== 'GET') {
      return methodNotAllowed(['GET']);
    }
    if (!config) {
      return htmlPage(503, 'Sign-in unavailable', `${NOT_CONFIGURED_MESSAGE}.`, '/', [clearStateCookie()]);
    }
    const cookies = parseCookies(readHeader(request.headers, 'cookie'));
    const saved = verifyState(cookies[STATE_COOKIE], config.secrets, nowSeconds());
    const returnTo = saved?.returnTo ?? '/';
    const clear = clearStateCookie();
    /** @param {string} code one of SIGNIN_ERROR_CODES */
    const fail = (code) => redirect(`${returnTo}#signin-error=${code}`, [clear]);

    const githubError = queryValue(request.query, 'error');
    if (githubError) {
      return fail(githubError === 'access_denied' ? 'access_denied' : 'github_error');
    }
    const state = queryValue(request.query, 'state');
    if (!saved || !state || !safeEqual(state, saved.state)) {
      return fail('bad_state');
    }
    const code = queryValue(request.query, 'code');
    if (!code || code.length > 512) {
      return fail('missing_code');
    }
    const origin = requestOrigin(request.headers);
    if (!origin) {
      return fail('server_error');
    }

    // 1. The code → an access token (used once, below, then dropped).
    /** @type {string | null} */
    let accessToken = null;
    try {
      const response = await doFetch(GITHUB_TOKEN_URL, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded',
          'User-Agent': USER_AGENT,
        },
        body: new URLSearchParams({
          client_id: config.clientId,
          client_secret: config.clientSecret,
          code,
          redirect_uri: `${origin}${CALLBACK_PATH}`,
        }).toString(),
        redirect: 'error',
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      const json = response.ok ? await response.json().catch(() => null) : null;
      if (json && typeof json.access_token === 'string' && json.access_token) {
        accessToken = json.access_token;
      } else {
        log(`token exchange failed (HTTP ${response.status}${json && typeof json.error === 'string' ? `, ${String(json.error).slice(0, 40).replace(/[^a-z_]/g, '')}` : ''})`);
      }
    } catch (error) {
      log(`token exchange failed (${error instanceof Error ? error.name : 'error'})`);
    }
    if (!accessToken) {
      return fail('exchange_failed');
    }

    // 2. The token → the public profile.
    /** @type {SessionUser | null} */
    let user = null;
    try {
      const response = await doFetch(GITHUB_USER_URL, {
        headers: {
          Accept: 'application/vnd.github+json',
          Authorization: `Bearer ${accessToken}`,
          'User-Agent': USER_AGENT,
          'X-GitHub-Api-Version': '2022-11-28',
        },
        redirect: 'error',
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      const json = response.ok ? await response.json().catch(() => null) : null;
      if (json && json.type !== 'Organization') {
        user = normalizeSessionUser({
          login: json.login,
          id: json.id,
          name: json.name,
          avatarUrl: json.avatar_url,
          htmlUrl: json.html_url,
        });
      }
      if (!user) {
        log(`profile request failed (HTTP ${response.status})`);
      }
    } catch (error) {
      log(`profile request failed (${error instanceof Error ? error.name : 'error'})`);
    } finally {
      // The token is not needed any more: drop it.
      accessToken = null;
    }
    if (!user) {
      return fail('profile_failed');
    }

    // 3. The session.
    const session = signSession({...user, iat: nowSeconds()}, config.secrets[0]);
    return redirect(`${returnTo}#signed-in`, [sessionCookie(session), clear]);
  }

  /** @param {HandlerRequest} request */
  async function me(request) {
    if (request.method === 'POST') {
      const action = queryValue(request.query, 'action');
      if (action !== 'signout') {
        return jsonResponse(400, {error: 'unknown action'});
      }
      if (!isSameOriginRequest(request.headers)) {
        return jsonResponse(403, {error: 'cross-site request'});
      }
      // Manual sign-out only: nothing else ever clears the session.
      return jsonResponse(200, {signedIn: false, configured: Boolean(config)}, [clearSessionCookie()]);
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return methodNotAllowed(['GET', 'POST']);
    }
    if (!config) {
      return jsonResponse(200, {signedIn: false, configured: false});
    }
    const session = readSession(request, env);
    if (!session) {
      return jsonResponse(200, {signedIn: false, configured: true});
    }
    const {payload, secretIndex} = session;
    const user = toUser(payload);
    /** @type {string[]} */
    const cookies = [];
    // Sliding renewal (and re-signing after a secret rotation): the cookie
    // gets a fresh 400 days, so an active visitor is never signed out.
    if (secretIndex > 0 || nowSeconds() - payload.iat > SESSION_RENEW_AFTER_SECONDS) {
      cookies.push(sessionCookie(signSession({...user, iat: nowSeconds()}, config.secrets[0])));
    }
    return jsonResponse(200, {signedIn: true, configured: true, user}, cookies);
  }

  return {login, callback, me};
}

// ---------------------------------------------------------------------------
// Node.js adapter (Vercel functions and plain node:http servers)

/**
 * Turns a handler into a Node.js `(req, res)` function. Works with Vercel's
 * helpers (req.query) and without them (it parses req.url). Request bodies
 * are never read.
 *
 * @param {(request: HandlerRequest) => Promise<HandlerResponse>} handle
 * @returns {(req: any, res: any) => Promise<void>}
 */
export function createNodeHandler(handle) {
  return async function authNodeHandler(req, res) {
    /** @type {HandlerResponse} */
    let result;
    try {
      const method = String(req.method ?? 'GET').toUpperCase();
      const query =
        req.query && typeof req.query === 'object'
          ? req.query
          : Object.fromEntries(new URL(req.url ?? '/', 'http://localhost').searchParams);
      result = await handle({method, query, headers: req.headers ?? {}});
    } catch (error) {
      console.error(`[auth] unexpected error (${error instanceof Error ? error.name : typeof error})`);
      result = jsonResponse(500, {error: 'internal error'});
    }
    res.statusCode = result.status;
    for (const [name, value] of Object.entries(result.headers)) {
      res.setHeader(name, value);
    }
    res.end(req.method === 'HEAD' ? undefined : result.body);
  };
}

/**
 * Builds the default export of one api/auth/*.mjs function. The handlers are
 * created on the first request, so process.env is read at run time.
 *
 * @param {keyof AuthHandlers} name
 * @returns {(req: any, res: any) => Promise<void>}
 */
export function vercelHandler(name) {
  /** @type {((req: any, res: any) => Promise<void>) | null} */
  let nodeHandler = null;
  return async (req, res) => {
    nodeHandler ??= createNodeHandler(createAuthHandlers({env: process.env})[name]);
    await nodeHandler(req, res);
  };
}
