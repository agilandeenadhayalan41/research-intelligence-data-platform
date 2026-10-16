// End-to-end smoke test of the Vercel function: a real node:http server on
// 127.0.0.1 mounts the default export of api/community.mjs (as Vercel would),
// and the browser-side shared store talks to it over HTTP. GitHub is the
// in-memory fake: the global fetch is replaced so that api.github.com goes to
// the fake and anything else that is not this local server throws. No request
// leaves the machine.

import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {after, before, describe, test} from 'node:test';

import {createSharedStore, createMemoryStore} from '../src/lib/storage.mjs';
import {ALICE_TOKEN, BOB_TOKEN, SERVER_TOKEN, createFakeGitHub} from './helpers/fakeGitHub.mjs';

const BRANCH = 'community-data';
const PAGE = 'docs/overview/introduction.md';
const API_PAGE = `/${PAGE}`;
const FILE = `community/pages/${createHash('sha256').update(API_PAGE).digest('hex').slice(0, 32)}.json`;

const realFetch = globalThis.fetch;
const github = createFakeGitHub();
let server;
let origin = '';

/** fetch against the local server. */
const local = (path, init) => realFetch(`${origin}${path}`, init);

async function call(method, {page, body, token, raw} = {}) {
  const headers = {'content-type': 'application/json'};
  if (token) {
    headers.authorization = `Bearer ${token}`;
  }
  const response = await local(page === undefined ? '/api/community' : `/api/community?page=${encodeURIComponent(page)}`, {
    method,
    headers,
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  return {status: response.status, headers: response.headers, json: await response.json()};
}

const visitor = (token) =>
  createSharedStore({
    fetchImpl: (url, init) => local(url, init),
    getToken: () => token,
    fallback: createMemoryStore(),
  });

before(async () => {
  process.env.COMMUNITY_GITHUB_TOKEN = SERVER_TOKEN;
  delete process.env.COMMUNITY_REPO;
  delete process.env.COMMUNITY_DATA_BRANCH;
  delete process.env.COMMUNITY_DATA_DIR;
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url));
    if (target.origin === 'https://api.github.com') {
      return github.fetch(String(url), init);
    }
    if (target.origin === origin) {
      return realFetch(url, init);
    }
    throw new Error(`smoke test: unexpected request to ${target.origin}`);
  };
  const {default: handler} = await import('../../../api/community.mjs');
  server = createServer((req, res) => {
    if (new URL(req.url, 'http://localhost').pathname === '/api/community') {
      handler(req, res).catch(() => {
        res.statusCode = 500;
        res.end();
      });
    } else {
      res.statusCode = 404;
      res.end('not found');
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  globalThis.fetch = realFetch;
  delete process.env.COMMUNITY_GITHUB_TOKEN;
  await new Promise((resolve) => server.close(resolve));
});

describe('api/community.mjs over HTTP', () => {
  test('probe and empty page', async () => {
    const probe = await call('GET');
    assert.equal(probe.status, 200);
    assert.deepEqual(probe.json, {configured: true});
    assert.equal(probe.headers.get('cache-control'), 'no-store');
    assert.equal(probe.headers.get('access-control-allow-origin'), null);

    const empty = await call('GET', {page: API_PAGE});
    assert.deepEqual(empty.json, {configured: true, pageId: API_PAGE, likes: {count: 0, likedBy: []}, comments: []});
    assert.equal(github.branches.has(BRANCH), false, 'reads never create the branch');
  });

  test('the first like creates the orphan data branch', async () => {
    const liked = await call('POST', {body: {action: 'like', page: API_PAGE}, token: ALICE_TOKEN});
    assert.equal(liked.status, 200);
    assert.deepEqual(liked.json.likes, {count: 1, likedBy: ['alice']});
    const history = github.history(BRANCH);
    assert.deepEqual(history, [
      {message: `community: like on ${API_PAGE}`, parents: 1},
      {message: `community: create the ${BRANCH} branch`, parents: 0},
    ]);
    assert.deepEqual([...github.branches.get(BRANCH).files.keys()].sort(), ['community/README.md', FILE, 'vercel.json']);
    assert.deepEqual(github.history('main'), [{message: 'initial commit', parents: 0}], 'main is untouched');
  });

  test('like, unlike, comment and delete through the browser store', async () => {
    const alice = visitor(ALICE_TOKEN);
    const bob = visitor(BOB_TOKEN);
    assert.equal(await alice.backend(), 'shared');

    assert.deepEqual(await bob.toggleLike(PAGE, 'bob'), {count: 2, likedBy: ['alice', 'bob']});
    assert.deepEqual(await alice.getLikes(PAGE), {count: 2, likedBy: ['alice', 'bob']});
    assert.deepEqual(await alice.toggleLike(PAGE, 'alice'), {count: 1, likedBy: ['bob']}, 'alice liked already: unlike');

    const first = await alice.addComment(PAGE, {author: {login: 'alice'}, body: 'Shared with everyone'});
    const second = await bob.addComment(PAGE, {author: {login: 'bob'}, body: 'Seen it'});
    assert.equal(first.author.name, 'Alice Example');
    assert.deepEqual(
      (await visitor(null).listComments(PAGE)).map((comment) => [comment.author.login, comment.body]),
      [
        ['alice', 'Shared with everyone'],
        ['bob', 'Seen it'],
      ],
    );

    assert.equal(await bob.deleteComment(PAGE, first.id, 'bob'), false, "bob cannot delete alice's comment");
    assert.equal(await bob.deleteComment(PAGE, second.id, 'bob'), true);
    assert.deepEqual(
      github.readJson(BRANCH, FILE).comments.map((comment) => comment.id),
      [first.id],
    );
  });

  test('a write conflict is retried and both writers win', async () => {
    let injected = false;
    github.hooks.beforePut = ({path}) => {
      if (path === FILE && !injected) {
        injected = true;
        const current = github.readJson(BRANCH, FILE);
        current.likes.push({login: 'carol', at: '2026-10-16T00:00:00.000Z'});
        github.writeFile(BRANCH, FILE, JSON.stringify(current));
      }
    };
    try {
      const response = await call('POST', {body: {action: 'comment', page: API_PAGE, body: 'after a conflict'}, token: ALICE_TOKEN});
      assert.equal(response.status, 200);
      assert.ok(injected);
      assert.deepEqual(response.json.likes.likedBy, ['bob', 'carol']);
      assert.equal(response.json.comments.at(-1).body, 'after a conflict');
      const saved = github.readJson(BRANCH, FILE);
      assert.deepEqual(saved.likes.map((like) => like.login), ['bob', 'carol']);
      assert.equal(saved.comments.at(-1).body, 'after a conflict');
    } finally {
      github.hooks.beforePut = null;
    }
  });

  test('errors over HTTP: 401, 403, 400, 405, 413', async () => {
    const noToken = await call('POST', {body: {action: 'like', page: API_PAGE}});
    assert.equal(noToken.status, 401);
    assert.equal(noToken.json.error, 'sign in with a GitHub token to like or comment');

    const mismatch = await call('POST', {body: {action: 'like', page: API_PAGE, login: 'bob'}, token: ALICE_TOKEN});
    assert.equal(mismatch.status, 403);

    assert.equal((await call('POST', {raw: '{oops', token: ALICE_TOKEN})).status, 400);
    assert.equal((await call('GET', {page: 'no-leading-slash'})).status, 400);

    const deleted = await call('DELETE', {page: API_PAGE});
    assert.equal(deleted.status, 405);
    assert.equal(deleted.headers.get('allow'), 'GET, POST');

    const big = JSON.stringify({action: 'comment', page: API_PAGE, body: 'x'.repeat(20000)});
    const tooLarge = await call('POST', {raw: big, token: ALICE_TOKEN});
    assert.equal(tooLarge.status, 413);

    for (const response of [noToken, mismatch, tooLarge]) {
      const text = JSON.stringify(response.json);
      for (const secret of [SERVER_TOKEN, ALICE_TOKEN, BOB_TOKEN]) {
        assert.ok(!text.includes(secret));
      }
    }
  });

  test('every commit is on the data branch, made with the server token', () => {
    const messages = github.history(BRANCH).map((commit) => commit.message);
    assert.deepEqual(messages.filter((message) => message.startsWith('community: ')).reverse(), [
      `community: create the ${BRANCH} branch`,
      `community: like on ${API_PAGE}`,
      `community: like on ${API_PAGE}`,
      `community: unlike on ${API_PAGE}`,
      `community: comment on ${API_PAGE}`,
      `community: comment on ${API_PAGE}`,
      `community: delete on ${API_PAGE}`,
      `community: comment on ${API_PAGE}`,
    ]);
    for (const request of github.requests) {
      if (request.path === '/user') {
        assert.match(request.auth, /^visitor:/, 'visitor tokens only go to GET /user');
      } else {
        assert.equal(request.auth, 'server', `${request.method} ${request.path}`);
      }
      if (request.method === 'PUT') {
        assert.equal(request.body.branch, BRANCH);
      }
    }
  });
});
