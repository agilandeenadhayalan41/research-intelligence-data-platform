// An in-memory fake of the parts of the GitHub REST API that
// api/_lib/community.mjs uses, for tests (no network):
//
//   GET  /user                                     (visitor tokens)
//   GET  /repos/:o/:r/git/ref/heads/:branch        (server token, as all /repos calls)
//   POST /repos/:o/:r/git/blobs | git/trees | git/commits | git/refs
//   GET  /repos/:o/:r/git/blobs/:sha
//   GET  /repos/:o/:r/contents/:path?ref=:branch
//   PUT  /repos/:o/:r/contents/:path               (compare-and-swap on `sha`)
//
// Any other URL throws, so a test can never reach the real GitHub API. The
// tokens are obviously fake strings.

import {createHash} from 'node:crypto';

export const OWNER = 'agilandeenadhayalan41';
export const REPO = 'research-intelligence-data-platform';
export const SERVER_TOKEN = 'fake-server-token-for-tests';
export const ALICE_TOKEN = 'fake-visitor-token-alice';
export const BOB_TOKEN = 'fake-visitor-token-bob';

// Each user has their own numeric id, as on GitHub (stable across renames).
export const ALICE_ID = 1001;
export const BOB_ID = 1002;

export const DEFAULT_USERS = Object.freeze({
  [ALICE_TOKEN]: {login: 'alice', id: ALICE_ID, name: 'Alice Example', avatar_url: 'https://avatars.example.test/u/1'},
  [BOB_TOKEN]: {login: 'bob', id: BOB_ID, name: '', avatar_url: 'http://insecure.example.test/bob.png'},
});

/** A stable fake id for a user given without one. */
const fakeIdOf = (login) => 2000 + Number.parseInt(createHash('sha256').update(login).digest('hex').slice(0, 6), 16);

const gitSha = (kind, text) =>
  createHash('sha1').update(`${kind} ${Buffer.byteLength(text)}\0${text}`).digest('hex');

/**
 * @param {{
 *   owner?: string, repo?: string, serverToken?: string,
 *   users?: Record<string, {login: string, id?: number, name?: string, avatar_url?: string}>,
 *   largeFileBytes?: number,
 * }} [options]
 */
export function createFakeGitHub({
  owner = OWNER,
  repo = REPO,
  serverToken = SERVER_TOKEN,
  users = DEFAULT_USERS,
  largeFileBytes = 1024 * 1024,
} = {}) {
  /** @type {Map<string, any>} git objects by sha */
  const objects = new Map();
  /** @type {Map<string, {commit: string, files: Map<string, {text: string, sha: string}>}>} */
  const branches = new Map();
  /** Every request, without tokens: {method, path, query, body, auth}. */
  const requests = [];
  /** Hooks a test can set. */
  const hooks = {
    /** Called before a PUT is applied; may change files (a concurrent writer). */
    beforePut: /** @type {null | ((info: {path: string, branch: string, attempt: number}) => void)} */ (null),
    /** Return a {status, body} to answer instead of the fake (or null). */
    override: /** @type {null | ((info: {method: string, path: string}) => null | {status: number, body: any})} */ (null),
  };
  let putAttempts = 0;
  let commitCounter = 0;

  function storeBlob(text) {
    const sha = gitSha('blob', text);
    objects.set(sha, {type: 'blob', text});
    return sha;
  }

  function makeCommit(message, parents, files) {
    commitCounter += 1;
    const sha = gitSha('commit', `${message}\n${parents.join(',')}\n${commitCounter}`);
    objects.set(sha, {type: 'commit', message, parents, files: new Map(files)});
    return sha;
  }

  // `main` exists with one file, as in the real repository.
  const mainFiles = new Map([['README.md', {text: '# Repo\n', sha: gitSha('blob', '# Repo\n')}]]);
  branches.set('main', {commit: makeCommit('initial commit', [], mainFiles), files: mainFiles});

  const respond = (status, body) =>
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: {'content-type': 'application/json'},
    });

  function authOf(headers) {
    const value = headers?.Authorization ?? headers?.authorization ?? '';
    const match = /^Bearer (.+)$/.exec(value);
    if (!match) {
      return 'none';
    }
    if (match[1] === serverToken) {
      return 'server';
    }
    return users[match[1]] ? `visitor:${users[match[1]].login}` : 'unknown-token';
  }

  function fileView(path, file) {
    const base64 = Buffer.from(file.text, 'utf8').toString('base64');
    const size = Buffer.byteLength(file.text);
    const large = size > largeFileBytes;
    return {
      type: 'file',
      name: path.split('/').pop(),
      path,
      sha: file.sha,
      size,
      encoding: large ? 'none' : 'base64',
      // GitHub wraps base64 at 60 characters.
      content: large ? '' : base64.replace(/(.{60})/g, '$1\n'),
    };
  }

  /**
   * @param {string} url
   * @param {{method?: string, headers?: Record<string, string>, body?: string}} [init]
   */
  async function fetchImpl(url, init = {}) {
    const parsed = new URL(url);
    if (parsed.origin !== 'https://api.github.com') {
      throw new Error(`fake GitHub: unexpected URL ${parsed.origin}${parsed.pathname}`);
    }
    const method = (init.method ?? 'GET').toUpperCase();
    const path = decodeURIComponent(parsed.pathname);
    const body = init.body ? JSON.parse(init.body) : undefined;
    const auth = authOf(init.headers);
    requests.push({method, path, query: Object.fromEntries(parsed.searchParams), body, auth});

    const override = hooks.override?.({method, path});
    if (override) {
      return respond(override.status, override.body);
    }

    if (path === '/user') {
      if (method !== 'GET') {
        throw new Error('fake GitHub: /user is GET only');
      }
      if (!auth.startsWith('visitor:')) {
        return respond(401, {message: 'Bad credentials'});
      }
      const token = /^Bearer (.+)$/.exec(init.headers.Authorization)[1];
      const user = users[token];
      return respond(200, {
        login: user.login,
        id: user.id ?? fakeIdOf(user.login),
        name: user.name ?? null,
        avatar_url: user.avatar_url,
      });
    }

    const prefix = `/repos/${owner}/${repo}`;
    if (!path.startsWith(`${prefix}/`)) {
      throw new Error(`fake GitHub: unexpected path ${method} ${path}`);
    }
    if (auth !== 'server') {
      return respond(401, {message: 'Bad credentials'});
    }
    const rest = path.slice(prefix.length);

    if (method === 'GET' && rest.startsWith('/git/ref/heads/')) {
      const name = rest.slice('/git/ref/heads/'.length);
      const branch = branches.get(name);
      return branch
        ? respond(200, {ref: `refs/heads/${name}`, object: {sha: branch.commit, type: 'commit'}})
        : respond(404, {message: 'Not Found'});
    }
    if (method === 'POST' && rest === '/git/blobs') {
      const text = body.encoding === 'base64' ? Buffer.from(body.content, 'base64').toString('utf8') : body.content;
      const sha = storeBlob(text);
      return respond(201, {sha, url: `https://api.github.com${prefix}/git/blobs/${sha}`});
    }
    if (method === 'GET' && rest.startsWith('/git/blobs/')) {
      const sha = rest.slice('/git/blobs/'.length);
      const blob = objects.get(sha);
      if (!blob || blob.type !== 'blob') {
        return respond(404, {message: 'Not Found'});
      }
      return respond(200, {sha, size: Buffer.byteLength(blob.text), encoding: 'base64', content: Buffer.from(blob.text).toString('base64')});
    }
    if (method === 'POST' && rest === '/git/trees') {
      if (body.base_tree) {
        throw new Error('fake GitHub: base_tree is not supported (an orphan tree was expected)');
      }
      const files = new Map();
      for (const entry of body.tree) {
        // An entry names an existing blob by `sha` or gives its text inline
        // as `content` (GitHub then creates the blob).
        const sha = typeof entry.content === 'string' && entry.sha === undefined ? storeBlob(entry.content) : entry.sha;
        const blob = objects.get(sha);
        if (!blob || blob.type !== 'blob' || entry.type !== 'blob' || entry.mode !== '100644') {
          return respond(422, {message: 'tree.sha is not a valid blob'});
        }
        files.set(entry.path, {text: blob.text, sha});
      }
      const sha = gitSha('tree', JSON.stringify([...files]));
      objects.set(sha, {type: 'tree', files});
      return respond(201, {sha});
    }
    if (method === 'POST' && rest === '/git/commits') {
      const tree = objects.get(body.tree);
      if (!tree || tree.type !== 'tree') {
        return respond(422, {message: 'Tree SHA does not exist'});
      }
      const sha = makeCommit(body.message, body.parents ?? [], tree.files);
      return respond(201, {sha, message: body.message, parents: body.parents});
    }
    if (method === 'POST' && rest === '/git/refs') {
      const name = String(body.ref).replace(/^refs\/heads\//, '');
      if (branches.has(name)) {
        return respond(422, {message: 'Reference already exists'});
      }
      const commit = objects.get(body.sha);
      if (!commit || commit.type !== 'commit') {
        return respond(422, {message: 'Object does not exist'});
      }
      branches.set(name, {commit: body.sha, files: new Map(commit.files)});
      return respond(201, {ref: body.ref, object: {sha: body.sha, type: 'commit'}});
    }
    if (rest.startsWith('/contents/')) {
      const filePath = rest.slice('/contents/'.length);
      if (method === 'GET') {
        const ref = parsed.searchParams.get('ref') ?? 'main';
        const branch = branches.get(ref);
        if (!branch) {
          return respond(404, {message: `No commit found for the ref ${ref}`});
        }
        const file = branch.files.get(filePath);
        return file ? respond(200, fileView(filePath, file)) : respond(404, {message: 'Not Found'});
      }
      if (method === 'PUT') {
        putAttempts += 1;
        const name = body.branch ?? 'main';
        hooks.beforePut?.({path: filePath, branch: name, attempt: putAttempts});
        const branch = branches.get(name);
        if (!branch) {
          return respond(404, {message: `Branch ${name} not found`});
        }
        const existing = branch.files.get(filePath);
        if (existing && !body.sha) {
          return respond(422, {message: 'Invalid request.\n\n"sha" wasn\'t supplied.'});
        }
        if (existing && body.sha !== existing.sha) {
          return respond(409, {message: `${filePath} does not match ${body.sha}`});
        }
        if (!existing && body.sha) {
          return respond(422, {message: 'sha does not match'});
        }
        const text = Buffer.from(body.content, 'base64').toString('utf8');
        const sha = storeBlob(text);
        branch.files.set(filePath, {text, sha});
        branch.commit = makeCommit(body.message, [branch.commit], branch.files);
        return respond(existing ? 200 : 201, {
          content: {path: filePath, sha},
          commit: {sha: branch.commit, message: body.message},
        });
      }
    }
    throw new Error(`fake GitHub: unexpected request ${method} ${path}`);
  }

  return {
    fetch: fetchImpl,
    requests,
    hooks,
    branches,
    /** Commit messages of a branch, newest first. */
    history(name) {
      const messages = [];
      let sha = branches.get(name)?.commit;
      while (sha) {
        const commit = objects.get(sha);
        messages.push({message: commit.message, parents: commit.parents.length});
        sha = commit.parents[0];
      }
      return messages;
    },
    /** The parsed JSON of a file on a branch, or undefined. */
    readJson(name, filePath) {
      const file = branches.get(name)?.files.get(filePath);
      return file ? JSON.parse(file.text) : undefined;
    },
    /** Writes a file directly, as a concurrent writer or a moderator would. */
    writeFile(name, filePath, text) {
      let branch = branches.get(name);
      if (!branch) {
        branch = {commit: makeCommit(`create ${name}`, [], new Map()), files: new Map()};
        branches.set(name, branch);
      }
      branch.files.set(filePath, {text, sha: storeBlob(text)});
      branch.commit = makeCommit(`edit ${filePath}`, [branch.commit], branch.files);
    },
    get putAttempts() {
      return putAttempts;
    },
  };
}
