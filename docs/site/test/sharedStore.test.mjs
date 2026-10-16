// Tests for createSharedStore (src/lib/storage.mjs): the browser side of the
// shared likes and comments. The API behind it is the real handler
// (api/_lib/community.mjs) over the in-memory fake GitHub, called in process:
// no server and no network.

import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {createCommunityHandler} from '../../../api/_lib/community.mjs';
import {
  CommunityApiError,
  GUEST_ID_STORAGE_KEY,
  SHARED_API_PATH,
  createCommunityStore,
  createGuestId,
  createGuestIdSource,
  createMemoryStore,
  createSharedStore,
  isGuestId,
  toApiPageId,
} from '../src/lib/storage.mjs';
import {ALICE_TOKEN, BOB_TOKEN, SERVER_TOKEN, createFakeGitHub} from './helpers/fakeGitHub.mjs';

const PAGE = 'docs/overview/introduction.md';
const API_PAGE = '/docs/overview/introduction.md';
// Fake per-browser guest ids (UUID v4).
const GUEST_A = '0f8a5a4e-1b2c-4d3e-8f90-a1b2c3d4e5f6';
const GUEST_B = '7c6b5a49-3827-4165-9443-322110fedcba';

/** A Map-backed Web Storage (window.localStorage). */
class MapStorage {
  constructor() {
    this.map = new Map();
  }
  get length() {
    return this.map.size;
  }
  key(index) {
    return [...this.map.keys()][index] ?? null;
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

/**
 * A fetch that serves /api/community with the real handler, and records
 * every call the store makes.
 */
function createSite({env = {COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN}, github = createFakeGitHub()} = {}) {
  let ids = 0;
  const handle = createCommunityHandler({
    env,
    fetchImpl: github.fetch,
    sleep: async () => {},
    newId: () => `c${++ids}`,
    log: () => {},
  });
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({url, init});
    const parsed = new URL(url, 'https://docs.example.test');
    assert.equal(parsed.pathname, SHARED_API_PATH, 'the store only talks to /api/community');
    const result = await handle({
      method: init.method ?? 'GET',
      query: Object.fromEntries(parsed.searchParams),
      headers: Object.fromEntries(Object.entries(init.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v])),
      body: init.body,
      ip: '203.0.113.5',
    });
    return new Response(JSON.stringify(result.json), {status: result.status, headers: result.headers});
  };
  return {fetchImpl, calls, github};
}

function sharedStore({site = createSite(), token = ALICE_TOKEN, guestId = GUEST_A, fallback = createMemoryStore()} = {}) {
  const auth = {token};
  const store = createSharedStore({
    fetchImpl: site.fetchImpl,
    getToken: () => auth.token,
    getGuestId: () => guestId,
    fallback,
  });
  return {store, site, auth, fallback};
}

describe('toApiPageId', () => {
  test('repository paths become site paths', () => {
    assert.equal(toApiPageId(PAGE), API_PAGE);
    assert.equal(toApiPageId('/blog/welcome'), '/blog/welcome');
    assert.equal(toApiPageId('docs/a b.md'), '/docs/a%20b.md');
    assert.equal(toApiPageId("docs/it's (1)!*.md"), '/docs/it%27s%20%281%29%21%2A.md');
    assert.equal(toApiPageId('docs/100%.md'), '/docs/100%25.md');
    assert.equal(toApiPageId('docs/é.md'), '/docs/%C3%A9.md');
    assert.throws(() => toApiPageId(''), {message: 'pageId must be a non-empty string'});
  });
});

describe('createSharedStore, API configured', () => {
  test("backend() is 'shared'; drafts stay in the fallback", async () => {
    const {store, site, fallback} = sharedStore();
    assert.equal(await store.backend(), 'shared');
    assert.equal(await store.draftsBackend(), 'memory');
    await store.saveDraft('blog/new', {content: '# Hi', format: 'md'});
    assert.equal((await fallback.getDraft('blog/new')).content, '# Hi');
    assert.equal((await store.listDrafts()).length, 1);
    assert.equal(await store.deleteDraft('blog/new'), true);
    assert.equal(site.calls.length, 1, 'only the status probe: drafts never reach the API');
    assert.equal(site.calls[0].url, SHARED_API_PATH);
  });

  test('reads need no token, send the guest id, and getLikes + listComments share one request', async () => {
    for (const token of [null, ALICE_TOKEN]) {
      const {store, site} = sharedStore({token});
      const [likes, comments] = await Promise.all([store.getLikes(PAGE), store.listComments(PAGE)]);
      assert.deepEqual(likes, token ? {count: 0, likedBy: []} : {count: 0, likedBy: [], liked: false});
      assert.deepEqual(comments, []);
      const reads = site.calls.filter((call) => call.url.includes('?page='));
      assert.equal(reads.length, 1);
      assert.equal(reads[0].url, `${SHARED_API_PATH}?page=${encodeURIComponent(API_PAGE)}`);
      assert.equal(reads[0].init.headers['X-Guest-Id'], GUEST_A, "so the API can flag this browser's own like and comments");
      for (const call of site.calls) {
        assert.equal(call.init.method, 'GET');
        assert.equal(call.init.headers.Authorization, undefined, 'reads never send the token');
        assert.equal(call.init.cache, 'no-store');
      }
      assert.equal(site.calls[0].init.headers['X-Guest-Id'], undefined, 'the status probe sends no guest id');
    }
  });

  test('toggleLike sends like, then unlike, with the Bearer token', async () => {
    const {store, site} = sharedStore();
    assert.deepEqual(await store.getLikes(PAGE), {count: 0, likedBy: []});
    assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 1, likedBy: ['alice']});
    assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 0, likedBy: []});
    const posts = site.calls.filter((call) => call.init.method === 'POST');
    assert.deepEqual(
      posts.map((call) => JSON.parse(call.init.body)),
      [
        {action: 'like', page: API_PAGE, login: 'alice'},
        {action: 'unlike', page: API_PAGE, login: 'alice'},
      ],
    );
    for (const call of posts) {
      assert.equal(call.url, SHARED_API_PATH);
      assert.equal(call.init.headers.Authorization, `Bearer ${ALICE_TOKEN}`);
      assert.equal(call.init.headers['X-Guest-Id'], GUEST_A, 'sent along: it proves which guest comments are yours');
      assert.equal(call.init.headers['Content-Type'], 'application/json');
      assert.equal(call.init.redirect, 'error', 'a redirect never carries the token away');
      assert.equal(call.init.credentials, 'same-origin');
    }
  });

  test('likes and comments are shared between visitors', async () => {
    const site = createSite();
    const alice = sharedStore({site}).store;
    const bob = sharedStore({site, token: BOB_TOKEN}).store;
    await alice.toggleLike(PAGE, 'alice');
    await alice.addComment(PAGE, {author: {login: 'alice', name: 'ignored', avatarUrl: null}, body: 'Hi Bob'});
    // A new visitor (new store, so no cached answer) sees both.
    const visitor = sharedStore({site, token: null, guestId: GUEST_B}).store;
    assert.deepEqual(await visitor.getLikes(PAGE), {count: 1, likedBy: ['alice'], liked: false});
    const [comment] = await visitor.listComments(PAGE);
    assert.equal(comment.body, 'Hi Bob');
    assert.equal(comment.pageId, PAGE, 'comments carry the caller page id');
    assert.deepEqual(comment.author, {
      login: 'alice',
      name: 'Alice Example',
      avatarUrl: 'https://avatars.example.test/u/1',
    });
    assert.equal(comment.canDelete, false, "not the visitor's");
    assert.deepEqual(await bob.toggleLike(PAGE, 'bob'), {count: 2, likedBy: ['alice', 'bob']});
  });

  test('addComment returns the verified comment; deleteComment only deletes your own', async () => {
    const site = createSite();
    const {store} = sharedStore({site});
    const comment = await store.addComment(PAGE, {author: {login: 'alice'}, body: '  Hello  '});
    assert.equal(comment.id, 'c1');
    assert.equal(comment.pageId, PAGE);
    assert.equal(comment.body, 'Hello');
    assert.equal(comment.author.name, 'Alice Example', 'the name comes from GitHub, not the browser');
    assert.deepEqual((await store.listComments(PAGE)).map((item) => item.id), ['c1']);

    const bob = sharedStore({site, token: BOB_TOKEN}).store;
    assert.equal(await bob.deleteComment(PAGE, 'c1', 'bob'), false, '403: not his comment');
    assert.equal(await bob.deleteComment(PAGE, 'missing', 'bob'), false, '404');
    assert.equal(await store.deleteComment(PAGE, 'c1', 'alice'), true);
    assert.deepEqual(await store.listComments(PAGE), []);
  });

  test('the comment length is checked before sending', async () => {
    const {store, site} = sharedStore();
    await assert.rejects(store.addComment(PAGE, {body: '   '}), {message: 'comment must be 1-5000 characters'});
    await assert.rejects(store.addComment(PAGE, {body: 'x'.repeat(5001)}), {message: 'comment must be 1-5000 characters'});
    assert.equal(site.calls.filter((call) => call.init.method === 'POST').length, 0);
  });

  test('without a token, likes and comments are guest writes that carry X-Guest-Id and no name', async () => {
    const site = createSite();
    const {store} = sharedStore({site, token: null, guestId: GUEST_A});
    assert.deepEqual(await store.getLikes(PAGE), {count: 0, likedBy: [], liked: false});
    assert.deepEqual(await store.toggleLike(PAGE, 'guest'), {count: 1, likedBy: [], liked: true});
    const comment = await store.addComment(PAGE, {author: {login: 'alice', name: 'Alice', avatarUrl: null}, body: ' as a guest '});
    assert.deepEqual(comment.author, {login: null, name: 'Guest', avatarUrl: null});
    assert.equal(comment.canDelete, true);
    assert.equal(comment.body, 'as a guest');

    const posts = site.calls.filter((call) => call.init.method === 'POST');
    assert.deepEqual(
      posts.map((call) => JSON.parse(call.init.body)),
      [
        {action: 'like', page: API_PAGE},
        {action: 'comment', page: API_PAGE, body: 'as a guest'},
      ],
      'a guest sends no login or name',
    );
    for (const call of posts) {
      assert.equal(call.init.headers['X-Guest-Id'], GUEST_A);
      assert.equal(call.init.headers.Authorization, undefined);
    }
    for (const call of site.calls) {
      assert.equal(call.init.redirect, 'error', 'a redirect never carries the guest id away');
    }

    // Another guest: the like is counted but not theirs, and so is the comment.
    const other = sharedStore({site, token: null, guestId: GUEST_B}).store;
    assert.deepEqual(await other.getLikes(PAGE), {count: 1, likedBy: [], liked: false});
    assert.equal((await other.listComments(PAGE))[0].canDelete, false);
    assert.equal(await other.deleteComment(PAGE, comment.id, 'guest'), false, '403: not their comment');
    assert.deepEqual(await other.toggleLike(PAGE, 'guest'), {count: 2, likedBy: [], liked: true}, 'a like of their own');

    // The first guest unlikes and deletes their own.
    assert.deepEqual(await store.toggleLike(PAGE, 'guest'), {count: 1, likedBy: [], liked: false});
    assert.equal(await store.deleteComment(PAGE, comment.id, 'guest'), true);
    assert.deepEqual(await store.listComments(PAGE), []);
    const lastPost = site.calls.filter((call) => call.init.method === 'POST').at(-1);
    assert.deepEqual(JSON.parse(lastPost.init.body), {action: 'delete', page: API_PAGE, id: comment.id});
  });

  test('the token is read at each write: adding it later switches from guest to verified', async () => {
    const {store, site, auth} = sharedStore({token: null});
    assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 1, likedBy: [], liked: true});
    auth.token = ALICE_TOKEN;
    assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 2, likedBy: ['alice']}, 'the login was not in likedBy: like');
    const last = site.calls.at(-1);
    assert.deepEqual(JSON.parse(last.init.body), {action: 'like', page: API_PAGE, login: 'alice'});
    assert.equal(last.init.headers.Authorization, `Bearer ${ALICE_TOKEN}`);
  });

  test("removing the token: a guest never acts on the signed-in view's `liked`", async () => {
    const {store, site, auth} = sharedStore();
    assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 1, likedBy: ['alice']});
    auth.token = null;
    // Within the 2 s reuse window: still the guest's own view.
    assert.deepEqual(await store.getLikes(PAGE), {count: 1, likedBy: ['alice'], liked: false});
    assert.deepEqual(await store.toggleLike(PAGE, 'guest'), {count: 2, likedBy: ['alice'], liked: true});
    const last = site.calls.at(-1);
    assert.deepEqual(JSON.parse(last.init.body), {action: 'like', page: API_PAGE}, 'a like, not a no-op unlike');
    assert.equal(last.init.headers.Authorization, undefined);

    // Straight after a token write, with no read in between.
    const second = sharedStore();
    await second.store.toggleLike(PAGE, 'alice');
    second.auth.token = null;
    assert.deepEqual(await second.store.toggleLike(PAGE, 'guest'), {count: 2, likedBy: ['alice'], liked: true});
    assert.deepEqual(JSON.parse(second.site.calls.at(-1).init.body), {action: 'like', page: API_PAGE});
  });

  test('a guest id that is not a UUID v4 is replaced by one kept in memory', async () => {
    const site = createSite();
    const store = createSharedStore({fetchImpl: site.fetchImpl, getGuestId: () => 'not-a-uuid', fallback: createMemoryStore()});
    await store.toggleLike(PAGE, 'guest');
    await store.toggleLike(PAGE, 'guest');
    const sent = site.calls.map((call) => call.init.headers['X-Guest-Id']).filter(Boolean);
    assert.equal(sent.length, 2, 'the two writes (the first read had no id yet)');
    assert.ok(isGuestId(sent[0]));
    assert.equal(new Set(sent).size, 1, 'the same id every time');
    assert.deepEqual(await store.getLikes(PAGE), {count: 0, likedBy: [], liked: false}, 'liked, then unliked');
  });

  test('API errors reject with the API message, never a token', async () => {
    const {store} = sharedStore({token: 'fake-token-github-refuses'});
    await assert.rejects(store.toggleLike(PAGE, 'alice'), (error) => {
      assert.equal(error.status, 401);
      assert.ok(!error.message.includes('fake-token-github-refuses'));
      return true;
    });
    const mismatch = sharedStore().store; // alice's token
    await assert.rejects(mismatch.toggleLike(PAGE, 'bob'), (error) => {
      assert.equal(error.status, 403);
      assert.match(error.message, /this token belongs to @alice/);
      return true;
    });
  });

  test('a failed read rejects (and is retried on the next call)', async () => {
    let fail = true;
    const site = createSite();
    const fetchImpl = async (url, init) => {
      if (fail && url.includes('?page=')) {
        return new Response(JSON.stringify({error: 'could not reach the community storage on GitHub'}), {status: 502});
      }
      return site.fetchImpl(url, init);
    };
    const store = createSharedStore({fetchImpl, fallback: createMemoryStore()});
    await assert.rejects(store.getLikes(PAGE), (error) => error instanceof CommunityApiError && error.status === 502);
    fail = false;
    assert.deepEqual(await store.getLikes(PAGE), {count: 0, likedBy: [], liked: false});
  });
});

describe('createSharedStore, API missing or not configured', () => {
  const cases = {
    'not configured (503)': () => createSite({env: {}}).fetchImpl,
    'the dev server answers HTML': () => async () => new Response('<!doctype html><p>page</p>', {status: 200}),
    'no API at all (404)': () => async () => new Response('Not found', {status: 404}),
    offline: () => async () => {
      throw new TypeError('Failed to fetch');
    },
  };
  for (const [name, makeFetch] of Object.entries(cases)) {
    test(`${name}: everything uses the fallback`, async () => {
      const calls = [];
      const inner = makeFetch();
      const fetchImpl = async (url, init) => {
        calls.push(url);
        return inner(url, init);
      };
      const fallback = createMemoryStore();
      const store = createSharedStore({fetchImpl, getToken: () => ALICE_TOKEN, fallback});
      assert.equal(await store.backend(), 'memory');
      assert.deepEqual(await store.toggleLike(PAGE, 'alice'), {count: 1, likedBy: ['alice']});
      await store.addComment(PAGE, {author: {login: 'alice'}, body: 'kept locally'});
      assert.deepEqual(await fallback.getLikes(PAGE), {count: 1, likedBy: ['alice']});
      assert.equal((await fallback.listComments(PAGE))[0].body, 'kept locally');
      assert.equal(await store.deleteComment(PAGE, (await store.listComments(PAGE))[0].id, 'alice'), true);
      assert.deepEqual(calls, [SHARED_API_PATH], 'one probe, no other request');
    });
  }
});

describe('createSharedStore options', () => {
  test('baseUrl must be a path on this site', () => {
    for (const baseUrl of ['https://elsewhere.example/api', '//elsewhere.example/api', 'api/community', '']) {
      assert.throws(() => createSharedStore({baseUrl}), TypeError, baseUrl);
    }
    assert.doesNotThrow(() => createSharedStore({baseUrl: '/custom/api'}));
  });

  test("createCommunityStore({kind: 'shared'}) keeps the guest id in localStorage and reuses it", async () => {
    const site = createSite();
    const storage = new MapStorage();
    const env = {window: {}, fetch: site.fetchImpl, localStorage: storage};
    const first = createCommunityStore({kind: 'shared', env});
    assert.equal(await first.backend(), 'shared');
    assert.deepEqual(await first.getLikes(PAGE), {count: 0, likedBy: [], liked: false});
    assert.equal(storage.getItem(GUEST_ID_STORAGE_KEY), null, 'a visitor who only reads gets no id');
    assert.ok(site.calls.every((call) => call.init.headers?.['X-Guest-Id'] === undefined), 'and sends none');
    assert.deepEqual(await first.toggleLike(PAGE, 'guest'), {count: 1, likedBy: [], liked: true});
    const id = storage.getItem(GUEST_ID_STORAGE_KEY);
    assert.ok(isGuestId(id), String(id));

    // A reload: a new store in the same browser is the same guest.
    const again = createCommunityStore({kind: 'shared', env});
    assert.deepEqual(await again.getLikes(PAGE), {count: 1, likedBy: [], liked: true});
    assert.deepEqual(await again.toggleLike(PAGE, 'guest'), {count: 0, likedBy: [], liked: false});
    const sent = new Set(site.calls.map((call) => call.init.headers?.['X-Guest-Id']).filter(Boolean));
    assert.deepEqual([...sent], [id]);

    // Another browser is another guest.
    const elsewhere = createCommunityStore({kind: 'shared', env: {window: {}, fetch: site.fetchImpl, localStorage: new MapStorage()}});
    assert.deepEqual(await elsewhere.toggleLike(PAGE, 'guest'), {count: 1, likedBy: [], liked: true});
    const reload = createCommunityStore({kind: 'shared', env}); // a fresh store: no cached answer
    assert.deepEqual(await reload.getLikes(PAGE), {count: 1, likedBy: [], liked: false});
  });

  test("createCommunityStore({kind: 'shared'}) uses env.fetch, and is a memory store without a window", async () => {
    const site = createSite();
    const env = {window: {}, fetch: site.fetchImpl};
    const store = createCommunityStore({kind: 'shared', env, getToken: () => ALICE_TOKEN});
    assert.equal(site.calls.length, 0, 'nothing is requested before the first call');
    assert.equal(await store.backend(), 'shared');
    assert.equal((await store.toggleLike(PAGE, 'alice')).count, 1);
    // Drafts: the browser store (memory here: the fake window has no storage).
    assert.equal(await store.draftsBackend(), 'memory');

    const ssr = createCommunityStore({kind: 'shared', env: {}});
    assert.equal(await ssr.backend(), 'memory');
    assert.throws(() => createCommunityStore({kind: 'remote'}), {message: 'unknown community store: remote'});
  });
});

describe('the guest id', () => {
  test('createGuestId makes lower-case UUID v4 strings, with or without crypto.randomUUID', () => {
    const webCrypto = globalThis.crypto;
    const sources = [
      undefined, // globalThis.crypto
      {getRandomValues: (bytes) => webCrypto.getRandomValues(bytes)},
      {
        randomUUID: () => {
          throw new Error('needs a secure context');
        },
        getRandomValues: (bytes) => webCrypto.getRandomValues(bytes),
      },
      {randomUUID: () => 'not a uuid', getRandomValues: (bytes) => webCrypto.getRandomValues(bytes)},
      null, // no Web Crypto at all
    ];
    const ids = sources.map((cryptoApi) => createGuestId(cryptoApi));
    for (const id of ids) {
      assert.ok(isGuestId(id), id);
      assert.equal(id, id.toLowerCase());
    }
    assert.equal(new Set(ids).size, ids.length);
  });

  test('createGuestIdSource reads, creates, repairs, follows other tabs and survives blocked storage', () => {
    let made = 0;
    const make = () => `00000000-0000-4000-8000-${String(++made).padStart(12, '0')}`;
    const storage = new MapStorage();
    const source = createGuestIdSource(() => storage, make);
    const first = source();
    assert.equal(first, '00000000-0000-4000-8000-000000000001');
    assert.equal(storage.getItem(GUEST_ID_STORAGE_KEY), first);
    assert.equal(source(), first);
    assert.equal(source({create: false}), first);
    assert.equal(made, 1);
    assert.equal(createGuestIdSource(() => new MapStorage(), make)({create: false}), null, 'reads never create one');
    assert.equal(made, 1);

    storage.setItem(GUEST_ID_STORAGE_KEY, 'tampered');
    assert.equal(source(), first, 'a broken value is replaced by the id this page had');
    assert.equal(storage.getItem(GUEST_ID_STORAGE_KEY), first);
    storage.setItem(GUEST_ID_STORAGE_KEY, GUEST_B);
    assert.equal(source(), GUEST_B, 'another tab saved one first: use it');

    const broken = new MapStorage();
    broken.setItem(GUEST_ID_STORAGE_KEY, 'not-a-uuid');
    assert.equal(createGuestIdSource(() => broken, make)(), '00000000-0000-4000-8000-000000000002');

    const blocked = createGuestIdSource(() => {
      throw Object.assign(new Error('blocked'), {name: 'SecurityError'});
    }, make);
    const kept = blocked();
    assert.ok(isGuestId(kept));
    assert.equal(blocked(), kept, 'kept in memory');

    const full = new MapStorage();
    full.setItem = () => {
      throw new Error('QuotaExceededError');
    };
    const quota = createGuestIdSource(() => full, make);
    assert.equal(quota(), quota());
  });
});
