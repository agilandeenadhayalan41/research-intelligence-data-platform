// Tests for the browser side of the optional GitHub sign-in
// (src/lib/auth.mjs): the sign-in URL, the #signed-in / #signin-error
// fragment, /api/auth/me answers, and the saved profile and account list.
// No network: fetch is a fake.

import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  ACCOUNTS_STORAGE_KEY,
  GUEST_ID_HEADER,
  GUEST_ID_STORAGE_KEY,
  LOGIN_PATH,
  MAX_ACCOUNTS,
  ME_PATH,
  SIGNIN_ERROR_MESSAGES,
  SIGNIN_UNKNOWN_ERROR,
  SIGNOUT_PATH,
  USER_STORAGE_KEY,
  buildLoginUrl,
  fetchMe,
  getGuestId,
  normalizeProfile,
  parseAccounts,
  parseMeResponse,
  parseStoredProfile,
  readAuthHash,
  rememberAccount,
  requestSignOut,
  safeReturnTo,
  serializeAccounts,
  serializeStoredProfile,
  urlWithoutHash,
} from '../src/lib/auth.mjs';
import {SIGNIN_ERROR_CODES, sanitizeReturnTo} from '../../../api/_lib/session.mjs';

const PROFILE = Object.freeze({
  login: 'octo-cat',
  name: 'The Octocat',
  avatarUrl: 'https://avatars.githubusercontent.com/u/583231?v=4',
  htmlUrl: 'https://github.com/octo-cat',
});

class FakeStorage {
  constructor() {
    this.map = new Map();
  }
  getItem(key) {
    return this.map.has(key) ? this.map.get(key) : null;
  }
  setItem(key, value) {
    this.map.set(key, String(value));
  }
  removeItem(key) {
    this.map.delete(key);
  }
}

function recordingFetch(answer) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({url: String(url), init});
    return answer(String(url), init);
  };
  return {fetchImpl, calls};
}

const json = (status, body) =>
  new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json; charset=utf-8'}});

// ---------------------------------------------------------------------------
// Sign-in URL

describe('buildLoginUrl', () => {
  const returnToOf = (url) => new URL(url, 'https://site.test').searchParams.get('returnTo');

  test('points at /api/auth/login with the current path and query', () => {
    const url = buildLoginUrl({pathname: '/architecture/roadmap', search: '?tab=1'});
    assert.ok(url.startsWith(`${LOGIN_PATH}?`));
    assert.equal(returnToOf(url), '/architecture/roadmap?tab=1');
    assert.equal(buildLoginUrl({pathname: '/'}), '/api/auth/login?returnTo=%2F');
    assert.equal(returnToOf(buildLoginUrl(null)), '/');
  });

  test('drops the fragment (the callback adds its own)', () => {
    assert.equal(returnToOf(buildLoginUrl({pathname: '/blog', search: '', hash: '#comments'})), '/blog');
  });

  test('switch asks for the account picker; login suggests an account', () => {
    const switching = new URL(buildLoginUrl({pathname: '/'}, {switchAccount: true}), 'https://site.test');
    assert.equal(switching.searchParams.get('switch'), '1');
    const hinted = new URL(buildLoginUrl({pathname: '/'}, {login: 'other-user'}), 'https://site.test');
    assert.equal(hinted.searchParams.get('login'), 'other-user');
    const invalid = new URL(buildLoginUrl({pathname: '/'}, {login: 'x&returnTo=//evil'}), 'https://site.test');
    assert.equal(invalid.searchParams.has('login'), false);
    assert.equal(invalid.searchParams.get('returnTo'), '/');
  });

  test('switching to a saved account sends both switch=1 and the login hint', () => {
    const url = new URL(buildLoginUrl({pathname: '/x'}, {switchAccount: true, login: 'b'}), 'https://site.test');
    assert.equal(url.searchParams.get('switch'), '1');
    assert.equal(url.searchParams.get('login'), 'b');
    assert.equal(url.searchParams.get('returnTo'), '/x');
  });

  test('never builds an open redirect', () => {
    for (const pathname of [
      '//evil.test',
      '/.//evil.test',
      '/..//evil.test',
      '/a/..//evil.test',
      '/%2e%2e//evil.test',
      '/\\evil.test',
      'https://evil.test',
      '/api/auth/login',
    ]) {
      assert.equal(returnToOf(buildLoginUrl({pathname, search: ''})), '/', pathname);
    }
  });

  test('safeReturnTo agrees with the server (sanitizeReturnTo)', () => {
    const samples = [
      '/',
      '/a/b?c=d',
      '/blog#x',
      '/a/../b',
      '/é',
      '//evil.test',
      '/.//evil.test',
      '/..//evil.test',
      '/a/..//evil.test',
      '/%2e%2e//evil.test',
      '/\\x',
      '/\tx',
      'x',
      '',
      '/api/auth/me',
      '/apiary',
      '/api?x',
      `/${'a'.repeat(3000)}`,
      undefined,
    ];
    for (const sample of samples) {
      assert.equal(safeReturnTo(sample), sanitizeReturnTo(sample), JSON.stringify(sample));
    }
    assert.equal(safeReturnTo('/apiary'), '/apiary');
  });
});

// ---------------------------------------------------------------------------
// The way back from GitHub

describe('readAuthHash / urlWithoutHash', () => {
  test('reads #signed-in', () => {
    assert.deepEqual(readAuthHash('#signed-in'), {kind: 'signed-in'});
    assert.deepEqual(readAuthHash('signed-in'), {kind: 'signed-in'});
  });

  test('reads #signin-error=<code> with a message for every server code', () => {
    for (const code of SIGNIN_ERROR_CODES) {
      const result = readAuthHash(`#signin-error=${code}`);
      assert.equal(result.kind, 'error');
      assert.equal(result.code, code);
      assert.equal(result.message, SIGNIN_ERROR_MESSAGES[code]);
      assert.ok(result.message.length > 10);
    }
    assert.deepEqual(Object.keys(SIGNIN_ERROR_MESSAGES).sort(), [...SIGNIN_ERROR_CODES].sort());
  });

  test('an unknown code gets a generic message; nothing is reflected', () => {
    assert.deepEqual(readAuthHash('#signin-error=whatever'), {
      kind: 'error',
      code: 'whatever',
      message: SIGNIN_UNKNOWN_ERROR,
    });
    assert.equal(readAuthHash('#signin-error=__proto__').message, SIGNIN_UNKNOWN_ERROR);
    assert.equal(readAuthHash('#signin-error=<img src=x>'), null);
  });

  test('ignores every other fragment', () => {
    for (const hash of ['', '#', '#comments', '#signed-in-later', '#signin-error', null, undefined]) {
      assert.equal(readAuthHash(hash), null, String(hash));
    }
  });

  test('urlWithoutHash keeps the path and query', () => {
    assert.equal(urlWithoutHash({pathname: '/blog', search: '?a=1', hash: '#signed-in'}), '/blog?a=1');
  });
});

// ---------------------------------------------------------------------------
// /api/auth/me

describe('parseMeResponse / fetchMe / requestSignOut', () => {
  test('parses the three answers', () => {
    assert.deepEqual(parseMeResponse({signedIn: true, configured: true, user: {...PROFILE, id: 1}}), {
      kind: 'signed-in',
      user: {...PROFILE},
    });
    assert.deepEqual(parseMeResponse({signedIn: false, configured: true}), {kind: 'signed-out', configured: true});
    assert.deepEqual(parseMeResponse({signedIn: false, configured: false}), {kind: 'signed-out', configured: false});
    assert.deepEqual(parseMeResponse({signedIn: false}), {kind: 'signed-out', configured: true});
  });

  test('anything else means the functions are not running here', () => {
    for (const value of [null, 'text', [], {}, {signedIn: 'yes'}, {signedIn: true}, {signedIn: true, user: {login: '-x-'}}]) {
      assert.deepEqual(parseMeResponse(value), {kind: 'unreachable'}, JSON.stringify(value));
    }
  });

  test('fetchMe asks /api/auth/me with same-origin credentials', async () => {
    const {fetchImpl, calls} = recordingFetch(() => json(200, {signedIn: true, configured: true, user: PROFILE}));
    assert.deepEqual(await fetchMe({fetchImpl}), {kind: 'signed-in', user: {...PROFILE}});
    assert.equal(calls[0].url, ME_PATH);
    assert.equal(calls[0].init.credentials, 'same-origin');
    assert.equal(calls[0].init.cache, 'no-store');
  });

  test('fetchMe: a static server 404, a dev server page or a network error → unreachable', async () => {
    const answers = [
      () => new Response('<!doctype html><title>404</title>', {status: 404, headers: {'content-type': 'text/html'}}),
      () => new Response('<!doctype html><div id="__docusaurus">', {status: 200, headers: {'content-type': 'text/html'}}),
      () => json(500, {error: 'internal error'}),
      () => new Response('{not json', {status: 200, headers: {'content-type': 'application/json'}}),
      () => {
        throw new TypeError('Failed to fetch');
      },
    ];
    for (const answer of answers) {
      const {fetchImpl} = recordingFetch(answer);
      assert.deepEqual(await fetchMe({fetchImpl}), {kind: 'unreachable'});
    }
  });

  test('requestSignOut POSTs ?action=signout and never rejects', async () => {
    const {fetchImpl, calls} = recordingFetch(() => json(200, {signedIn: false, configured: true}));
    assert.equal(await requestSignOut({fetchImpl}), true);
    assert.equal(calls[0].url, SIGNOUT_PATH);
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.credentials, 'same-origin');
    const failing = recordingFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    assert.equal(await requestSignOut({fetchImpl: failing.fetchImpl}), false);
    const notFound = recordingFetch(() => new Response('', {status: 404}));
    assert.equal(await requestSignOut({fetchImpl: notFound.fetchImpl}), false);
  });
});

// ---------------------------------------------------------------------------
// Saved profile and account switcher

describe('saved profile', () => {
  test('round-trips with its source', () => {
    const storage = new FakeStorage();
    storage.setItem(USER_STORAGE_KEY, serializeStoredProfile(PROFILE, 'session'));
    assert.deepEqual(parseStoredProfile(storage.getItem(USER_STORAGE_KEY)), {...PROFILE, source: 'session'});
    assert.deepEqual(parseStoredProfile(serializeStoredProfile(PROFILE, 'typed')), {...PROFILE, source: 'typed'});
  });

  test('a profile saved before OAuth (no source) counts as typed, i.e. not verified', () => {
    assert.equal(parseStoredProfile(JSON.stringify(PROFILE)).source, 'typed');
    assert.equal(parseStoredProfile(JSON.stringify({...PROFILE, source: 'admin'})).source, 'typed');
  });

  test('is checked again: localStorage is not trusted input', () => {
    for (const raw of [null, '', '{', 'null', '[]', JSON.stringify({login: 'bad login'})]) {
      assert.equal(parseStoredProfile(raw), null, String(raw));
    }
    const cleaned = parseStoredProfile(
      JSON.stringify({login: 'octo-cat', name: '', avatarUrl: 'javascript:alert(1)', htmlUrl: 'http://x.test'}),
    );
    assert.deepEqual(cleaned, {
      login: 'octo-cat',
      name: 'octo-cat',
      avatarUrl: 'https://github.com/octo-cat.png',
      htmlUrl: 'https://github.com/octo-cat',
      source: 'typed',
    });
    assert.equal(normalizeProfile({login: 'a', name: 'x'.repeat(500)}).name.length, 100);
  });
});

describe('account list', () => {
  const other = {login: 'other-user', name: 'Other', avatarUrl: 'https://avatars.githubusercontent.com/u/2'};

  test('persists through localStorage (login, name, avatar only)', () => {
    const storage = new FakeStorage();
    let accounts = parseAccounts(storage.getItem(ACCOUNTS_STORAGE_KEY));
    assert.deepEqual(accounts, []);
    accounts = rememberAccount(accounts, PROFILE);
    accounts = rememberAccount(accounts, other);
    storage.setItem(ACCOUNTS_STORAGE_KEY, serializeAccounts(accounts));
    const reloaded = parseAccounts(storage.getItem(ACCOUNTS_STORAGE_KEY));
    assert.deepEqual(reloaded, [
      other,
      {login: PROFILE.login, name: PROFILE.name, avatarUrl: PROFILE.avatarUrl},
    ]);
    assert.ok(!storage.getItem(ACCOUNTS_STORAGE_KEY).includes('htmlUrl'));
  });

  test('signing in again moves the account to the top and refreshes it; nothing is dropped', () => {
    let accounts = rememberAccount([], PROFILE);
    accounts = rememberAccount(accounts, other);
    accounts = rememberAccount(accounts, {...PROFILE, login: 'OCTO-CAT', name: 'Renamed'});
    assert.deepEqual(
      accounts.map((account) => [account.login, account.name]),
      [
        ['OCTO-CAT', 'Renamed'],
        ['other-user', 'Other'],
      ],
    );
  });

  test(`keeps at most ${MAX_ACCOUNTS}, newest first`, () => {
    let accounts = [];
    for (let i = 0; i < MAX_ACCOUNTS + 3; i += 1) {
      accounts = rememberAccount(accounts, {login: `user-${i}`, name: `User ${i}`});
    }
    assert.equal(accounts.length, MAX_ACCOUNTS);
    assert.equal(accounts[0].login, `user-${MAX_ACCOUNTS + 2}`);
  });

  test('ignores invalid entries and duplicates in storage', () => {
    assert.deepEqual(parseAccounts('{'), []);
    assert.deepEqual(parseAccounts('{"login":"x"}'), []);
    const raw = JSON.stringify([PROFILE, {login: 'bad login'}, null, 'text', {...PROFILE, login: 'Octo-Cat'}, other]);
    assert.deepEqual(
      parseAccounts(raw).map((account) => account.login),
      ['octo-cat', 'other-user'],
    );
    assert.deepEqual(rememberAccount([other], {login: 'not valid!'}), [other]);
  });
});

describe('guest id', () => {
  const UUID = '0b7c1a3e-5d2f-4c8b-9a6e-1f2d3c4b5a69';

  test('creates a random UUID once and keeps it', () => {
    const storage = new FakeStorage();
    let made = 0;
    const randomUUID = () => {
      made += 1;
      return UUID;
    };
    assert.equal(getGuestId(storage, randomUUID), UUID);
    assert.equal(storage.getItem(GUEST_ID_STORAGE_KEY), UUID);
    assert.equal(getGuestId(storage, () => 'never-used'), UUID);
    assert.equal(made, 1);
    assert.equal(GUEST_ID_HEADER, 'X-Guest-Id');
  });

  test('replaces an invalid saved id and survives blocked storage', () => {
    const storage = new FakeStorage();
    storage.setItem(GUEST_ID_STORAGE_KEY, 'not-a-uuid');
    assert.equal(getGuestId(storage, () => UUID), UUID);
    const blocked = {
      getItem() {
        throw new Error('SecurityError');
      },
      setItem() {
        throw new Error('SecurityError');
      },
    };
    assert.equal(getGuestId(blocked, () => UUID), UUID);
    assert.equal(getGuestId(null, () => UUID), UUID);
    assert.match(getGuestId(new FakeStorage()), /^[0-9a-f-]{36}$/);
  });
});
