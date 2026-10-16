// Community data: likes, comments and blog-editor drafts.
//
// Two kinds of store sit behind one CommunityStore interface:
//
// - 'browser': everything is kept in the visitor's own browser: IndexedDB when
//   the browser offers it, else localStorage, else an in-memory store that
//   lasts until the tab is closed (private windows, blocked storage,
//   server-side rendering). Nothing is shared with other visitors.
// - 'shared' (createSharedStore): likes and comments go to the site's own
//   /api/community function (api/community.mjs at the repository root), which
//   keeps them as JSON files in the GitHub repository, shared by every
//   visitor. Writes carry the visitor's GitHub token so the function can check
//   who they are with GitHub. Drafts stay in the browser store. When the API
//   is not there or not configured (`npm start`, a deployment without the
//   server token), the shared store uses the browser store for everything.
//
// Components only see the CommunityStore interface below.
//
// This module is plain ESM with no imports so `node --test` can load it, and it
// touches no browser global at import time, so it is safe during SSR.

export const STORE_VERSION = 1;

/** userKey (and comment owner) used when nobody is signed in. */
export const GUEST = 'guest';

export const COMMENT_MAX_LENGTH = 5000;
export const COMMENT_LENGTH_ERROR = `comment must be 1-${COMMENT_MAX_LENGTH} characters`;

/** Prefix of every key the localStorage adapter writes. */
export const LOCAL_STORAGE_PREFIX = `rid-community:v${STORE_VERSION}:`;

export const DB_NAME = 'rid-community';
export const DB_VERSION = 1;

/** @type {readonly StoreName[]} */
export const OBJECT_STORES = Object.freeze(['likes', 'comments', 'drafts']);

const NAME_MAX_LENGTH = 100;
const KEY_MAX_LENGTH = 1000;

/** @typedef {'likes' | 'comments' | 'drafts'} StoreName */
/** @typedef {'indexedDB' | 'localStorage' | 'memory'} BrowserBackendKind */
/** @typedef {'shared' | BrowserBackendKind} BackendKind */
/** @typedef {'md' | 'mdx'} DraftFormat */

/**
 * @typedef {object} Likes
 * @property {number} count Number of distinct users who like the page.
 * @property {string[]} likedBy userKeys (GitHub logins, or 'guest').
 */

/**
 * @typedef {object} CommentAuthor
 * @property {string | null} login GitHub login, or null for a guest.
 * @property {string} name Display name.
 * @property {string | null} avatarUrl https URL of the avatar, or null.
 */

/**
 * @typedef {object} Comment
 * @property {string} id
 * @property {string} pageId
 * @property {CommentAuthor} author
 * @property {string} body Trimmed text, 1-5000 characters.
 * @property {string} createdAt ISO 8601 timestamp.
 */

/**
 * @typedef {object} NewComment
 * @property {Partial<CommentAuthor> | null} [author] Missing or without a login: a guest comment.
 * @property {string} body
 */

/**
 * @typedef {object} Draft
 * @property {string} key
 * @property {string} content
 * @property {DraftFormat} format
 * @property {string} updatedAt ISO 8601 timestamp.
 */

/**
 * @typedef {object} DraftInput
 * @property {string} content
 * @property {DraftFormat} format
 */

/**
 * The interface every component uses. All methods are async and can be
 * called unbound (`const {getLikes} = store`).
 *
 * @typedef {object} CommunityStore
 * @property {() => Promise<BackendKind>} backend Where likes and comments are kept: 'shared' (the site's GitHub repository), or the browser storage in use.
 * @property {() => Promise<BrowserBackendKind>} draftsBackend Where editor drafts are kept (always in this browser).
 * @property {(pageId: string) => Promise<Likes>} getLikes
 * @property {(pageId: string, userKey: string) => Promise<Likes>} toggleLike Adds or removes userKey's like.
 * @property {(pageId: string) => Promise<Comment[]>} listComments Oldest first.
 * @property {(pageId: string, input: NewComment) => Promise<Comment>} addComment Rejects with Error('comment must be 1-5000 characters').
 * @property {(pageId: string, id: string, userKey: string) => Promise<boolean>} deleteComment Only the author may delete; false when not found or not allowed.
 * @property {(key: string) => Promise<Draft | null>} getDraft
 * @property {(key: string, input: DraftInput) => Promise<Draft>} saveDraft
 * @property {() => Promise<Draft[]>} listDrafts Newest first.
 * @property {(key: string) => Promise<boolean>} deleteDraft True when a draft was removed.
 */

/**
 * @typedef {object} StoreOptions
 * @property {() => Date | number} [now] Clock, for tests.
 * @property {() => string} [newId] Comment id generator, for tests.
 */

/**
 * Minimal Web Storage shape (window.localStorage satisfies it).
 *
 * @typedef {object} StorageLike
 * @property {(key: string) => string | null} getItem
 * @property {(key: string, value: string) => void} setItem
 * @property {(key: string) => void} removeItem
 * @property {(index: number) => string | null} key
 * @property {number} length
 */

/**
 * What `change` returns inside Backend.update: `result` is handed back to the
 * caller; `next` undefined leaves the record alone, null deletes it, anything
 * else is written.
 *
 * @template T
 * @typedef {{result: T, next?: unknown}} Change
 */

/**
 * Key-value storage behind a CommunityStore. `update` is a read-modify-write
 * that runs atomically (one IndexedDB readwrite transaction, or one
 * synchronous step for the other backends).
 *
 * @typedef {object} Backend
 * @property {BrowserBackendKind} kind
 * @property {(store: StoreName, key: string) => Promise<unknown>} get
 * @property {(store: StoreName) => Promise<unknown[]>} getAll
 * @property {<T>(store: StoreName, key: string, change: (current: unknown) => Change<T>) => Promise<T>} update
 */

// ---------------------------------------------------------------------------
// Public factories

/**
 * Map-based store. Used for tests and whenever browser storage is
 * unavailable; the data lasts as long as the page.
 *
 * @param {StoreOptions} [options]
 * @returns {CommunityStore}
 */
export function createMemoryStore(options = {}) {
  const backend = createMemoryBackend();
  return createStore(async () => backend, options);
}

/**
 * Store over a Web Storage-like object (normally window.localStorage). Every
 * key starts with 'rid-community:v1:'.
 *
 * @param {StorageLike} storage
 * @param {StoreOptions} [options]
 * @returns {CommunityStore}
 */
export function createLocalStorageStore(storage, options = {}) {
  if (!storage || typeof storage.getItem !== 'function') {
    throw new TypeError('createLocalStorageStore needs a Web Storage-like object');
  }
  const backend = createLocalStorageBackend(storage);
  return createStore(async () => backend, options);
}

/**
 * Store over IndexedDB: database 'rid-community', version 1, object stores
 * 'likes', 'comments' and 'drafts'. The database is opened on first use; if it
 * cannot be opened, every call rejects with that error.
 *
 * @param {IDBFactory} indexedDB
 * @param {StoreOptions} [options]
 * @returns {CommunityStore}
 */
export function createIndexedDbStore(indexedDB, options = {}) {
  if (!indexedDB || typeof indexedDB.open !== 'function') {
    throw new TypeError('createIndexedDbStore needs an IDBFactory');
  }
  const backend = createIndexedDbBackend(indexedDB);
  return createStore(async () => backend, options);
}

/**
 * @typedef {object} CommunityStoreConfig
 * @property {string} [kind] 'shared' or 'browser' (see customFields.communityStore).
 * @property {any} [env] Where to look for window, indexedDB, localStorage and fetch (globalThis by default).
 * @property {number} [openTimeoutMs] Give up on IndexedDB if it has not opened by then (default 3000).
 * @property {() => Date | number} [now]
 * @property {() => string} [newId]
 * @property {string} [baseUrl] 'shared' only: the API path (default '/api/community').
 * @property {() => string | null | undefined} [getToken] 'shared' only: the visitor's GitHub token, read at each write.
 * @property {FetchLike} [fetchImpl] 'shared' only: defaults to env.fetch.
 */

/**
 * The store the site uses.
 *
 * 'browser': IndexedDB if it opens, else localStorage if it accepts writes,
 * else memory.
 *
 * 'shared': likes and comments through the site's /api/community (see
 * createSharedStore), drafts in the 'browser' store; everything in the
 * 'browser' store when the API does not answer `configured: true`.
 *
 * Without a window (SSR, Node) both return a memory store. Neither throws, and
 * neither touches storage or the network until the first call, so they are
 * safe to create during render. Any other kind throws
 * `unknown community store: <kind>`.
 *
 * @param {CommunityStoreConfig} [config]
 * @returns {CommunityStore}
 */
export function createCommunityStore({
  kind = 'browser',
  env = globalThis,
  openTimeoutMs = 3000,
  now,
  newId,
  baseUrl,
  getToken,
  fetchImpl,
} = {}) {
  if (kind !== 'browser' && kind !== 'shared') {
    throw new Error(`unknown community store: ${kind}`);
  }
  const options = {now, newId};
  if (!env || !readGlobal(env, 'window')) {
    return createMemoryStore(options);
  }

  /** @type {Promise<Backend> | null} */
  let chosen = null;
  const getBackend = () => {
    if (!chosen) {
      chosen = chooseBrowserBackend(env, openTimeoutMs);
    }
    return chosen;
  };
  const browser = createStore(getBackend, options);
  if (kind === 'browser') {
    return browser;
  }
  return createSharedStore({
    baseUrl,
    getToken,
    fetchImpl: fetchImpl ?? fetchFrom(env),
    fallback: browser,
  });
}

// ---------------------------------------------------------------------------
// The shared store (the site's /api/community)

/** Path of the site's community API (api/community.mjs at the repository root). */
export const SHARED_API_PATH = '/api/community';

/** How long a GET answer is reused (getLikes and listComments of one render). */
const RECENT_MS = 2000;

/** What the API answers to a write without a valid token. */
export const SIGN_IN_ERROR = 'sign in with a GitHub token to like or comment';

/**
 * @typedef {(url: string, init?: {method?: string, headers?: Record<string, string>, body?: string, cache?: string, credentials?: string, redirect?: string, signal?: AbortSignal}) => Promise<{status: number, ok: boolean, json: () => Promise<any>}>} FetchLike
 */

/**
 * @typedef {object} SharedStoreOptions
 * @property {string} [baseUrl] A path on this site (default '/api/community'); never another origin, so the token stays on this site.
 * @property {() => string | null | undefined} [getToken] The visitor's GitHub token, read at each write. It is sent only with writes.
 * @property {FetchLike} [fetchImpl] Defaults to the global fetch.
 * @property {CommunityStore} [fallback] The browser store: drafts always, everything when the API is not configured. Defaults to a memory store.
 * @property {() => Date | number} [now] Clock, for tests.
 */

/** A failed /api/community request. `message` is the API's (it never holds a token). */
export class CommunityApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status HTTP status, or 0 when the site could not be reached.
   */
  constructor(message, status) {
    super(message);
    this.name = 'CommunityApiError';
    /** @type {number} */
    this.status = status;
  }
}

/**
 * The API's page id for a store pageId: a site path with a leading slash,
 * characters outside [A-Za-z0-9/_.~-] percent-encoded. The site's page ids
 * are repository paths, so 'docs/overview/introduction.md' becomes
 * '/docs/overview/introduction.md'.
 *
 * @param {string} pageId
 * @returns {string}
 */
export function toApiPageId(pageId) {
  checkPageId(pageId);
  const path = pageId.startsWith('/') ? pageId : `/${pageId}`;
  try {
    return path.replace(/[^A-Za-z0-9/_.~-]/gu, (char) =>
      encodeURIComponent(char).replace(
        /[!'()*]/g,
        (rest) => `%${rest.charCodeAt(0).toString(16).toUpperCase()}`,
      ),
    );
  } catch {
    throw new Error('pageId must be a non-empty string');
  }
}

/**
 * Likes and comments shared by every visitor, through the site's
 * /api/community (api/community.mjs), which keeps them in the GitHub
 * repository.
 *
 * - Reads (getLikes, listComments) need no sign-in. getLikes and listComments
 *   for the same page share one request.
 * - Writes send the visitor's token as `Authorization: Bearer <token>`, and
 *   the signed-in username as `login`; the API checks the token with GitHub
 *   and answers 403 when it belongs to someone else. toggleLike sends 'like'
 *   or 'unlike' depending on the last state it saw. Failed requests reject
 *   with CommunityApiError (status 401 without a token, without a request).
 * - Drafts always use `fallback`.
 * - backend() asks GET <baseUrl> once: 'shared' when it answers
 *   `configured: true`; otherwise (no API, not configured, offline) every
 *   method uses `fallback` and backend() is the fallback's.
 *
 * @param {SharedStoreOptions} [options]
 * @returns {CommunityStore}
 */
export function createSharedStore({
  baseUrl = SHARED_API_PATH,
  getToken,
  fetchImpl,
  fallback,
  now,
} = {}) {
  if (typeof baseUrl !== 'string' || !baseUrl.startsWith('/') || baseUrl.startsWith('//')) {
    throw new TypeError('baseUrl must be a path on this site, such as /api/community');
  }
  const local = fallback ?? createMemoryStore();
  /** @type {FetchLike} */
  const send = fetchImpl ?? ((url, init) => globalThis.fetch(url, /** @type {any} */ (init)));
  const clock = () => toDate(now ? now() : new Date()).getTime();

  /** @type {Promise<'shared' | 'local'> | null} */
  let mode = null;
  /** In-flight or just-answered GETs, so getLikes + listComments make one request. */
  /** @type {Map<string, {at: number, promise: Promise<SharedPage>}>} */
  const recent = new Map();
  /** The last state seen for each page (toggleLike decides like/unlike from it). */
  /** @type {Map<string, SharedPage>} */
  const known = new Map();

  const resolveMode = () => {
    mode ??= probe();
    return mode;
  };
  const isShared = async () => (await resolveMode()) === 'shared';

  /** @returns {Promise<'shared' | 'local'>} */
  async function probe() {
    try {
      const {data} = await call('GET', baseUrl);
      return data && data.configured === true ? 'shared' : 'local';
    } catch {
      return 'local';
    }
  }

  /**
   * @param {'GET' | 'POST'} method
   * @param {string} url
   * @param {{token?: string, body?: Record<string, unknown>}} [options]
   */
  async function call(method, url, {token, body} = {}) {
    /** @type {Record<string, string>} */
    const headers = {Accept: 'application/json'};
    /** @type {Parameters<FetchLike>[1] & {headers: Record<string, string>}} */
    const init = {method, headers, cache: 'no-store', credentials: 'same-origin'};
    if (body) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (token) {
      headers.Authorization = `Bearer ${token}`;
      init.redirect = 'error'; // never follow a redirect with the token
    }
    const signal = timeoutSignal(15000);
    if (signal) {
      init.signal = signal;
    }
    const response = await send(url, init);
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null; // not JSON (for example the dev server's HTML page)
    }
    return {status: response.status, ok: response.ok, data};
  }

  /** @param {string} apiPage */
  const pageUrl = (apiPage) => `${baseUrl}?page=${encodeURIComponent(apiPage)}`;

  /**
   * @param {string} apiPage
   * @returns {Promise<SharedPage>}
   */
  function fetchPage(apiPage) {
    const hit = recent.get(apiPage);
    if (hit && clock() - hit.at < RECENT_MS) {
      return hit.promise;
    }
    const promise = (async () => {
      let response;
      try {
        response = await call('GET', pageUrl(apiPage));
      } catch {
        throw new CommunityApiError('could not reach the site', 0);
      }
      if (!response.ok) {
        throw apiError(response, 'could not load the likes and comments');
      }
      return readSharedPage(response.data);
    })();
    recent.set(apiPage, {at: clock(), promise});
    promise.then(
      (page) => known.set(apiPage, page),
      () => {
        if (recent.get(apiPage)?.promise === promise) {
          recent.delete(apiPage);
        }
      },
    );
    return promise;
  }

  /**
   * @param {string} action
   * @param {string} apiPage
   * @param {Record<string, unknown>} fields
   */
  async function post(action, apiPage, fields) {
    const token = readToken(getToken);
    if (!token) {
      throw new CommunityApiError(SIGN_IN_ERROR, 401);
    }
    let response;
    try {
      response = await call('POST', baseUrl, {token, body: {action, page: apiPage, ...fields}});
    } catch {
      throw new CommunityApiError('could not reach the site', 0);
    }
    if (!response.ok) {
      throw apiError(response, 'could not save the change');
    }
    const page = readSharedPage(response.data);
    known.set(apiPage, page);
    recent.set(apiPage, {at: clock(), promise: Promise.resolve(page)});
    return {page, data: response.data};
  }

  return {
    async backend() {
      return (await isShared()) ? 'shared' : local.backend();
    },

    draftsBackend() {
      return local.draftsBackend();
    },

    async getLikes(pageId) {
      if (!(await isShared())) {
        return local.getLikes(pageId);
      }
      return copyLikes((await fetchPage(toApiPageId(pageId))).likes);
    },

    async toggleLike(pageId, userKey) {
      if (!(await isShared())) {
        return local.toggleLike(pageId, userKey);
      }
      const apiPage = toApiPageId(pageId);
      checkUserKey(userKey);
      const current = known.get(apiPage) ?? (await fetchPage(apiPage));
      const action = current.likes.likedBy.includes(userKey) ? 'unlike' : 'like';
      const {page} = await post(action, apiPage, {login: userKey});
      return copyLikes(page.likes);
    },

    async listComments(pageId) {
      if (!(await isShared())) {
        return local.listComments(pageId);
      }
      const {comments} = await fetchPage(toApiPageId(pageId));
      return comments
        .map((comment) => copyComment({...comment, pageId}))
        .sort((a, b) => compareStrings(a.createdAt, b.createdAt));
    },

    async addComment(pageId, input) {
      if (!(await isShared())) {
        return local.addComment(pageId, input);
      }
      const apiPage = toApiPageId(pageId);
      const body = normalizeBody(input ? input.body : undefined);
      const login = input && input.author && isNonEmptyString(input.author.login) ? input.author.login : undefined;
      const {data} = await post('comment', apiPage, login ? {body, login} : {body});
      const comment = data && data.comment;
      if (!isComment(comment)) {
        throw new CommunityApiError('the site answered without the new comment', 502);
      }
      return copyComment({...comment, pageId});
    },

    async deleteComment(pageId, id, userKey) {
      if (!(await isShared())) {
        return local.deleteComment(pageId, id, userKey);
      }
      const apiPage = toApiPageId(pageId);
      if (!isNonEmptyString(id) || !isNonEmptyString(userKey)) {
        return false;
      }
      try {
        await post('delete', apiPage, {id, login: userKey});
        return true;
      } catch (error) {
        if (error instanceof CommunityApiError && (error.status === 403 || error.status === 404)) {
          return false;
        }
        throw error;
      }
    },

    getDraft: (key) => local.getDraft(key),
    saveDraft: (key, input) => local.saveDraft(key, input),
    listDrafts: () => local.listDrafts(),
    deleteDraft: (key) => local.deleteDraft(key),
  };
}

/**
 * @typedef {object} SharedPage
 * @property {Likes} likes
 * @property {Comment[]} comments
 */

/**
 * The page state in an API answer, checked (it is input from the network).
 *
 * @param {any} data
 * @returns {SharedPage}
 */
function readSharedPage(data) {
  const likedBy =
    data && data.likes && Array.isArray(data.likes.likedBy)
      ? [...new Set(data.likes.likedBy.filter(isNonEmptyString))]
      : [];
  const comments = data && Array.isArray(data.comments) ? data.comments.filter(isComment).map(copyComment) : [];
  return {likes: likesFrom(likedBy), comments};
}

/**
 * @param {{status: number, data: any}} response
 * @param {string} fallbackMessage
 */
function apiError(response, fallbackMessage) {
  const message =
    response.data && typeof response.data.error === 'string' && response.data.error
      ? response.data.error.slice(0, 300)
      : `${fallbackMessage} (HTTP ${response.status})`;
  return new CommunityApiError(message, response.status);
}

/** @param {(() => string | null | undefined) | undefined} getToken */
function readToken(getToken) {
  try {
    const value = typeof getToken === 'function' ? getToken() : null;
    return typeof value === 'string' && value.trim() ? value.trim() : null;
  } catch {
    return null;
  }
}

/** @param {Likes} likes */
function copyLikes(likes) {
  return likesFrom(likes.likedBy);
}

/**
 * @param {any} env
 * @returns {FetchLike | undefined}
 */
function fetchFrom(env) {
  const fetchFn = readGlobal(env, 'fetch');
  return typeof fetchFn === 'function' ? (url, init) => fetchFn.call(env, url, init) : undefined;
}

/** @param {number} ms */
function timeoutSignal(ms) {
  try {
    return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
      ? AbortSignal.timeout(ms)
      : undefined;
  } catch {
    return undefined;
  }
}

/**
 * True when userKey may delete the comment: its author's login, or 'guest'
 * for a comment written while signed out.
 *
 * @param {Pick<Comment, 'author'>} comment
 * @param {string} userKey
 * @returns {boolean}
 */
export function canDeleteComment(comment, userKey) {
  if (!comment || !comment.author || !isNonEmptyString(userKey)) {
    return false;
  }
  const owner = comment.author.login ?? GUEST;
  return owner === userKey;
}

// ---------------------------------------------------------------------------
// The store (shared by every backend)

/**
 * @param {() => Promise<Backend>} getBackend
 * @param {StoreOptions} options
 * @returns {CommunityStore}
 */
function createStore(getBackend, {now, newId} = {}) {
  const clock = now ?? (() => new Date());
  const makeId = newId ?? defaultId;
  const timestamp = () => toDate(clock()).toISOString();

  return {
    async backend() {
      return (await getBackend()).kind;
    },

    async draftsBackend() {
      return (await getBackend()).kind;
    },

    async getLikes(pageId) {
      checkPageId(pageId);
      const backend = await getBackend();
      return likesFrom(readLikedBy(await backend.get('likes', pageId)));
    },

    async toggleLike(pageId, userKey) {
      checkPageId(pageId);
      checkUserKey(userKey);
      const backend = await getBackend();
      return backend.update('likes', pageId, (current) => {
        const likedBy = readLikedBy(current);
        const index = likedBy.indexOf(userKey);
        if (index === -1) {
          likedBy.push(userKey);
        } else {
          likedBy.splice(index, 1);
        }
        return {
          result: likesFrom(likedBy),
          next: likedBy.length > 0 ? {pageId, likedBy} : null,
        };
      });
    },

    async listComments(pageId) {
      checkPageId(pageId);
      const backend = await getBackend();
      const comments = readComments(await backend.get('comments', pageId));
      // Array.prototype.sort is stable: equal timestamps keep insertion order.
      return comments.sort((a, b) => compareStrings(a.createdAt, b.createdAt));
    },

    async addComment(pageId, input) {
      checkPageId(pageId);
      const body = normalizeBody(input ? input.body : undefined);
      /** @type {Comment} */
      const comment = {
        id: String(makeId()),
        pageId,
        author: normalizeAuthor(input ? input.author : null),
        body,
        createdAt: timestamp(),
      };
      const backend = await getBackend();
      await backend.update('comments', pageId, (current) => {
        const comments = readComments(current);
        comments.push(comment);
        return {result: undefined, next: {pageId, comments}};
      });
      return copyComment(comment);
    },

    async deleteComment(pageId, id, userKey) {
      checkPageId(pageId);
      if (!isNonEmptyString(id) || !isNonEmptyString(userKey)) {
        return false;
      }
      const backend = await getBackend();
      return backend.update('comments', pageId, (current) => {
        const comments = readComments(current);
        const index = comments.findIndex((comment) => comment.id === id);
        if (index === -1 || !canDeleteComment(comments[index], userKey)) {
          return {result: false};
        }
        comments.splice(index, 1);
        return {
          result: true,
          next: comments.length > 0 ? {pageId, comments} : null,
        };
      });
    },

    async getDraft(key) {
      checkDraftKey(key);
      const backend = await getBackend();
      const draft = await backend.get('drafts', key);
      return isDraft(draft) ? copyDraft(draft) : null;
    },

    async saveDraft(key, input) {
      checkDraftKey(key);
      const content = input ? input.content : undefined;
      const format = input ? input.format : undefined;
      if (typeof content !== 'string') {
        throw new Error('draft content must be a string');
      }
      if (format !== 'md' && format !== 'mdx') {
        throw new Error("draft format must be 'md' or 'mdx'");
      }
      /** @type {Draft} */
      const draft = {key, content, format, updatedAt: timestamp()};
      const backend = await getBackend();
      await backend.update('drafts', key, () => ({result: undefined, next: draft}));
      return copyDraft(draft);
    },

    async listDrafts() {
      const backend = await getBackend();
      const drafts = (await backend.getAll('drafts')).filter(isDraft);
      return drafts
        .map(copyDraft)
        .sort(
          (a, b) =>
            compareStrings(b.updatedAt, a.updatedAt) || compareStrings(a.key, b.key),
        );
    },

    async deleteDraft(key) {
      checkDraftKey(key);
      const backend = await getBackend();
      return backend.update('drafts', key, (current) =>
        current === undefined ? {result: false} : {result: true, next: null},
      );
    },
  };
}

// ---------------------------------------------------------------------------
// Backends

/** @returns {Backend} */
function createMemoryBackend() {
  /** @type {Map<StoreName, Map<string, string>>} */
  const tables = new Map(OBJECT_STORES.map((name) => [name, new Map()]));
  /** @param {StoreName} store */
  const table = (store) => {
    const found = tables.get(store);
    if (!found) {
      throw new Error(`unknown object store: ${store}`);
    }
    return found;
  };
  // Values are kept as JSON so callers never share objects with the store.
  /** @param {string | undefined} raw */
  const parse = (raw) => (raw === undefined ? undefined : JSON.parse(raw));

  return {
    kind: 'memory',
    async get(store, key) {
      return parse(table(store).get(key));
    },
    async getAll(store) {
      return [...table(store).values()].map(parse);
    },
    async update(store, key, change) {
      const rows = table(store);
      const outcome = change(parse(rows.get(key)));
      if (outcome.next === null) {
        rows.delete(key);
      } else if (outcome.next !== undefined) {
        rows.set(key, JSON.stringify(outcome.next));
      }
      return outcome.result;
    },
  };
}

/**
 * @param {StorageLike} storage
 * @returns {Backend}
 */
function createLocalStorageBackend(storage) {
  /** @param {StoreName} store */
  const prefixOf = (store) => `${LOCAL_STORAGE_PREFIX}${store}:`;
  /** @param {string} itemKey */
  const read = (itemKey) => {
    const raw = storage.getItem(itemKey);
    if (raw === null || raw === undefined) {
      return undefined;
    }
    try {
      return JSON.parse(raw);
    } catch {
      return undefined; // a corrupted entry reads as missing
    }
  };

  return {
    kind: 'localStorage',
    async get(store, key) {
      return read(prefixOf(store) + key);
    },
    async getAll(store) {
      const prefix = prefixOf(store);
      /** @type {string[]} */
      const keys = [];
      for (let index = 0; index < storage.length; index += 1) {
        const itemKey = storage.key(index);
        if (itemKey !== null && itemKey.startsWith(prefix)) {
          keys.push(itemKey);
        }
      }
      return keys.map(read).filter((value) => value !== undefined);
    },
    async update(store, key, change) {
      const itemKey = prefixOf(store) + key;
      const outcome = change(read(itemKey));
      if (outcome.next === null) {
        storage.removeItem(itemKey);
      } else if (outcome.next !== undefined) {
        try {
          storage.setItem(itemKey, JSON.stringify(outcome.next));
        } catch {
          throw new Error('could not save in this browser: storage is full or blocked');
        }
      }
      return outcome.result;
    },
  };
}

/**
 * @param {IDBFactory} factory
 * @returns {Backend & {ready: () => Promise<void>}}
 */
function createIndexedDbBackend(factory) {
  /** @type {Promise<IDBDatabase> | null} */
  let opening = null;

  /** @returns {Promise<IDBDatabase>} */
  function open() {
    if (opening) {
      return opening;
    }
    const attempt = new Promise((resolve, reject) => {
      /** @type {IDBOpenDBRequest} */
      let request;
      try {
        request = factory.open(DB_NAME, DB_VERSION);
      } catch (error) {
        reject(asError(error, 'IndexedDB could not be opened'));
        return;
      }
      request.onupgradeneeded = () => {
        const db = request.result;
        for (const name of OBJECT_STORES) {
          if (!db.objectStoreNames.contains(name)) {
            db.createObjectStore(name);
          }
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        // Let a newer version of the site upgrade the database in another tab.
        db.onversionchange = () => {
          db.close();
          opening = null;
        };
        db.onclose = () => {
          opening = null;
        };
        resolve(db);
      };
      request.onerror = () => {
        reject(asError(request.error, 'IndexedDB could not be opened'));
      };
    });
    opening = attempt;
    attempt.catch(() => {
      if (opening === attempt) {
        opening = null; // allow a later retry
      }
    });
    return attempt;
  }

  /**
   * Runs `body` in one transaction and resolves with what it set as the
   * result once the transaction has committed.
   *
   * @template T
   * @param {StoreName} store
   * @param {IDBTransactionMode} mode
   * @param {(objectStore: IDBObjectStore, done: (value: T) => void, fail: (error: unknown) => void) => void} body
   * @returns {Promise<T>}
   */
  async function run(store, mode, body) {
    const db = await open();
    return new Promise((resolve, reject) => {
      /** @type {IDBTransaction} */
      let tx;
      try {
        tx = db.transaction(store, mode);
      } catch (error) {
        reject(asError(error, 'IndexedDB transaction failed'));
        return;
      }
      /** @type {T} */
      let result;
      /** @type {Error | null} */
      let failure = null;
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => {
        reject(failure ?? asError(tx.error, 'IndexedDB transaction was aborted'));
      };
      body(
        tx.objectStore(store),
        (value) => {
          result = value;
        },
        (error) => {
          failure = asError(error, 'IndexedDB transaction failed');
          try {
            tx.abort();
          } catch {
            reject(failure);
          }
        },
      );
    });
  }

  return {
    kind: 'indexedDB',
    async ready() {
      await open();
    },
    get(store, key) {
      return run(store, 'readonly', (objectStore, done) => {
        const request = objectStore.get(key);
        request.onsuccess = () => done(request.result);
      });
    },
    getAll(store) {
      return run(store, 'readonly', (objectStore, done) => {
        const request = objectStore.getAll();
        request.onsuccess = () => done(request.result);
      });
    },
    update(store, key, change) {
      return run(store, 'readwrite', (objectStore, done, fail) => {
        const request = objectStore.get(key);
        request.onsuccess = () => {
          let outcome;
          try {
            outcome = change(request.result);
          } catch (error) {
            fail(error);
            return;
          }
          done(outcome.result);
          if (outcome.next === null) {
            objectStore.delete(key);
          } else if (outcome.next !== undefined) {
            objectStore.put(outcome.next, key);
          }
        };
      });
    },
  };
}

/**
 * @param {any} env
 * @param {number} openTimeoutMs
 * @returns {Promise<Backend>}
 */
async function chooseBrowserBackend(env, openTimeoutMs) {
  const factory = readGlobal(env, 'indexedDB');
  if (factory && typeof factory.open === 'function') {
    const backend = createIndexedDbBackend(factory);
    try {
      await withTimeout(backend.ready(), openTimeoutMs);
      return backend;
    } catch {
      // Private mode, disabled storage or a hung open: try the next option.
    }
  }
  const storage = usableStorage(readGlobal(env, 'localStorage'));
  if (storage) {
    return createLocalStorageBackend(storage);
  }
  return createMemoryBackend();
}

// ---------------------------------------------------------------------------
// Helpers

/**
 * Reading window.localStorage can throw (SecurityError) when site data is
 * blocked, so every global is read through this.
 *
 * @param {any} env
 * @param {string} name
 * @returns {any}
 */
function readGlobal(env, name) {
  try {
    return env[name];
  } catch {
    return undefined;
  }
}

/**
 * @param {any} storage
 * @returns {StorageLike | null}
 */
function usableStorage(storage) {
  if (!storage || typeof storage.setItem !== 'function') {
    return null;
  }
  const probe = `${LOCAL_STORAGE_PREFIX}__probe__`;
  try {
    storage.setItem(probe, '1');
    storage.removeItem(probe);
    return storage;
  } catch {
    return null; // e.g. Safari private mode, quota 0
  }
}

/**
 * @template T
 * @param {Promise<T>} promise
 * @param {number} ms
 * @returns {Promise<T>}
 */
function withTimeout(promise, ms) {
  if (!(Number.isFinite(ms) && ms > 0)) {
    return promise;
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('IndexedDB did not open in time')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/**
 * @param {unknown} error
 * @param {string} fallback
 * @returns {Error}
 */
function asError(error, fallback) {
  if (error instanceof Error) {
    return error;
  }
  if (error && typeof error === 'object' && 'message' in error && error.message) {
    return new Error(`${fallback}: ${String(error.message)}`);
  }
  return new Error(fallback);
}

/** @returns {string} */
function defaultId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    try {
      return cryptoApi.randomUUID();
    } catch {
      // randomUUID needs a secure context in browsers; fall through.
    }
  }
  const random = Math.random().toString(36).slice(2, 12);
  return `${Date.now().toString(36)}-${random}`;
}

/**
 * @param {Date | number} value
 * @returns {Date}
 */
function toDate(value) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

/**
 * @param {unknown} value
 * @returns {value is string}
 */
function isNonEmptyString(value) {
  return typeof value === 'string' && value.length > 0;
}

/**
 * @param {string} a
 * @param {string} b
 */
function compareStrings(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** @param {unknown} pageId */
function checkPageId(pageId) {
  if (!isNonEmptyString(pageId) || pageId.length > KEY_MAX_LENGTH) {
    throw new Error('pageId must be a non-empty string');
  }
}

/** @param {unknown} userKey */
function checkUserKey(userKey) {
  if (!isNonEmptyString(userKey) || userKey.length > NAME_MAX_LENGTH) {
    throw new Error('userKey must be a non-empty string');
  }
}

/** @param {unknown} key */
function checkDraftKey(key) {
  if (!isNonEmptyString(key) || key.length > KEY_MAX_LENGTH) {
    throw new Error('draft key must be a non-empty string');
  }
}

/**
 * Trims and checks the length in characters (code points, so an emoji
 * counts once).
 *
 * @param {unknown} body
 * @returns {string}
 */
function normalizeBody(body) {
  if (typeof body !== 'string') {
    throw new Error(COMMENT_LENGTH_ERROR);
  }
  const trimmed = body.trim();
  const length = Array.from(trimmed).length;
  if (length < 1 || length > COMMENT_MAX_LENGTH) {
    throw new Error(COMMENT_LENGTH_ERROR);
  }
  return trimmed;
}

/**
 * @param {Partial<CommentAuthor> | null | undefined} author
 * @returns {CommentAuthor}
 */
function normalizeAuthor(author) {
  const login =
    author && typeof author.login === 'string' && author.login.trim()
      ? author.login.trim().slice(0, NAME_MAX_LENGTH)
      : null;
  const name =
    author && typeof author.name === 'string' && author.name.trim()
      ? author.name.trim().slice(0, NAME_MAX_LENGTH)
      : (login ?? 'Guest');
  const avatarUrl =
    author && typeof author.avatarUrl === 'string' && /^https:\/\//i.test(author.avatarUrl)
      ? author.avatarUrl
      : null;
  return {login, name, avatarUrl};
}

/**
 * @param {string[]} likedBy
 * @returns {Likes}
 */
function likesFrom(likedBy) {
  return {count: likedBy.length, likedBy: [...likedBy]};
}

/**
 * @param {unknown} record
 * @returns {string[]}
 */
function readLikedBy(record) {
  const list =
    record && typeof record === 'object' && 'likedBy' in record && Array.isArray(record.likedBy)
      ? record.likedBy
      : [];
  return [...new Set(list.filter(isNonEmptyString))];
}

/**
 * @param {unknown} record
 * @returns {Comment[]}
 */
function readComments(record) {
  const list =
    record && typeof record === 'object' && 'comments' in record && Array.isArray(record.comments)
      ? record.comments
      : [];
  return list.filter(isComment).map(copyComment);
}

/**
 * @param {any} value
 * @returns {value is Comment}
 */
function isComment(value) {
  return Boolean(
    value &&
      isNonEmptyString(value.id) &&
      typeof value.pageId === 'string' &&
      typeof value.body === 'string' &&
      typeof value.createdAt === 'string' &&
      value.author &&
      typeof value.author.name === 'string',
  );
}

/**
 * @param {Comment} comment
 * @returns {Comment}
 */
function copyComment(comment) {
  return {
    id: comment.id,
    pageId: comment.pageId,
    author: {
      login: typeof comment.author.login === 'string' ? comment.author.login : null,
      name: comment.author.name,
      avatarUrl: typeof comment.author.avatarUrl === 'string' ? comment.author.avatarUrl : null,
    },
    body: comment.body,
    createdAt: comment.createdAt,
  };
}

/**
 * @param {any} value
 * @returns {value is Draft}
 */
function isDraft(value) {
  return Boolean(
    value &&
      isNonEmptyString(value.key) &&
      typeof value.content === 'string' &&
      (value.format === 'md' || value.format === 'mdx') &&
      typeof value.updatedAt === 'string',
  );
}

/**
 * @param {Draft} draft
 * @returns {Draft}
 */
function copyDraft(draft) {
  return {
    key: draft.key,
    content: draft.content,
    format: draft.format,
    updatedAt: draft.updatedAt,
  };
}
