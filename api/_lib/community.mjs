// Shared likes and comments for the docs site, kept as JSON files in this
// GitHub repository.
//
// `api/community.mjs` (a Vercel Node.js function) is a thin wrapper around
// this module. The folder starts with an underscore, so Vercel does not turn
// this file into a route of its own.
//
// How it works:
// - Reads: GET /api/community?page=<pageId> returns the likes and comments of
//   one page. Anyone can read; no sign-in.
// - Writes: POST /api/community {action, page, body?, id?, login?} with the
//   visitor's GitHub token as `Authorization: Bearer <token>`. The token is
//   only used to call GET https://api.github.com/user, which tells us who the
//   visitor is. It is never stored, logged or sent anywhere else. The write is
//   then made with the server's own token (COMMUNITY_GITHUB_TOKEN).
// - Storage: one file per page, community/pages/<32 hex of sha256(pageId)>.json,
//   on the branch community-data (an orphan branch, created on the first
//   write). Every write is a commit made through the contents API with the
//   file's sha, so concurrent writers cannot overwrite each other: a 409/422
//   conflict re-reads the file and tries again.
//
// The module has no dependencies (node:crypto and the global fetch only) and
// every outside effect is injected, so `node --test` covers it without network
// access: createCommunityHandler({env, fetchImpl, now, random, sleep, newId, log}).

import {createHash, randomUUID} from 'node:crypto';

export const GITHUB_API = 'https://api.github.com';
export const DEFAULT_REPO = 'agilandeenadhayalan41/research-intelligence-data-platform';
export const DEFAULT_BRANCH = 'community-data';
export const DEFAULT_DIR = 'community';
export const FILE_VERSION = 1;

/** A site path such as /docs/overview/introduction.md or /blog/welcome. */
export const PAGE_ID_PATTERN = /^\/[A-Za-z0-9/_.~%-]{0,300}$/;
export const COMMENT_MAX_LENGTH = 5000;
export const COMMENT_LENGTH_ERROR = `comment must be 1-${COMMENT_MAX_LENGTH} characters`;
/** Largest accepted JSON body, in bytes. */
export const BODY_MAX_BYTES = 16 * 1024;
/** A page file stops taking comments at this many (it is read on every write). */
export const MAX_COMMENTS_PER_PAGE = 1000;
/**
 * A page file stops taking comments at this size in bytes: under the contents
 * API's 1 MB inline limit and well under Vercel's 4.5 MB response limit.
 */
export const PAGE_FILE_MAX_BYTES = 900_000;
export const PAGE_SIZE_ERROR = 'this page has reached its size limit';

export const READ_CACHE_MS = 20 * 1000;
export const LOGIN_CACHE_MS = 10 * 60 * 1000;
export const WRITE_LIMIT = Object.freeze({max: 20, windowMs: 10 * 60 * 1000});
export const READ_LIMIT = Object.freeze({max: 120, windowMs: 60 * 1000});
/** Retries after a write conflict (so at most MAX_RETRIES + 1 attempts). */
export const MAX_RETRIES = 4;

export const UNAUTHORIZED_ERROR = 'sign in with a GitHub token to like or comment';
export const BUSY_ERROR = 'busy, try again';
export const NOT_CONFIGURED_ERROR = 'shared likes and comments are not configured on this deployment';

/** Passed as `body` by createNodeHandler when the request body was over BODY_MAX_BYTES. */
export const BODY_TOO_LARGE = Symbol('community body too large');
/** Passed as `body` by createNodeHandler when the platform could not parse the JSON body. */
export const BODY_INVALID = Symbol('community body invalid');

const ACTIONS = new Set(['like', 'unlike', 'comment', 'delete']);
const LOGIN_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const TOKEN_PATTERN = /^[\x21-\x7E]{1,1000}$/;
const REPO_PATTERN = /^([A-Za-z0-9_.-]{1,100})\/([A-Za-z0-9_.-]{1,100})$/;
const BRANCH_PATTERN = /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/;
const DIR_PATTERN = /^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:\/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$/;
/** Branches the function refuses to write to. */
const PROTECTED_BRANCHES = new Set(['main', 'master', 'head']);
const ID_MAX_LENGTH = 100;
const NAME_MAX_LENGTH = 100;
const GITHUB_TIMEOUT_MS = 8000;
const CACHE_MAX_ENTRIES = 500;
const LOGIN_CACHE_MAX_ENTRIES = 1000;
const RATE_LIMIT_MAX_KEYS = 10000;
const USER_AGENT = 'research-intelligence-docs-site-community';

const BRANCH_README = (branch, dir) => `# Community data for the docs site

This branch holds the likes and comments that visitors add to the docs site.
It is written only by the site's \`/api/community\` function (\`api/community.mjs\`
on \`main\`), one commit per like, unlike, comment or deleted comment.

- \`${dir}/pages/<id>.json\` is one page. \`<id>\` is the first 32 hex characters of
  the SHA-256 of the page id (the page's repository path with a leading slash,
  for example \`/docs/overview/introduction.md\`); the file repeats the page id.
- The branch shares no history with \`main\`. It is never merged, built or
  deployed: its own root \`vercel.json\` (created with the branch) sets
  \`git.deploymentEnabled\` to false, so its commits start no Vercel deployment,
  and it has no CI workflow files.
- Moderation: edit or delete a page file on this branch. Keep the JSON valid.

See docs/site/README.md on \`main\` for the details. Branch: \`${branch}\`.
`;

/**
 * The data branch's own root vercel.json. Vercel reads vercel.json from the
 * pushed commit, so main's copy does not apply to this orphan branch; this one
 * turns off deployments for its commits.
 */
export const BRANCH_VERCEL_JSON = `${JSON.stringify({git: {deploymentEnabled: false}})}\n`;

// ---------------------------------------------------------------------------
// Types

/**
 * @typedef {(url: string, init?: {method?: string, headers?: Record<string, string>, body?: string, signal?: AbortSignal}) => Promise<{status: number, ok: boolean, json: () => Promise<any>}>} FetchLike
 */

/**
 * @typedef {object} HandlerRequest
 * @property {string} [method]
 * @property {Record<string, unknown> | URLSearchParams} [query]
 * @property {Record<string, string | string[] | undefined>} [headers] Header names in any case.
 * @property {unknown} [body] Raw (string, Buffer, Uint8Array), already parsed (object), BODY_TOO_LARGE or BODY_INVALID.
 * @property {string} [ip] Client IP, the rate-limit key.
 */

/**
 * @typedef {object} HandlerResponse
 * @property {number} status
 * @property {Record<string, string>} headers
 * @property {Record<string, unknown>} json
 */

/**
 * @typedef {object} StoredLike
 * @property {string} login
 * @property {string} at ISO 8601.
 */

/**
 * @typedef {object} StoredComment
 * @property {string} id
 * @property {string} login
 * @property {string} name
 * @property {string | null} avatarUrl
 * @property {string} body
 * @property {string} createdAt ISO 8601.
 */

/**
 * The JSON file of one page.
 *
 * @typedef {object} PageFile
 * @property {1} version
 * @property {string} pageId
 * @property {StoredLike[]} likes Unique by login, oldest first.
 * @property {StoredComment[]} comments Oldest first.
 */

/**
 * @typedef {object} Viewer
 * @property {string} login
 * @property {string} name
 * @property {string | null} avatarUrl
 */

/** A GitHub API call that failed. Its message never holds a token. */
export class GitHubFailure extends Error {
  /**
   * @param {string} step
   * @param {number} status 0 for a network error or timeout.
   */
  constructor(step, status) {
    super(`GitHub ${step} failed (${status === 0 ? 'network error' : `HTTP ${status}`})`);
    this.name = 'GitHubFailure';
    this.step = step;
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers

/**
 * Path of a page's file on the data branch.
 *
 * @param {string} pageId
 * @param {string} [dir]
 */
export function pageFilePath(pageId, dir = DEFAULT_DIR) {
  return `${dir}/pages/${sha256(pageId).slice(0, 32)}.json`;
}

/** @param {unknown} value */
export function isPageId(value) {
  return typeof value === 'string' && PAGE_ID_PATTERN.test(value);
}

/**
 * Reads the configuration from the environment. Never throws.
 *
 * @param {Record<string, string | undefined>} env
 */
export function readConfig(env) {
  const source = env ?? {};
  const token = clean(source.COMMUNITY_GITHUB_TOKEN);
  if (!token) {
    return {configured: /** @type {const} */ (false), reason: 'missing token'};
  }
  const repo = clean(source.COMMUNITY_REPO) || DEFAULT_REPO;
  const branch = clean(source.COMMUNITY_DATA_BRANCH) || DEFAULT_BRANCH;
  const dir = (clean(source.COMMUNITY_DATA_DIR) || DEFAULT_DIR).replace(/^\/+|\/+$/g, '');
  const repoMatch = REPO_PATTERN.exec(repo);
  const branchOk =
    BRANCH_PATTERN.test(branch) &&
    branch.length <= 100 &&
    !branch.split('/').some((part) => part === '.' || part === '..' || part.endsWith('.lock')) &&
    !PROTECTED_BRANCHES.has(branch.toLowerCase());
  const dirOk = DIR_PATTERN.test(dir) && dir.length <= 100 && !dir.split('/').includes('..');
  if (!TOKEN_PATTERN.test(token) || !repoMatch || !branchOk || !dirOk) {
    return {configured: /** @type {const} */ (false), reason: 'invalid settings'};
  }
  return {
    configured: /** @type {const} */ (true),
    token,
    owner: repoMatch[1],
    name: repoMatch[2],
    branch,
    dir,
  };
}

/**
 * A page file read from GitHub, checked again (it can be edited by hand).
 * Returns null when it is not a page file at all.
 *
 * @param {unknown} raw
 * @param {string} pageId
 * @returns {PageFile | null}
 */
export function normalizePageFile(raw, pageId) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null;
  }
  const value = /** @type {Record<string, unknown>} */ (raw);
  /** @type {StoredLike[]} */
  const likes = [];
  const seen = new Set();
  for (const like of Array.isArray(value.likes) ? value.likes : []) {
    if (like && typeof like.login === 'string' && LOGIN_PATTERN.test(like.login) && !seen.has(like.login)) {
      seen.add(like.login);
      likes.push({login: like.login, at: typeof like.at === 'string' ? like.at : ''});
    }
  }
  /** @type {StoredComment[]} */
  const comments = [];
  for (const comment of Array.isArray(value.comments) ? value.comments : []) {
    if (
      comment &&
      typeof comment.id === 'string' &&
      comment.id &&
      typeof comment.login === 'string' &&
      LOGIN_PATTERN.test(comment.login) &&
      typeof comment.body === 'string' &&
      typeof comment.createdAt === 'string'
    ) {
      comments.push({
        id: comment.id.slice(0, ID_MAX_LENGTH),
        login: comment.login,
        name: typeof comment.name === 'string' && comment.name.trim() ? comment.name.trim().slice(0, NAME_MAX_LENGTH) : comment.login,
        avatarUrl: httpsUrl(comment.avatarUrl),
        body: comment.body,
        createdAt: comment.createdAt,
      });
    }
  }
  return {version: FILE_VERSION, pageId, likes, comments};
}

/**
 * @param {string} pageId
 * @returns {PageFile}
 */
export function emptyPageFile(pageId) {
  return {version: FILE_VERSION, pageId, likes: [], comments: []};
}

/**
 * What GET and POST return for a page.
 *
 * @param {string} pageId
 * @param {PageFile} file
 */
export function pageView(pageId, file) {
  return {
    configured: true,
    pageId,
    likes: {count: file.likes.length, likedBy: file.likes.map((like) => like.login)},
    comments: file.comments.map((comment) => commentView(pageId, comment)),
  };
}

/**
 * @param {string} pageId
 * @param {StoredComment} comment
 */
function commentView(pageId, comment) {
  return {
    id: comment.id,
    pageId,
    author: {login: comment.login, name: comment.name, avatarUrl: comment.avatarUrl},
    body: comment.body,
    createdAt: comment.createdAt,
  };
}

/**
 * Trims and checks the length in characters (code points).
 *
 * @param {unknown} body
 * @returns {string | null}
 */
export function normalizeCommentBody(body) {
  if (typeof body !== 'string') {
    return null;
  }
  const trimmed = body.trim();
  const length = Array.from(trimmed).length;
  return length >= 1 && length <= COMMENT_MAX_LENGTH ? trimmed : null;
}

// ---------------------------------------------------------------------------
// The handler

/**
 * @typedef {object} HandlerOptions
 * @property {Record<string, string | undefined>} [env] Defaults to process.env.
 * @property {FetchLike} [fetchImpl] Defaults to the global fetch.
 * @property {() => Date | number} [now]
 * @property {() => number} [random] In [0, 1), for the retry jitter.
 * @property {(ms: number) => Promise<void>} [sleep]
 * @property {() => string} [newId] Comment ids.
 * @property {(message: string) => void} [log] Gets short messages (step and status only).
 */

/**
 * @param {HandlerOptions} [options]
 * @returns {(request: HandlerRequest) => Promise<HandlerResponse>}
 */
export function createCommunityHandler({
  env = globalThis.process?.env ?? {},
  fetchImpl = (url, init) => globalThis.fetch(url, init),
  now = () => Date.now(),
  random = Math.random,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  newId = randomUUID,
  log = (message) => console.error(`[community] ${message}`),
} = {}) {
  const config = readConfig(env);
  const clock = () => {
    const value = now();
    return value instanceof Date ? value.getTime() : Number(value);
  };

  /** @type {Map<string, {at: number, file: PageFile}>} */
  const pageCache = new Map();
  /** @type {Map<string, {expires: number, viewer: Viewer}>} */
  const loginCache = new Map();
  const readLimiter = createRateLimiter(READ_LIMIT);
  const writeLimiter = createRateLimiter(WRITE_LIMIT);
  /** @type {Promise<void> | null} */
  let branchReady = null;

  // ---- GitHub ------------------------------------------------------------

  /**
   * @param {string} method
   * @param {string} path Under https://api.github.com.
   * @param {{token: string, body?: unknown, step: string}} options
   */
  async function github(method, path, {token, body, step}) {
    /** @type {Record<string, string>} */
    const headers = {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'User-Agent': USER_AGENT,
      'X-GitHub-Api-Version': '2022-11-28',
    };
    /** @type {{method: string, headers: Record<string, string>, body?: string, signal?: AbortSignal}} */
    const init = {method, headers};
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
      init.signal = AbortSignal.timeout(GITHUB_TIMEOUT_MS);
    }
    let response;
    try {
      response = await fetchImpl(`${GITHUB_API}${path}`, init);
    } catch {
      throw new GitHubFailure(step, 0);
    }
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    return {status: response.status, ok: response.ok, data};
  }

  /** @param {string} method @param {string} path @param {string} step @param {unknown} [body] */
  function repoApi(method, path, step, body) {
    if (!config.configured) {
      throw new Error('not configured');
    }
    return github(method, `/repos/${config.owner}/${config.name}${path}`, {
      token: config.token,
      body,
      step,
    });
  }

  /**
   * Creates the data branch as an orphan branch (no parent, so it shares no
   * history with main) unless it exists. Runs once per instance.
   */
  function ensureBranch() {
    if (!branchReady) {
      const attempt = createBranchIfMissing();
      branchReady = attempt;
      attempt.catch(() => {
        if (branchReady === attempt) {
          branchReady = null;
        }
      });
    }
    return branchReady;
  }

  async function createBranchIfMissing() {
    if (!config.configured) {
      return;
    }
    const refPath = `/git/ref/heads/${encodePath(config.branch)}`;
    const existing = await repoApi('GET', refPath, 'branch lookup');
    if (existing.ok) {
      return;
    }
    if (existing.status !== 404) {
      throw new GitHubFailure('branch lookup', existing.status);
    }
    const blob = await repoApi('POST', '/git/blobs', 'branch blob', {
      content: BRANCH_README(config.branch, config.dir),
      encoding: 'utf-8',
    });
    const blobSha = shaOf(blob, 'branch blob');
    const tree = await repoApi('POST', '/git/trees', 'branch tree', {
      tree: [
        {path: `${config.dir}/README.md`, mode: '100644', type: 'blob', sha: blobSha},
        {path: 'vercel.json', mode: '100644', type: 'blob', content: BRANCH_VERCEL_JSON},
      ],
    });
    const treeSha = shaOf(tree, 'branch tree');
    const commit = await repoApi('POST', '/git/commits', 'branch commit', {
      message: `community: create the ${config.branch} branch`,
      tree: treeSha,
      parents: [],
    });
    const commitSha = shaOf(commit, 'branch commit');
    const ref = await repoApi('POST', '/git/refs', 'branch create', {
      ref: `refs/heads/${config.branch}`,
      sha: commitSha,
    });
    if (ref.ok) {
      log(`created the ${config.branch} branch`);
      return;
    }
    if (ref.status === 422) {
      // Another instance created it first.
      const again = await repoApi('GET', refPath, 'branch lookup');
      if (again.ok) {
        return;
      }
    }
    throw new GitHubFailure('branch create', ref.status);
  }

  /**
   * @param {string} pageId
   * @returns {Promise<{file: PageFile, sha: string | null, corrupt: boolean}>}
   */
  async function readPage(pageId) {
    if (!config.configured) {
      throw new Error('not configured');
    }
    const path = pageFilePath(pageId, config.dir);
    const response = await repoApi(
      'GET',
      `/contents/${path}?ref=${encodeURIComponent(config.branch)}`,
      'read',
    );
    if (response.status === 404) {
      // No file yet (or no branch yet): an empty page.
      return {file: emptyPageFile(pageId), sha: null, corrupt: false};
    }
    if (!response.ok || !response.data || typeof response.data.sha !== 'string') {
      throw new GitHubFailure('read', response.ok ? 502 : response.status);
    }
    const sha = response.data.sha;
    let content = typeof response.data.content === 'string' ? response.data.content : '';
    if (response.data.encoding !== 'base64' && response.data.size > 0) {
      // Files over 1 MB come without content from the contents API.
      const blob = await repoApi('GET', `/git/blobs/${encodeURIComponent(sha)}`, 'read blob');
      if (!blob.ok || !blob.data || typeof blob.data.content !== 'string') {
        throw new GitHubFailure('read blob', blob.ok ? 502 : blob.status);
      }
      content = blob.data.content;
    }
    const text = Buffer.from(content, 'base64').toString('utf8');
    let parsed;
    try {
      parsed = text.trim() ? JSON.parse(text) : {};
    } catch {
      parsed = null;
    }
    const file = normalizePageFile(parsed, pageId);
    if (!file) {
      log(`page file ${path} is not valid JSON`);
      return {file: emptyPageFile(pageId), sha, corrupt: true};
    }
    return {file, sha, corrupt: false};
  }

  /** @param {string} pageId */
  async function readCached(pageId) {
    const hit = pageCache.get(pageId);
    if (hit && clock() - hit.at < READ_CACHE_MS) {
      return hit.file;
    }
    const {file} = await readPage(pageId);
    remember(pageId, file);
    return file;
  }

  /** @param {string} pageId @param {PageFile} file */
  function remember(pageId, file) {
    pageCache.delete(pageId);
    pageCache.set(pageId, {at: clock(), file});
    trimMap(pageCache, CACHE_MAX_ENTRIES);
  }

  /**
   * The verified identity behind a visitor's token, or null when GitHub does
   * not accept the token. Cached by the token's SHA-256 for 10 minutes.
   *
   * @param {string} token
   * @returns {Promise<Viewer | null>}
   */
  async function verify(token) {
    const key = sha256(token);
    const cached = loginCache.get(key);
    const time = clock();
    if (cached && cached.expires > time) {
      return cached.viewer;
    }
    loginCache.delete(key);
    const response = await github('GET', '/user', {token, step: 'verify'});
    if (response.status === 401) {
      return null;
    }
    if (!response.ok) {
      throw new GitHubFailure('verify', response.status);
    }
    const data = response.data ?? {};
    if (typeof data.login !== 'string' || !LOGIN_PATTERN.test(data.login)) {
      return null;
    }
    /** @type {Viewer} */
    const viewer = {
      login: data.login,
      name:
        typeof data.name === 'string' && data.name.trim()
          ? data.name.trim().slice(0, NAME_MAX_LENGTH)
          : data.login,
      avatarUrl: httpsUrl(data.avatar_url),
    };
    loginCache.set(key, {expires: time + LOGIN_CACHE_MS, viewer});
    trimMap(loginCache, LOGIN_CACHE_MAX_ENTRIES);
    return viewer;
  }

  /**
   * Read, change, compare-and-swap write; on a conflict, read again and retry.
   *
   * @param {string} pageId
   * @param {string} action
   * @param {(file: PageFile) => {error: string, status: number} | {changed: boolean, extra?: (file: PageFile) => Record<string, unknown>}} change
   *   Mutates `file` in place.
   * @returns {Promise<HandlerResponse>}
   */
  async function write(pageId, action, change) {
    if (!config.configured) {
      throw new Error('not configured');
    }
    await ensureBranch();
    const path = pageFilePath(pageId, config.dir);
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
      if (attempt > 0) {
        await sleep(Math.round(attempt * 40 + random() * 120));
      }
      const current = await readPage(pageId);
      if (current.corrupt) {
        return reply(500, {
          configured: true,
          error: 'the saved data of this page is not valid JSON; a maintainer has to fix it',
        });
      }
      const file = current.file;
      const outcome = change(file);
      if ('error' in outcome) {
        return reply(outcome.status, {configured: true, error: outcome.error});
      }
      const extra = outcome.extra ? outcome.extra(file) : {};
      if (!outcome.changed) {
        remember(pageId, file);
        return reply(200, {...pageView(pageId, file), ...extra});
      }
      const json = `${JSON.stringify(file, null, 2)}\n`;
      // Only a new comment can be refused for size, so likes, unlikes and
      // deletes keep working on a file that is already large.
      if (action === 'comment' && Buffer.byteLength(json, 'utf8') > PAGE_FILE_MAX_BYTES) {
        return reply(409, {configured: true, error: PAGE_SIZE_ERROR});
      }
      const content = Buffer.from(json, 'utf8').toString('base64');
      /** @type {Record<string, unknown>} */
      const body = {message: `community: ${action} on ${pageId}`, content, branch: config.branch};
      if (current.sha) {
        body.sha = current.sha;
      }
      const put = await repoApi('PUT', `/contents/${path}`, 'write', body);
      if (put.ok) {
        remember(pageId, file);
        return reply(200, {...pageView(pageId, file), ...extra});
      }
      if (put.status === 409 || put.status === 422) {
        continue; // someone else changed the file (or created it) first
      }
      if (put.status === 404) {
        branchReady = null; // the branch may have been deleted: create it again
        await ensureBranch();
        continue;
      }
      throw new GitHubFailure('write', put.status);
    }
    log(`write conflict on ${path} after ${MAX_RETRIES + 1} attempts`);
    return reply(503, {configured: true, error: BUSY_ERROR}, {'Retry-After': '2'});
  }

  // ---- Routes ------------------------------------------------------------

  /** @param {HandlerRequest} request */
  async function get(request) {
    const page = queryParam(request.query, 'page');
    if (page === undefined) {
      // Status probe used by the site to decide between shared and browser storage.
      return reply(200, {configured: true});
    }
    if (!isPageId(page)) {
      return badRequest('page must be a site path such as /docs/overview/introduction.md');
    }
    return reply(200, pageView(page, await readCached(page)));
  }

  /** @param {HandlerRequest} request */
  async function post(request) {
    const declared = Number(header(request.headers, 'content-length'));
    if (request.body === BODY_TOO_LARGE || (Number.isFinite(declared) && declared > BODY_MAX_BYTES)) {
      return tooLarge();
    }
    const parsed = parseBody(request.body);
    if (parsed === BODY_TOO_LARGE) {
      return tooLarge();
    }
    if (parsed === BODY_INVALID) {
      return badRequest('the body must be a JSON object');
    }
    const {action, page} = parsed;
    if (typeof action !== 'string' || !ACTIONS.has(action)) {
      return badRequest("action must be 'like', 'unlike', 'comment' or 'delete'");
    }
    if (!isPageId(page)) {
      return badRequest('page must be a site path such as /docs/overview/introduction.md');
    }
    const token = bearerToken(request.headers);
    if (!token) {
      return unauthorized();
    }
    /** @type {string | null} */
    let body = null;
    if (action === 'comment') {
      body = normalizeCommentBody(parsed.body);
      if (body === null) {
        return badRequest(COMMENT_LENGTH_ERROR);
      }
    }
    const id = parsed.id;
    if (action === 'delete' && (typeof id !== 'string' || !id || id.length > ID_MAX_LENGTH)) {
      return badRequest('id must be the id of a comment');
    }
    const viewer = await verify(token);
    if (!viewer) {
      return unauthorized();
    }
    // The site's sign-in is only a typed username: when the browser says who
    // it thinks it is, it must be the owner of the token.
    if (typeof parsed.login === 'string' && parsed.login.toLowerCase() !== viewer.login.toLowerCase()) {
      return reply(403, {
        configured: true,
        error: `this token belongs to @${viewer.login}; sign in as @${viewer.login} to like or comment`,
      });
    }

    switch (action) {
      case 'like':
        return write(page, action, (file) => {
          if (file.likes.some((like) => like.login === viewer.login)) {
            return {changed: false};
          }
          file.likes.push({login: viewer.login, at: isoNow()});
          return {changed: true};
        });
      case 'unlike':
        return write(page, action, (file) => {
          const index = file.likes.findIndex((like) => like.login === viewer.login);
          if (index === -1) {
            return {changed: false};
          }
          file.likes.splice(index, 1);
          return {changed: true};
        });
      case 'comment': {
        /** @type {StoredComment} */
        const comment = {
          id: String(newId()).slice(0, ID_MAX_LENGTH),
          login: viewer.login,
          name: viewer.name,
          avatarUrl: viewer.avatarUrl,
          body: /** @type {string} */ (body),
          createdAt: isoNow(),
        };
        const extra = () => ({comment: commentView(page, comment)});
        return write(page, action, (file) => {
          if (file.comments.some((item) => item.id === comment.id)) {
            return {changed: false, extra};
          }
          if (file.comments.length >= MAX_COMMENTS_PER_PAGE) {
            return {status: 409, error: 'this page has reached its comment limit'};
          }
          file.comments.push(comment);
          return {changed: true, extra};
        });
      }
      default:
        return write(page, action, (file) => {
          const index = file.comments.findIndex((item) => item.id === id);
          if (index === -1) {
            return {status: 404, error: 'comment not found'};
          }
          if (file.comments[index].login !== viewer.login) {
            return {status: 403, error: 'you can only delete your own comments'};
          }
          file.comments.splice(index, 1);
          return {changed: true, extra: () => ({deleted: true})};
        });
    }
  }

  function isoNow() {
    return new Date(clock()).toISOString();
  }

  /**
   * @param {HandlerRequest} request
   * @returns {Promise<HandlerResponse>}
   */
  async function route(request) {
    const method = String(request.method ?? 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'POST') {
      return reply(405, {error: 'only GET and POST are allowed'}, {Allow: 'GET, POST'});
    }
    if (!config.configured) {
      return reply(503, {configured: false, error: NOT_CONFIGURED_ERROR});
    }
    const ip = typeof request.ip === 'string' && request.ip ? request.ip.slice(0, 100) : 'unknown';
    const wait = (method === 'GET' ? readLimiter : writeLimiter).take(ip, clock());
    if (wait > 0) {
      const seconds = Math.max(1, Math.ceil(wait / 1000));
      return reply(
        429,
        {configured: true, error: `too many requests, try again in ${seconds} seconds`},
        {'Retry-After': String(seconds)},
      );
    }
    try {
      return method === 'GET' ? await get(request) : await post(request);
    } catch (error) {
      if (error instanceof GitHubFailure) {
        log(`${error.step}: ${error.status === 0 ? 'network error' : `HTTP ${error.status}`}`);
        if (error.status === 403 || error.status === 429) {
          return reply(
            503,
            {configured: true, error: 'GitHub is rate limiting the community storage, try again later'},
            {'Retry-After': '60'},
          );
        }
        return reply(502, {configured: true, error: 'could not reach the community storage on GitHub'});
      }
      throw error;
    }
  }

  return async function handleCommunityRequest(request) {
    const secrets = [config.configured ? config.token : null, bearerToken(request?.headers)];
    let response;
    try {
      response = await route(request ?? {});
    } catch (error) {
      log(`unexpected error (${error instanceof Error ? error.name : typeof error})`);
      response = reply(500, {error: 'internal error'});
    }
    return {...response, json: redact(response.json, secrets)};
  };
}

// ---------------------------------------------------------------------------
// Node.js adapter (Vercel functions and plain node:http servers)

/**
 * Turns the handler into a Node.js `(req, res)` function. It works with
 * Vercel's Node helpers (req.query, req.body) and without them (it then
 * parses the URL and reads the body stream itself, at most BODY_MAX_BYTES).
 *
 * @param {(request: HandlerRequest) => Promise<HandlerResponse>} handle
 */
export function createNodeHandler(handle) {
  /**
   * @param {any} req
   * @param {any} res
   */
  return async function communityNodeHandler(req, res) {
    /** @type {HandlerResponse} */
    let result;
    try {
      const method = String(req.method ?? 'GET').toUpperCase();
      const query =
        req.query && typeof req.query === 'object'
          ? req.query
          : Object.fromEntries(new URL(req.url ?? '/', 'http://localhost').searchParams);
      const body = method === 'POST' ? await readNodeBody(req) : undefined;
      result = await handle({method, query, headers: req.headers ?? {}, body, ip: clientIp(req)});
    } catch {
      result = reply(500, {error: 'internal error'});
    }
    res.statusCode = result.status;
    for (const [name, value] of Object.entries(result.headers)) {
      res.setHeader(name, value);
    }
    res.end(JSON.stringify(result.json));
  };
}

/** @param {any} req */
async function readNodeBody(req) {
  let parsedByPlatform;
  try {
    // Vercel parses the body lazily and throws on invalid JSON.
    parsedByPlatform = req.body;
  } catch {
    return BODY_INVALID;
  }
  if (parsedByPlatform !== undefined) {
    return parsedByPlatform;
  }
  if (req.readableEnded || typeof req.on !== 'function') {
    return undefined;
  }
  const declared = Number(req.headers?.['content-length']);
  if (Number.isFinite(declared) && declared > BODY_MAX_BYTES) {
    return BODY_TOO_LARGE; // not read: Node discards the rest after the response
  }
  return new Promise((resolve, reject) => {
    /** @type {Buffer[]} */
    const chunks = [];
    let size = 0;
    req.on('data', (/** @type {Buffer} */ chunk) => {
      size += chunk.length;
      if (size <= BODY_MAX_BYTES) {
        chunks.push(chunk);
      } else {
        chunks.length = 0; // keep draining, store nothing
      }
    });
    req.on('end', () => resolve(size > BODY_MAX_BYTES ? BODY_TOO_LARGE : Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

/**
 * The client IP. On Vercel, x-real-ip and x-forwarded-for are set by the
 * platform (a client cannot spoof them there).
 *
 * @param {any} req
 */
function clientIp(req) {
  const headers = req.headers ?? {};
  const real = header(headers, 'x-real-ip');
  if (real) {
    return real.trim();
  }
  const forwarded = header(headers, 'x-forwarded-for');
  if (forwarded) {
    return forwarded.split(',')[0].trim();
  }
  return req.socket?.remoteAddress ?? 'unknown';
}

// ---------------------------------------------------------------------------
// Helpers

/**
 * @param {number} status
 * @param {Record<string, unknown>} json
 * @param {Record<string, string>} [headers]
 * @returns {HandlerResponse}
 */
function reply(status, json, headers = {}) {
  return {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      ...headers,
    },
    json,
  };
}

/** @param {string} message */
function badRequest(message) {
  return reply(400, {configured: true, error: message});
}

function unauthorized() {
  return reply(401, {configured: true, error: UNAUTHORIZED_ERROR}, {'WWW-Authenticate': 'Bearer'});
}

function tooLarge() {
  return reply(413, {configured: true, error: `the request body must be at most ${BODY_MAX_BYTES / 1024} KB`});
}

/**
 * @param {unknown} body
 * @returns {Record<string, unknown> | typeof BODY_TOO_LARGE | typeof BODY_INVALID}
 */
function parseBody(body) {
  if (body === BODY_INVALID || body === undefined || body === null) {
    return BODY_INVALID;
  }
  /** @type {unknown} */
  let value = body;
  if (typeof body === 'string' || body instanceof Uint8Array) {
    const bytes = typeof body === 'string' ? Buffer.byteLength(body, 'utf8') : body.byteLength;
    if (bytes > BODY_MAX_BYTES) {
      return BODY_TOO_LARGE;
    }
    try {
      value = JSON.parse(typeof body === 'string' ? body : Buffer.from(body).toString('utf8'));
    } catch {
      return BODY_INVALID;
    }
  } else if (typeof body === 'object') {
    // Already parsed by the platform: measure it the way it was sent.
    let text;
    try {
      text = JSON.stringify(body);
    } catch {
      return BODY_INVALID;
    }
    if (Buffer.byteLength(text, 'utf8') > BODY_MAX_BYTES) {
      return BODY_TOO_LARGE;
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return BODY_INVALID;
  }
  return /** @type {Record<string, unknown>} */ (value);
}

/**
 * @param {HandlerRequest['query']} query
 * @param {string} name
 * @returns {string | undefined | null} undefined when missing, null when not a single string.
 */
function queryParam(query, name) {
  if (!query) {
    return undefined;
  }
  if (typeof (/** @type {any} */ (query).getAll) === 'function') {
    const values = /** @type {URLSearchParams} */ (query).getAll(name);
    return values.length === 0 ? undefined : values.length === 1 ? values[0] : null;
  }
  const value = /** @type {Record<string, unknown>} */ (query)[name];
  if (value === undefined) {
    return undefined;
  }
  if (Array.isArray(value)) {
    return value.length === 1 && typeof value[0] === 'string' ? value[0] : null;
  }
  return typeof value === 'string' ? value : null;
}

/**
 * @param {HandlerRequest['headers']} headers
 * @param {string} name Lower case.
 * @returns {string | undefined}
 */
function header(headers, name) {
  if (!headers || typeof headers !== 'object') {
    return undefined;
  }
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === name) {
      return Array.isArray(value) ? value[0] : typeof value === 'string' ? value : undefined;
    }
  }
  return undefined;
}

/** @param {HandlerRequest['headers']} headers */
function bearerToken(headers) {
  const value = header(headers, 'authorization');
  const match = typeof value === 'string' ? /^Bearer\s+(\S+)\s*$/i.exec(value) : null;
  return match && TOKEN_PATTERN.test(match[1]) ? match[1] : null;
}

/**
 * Removes the secrets from a JSON value (defence in depth: no message is
 * built from a token in the first place).
 *
 * @param {Record<string, unknown>} json
 * @param {(string | null | undefined)[]} secrets
 */
function redact(json, secrets) {
  let text = JSON.stringify(json);
  let changed = false;
  for (const secret of secrets) {
    if (typeof secret === 'string' && secret.length >= 4 && text.includes(secret)) {
      text = text.split(secret).join('[redacted]');
      changed = true;
    }
  }
  return changed ? JSON.parse(text) : json;
}

/**
 * Sliding-window limiter, in memory (per function instance, best effort).
 *
 * @param {{max: number, windowMs: number}} limit
 */
function createRateLimiter({max, windowMs}) {
  /** @type {Map<string, number[]>} */
  const hits = new Map();
  return {
    /**
     * Records a hit and returns 0, or returns how many ms to wait.
     *
     * @param {string} key
     * @param {number} time
     */
    take(key, time) {
      const list = (hits.get(key) ?? []).filter((at) => at > time - windowMs);
      if (list.length >= max) {
        hits.set(key, list);
        return list[0] + windowMs - time;
      }
      list.push(time);
      hits.delete(key);
      hits.set(key, list);
      trimMap(hits, RATE_LIMIT_MAX_KEYS);
      return 0;
    },
  };
}

/**
 * Drops the oldest entries (Map keeps insertion order).
 *
 * @param {Map<string, unknown>} map
 * @param {number} max
 */
function trimMap(map, max) {
  while (map.size > max) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) {
      return;
    }
    map.delete(oldest);
  }
}

/**
 * @param {{ok: boolean, status: number, data: any}} response
 * @param {string} step
 */
function shaOf(response, step) {
  if (!response.ok || !response.data || typeof response.data.sha !== 'string') {
    throw new GitHubFailure(step, response.ok ? 502 : response.status);
  }
  return response.data.sha;
}

/** @param {string} path */
function encodePath(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

/** @param {string} value */
function sha256(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

/** @param {unknown} value */
function clean(value) {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * @param {unknown} value
 * @returns {string | null}
 */
function httpsUrl(value) {
  if (typeof value !== 'string') {
    return null;
  }
  try {
    return new URL(value).protocol === 'https:' ? value : null;
  } catch {
    return null;
  }
}
