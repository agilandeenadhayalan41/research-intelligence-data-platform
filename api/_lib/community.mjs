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
// - Writes: POST /api/community {action, page, body?, id?, login?}. Guests
//   come first: a write without Authorization is a GUEST write, identified by
//   the random per-browser id in `X-Guest-Id` (a UUID v4 the site keeps in
//   localStorage). Only its hash is kept ("guest:<16 hex of sha256>"), so a
//   guest can unlike their own like and delete their own comments, and nobody
//   else can. A visitor who wants to show who they are is a verified user:
//   first `resolveSessionUser(request)` (the sign-in's session cookie, which
//   api/community.mjs wires in; null by default), else a GitHub token as
//   `Authorization: Bearer <token>`, which is only used to call
//   GET https://api.github.com/user to learn the login and is never stored,
//   logged or sent anywhere else. Names never come from the request body. The
//   write is made with the server's own token (COMMUNITY_GITHUB_TOKEN).
// - Storage: one file per page, community/pages/<32 hex of sha256(pageId)>.json,
//   on the branch community-data (an orphan branch, created on the first
//   write). Every write is a commit made through the contents API with the
//   file's sha, so concurrent writers cannot overwrite each other: a 409/422
//   conflict re-reads the file and tries again.
//
// The module has no dependencies (node:crypto and the global fetch only) and
// every outside effect is injected, so `node --test` covers it without network
// access: createCommunityHandler({env, fetchImpl, now, random, sleep, newId,
// log, resolveSessionUser}).

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
/** Every write, per client IP (guests and users). */
export const WRITE_LIMIT = Object.freeze({max: 20, windowMs: 10 * 60 * 1000});
/** Guest writes, per client IP and guest id (on top of WRITE_LIMIT). */
export const GUEST_WRITE_LIMIT = Object.freeze({max: 10, windowMs: 10 * 60 * 1000});
export const READ_LIMIT = Object.freeze({max: 120, windowMs: 60 * 1000});
/** Retries after a write conflict (so at most MAX_RETRIES + 1 attempts). */
export const MAX_RETRIES = 4;

export const UNAUTHORIZED_ERROR = 'GitHub did not accept this token; replace it, or remove it to like and comment as a guest';
export const GUEST_ID_ERROR = 'X-Guest-Id must be a random UUID (version 4); the site sends one for guest likes and comments';
export const OWN_COMMENTS_ERROR = 'you can only delete your own comments';
export const CROSS_SITE_ERROR = 'a signed-in write must come from this site as application/json';
/** Request header with the guest's random per-browser id. */
export const GUEST_ID_HEADER = 'X-Guest-Id';
/** A UUID version 4 (any case). */
export const GUEST_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
/** Display name of every guest. */
export const GUEST_NAME = 'Guest';
export const BUSY_ERROR = 'busy, try again';
export const NOT_CONFIGURED_ERROR = 'shared likes and comments are not configured on this deployment';

/** Passed as `body` by createNodeHandler when the request body was over BODY_MAX_BYTES. */
export const BODY_TOO_LARGE = Symbol('community body too large');
/** Passed as `body` by createNodeHandler when the platform could not parse the JSON body. */
export const BODY_INVALID = Symbol('community body invalid');

const ACTIONS = new Set(['like', 'unlike', 'comment', 'delete']);
const LOGIN_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const GUEST_KEY_PATTERN = /^[0-9a-f]{16}$/;
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
 * A user's like is {login, at}; a guest's is {login: null, guestKey, at}.
 *
 * @typedef {object} StoredLike
 * @property {string | null} login GitHub login, or null for a guest.
 * @property {string} [guestKey] Guests only: guestKeyOf(the guest id), never the id itself.
 * @property {string} at ISO 8601.
 */

/**
 * A guest's comment has login null, name 'Guest', a guestKey and no avatar.
 *
 * @typedef {object} StoredComment
 * @property {string} id
 * @property {string | null} login GitHub login, or null for a guest.
 * @property {string} name
 * @property {string} [guestKey] Guests only: guestKeyOf(the guest id).
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
 * A verified user (from the session resolver or from a GitHub token).
 *
 * @typedef {object} Viewer
 * @property {string} login
 * @property {string} name
 * @property {string | null} avatarUrl
 */

/**
 * What resolveSessionUser returns for a signed-in visitor.
 *
 * @typedef {object} SessionUser
 * @property {string} login
 * @property {string | null} [name]
 * @property {string | null} [avatarUrl]
 * @property {string | null} [htmlUrl]
 */

/**
 * Who is asking. `user` acts when present; `guestKey` (from a valid
 * X-Guest-Id) still proves ownership of that guest's comments.
 *
 * @typedef {object} Caller
 * @property {Viewer | null} user
 * @property {string | null} guestKey
 */

/** @type {Caller} */
const NOBODY = Object.freeze({user: null, guestKey: null});

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

/** @param {unknown} value */
export function isGuestId(value) {
  return typeof value === 'string' && GUEST_ID_PATTERN.test(value);
}

/**
 * The only thing kept about a guest: the first 16 hex characters of the
 * SHA-256 of their id (lower case), so the id itself is never stored.
 *
 * @param {string} guestId A valid guest id.
 */
export function guestKeyOf(guestId) {
  return sha256(guestId.toLowerCase()).slice(0, 16);
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
    const owner = ownerOf(like);
    const key = owner && (owner.login ?? `guest:${owner.guestKey}`);
    if (owner && key && !seen.has(key)) {
      seen.add(key);
      const at = typeof like.at === 'string' ? like.at : '';
      likes.push(owner.login ? {login: owner.login, at} : {login: null, guestKey: owner.guestKey, at});
    }
  }
  /** @type {StoredComment[]} */
  const comments = [];
  for (const comment of Array.isArray(value.comments) ? value.comments : []) {
    const owner = ownerOf(comment);
    if (
      owner &&
      typeof comment.id === 'string' &&
      comment.id &&
      typeof comment.body === 'string' &&
      typeof comment.createdAt === 'string'
    ) {
      const id = comment.id.slice(0, ID_MAX_LENGTH);
      comments.push(
        owner.login
          ? {
              id,
              login: owner.login,
              name: cleanName(comment.name) ?? owner.login,
              avatarUrl: httpsUrl(comment.avatarUrl),
              body: comment.body,
              createdAt: comment.createdAt,
            }
          : {
              id,
              login: null,
              name: GUEST_NAME,
              guestKey: /** @type {string} */ (owner.guestKey),
              avatarUrl: null,
              body: comment.body,
              createdAt: comment.createdAt,
            },
      );
    }
  }
  return {version: FILE_VERSION, pageId, likes, comments};
}

/**
 * Whose a stored like or comment is: a valid login, or (login null) a valid
 * guest key. Anything else is dropped.
 *
 * @param {any} record
 * @returns {{login: string, guestKey: null} | {login: null, guestKey: string} | null}
 */
function ownerOf(record) {
  if (!record || typeof record !== 'object') {
    return null;
  }
  if (typeof record.login === 'string' && LOGIN_PATTERN.test(record.login)) {
    return {login: record.login, guestKey: null};
  }
  if (
    (record.login === null || record.login === undefined) &&
    typeof record.guestKey === 'string' &&
    GUEST_KEY_PATTERN.test(record.guestKey)
  ) {
    return {login: null, guestKey: record.guestKey};
  }
  return null;
}

/**
 * The like that belongs to the caller: the user's when one is acting, else
 * the guest's.
 *
 * @param {StoredLike} like
 * @param {Caller} caller
 */
function ownsLike(like, caller) {
  if (caller.user) {
    return like.login === caller.user.login;
  }
  return caller.guestKey !== null && like.login === null && like.guestKey === caller.guestKey;
}

/**
 * A verified user may delete their comments; a guest id (also sent along with
 * a user) may delete the comments written with it.
 *
 * @param {StoredComment} comment
 * @param {Caller} caller
 */
function ownsComment(comment, caller) {
  if (caller.user && comment.login === caller.user.login) {
    return true;
  }
  return caller.guestKey !== null && comment.login === null && comment.guestKey === caller.guestKey;
}

/**
 * @param {string} pageId
 * @returns {PageFile}
 */
export function emptyPageFile(pageId) {
  return {version: FILE_VERSION, pageId, likes: [], comments: []};
}

/**
 * What GET and POST return for a page, as seen by `caller`. Guest keys never
 * leave the server: `likes.likedBy` lists users' logins only (`count`
 * includes guests), and the caller's own like and comments show only as the
 * `likes.liked` and `canDelete` flags, computed for this request.
 *
 * @param {string} pageId
 * @param {PageFile} file
 * @param {Caller} [caller]
 */
export function pageView(pageId, file, caller = NOBODY) {
  return {
    configured: true,
    pageId,
    likes: {
      count: file.likes.length,
      likedBy: file.likes.flatMap((like) => (like.login ? [like.login] : [])),
      liked: file.likes.some((like) => ownsLike(like, caller)),
    },
    comments: file.comments.map((comment) => commentView(pageId, comment, caller)),
  };
}

/**
 * @param {string} pageId
 * @param {StoredComment} comment
 * @param {Caller} caller
 */
function commentView(pageId, comment, caller) {
  return {
    id: comment.id,
    pageId,
    author: {login: comment.login, name: comment.name, avatarUrl: comment.avatarUrl},
    body: comment.body,
    createdAt: comment.createdAt,
    canDelete: ownsComment(comment, caller),
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
 * @property {(request: HandlerRequest) => SessionUser | null | undefined | Promise<SessionUser | null | undefined>} [resolveSessionUser]
 *   The signed-in user of a request (for example from a session cookie), tried
 *   before the Bearer token. Default: nobody.
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
  resolveSessionUser = () => null,
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
  const guestLimiter = createRateLimiter(GUEST_WRITE_LIMIT);
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
    const viewer = toViewer({login: data.login, name: data.name, avatarUrl: data.avatar_url});
    if (!viewer) {
      return null;
    }
    loginCache.set(key, {expires: time + LOGIN_CACHE_MS, viewer});
    trimMap(loginCache, LOGIN_CACHE_MAX_ENTRIES);
    return viewer;
  }

  /**
   * The user signed in through the session resolver, checked (null when the
   * resolver has nobody or answers something that is not a user).
   *
   * @param {HandlerRequest} request
   * @returns {Promise<Viewer | null>}
   */
  async function sessionUser(request) {
    return toViewer(await resolveSessionUser(request));
  }

  /**
   * Read, change, compare-and-swap write; on a conflict, read again and retry.
   *
   * @param {string} pageId
   * @param {Caller} caller Whose view of the page is returned.
   * @param {string} action
   * @param {(file: PageFile) => {error: string, status: number} | {changed: boolean, extra?: (file: PageFile) => Record<string, unknown>}} change
   *   Mutates `file` in place.
   * @returns {Promise<HandlerResponse>}
   */
  async function write(pageId, caller, action, change) {
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
        return reply(200, {...pageView(pageId, file, caller), ...extra});
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
        return reply(200, {...pageView(pageId, file, caller), ...extra});
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
    // Only for the caller's own flags: a missing or malformed guest id, or a
    // failing session lookup, never stops a read.
    const guestId = header(request.headers, GUEST_ID_HEADER.toLowerCase())?.trim();
    /** @type {Viewer | null} */
    let user = null;
    try {
      user = await sessionUser(request);
    } catch {
      log('session lookup failed on a read');
    }
    const caller = {user, guestKey: isGuestId(guestId) ? guestKeyOf(/** @type {string} */ (guestId)) : null};
    return reply(200, pageView(page, await readCached(page), caller));
  }

  /**
   * @param {HandlerRequest} request
   * @param {string} ip
   */
  async function post(request, ip) {
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

    // Who is writing. The request body never names anyone: a verified user
    // comes from the session resolver or from GitHub (the token), a guest is
    // only the hash of their random id.
    const guestHeader = header(request.headers, GUEST_ID_HEADER.toLowerCase());
    /** @type {string | null} */
    let guestKey = null;
    if (guestHeader !== undefined) {
      const guestId = guestHeader.trim();
      if (!isGuestId(guestId)) {
        return badRequest(GUEST_ID_ERROR);
      }
      guestKey = guestKeyOf(guestId);
    }
    /** @type {Viewer | null} */
    let user = await sessionUser(request);
    let source = 'session';
    // A session cookie travels with any cross-site form or text/plain POST,
    // so a session write must be one a cross-site page cannot make without a
    // CORS preflight (which this function never grants).
    if (user && !sameSiteWrite(request.headers)) {
      return reply(403, {configured: true, error: CROSS_SITE_ERROR});
    }
    if (!user && header(request.headers, 'authorization')) {
      const token = bearerToken(request.headers);
      if (!token) {
        return unauthorized();
      }
      user = await verify(token);
      if (!user) {
        return unauthorized();
      }
      source = 'token';
    }
    if (!user && guestKey === null) {
      return badRequest(GUEST_ID_ERROR);
    }
    // The site's username sign-in is only typed: when the browser says who it
    // thinks it is, it must be the verified user. (A guest's `login` is ignored.)
    if (user && typeof parsed.login === 'string' && parsed.login.toLowerCase() !== user.login.toLowerCase()) {
      return reply(403, {
        configured: true,
        error:
          source === 'token'
            ? `this token belongs to @${user.login}; sign in as @${user.login} to like or comment`
            : `you are signed in as @${user.login}`,
      });
    }
    if (!user) {
      const wait = guestLimiter.take(`${ip}\n${guestKey}`, clock());
      if (wait > 0) {
        return tooManyRequests(wait);
      }
    }
    /** @type {Caller} */
    const caller = {user, guestKey};
    const actor = user;
    const actorGuestKey = /** @type {string} */ (guestKey);

    switch (action) {
      case 'like':
        return write(page, caller, action, (file) => {
          if (file.likes.some((like) => ownsLike(like, caller))) {
            return {changed: false};
          }
          file.likes.push(
            actor ? {login: actor.login, at: isoNow()} : {login: null, guestKey: actorGuestKey, at: isoNow()},
          );
          return {changed: true};
        });
      case 'unlike':
        return write(page, caller, action, (file) => {
          const index = file.likes.findIndex((like) => ownsLike(like, caller));
          if (index === -1) {
            return {changed: false};
          }
          file.likes.splice(index, 1);
          return {changed: true};
        });
      case 'comment': {
        const commentId = String(newId()).slice(0, ID_MAX_LENGTH);
        const text = /** @type {string} */ (body);
        const createdAt = isoNow();
        /** @type {StoredComment} */
        const comment = actor
          ? {id: commentId, login: actor.login, name: actor.name, avatarUrl: actor.avatarUrl, body: text, createdAt}
          : {id: commentId, login: null, name: GUEST_NAME, guestKey: actorGuestKey, avatarUrl: null, body: text, createdAt};
        const extra = () => ({comment: commentView(page, comment, caller)});
        return write(page, caller, action, (file) => {
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
        return write(page, caller, action, (file) => {
          const index = file.comments.findIndex((item) => item.id === id);
          if (index === -1) {
            return {status: 404, error: 'comment not found'};
          }
          if (!ownsComment(file.comments[index], caller)) {
            return {status: 403, error: OWN_COMMENTS_ERROR};
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
      return tooManyRequests(wait);
    }
    try {
      return method === 'GET' ? await get(request) : await post(request, ip);
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
    const secrets = [
      config.configured ? config.token : null,
      bearerToken(request?.headers),
      header(request?.headers, GUEST_ID_HEADER.toLowerCase())?.trim(),
    ];
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

/** @param {number} waitMs */
function tooManyRequests(waitMs) {
  const seconds = Math.max(1, Math.ceil(waitMs / 1000));
  return reply(
    429,
    {configured: true, error: `too many requests, try again in ${seconds} seconds`},
    {'Retry-After': String(seconds)},
  );
}

/**
 * A checked user from GitHub or from the session resolver, or null.
 *
 * @param {unknown} value {login, name?, avatarUrl?}
 * @returns {Viewer | null}
 */
function toViewer(value) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const {login, name, avatarUrl} = /** @type {Record<string, unknown>} */ (value);
  if (typeof login !== 'string' || !LOGIN_PATTERN.test(login)) {
    return null;
  }
  return {login, name: cleanName(name) ?? login, avatarUrl: httpsUrl(avatarUrl)};
}

/**
 * @param {unknown} value
 * @returns {string | null}
 */
function cleanName(value) {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, NAME_MAX_LENGTH) : null;
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

/**
 * Whether a write identified by a session cookie came from this site: it is
 * application/json (a cross-site page cannot send that without a preflight),
 * not marked cross-site by the browser, and any Origin is this host.
 *
 * @param {HandlerRequest['headers']} headers
 */
function sameSiteWrite(headers) {
  const type = (header(headers, 'content-type') ?? '').trim().toLowerCase();
  if (!/^application\/json\s*(;|$)/.test(type)) {
    return false;
  }
  if ((header(headers, 'sec-fetch-site') ?? '').trim().toLowerCase() === 'cross-site') {
    return false;
  }
  const origin = header(headers, 'origin');
  if (origin === undefined) {
    return true;
  }
  const host = (header(headers, 'x-forwarded-host') ?? header(headers, 'host') ?? '').split(',')[0].trim().toLowerCase();
  try {
    return Boolean(host) && new URL(origin).host.toLowerCase() === host;
  } catch {
    return false; // 'null' or not a URL
  }
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
