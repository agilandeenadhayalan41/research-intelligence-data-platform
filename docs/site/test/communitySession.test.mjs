// End-to-end test of GitHub sign-in wired into the shared likes and comments:
// a real node:http server on 127.0.0.1 mounts the default exports of
// api/auth/login.mjs, api/auth/callback.mjs, api/auth/me.mjs and
// api/community.mjs, as Vercel would. GitHub is the in-memory fake (its OAuth
// token endpoint is scripted here): the global fetch is replaced so that
// github.com and api.github.com go to the fakes and anything else that is not
// this local server throws. No request leaves the machine. Every secret and
// token below is an obviously fake test string.
//
// Covered: a GitHub sign-in that ends with the rid_session cookie, then a like
// and a comment carried by that cookie alone (no token: the author is the
// verified GitHub user), the browser-side shared store doing the same, a guest
// write, cross-site cookie writes and guest-only writes that still carry the
// cookie refused (and nothing written), and Sign out.

import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {after, before, describe, test} from 'node:test';

import {CROSS_SITE_ERROR} from '../../../api/_lib/community.mjs';
import {SESSION_COOKIE, STATE_COOKIE} from '../../../api/_lib/session.mjs';
import {createMemoryStore, createSharedStore} from '../src/lib/storage.mjs';
import {DEFAULT_USERS, SERVER_TOKEN, createFakeGitHub} from './helpers/fakeGitHub.mjs';

const BRANCH = 'community-data';
const PAGE = 'docs/overview/introduction.md';
const API_PAGE = `/${PAGE}`;
const FILE = `community/pages/${createHash('sha256').update(API_PAGE).digest('hex').slice(0, 32)}.json`;
const GUEST_A = '0f8a5a4e-1b2c-4d3e-8f90-a1b2c3d4e5f6';
const GUEST_B = '7c6b5a49-3827-4165-9443-322110fedcba';

const SESSION_SECRET = 'test-session-secret-0123456789abcdef-not-real';
const CLIENT_ID = 'fake-client-id-for-tests';
const CLIENT_SECRET = 'fake-client-secret-for-tests-not-real';
const OAUTH_CODE = 'fake-oauth-code-carol';
// What GitHub's token endpoint hands the callback; the fake /user knows it.
const OAUTH_TOKEN = 'fake-oauth-access-token-carol';
const CAROL = Object.freeze({login: 'carol', id: 3003, name: 'Carol Example', avatar_url: 'https://avatars.example.test/u/3'});

const realFetch = globalThis.fetch;
const github = createFakeGitHub({users: {...DEFAULT_USERS, [OAUTH_TOKEN]: CAROL}});
/** Bodies posted to GitHub's OAuth token endpoint (the callback's code exchange). */
const tokenExchanges = [];
let server;
let origin = '';
let host = '';
// The rate limits count writes per client IP (20 per 10 minutes): each test
// writes from its own address, as Vercel would report it in X-Real-IP.
let clientIp = '198.51.100.1';

/** fetch against the local server, never following redirects. */
const local = (path, init = {}) => realFetch(`${origin}${path}`, {redirect: 'manual', ...init});

/** name=value pairs of the response's Set-Cookie headers. */
function setCookies(response) {
  return Object.fromEntries(
    response.headers.getSetCookie().map((cookie) => {
      const [pair] = cookie.split(';');
      const index = pair.indexOf('=');
      return [pair.slice(0, index), {value: pair.slice(index + 1), raw: cookie}];
    }),
  );
}

/**
 * A POST to /api/community as the site's own page would make it (JSON, same
 * origin), with the given cookie and guest id; `headers` overrides any of it.
 */
async function write(body, {cookie, guestId, headers = {}} = {}) {
  /** @type {Record<string, string>} */
  const all = {'content-type': 'application/json', origin, 'sec-fetch-site': 'same-origin', 'x-real-ip': clientIp};
  if (cookie) {
    all.cookie = cookie;
  }
  if (guestId) {
    all['x-guest-id'] = guestId;
  }
  Object.assign(all, headers);
  for (const [name, value] of Object.entries(all)) {
    if (value === undefined) {
      delete all[name];
    }
  }
  const response = await local('/api/community', {method: 'POST', headers: all, body: JSON.stringify(body)});
  return {status: response.status, json: await response.json()};
}

async function read({cookie, guestId} = {}) {
  /** @type {Record<string, string>} */
  const headers = {'x-real-ip': clientIp};
  if (cookie) {
    headers.cookie = cookie;
  }
  if (guestId) {
    headers['x-guest-id'] = guestId;
  }
  const response = await local(`/api/community?page=${encodeURIComponent(API_PAGE)}`, {headers});
  return {status: response.status, json: await response.json()};
}

/** GitHub calls made with a visitor's token (the callback's profile read, a Bearer write). */
const visitorCalls = () => github.requests.filter((request) => request.auth.startsWith('visitor:'));

let sessionCookie = '';

before(async () => {
  process.env.COMMUNITY_GITHUB_TOKEN = SERVER_TOKEN;
  process.env.GITHUB_OAUTH_CLIENT_ID = CLIENT_ID;
  process.env.GITHUB_OAUTH_CLIENT_SECRET = CLIENT_SECRET;
  process.env.SESSION_SECRET = SESSION_SECRET;
  for (const name of ['COMMUNITY_REPO', 'COMMUNITY_DATA_BRANCH', 'COMMUNITY_DATA_DIR', 'SESSION_SECRET_PREVIOUS']) {
    delete process.env[name];
  }
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url));
    if (target.origin === 'https://api.github.com') {
      return github.fetch(String(url), init);
    }
    if (target.href === 'https://github.com/login/oauth/access_token') {
      const form = new URLSearchParams(String(init?.body ?? ''));
      tokenExchanges.push(Object.fromEntries(form));
      const ok = form.get('code') === OAUTH_CODE && form.get('client_secret') === CLIENT_SECRET;
      return Response.json(ok ? {access_token: OAUTH_TOKEN, token_type: 'bearer', scope: ''} : {error: 'bad_verification_code'});
    }
    if (target.origin === origin) {
      return realFetch(url, init);
    }
    throw new Error(`session test: unexpected request to ${target.origin}`);
  };
  const routes = {
    '/api/community': (await import('../../../api/community.mjs')).default,
    '/api/auth/login': (await import('../../../api/auth/login.mjs')).default,
    '/api/auth/callback': (await import('../../../api/auth/callback.mjs')).default,
    '/api/auth/me': (await import('../../../api/auth/me.mjs')).default,
  };
  server = createServer((req, res) => {
    const route = routes[new URL(req.url, 'http://localhost').pathname];
    if (!route) {
      res.statusCode = 404;
      res.end('not found');
      return;
    }
    route(req, res).catch(() => {
      res.statusCode = 500;
      res.end();
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  host = `127.0.0.1:${server.address().port}`;
  origin = `http://${host}`;
});

after(async () => {
  globalThis.fetch = realFetch;
  for (const name of ['COMMUNITY_GITHUB_TOKEN', 'GITHUB_OAUTH_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_SECRET', 'SESSION_SECRET']) {
    delete process.env[name];
  }
  await new Promise((resolve) => server.close(resolve));
});

describe('GitHub sign-in and the shared likes and comments, over HTTP', () => {
  test('sign in with GitHub: the callback sets the rid_session cookie', async () => {
    clientIp = '198.51.100.1';
    const login = await local(`/api/auth/login?returnTo=${encodeURIComponent('/overview/introduction')}`);
    assert.equal(login.status, 302);
    const authorize = new URL(login.headers.get('location'));
    assert.equal(`${authorize.origin}${authorize.pathname}`, 'https://github.com/login/oauth/authorize');
    assert.equal(authorize.searchParams.get('client_id'), CLIENT_ID);
    assert.equal(authorize.searchParams.get('redirect_uri'), `${origin}/api/auth/callback`);
    assert.equal(authorize.searchParams.get('scope'), null, 'public profile only');
    const state = authorize.searchParams.get('state');
    const stateCookie = setCookies(login)[STATE_COOKIE];
    assert.ok(state && stateCookie, 'a state and its signed cookie');

    // GitHub sends the browser back with a code and the same state.
    const callback = await local(`/api/auth/callback?code=${OAUTH_CODE}&state=${encodeURIComponent(state)}`, {
      headers: {cookie: `${STATE_COOKIE}=${stateCookie.value}`},
    });
    assert.equal(callback.status, 302);
    assert.equal(callback.headers.get('location'), '/overview/introduction#signed-in');
    const session = setCookies(callback)[SESSION_COOKIE];
    assert.ok(session, 'the session cookie is set');
    assert.match(session.raw, /HttpOnly/i);
    assert.match(session.raw, /SameSite=Lax/i);
    assert.match(session.raw, /Path=\//);
    assert.equal(tokenExchanges.length, 1);
    assert.equal(tokenExchanges[0].code, OAUTH_CODE);
    assert.deepEqual(
      visitorCalls().map((request) => `${request.method} ${request.path} ${request.auth}`),
      ['GET /user visitor:carol'],
      'the callback reads the profile once with the OAuth token (then drops it)',
    );
    assert.ok(!session.raw.includes(OAUTH_TOKEN), 'the OAuth token is never put in the cookie');
    sessionCookie = `${SESSION_COOKIE}=${session.value}`;

    const me = await local('/api/auth/me', {headers: {cookie: sessionCookie}});
    const meJson = await me.json();
    assert.equal(meJson.signedIn, true);
    assert.equal(meJson.user.login, 'carol');
  });

  test('a like and a comment carried by the session cookie alone: the verified GitHub user, no token', async () => {
    clientIp = '198.51.100.2';
    assert.ok(sessionCookie, 'signed in by the previous test');
    const before = github.requests.length;

    // The site names the session's user in every session write (login).
    const liked = await write({action: 'like', page: API_PAGE, login: 'carol'}, {cookie: sessionCookie, guestId: GUEST_A});
    assert.equal(liked.status, 200, JSON.stringify(liked.json));
    assert.deepEqual(liked.json.likes, {count: 1, likedBy: ['carol'], liked: true});

    const commented = await write(
      {action: 'comment', page: API_PAGE, body: 'Signed in with GitHub, no token.', login: 'carol'},
      {cookie: sessionCookie, guestId: GUEST_A},
    );
    assert.equal(commented.status, 200, JSON.stringify(commented.json));
    assert.deepEqual(commented.json.comment.author, {
      login: 'carol',
      name: 'Carol Example',
      avatarUrl: 'https://avatars.example.test/u/3',
    });
    assert.equal(commented.json.comment.canDelete, true);

    // The author was taken from the session, not from GitHub: no visitor token
    // reached GitHub, only the server token wrote.
    const since = github.requests.slice(before);
    assert.ok(since.length > 0);
    assert.ok(since.every((request) => request.auth === 'server'), JSON.stringify(since.map((r) => r.auth)));

    // What was committed names carol (and keeps no guest key for her).
    const saved = github.readJson(BRANCH, FILE);
    assert.deepEqual(
      saved.likes.map(({login, userId, guestKey}) => ({login, userId, guestKey})),
      [{login: 'carol', userId: 3003, guestKey: undefined}],
    );
    assert.equal(saved.comments[0].login, 'carol');
    assert.equal(saved.comments[0].userId, 3003, "the GitHub id from the session, so a renamed login cannot take over carol's");
    assert.equal(saved.comments[0].name, 'Carol Example');
    assert.equal(saved.comments[0].guestKey, undefined);

    // Reads with the cookie see carol's like and comment as hers; without it they are not.
    const asCarol = await read({cookie: sessionCookie});
    assert.equal(asCarol.json.likes.liked, true);
    assert.equal(asCarol.json.comments[0].canDelete, true);
    const asGuest = await read({guestId: GUEST_B});
    assert.equal(asGuest.json.likes.liked, false);
    assert.equal(asGuest.json.comments[0].canDelete, false);
  });

  test("a session write that names someone else is refused (the browser's view is stale)", async () => {
    clientIp = '198.51.100.3';
    const refused = await write(
      {action: 'comment', page: API_PAGE, body: 'Who am I?', login: 'somebody-else'},
      {cookie: sessionCookie, guestId: GUEST_A},
    );
    assert.equal(refused.status, 403);
    assert.equal(refused.json.error, 'you are signed in as @carol');
  });

  test('a guest write (no login) that still carries a live cookie is refused, not published as the GitHub user', async () => {
    clientIp = '198.51.100.9';
    const commits = github.history(BRANCH).length;
    for (const body of [
      {action: 'like', page: API_PAGE},
      {action: 'comment', page: API_PAGE, body: 'Meant to be anonymous.'},
    ]) {
      const refused = await write(body, {cookie: sessionCookie, guestId: GUEST_B});
      assert.equal(refused.status, 403, body.action);
      assert.equal(refused.json.error, 'you are signed in as @carol');
    }
    assert.equal(github.history(BRANCH).length, commits, 'no commit');
    assert.equal(github.readJson(BRANCH, FILE).comments.some((item) => item.body === 'Meant to be anonymous.'), false);
  });

  test("the site's shared store, signed in: likes and comments as the session's user", async () => {
    clientIp = '198.51.100.4';
    const browser = createSharedStore({
      // A browser adds the same-origin cookie by itself (credentials: 'same-origin').
      fetchImpl: (url, init) => {
        assert.equal(init.credentials, 'same-origin');
        return local(url, {...init, headers: {...init.headers, cookie: sessionCookie, origin, 'x-real-ip': clientIp}});
      },
      getSessionLogin: () => 'carol',
      // A saved token is not used while signed in with GitHub.
      getToken: () => 'fake-token-that-must-not-be-sent',
      getGuestId: () => GUEST_A,
      fallback: createMemoryStore(),
    });
    const before = visitorCalls().length;
    assert.equal(await browser.backend(), 'shared');
    assert.deepEqual(await browser.getLikes(PAGE), {count: 1, likedBy: ['carol'], liked: true});
    // carol already likes the page: the toggle removes her like.
    assert.deepEqual(await browser.toggleLike(PAGE, 'carol'), {count: 0, likedBy: [], liked: false});
    assert.deepEqual(await browser.toggleLike(PAGE, 'carol'), {count: 1, likedBy: ['carol'], liked: true});
    const comment = await browser.addComment(PAGE, {
      author: {login: 'carol', name: 'Carol Example', avatarUrl: null},
      body: 'From the store.',
    });
    assert.equal(comment.author.login, 'carol');
    assert.equal(comment.canDelete, true);
    assert.equal(await browser.deleteComment(PAGE, comment.id, 'carol'), true);
    assert.equal(visitorCalls().length, before, 'no token went to GitHub');
  });

  test('a guest write still works next to the session', async () => {
    clientIp = '198.51.100.5';
    const liked = await write({action: 'like', page: API_PAGE}, {guestId: GUEST_B});
    assert.equal(liked.status, 200, JSON.stringify(liked.json));
    assert.deepEqual(liked.json.likes, {count: 2, likedBy: ['carol'], liked: true});
    const commented = await write({action: 'comment', page: API_PAGE, body: 'A guest says hello.'}, {guestId: GUEST_B});
    assert.equal(commented.status, 200);
    assert.deepEqual(commented.json.comment.author, {login: null, name: 'Guest', avatarUrl: null});
    assert.equal(commented.json.comment.canDelete, true);
    // carol cannot delete the guest's comment; the guest can.
    const notHers = await write(
      {action: 'delete', page: API_PAGE, id: commented.json.comment.id, login: 'carol'},
      {cookie: sessionCookie},
    );
    assert.equal(notHers.status, 403);
    assert.equal(notHers.json.error, 'you can only delete your own comments');
    const own = await write({action: 'delete', page: API_PAGE, id: commented.json.comment.id}, {guestId: GUEST_B});
    assert.equal(own.status, 200);
    assert.equal(own.json.comments.some((item) => item.id === commented.json.comment.id), false);
  });

  test('cross-site writes carrying the cookie are refused and write nothing', async () => {
    clientIp = '198.51.100.6';
    const commits = github.history(BRANCH).length;
    const attempts = [
      {headers: {origin: 'https://evil.example'}},
      {headers: {'sec-fetch-site': 'cross-site', origin: undefined}},
      // A form or text/plain POST needs no CORS preflight.
      {headers: {'content-type': 'text/plain;charset=UTF-8', origin: undefined, 'sec-fetch-site': undefined}},
      {headers: {'content-type': 'application/x-www-form-urlencoded', origin: 'null'}},
    ];
    for (const {headers} of attempts) {
      for (const body of [
        {action: 'like', page: API_PAGE},
        {action: 'comment', page: API_PAGE, body: 'Posted by another site.'},
      ]) {
        const refused = await write(body, {cookie: sessionCookie, headers});
        assert.equal(refused.status, 403, JSON.stringify(headers));
        assert.equal(refused.json.error, CROSS_SITE_ERROR);
      }
    }
    assert.equal(github.history(BRANCH).length, commits, 'no commit');
    assert.equal(github.readJson(BRANCH, FILE).comments.some((item) => item.body === 'Posted by another site.'), false);
  });

  test('a cookie that does not verify is not a session: the write is a guest one', async () => {
    clientIp = '198.51.100.7';
    const [payload, signature] = sessionCookie.slice(SESSION_COOKIE.length + 1).split('.');
    const forged = `${SESSION_COOKIE}=${payload}.${signature.slice(0, -2)}xx`;
    const commented = await write(
      {action: 'comment', page: API_PAGE, body: 'Forged cookie.'},
      {cookie: forged, guestId: GUEST_B},
    );
    assert.equal(commented.status, 200);
    assert.equal(commented.json.comment.author.login, null);
    assert.equal(commented.json.comment.author.name, 'Guest');
  });

  test('Sign out clears the cookie; the next write is a guest one', async () => {
    clientIp = '198.51.100.8';
    const signout = await local('/api/auth/me?action=signout', {
      method: 'POST',
      headers: {cookie: sessionCookie, origin, 'sec-fetch-site': 'same-origin'},
    });
    assert.equal(signout.status, 200);
    const cleared = setCookies(signout)[SESSION_COOKIE];
    assert.ok(cleared, 'the cookie is cleared');
    assert.equal(cleared.value, '');
    const liked = await write({action: 'like', page: API_PAGE}, {guestId: GUEST_A});
    assert.equal(liked.status, 200);
    assert.equal(liked.json.likes.liked, true, "now the guest's own like");
    assert.equal(liked.json.likes.count, 3);
  });
});
