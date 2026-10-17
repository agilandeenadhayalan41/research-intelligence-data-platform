import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  COMMITS_CACHE_TTL_MS,
  DEFAULT_EXCLUDED_AUTHORS,
  GitHubError,
  LOGIN_PATTERN,
  commitsCacheKey,
  fetchPageCommits,
  fetchUser,
  isAutomationAuthor,
  isBlogPostPath,
  newPostTemplate,
  normalizeExcludeList,
  normalizeLogin,
  openBlogDeletePullRequest,
  openBlogPullRequest,
  postFileName,
  preferGitHubHistory,
  repoPathForDoc,
  slugify,
  summarizeCommits,
  utf8ToBase64,
} from '../src/lib/github.mjs';

const API = 'https://api.github.com';
const TOKEN = 'test-token-not-a-real-secret-7f3a9c';
const BASE_SHA = 'a'.repeat(40);
const FILE_SHA = 'b'.repeat(40);

// ---------------------------------------------------------------------------
// Fakes

class FakeStorage {
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
 * A fetch that answers from a script, in order. Each step is
 * {expect: 'METHOD url', status, body, headers, throws}. Every call is
 * recorded; a request that does not match the script gets a 599 answer whose
 * message names the mismatch (assertions inside fetch would be swallowed).
 */
function scriptedFetch(steps) {
  const calls = [];
  const queue = [...steps];
  const fetchImpl = async (url, init = {}) => {
    const headers = new Headers(init.headers ?? {});
    const call = {
      method: init.method ?? 'GET',
      url: String(url),
      headers,
      authorization: headers.get('authorization'),
      body: init.body === undefined ? undefined : JSON.parse(init.body),
    };
    calls.push(call);
    const step = queue.shift();
    const got = `${call.method} ${call.url}`;
    if (!step) {
      return json(599, {message: `mock: unexpected request ${got}`});
    }
    if (step.expect !== got) {
      return json(599, {message: `mock: expected ${step.expect}, got ${got}`});
    }
    if (step.throws) {
      throw step.throws;
    }
    return json(step.status ?? 200, step.body, step.headers);
  };
  return {
    fetchImpl,
    calls,
    assertDone() {
      assert.deepEqual(
        calls.map((call) => `${call.method} ${call.url}`),
        steps.map((step) => step.expect),
      );
    },
  };
}

function json(status, body, headers = {}) {
  const text = body === undefined ? null : typeof body === 'string' ? body : JSON.stringify(body);
  return new Response(status === 204 ? null : text, {
    status,
    headers: {'content-type': 'application/json', ...headers},
  });
}

function commit({sha, login, name, date, committerDate, avatar = true}) {
  return {
    sha,
    html_url: `https://github.com/o/r/commit/${sha}`,
    commit: {
      author: {name, email: 'hidden@example.invalid', date},
      committer: {name: 'GitHub', date: committerDate ?? date},
      message: 'change',
    },
    author: login
      ? {
          login,
          avatar_url: avatar ? `https://avatars.githubusercontent.com/${login}` : null,
          html_url: `https://github.com/${login}`,
        }
      : null,
  };
}

const SAMPLE_COMMITS = [
  commit({sha: 's6', login: 'bob', name: 'Bob B', date: '2026-10-15T12:00:00Z'}),
  commit({sha: 's5', login: 'alice', name: 'Alice A', date: '2026-10-14T12:00:00Z'}),
  commit({sha: 's4', login: null, name: 'Carol C', date: '2026-10-13T12:00:00Z'}),
  commit({sha: 's3', login: 'Bob', name: 'Bob (old name)', date: '2026-10-12T12:00:00Z'}),
  commit({sha: 's2', login: 'alice', name: 'Alice A', date: '2026-10-11T12:00:00Z'}),
  commit({sha: 's1', login: 'dave', name: 'Dave D', date: '2026-10-10T12:00:00Z'}),
];

function assertNoToken(error) {
  assert.ok(!String(error.message).includes(TOKEN), `token leaked in message: ${error.message}`);
  assert.ok(!String(error.stack).includes(TOKEN), 'token leaked in stack');
  assert.ok(!JSON.stringify(error).includes(TOKEN), 'token leaked in error fields');
}

// ---------------------------------------------------------------------------

describe('repoPathForDoc', () => {
  test('maps Docusaurus sources to repository paths', () => {
    assert.equal(repoPathForDoc('@site/../architecture/x.md'), 'docs/architecture/x.md');
    assert.equal(repoPathForDoc('@site/../overview/introduction.md'), 'docs/overview/introduction.md');
    assert.equal(repoPathForDoc('@site/blog/y.md'), 'docs/site/blog/y.md');
    assert.equal(repoPathForDoc('@site/blog/2026-10-16-welcome/index.mdx'), 'docs/site/blog/2026-10-16-welcome/index.mdx');
    assert.equal(repoPathForDoc('@site/src/pages/index.mdx'), 'docs/site/src/pages/index.mdx');
    assert.equal(repoPathForDoc('@site/../../README.md'), 'README.md');
  });

  test('normalises separators, "." and empty segments', () => {
    assert.equal(repoPathForDoc('@site/./blog//y.md'), 'docs/site/blog/y.md');
    assert.equal(repoPathForDoc('@site\\..\\adr\\0001.md'), 'docs/adr/0001.md');
    assert.equal(repoPathForDoc('  @site/../a/b/../c.md  '), 'docs/a/c.md');
  });

  test('honours docsDir', () => {
    assert.equal(repoPathForDoc('@site/../x.md', {docsDir: 'documentation'}), 'documentation/x.md');
    assert.equal(repoPathForDoc('@site/blog/y.md', {docsDir: 'web/docs/'}), 'web/docs/site/blog/y.md');
    assert.equal(repoPathForDoc('@site/blog/y.md', {docsDir: ''}), 'site/blog/y.md');
  });

  test('returns null for other inputs or paths outside the repository', () => {
    assert.equal(repoPathForDoc('@site/../../../outside.md'), null);
    assert.equal(repoPathForDoc('architecture/x.md'), null);
    assert.equal(repoPathForDoc('/abs/path.md'), null);
    assert.equal(repoPathForDoc(''), null);
    assert.equal(repoPathForDoc(undefined), null);
    assert.equal(repoPathForDoc(42), null);
  });
});

describe('summarizeCommits', () => {
  test('groups by login (case-insensitive), most commits first, ties by most recent', () => {
    const {contributors, last} = summarizeCommits(SAMPLE_COMMITS);
    assert.deepEqual(
      contributors.map((c) => [c.login, c.name, c.commits]),
      [
        ['bob', 'Bob B', 2],
        ['alice', 'Alice A', 2],
        [null, 'Carol C', 1],
        ['dave', 'Dave D', 1],
      ],
    );
    assert.deepEqual(contributors[0], {
      login: 'bob',
      name: 'Bob B',
      avatarUrl: 'https://avatars.githubusercontent.com/bob',
      htmlUrl: 'https://github.com/bob',
      commits: 2,
      lastDate: '2026-10-15T12:00:00Z',
      lastCommitUrl: 'https://github.com/o/r/commit/s6',
    });
    assert.deepEqual(last, {
      login: 'bob',
      name: 'Bob B',
      date: '2026-10-15T12:00:00Z',
      sha: 's6',
      htmlUrl: 'https://github.com/o/r/commit/s6',
    });
  });

  test('falls back to the git author name when there is no GitHub user', () => {
    const {contributors, last} = summarizeCommits([
      commit({sha: 'x2', login: null, name: 'Eve', date: '2026-01-02T00:00:00Z'}),
      commit({sha: 'x1', login: null, name: 'eve', date: '2026-01-01T00:00:00Z'}),
      commit({sha: 'x0', login: null, name: 'Frank', date: '2025-12-31T00:00:00Z'}),
    ]);
    assert.deepEqual(contributors, [
      {
        login: null,
        name: 'Eve',
        avatarUrl: null,
        htmlUrl: null,
        commits: 2,
        lastDate: '2026-01-02T00:00:00Z',
        lastCommitUrl: 'https://github.com/o/r/commit/x2',
      },
      {
        login: null,
        name: 'Frank',
        avatarUrl: null,
        htmlUrl: null,
        commits: 1,
        lastDate: '2025-12-31T00:00:00Z',
        lastCommitUrl: 'https://github.com/o/r/commit/x0',
      },
    ]);
    assert.equal(last.login, null);
    assert.equal(last.name, 'Eve');
  });

  test('picks the most recent commit even when the input is not sorted', () => {
    const {last} = summarizeCommits([
      commit({sha: 'old', login: 'a', name: 'A', date: '2025-01-01T00:00:00Z'}),
      commit({sha: 'new', login: 'b', name: 'B', date: '2026-01-01T00:00:00Z'}),
    ]);
    assert.equal(last.sha, 'new');
  });

  test('uses the committer date when the author date is missing', () => {
    const item = commit({sha: 'c', login: 'a', name: 'A', date: undefined, committerDate: '2026-02-02T00:00:00Z'});
    assert.equal(summarizeCommits([item]).last.date, '2026-02-02T00:00:00Z');
  });

  test('handles empty, invalid and partial input', () => {
    assert.deepEqual(summarizeCommits([]), {contributors: [], last: null});
    assert.deepEqual(summarizeCommits(null), {contributors: [], last: null});
    assert.deepEqual(summarizeCommits({message: 'Not Found'}), {contributors: [], last: null});
    assert.deepEqual(summarizeCommits([null, 3, 'x', {}]), {contributors: [], last: null});
    const partial = summarizeCommits([{sha: 'p', commit: {}, author: null}]);
    assert.equal(partial.contributors[0].name, 'Unknown');
    assert.equal(partial.last.date, null);
  });

  test('drops non-https avatar and profile URLs', () => {
    const item = commit({sha: 'z', login: 'a', name: 'A', date: '2026-01-01T00:00:00Z'});
    item.author.avatar_url = 'javascript:alert(1)';
    const [contributor] = summarizeCommits([item]).contributors;
    assert.equal(contributor.avatarUrl, null);
  });
});

describe('fetchPageCommits', () => {
  const base = {owner: 'o', repo: 'r', path: 'docs/architecture/x.md'};
  const url = `${API}/repos/o/r/commits?path=docs/architecture/x.md&per_page=100`;

  test('requests the commits of the path and summarises them', async () => {
    const mock = scriptedFetch([{expect: `GET ${url}`, body: SAMPLE_COMMITS}]);
    const result = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, now: () => 1000});
    mock.assertDone();
    assert.equal(result.ok, true);
    assert.equal(result.cached, false);
    assert.equal(result.fetchedAt, 1000);
    assert.deepEqual(result.data, summarizeCommits(SAMPLE_COMMITS));
    assert.equal(mock.calls[0].authorization, null, 'no token, no Authorization header');
    assert.equal(mock.calls[0].headers.get('accept'), 'application/vnd.github+json');
  });

  test('sends the token as a Bearer header when set', async () => {
    const mock = scriptedFetch([{expect: `GET ${url}`, body: []}]);
    await fetchPageCommits({...base, token: ` ${TOKEN} `, fetchImpl: mock.fetchImpl});
    assert.equal(mock.calls[0].authorization, `Bearer ${TOKEN}`);
  });

  test('encodes owner, repo and path', async () => {
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r/commits?path=docs/a%20b/c%26d%23e.md&per_page=100`, body: []},
    ]);
    const result = await fetchPageCommits({owner: 'o', repo: 'r', path: '/docs/a b/c&d#e.md', fetchImpl: mock.fetchImpl});
    mock.assertDone();
    assert.equal(result.ok, true);
  });

  test('caches successful answers for one hour per path', async () => {
    const cache = new FakeStorage();
    let clock = 1_000_000;
    const now = () => clock;
    const mock = scriptedFetch([
      {expect: `GET ${url}`, body: SAMPLE_COMMITS},
      {expect: `GET ${API}/repos/o/r/commits?path=docs/other.md&per_page=100`, body: []},
      {expect: `GET ${url}`, body: SAMPLE_COMMITS.slice(0, 1)},
    ]);
    const options = {...base, fetchImpl: mock.fetchImpl, cache, now};

    const first = await fetchPageCommits(options);
    assert.equal(first.cached, false);
    assert.ok(cache.getItem(commitsCacheKey('o', 'r', 'docs/architecture/x.md')));

    clock += COMMITS_CACHE_TTL_MS - 1;
    const second = await fetchPageCommits(options);
    assert.equal(second.ok, true);
    assert.equal(second.cached, true);
    assert.equal(second.fetchedAt, 1_000_000);
    assert.deepEqual(second.data, first.data);

    // Another path has its own entry.
    const other = await fetchPageCommits({...options, path: 'docs/other.md'});
    assert.equal(other.cached, false);

    clock += 1; // exactly one hour after the first fetch: expired
    const third = await fetchPageCommits(options);
    assert.equal(third.cached, false);
    assert.equal(third.data.contributors.length, 1);
    mock.assertDone();
  });

  test('accepts a Date clock', async () => {
    const cache = new FakeStorage();
    const mock = scriptedFetch([{expect: `GET ${url}`, body: []}]);
    const result = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, cache, now: () => new Date(5000)});
    assert.equal(result.fetchedAt, 5000);
    const again = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, cache, now: () => new Date(6000)});
    assert.equal(again.cached, true);
  });

  test('ignores corrupted, future-dated or malformed cache entries', async () => {
    const cache = new FakeStorage();
    const key = commitsCacheKey('o', 'r', base.path);
    const entries = [
      '{broken',
      JSON.stringify({fetchedAt: 10_000, data: {contributors: []}}), // in the future
      JSON.stringify({fetchedAt: 'yesterday', data: {contributors: []}}),
      JSON.stringify({fetchedAt: 100, data: {contributors: 'nope'}}),
    ];
    for (const entry of entries) {
      cache.setItem(key, entry);
      const mock = scriptedFetch([{expect: `GET ${url}`, body: []}]);
      const result = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, cache, now: () => 1000});
      mock.assertDone();
      assert.equal(result.cached, false, entry);
    }
  });

  test('works when the cache throws', async () => {
    const cache = {
      getItem() {
        throw new Error('SecurityError');
      },
      setItem() {
        throw new Error('QuotaExceededError');
      },
    };
    const mock = scriptedFetch([{expect: `GET ${url}`, body: SAMPLE_COMMITS}]);
    const result = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, cache});
    assert.equal(result.ok, true);
  });

  test('reports rate limiting (403 with remaining 0, 403 message, 429) and does not cache it', async () => {
    const cache = new FakeStorage();
    const reset = 1_790_000_000;
    const mock = scriptedFetch([
      {
        expect: `GET ${url}`,
        status: 403,
        body: {message: 'API rate limit exceeded for 203.0.113.1.'},
        headers: {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(reset)},
      },
      {expect: `GET ${url}`, status: 403, body: {message: 'You have exceeded a secondary rate limit.'}},
      {expect: `GET ${url}`, status: 429, body: {message: 'Too many requests'}},
      {expect: `GET ${url}`, body: SAMPLE_COMMITS},
    ]);
    const options = {...base, fetchImpl: mock.fetchImpl, cache};

    const first = await fetchPageCommits(options);
    assert.deepEqual(
      {ok: first.ok, error: first.error, status: first.status, resetAt: first.resetAt},
      {ok: false, error: 'rate limited', status: 403, resetAt: new Date(reset * 1000).toISOString()},
    );
    assert.match(first.message, /rate limit/);
    assert.match(first.message, /token/, 'suggests adding a token when there is none');
    assert.equal(cache.length, 0);

    const second = await fetchPageCommits(options);
    assert.equal(second.error, 'rate limited');
    assert.equal(second.resetAt, null);
    const third = await fetchPageCommits(options);
    assert.equal(third.error, 'rate limited');
    assert.equal(third.status, 429);

    const fourth = await fetchPageCommits(options);
    assert.equal(fourth.ok, true);
    mock.assertDone();
  });

  test('maps other failures', async () => {
    const cases = [
      [{status: 404, body: {message: 'Not Found'}}, 'not found'],
      [{status: 401, body: {message: 'Bad credentials'}}, 'bad credentials'],
      [{status: 500, body: {message: 'Server Error'}}, 'http 500'],
      [{status: 403, body: {message: 'Resource not accessible'}}, 'http 403'],
      [{status: 200, body: '<html>not json'}, 'invalid response'],
      [{status: 200, body: {message: 'not a list'}}, 'invalid response'],
      [{throws: new TypeError('Failed to fetch')}, 'network error'],
    ];
    for (const [step, error] of cases) {
      const mock = scriptedFetch([{expect: `GET ${url}`, ...step}]);
      const result = await fetchPageCommits({...base, fetchImpl: mock.fetchImpl, token: TOKEN});
      assert.equal(result.ok, false);
      assert.equal(result.error, error);
      assert.equal(typeof result.message, 'string');
      assert.ok(!JSON.stringify(result).includes(TOKEN));
    }
  });

  test('validates its input without calling GitHub', async () => {
    const mock = scriptedFetch([]);
    for (const options of [{...base, owner: ''}, {...base, repo: undefined}, {...base, path: '  '}]) {
      const result = await fetchPageCommits({...options, fetchImpl: mock.fetchImpl});
      assert.equal(result.error, 'invalid request');
    }
    const badToken = await fetchPageCommits({...base, token: 'has space', fetchImpl: mock.fetchImpl});
    assert.equal(badToken.error, 'invalid token');
    assert.equal(mock.calls.length, 0);
  });
});

describe('fetchUser', () => {
  const profile = {
    login: 'octocat',
    name: 'The Octocat',
    type: 'User',
    avatar_url: 'https://avatars.githubusercontent.com/u/583231?v=4',
    html_url: 'https://github.com/octocat',
    email: null,
  };

  test('returns the public profile', async () => {
    const mock = scriptedFetch([{expect: `GET ${API}/users/octocat`, body: profile}]);
    const result = await fetchUser('octocat', {fetchImpl: mock.fetchImpl});
    mock.assertDone();
    assert.deepEqual(result, {
      ok: true,
      data: {
        login: 'octocat',
        name: 'The Octocat',
        avatarUrl: 'https://avatars.githubusercontent.com/u/583231?v=4',
        htmlUrl: 'https://github.com/octocat',
      },
    });
    assert.equal(mock.calls[0].authorization, null);
  });

  test("uses GitHub's spelling of the login and falls back to it for the name", async () => {
    const mock = scriptedFetch([{expect: `GET ${API}/users/OctoCat`, body: {...profile, name: null}}]);
    const result = await fetchUser('OctoCat', {fetchImpl: mock.fetchImpl});
    assert.equal(result.data.login, 'octocat');
    assert.equal(result.data.name, 'octocat');
  });

  test('accepts @login, spaces and a profile URL', async () => {
    for (const input of ['@octocat', '  octocat  ', 'https://github.com/octocat', 'github.com/octocat/']) {
      const mock = scriptedFetch([{expect: `GET ${API}/users/octocat`, body: profile}]);
      const result = await fetchUser(input, {fetchImpl: mock.fetchImpl});
      assert.equal(result.ok, true, input);
    }
  });

  test('validates the login against the GitHub pattern before any request', async () => {
    const mock = scriptedFetch([]);
    const invalid = ['', ' ', '-octocat', 'octo_cat', 'octo cat', 'a'.repeat(40), 'octo/cat', 'ünïcode', null, 7];
    for (const login of invalid) {
      const result = await fetchUser(login, {fetchImpl: mock.fetchImpl});
      assert.equal(result.ok, false, String(login));
      assert.equal(result.error, 'invalid login');
    }
    assert.equal(mock.calls.length, 0);
    assert.ok(LOGIN_PATTERN.test('a'.repeat(39)));
    assert.ok(LOGIN_PATTERN.test('a-b-1'));
    assert.equal(normalizeLogin('a'.repeat(39)), 'a'.repeat(39));
  });

  test('maps failures', async () => {
    const cases = [
      [{status: 404, body: {message: 'Not Found'}}, 'not found'],
      [{status: 403, body: {message: 'API rate limit exceeded'}, headers: {'x-ratelimit-remaining': '0'}}, 'rate limited'],
      [{status: 429, body: {}}, 'rate limited'],
      [{status: 401, body: {message: 'Bad credentials'}}, 'bad credentials'],
      [{status: 502, body: 'Bad gateway'}, 'http 502'],
      [{status: 200, body: {message: 'odd'}}, 'invalid response'],
      [{status: 200, body: {...profile, type: 'Organization'}}, 'not a user'],
      [{throws: new TypeError('NetworkError when attempting to fetch resource.')}, 'network error'],
    ];
    for (const [step, error] of cases) {
      const mock = scriptedFetch([{expect: `GET ${API}/users/octocat`, ...step}]);
      const result = await fetchUser('octocat', {fetchImpl: mock.fetchImpl});
      assert.equal(result.ok, false);
      assert.equal(result.error, error, JSON.stringify(step));
      assert.ok(result.message.length > 0);
    }
  });

  test('not found names the login', async () => {
    const mock = scriptedFetch([{expect: `GET ${API}/users/nobody-here`, status: 404, body: {}}]);
    const result = await fetchUser('nobody-here', {fetchImpl: mock.fetchImpl});
    assert.match(result.message, /nobody-here/);
  });

  test('sends the token when one is given', async () => {
    const mock = scriptedFetch([{expect: `GET ${API}/users/octocat`, body: profile}]);
    await fetchUser('octocat', {fetchImpl: mock.fetchImpl, token: TOKEN});
    assert.equal(mock.calls[0].authorization, `Bearer ${TOKEN}`);
  });
});

describe('openBlogPullRequest', () => {
  const POST_PATH = 'docs/site/blog/2026-10-16-welcome-post.md';
  const CONTENT = '---\ntitle: "Café — 日本語"\n---\n\nHello 🎉 naïve résumé\n';
  const NOW = () => new Date('2026-10-16T09:30:05Z');
  const BRANCH = 'blog/2026-10-16-welcome-post-20261016093005';
  const CONTENTS = `${API}/repos/o/r/contents/docs/site/blog/2026-10-16-welcome-post.md`;
  const FORK_CONTENTS = `${API}/repos/contrib/r-fork/contents/docs/site/blog/2026-10-16-welcome-post.md`;
  const REF_QUERY = `?ref=${encodeURIComponent(BRANCH)}`;

  const options = (mock, extra = {}) => ({
    owner: 'o',
    repo: 'r',
    base: 'main',
    token: TOKEN,
    path: POST_PATH,
    content: CONTENT,
    title: 'Blog: Welcome post',
    body: 'Adds the welcome post.',
    fetchImpl: mock.fetchImpl,
    now: NOW,
    sleep: async () => {},
    ...extra,
  });

  const decode = (base64) => Buffer.from(base64, 'base64').toString('utf8');

  function assertOnlyGitHubWithToken(mock) {
    for (const call of mock.calls) {
      assert.ok(call.url.startsWith(`${API}/`), call.url);
      assert.equal(call.authorization, `Bearer ${TOKEN}`, call.url);
      assert.ok(!call.url.includes(TOKEN));
      assert.ok(!JSON.stringify(call.body ?? null).includes(TOKEN));
    }
  }

  test('push access: branch in the repository, new file, pull request', async () => {
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r`, body: {full_name: 'o/r', permissions: {push: true}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {ref: 'refs/heads/main', object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {ref: `refs/heads/${BRANCH}`}},
      {expect: `GET ${CONTENTS}${REF_QUERY}`, status: 404, body: {message: 'Not Found'}},
      {expect: `PUT ${CONTENTS}`, status: 201, body: {content: {sha: FILE_SHA}, commit: {sha: 'c'.repeat(40)}}},
      {
        expect: `POST ${API}/repos/o/r/pulls`,
        status: 201,
        body: {html_url: 'https://github.com/o/r/pull/7', number: 7},
      },
    ]);
    const result = await openBlogPullRequest(options(mock));
    mock.assertDone();
    assert.deepEqual(result, {
      url: 'https://github.com/o/r/pull/7',
      number: 7,
      branch: BRANCH,
      head: BRANCH,
      forked: false,
    });

    const [, , createRef, , put, pull] = mock.calls;
    assert.deepEqual(createRef.body, {ref: `refs/heads/${BRANCH}`, sha: BASE_SHA});
    assert.equal(put.body.branch, BRANCH);
    assert.equal(put.body.message, 'Add blog post 2026-10-16-welcome-post.md');
    assert.equal('sha' in put.body, false, 'a new file has no sha');
    assert.equal(decode(put.body.content), CONTENT, 'content is base64 of the UTF-8 bytes');
    assert.equal(put.body.content, Buffer.from(CONTENT, 'utf8').toString('base64'));
    assert.deepEqual(pull.body, {
      title: 'Blog: Welcome post',
      head: BRANCH,
      base: 'main',
      body: 'Adds the welcome post.',
    });
    assert.equal(put.headers.get('content-type'), 'application/json');
    assertOnlyGitHubWithToken(mock);
  });

  test('push access: an existing file is updated with its sha', async () => {
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: true, admin: false}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
      {expect: `GET ${CONTENTS}${REF_QUERY}`, body: {type: 'file', sha: FILE_SHA, path: POST_PATH}},
      {expect: `PUT ${CONTENTS}`, status: 200, body: {}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/8', number: 8}},
    ]);
    const result = await openBlogPullRequest(options(mock, {title: undefined, body: undefined}));
    mock.assertDone();
    assert.equal(result.number, 8);
    const put = mock.calls[4];
    assert.equal(put.body.sha, FILE_SHA);
    assert.equal(put.body.message, 'Update blog post 2026-10-16-welcome-post.md');
    const pull = mock.calls[5];
    assert.equal(pull.body.title, 'Update blog post 2026-10-16-welcome-post.md', 'title defaults to the commit message');
    assert.equal(typeof pull.body.body, 'string');
  });

  test('no push access: fork, wait for it, sync, branch on the fork, PR from the fork', async () => {
    const sleeps = [];
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: false, pull: true}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/forks`, status: 202, body: {name: 'r-fork', owner: {login: 'contrib'}}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, status: 409, body: {message: 'Git Repository is empty.'}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, status: 404, body: {message: 'Not Found'}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/contrib/r-fork/merge-upstream`, body: {merge_type: 'fast-forward'}},
      {expect: `POST ${API}/repos/contrib/r-fork/git/refs`, status: 201, body: {}},
      {expect: `GET ${FORK_CONTENTS}${REF_QUERY}`, status: 404, body: {message: 'Not Found'}},
      {expect: `PUT ${FORK_CONTENTS}`, status: 201, body: {}},
      {
        expect: `POST ${API}/repos/o/r/pulls`,
        status: 201,
        body: {html_url: 'https://github.com/o/r/pull/9', number: 9},
      },
    ]);
    const result = await openBlogPullRequest(
      options(mock, {forkPollIntervalMs: 1500, sleep: async (ms) => void sleeps.push(ms)}),
    );
    mock.assertDone();
    assert.deepEqual(result, {
      url: 'https://github.com/o/r/pull/9',
      number: 9,
      branch: BRANCH,
      head: `contrib:${BRANCH}`,
      forked: true,
    });
    assert.deepEqual(sleeps, [1500, 1500]);
    assert.deepEqual(mock.calls[2].body, {default_branch_only: true});
    assert.deepEqual(mock.calls[6].body, {branch: 'main'});
    assert.deepEqual(mock.calls[7].body, {ref: `refs/heads/${BRANCH}`, sha: BASE_SHA});
    assert.equal(mock.calls[9].body.branch, BRANCH);
    assert.equal(decode(mock.calls[9].body.content), CONTENT);
    assert.deepEqual(mock.calls[10].body, {
      title: 'Blog: Welcome post',
      head: `contrib:${BRANCH}`,
      base: 'main',
      body: 'Adds the welcome post.',
      maintainer_can_modify: true,
    });
    assertOnlyGitHubWithToken(mock);
  });

  test('fork: a failed sync (conflict) does not stop the flow', async () => {
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: false}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/forks`, status: 202, body: {name: 'r-fork', owner: {login: 'contrib'}}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/contrib/r-fork/merge-upstream`, status: 409, body: {message: 'Merge conflict'}},
      {expect: `POST ${API}/repos/contrib/r-fork/git/refs`, status: 201, body: {}},
      {expect: `GET ${FORK_CONTENTS}${REF_QUERY}`, status: 404, body: {}},
      {expect: `PUT ${FORK_CONTENTS}`, status: 201, body: {}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/10', number: 10}},
    ]);
    const result = await openBlogPullRequest(options(mock));
    mock.assertDone();
    assert.equal(result.number, 10);
  });

  test('fork: gives up after the bounded number of polls', async () => {
    const sleeps = [];
    const steps = [
      {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: false}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/forks`, status: 202, body: {name: 'r', owner: {login: 'contrib'}}},
      ...Array.from({length: 3}, () => ({expect: `GET ${API}/repos/contrib/r/git/ref/heads/main`, status: 409, body: {}})),
    ];
    const mock = scriptedFetch(steps);
    await assert.rejects(
      openBlogPullRequest(options(mock, {forkPollAttempts: 3, sleep: async (ms) => void sleeps.push(ms)})),
      (error) => {
        assert.ok(error instanceof GitHubError);
        assert.match(error.message, /still creating your fork contrib\/r/);
        assertNoToken(error);
        return true;
      },
    );
    mock.assertDone();
    assert.equal(sleeps.length, 2, 'sleeps between attempts only');
  });

  test('errors are clear, typed and never contain the token', async () => {
    const repoStep = {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: true}}};
    const refStep = {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}};
    const cases = [
      {
        name: 'bad credentials',
        steps: [{expect: `GET ${API}/repos/o/r`, status: 401, body: {message: `Bad credentials for ${TOKEN}`}}],
        status: 401,
        message: /Reading the repository failed: GitHub rejected the token \(401/,
      },
      {
        name: 'repository not found',
        steps: [{expect: `GET ${API}/repos/o/r`, status: 404, body: {message: 'Not Found'}}],
        status: 404,
        message: /repository o\/r was not found/,
      },
      {
        name: 'base branch missing',
        steps: [repoStep, {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, status: 404, body: {}}],
        status: 404,
        message: /branch "main" does not exist in o\/r/,
      },
      {
        name: 'GitHub echoes the token in an error',
        steps: [
          repoStep,
          refStep,
          {
            expect: `POST ${API}/repos/o/r/git/refs`,
            status: 403,
            body: {message: `Resource not accessible by personal access token ${TOKEN}`},
          },
        ],
        status: 403,
        message: /Creating the branch failed: the token is not allowed to do this \(403: Resource not accessible by personal access token \[token\]\)/,
      },
      {
        name: 'rate limited',
        steps: [
          {
            expect: `GET ${API}/repos/o/r`,
            status: 403,
            body: {message: 'API rate limit exceeded'},
            headers: {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1790000000'},
          },
        ],
        status: 403,
        message: /rate limit was reached \(it resets at 2026-/,
      },
      {
        name: 'pull request already exists',
        steps: [
          repoStep,
          refStep,
          {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
          {expect: `GET ${CONTENTS}${REF_QUERY}`, status: 404, body: {}},
          {expect: `PUT ${CONTENTS}`, status: 201, body: {}},
          {
            expect: `POST ${API}/repos/o/r/pulls`,
            status: 422,
            body: {message: 'Validation Failed', errors: [{message: 'A pull request already exists for o:x.'}]},
          },
        ],
        status: 422,
        message: /Opening the pull request failed: GitHub answered HTTP 422 \(Validation Failed; A pull request already exists/,
      },
      {
        name: 'network failure whose message contains the token',
        steps: [{expect: `GET ${API}/repos/o/r`, throws: new TypeError(`Headers: "Bearer ${TOKEN}" is invalid`)}],
        status: null,
        message: /Reading the repository failed: could not reach GitHub/,
      },
      {
        name: 'path is a folder',
        steps: [
          repoStep,
          refStep,
          {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
          {expect: `GET ${CONTENTS}${REF_QUERY}`, body: [{type: 'file'}]},
        ],
        status: null,
        message: /is a folder/,
      },
      {
        name: 'no base sha',
        steps: [repoStep, {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {}}}],
        status: null,
        message: /no commit for branch "main"/,
      },
      {
        name: 'pull request answer without a URL',
        steps: [
          repoStep,
          refStep,
          {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
          {expect: `GET ${CONTENTS}${REF_QUERY}`, status: 404, body: {}},
          {expect: `PUT ${CONTENTS}`, status: 201, body: {}},
          {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {}},
        ],
        status: null,
        message: /did not return the new pull request/,
      },
    ];
    for (const item of cases) {
      const mock = scriptedFetch(item.steps);
      await assert.rejects(openBlogPullRequest(options(mock)), (error) => {
        assert.ok(error instanceof GitHubError, item.name);
        assert.match(error.message, item.message, item.name);
        assert.equal(error.status, item.status, item.name);
        assertNoToken(error);
        return true;
      });
      mock.assertDone();
    }
  });

  test('validates its input before calling GitHub', async () => {
    const mock = scriptedFetch([]);
    const invalid = [
      [{token: ''}, /A GitHub token is required/],
      [{token: undefined}, /A GitHub token is required/],
      [{token: `${TOKEN} extra`}, /not allowed/],
      [{token: `${TOKEN}\n`.repeat(2)}, /not allowed/],
      [{owner: 'o/../x'}, /owner and name/],
      [{repo: ''}, /owner and name/],
      [{base: '../main'}, /not a valid branch name/],
      [{path: '../secrets.md'}, /relative repository path/],
      [{path: '/docs/site/blog/x.md'}, /relative repository path/],
      [{path: 'docs/site/blog/x.txt'}, /relative repository path/],
      [{path: 'docs\\site\\blog\\x.md'}, /relative repository path/],
      [{content: undefined}, /must be text/],
    ];
    for (const [extra, message] of invalid) {
      await assert.rejects(openBlogPullRequest(options(mock, extra)), (error) => {
        assert.ok(error instanceof GitHubError);
        assert.match(error.message, message, JSON.stringify(extra));
        assertNoToken(error);
        return true;
      });
    }
    assert.equal(mock.calls.length, 0);
  });

  test('branch slug falls back to "post" and uses the UTC timestamp', async () => {
    const mock = scriptedFetch([
      {expect: `GET ${API}/repos/o/r`, body: {permissions: {push: true}}},
      {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
      {expect: `GET ${API}/repos/o/r/contents/docs/site/blog/%E6%97%A5%E6%9C%AC.mdx?ref=blog%2Fpost-20261231235959`, status: 404, body: {}},
      {expect: `PUT ${API}/repos/o/r/contents/docs/site/blog/%E6%97%A5%E6%9C%AC.mdx`, status: 201, body: {}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/1', number: 1}},
    ]);
    const result = await openBlogPullRequest(
      options(mock, {path: 'docs/site/blog/日本.mdx', now: () => new Date('2026-12-31T23:59:59Z')}),
    );
    mock.assertDone();
    assert.equal(result.branch, 'blog/post-20261231235959');
  });
});

describe('slugify', () => {
  test('makes URL-safe slugs', () => {
    assert.equal(slugify('Hello, World!'), 'hello-world');
    assert.equal(slugify('  Welcome to the blog  '), 'welcome-to-the-blog');
    assert.equal(slugify("Don't panic: it's fine"), 'dont-panic-its-fine');
    assert.equal(slugify('Café naïve résumé'), 'cafe-naive-resume');
    assert.equal(slugify('Straße Øresund Æther'), 'strasse-oresund-aether');
    assert.equal(slugify('BigQuery & OpenAlex -- v2.0'), 'bigquery-openalex-v2-0');
    assert.equal(slugify('2026-10-16-welcome'), '2026-10-16-welcome');
  });

  test('returns an empty string when nothing is usable', () => {
    assert.equal(slugify(''), '');
    assert.equal(slugify('!!!'), '');
    assert.equal(slugify('日本語'), '');
    assert.equal(slugify(undefined), '');
  });

  test('caps the length at 60 characters, preferably at a word boundary', () => {
    const slug = slugify('A very long title that keeps going and going well past the sixty character limit');
    assert.ok(slug.length <= 60, slug);
    assert.ok(!slug.endsWith('-'));
    assert.equal(slug, 'a-very-long-title-that-keeps-going-and-going-well-past-the');
    assert.equal(slugify('x'.repeat(100)).length, 60);
  });
});

describe('newPostTemplate and postFileName', () => {
  const frontMatter = (source) => {
    const match = /^---\n([\s\S]*?)\n---\n/.exec(source);
    assert.ok(match, 'starts with front matter');
    return Object.fromEntries(
      match[1].split('\n').map((line) => {
        const at = line.indexOf(': ');
        return [line.slice(0, at), line.slice(at + 2)];
      }),
    );
  };

  test('md template: front matter title, description, tags, date and the md truncate marker', () => {
    const source = newPostTemplate({title: 'Welcome to the blog', format: 'md', date: '2026-10-16'});
    assert.deepEqual(Object.keys(frontMatter(source)), ['title', 'description', 'tags', 'date']);
    const fields = frontMatter(source);
    assert.equal(JSON.parse(fields.title), 'Welcome to the blog');
    assert.equal(typeof JSON.parse(fields.description), 'string');
    assert.equal(fields.tags, '[]');
    assert.equal(fields.date, '2026-10-16');
    assert.ok(source.includes('\n<!-- truncate -->\n'));
    assert.ok(!source.includes('{/*'));
    assert.ok(source.endsWith('\n'));
  });

  test('mdx template uses the MDX truncate marker', () => {
    const source = newPostTemplate({title: 'X', format: 'mdx', date: '2026-10-16'});
    assert.ok(source.includes('\n{/* truncate */}\n'));
    assert.ok(!source.includes('<!--'), 'HTML comments are invalid in MDX');
  });

  test('quotes titles safely for YAML', () => {
    const title = 'Release: "v2" #1 — it\'s here';
    const fields = frontMatter(newPostTemplate({title, date: '2026-10-16'}));
    assert.equal(JSON.parse(fields.title), title);
    const multiline = frontMatter(newPostTemplate({title: 'line one\nline two', date: '2026-10-16'}));
    assert.equal(JSON.parse(multiline.title), 'line one line two');
  });

  test('defaults: Untitled post, md, today', () => {
    const source = newPostTemplate();
    const fields = frontMatter(source);
    assert.equal(JSON.parse(fields.title), 'Untitled post');
    assert.match(fields.date, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(source.includes('<!-- truncate -->'));
  });

  test('a Date gives its local calendar day', () => {
    const fields = frontMatter(newPostTemplate({title: 't', date: new Date(2026, 0, 5, 23, 30)}));
    assert.equal(fields.date, '2026-01-05');
  });

  test('rejects an unknown format or an invalid date', () => {
    assert.throws(() => newPostTemplate({format: 'html'}), {message: "format must be 'md' or 'mdx'"});
    assert.throws(() => newPostTemplate({date: '16/10/2026'}), /date must be/);
    assert.throws(() => newPostTemplate({date: '2026-02-30'}), /date must be/);
    assert.throws(() => newPostTemplate({date: new Date('nope')}), /date must be/);
  });

  test('postFileName is date + slug + extension', () => {
    assert.equal(postFileName({title: 'Welcome to the blog', date: '2026-10-16'}), '2026-10-16-welcome-to-the-blog.md');
    assert.equal(postFileName({title: 'MDX demo', format: 'mdx', date: '2026-10-16'}), '2026-10-16-mdx-demo.mdx');
    assert.equal(postFileName({title: '日本語', date: '2026-10-16'}), '2026-10-16-post.md');
    assert.equal(postFileName({date: '2026-10-16'}), '2026-10-16-untitled-post.md');
  });
});

describe('utf8ToBase64', () => {
  test('matches Node for ASCII, accents, CJK, emoji and large input', () => {
    const samples = ['', 'hello', 'Café — naïve', '日本語テキスト', '🎉🚀 emoji', 'x'.repeat(100_000) + '✓'];
    for (const sample of samples) {
      assert.equal(utf8ToBase64(sample), Buffer.from(sample, 'utf8').toString('base64'));
    }
  });
});

// ---------------------------------------------------------------------------
// Page history: people only, never bots or AI tools

/** A commit whose author can be a bot: GitHub account type and git email. */
function authored({sha, login = null, name, date, email = 'person@example.invalid', type = 'User'}) {
  const item = commit({sha, login, name, date});
  item.commit.author.email = email;
  if (item.author) {
    item.author.type = type;
  }
  return item;
}

describe('isAutomationAuthor', () => {
  test('a GitHub Bot account is automation, whatever its name', () => {
    assert.equal(isAutomationAuthor({login: 'some-helper', name: 'Helper', type: 'Bot'}), true);
    assert.equal(isAutomationAuthor({login: 'Copilot', type: 'Bot'}), true);
  });

  test('a login or name ending in [bot] is automation', () => {
    assert.equal(isAutomationAuthor({login: 'dependabot[bot]'}), true);
    assert.equal(isAutomationAuthor({login: 'anything-new[BOT]', type: 'User'}), true);
    assert.equal(isAutomationAuthor({login: null, name: 'release-helper[bot]'}), true);
  });

  test('the exclude list matches login or name case-insensitively, ignoring [bot] and separators', () => {
    assert.equal(isAutomationAuthor({login: 'Claude'}), true);
    assert.equal(isAutomationAuthor({name: 'CLAUDE'}), true);
    assert.equal(isAutomationAuthor({name: 'Claude Code'}), true, 'matches claude-code');
    assert.equal(isAutomationAuthor({name: 'Cursor Agent'}), true, 'matches cursoragent');
    assert.equal(isAutomationAuthor({login: 'github-actions'}), true);
    assert.equal(isAutomationAuthor({login: 'CodeRabbitAI'}), true);
    assert.equal(isAutomationAuthor({name: 'Ada'}, ['ADA[bot]']), true, 'list entries are normalised too');
    assert.equal(isAutomationAuthor({name: 'Claude'}, []), false, 'an empty list excludes no name');
    assert.equal(isAutomationAuthor({name: 'Ada'}, ['grace']), false);
  });

  test('Anthropic and GitHub bot emails are automation; the email is only checked', () => {
    assert.equal(isAutomationAuthor({name: 'Someone', email: 'noreply@anthropic.com'}), true);
    assert.equal(isAutomationAuthor({name: 'Someone', email: 'Agent@Anthropic.com '}), true);
    assert.equal(
      isAutomationAuthor({name: 'Someone', email: '49699333+dependabot[bot]@users.noreply.github.com'}),
      true,
    );
    assert.equal(isAutomationAuthor({name: 'Someone', email: '123+someone@users.noreply.github.com'}), false);
    assert.equal(isAutomationAuthor({name: 'Someone', email: 'someone@anthropic.com.example'}), false);
  });

  test('people are kept', () => {
    for (const author of [
      {login: 'alice', name: 'Alice A', email: 'alice@example.invalid', type: 'User'},
      {login: null, name: 'Bob B'},
      {login: 'claudette', name: 'Claudette'},
      {login: 'copilot-fan', name: 'Rob Bot'},
      {},
    ]) {
      assert.equal(isAutomationAuthor(author), false, JSON.stringify(author));
    }
    assert.equal(isAutomationAuthor(null), false);
    assert.equal(isAutomationAuthor(undefined), false);
  });

  test('normalizeExcludeList: default list, normalised entries, junk dropped', () => {
    assert.deepEqual(normalizeExcludeList(undefined), normalizeExcludeList(DEFAULT_EXCLUDED_AUTHORS));
    assert.ok(normalizeExcludeList(undefined).includes('claudecode'));
    assert.deepEqual(normalizeExcludeList([' My-Bot[bot] ', 'my_bot', 7, '', null, 'Other']), ['mybot', 'other']);
    assert.deepEqual(normalizeExcludeList([]), []);
    assert.deepEqual(normalizeExcludeList('claude'), normalizeExcludeList(DEFAULT_EXCLUDED_AUTHORS));
  });

  test('the default list is the documented one', () => {
    assert.deepEqual(
      [...DEFAULT_EXCLUDED_AUTHORS],
      [
        'claude',
        'claude-code',
        'anthropic-claude',
        'copilot',
        'github-copilot',
        'copilot-swe-agent',
        'github-actions',
        'dependabot',
        'renovate',
        'vercel',
        'cursor',
        'cursoragent',
        'devin-ai-integration',
        'codex',
        'chatgpt-codex-connector',
        'gemini-code-assist',
        'sweep-ai',
        'coderabbitai',
      ],
    );
    assert.ok(Object.isFrozen(DEFAULT_EXCLUDED_AUTHORS));
  });
});

describe('summarizeCommits without automation', () => {
  const MIXED = [
    authored({sha: 'm6', login: 'claude', name: 'Claude', date: '2026-10-16T12:00:00Z', email: 'noreply@anthropic.com'}),
    authored({sha: 'm5', login: 'dependabot[bot]', name: 'dependabot[bot]', date: '2026-10-15T12:00:00Z', type: 'Bot'}),
    authored({sha: 'm4', login: 'alice', name: 'Alice A', date: '2026-10-14T12:00:00Z'}),
    authored({sha: 'm3', login: null, name: 'Claude', date: '2026-10-13T12:00:00Z', email: 'noreply@anthropic.com'}),
    authored({sha: 'm2', login: 'helper', name: 'Helper', date: '2026-10-12T12:00:00Z', type: 'Bot'}),
    authored({sha: 'm1', login: 'alice', name: 'Alice A', date: '2026-10-11T12:00:00Z'}),
    authored({sha: 'm0', login: null, name: 'Bob B', date: '2026-10-10T12:00:00Z'}),
  ];

  test('credits people only; the last change is the latest commit by a person', () => {
    const summary = summarizeCommits(MIXED);
    assert.deepEqual(
      summary.contributors.map((c) => [c.login, c.name, c.commits]),
      [
        ['alice', 'Alice A', 2],
        [null, 'Bob B', 1],
      ],
    );
    assert.equal(summary.last.login, 'alice');
    assert.equal(summary.last.sha, 'm4');
    assert.equal(summary.last.date, '2026-10-14T12:00:00Z');
    assert.deepEqual(summary.automation, {
      commits: 4,
      lastDate: '2026-10-16T12:00:00Z',
      lastSha: 'm6',
      htmlUrl: 'https://github.com/o/r/commit/m6',
    });
    const text = JSON.stringify(summary);
    for (const hidden of ['Claude', 'claude', 'dependabot', 'Helper', 'helper', 'anthropic', 'example.invalid']) {
      assert.ok(!text.includes(hidden), `${hidden} must not appear in the summary`);
    }
  });

  test('a page changed only by automation: no people, no last change, the date of the latest commit', () => {
    const summary = summarizeCommits(MIXED.filter((item) => ['m6', 'm5', 'm3', 'm2'].includes(item.sha)));
    assert.deepEqual(summary.contributors, []);
    assert.equal(summary.last, null);
    assert.equal(summary.automation.commits, 4);
    assert.equal(summary.automation.lastDate, '2026-10-16T12:00:00Z');
  });

  test('a custom exclude list replaces the default one', () => {
    const summary = summarizeCommits(MIXED, {excludeAuthors: ['alice']});
    assert.deepEqual(
      summary.contributors.map((c) => c.name),
      ['Bob B'],
      'Claude stays out by email, the Bot accounts by type, alice by the list',
    );
    const named = summarizeCommits(
      [authored({sha: 'n1', login: null, name: 'Claude', date: '2026-10-16T12:00:00Z', email: 'c@example.invalid'})],
      {excludeAuthors: []},
    );
    assert.equal(named.contributors[0].name, 'Claude', 'with an empty list only the bot rules apply');
  });

  test('fetchPageCommits filters, and does not reuse a summary cached with another exclude list', async () => {
    const cache = new FakeStorage();
    const url = `${API}/repos/o/r/commits?path=docs/x.md&per_page=100`;
    const mock = scriptedFetch([
      {expect: `GET ${url}`, body: MIXED},
      {expect: `GET ${url}`, body: MIXED},
    ]);
    const options = {owner: 'o', repo: 'r', path: 'docs/x.md', fetchImpl: mock.fetchImpl, cache, now: () => 1000};

    const first = await fetchPageCommits(options);
    assert.equal(first.ok, true);
    assert.deepEqual(first.data.contributors.map((c) => c.name), ['Alice A', 'Bob B']);
    assert.equal(first.data.automation.commits, 4);
    assert.equal((await fetchPageCommits(options)).cached, true, 'same list: cached');

    const other = await fetchPageCommits({...options, excludeAuthors: ['bob b']});
    assert.equal(other.cached, false, 'another list: asked again');
    assert.deepEqual(other.data.contributors.map((c) => c.name), ['Alice A']);
    mock.assertDone();
    const cachedAgain = await fetchPageCommits({...options, excludeAuthors: ['bob b']});
    assert.equal(cachedAgain.cached, true);
    assert.deepEqual(cachedAgain.data.automation, other.data.automation);
  });
});

// ---------------------------------------------------------------------------
// Deleting a blog post through a pull request

describe('openBlogDeletePullRequest', () => {
  const POST_PATH = 'docs/site/blog/2026-10-16-welcome-post.md';
  const NOW = () => new Date('2026-10-16T09:30:05Z');
  const BRANCH = 'blog/delete-2026-10-16-welcome-post-20261016093005';
  const POST_SHA = 'd'.repeat(40);
  const UPSTREAM_CONTENTS = `${API}/repos/o/r/contents/docs/site/blog/2026-10-16-welcome-post.md`;
  const FORK_CONTENTS = `${API}/repos/contrib/r-fork/contents/docs/site/blog/2026-10-16-welcome-post.md`;
  const AT_BASE = `?ref=${BASE_SHA}`;

  const options = (mock, extra = {}) => ({
    owner: 'o',
    repo: 'r',
    base: 'main',
    token: TOKEN,
    path: POST_PATH,
    title: 'Welcome post',
    body: 'Deletes the welcome post.',
    fetchImpl: mock.fetchImpl,
    now: NOW,
    sleep: async () => {},
    ...extra,
  });

  const repoStep = (push) => ({expect: `GET ${API}/repos/o/r`, body: {permissions: {push}}});
  const refStep = {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}};
  const fileStep = {
    expect: `GET ${UPSTREAM_CONTENTS}${AT_BASE}`,
    body: {type: 'file', path: POST_PATH, sha: POST_SHA},
  };

  function assertOnlyGitHubWithToken(mock) {
    for (const call of mock.calls) {
      assert.ok(call.url.startsWith(`${API}/`), call.url);
      assert.equal(call.authorization, `Bearer ${TOKEN}`, call.url);
      assert.ok(!call.url.includes(TOKEN));
      assert.ok(!JSON.stringify(call.body ?? null).includes(TOKEN));
    }
  }

  test('push access: finds the post, branches in the repository, deletes it, opens the pull request', async () => {
    const mock = scriptedFetch([
      repoStep(true),
      refStep,
      fileStep,
      {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {ref: `refs/heads/${BRANCH}`}},
      {expect: `DELETE ${UPSTREAM_CONTENTS}`, status: 200, body: {content: null, commit: {sha: 'c'.repeat(40)}}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/21', number: 21}},
    ]);
    const result = await openBlogDeletePullRequest(options(mock));
    mock.assertDone();
    assert.deepEqual(result, {
      url: 'https://github.com/o/r/pull/21',
      number: 21,
      branch: BRANCH,
      head: BRANCH,
      forked: false,
    });
    const [, , , createRef, remove, pull] = mock.calls;
    assert.deepEqual(createRef.body, {ref: `refs/heads/${BRANCH}`, sha: BASE_SHA});
    assert.deepEqual(remove.body, {
      message: 'Delete blog post 2026-10-16-welcome-post.md',
      sha: POST_SHA,
      branch: BRANCH,
    });
    assert.equal(remove.headers.get('content-type'), 'application/json');
    assert.deepEqual(pull.body, {
      title: 'Delete blog post: Welcome post',
      head: BRANCH,
      base: 'main',
      body: 'Deletes the welcome post.',
    });
    assertOnlyGitHubWithToken(mock);
  });

  test('no push access: fork, wait for it, sync, branch and delete on the fork, PR from the fork', async () => {
    const sleeps = [];
    const mock = scriptedFetch([
      repoStep(false),
      refStep,
      fileStep,
      {expect: `POST ${API}/repos/o/r/forks`, status: 202, body: {name: 'r-fork', owner: {login: 'contrib'}}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, status: 409, body: {message: 'Git Repository is empty.'}},
      {expect: `GET ${API}/repos/contrib/r-fork/git/ref/heads/main`, body: {object: {sha: BASE_SHA}}},
      {expect: `POST ${API}/repos/contrib/r-fork/merge-upstream`, status: 409, body: {message: 'Merge conflict'}},
      {expect: `POST ${API}/repos/contrib/r-fork/git/refs`, status: 201, body: {}},
      {expect: `DELETE ${FORK_CONTENTS}`, status: 200, body: {}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/22', number: 22}},
    ]);
    const result = await openBlogDeletePullRequest(
      options(mock, {title: undefined, body: undefined, forkPollIntervalMs: 750, sleep: async (ms) => void sleeps.push(ms)}),
    );
    mock.assertDone();
    assert.deepEqual(result, {
      url: 'https://github.com/o/r/pull/22',
      number: 22,
      branch: BRANCH,
      head: `contrib:${BRANCH}`,
      forked: true,
    });
    assert.deepEqual(sleeps, [750]);
    assert.deepEqual(mock.calls[3].body, {default_branch_only: true});
    assert.deepEqual(mock.calls[7].body, {ref: `refs/heads/${BRANCH}`, sha: BASE_SHA});
    assert.deepEqual(mock.calls[8].body, {
      message: 'Delete blog post 2026-10-16-welcome-post.md',
      sha: POST_SHA,
      branch: BRANCH,
    });
    const pull = mock.calls[9].body;
    assert.equal(pull.title, 'Delete blog post: 2026-10-16-welcome-post.md', 'no title: the file name');
    assert.equal(pull.head, `contrib:${BRANCH}`);
    assert.equal(pull.maintainer_can_modify, true);
    assert.match(pull.body, /docs\/site\/blog\/2026-10-16-welcome-post\.md/);
    assertOnlyGitHubWithToken(mock);
  });

  test('a post that is not on the base branch: "post not found on main", and no branch is made', async () => {
    const mock = scriptedFetch([
      repoStep(true),
      refStep,
      {expect: `GET ${UPSTREAM_CONTENTS}${AT_BASE}`, status: 404, body: {message: `Not Found ${TOKEN}`}},
    ]);
    await assert.rejects(openBlogDeletePullRequest(options(mock)), (error) => {
      assert.ok(error instanceof GitHubError);
      assert.match(error.message, /post not found on main/);
      assert.equal(error.status, 404);
      assertNoToken(error);
      return true;
    });
    mock.assertDone();
  });

  test('a folder, or an answer without a sha, stops before the branch is made', async () => {
    for (const body of [[{type: 'file', sha: POST_SHA}], {type: 'dir', sha: POST_SHA}, {type: 'file'}]) {
      const mock = scriptedFetch([repoStep(true), refStep, {expect: `GET ${UPSTREAM_CONTENTS}${AT_BASE}`, body}]);
      await assert.rejects(openBlogDeletePullRequest(options(mock)), (error) => {
        assert.ok(error instanceof GitHubError, JSON.stringify(body));
        assert.match(error.message, /is a folder|no version of/);
        assertNoToken(error);
        return true;
      });
      mock.assertDone();
    }
  });

  test('errors are clear and never contain the token', async () => {
    const cases = [
      {
        name: 'bad credentials',
        steps: [{expect: `GET ${API}/repos/o/r`, status: 401, body: {message: `Bad credentials ${TOKEN}`}}],
        message: /GitHub rejected the token/,
      },
      {
        name: 'GitHub echoes the token when the delete is refused',
        steps: [
          repoStep(true),
          refStep,
          fileStep,
          {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
          {expect: `DELETE ${UPSTREAM_CONTENTS}`, status: 409, body: {message: `sha does not match (${TOKEN})`}},
        ],
        message: /Deleting the post on the branch failed: GitHub answered HTTP 409 \(sha does not match \(\[token\]\)\)/,
      },
      {
        name: 'network failure whose message contains the token',
        steps: [repoStep(true), {expect: `GET ${API}/repos/o/r/git/ref/heads/main`, throws: new TypeError(`bad header Bearer ${TOKEN}`)}],
        message: /could not reach GitHub/,
      },
      {
        name: 'pull request already exists',
        steps: [
          repoStep(true),
          refStep,
          fileStep,
          {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
          {expect: `DELETE ${UPSTREAM_CONTENTS}`, status: 200, body: {}},
          {expect: `POST ${API}/repos/o/r/pulls`, status: 422, body: {message: 'Validation Failed'}},
        ],
        message: /Opening the pull request failed: GitHub answered HTTP 422/,
      },
    ];
    for (const item of cases) {
      const mock = scriptedFetch(item.steps);
      await assert.rejects(openBlogDeletePullRequest(options(mock)), (error) => {
        assert.ok(error instanceof GitHubError, item.name);
        assert.match(error.message, item.message, item.name);
        assertNoToken(error);
        return true;
      });
      mock.assertDone();
    }
  });

  test('only blog posts can be deleted; every input is checked before any request', async () => {
    const mock = scriptedFetch([]);
    const invalid = [
      [{path: 'docs/architecture/roadmap.md'}, /Only blog posts can be deleted/],
      [{path: 'docs/site/blog/'}, /Only blog posts can be deleted/],
      [{path: 'docs/site/blog/x.txt'}, /Only blog posts can be deleted/],
      [{path: 'docs/site/blog/../../README.md'}, /Only blog posts can be deleted/],
      [{path: 'docs/site/blog/./x.md'}, /Only blog posts can be deleted/],
      [{path: '/docs/site/blog/x.md'}, /Only blog posts can be deleted/],
      [{path: 'docs\\site\\blog\\x.md'}, /Only blog posts can be deleted/],
      [{path: 'Docs/site/blog/x.md'}, /Only blog posts can be deleted/],
      [{path: 'docs/site/blogger/x.md'}, /Only blog posts can be deleted/],
      [{path: undefined}, /Only blog posts can be deleted/],
      [{token: ''}, /A GitHub token is required.*account menu/],
      [{token: `${TOKEN} extra`}, /not allowed/],
      [{owner: 'o/../x'}, /owner and name/],
      [{base: '../main'}, /not a valid branch name/],
    ];
    for (const [extra, message] of invalid) {
      await assert.rejects(openBlogDeletePullRequest(options(mock, extra)), (error) => {
        assert.ok(error instanceof GitHubError);
        assert.match(error.message, message, JSON.stringify(extra));
        assertNoToken(error);
        return true;
      });
    }
    assert.equal(mock.calls.length, 0);
  });

  test('isBlogPostPath', () => {
    assert.equal(isBlogPostPath('docs/site/blog/2026-10-16-welcome-to-the-blog.md'), true);
    assert.equal(isBlogPostPath('docs/site/blog/folder/post.mdx'), true);
    assert.equal(isBlogPostPath('docs/site/blog/post.MD'), true);
    for (const path of ['docs/site/blog/', 'docs/site/blog/x.js', 'docs/overview/status.md', 'docs/site/blog/../x.md', 42, null]) {
      assert.equal(isBlogPostPath(path), false, String(path));
    }
  });

  test('a long or multi-line title is cleaned up for the pull request title', async () => {
    const mock = scriptedFetch([
      repoStep(true),
      refStep,
      fileStep,
      {expect: `POST ${API}/repos/o/r/git/refs`, status: 201, body: {}},
      {expect: `DELETE ${UPSTREAM_CONTENTS}`, status: 200, body: {}},
      {expect: `POST ${API}/repos/o/r/pulls`, status: 201, body: {html_url: 'https://github.com/o/r/pull/23', number: 23}},
    ]);
    await openBlogDeletePullRequest(options(mock, {title: `  A\n\ttitle ${'x'.repeat(300)}  `}));
    mock.assertDone();
    const {title} = mock.calls[5].body;
    assert.ok(title.startsWith('Delete blog post: A title xxx'), title);
    assert.ok(title.length <= 'Delete blog post: '.length + 200, String(title.length));
    assert.ok(!/[\n\t]/.test(title));
  });
});

describe('preferGitHubHistory', () => {
  const person = {login: 'ada', name: 'Ada', commits: 1};
  const bots = {commits: 2, lastDate: '2026-10-16T10:00:00Z', lastSha: 'c'.repeat(40), htmlUrl: null};
  const noBuild = {peopleCount: 0, hasLast: false};

  test('GitHub wins when it names people, whatever the build has', () => {
    assert.equal(preferGitHubHistory({contributors: [person]}, noBuild), true);
    assert.equal(preferGitHubHistory({contributors: [person], automation: bots}, {peopleCount: 3, hasLast: true}), true);
  });

  test('automation-only GitHub data wins only when the build names nobody', () => {
    assert.equal(preferGitHubHistory({contributors: [], automation: bots}, noBuild), true);
  });

  test('automation-only GitHub data never hides people found at build time (rename, capped page)', () => {
    assert.equal(preferGitHubHistory({contributors: [], automation: bots}, {peopleCount: 2, hasLast: true}), false);
    assert.equal(preferGitHubHistory({contributors: [], automation: bots}, {peopleCount: 0, hasLast: true}), false);
    assert.equal(preferGitHubHistory({contributors: [], automation: bots}, {peopleCount: 1, hasLast: false}), false);
  });

  test('an empty GitHub answer (file not on the default branch) keeps the build', () => {
    assert.equal(preferGitHubHistory({contributors: []}, noBuild), false);
    assert.equal(preferGitHubHistory({contributors: [], automation: null}, noBuild), false);
    assert.equal(preferGitHubHistory({contributors: [], automation: {...bots, commits: 0}}, noBuild), false);
  });
});
