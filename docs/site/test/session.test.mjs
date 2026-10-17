// Tests for the sign-in functions (api/_lib/session.mjs and api/auth/*.mjs at
// the repository root). No network: GitHub is a scripted fake fetch. Every
// secret and token below is an obviously fake test string.

import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {describe, test} from 'node:test';

import {
  CALLBACK_PATH,
  GITHUB_AUTHORIZE_URL,
  GITHUB_TOKEN_URL,
  GITHUB_USER_URL,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
  SESSION_RENEW_AFTER_SECONDS,
  SIGNIN_ERROR_CODES,
  STATE_COOKIE,
  STATE_MAX_AGE_SECONDS,
  base64url,
  createAuthHandlers,
  createNodeHandler,
  getSessionUser,
  parseCookies,
  readAuthConfig,
  requestOrigin,
  safeEqual,
  sanitizeReturnTo,
  serializeCookie,
  signSession,
  signState,
  verifySession,
  verifySessionDetailed,
  verifyState,
} from '../../../api/_lib/session.mjs';
import loginFunction from '../../../api/auth/login.mjs';
import callbackFunction from '../../../api/auth/callback.mjs';
import meFunction from '../../../api/auth/me.mjs';

const SECRET = 'test-session-secret-0123456789abcdef-not-real';
const OLD_SECRET = 'old-test-session-secret-fedcba9876543210-not-real';
const CLIENT_ID = 'fake-client-id-for-tests';
const CLIENT_SECRET = 'fake-client-secret-for-tests-not-real';
const ACCESS_TOKEN = 'fake-oauth-access-token-for-tests-0001';
const CODE = 'fake-oauth-code-0001';
const HOST = 'docs.example.test';
const ORIGIN = `https://${HOST}`;
const T0 = Date.UTC(2026, 9, 16, 12, 0, 0);

const ENV = Object.freeze({
  GITHUB_OAUTH_CLIENT_ID: CLIENT_ID,
  GITHUB_OAUTH_CLIENT_SECRET: CLIENT_SECRET,
  SESSION_SECRET: SECRET,
});

const USER = Object.freeze({
  login: 'octo-cat',
  id: 583231,
  name: 'The Octocat',
  avatarUrl: 'https://avatars.githubusercontent.com/u/583231?v=4',
  htmlUrl: 'https://github.com/octo-cat',
});

const GITHUB_USER_JSON = Object.freeze({
  login: USER.login,
  id: USER.id,
  name: USER.name,
  avatar_url: USER.avatarUrl,
  html_url: USER.htmlUrl,
  type: 'User',
  email: 'not-copied@example.test',
});

// ---------------------------------------------------------------------------
// Fakes

/** 32 predictable "random" bytes. */
const fakeRandomBytes = (size) => Buffer.alloc(size, 7);
const EXPECTED_STATE = base64url(Buffer.alloc(32, 7));

function jsonAnswer(status, body) {
  return new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});
}

/**
 * A fetch that answers from a script, in order, and records every call.
 * Each step: {match: (url, init) => boolean, answer: () => Response, throws?: Error}.
 */
function scriptedFetch(steps) {
  const calls = [];
  const queue = [...steps];
  const fetchImpl = async (url, init = {}) => {
    calls.push({url: String(url), init});
    const step = queue.shift();
    if (!step) {
      return jsonAnswer(599, {message: `unexpected request ${url}`});
    }
    if (step.throws) {
      throw step.throws;
    }
    return step.answer(String(url), init);
  };
  return {fetchImpl, calls, remaining: () => queue.length};
}

const tokenStep = (answer = () => jsonAnswer(200, {access_token: ACCESS_TOKEN, token_type: 'bearer', scope: ''})) => ({
  answer,
});
const userStep = (answer = () => jsonAnswer(200, GITHUB_USER_JSON)) => ({answer});

function makeHandlers({env = ENV, fetchImpl, now = () => T0, logs = []} = {}) {
  return createAuthHandlers({
    env,
    fetchImpl: fetchImpl ?? (async () => jsonAnswer(599, {message: 'no network in tests'})),
    now,
    randomBytes: fakeRandomBytes,
    log: (message) => logs.push(message),
  });
}

const baseHeaders = (extra = {}) => ({host: HOST, 'x-forwarded-proto': 'https', ...extra});

function setCookies(response) {
  const value = response.headers['set-cookie'];
  return Array.isArray(value) ? value : value ? [value] : [];
}

function cookieValue(setCookie) {
  return setCookie.split(';')[0].split('=').slice(1).join('=');
}

function findCookie(response, name) {
  return setCookies(response).find((cookie) => cookie.startsWith(`${name}=`));
}

/** Runs /api/auth/login and returns the state cookie value and the state. */
async function startLogin(handlers, query = {returnTo: '/architecture/roadmap'}) {
  const response = await handlers.login({method: 'GET', query, headers: baseHeaders()});
  assert.equal(response.status, 302);
  const location = new URL(response.headers.location);
  return {
    response,
    state: location.searchParams.get('state'),
    cookie: cookieValue(findCookie(response, STATE_COOKIE)),
  };
}

function sessionCookieFor(user = USER, {iat = Math.floor(T0 / 1000), secret = SECRET} = {}) {
  return signSession({...user, iat}, secret);
}

// ---------------------------------------------------------------------------
// Session signing

describe('signSession / verifySession', () => {
  const iat = Math.floor(T0 / 1000);

  test('round-trips the public profile with version 1 and iat', () => {
    const value = signSession({...USER, iat}, SECRET);
    assert.match(value, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/);
    assert.deepEqual(verifySession(value, SECRET), {v: 1, ...USER, iat});
    const payload = JSON.parse(Buffer.from(value.split('.')[0], 'base64url').toString('utf8'));
    assert.deepEqual(Object.keys(payload).sort(), ['avatarUrl', 'htmlUrl', 'iat', 'id', 'login', 'name', 'v']);
  });

  test('the signature is HMAC-SHA256(SESSION_SECRET) of the payload part', () => {
    const value = signSession({...USER, iat}, SECRET);
    const [encoded, signature] = value.split('.');
    assert.equal(signature, createHmac('sha256', SECRET).update(encoded).digest('base64url'));
  });

  test('rejects a tampered payload, a tampered signature and another secret', () => {
    const value = signSession({...USER, iat}, SECRET);
    const [encoded, signature] = value.split('.');
    const forged = base64url(JSON.stringify({v: 1, ...USER, login: 'someone-else', iat}));
    assert.equal(verifySession(`${forged}.${signature}`, SECRET), null);
    const flipped = signature.slice(0, -1) + (signature.endsWith('A') ? 'B' : 'A');
    assert.equal(verifySession(`${encoded}.${flipped}`, SECRET), null);
    assert.equal(verifySession(value, OLD_SECRET), null);
  });

  test('rejects malformed values', () => {
    for (const value of [undefined, null, '', 'abc', 'a.b.c', '.', 'a.', '.b', 'no dots here', 42, 'x'.repeat(5000)]) {
      assert.equal(verifySession(value, SECRET), null, String(value));
    }
    // Correctly signed, but not JSON / not an object.
    for (const body of ['not json', '"a string"', 'null', '[1,2]']) {
      const encoded = base64url(body);
      const signature = createHmac('sha256', SECRET).update(encoded).digest('base64url');
      assert.equal(verifySession(`${encoded}.${signature}`, SECRET), null, body);
    }
  });

  test('rejects a session with another version, an invalid login or id, or no iat', () => {
    const sign = (payload) => {
      const encoded = base64url(JSON.stringify(payload));
      return `${encoded}.${createHmac('sha256', SECRET).update(encoded).digest('base64url')}`;
    };
    assert.ok(verifySession(sign({v: 1, ...USER, iat}), SECRET));
    assert.equal(verifySession(sign({v: 2, ...USER, iat}), SECRET), null);
    assert.equal(verifySession(sign({...USER, iat}), SECRET), null);
    assert.equal(verifySession(sign({v: 1, ...USER, login: '-bad-', iat}), SECRET), null);
    assert.equal(verifySession(sign({v: 1, ...USER, id: '583231', iat}), SECRET), null);
    assert.equal(verifySession(sign({v: 1, ...USER}), SECRET), null);
  });

  test('tolerates SESSION_SECRET rotation through SESSION_SECRET_PREVIOUS', () => {
    const old = signSession({...USER, iat}, OLD_SECRET);
    assert.equal(verifySession(old, SECRET), null);
    assert.equal(verifySessionDetailed(old, [SECRET, OLD_SECRET]).secretIndex, 1);
    assert.equal(verifySessionDetailed(signSession({...USER, iat}, SECRET), [SECRET, OLD_SECRET]).secretIndex, 0);
  });

  test('refuses short secrets and invalid users when signing', () => {
    assert.throws(() => signSession({...USER, iat}, 'too-short'), /at least 32/);
    assert.throws(() => signSession({...USER, login: 'not a login', iat}, SECRET), /invalid session user/);
    assert.throws(() => signSession({...USER, iat: 0}, SECRET), /iat/);
    // A short secret never verifies anything either.
    assert.equal(verifySession(signSession({...USER, iat}, SECRET), 'short'), null);
  });

  test('normalizes the profile: name fallback, https URLs only', () => {
    const value = signSession({...USER, name: '  ', avatarUrl: 'http://insecure.test/a.png', htmlUrl: 'javascript:alert(1)', iat}, SECRET);
    const payload = verifySession(value, SECRET);
    assert.equal(payload.name, USER.login);
    assert.equal(payload.avatarUrl, `https://github.com/${USER.login}.png`);
    assert.equal(payload.htmlUrl, `https://github.com/${USER.login}`);
  });

  test('safeEqual compares strings in constant time', () => {
    assert.equal(safeEqual('abc', 'abc'), true);
    assert.equal(safeEqual('abc', 'abd'), false);
    assert.equal(safeEqual('abc', 'abcd'), false);
    assert.equal(safeEqual('abc', undefined), false);
  });
});

// ---------------------------------------------------------------------------
// Cookies

describe('parseCookies / serializeCookie', () => {
  test('parses a Cookie header', () => {
    const cookies = parseCookies('a=1; rid_session=abc.def;  theme = dark ; quoted="x y"; enc=%2Fpath');
    assert.equal(cookies.a, '1');
    assert.equal(cookies.rid_session, 'abc.def');
    assert.equal(cookies.theme, 'dark');
    assert.equal(cookies.quoted, 'x y');
    assert.equal(cookies.enc, '/path');
  });

  test('skips malformed pairs, keeps the first duplicate, has no prototype', () => {
    const cookies = parseCookies('novalue; =x; a=first; a=second; __proto__=polluted; bad%=1; b=%E0%A4%A');
    assert.equal(cookies.a, 'first');
    assert.equal(Object.getPrototypeOf(cookies), null);
    assert.equal(cookies.__proto__, 'polluted');
    assert.equal({}.polluted, undefined);
    assert.equal(cookies.b, '%E0%A4%A');
    assert.equal('novalue' in cookies, false);
  });

  test('accepts missing headers and arrays', () => {
    assert.deepEqual({...parseCookies(undefined)}, {});
    assert.deepEqual({...parseCookies('')}, {});
    assert.deepEqual({...parseCookies(['a=1', 'b=2'])}, {a: '1', b: '2'});
  });

  test('serializes cookies with their attributes', () => {
    assert.equal(
      serializeCookie('rid_session', 'abc.def', {httpOnly: true, secure: true, sameSite: 'Lax', path: '/', maxAge: 60}),
      'rid_session=abc.def; Max-Age=60; Path=/; HttpOnly; Secure; SameSite=Lax',
    );
    assert.equal(
      serializeCookie('x', '', {maxAge: 0, expires: new Date(0), path: '/api/auth'}),
      'x=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/api/auth',
    );
  });

  test('refuses names and values a cookie cannot hold', () => {
    assert.throws(() => serializeCookie('bad name', 'x'), /cookie name/);
    assert.throws(() => serializeCookie('a', 'x;y'), /cookie value/);
    assert.throws(() => serializeCookie('a', 'x y'), /cookie value/);
    assert.throws(() => serializeCookie('a', 'x', {sameSite: 'Sometimes'}), /SameSite/);
    assert.throws(() => serializeCookie('a', 'x', {path: 'relative'}), /path/);
    assert.throws(() => serializeCookie('a', 'x', {maxAge: 1.5}), /maxAge/);
  });
});

// ---------------------------------------------------------------------------
// Return path and origin

describe('sanitizeReturnTo', () => {
  test('keeps same-site paths and queries, drops the fragment', () => {
    assert.equal(sanitizeReturnTo('/'), '/');
    assert.equal(sanitizeReturnTo('/architecture/roadmap'), '/architecture/roadmap');
    assert.equal(sanitizeReturnTo('/blog?tag=a&b=c'), '/blog?tag=a&b=c');
    assert.equal(sanitizeReturnTo('/blog#comments'), '/blog');
    assert.equal(sanitizeReturnTo('/a/../b'), '/b');
    assert.equal(sanitizeReturnTo('/é'), '/%C3%A9');
  });

  test('rejects open redirects and anything that is not a site path', () => {
    for (const value of [
      undefined,
      null,
      '',
      'https://evil.test/',
      'http:/evil.test',
      'javascript:alert(1)',
      '//evil.test',
      '//evil.test/path',
      '/.//evil.test',
      '/..//evil.test',
      '/a/..//evil.test',
      '/%2e%2e//evil.test',
      '/\\evil.test',
      '\\\\evil.test',
      '/\t/evil.test',
      '/\n/evil.test',
      ' /a',
      'relative/path',
      '/a b',
      `/${'a'.repeat(2001)}`,
      '/api/auth/login',
      '/api/auth/callback?code=x',
      '/api',
    ]) {
      assert.equal(sanitizeReturnTo(value), '/', JSON.stringify(value));
    }
  });
});

describe('requestOrigin', () => {
  test('uses the forwarded host, https except for a local http server', () => {
    assert.equal(requestOrigin({host: HOST}), ORIGIN);
    assert.equal(requestOrigin({'x-forwarded-host': 'Docs.Example.Test', host: 'internal'}), ORIGIN);
    assert.equal(requestOrigin({host: HOST, 'x-forwarded-proto': 'http'}), ORIGIN);
    assert.equal(requestOrigin({host: 'localhost:3000', 'x-forwarded-proto': 'http'}), 'http://localhost:3000');
    assert.equal(requestOrigin({host: 'localhost:3000'}), 'http://localhost:3000');
    assert.equal(requestOrigin({host: 'localhost:3000', 'x-forwarded-proto': 'https'}), 'https://localhost:3000');
    assert.equal(requestOrigin(new Headers({host: HOST})), ORIGIN);
  });

  test('rejects missing or malformed hosts', () => {
    assert.equal(requestOrigin({}), null);
    assert.equal(requestOrigin({host: 'evil.test/path'}), null);
    assert.equal(requestOrigin({host: 'user@evil.test'}), null);
  });
});

describe('readAuthConfig', () => {
  test('needs the three variables and a long enough SESSION_SECRET', () => {
    assert.ok(readAuthConfig(ENV));
    assert.equal(readAuthConfig({}), null);
    assert.equal(readAuthConfig(undefined), null);
    assert.equal(readAuthConfig({...ENV, GITHUB_OAUTH_CLIENT_ID: ''}), null);
    assert.equal(readAuthConfig({...ENV, GITHUB_OAUTH_CLIENT_SECRET: undefined}), null);
    assert.equal(readAuthConfig({...ENV, SESSION_SECRET: 'x'.repeat(31)}), null);
    assert.deepEqual(readAuthConfig({...ENV, SESSION_SECRET_PREVIOUS: OLD_SECRET}).secrets, [SECRET, OLD_SECRET]);
  });
});

// ---------------------------------------------------------------------------
// GET /api/auth/login

describe('login', () => {
  test('redirects to GitHub with state, redirect_uri and no scopes', async () => {
    const {response} = await startLogin(makeHandlers());
    const location = new URL(response.headers.location);
    assert.equal(`${location.origin}${location.pathname}`, GITHUB_AUTHORIZE_URL);
    assert.equal(location.searchParams.get('client_id'), CLIENT_ID);
    assert.equal(location.searchParams.get('redirect_uri'), `${ORIGIN}${CALLBACK_PATH}`);
    assert.equal(location.searchParams.get('state'), EXPECTED_STATE);
    assert.equal(location.searchParams.get('allow_signup'), 'true');
    assert.equal(location.searchParams.has('scope'), false);
    assert.equal(location.searchParams.has('prompt'), false);
    assert.equal(response.headers['cache-control'], 'no-store');
    assert.equal(response.headers['access-control-allow-origin'], undefined);
    assert.ok(!response.headers.location.includes(CLIENT_SECRET));
  });

  test('sets a signed, short-lived, HttpOnly state cookie holding state and returnTo', async () => {
    const {response, cookie} = await startLogin(makeHandlers(), {returnTo: '/blog?x=1#frag'});
    const header = findCookie(response, STATE_COOKIE);
    assert.match(header, /; Max-Age=600; Path=\/api\/auth; HttpOnly; Secure; SameSite=Lax$/);
    assert.equal(STATE_MAX_AGE_SECONDS, 600);
    assert.deepEqual(verifyState(cookie, [SECRET], Math.floor(T0 / 1000)), {
      state: EXPECTED_STATE,
      returnTo: '/blog?x=1',
    });
    // Tampering with the cookie (here: the return path) breaks it.
    const parts = cookie.split('.');
    parts[1] = base64url('https://evil.test/');
    assert.equal(verifyState(parts.join('.'), [SECRET], Math.floor(T0 / 1000)), null);
  });

  test('rejects open redirects in returnTo (falls back to /)', async () => {
    for (const returnTo of ['https://evil.test/', '//evil.test', '/.//evil.test', '/\\evil.test', 'javascript:alert(1)']) {
      const {cookie} = await startLogin(makeHandlers(), {returnTo});
      assert.equal(verifyState(cookie, [SECRET], Math.floor(T0 / 1000)).returnTo, '/', returnTo);
    }
    const {cookie} = await startLogin(makeHandlers(), {});
    assert.equal(verifyState(cookie, [SECRET], Math.floor(T0 / 1000)).returnTo, '/');
  });

  test('switch=1 asks for the account picker; login suggests a valid account', async () => {
    const handlers = makeHandlers();
    let location = new URL((await startLogin(handlers, {returnTo: '/', switch: '1'})).response.headers.location);
    assert.equal(location.searchParams.get('prompt'), 'select_account');
    location = new URL((await startLogin(handlers, {returnTo: '/', login: 'other-user'})).response.headers.location);
    assert.equal(location.searchParams.get('login'), 'other-user');
    location = new URL((await startLogin(handlers, {returnTo: '/', login: 'bad login&x=1'})).response.headers.location);
    assert.equal(location.searchParams.has('login'), false);
  });

  test('picking a saved account sends both the picker and the account hint', async () => {
    const handlers = makeHandlers();
    const location = new URL(
      (await startLogin(handlers, {returnTo: '/x', switch: '1', login: 'b'})).response.headers.location,
    );
    assert.equal(location.searchParams.get('prompt'), 'select_account');
    assert.equal(location.searchParams.get('login'), 'b');
  });

  test('answers 503 "not configured" without the environment variables', async () => {
    for (const env of [{}, {...ENV, SESSION_SECRET: 'short'}, {...ENV, GITHUB_OAUTH_CLIENT_SECRET: ''}]) {
      const response = await makeHandlers({env}).login({method: 'GET', query: {returnTo: '/blog'}, headers: baseHeaders()});
      assert.equal(response.status, 503);
      assert.match(response.headers['content-type'], /^text\/html/);
      assert.match(response.body, /Sign-in is not configured on this deployment/);
      assert.match(response.body, /href="\/blog"/);
      assert.equal(findCookie(response, STATE_COOKIE), undefined);
    }
  });

  test('the "Back to the site" link never becomes a protocol-relative URL', async () => {
    for (const returnTo of ['/.//evil.test', '/..//evil.test', '/a/..//evil.test', '/%2e%2e//evil.test']) {
      const unconfigured = await makeHandlers({env: {}}).login({method: 'GET', query: {returnTo}, headers: baseHeaders()});
      assert.equal(unconfigured.status, 503);
      assert.match(unconfigured.body, /href="\/"/, returnTo);
      assert.doesNotMatch(unconfigured.body, /evil/, returnTo);
      const noHost = await makeHandlers().login({method: 'GET', query: {returnTo}, headers: {}});
      assert.equal(noHost.status, 400);
      assert.match(noHost.body, /href="\/"/, returnTo);
      assert.doesNotMatch(noHost.body, /evil/, returnTo);
    }
  });

  test('refuses other methods and requests without a host', async () => {
    const handlers = makeHandlers();
    const post = await handlers.login({method: 'POST', query: {}, headers: baseHeaders()});
    assert.equal(post.status, 405);
    assert.equal(post.headers.allow, 'GET');
    const noHost = await handlers.login({method: 'GET', query: {}, headers: {}});
    assert.equal(noHost.status, 400);
  });
});

// ---------------------------------------------------------------------------
// GET /api/auth/callback

describe('callback', () => {
  async function runCallback({steps = [tokenStep(), userStep()], query, cookie, now, env, returnTo} = {}) {
    const logs = [];
    const fake = scriptedFetch(steps);
    const handlers = makeHandlers({fetchImpl: fake.fetchImpl, now, env, logs});
    const started = await startLogin(makeHandlers(), returnTo ? {returnTo} : undefined);
    const response = await handlers.callback({
      method: 'GET',
      query: query ?? {code: CODE, state: started.state},
      headers: baseHeaders({cookie: `other=1; ${STATE_COOKIE}=${cookie ?? started.cookie}`}),
    });
    return {response, calls: fake.calls, logs, remaining: fake.remaining()};
  }

  function errorCode(response) {
    assert.equal(response.status, 302);
    const match = /#signin-error=([a-z_]+)$/.exec(response.headers.location);
    assert.ok(match, response.headers.location);
    assert.ok(SIGNIN_ERROR_CODES.includes(match[1]), match[1]);
    return match[1];
  }

  function assertStateCleared(response) {
    const cleared = findCookie(response, STATE_COOKIE);
    assert.match(cleared, /^rid_oauth_state=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=\/api\/auth/);
  }

  test('exchanges the code, reads /user, sets the session and returns with #signed-in', async () => {
    const {response, calls, logs} = await runCallback();
    assert.equal(response.status, 302);
    assert.equal(response.headers.location, '/architecture/roadmap#signed-in');
    assert.equal(response.headers['cache-control'], 'no-store');
    assert.equal(response.headers['referrer-policy'], 'no-referrer');

    // Token exchange.
    assert.equal(calls.length, 2);
    assert.equal(calls[0].url, GITHUB_TOKEN_URL);
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.headers.Accept, 'application/json');
    const form = new URLSearchParams(calls[0].init.body);
    assert.deepEqual(Object.fromEntries(form), {
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code: CODE,
      redirect_uri: `${ORIGIN}${CALLBACK_PATH}`,
    });
    // Profile.
    assert.equal(calls[1].url, GITHUB_USER_URL);
    assert.equal(calls[1].init.headers.Authorization, `Bearer ${ACCESS_TOKEN}`);
    assert.ok(calls[1].init.headers['User-Agent']);

    // Session cookie.
    const header = findCookie(response, SESSION_COOKIE);
    assert.match(header, /; Max-Age=34560000; Path=\/; HttpOnly; Secure; SameSite=Lax$/);
    assert.equal(SESSION_MAX_AGE_SECONDS, 34560000);
    const payload = verifySession(cookieValue(header), SECRET);
    assert.deepEqual(payload, {v: 1, ...USER, iat: Math.floor(T0 / 1000)});
    assertStateCleared(response);
    assert.deepEqual(logs, []);
  });

  test('never stores or returns the GitHub token (or the code, or secrets)', async () => {
    const {response, logs} = await runCallback();
    const everything = JSON.stringify(response);
    const sessionPayload = Buffer.from(cookieValue(findCookie(response, SESSION_COOKIE)).split('.')[0], 'base64url').toString();
    for (const secret of [ACCESS_TOKEN, CODE, CLIENT_SECRET, SECRET]) {
      assert.ok(!everything.includes(secret), `response contains ${secret}`);
      assert.ok(!sessionPayload.includes(secret), `session contains ${secret}`);
    }
    assert.ok(!sessionPayload.includes('not-copied@example.test'), 'the e-mail is not kept');
    assert.deepEqual(logs, []);
  });

  test('state mismatch → bad_state, without calling GitHub', async () => {
    const {response, calls} = await runCallback({query: {code: CODE, state: 'A'.repeat(43)}});
    assert.equal(errorCode(response), 'bad_state');
    assert.equal(response.headers.location, '/architecture/roadmap#signin-error=bad_state');
    assert.equal(calls.length, 0);
    assert.equal(findCookie(response, SESSION_COOKIE), undefined);
    assertStateCleared(response);
  });

  test('missing, tampered or expired state cookie → bad_state, back to /', async () => {
    for (const cookie of ['', 'garbage', `${EXPECTED_STATE}.${base64url('/x')}.${Math.floor(T0 / 1000)}.forged`]) {
      const {response, calls} = await runCallback({query: {code: CODE, state: EXPECTED_STATE}, cookie});
      assert.equal(errorCode(response), 'bad_state');
      assert.equal(response.headers.location, '/#signin-error=bad_state');
      assert.equal(calls.length, 0);
    }
    const later = () => T0 + (STATE_MAX_AGE_SECONDS + 1) * 1000;
    const {response} = await runCallback({now: later});
    assert.equal(errorCode(response), 'bad_state');
    // A state cookie signed with another secret does not verify.
    const foreign = signState({state: EXPECTED_STATE, returnTo: '/', iat: Math.floor(T0 / 1000)}, OLD_SECRET);
    assert.equal(errorCode((await runCallback({query: {code: CODE, state: EXPECTED_STATE}, cookie: foreign})).response), 'bad_state');
  });

  test('the visitor declined on GitHub → access_denied; other GitHub errors → github_error', async () => {
    let result = await runCallback({query: {error: 'access_denied', error_description: 'The user has denied', state: EXPECTED_STATE}});
    assert.equal(errorCode(result.response), 'access_denied');
    assert.equal(result.response.headers.location, '/architecture/roadmap#signin-error=access_denied');
    assert.equal(result.calls.length, 0);
    result = await runCallback({query: {error: 'redirect_uri_mismatch<script>', state: EXPECTED_STATE}});
    assert.equal(errorCode(result.response), 'github_error');
    assert.ok(!result.response.headers.location.includes('script'));
  });

  test('no code → missing_code', async () => {
    const {response, calls} = await runCallback({query: {state: EXPECTED_STATE}});
    assert.equal(errorCode(response), 'missing_code');
    assert.equal(calls.length, 0);
  });

  test('token exchange failures → exchange_failed (no session, nothing secret logged)', async () => {
    const failures = [
      tokenStep(() => jsonAnswer(200, {error: 'bad_verification_code', error_description: 'The code passed is incorrect or expired.'})),
      tokenStep(() => jsonAnswer(500, {message: 'boom'})),
      tokenStep(() => new Response('<html>', {status: 200, headers: {'content-type': 'text/html'}})),
      {throws: new TypeError('fetch failed')},
    ];
    for (const step of failures) {
      const {response, calls, logs} = await runCallback({steps: [step]});
      assert.equal(errorCode(response), 'exchange_failed');
      assert.equal(calls.length, 1);
      assert.equal(findCookie(response, SESSION_COOKIE), undefined);
      assertStateCleared(response);
      assert.equal(logs.length, 1);
      for (const secret of [CODE, CLIENT_SECRET, SECRET]) {
        assert.ok(!logs[0].includes(secret));
      }
    }
  });

  test('profile failures → profile_failed (the token is not logged)', async () => {
    const failures = [
      userStep(() => jsonAnswer(401, {message: 'Bad credentials'})),
      userStep(() => jsonAnswer(200, {...GITHUB_USER_JSON, login: 'not a login'})),
      userStep(() => jsonAnswer(200, {...GITHUB_USER_JSON, id: 'x'})),
      userStep(() => jsonAnswer(200, {...GITHUB_USER_JSON, type: 'Organization'})),
      {throws: new Error('socket hang up')},
    ];
    for (const step of failures) {
      const {response, calls, logs} = await runCallback({steps: [tokenStep(), step]});
      assert.equal(errorCode(response), 'profile_failed');
      assert.equal(calls.length, 2);
      assert.equal(findCookie(response, SESSION_COOKIE), undefined);
      assert.ok(logs.every((line) => !line.includes(ACCESS_TOKEN)));
    }
  });

  test('every error code comes from the closed list', () => {
    assert.deepEqual([...SIGNIN_ERROR_CODES].sort(), [
      'access_denied',
      'bad_state',
      'exchange_failed',
      'github_error',
      'missing_code',
      'profile_failed',
      'server_error',
    ]);
    assert.ok(Object.isFrozen(SIGNIN_ERROR_CODES));
  });

  test('not configured → 503; other methods → 405', async () => {
    const response = await makeHandlers({env: {}}).callback({method: 'GET', query: {code: CODE, state: 'x'}, headers: baseHeaders()});
    assert.equal(response.status, 503);
    assert.match(response.body, /Sign-in is not configured on this deployment/);
    assert.equal((await makeHandlers().callback({method: 'POST', query: {}, headers: baseHeaders()})).status, 405);
  });
});

// ---------------------------------------------------------------------------
// /api/auth/me

describe('me', () => {
  const iat = Math.floor(T0 / 1000);
  const get = (handlers, cookie) =>
    handlers.me({method: 'GET', query: {}, headers: baseHeaders(cookie ? {cookie} : {})});

  test('signed in: the user from the verified cookie, no renewal when fresh', async () => {
    const response = await get(makeHandlers(), `${SESSION_COOKIE}=${sessionCookieFor()}`);
    assert.equal(response.status, 200);
    assert.equal(response.headers['cache-control'], 'no-store');
    assert.match(response.headers['content-type'], /^application\/json/);
    assert.deepEqual(JSON.parse(response.body), {signedIn: true, configured: true, user: {...USER}});
    assert.equal(findCookie(response, SESSION_COOKIE), undefined);
  });

  test('signed out: no cookie, or a cookie that does not verify (left alone)', async () => {
    const handlers = makeHandlers();
    for (const cookie of [undefined, `${SESSION_COOKIE}=garbage`, `${SESSION_COOKIE}=${sessionCookieFor(USER, {secret: OLD_SECRET})}`]) {
      const response = await get(handlers, cookie);
      assert.deepEqual(JSON.parse(response.body), {signedIn: false, configured: true});
      assert.equal(response.headers['set-cookie'], undefined, 'nothing signs a visitor out automatically');
    }
  });

  test('not configured: {signedIn: false, configured: false}', async () => {
    const response = await get(makeHandlers({env: {}}), `${SESSION_COOKIE}=${sessionCookieFor()}`);
    assert.deepEqual(JSON.parse(response.body), {signedIn: false, configured: false});
  });

  test('sliding renewal: a session older than 30 days gets a fresh cookie', async () => {
    const issued = iat - SESSION_RENEW_AFTER_SECONDS - 1;
    const response = await get(makeHandlers(), `${SESSION_COOKIE}=${sessionCookieFor(USER, {iat: issued})}`);
    assert.equal(JSON.parse(response.body).signedIn, true);
    const header = findCookie(response, SESSION_COOKIE);
    assert.match(header, /; Max-Age=34560000; Path=\/; HttpOnly; Secure; SameSite=Lax$/);
    assert.equal(verifySession(cookieValue(header), SECRET).iat, iat);

    // Exactly 30 days old: not yet.
    const notYet = await get(makeHandlers(), `${SESSION_COOKIE}=${sessionCookieFor(USER, {iat: iat - SESSION_RENEW_AFTER_SECONDS})}`);
    assert.equal(findCookie(notYet, SESSION_COOKIE), undefined);

    // A year old (still within the cookie's 400 days): still signed in, renewed.
    const yearOld = await get(makeHandlers(), `${SESSION_COOKIE}=${sessionCookieFor(USER, {iat: iat - 365 * 86400})}`);
    assert.equal(JSON.parse(yearOld.body).signedIn, true);
    assert.ok(findCookie(yearOld, SESSION_COOKIE));
  });

  test('after a secret rotation the session is re-signed with the new secret', async () => {
    const env = {...ENV, SESSION_SECRET_PREVIOUS: OLD_SECRET};
    const response = await get(makeHandlers({env}), `${SESSION_COOKIE}=${sessionCookieFor(USER, {secret: OLD_SECRET})}`);
    assert.equal(JSON.parse(response.body).signedIn, true);
    const renewed = cookieValue(findCookie(response, SESSION_COOKIE));
    assert.ok(verifySession(renewed, SECRET));
    assert.equal(verifySession(renewed, OLD_SECRET), null);
  });

  test('POST ?action=signout clears the session cookie', async () => {
    const response = await makeHandlers().me({
      method: 'POST',
      query: {action: 'signout'},
      headers: baseHeaders({cookie: `${SESSION_COOKIE}=${sessionCookieFor()}`, origin: ORIGIN, 'sec-fetch-site': 'same-origin'}),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(response.body), {signedIn: false, configured: true});
    assert.match(findCookie(response, SESSION_COOKIE), /^rid_session=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=\/; HttpOnly; Secure; SameSite=Lax$/);
  });

  test('sign-out refuses cross-site requests and unknown actions', async () => {
    const handlers = makeHandlers();
    for (const headers of [{origin: 'https://evil.test'}, {'sec-fetch-site': 'cross-site'}, {'sec-fetch-site': 'same-site'}, {origin: 'null'}]) {
      const response = await handlers.me({method: 'POST', query: {action: 'signout'}, headers: baseHeaders(headers)});
      assert.equal(response.status, 403, JSON.stringify(headers));
      assert.equal(response.headers['set-cookie'], undefined);
    }
    assert.equal((await handlers.me({method: 'POST', query: {action: 'delete'}, headers: baseHeaders()})).status, 400);
    const put = await handlers.me({method: 'PUT', query: {}, headers: baseHeaders()});
    assert.equal(put.status, 405);
    assert.equal(put.headers.allow, 'GET, POST');
  });
});

// ---------------------------------------------------------------------------
// getSessionUser (for the community API)

describe('getSessionUser', () => {
  test('returns the user of a Node.js request or a Fetch Request', () => {
    const cookie = `theme=dark; ${SESSION_COOKIE}=${sessionCookieFor()}`;
    assert.deepEqual(getSessionUser({headers: {cookie}}, ENV), {...USER});
    assert.deepEqual(getSessionUser(new Request('https://docs.example.test/api/community', {headers: {cookie}}), ENV), {...USER});
  });

  test('returns null for guests, bad cookies and unconfigured deployments', () => {
    assert.equal(getSessionUser({headers: {}}, ENV), null);
    assert.equal(getSessionUser(null, ENV), null);
    assert.equal(getSessionUser({headers: {cookie: `${SESSION_COOKIE}=nope`}}, ENV), null);
    assert.equal(getSessionUser({headers: {cookie: `${SESSION_COOKIE}=${sessionCookieFor()}`}}, {}), null);
    assert.equal(
      getSessionUser({headers: {cookie: `${SESSION_COOKIE}=${sessionCookieFor()}`}}, {...ENV, GITHUB_OAUTH_CLIENT_ID: ''}),
      null,
    );
  });

  test('accepts the previous secret during a rotation', () => {
    const cookie = `${SESSION_COOKIE}=${sessionCookieFor(USER, {secret: OLD_SECRET})}`;
    assert.equal(getSessionUser({headers: {cookie}}, ENV), null);
    assert.deepEqual(getSessionUser({headers: {cookie}}, {...ENV, SESSION_SECRET_PREVIOUS: OLD_SECRET}), {...USER});
  });
});

// ---------------------------------------------------------------------------
// Node.js adapter and the function files

class FakeResponse {
  constructor() {
    this.statusCode = 200;
    this.headers = {};
    this.body = undefined;
  }
  setHeader(name, value) {
    this.headers[name.toLowerCase()] = value;
  }
  end(body) {
    this.body = body;
  }
}

describe('Node.js adapter', () => {
  test('writes status, headers (several Set-Cookie) and body', async () => {
    const handler = createNodeHandler(makeHandlers().login);
    const res = new FakeResponse();
    await handler({method: 'GET', url: '/api/auth/login?returnTo=%2Fblog', headers: baseHeaders()}, res);
    assert.equal(res.statusCode, 302);
    assert.match(res.headers.location, /^https:\/\/github\.com\/login\/oauth\/authorize\?/);
    assert.ok(Array.isArray(res.headers['set-cookie']));
    const cookie = cookieValue(res.headers['set-cookie'][0]);
    assert.equal(verifyState(cookie, [SECRET], Math.floor(T0 / 1000)).returnTo, '/blog');
  });

  test('uses Vercel req.query when present and answers 500 on a crash', async () => {
    const meHandler = createNodeHandler(makeHandlers().me);
    const res = new FakeResponse();
    await meHandler({method: 'GET', query: {}, url: '/ignored', headers: baseHeaders()}, res);
    assert.deepEqual(JSON.parse(res.body), {signedIn: false, configured: true});

    const original = console.error;
    const errors = [];
    console.error = (message) => errors.push(message);
    try {
      const crashing = createNodeHandler(async () => {
        throw new Error(`secret ${SECRET}`);
      });
      const crashed = new FakeResponse();
      await crashing({method: 'GET', url: '/', headers: {}}, crashed);
      assert.equal(crashed.statusCode, 500);
      assert.ok(errors.every((line) => !String(line).includes(SECRET)));
    } finally {
      console.error = original;
    }
  });

  test('api/auth/{login,callback,me}.mjs export Vercel handlers', async () => {
    for (const fn of [loginFunction, callbackFunction, meFunction]) {
      assert.equal(typeof fn, 'function');
    }
    // Without the environment variables, me answers "not configured" (no network).
    const saved = {...process.env};
    for (const key of ['GITHUB_OAUTH_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_SECRET', 'SESSION_SECRET']) {
      delete process.env[key];
    }
    try {
      const res = new FakeResponse();
      await meFunction({method: 'GET', url: '/api/auth/me', headers: baseHeaders()}, res);
      assert.deepEqual(JSON.parse(res.body), {signedIn: false, configured: false});
    } finally {
      Object.assign(process.env, saved);
    }
  });
});
