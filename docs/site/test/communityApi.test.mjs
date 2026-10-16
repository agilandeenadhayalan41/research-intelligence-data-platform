// Tests for the /api/community handler (api/_lib/community.mjs at the
// repository root) against an in-memory fake of the GitHub REST API. No
// network: the fake throws on any URL it does not know.

import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {describe, test} from 'node:test';

import {
  BODY_INVALID,
  BODY_MAX_BYTES,
  BODY_TOO_LARGE,
  BUSY_ERROR,
  CROSS_SITE_ERROR,
  COMMENT_LENGTH_ERROR,
  GUEST_ID_ERROR,
  GUEST_NAME,
  GUEST_WRITE_LIMIT,
  GITHUB_READ_LIMIT,
  READ_BUSY_ERROR,
  OWN_COMMENTS_ERROR,
  BRANCH_VERCEL_JSON,
  LOGIN_CACHE_MS,
  MAX_RETRIES,
  PAGE_FILE_MAX_BYTES,
  PAGE_SIZE_ERROR,
  READ_CACHE_MS,
  UNAUTHORIZED_ERROR,
  WRITE_LIMIT,
  createCommunityHandler,
  guestKeyOf,
  isGuestId,
  isPageId,
  normalizePageFile,
  pageFilePath,
  readConfig,
} from '../../../api/_lib/community.mjs';
import {ALICE_ID, ALICE_TOKEN, BOB_TOKEN, SERVER_TOKEN, createFakeGitHub} from './helpers/fakeGitHub.mjs';

const PAGE = '/docs/overview/introduction.md';
const BRANCH = 'community-data';
const FILE = `community/pages/${createHash('sha256').update(PAGE).digest('hex').slice(0, 32)}.json`;
const START = Date.parse('2026-10-16T12:00:00.000Z');
// Guest ids are random UUID v4 strings made by each browser (fake ones here).
const GUEST_A = '0f8a5a4e-1b2c-4d3e-8f90-a1b2c3d4e5f6';
const GUEST_B = '7c6b5a49-3827-4165-9443-322110fedcba';
const GUEST_C = 'aaaaaaaa-bbbb-4ccc-addd-eeeeeeeeeeee';

function setup({env, fake = createFakeGitHub(), resolveSessionUser} = {}) {
  let time = START;
  let ids = 0;
  const logs = [];
  const sleeps = [];
  const handle = createCommunityHandler({
    resolveSessionUser,
    env: env ?? {COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN},
    fetchImpl: fake.fetch,
    now: () => time,
    random: () => 0.5,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    newId: () => `comment-${++ids}`,
    log: (message) => logs.push(message),
  });

  /** GET with ?page= (omit page for the status probe), optionally as a guest. */
  const get = (page, {ip = '203.0.113.7', query, guestId, headers = {}} = {}) =>
    handle({
      method: 'GET',
      query: query ?? (page === undefined ? {} : {page}),
      headers: {...(guestId ? {'x-guest-id': guestId} : {}), ...headers},
      ip,
    });

  /** POST a JSON body (a string is sent as is). Alice's token unless `token` is null. */
  const post = (body, {token = ALICE_TOKEN, guestId, ip = '203.0.113.7', headers = {}} = {}) =>
    handle({
      method: 'POST',
      query: {},
      headers: {
        'content-type': 'application/json',
        ...(token ? {authorization: `Bearer ${token}`} : {}),
        ...(guestId ? {'x-guest-id': guestId} : {}),
        ...headers,
      },
      body: typeof body === 'string' || typeof body === 'symbol' ? body : JSON.stringify(body),
      ip,
    });

  return {
    handle,
    fake,
    get,
    post,
    logs,
    sleeps,
    advance(ms) {
      time += ms;
    },
  };
}

/** Requests the fake received, as 'METHOD /path-after-repo'. */
function trace(fake, from = 0) {
  return fake.requests.slice(from).map(({method, path}) => `${method} ${path.replace(/^\/repos\/[^/]+\/[^/]+/, '')}`);
}

function assertNoSecrets(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  for (const secret of [SERVER_TOKEN, ALICE_TOKEN, BOB_TOKEN]) {
    assert.ok(!text.includes(secret), `a token leaked into ${text}`);
  }
}

describe('configuration', () => {
  test('without COMMUNITY_GITHUB_TOKEN every request is 503 {configured: false}', async () => {
    const {get, post, fake} = setup({env: {}});
    for (const response of [await get(PAGE), await get(), await post({action: 'like', page: PAGE})]) {
      assert.equal(response.status, 503);
      assert.equal(response.json.configured, false);
      assert.equal(response.headers['Cache-Control'], 'no-store');
    }
    assert.equal(fake.requests.length, 0);
  });

  test('defaults, overrides, and refused settings', () => {
    assert.deepEqual(readConfig({COMMUNITY_GITHUB_TOKEN: ' x-token '}), {
      configured: true,
      token: 'x-token',
      owner: 'agilandeenadhayalan41',
      name: 'research-intelligence-data-platform',
      branch: 'community-data',
      dir: 'community',
    });
    const custom = readConfig({
      COMMUNITY_GITHUB_TOKEN: 't',
      COMMUNITY_REPO: 'someone/elsewhere',
      COMMUNITY_DATA_BRANCH: 'data/likes',
      COMMUNITY_DATA_DIR: '/likes/',
    });
    assert.equal(custom.configured && custom.branch, 'data/likes');
    assert.equal(custom.configured && custom.dir, 'likes');
    // Never main: the data branch must be a separate branch.
    for (const branch of ['main', 'Main', 'master', 'HEAD', '../x', 'a..b/../c', 'x.lock', 'with space']) {
      assert.equal(readConfig({COMMUNITY_GITHUB_TOKEN: 't', COMMUNITY_DATA_BRANCH: branch}).configured, false, branch);
    }
    assert.equal(readConfig({COMMUNITY_GITHUB_TOKEN: 't', COMMUNITY_REPO: 'no-slash'}).configured, false);
    assert.equal(readConfig({COMMUNITY_GITHUB_TOKEN: 't', COMMUNITY_DATA_DIR: '../up'}).configured, false);
  });

  test('a refused setting answers 503 configured false, and nothing reaches GitHub', async () => {
    const {post, fake} = setup({env: {COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN, COMMUNITY_DATA_BRANCH: 'main'}});
    const response = await post({action: 'like', page: PAGE});
    assert.equal(response.status, 503);
    assert.equal(response.json.configured, false);
    assert.equal(fake.requests.length, 0);
  });

  test('custom repo, branch and folder are used', async () => {
    const fake = createFakeGitHub({owner: 'someone', repo: 'elsewhere'});
    const {post} = setup({
      fake,
      env: {
        COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN,
        COMMUNITY_REPO: 'someone/elsewhere',
        COMMUNITY_DATA_BRANCH: 'data/likes',
        COMMUNITY_DATA_DIR: 'likes',
      },
    });
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
    const path = pageFilePath(PAGE, 'likes');
    assert.match(path, /^likes\/pages\/[0-9a-f]{32}\.json$/);
    assert.deepEqual(fake.readJson('data/likes', path).likes.map((like) => like.login), ['alice']);
    assert.ok(fake.branches.get('data/likes').files.has('likes/README.md'));
  });
});

describe('methods and validation', () => {
  test('methods other than GET and POST are 405, with Allow', async () => {
    for (const env of [{COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN}, {}]) {
      const {handle} = setup({env});
      for (const method of ['PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD']) {
        const response = await handle({method, query: {page: PAGE}, headers: {}});
        assert.equal(response.status, 405, method);
        assert.equal(response.headers.Allow, 'GET, POST');
      }
    }
  });

  test('page ids must be site paths', async () => {
    assert.ok(isPageId('/'));
    assert.ok(isPageId('/docs/a_b/c-d.md'));
    assert.ok(isPageId('/docs/x%20y~z.md'));
    for (const bad of ['', 'docs/x.md', '/a b', '/a?b', '/a#b', '/<x>', `/${'a'.repeat(301)}`, 7, null]) {
      assert.equal(isPageId(bad), false, String(bad));
    }
    assert.ok(isPageId(`/${'a'.repeat(300)}`));

    const {get, post, fake} = setup();
    for (const query of [{page: 'docs/x.md'}, {page: '/a b'}, {page: ['/a', '/b']}, {page: ''}]) {
      const response = await get(undefined, {query});
      assert.equal(response.status, 400, JSON.stringify(query));
    }
    assert.equal((await get(undefined, {query: new URLSearchParams('page=%2Fa&page=%2Fb')})).status, 400);
    assert.equal((await post({action: 'like', page: 'no-slash'})).status, 400);
    assert.equal((await post({action: 'like'})).status, 400);
    assert.equal(fake.requests.length, 0, 'nothing invalid reaches GitHub');
  });

  test('bodies: 413 over 16 KB, 400 for invalid JSON and unknown actions', async () => {
    const {post, handle, fake} = setup();
    const big = JSON.stringify({action: 'comment', page: PAGE, body: 'x'.repeat(BODY_MAX_BYTES)});
    assert.equal((await post(big)).status, 413);
    assert.equal((await post(BODY_TOO_LARGE)).status, 413);
    assert.equal(
      (await post({action: 'like', page: PAGE}, {headers: {'content-length': String(BODY_MAX_BYTES + 1)}})).status,
      413,
    );
    // An object already parsed by the platform is measured too.
    const parsedBig = await handle({
      method: 'POST',
      headers: {authorization: `Bearer ${ALICE_TOKEN}`},
      body: {action: 'comment', page: PAGE, body: 'y'.repeat(BODY_MAX_BYTES)},
    });
    assert.equal(parsedBig.status, 413);
    // Bytes, not characters: 5000 emoji are 20 KB of UTF-8.
    assert.equal((await post({action: 'comment', page: PAGE, body: '😀'.repeat(5000)})).status, 413);

    assert.equal((await post('{not json')).status, 400);
    assert.equal((await post(BODY_INVALID)).status, 400);
    assert.equal((await post('[1, 2]')).status, 400);
    assert.equal((await post('"text"')).status, 400);
    assert.equal((await post(Buffer.from(JSON.stringify({action: 'nope', page: PAGE})))).status, 400);
    const unknown = await post({action: 'shout', page: PAGE});
    assert.equal(unknown.status, 400);
    assert.match(unknown.json.error, /action must be/);
    assert.equal(fake.requests.length, 0);
  });

  test('responses are JSON, never cached, and carry no CORS headers', async () => {
    const {get} = setup();
    const response = await get(PAGE);
    assert.equal(response.headers['Content-Type'], 'application/json; charset=utf-8');
    assert.equal(response.headers['Cache-Control'], 'no-store');
    assert.equal(response.headers['X-Content-Type-Options'], 'nosniff');
    assert.ok(!Object.keys(response.headers).some((name) => name.toLowerCase().startsWith('access-control-')));
  });
});

describe('reads', () => {
  test('the status probe answers configured without calling GitHub', async () => {
    const {get, fake} = setup();
    const response = await get();
    assert.equal(response.status, 200);
    assert.deepEqual(response.json, {configured: true});
    assert.equal(fake.requests.length, 0);
  });

  test('a page without a file (or before the branch exists) is empty', async () => {
    const {get, fake} = setup();
    const response = await get(PAGE);
    assert.equal(response.status, 200);
    assert.deepEqual(response.json, {
      configured: true,
      pageId: PAGE,
      likes: {count: 0, likedBy: [], liked: false},
      comments: [],
    });
    assert.deepEqual(trace(fake), [`GET /contents/${FILE}`]);
    assert.deepEqual(fake.requests[0].query, {ref: BRANCH});
    assert.equal(fake.requests[0].auth, 'server');
  });

  test('an existing page is read, checked, and cached for 20 seconds', async () => {
    const fake = createFakeGitHub();
    fake.writeFile(
      BRANCH,
      FILE,
      JSON.stringify({
        version: 1,
        pageId: PAGE,
        likes: [
          {login: 'alice', at: '2026-10-01T00:00:00.000Z'},
          {login: 'alice', at: 'duplicate'},
          {login: 'not a login!', at: 'x'},
          {login: 'bob', at: '2026-10-02T00:00:00.000Z'},
        ],
        comments: [
          {id: 'c1', login: 'bob', name: 'Bob', avatarUrl: 'https://avatars.example.test/b', body: 'First', createdAt: '2026-10-03T00:00:00.000Z'},
          {id: 'c2', login: 'alice', name: '', avatarUrl: 'javascript:alert(1)', body: 'Second', createdAt: '2026-10-04T00:00:00.000Z'},
          {id: '', login: 'x', body: 'dropped: no id', createdAt: 'x'},
        ],
      }),
    );
    const {get, advance} = setup({fake});
    const first = await get(PAGE);
    assert.equal(first.status, 200);
    assert.deepEqual(first.json.likes, {count: 2, likedBy: ['alice', 'bob'], liked: false});
    assert.deepEqual(first.json.comments, [
      {
        id: 'c1',
        pageId: PAGE,
        author: {login: 'bob', name: 'Bob', avatarUrl: 'https://avatars.example.test/b'},
        body: 'First',
        createdAt: '2026-10-03T00:00:00.000Z',
        canDelete: false,
      },
      {
        id: 'c2',
        pageId: PAGE,
        author: {login: 'alice', name: 'alice', avatarUrl: null},
        body: 'Second',
        createdAt: '2026-10-04T00:00:00.000Z',
        canDelete: false,
      },
    ]);
    const calls = fake.requests.length;
    advance(READ_CACHE_MS - 1);
    assert.deepEqual((await get(PAGE)).json, first.json);
    assert.equal(fake.requests.length, calls, 'served from the cache');
    advance(1);
    await get(PAGE);
    assert.equal(fake.requests.length, calls + 1, 'read again after 20 s');
  });

  test('files over 1 MB are read through the blobs API', async () => {
    const fake = createFakeGitHub({largeFileBytes: 10});
    fake.writeFile(BRANCH, FILE, JSON.stringify({version: 1, pageId: PAGE, likes: [{login: 'bob', at: 'x'}], comments: []}));
    const {get} = setup({fake});
    const response = await get(PAGE);
    assert.deepEqual(response.json.likes, {count: 1, likedBy: ['bob'], liked: false});
    assert.deepEqual(trace(fake).map((line) => line.replace(/[0-9a-f]{40}/, '<sha>')), [
      `GET /contents/${FILE}`,
      'GET /git/blobs/<sha>',
    ]);
  });

  test('GitHub failures: 502, or 503 when GitHub rate limits; never a token', async () => {
    const fake = createFakeGitHub();
    const {get, logs} = setup({fake});
    fake.hooks.override = () => ({status: 500, body: {message: `boom ${SERVER_TOKEN} ${ALICE_TOKEN}`}});
    const failed = await get(PAGE);
    assert.equal(failed.status, 502);
    assertNoSecrets(failed);
    fake.hooks.override = () => ({status: 403, body: {message: 'API rate limit exceeded'}});
    const limited = await get('/docs/other.md');
    assert.equal(limited.status, 503);
    assert.equal(limited.headers['Retry-After'], '60');
    assertNoSecrets(logs);
  });

  test('network errors are 502', async () => {
    const {handle} = {
      handle: createCommunityHandler({
        env: {COMMUNITY_GITHUB_TOKEN: SERVER_TOKEN},
        fetchImpl: async () => {
          throw new TypeError(`fetch failed for ${SERVER_TOKEN}`);
        },
        log: () => {},
      }),
    };
    const response = await handle({method: 'GET', query: {page: PAGE}});
    assert.equal(response.status, 502);
    assertNoSecrets(response);
  });
});

describe('writes', () => {
  test('no token and no guest id is 400; a malformed or refused token is 401 (never a silent guest write)', async () => {
    const {post, fake} = setup();
    const missing = await post({action: 'like', page: PAGE}, {token: null});
    assert.equal(missing.status, 400);
    assert.equal(missing.json.error, GUEST_ID_ERROR);
    const basic = await post({action: 'like', page: PAGE}, {token: null, headers: {authorization: 'Basic abc'}});
    assert.equal(basic.status, 401);
    assert.equal(basic.json.error, UNAUTHORIZED_ERROR);
    assert.equal(basic.headers['WWW-Authenticate'], 'Bearer');
    const basicGuest = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A, headers: {authorization: 'Basic abc'}});
    assert.equal(basicGuest.status, 401, 'an Authorization header that is not a Bearer token is refused, even with a guest id');
    assert.equal(fake.requests.length, 0, 'no token: GitHub is not asked');

    const invalid = await post({action: 'like', page: PAGE}, {token: 'fake-token-github-refuses', guestId: GUEST_A});
    assert.equal(invalid.status, 401);
    assert.equal(invalid.json.error, UNAUTHORIZED_ERROR);
    assert.deepEqual(trace(fake), ['GET /user']);
    assert.ok(!JSON.stringify(invalid).includes('fake-token-github-refuses'));
    assert.equal(fake.readJson(BRANCH, FILE), undefined, 'nothing was written as a guest');
  });

  test('like and unlike are idempotent and each change is one commit', async () => {
    const {post, fake} = setup();
    const liked = await post({action: 'like', page: PAGE});
    assert.equal(liked.status, 200);
    assert.deepEqual(liked.json.likes, {count: 1, likedBy: ['alice'], liked: true});
    assert.deepEqual(liked.json.comments, []);
    assert.deepEqual(fake.readJson(BRANCH, FILE), {
      version: 1,
      pageId: PAGE,
      likes: [{login: 'alice', userId: ALICE_ID, at: '2026-10-16T12:00:00.000Z'}],
      comments: [],
    });

    const puts = () => fake.requests.filter((request) => request.method === 'PUT').length;
    assert.equal(puts(), 1);
    const again = await post({action: 'like', page: PAGE});
    assert.deepEqual(again.json.likes, {count: 1, likedBy: ['alice'], liked: true});
    assert.equal(puts(), 1, 'liking twice writes nothing');

    assert.deepEqual((await post({action: 'like', page: PAGE}, {token: BOB_TOKEN})).json.likes.likedBy, ['alice', 'bob']);
    assert.deepEqual((await post({action: 'unlike', page: PAGE})).json.likes, {count: 1, likedBy: ['bob'], liked: false});
    assert.deepEqual((await post({action: 'unlike', page: PAGE})).json.likes, {count: 1, likedBy: ['bob'], liked: false});
    assert.equal(puts(), 3, 'unliking twice writes once');

    const messages = fake.history(BRANCH).map((commit) => commit.message);
    assert.deepEqual(messages.slice(0, 3), [
      `community: unlike on ${PAGE}`,
      `community: like on ${PAGE}`,
      `community: like on ${PAGE}`,
    ]);
    const put = fake.requests.find((request) => request.method === 'PUT');
    assert.equal(put.body.branch, BRANCH);
    assert.equal(put.body.author, undefined, 'the commit uses the token owner identity');
    assert.equal(put.body.committer, undefined);
  });

  test('a login sent by the browser must match the token owner', async () => {
    const {post, fake} = setup();
    const response = await post({action: 'like', page: PAGE, login: 'bob'});
    assert.equal(response.status, 403);
    assert.match(response.json.error, /belongs to @alice/);
    assert.equal(fake.readJson(BRANCH, FILE), undefined);
    assert.equal((await post({action: 'like', page: PAGE, login: 'ALICE'})).status, 200, 'logins ignore case');
  });

  test('comments: 1-5000 characters after trimming, verified author, oldest first', async () => {
    const {post, fake, advance} = setup();
    for (const body of ['', '   \n ', 'x'.repeat(5001), 42, undefined]) {
      const response = await post({action: 'comment', page: PAGE, body});
      assert.equal(response.status, 400, String(body).slice(0, 10));
      assert.equal(response.json.error, COMMENT_LENGTH_ERROR);
    }
    assert.equal(fake.requests.length, 0, 'invalid comments do not reach GitHub');

    const first = await post({action: 'comment', page: PAGE, body: '  Hello <b>there</b>  '});
    assert.equal(first.status, 200);
    assert.deepEqual(first.json.comment, {
      id: 'comment-1',
      pageId: PAGE,
      author: {login: 'alice', name: 'Alice Example', avatarUrl: 'https://avatars.example.test/u/1'},
      body: 'Hello <b>there</b>',
      createdAt: '2026-10-16T12:00:00.000Z',
      canDelete: true,
    });
    advance(1000);
    const second = await post({action: 'comment', page: PAGE, body: 'x'.repeat(5000)}, {token: BOB_TOKEN});
    assert.equal(second.status, 200);
    // bob has no name and an http avatar: the login, and no avatar.
    assert.deepEqual(second.json.comment.author, {login: 'bob', name: 'bob', avatarUrl: null});
    assert.deepEqual(second.json.comments.map((comment) => comment.id), ['comment-1', 'comment-2']);
    assert.deepEqual(second.json.comments.map((comment) => comment.canDelete), [false, true], 'canDelete is for the caller (bob)');
    assert.deepEqual(fake.readJson(BRANCH, FILE).comments[0], {
      id: 'comment-1',
      login: 'alice',
      userId: ALICE_ID,
      name: 'Alice Example',
      avatarUrl: 'https://avatars.example.test/u/1',
      body: 'Hello <b>there</b>',
      createdAt: '2026-10-16T12:00:00.000Z',
    });
  });

  test('delete: only your own comment (403 otherwise), 404 when missing', async () => {
    const {post, fake} = setup();
    const created = await post({action: 'comment', page: PAGE, body: 'mine'});
    const id = created.json.comment.id;

    const other = await post({action: 'delete', page: PAGE, id}, {token: BOB_TOKEN});
    assert.equal(other.status, 403);
    assert.equal(other.json.error, 'you can only delete your own comments');
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, 1);

    const own = await post({action: 'delete', page: PAGE, id});
    assert.equal(own.status, 200);
    assert.equal(own.json.deleted, true);
    assert.deepEqual(own.json.comments, []);
    assert.deepEqual(fake.readJson(BRANCH, FILE).comments, []);
    assert.equal(fake.history(BRANCH)[0].message, `community: delete on ${PAGE}`);

    assert.equal((await post({action: 'delete', page: PAGE, id})).status, 404);
    assert.equal((await post({action: 'delete', page: PAGE})).status, 400);
    assert.equal((await post({action: 'delete', page: PAGE, id: 'x'.repeat(101)})).status, 400);
  });

  test(`a comment that would grow the page file past ${PAGE_FILE_MAX_BYTES} bytes is 409; likes and deletes still work`, async () => {
    const fake = createFakeGitHub();
    // As the handler writes it: normalized, pretty-printed, newline at the end.
    const size = (file) => Buffer.byteLength(`${JSON.stringify(normalizePageFile(file, PAGE), null, 2)}\n`, 'utf8');
    const filler = (index, length) => ({
      id: `seed-${index}`,
      login: 'bob',
      name: 'Bob',
      avatarUrl: null,
      body: 'y'.repeat(length),
      createdAt: '2026-10-01T00:00:00.000Z',
    });
    const seeded = {version: 1, pageId: PAGE, likes: [], comments: []};
    while (size(seeded) + 5000 < PAGE_FILE_MAX_BYTES - 3000) {
      seeded.comments.push(filler(seeded.comments.length, 4500));
    }
    // Top it up to about 1000 bytes under the cap.
    const overhead = size({...seeded, comments: [...seeded.comments, filler(seeded.comments.length, 0)]}) - size(seeded);
    seeded.comments.push(filler(seeded.comments.length, PAGE_FILE_MAX_BYTES - 1000 - size(seeded) - overhead));
    assert.ok(size(seeded) < PAGE_FILE_MAX_BYTES && size(seeded) > PAGE_FILE_MAX_BYTES - 1500, String(size(seeded)));
    fake.writeFile(BRANCH, FILE, JSON.stringify(seeded));
    const count = seeded.comments.length;
    const {post} = setup({fake});

    const before = fake.requests.length;
    const refused = await post({action: 'comment', page: PAGE, body: 'z'.repeat(2000)});
    assert.equal(refused.status, 409);
    assert.deepEqual(refused.json, {configured: true, error: PAGE_SIZE_ERROR});
    assert.ok(!trace(fake, before).some((line) => line.startsWith('PUT ')), 'no write is sent');
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, count);

    // A short comment that stays under the cap is still accepted.
    const small = await post({action: 'comment', page: PAGE, body: 'ok'});
    assert.equal(small.status, 200);
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, count + 1);

    // Likes and deletes are never refused for size.
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
    const deleted = await post({action: 'delete', page: PAGE, id: small.json.comment.id});
    assert.equal(deleted.status, 200);
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, count);
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes.map((like) => like.login), ['alice']);
  });

  test('the verified login is cached for 10 minutes by token hash', async () => {
    const {post, fake, advance} = setup();
    await post({action: 'like', page: PAGE});
    await post({action: 'unlike', page: PAGE});
    const userCalls = () => fake.requests.filter((request) => request.path === '/user').length;
    assert.equal(userCalls(), 1);
    advance(LOGIN_CACHE_MS);
    await post({action: 'like', page: PAGE});
    assert.equal(userCalls(), 2);
  });

  test('tokens go only where they belong and never into a response or a log', async () => {
    const {post, get, fake, logs} = setup();
    await post({action: 'comment', page: PAGE, body: 'hi'});
    await post({action: 'like', page: PAGE}, {token: BOB_TOKEN});
    await get(PAGE);
    for (const request of fake.requests) {
      if (request.path === '/user') {
        assert.match(request.auth, /^visitor:/, 'the visitor token only goes to GET /user');
      } else {
        assert.equal(request.auth, 'server', `${request.method} ${request.path} uses the server token`);
      }
      assertNoSecrets(request.body ?? {});
    }
    assertNoSecrets(logs);

    // Even if GitHub echoed a token back, it is not passed on.
    fake.hooks.override = ({method}) =>
      method === 'PUT' ? {status: 500, body: {message: `${SERVER_TOKEN} ${ALICE_TOKEN}`}} : null;
    const failed = await post({action: 'comment', page: PAGE, body: 'again'});
    assert.equal(failed.status, 502);
    assertNoSecrets(failed);
    assertNoSecrets(logs);
  });

  test('a corrupted page file is never overwritten', async () => {
    const fake = createFakeGitHub();
    fake.writeFile(BRANCH, FILE, '{"likes": [');
    const {post, get} = setup({fake});
    assert.deepEqual((await get(PAGE)).json.likes, {count: 0, likedBy: [], liked: false});
    const response = await post({action: 'like', page: PAGE});
    assert.equal(response.status, 500);
    assert.equal(fake.requests.filter((request) => request.method === 'PUT').length, 0);
    assert.equal(fake.branches.get(BRANCH).files.get(FILE).text, '{"likes": [');
  });
});

describe('compare-and-swap', () => {
  test('a 409 conflict re-reads the file and retries; both changes survive', async () => {
    const fake = createFakeGitHub();
    const {post, sleeps} = setup({fake});
    await post({action: 'like', page: PAGE}, {token: BOB_TOKEN}); // creates the branch and the file
    let concurrent = 0;
    fake.hooks.beforePut = ({path}) => {
      // Two other writers land between our read and our write.
      if (path === FILE && concurrent < 2) {
        concurrent += 1;
        const current = fake.readJson(BRANCH, FILE);
        current.likes.push({login: `writer${concurrent}`, at: 'x'});
        fake.writeFile(BRANCH, FILE, JSON.stringify(current));
      }
    };
    const response = await post({action: 'like', page: PAGE});
    assert.equal(response.status, 200);
    assert.deepEqual(response.json.likes.likedBy, ['bob', 'writer1', 'writer2', 'alice']);
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes.map((like) => like.login), ['bob', 'writer1', 'writer2', 'alice']);
    assert.equal(sleeps.length, 2, 'one jittered pause per retry');
    assert.ok(sleeps.every((ms) => ms > 0 && ms < 500));
  });

  test('a 422 (file created meanwhile, so no sha was sent) is retried with the sha', async () => {
    const fake = createFakeGitHub();
    const {post} = setup({fake});
    await post({action: 'like', page: '/docs/other.md'}); // creates the branch
    let once = false;
    fake.hooks.beforePut = ({path}) => {
      if (path === FILE && !once) {
        once = true;
        fake.writeFile(BRANCH, FILE, JSON.stringify({version: 1, pageId: PAGE, likes: [{login: 'bob', at: 'x'}], comments: []}));
      }
    };
    const response = await post({action: 'comment', page: PAGE, body: 'hello'});
    assert.equal(response.status, 200);
    assert.deepEqual(response.json.likes.likedBy, ['bob']);
    assert.equal(response.json.comments.length, 1);
    const puts = fake.requests.filter((request) => request.method === 'PUT' && request.path.endsWith(FILE));
    assert.equal(puts.length, 2);
    assert.equal(puts[0].body.sha, undefined);
    assert.match(puts[1].body.sha, /^[0-9a-f]{40}$/);
  });

  test(`after ${MAX_RETRIES} retries it gives up with 503 busy`, async () => {
    const fake = createFakeGitHub();
    const {post, logs} = setup({fake});
    await post({action: 'like', page: PAGE}, {token: BOB_TOKEN});
    const before = fake.putAttempts;
    fake.hooks.override = ({method}) => (method === 'PUT' ? {status: 409, body: {message: 'conflict'}} : null);
    const response = await post({action: 'like', page: PAGE});
    assert.equal(response.status, 503);
    assert.equal(response.json.error, BUSY_ERROR);
    assert.equal(response.headers['Retry-After'], '2');
    assert.equal(fake.requests.filter((request) => request.method === 'PUT').length - 1, MAX_RETRIES + 1);
    assert.equal(fake.putAttempts, before, 'the override answered every PUT');
    assert.ok(logs.some((line) => line.includes('write conflict')));
  });
});

describe('the data branch', () => {
  test('is created as an orphan branch (blob, tree, commit without parents, ref) on the first write', async () => {
    const fake = createFakeGitHub();
    const mainBefore = fake.branches.get('main').commit;
    const {post} = setup({fake});
    const response = await post({action: 'like', page: PAGE});
    assert.equal(response.status, 200);
    assert.deepEqual(trace(fake), [
      'GET /user',
      `GET /git/ref/heads/${BRANCH}`,
      'POST /git/blobs',
      'POST /git/trees',
      'POST /git/commits',
      'POST /git/refs',
      `GET /contents/${FILE}`,
      `PUT /contents/${FILE}`,
    ]);
    const byPath = (path) => fake.requests.find((request) => request.path.endsWith(path));
    assert.equal(byPath('/git/blobs').body.encoding, 'utf-8');
    assert.match(byPath('/git/blobs').body.content, /^# Community data for the docs site/);
    assert.deepEqual(byPath('/git/trees').body.tree.map(({path, mode, type}) => ({path, mode, type})), [
      {path: 'community/README.md', mode: '100644', type: 'blob'},
      {path: 'vercel.json', mode: '100644', type: 'blob'},
    ]);
    // Vercel reads vercel.json from the pushed commit, so the branch carries
    // its own copy that turns deployments off (given inline, no blob call).
    assert.equal(byPath('/git/trees').body.tree[1].content, BRANCH_VERCEL_JSON);
    assert.equal(byPath('/git/trees').body.tree[1].sha, undefined);
    assert.equal(byPath('/git/trees').body.base_tree, undefined);
    assert.deepEqual(byPath('/git/commits').body.parents, []);
    assert.equal(byPath('/git/refs').body.ref, `refs/heads/${BRANCH}`);

    const history = fake.history(BRANCH);
    assert.deepEqual(history.at(-1), {message: `community: create the ${BRANCH} branch`, parents: 0});
    assert.equal(history.length, 2);
    assert.ok(fake.branches.get(BRANCH).files.has('community/README.md'));
    const branchVercel = fake.readJson(BRANCH, 'vercel.json');
    assert.deepEqual(branchVercel, {git: {deploymentEnabled: false}});
    assert.equal(fake.branches.get('main').commit, mainBefore, 'main is never written');

    // Known now: the next write does not look for the branch again.
    const from = fake.requests.length;
    await post({action: 'unlike', page: PAGE});
    assert.deepEqual(trace(fake, from), [`GET /contents/${FILE}`, `PUT /contents/${FILE}`]);
  });

  test('an existing branch is used as it is', async () => {
    const fake = createFakeGitHub();
    fake.writeFile(BRANCH, 'community/README.md', 'kept');
    const {post} = setup({fake});
    await post({action: 'like', page: PAGE});
    assert.deepEqual(trace(fake).slice(0, 3), ['GET /user', `GET /git/ref/heads/${BRANCH}`, `GET /contents/${FILE}`]);
    assert.equal(fake.branches.get(BRANCH).files.get('community/README.md').text, 'kept');
  });

  test('another instance creating it first (422 on the ref) is fine', async () => {
    const fake = createFakeGitHub();
    const {post} = setup({fake});
    fake.hooks.override = ({method, path}) => {
      if (method === 'POST' && path.endsWith('/git/refs')) {
        fake.hooks.override = null;
        fake.writeFile(BRANCH, 'community/README.md', 'made by another instance');
        return {status: 422, body: {message: 'Reference already exists'}};
      }
      return null;
    };
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes.map((like) => like.login), ['alice']);
  });

  test('a branch deleted later is created again', async () => {
    const fake = createFakeGitHub();
    const {post} = setup({fake});
    await post({action: 'like', page: PAGE});
    fake.branches.delete(BRANCH);
    const response = await post({action: 'comment', page: PAGE, body: 'after a reset'});
    assert.equal(response.status, 200);
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, 1);
  });
});

describe('rate limits (per IP, in memory)', () => {
  test('20 writes per 10 minutes, then 429 with Retry-After', async () => {
    const {post, advance} = setup();
    for (let index = 0; index < 20; index += 1) {
      const response = await post({action: index % 2 ? 'unlike' : 'like', page: PAGE});
      assert.equal(response.status, 200, `write ${index + 1}`);
      advance(1000);
    }
    const limited = await post({action: 'like', page: PAGE});
    assert.equal(limited.status, 429);
    assert.equal(limited.headers['Retry-After'], String(10 * 60 - 20));
    assert.match(limited.json.error, /too many requests/);
    // Bad requests count too (they cost the same to an attacker).
    assert.equal((await post({action: 'like', page: PAGE}, {token: null})).status, 429);
    assert.equal((await post({action: 'like', page: PAGE}, {ip: '198.51.100.9'})).status, 200, 'other IPs are not affected');
    advance(10 * 60 * 1000 - 20 * 1000); // now 10 min after the first write (at 0 s): it leaves the window
    assert.equal((await post({action: 'unlike', page: PAGE})).status, 200, 'the oldest write left the window');
    assert.equal((await post({action: 'like', page: PAGE})).status, 429, 'only one slot came free');
  });

  test(`at most ${GITHUB_READ_LIMIT.max} page reads a minute reach GitHub per instance, whoever asks; cached pages keep working`, async () => {
    const {get, post, fake, advance} = setup();
    // Made-up page ids from many addresses: each one would be a GitHub call.
    for (let index = 0; index < GITHUB_READ_LIMIT.max; index += 1) {
      const response = await get(`/docs/made-up-${index}.md`, {ip: `198.51.100.${index}`});
      assert.equal(response.status, 200, `read ${index + 1}`);
      advance(100);
    }
    const calls = fake.requests.length;
    assert.equal(calls, GITHUB_READ_LIMIT.max);
    const limited = await get('/docs/one-more.md', {ip: '192.0.2.1'});
    assert.equal(limited.status, 503);
    assert.equal(limited.json.error, READ_BUSY_ERROR);
    assert.equal(limited.headers['Retry-After'], String(60 - 3));
    assert.equal(fake.requests.length, calls, 'GitHub was not called');
    // A page in the cache is still served, and so is an expired copy.
    assert.equal((await get('/docs/made-up-0.md', {ip: '192.0.2.2'})).status, 200);
    advance(READ_CACHE_MS);
    assert.equal((await get('/docs/made-up-1.md', {ip: '192.0.2.3'})).status, 200, 'an expired copy');
    assert.equal(fake.requests.length, calls);
    // A write still reads the page it changes.
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
    advance(60 * 1000);
    assert.equal((await get('/docs/one-more.md', {ip: '192.0.2.1'})).status, 200, 'the window moved on');
  });

  test('120 reads per minute, then 429', async () => {
    const {get, advance} = setup();
    for (let index = 0; index < 120; index += 1) {
      assert.equal((await get()).status, 200);
    }
    const limited = await get(PAGE);
    assert.equal(limited.status, 429);
    assert.equal(limited.headers['Retry-After'], '60');
    advance(60 * 1000);
    assert.equal((await get()).status, 200);
  });
});

describe('guests (no sign-in)', () => {
  test('a guest likes and unlikes with their guest id; only its hash is stored', async () => {
    const {post, get, fake} = setup();
    const liked = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A});
    assert.equal(liked.status, 200);
    assert.deepEqual(liked.json.likes, {count: 1, likedBy: [], liked: true});
    assert.equal(guestKeyOf(GUEST_A), createHash('sha256').update(GUEST_A).digest('hex').slice(0, 16));
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes, [
      {login: null, guestKey: guestKeyOf(GUEST_A), at: '2026-10-16T12:00:00.000Z'},
    ]);
    assert.ok(!fake.requests.some((request) => request.path === '/user'), 'a guest write never calls GitHub /user');

    // Idempotent, also when the browser sends its id in upper case.
    const puts = () => fake.requests.filter((request) => request.method === 'PUT').length;
    assert.equal(puts(), 1);
    await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A.toUpperCase()});
    assert.equal(puts(), 1, 'liking twice writes nothing');

    // Everyone sees the count; only the guest sees that the like is theirs.
    assert.deepEqual((await get(PAGE, {guestId: GUEST_A})).json.likes, {count: 1, likedBy: [], liked: true});
    assert.deepEqual((await get(PAGE, {guestId: GUEST_B})).json.likes, {count: 1, likedBy: [], liked: false});
    assert.deepEqual((await get(PAGE)).json.likes, {count: 1, likedBy: [], liked: false});

    await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_B});
    assert.deepEqual((await post({action: 'like', page: PAGE})).json.likes, {count: 3, likedBy: ['alice'], liked: true});

    // Unlike only ever removes the caller's own like.
    const stranger = await post({action: 'unlike', page: PAGE}, {token: null, guestId: GUEST_C});
    assert.deepEqual(stranger.json.likes, {count: 3, likedBy: ['alice'], liked: false});
    assert.equal(puts(), 3, "another guest's unlike writes nothing");
    const unliked = await post({action: 'unlike', page: PAGE}, {token: null, guestId: GUEST_A});
    assert.deepEqual(unliked.json.likes, {count: 2, likedBy: ['alice'], liked: false});
    assert.deepEqual(
      fake.readJson(BRANCH, FILE).likes.map((like) => like.login ?? like.guestKey),
      [guestKeyOf(GUEST_B), 'alice'],
    );
    assert.equal(fake.history(BRANCH)[0].message, `community: unlike on ${PAGE}`);
  });

  test('a guest comments as "Guest" and only that guest id can delete it', async () => {
    const {post, get, fake} = setup();
    const created = await post({action: 'comment', page: PAGE, body: '  Nice page  '}, {token: null, guestId: GUEST_A});
    assert.equal(created.status, 200);
    assert.deepEqual(created.json.comment, {
      id: 'comment-1',
      pageId: PAGE,
      author: {login: null, name: GUEST_NAME, avatarUrl: null},
      body: 'Nice page',
      createdAt: '2026-10-16T12:00:00.000Z',
      canDelete: true,
    });
    assert.deepEqual(fake.readJson(BRANCH, FILE).comments, [
      {
        id: 'comment-1',
        login: null,
        name: 'Guest',
        guestKey: guestKeyOf(GUEST_A),
        avatarUrl: null,
        body: 'Nice page',
        createdAt: '2026-10-16T12:00:00.000Z',
      },
    ]);

    // canDelete is worked out for each request.
    assert.equal((await get(PAGE, {guestId: GUEST_A})).json.comments[0].canDelete, true);
    assert.equal((await get(PAGE, {guestId: GUEST_B})).json.comments[0].canDelete, false);
    assert.equal((await get(PAGE)).json.comments[0].canDelete, false);

    const id = created.json.comment.id;
    const otherGuest = await post({action: 'delete', page: PAGE, id}, {token: null, guestId: GUEST_B});
    assert.equal(otherGuest.status, 403);
    assert.equal(otherGuest.json.error, OWN_COMMENTS_ERROR);
    const user = await post({action: 'delete', page: PAGE, id});
    assert.equal(user.status, 403, 'a signed-in user cannot delete a guest comment either');
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, 1);

    const own = await post({action: 'delete', page: PAGE, id}, {token: null, guestId: GUEST_A.toUpperCase()});
    assert.equal(own.status, 200);
    assert.equal(own.json.deleted, true);
    assert.deepEqual(fake.readJson(BRANCH, FILE).comments, []);
    assert.equal(fake.history(BRANCH)[0].message, `community: delete on ${PAGE}`);
  });

  test("a guest cannot delete a user's comment, even by naming the user in the body", async () => {
    const {post, fake} = setup();
    const alices = await post({action: 'comment', page: PAGE, body: 'by alice'});
    const id = alices.json.comment.id;
    for (const extra of [{}, {login: 'alice'}, {author: {login: 'alice'}}]) {
      const response = await post({action: 'delete', page: PAGE, id, ...extra}, {token: null, guestId: GUEST_A});
      assert.equal(response.status, 403, JSON.stringify(extra));
    }
    assert.equal(fake.readJson(BRANCH, FILE).comments.length, 1);
  });

  test('signed in, the guest id still proves which guest comments are yours', async () => {
    const {post, get} = setup();
    const asGuest = await post({action: 'comment', page: PAGE, body: 'before signing in'}, {token: null, guestId: GUEST_A});
    // With a token, new likes and comments are the user's...
    const signedIn = await post({action: 'comment', page: PAGE, body: 'signed in'}, {guestId: GUEST_A});
    assert.equal(signedIn.json.comment.author.login, 'alice');
    assert.deepEqual(signedIn.json.comments.map((comment) => comment.canDelete), [true, true]);
    assert.deepEqual((await get(PAGE, {guestId: GUEST_B})).json.comments.map((comment) => comment.canDelete), [false, false]);
    // ...and the old guest comment can still be deleted with the same guest id.
    assert.equal((await post({action: 'delete', page: PAGE, id: asGuest.json.comment.id})).status, 403, 'not without it');
    assert.equal((await post({action: 'delete', page: PAGE, id: asGuest.json.comment.id}, {guestId: GUEST_A})).status, 200);
  });

  test('nobody can be impersonated through body fields', async () => {
    const {post, fake} = setup();
    const forged = {
      login: 'alice',
      name: 'Alice Example',
      avatarUrl: 'https://avatars.example.test/u/1',
      author: {login: 'alice', name: 'Alice Example'},
      guestKey: guestKeyOf(GUEST_B),
    };
    const comment = await post({action: 'comment', page: PAGE, body: 'hi', ...forged}, {token: null, guestId: GUEST_A});
    assert.equal(comment.status, 200);
    assert.deepEqual(comment.json.comment.author, {login: null, name: 'Guest', avatarUrl: null});
    const stored = fake.readJson(BRANCH, FILE).comments[0];
    assert.deepEqual([stored.login, stored.name, stored.avatarUrl, stored.guestKey], [null, 'Guest', null, guestKeyOf(GUEST_A)]);
    const like = await post({action: 'like', page: PAGE, ...forged}, {token: null, guestId: GUEST_A});
    assert.deepEqual(like.json.likes.likedBy, [], 'a guest like never counts as a user');
    assert.equal(fake.readJson(BRANCH, FILE).likes[0].login, null);

    // A verified user cannot claim another login, and names come from GitHub.
    const asBob = await post({action: 'comment', page: PAGE, body: 'x', login: 'bob'});
    assert.equal(asBob.status, 403);
    const renamed = await post({
      action: 'comment',
      page: PAGE,
      body: 'y',
      name: 'Bob',
      avatarUrl: 'https://evil.example.test/x.png',
      author: {login: 'bob', name: 'Bob'},
    });
    assert.deepEqual(renamed.json.comment.author, {
      login: 'alice',
      name: 'Alice Example',
      avatarUrl: 'https://avatars.example.test/u/1',
    });
  });

  test('the guest id must be a UUID v4; reads ignore a bad one', async () => {
    assert.ok(isGuestId(GUEST_A));
    assert.ok(isGuestId(GUEST_A.toUpperCase()));
    for (const bad of [
      '',
      'guest',
      'not-a-uuid',
      '0f8a5a4e-1b2c-1d3e-8f90-a1b2c3d4e5f6', // version 1
      '0f8a5a4e-1b2c-4d3e-cf90-a1b2c3d4e5f6', // wrong variant
      `${GUEST_A}0`,
      'zf8a5a4e-1b2c-4d3e-8f90-a1b2c3d4e5f6',
      42,
      null,
    ]) {
      assert.equal(isGuestId(bad), false, String(bad));
    }

    const {post, get, fake} = setup();
    for (const guestId of ['not-a-uuid', '0f8a5a4e-1b2c-1d3e-8f90-a1b2c3d4e5f6', `${GUEST_A}-extra`]) {
      const response = await post({action: 'like', page: PAGE}, {token: null, guestId});
      assert.equal(response.status, 400, guestId);
      assert.equal(response.json.error, GUEST_ID_ERROR);
    }
    const empty = await post({action: 'like', page: PAGE}, {token: null, headers: {'x-guest-id': ''}});
    assert.equal(empty.status, 400);
    assert.equal((await post({action: 'like', page: PAGE}, {guestId: 'not-a-uuid'})).status, 400, 'also next to a token');
    assert.equal(fake.requests.length, 0, 'nothing reaches GitHub');

    const read = await get(PAGE, {guestId: 'not-a-uuid'});
    assert.equal(read.status, 200);
    assert.equal(read.json.likes.liked, false);
  });

  test('the raw guest id is never stored, sent to GitHub, returned or logged, and guest keys are never returned', async () => {
    const {post, get, fake, logs} = setup();
    const responses = [];
    responses.push(await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A}));
    const first = await post({action: 'comment', page: PAGE, body: 'hello'}, {token: null, guestId: GUEST_A});
    responses.push(first);
    responses.push(await post({action: 'comment', page: PAGE, body: 'second'}, {token: null, guestId: GUEST_A}));
    responses.push(await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_B}));
    responses.push(await post({action: 'delete', page: PAGE, id: first.json.comment.id}, {token: null, guestId: GUEST_A}));
    responses.push(await post({action: 'delete', page: PAGE, id: 'missing'}, {token: null, guestId: GUEST_A}));
    responses.push(await post({action: 'unlike', page: PAGE}, {token: null, guestId: GUEST_A}));
    responses.push(await get(PAGE, {guestId: GUEST_A}), await get(PAGE, {guestId: GUEST_B}), await get(PAGE));
    fake.hooks.override = ({method}) => (method === 'PUT' ? {status: 500, body: {message: `echo ${GUEST_A}`}} : null);
    responses.push(await post({action: 'comment', page: PAGE, body: 'fails'}, {token: null, guestId: GUEST_A}));
    fake.hooks.override = null;
    assert.equal(responses.at(-1).status, 502);

    // Everything that reached GitHub (file contents decoded), everything on
    // the data branch, every answer and every log line.
    const sent = fake.requests
      .map((request) => {
        const content = request.body?.content ? Buffer.from(request.body.content, 'base64').toString('utf8') : '';
        return `${JSON.stringify(request)}\n${content}`;
      })
      .join('\n');
    const stored = [...fake.branches.values()].flatMap((branch) => [...branch.files.values()].map((file) => file.text)).join('\n');
    const answered = JSON.stringify(responses);
    for (const id of [GUEST_A, GUEST_B]) {
      for (const form of [id, id.toUpperCase(), id.replaceAll('-', '')]) {
        for (const [where, text] of Object.entries({sent, stored, answered, logs: logs.join('\n')})) {
          assert.ok(!text.includes(form), `a raw guest id reached ${where}`);
        }
      }
    }
    assert.ok(stored.includes(guestKeyOf(GUEST_B)), 'only the hash is kept');
    for (const id of [GUEST_A, GUEST_B]) {
      assert.ok(!answered.includes(guestKeyOf(id)), 'a guest key is never returned');
    }
    assert.ok(!answered.includes('guestKey'));
  });
});

describe('session users (resolveSessionUser)', () => {
  const CAROL = {
    login: 'carol',
    id: 3003,
    name: 'Carol Example',
    avatarUrl: 'https://avatars.example.test/u/3',
    htmlUrl: 'https://github.com/carol',
  };
  const COOKIE = {cookie: 'session=fake-session-for-carol'};
  const fromCookie = (request) => (request.headers?.cookie === COOKIE.cookie ? CAROL : null);

  test('the resolver is tried first: its user writes, and the Bearer token is not even checked', async () => {
    const seen = [];
    const {post, fake} = setup({
      resolveSessionUser: async (request) => {
        seen.push(request);
        return fromCookie(request);
      },
    });
    // The site always names the session's user in a session write.
    const liked = await post({action: 'like', page: PAGE, login: 'carol'}, {headers: COOKIE, guestId: GUEST_A}); // and alice's token
    assert.equal(liked.status, 200);
    assert.deepEqual(liked.json.likes, {count: 1, likedBy: ['carol'], liked: true});
    assert.ok(!fake.requests.some((request) => request.path === '/user'), 'the token was not verified');
    assert.equal(seen[0].method, 'POST');
    assert.equal(seen[0].headers.cookie, COOKIE.cookie, 'the resolver gets the request');

    const comment = await post(
      {action: 'comment', page: PAGE, body: 'from a session', login: 'Carol'},
      {token: null, headers: COOKIE},
    );
    assert.deepEqual(comment.json.comment.author, {
      login: 'carol',
      name: 'Carol Example',
      avatarUrl: 'https://avatars.example.test/u/3',
    });
    assert.equal(fake.readJson(BRANCH, FILE).comments[0].login, 'carol');
    assert.equal(fake.readJson(BRANCH, FILE).comments[0].guestKey, undefined);

    // Without a session: the token, and without a token: the guest id.
    assert.deepEqual((await post({action: 'like', page: PAGE})).json.likes.likedBy, ['carol', 'alice']);
    const guest = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A});
    assert.deepEqual(guest.json.likes, {count: 3, likedBy: ['carol', 'alice'], liked: true});
  });

  test("reads show a session user's own like and comments; the body cannot rename them", async () => {
    const {post, get} = setup({resolveSessionUser: fromCookie});
    await post({action: 'like', page: PAGE, login: 'carol'}, {token: null, headers: COOKIE});
    const created = await post({action: 'comment', page: PAGE, body: 'mine', login: 'carol'}, {token: null, headers: COOKIE});
    const mine = await get(PAGE, {headers: COOKIE});
    assert.equal(mine.json.likes.liked, true);
    assert.equal(mine.json.comments[0].canDelete, true);
    const theirs = await get(PAGE, {guestId: GUEST_A});
    assert.equal(theirs.json.likes.liked, false);
    assert.equal(theirs.json.comments[0].canDelete, false);

    const renamed = await post({action: 'comment', page: PAGE, body: 'x', login: 'alice'}, {token: null, headers: COOKIE});
    assert.equal(renamed.status, 403);
    assert.match(renamed.json.error, /signed in as @carol/);
    const forged = await post(
      {action: 'comment', page: PAGE, body: 'y', login: 'carol', name: 'Someone Else', avatarUrl: 'https://evil.example.test/a.png'},
      {token: null, headers: COOKIE},
    );
    assert.equal(forged.json.comment.author.name, 'Carol Example');

    const id = created.json.comment.id;
    assert.equal((await post({action: 'delete', page: PAGE, id}, {token: null, guestId: GUEST_A})).status, 403);
    assert.equal((await post({action: 'delete', page: PAGE, id})).status, 403, 'alice cannot delete it');
    assert.equal((await post({action: 'delete', page: PAGE, id, login: 'carol'}, {token: null, headers: COOKIE})).status, 200);
  });

  test('answers that are not a user are ignored; a failing resolver fails writes, not reads', async () => {
    for (const answer of [undefined, null, {}, {login: 'not a login!'}, {login: 42}, 'carol']) {
      const {post} = setup({resolveSessionUser: async () => answer});
      const response = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A});
      assert.deepEqual(response.json.likes, {count: 1, likedBy: [], liked: true}, JSON.stringify(answer));
    }
    const plain = setup({resolveSessionUser: () => ({login: 'dave', name: '  ', avatarUrl: 'http://insecure.example.test/d.png'})});
    const dave = await plain.post({action: 'comment', page: PAGE, body: 'hi', login: 'dave'}, {token: null});
    assert.deepEqual(dave.json.comment.author, {login: 'dave', name: 'dave', avatarUrl: null});

    const {post, get, fake, logs} = setup({
      resolveSessionUser: () => {
        throw new Error('session store down');
      },
    });
    const write = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A});
    assert.equal(write.status, 500);
    assert.equal(fake.readJson(BRANCH, FILE), undefined, 'not written as a guest instead');
    const read = await get(PAGE, {guestId: GUEST_A});
    assert.equal(read.status, 200);
    assert.ok(logs.some((line) => line.includes('session lookup failed')));
  });

  test('a session write a cross-site page could make is refused with 403 (CSRF)', async () => {
    const {post, fake} = setup({resolveSessionUser: fromCookie});
    const like = JSON.stringify({action: 'like', page: PAGE});
    const refused = [
      ['text/plain', {'content-type': 'text/plain'}],
      ['a form', {'content-type': 'application/x-www-form-urlencoded'}],
      ['no content type', {'content-type': ''}],
      ['a foreign Origin', {origin: 'https://evil.example.test', host: 'docs.example.test'}],
      ['Origin null', {origin: 'null', host: 'docs.example.test'}],
      ['an Origin without a host header', {origin: 'https://docs.example.test'}],
      ['Sec-Fetch-Site: cross-site', {'sec-fetch-site': 'cross-site'}],
    ];
    for (const [label, headers] of refused) {
      const response = await post(like, {token: null, headers: {...COOKIE, ...headers}});
      assert.equal(response.status, 403, label);
      assert.equal(response.json.error, CROSS_SITE_ERROR, label);
    }
    // The platform may hand over a parsed form object: still refused.
    const handled = await setup({resolveSessionUser: fromCookie}).handle({
      method: 'POST',
      query: {},
      headers: {...COOKIE, 'content-type': 'application/x-www-form-urlencoded'},
      body: {action: 'like', page: PAGE},
      ip: '203.0.113.7',
    });
    assert.equal(handled.status, 403);
    assert.equal(fake.readJson(BRANCH, FILE), undefined, 'nothing was written');

    for (const headers of [
      {'content-type': 'application/json; charset=utf-8'},
      {origin: 'https://docs.example.test', host: 'docs.example.test', 'sec-fetch-site': 'same-origin'},
      {origin: 'https://docs.example.test', host: 'internal:3000', 'x-forwarded-host': 'docs.example.test'},
    ]) {
      const response = await post({action: 'like', page: PAGE, login: 'carol'}, {token: null, headers: {...COOKIE, ...headers}});
      assert.equal(response.status, 200, JSON.stringify(headers));
    }
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes.map((item) => item.login), ['carol']);

    // Guests and tokens need a custom header, which already forces a preflight.
    const guest = await post(like, {token: null, guestId: GUEST_A, headers: {'content-type': 'text/plain'}});
    assert.equal(guest.status, 200);
  });

  test('a write the page made as a guest (no login) is refused while the cookie names a user, never published as them', async () => {
    const {post, fake} = setup({resolveSessionUser: fromCookie});
    for (const body of [
      {action: 'like', page: PAGE},
      {action: 'comment', page: PAGE, body: 'meant to be anonymous'},
      {action: 'delete', page: PAGE, id: 'comment-1'},
    ]) {
      const response = await post(body, {token: null, guestId: GUEST_A, headers: COOKIE});
      assert.equal(response.status, 403, body.action);
      assert.equal(response.json.error, 'you are signed in as @carol', body.action);
    }
    assert.equal(fake.readJson(BRANCH, FILE), undefined, 'nothing was written');
    // Without the cookie the same write is a guest one; a token write needs no login.
    assert.equal((await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A})).status, 200);
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
  });

  test('a login that changed hands: the GitHub numeric id decides whose likes and comments they are', async () => {
    // A stale session of the old owner of "carol" (id 3003), and the person
    // who registered "carol" after the rename (id 4004).
    const sessions = {old: CAROL, new: {...CAROL, id: 4004, name: 'New Carol'}};
    const {post, get, fake} = setup({resolveSessionUser: (request) => sessions[request.headers?.['x-test-session']] ?? null});
    const as = (who) => ({token: null, headers: {'x-test-session': who}});

    const oldComment = await post({action: 'comment', page: PAGE, body: 'old owner', login: 'carol'}, as('old'));
    await post({action: 'like', page: PAGE, login: 'carol'}, as('old'));
    const saved = fake.readJson(BRANCH, FILE);
    assert.deepEqual(saved.likes.map(({login, userId}) => ({login, userId})), [{login: 'carol', userId: 3003}]);
    assert.equal(saved.comments[0].userId, 3003);

    const seen = await get(PAGE, as('new'));
    assert.equal(seen.json.likes.liked, false, "the old owner's like is not the new owner's");
    assert.equal(seen.json.comments[0].canDelete, false);
    const notTheirs = await post({action: 'delete', page: PAGE, id: oldComment.json.comment.id, login: 'carol'}, as('new'));
    assert.equal(notTheirs.status, 403);
    assert.equal(notTheirs.json.error, OWN_COMMENTS_ERROR);
    const unliked = await post({action: 'unlike', page: PAGE, login: 'carol'}, as('new'));
    assert.deepEqual(unliked.json.likes, {count: 1, likedBy: ['carol'], liked: false}, 'the unlike removes nothing');

    // The new owner's own like and comment are theirs, not the stale session's.
    const liked = await post({action: 'like', page: PAGE, login: 'carol'}, as('new'));
    assert.deepEqual(liked.json.likes, {count: 2, likedBy: ['carol', 'carol'], liked: true});
    const newComment = await post({action: 'comment', page: PAGE, body: 'new owner', login: 'carol'}, as('new'));
    assert.equal(newComment.json.comment.canDelete, true);
    const stale = await post({action: 'delete', page: PAGE, id: newComment.json.comment.id, login: 'carol'}, as('old'));
    assert.equal(stale.status, 403);
    const staleUnlike = await post({action: 'unlike', page: PAGE, login: 'carol'}, as('old'));
    assert.deepEqual(staleUnlike.json.likes, {count: 1, likedBy: ['carol'], liked: false});
    assert.deepEqual(fake.readJson(BRANCH, FILE).likes.map((like) => like.userId), [4004]);
  });

  test('older records without an id still belong to their login, ignoring case', async () => {
    const fake = createFakeGitHub();
    fake.writeFile(
      BRANCH,
      FILE,
      JSON.stringify({
        version: 1,
        pageId: PAGE,
        likes: [{login: 'Carol', at: '2026-10-01T00:00:00.000Z'}],
        comments: [{id: 'c1', login: 'Carol', name: 'Carol', avatarUrl: null, body: 'old', createdAt: '2026-10-01T00:00:00.000Z'}],
      }),
    );
    const {post, get} = setup({fake, resolveSessionUser: fromCookie});
    const seen = await get(PAGE, {headers: COOKIE});
    assert.equal(seen.json.likes.liked, true);
    assert.equal(seen.json.comments[0].canDelete, true);
    const puts = () => fake.requests.filter((request) => request.method === 'PUT').length;
    await post({action: 'like', page: PAGE, login: 'carol'}, {token: null, headers: COOKIE});
    assert.equal(puts(), 0, 'already liked: nothing written');
    const deleted = await post({action: 'delete', page: PAGE, id: 'c1', login: 'carol'}, {token: null, headers: COOKIE});
    assert.equal(deleted.status, 200);
    // Ids that are not positive integers are dropped when a file is read.
    const file = normalizePageFile(
      {likes: [{login: 'eve', userId: 7, at: 'x'}, {login: 'frank', userId: '8', at: 'x'}, {login: 'gus', userId: -1, at: 'x'}]},
      PAGE,
    );
    assert.deepEqual(file?.likes, [
      {login: 'eve', userId: 7, at: 'x'},
      {login: 'frank', at: 'x'},
      {login: 'gus', at: 'x'},
    ]);
  });
});

describe('rate limits for guests', () => {
  test(`${GUEST_WRITE_LIMIT.max} guest writes per 10 minutes per IP and guest id, then 429`, async () => {
    const {post, advance} = setup();
    for (let index = 0; index < GUEST_WRITE_LIMIT.max; index += 1) {
      const response = await post({action: index % 2 ? 'unlike' : 'like', page: PAGE}, {token: null, guestId: GUEST_A});
      assert.equal(response.status, 200, `guest write ${index + 1}`);
      advance(1000);
    }
    const limited = await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A});
    assert.equal(limited.status, 429);
    assert.equal(limited.headers['Retry-After'], String(10 * 60 - 10));
    assert.match(limited.json.error, /too many requests/);
    // The same guest elsewhere, another guest, and a signed-in user on this IP go on.
    assert.equal((await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A, ip: '198.51.100.9'})).status, 200);
    assert.equal((await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_B})).status, 200);
    assert.equal((await post({action: 'like', page: PAGE})).status, 200);
    advance(10 * 60 * 1000 - 10 * 1000); // 10 min after the first guest write: it leaves the window
    assert.equal((await post({action: 'unlike', page: PAGE}, {token: null, guestId: GUEST_A})).status, 200);
    assert.equal((await post({action: 'like', page: PAGE}, {token: null, guestId: GUEST_A})).status, 429, 'only one slot came free');
  });

  test(`a guest who keeps changing their id still meets the per-IP limit (${WRITE_LIMIT.max})`, async () => {
    const {post} = setup();
    const statuses = [];
    for (let index = 0; index < WRITE_LIMIT.max + 5; index += 1) {
      const guestId = `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
      statuses.push((await post({action: 'like', page: PAGE}, {token: null, guestId})).status);
    }
    assert.deepEqual(statuses, [...Array(WRITE_LIMIT.max).fill(200), ...Array(5).fill(429)]);
  });
});
