// Tests for createSharedStore (src/lib/storage.mjs): the browser side of the
// shared likes and comments. The API behind it is the real handler
// (api/_lib/community.mjs) over the in-memory fake GitHub, called in process:
// no server and no network.

import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {createCommunityHandler} from '../../../api/_lib/community.mjs';
import {
  CommunityApiError,
  SHARED_API_PATH,
  SIGN_IN_ERROR,
  createCommunityStore,
  createMemoryStore,
  createSharedStore,
  toApiPageId,
} from '../src/lib/storage.mjs';
import {ALICE_TOKEN, BOB_TOKEN, SERVER_TOKEN, createFakeGitHub} from './helpers/fakeGitHub.mjs';

const PAGE = 'docs/overview/introduction.md';
const API_PAGE = '/docs/overview/introduction.md';

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

function sharedStore({site = createSite(), token = ALICE_TOKEN, fallback = createMemoryStore()} = {}) {
  const auth = {token};
  const store = createSharedStore({fetchImpl: site.fetchImpl, getToken: () => auth.token, fallback});
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

  test('reads need no token, and getLikes + listComments share one request', async () => {
    const {store, site} = sharedStore({token: null});
    const [likes, comments] = await Promise.all([store.getLikes(PAGE), store.listComments(PAGE)]);
    assert.deepEqual(likes, {count: 0, likedBy: []});
    assert.deepEqual(comments, []);
    const reads = site.calls.filter((call) => call.url.includes('?page='));
    assert.equal(reads.length, 1);
    assert.equal(reads[0].url, `${SHARED_API_PATH}?page=${encodeURIComponent(API_PAGE)}`);
    for (const call of site.calls) {
      assert.equal(call.init.method, 'GET');
      assert.equal(call.init.headers.Authorization, undefined, 'reads never send the token');
      assert.equal(call.init.cache, 'no-store');
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
    const visitor = sharedStore({site, token: null}).store;
    assert.deepEqual(await visitor.getLikes(PAGE), {count: 1, likedBy: ['alice']});
    const [comment] = await visitor.listComments(PAGE);
    assert.equal(comment.body, 'Hi Bob');
    assert.equal(comment.pageId, PAGE, 'comments carry the caller page id');
    assert.deepEqual(comment.author, {
      login: 'alice',
      name: 'Alice Example',
      avatarUrl: 'https://avatars.example.test/u/1',
    });
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

  test('without a token writes reject with 401 and send nothing', async () => {
    const {store, site, auth} = sharedStore({token: null});
    for (const attempt of [
      () => store.toggleLike(PAGE, 'alice'),
      () => store.addComment(PAGE, {author: {login: 'alice'}, body: 'hi'}),
      () => store.deleteComment(PAGE, 'c1', 'alice'),
    ]) {
      await assert.rejects(attempt(), (error) => {
        assert.ok(error instanceof CommunityApiError);
        assert.equal(error.status, 401);
        assert.equal(error.message, SIGN_IN_ERROR);
        return true;
      });
    }
    assert.equal(site.calls.filter((call) => call.init.method === 'POST').length, 0);
    // The token is read at each write: adding it later just works.
    auth.token = ALICE_TOKEN;
    assert.equal((await store.toggleLike(PAGE, 'alice')).count, 1);
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
    assert.deepEqual(await store.getLikes(PAGE), {count: 0, likedBy: []});
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
