// Community data (likes, comments and blog-editor drafts) kept in the
// visitor's own browser.
//
// The site is a static build with no server, so this data is saved where the
// visitor is: IndexedDB when the browser offers it, else localStorage, else an
// in-memory store that lasts until the tab is closed (private windows, blocked
// storage, server-side rendering). Nothing is shared with other visitors.
//
// Components only see the CommunityStore interface below. A remote adapter
// (an API in front of an external database) can be added later as another
// `kind` in createCommunityStore without changing any component.
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
/** @typedef {'indexedDB' | 'localStorage' | 'memory'} BackendKind */
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
 * @property {() => Promise<BackendKind>} backend Where the data is kept (resolves the browser fallback chain).
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
 * @property {BackendKind} kind
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
 * @property {string} [kind] 'browser' (the only kind so far; see customFields.communityStore).
 * @property {any} [env] Where to look for window, indexedDB and localStorage (globalThis by default).
 * @property {number} [openTimeoutMs] Give up on IndexedDB if it has not opened by then (default 3000).
 * @property {() => Date | number} [now]
 * @property {() => string} [newId]
 */

/**
 * The store the site uses.
 *
 * 'browser': IndexedDB if it opens, else localStorage if it accepts writes,
 * else memory. Without a window (SSR, Node) it returns a memory store. It never
 * throws for 'browser' and touches no storage until the first call, so it is
 * safe to create during render.
 *
 * Any other kind throws `unknown community store: <kind>`; a future 'remote'
 * adapter plugs in here.
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
} = {}) {
  if (kind !== 'browser') {
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
  return createStore(getBackend, options);
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
