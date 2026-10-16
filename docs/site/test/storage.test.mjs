import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  COMMENT_MAX_LENGTH,
  DB_NAME,
  DB_VERSION,
  GUEST,
  LOCAL_STORAGE_PREFIX,
  OBJECT_STORES,
  STORE_VERSION,
  canDeleteComment,
  createCommunityStore,
  createIndexedDbStore,
  createLocalStorageStore,
  createMemoryStore,
} from '../src/lib/storage.mjs';

const LENGTH_ERROR = {message: 'comment must be 1-5000 characters'};

// ---------------------------------------------------------------------------
// Fakes

/** Web Storage-like object backed by a Map. */
class FakeStorage {
  constructor({quota = Infinity, failSetItem = false} = {}) {
    this.map = new Map();
    this.quota = quota;
    this.failSetItem = failSetItem;
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
    if (this.failSetItem) {
      throw new Error('QuotaExceededError');
    }
    const used = [...this.map].reduce((sum, [k, v]) => (k === key ? sum : sum + k.length + v.length), 0);
    if (used + key.length + String(value).length > this.quota) {
      throw new Error('QuotaExceededError');
    }
    this.map.set(key, String(value));
  }
  removeItem(key) {
    this.map.delete(key);
  }
}

/**
 * Small IndexedDB fake: open with upgradeneeded, out-of-line keys,
 * get/getAll/put/delete, readonly/readwrite transactions that run one at a
 * time (like overlapping readwrite transactions in a real browser), commit on
 * completion and roll back on abort. Values are structured-cloned.
 */
function createFakeIndexedDb({failOpen = false, throwOnOpen = false, neverOpen = false, failWrites = false} = {}) {
  const databases = new Map();
  const later = (fn) => setTimeout(fn, 0);
  const domError = (name, message = name) => Object.assign(new Error(message), {name});

  class FakeRequest {
    constructor() {
      this.result = undefined;
      this.error = null;
      this.onsuccess = null;
      this.onerror = null;
      this.onupgradeneeded = null;
      this.onblocked = null;
    }
  }

  class FakeObjectStore {
    constructor(tx, name) {
      this.tx = tx;
      this.name = name;
    }
    rows() {
      return this.tx.db.record.stores.get(this.name);
    }
    get(key) {
      return this.tx.request(false, () => structuredClone(this.rows().get(key)));
    }
    getAll() {
      return this.tx.request(false, () =>
        [...this.rows().keys()].sort().map((key) => structuredClone(this.rows().get(key))),
      );
    }
    put(value, key) {
      if (key === undefined) {
        throw domError('DataError', 'out-of-line key required');
      }
      const copy = structuredClone(value);
      return this.tx.request(true, () => {
        if (failWrites) {
          throw domError('QuotaExceededError');
        }
        this.rows().set(key, copy);
        return key;
      });
    }
    delete(key) {
      return this.tx.request(true, () => {
        this.rows().delete(key);
        return undefined;
      });
    }
  }

  class FakeTransaction {
    constructor(db, names, mode) {
      this.db = db;
      this.names = names;
      this.mode = mode;
      this.error = null;
      this.oncomplete = null;
      this.onabort = null;
      this.onerror = null;
      this.outstanding = 0;
      this.finished = false;
      this.aborted = false;
      let release;
      const previous = db.queue;
      db.queue = new Promise((resolve) => {
        release = resolve;
      });
      this.release = release;
      this.started = previous.then(() => {
        this.snapshot = new Map(names.map((name) => [name, new Map(db.record.stores.get(name))]));
      });
      this.checkDone();
    }
    objectStore(name) {
      if (!this.names.includes(name)) {
        throw domError('NotFoundError');
      }
      return new FakeObjectStore(this, name);
    }
    request(write, run) {
      if (this.finished || this.aborted) {
        throw domError('TransactionInactiveError');
      }
      if (write && this.mode !== 'readwrite') {
        throw domError('ReadOnlyError');
      }
      const request = new FakeRequest();
      this.outstanding += 1;
      this.started.then(() =>
        later(() => {
          if (this.aborted) {
            return;
          }
          try {
            request.result = run();
            request.onsuccess?.({target: request});
          } catch (error) {
            request.error = error;
            request.onerror?.({target: request});
            this.error = error;
            this.abort();
            return;
          }
          this.outstanding -= 1;
          this.checkDone();
        }),
      );
      return request;
    }
    checkDone() {
      this.started.then(() =>
        later(() => {
          if (!this.finished && !this.aborted && this.outstanding === 0) {
            this.finished = true;
            this.release();
            this.oncomplete?.({target: this});
          }
        }),
      );
    }
    abort() {
      if (this.finished || this.aborted) {
        throw domError('InvalidStateError');
      }
      this.aborted = true;
      this.started.then(() => {
        for (const [name, rows] of this.snapshot) {
          this.db.record.stores.set(name, rows);
        }
        this.release();
        later(() => this.onabort?.({target: this}));
      });
    }
  }

  class FakeDatabase {
    constructor(name, record) {
      this.name = name;
      this.record = record;
      this.queue = Promise.resolve();
      this.upgrading = false;
      this.closed = false;
      this.onversionchange = null;
      this.onclose = null;
    }
    get version() {
      return this.record.version;
    }
    get objectStoreNames() {
      const names = [...this.record.stores.keys()];
      return {contains: (name) => names.includes(name), length: names.length, item: (i) => names[i]};
    }
    createObjectStore(name) {
      if (!this.upgrading) {
        throw domError('InvalidStateError');
      }
      if (this.record.stores.has(name)) {
        throw domError('ConstraintError');
      }
      this.record.stores.set(name, new Map());
    }
    transaction(storeNames, mode = 'readonly') {
      if (this.closed) {
        throw domError('InvalidStateError');
      }
      const names = [].concat(storeNames);
      for (const name of names) {
        if (!this.record.stores.has(name)) {
          throw domError('NotFoundError');
        }
      }
      return new FakeTransaction(this, names, mode);
    }
    close() {
      this.closed = true;
    }
  }

  const factory = {
    opens: [],
    databases,
    open(name, version) {
      factory.opens.push({name, version});
      if (throwOnOpen) {
        throw domError('SecurityError');
      }
      const request = new FakeRequest();
      later(() => {
        if (neverOpen) {
          return;
        }
        if (failOpen) {
          request.error = domError('InvalidStateError', 'IndexedDB is disabled');
          request.onerror?.({target: request});
          return;
        }
        let record = databases.get(name);
        if (!record) {
          record = {version: 0, stores: new Map()};
          databases.set(name, record);
        }
        const db = new FakeDatabase(name, record);
        request.result = db;
        if (version > record.version) {
          const oldVersion = record.version;
          record.version = version;
          db.upgrading = true;
          request.onupgradeneeded?.({target: request, oldVersion, newVersion: version});
          db.upgrading = false;
        }
        request.onsuccess?.({target: request});
      });
      return request;
    },
  };
  return factory;
}

/** Deterministic clock (one second per call) and ids. */
function testOptions(start = '2026-10-16T10:00:00.000Z') {
  let time = Date.parse(start);
  let id = 0;
  return {
    now: () => {
      const date = new Date(time);
      time += 1000;
      return date;
    },
    newId: () => `id-${++id}`,
  };
}

// ---------------------------------------------------------------------------
// Behaviour shared by every adapter

const adapters = {
  memory: (options) => createMemoryStore(options),
  localStorage: (options) => createLocalStorageStore(new FakeStorage(), options),
  indexedDB: (options) => createIndexedDbStore(createFakeIndexedDb(), options),
  'browser (IndexedDB)': (options) =>
    createCommunityStore({env: {window: {}, indexedDB: createFakeIndexedDb()}, ...options}),
  'browser (localStorage fallback)': (options) =>
    createCommunityStore({
      env: {window: {}, indexedDB: createFakeIndexedDb({failOpen: true}), localStorage: new FakeStorage()},
      ...options,
    }),
};

const expectedBackend = {
  memory: 'memory',
  localStorage: 'localStorage',
  indexedDB: 'indexedDB',
  'browser (IndexedDB)': 'indexedDB',
  'browser (localStorage fallback)': 'localStorage',
};

for (const [name, make] of Object.entries(adapters)) {
  describe(`CommunityStore: ${name}`, () => {
    test('reports its backend', async () => {
      assert.equal(await make(testOptions()).backend(), expectedBackend[name]);
    });

    test('likes start empty', async () => {
      const store = make(testOptions());
      assert.deepEqual(await store.getLikes('docs/a.md'), {count: 0, likedBy: []});
    });

    test('toggleLike adds and removes one like per user', async () => {
      const store = make(testOptions());
      assert.deepEqual(await store.toggleLike('docs/a.md', 'octocat'), {count: 1, likedBy: ['octocat']});
      assert.deepEqual(await store.toggleLike('docs/a.md', GUEST), {
        count: 2,
        likedBy: ['octocat', 'guest'],
      });
      assert.deepEqual(await store.getLikes('docs/a.md'), {count: 2, likedBy: ['octocat', 'guest']});
      assert.deepEqual(await store.toggleLike('docs/a.md', 'octocat'), {count: 1, likedBy: ['guest']});
      assert.deepEqual(await store.toggleLike('docs/a.md', GUEST), {count: 0, likedBy: []});
      assert.deepEqual(await store.getLikes('docs/a.md'), {count: 0, likedBy: []});
    });

    test('likes are kept per page', async () => {
      const store = make(testOptions());
      await store.toggleLike('docs/a.md', 'octocat');
      assert.equal((await store.getLikes('docs/a.md')).count, 1);
      assert.equal((await store.getLikes('docs/b.md')).count, 0);
    });

    test('concurrent toggles by different users are all kept', async () => {
      const store = make(testOptions());
      const users = Array.from({length: 8}, (_, index) => `user-${index}`);
      await Promise.all(users.map((user) => store.toggleLike('docs/a.md', user)));
      const likes = await store.getLikes('docs/a.md');
      assert.equal(likes.count, users.length);
      assert.deepEqual([...likes.likedBy].sort(), [...users].sort());
    });

    test('rejects a missing pageId or userKey', async () => {
      const store = make(testOptions());
      await assert.rejects(store.getLikes(''), {message: 'pageId must be a non-empty string'});
      await assert.rejects(store.getLikes(undefined), {message: 'pageId must be a non-empty string'});
      await assert.rejects(store.toggleLike('docs/a.md', ''), {message: 'userKey must be a non-empty string'});
      await assert.rejects(store.toggleLike('docs/a.md', null), {message: 'userKey must be a non-empty string'});
    });

    test('addComment trims the body, stamps the date and assigns an id', async () => {
      const store = make(testOptions());
      const comment = await store.addComment('docs/a.md', {
        author: {login: 'octocat', name: 'The Octocat', avatarUrl: 'https://avatars.githubusercontent.com/u/1'},
        body: '  Nice page!  \n',
      });
      assert.deepEqual(comment, {
        id: 'id-1',
        pageId: 'docs/a.md',
        author: {login: 'octocat', name: 'The Octocat', avatarUrl: 'https://avatars.githubusercontent.com/u/1'},
        body: 'Nice page!',
        createdAt: '2026-10-16T10:00:00.000Z',
      });
      assert.deepEqual(await store.listComments('docs/a.md'), [comment]);
      assert.deepEqual(await store.listComments('docs/b.md'), []);
    });

    test('listComments returns the oldest first', async () => {
      const store = make(testOptions());
      const first = await store.addComment('p', {author: null, body: 'first'});
      const second = await store.addComment('p', {author: null, body: 'second'});
      const third = await store.addComment('p', {author: null, body: 'third'});
      assert.deepEqual(
        (await store.listComments('p')).map((comment) => comment.id),
        [first.id, second.id, third.id],
      );
    });

    test('listComments sorts by date even when stored out of order', async () => {
      let call = 0;
      const dates = ['2026-10-16T12:00:00.000Z', '2026-10-16T09:00:00.000Z', '2026-10-16T09:00:00.000Z'];
      const store = make({now: () => new Date(dates[call++]), newId: () => `c${call}`});
      await store.addComment('p', {body: 'late'});
      await store.addComment('p', {body: 'early A'});
      await store.addComment('p', {body: 'early B'});
      assert.deepEqual(
        (await store.listComments('p')).map((comment) => comment.body),
        ['early A', 'early B', 'late'],
      );
    });

    test('addComment enforces 1-5000 characters with the exact message', async () => {
      const store = make(testOptions());
      await assert.rejects(store.addComment('p', {body: ''}), LENGTH_ERROR);
      await assert.rejects(store.addComment('p', {body: '   \n\t '}), LENGTH_ERROR);
      await assert.rejects(store.addComment('p', {body: 'a'.repeat(COMMENT_MAX_LENGTH + 1)}), LENGTH_ERROR);
      await assert.rejects(store.addComment('p', {body: 42}), LENGTH_ERROR);
      await assert.rejects(store.addComment('p', {}), LENGTH_ERROR);
      await assert.rejects(store.addComment('p', undefined), LENGTH_ERROR);
      assert.deepEqual(await store.listComments('p'), []);

      const longest = await store.addComment('p', {body: `  ${'a'.repeat(COMMENT_MAX_LENGTH)}  `});
      assert.equal(longest.body.length, COMMENT_MAX_LENGTH);
      // Characters, not UTF-16 units: 5000 emoji are accepted.
      const emoji = await store.addComment('p', {body: '🎉'.repeat(COMMENT_MAX_LENGTH)});
      assert.equal(Array.from(emoji.body).length, COMMENT_MAX_LENGTH);
      await assert.rejects(store.addComment('p', {body: '🎉'.repeat(COMMENT_MAX_LENGTH + 1)}), LENGTH_ERROR);
    });

    test('addComment normalises the author', async () => {
      const store = make(testOptions());
      const guest = await store.addComment('p', {body: 'hello'});
      assert.deepEqual(guest.author, {login: null, name: 'Guest', avatarUrl: null});

      const namedGuest = await store.addComment('p', {author: {login: '', name: '  Ada  '}, body: 'hi'});
      assert.deepEqual(namedGuest.author, {login: null, name: 'Ada', avatarUrl: null});

      const user = await store.addComment('p', {
        author: {login: 'octocat', avatarUrl: 'javascript:alert(1)'},
        body: 'hi',
      });
      assert.deepEqual(user.author, {login: 'octocat', name: 'octocat', avatarUrl: null});
    });

    test('deleteComment: only the author may delete', async () => {
      const store = make(testOptions());
      const mine = await store.addComment('p', {author: {login: 'octocat', name: 'Octo'}, body: 'mine'});
      const guest = await store.addComment('p', {author: null, body: 'guest'});

      assert.equal(await store.deleteComment('p', mine.id, 'someone-else'), false);
      assert.equal(await store.deleteComment('p', mine.id, GUEST), false);
      assert.equal(await store.deleteComment('p', guest.id, 'octocat'), false);
      assert.equal(await store.deleteComment('other-page', mine.id, 'octocat'), false);
      assert.equal(await store.deleteComment('p', 'no-such-id', 'octocat'), false);
      assert.equal(await store.deleteComment('p', mine.id, ''), false);
      assert.equal((await store.listComments('p')).length, 2);

      assert.equal(await store.deleteComment('p', mine.id, 'octocat'), true);
      assert.equal(await store.deleteComment('p', guest.id, GUEST), true);
      assert.deepEqual(await store.listComments('p'), []);
      assert.equal(await store.deleteComment('p', mine.id, 'octocat'), false);
    });

    test('returns copies: changing a result does not change the store', async () => {
      const store = make(testOptions());
      const likes = await store.toggleLike('p', 'octocat');
      likes.likedBy.push('intruder');
      likes.count = 99;
      assert.deepEqual(await store.getLikes('p'), {count: 1, likedBy: ['octocat']});

      const comment = await store.addComment('p', {author: {login: 'octocat'}, body: 'original'});
      comment.body = 'changed';
      comment.author.login = 'intruder';
      const [stored] = await store.listComments('p');
      assert.equal(stored.body, 'original');
      assert.equal(stored.author.login, 'octocat');

      const draft = await store.saveDraft('k', {content: 'x', format: 'md'});
      draft.content = 'changed';
      assert.equal((await store.getDraft('k')).content, 'x');
    });

    test('drafts: save, get, overwrite, list newest first, delete', async () => {
      const store = make(testOptions());
      assert.equal(await store.getDraft('blog/a.md'), null);
      assert.deepEqual(await store.listDrafts(), []);

      const a = await store.saveDraft('blog/a.md', {content: '# A', format: 'md'});
      assert.deepEqual(a, {key: 'blog/a.md', content: '# A', format: 'md', updatedAt: '2026-10-16T10:00:00.000Z'});
      const b = await store.saveDraft('new:untitled', {content: '<B />', format: 'mdx'});
      assert.deepEqual(await store.getDraft('blog/a.md'), a);
      assert.deepEqual(await store.listDrafts(), [b, a]);

      const a2 = await store.saveDraft('blog/a.md', {content: '# A, edited', format: 'mdx'});
      assert.equal(a2.updatedAt, '2026-10-16T10:00:02.000Z');
      assert.deepEqual(await store.listDrafts(), [a2, b]);

      assert.equal(await store.deleteDraft('blog/a.md'), true);
      assert.equal(await store.deleteDraft('blog/a.md'), false);
      assert.equal(await store.getDraft('blog/a.md'), null);
      assert.deepEqual(await store.listDrafts(), [b]);
    });

    test('drafts: validates key, content and format', async () => {
      const store = make(testOptions());
      await assert.rejects(store.saveDraft('', {content: 'x', format: 'md'}), {
        message: 'draft key must be a non-empty string',
      });
      await assert.rejects(store.saveDraft('k', {content: 3, format: 'md'}), {
        message: 'draft content must be a string',
      });
      await assert.rejects(store.saveDraft('k', {content: 'x', format: 'html'}), {
        message: "draft format must be 'md' or 'mdx'",
      });
      await assert.rejects(store.getDraft(''), {message: 'draft key must be a non-empty string'});
      // Empty content is a valid draft.
      assert.equal((await store.saveDraft('k', {content: '', format: 'md'})).content, '');
    });

    test('methods work unbound', async () => {
      const {toggleLike, getLikes} = make(testOptions());
      await toggleLike('p', 'octocat');
      assert.equal((await getLikes('p')).count, 1);
    });
  });
}

// ---------------------------------------------------------------------------
// Adapter specifics

describe('canDeleteComment', () => {
  test('matches the author login, or guest for guest comments', () => {
    assert.equal(canDeleteComment({author: {login: 'octocat', name: 'o', avatarUrl: null}}, 'octocat'), true);
    assert.equal(canDeleteComment({author: {login: 'octocat', name: 'o', avatarUrl: null}}, 'guest'), false);
    assert.equal(canDeleteComment({author: {login: null, name: 'Guest', avatarUrl: null}}, 'guest'), true);
    assert.equal(canDeleteComment({author: {login: null, name: 'Guest', avatarUrl: null}}, 'octocat'), false);
    assert.equal(canDeleteComment({author: {login: 'octocat', name: 'o', avatarUrl: null}}, ''), false);
    assert.equal(canDeleteComment(null, 'octocat'), false);
  });
});

describe('constants', () => {
  test('match the shared interface', () => {
    assert.equal(STORE_VERSION, 1);
    assert.equal(LOCAL_STORAGE_PREFIX, 'rid-community:v1:');
    assert.equal(DB_NAME, 'rid-community');
    assert.equal(DB_VERSION, 1);
    assert.deepEqual([...OBJECT_STORES], ['likes', 'comments', 'drafts']);
  });
});

describe('createLocalStorageStore', () => {
  test('prefixes every key with rid-community:v1: and leaves other keys alone', async () => {
    const storage = new FakeStorage();
    storage.setItem('theme', 'dark');
    const store = createLocalStorageStore(storage, testOptions());
    await store.toggleLike('docs/a.md', 'octocat');
    await store.addComment('docs/a.md', {body: 'hi'});
    await store.saveDraft('blog/x.md', {content: 'x', format: 'md'});
    const keys = [...storage.map.keys()].filter((key) => key !== 'theme');
    assert.equal(keys.length, 3);
    for (const key of keys) {
      assert.ok(key.startsWith('rid-community:v1:'), key);
    }
    assert.equal(storage.getItem('theme'), 'dark');
  });

  test('persists across store instances (a page reload)', async () => {
    const storage = new FakeStorage();
    const first = createLocalStorageStore(storage, testOptions());
    await first.toggleLike('p', 'octocat');
    await first.addComment('p', {body: 'kept'});
    await first.saveDraft('d', {content: 'draft', format: 'mdx'});

    const second = createLocalStorageStore(storage, testOptions());
    assert.deepEqual(await second.getLikes('p'), {count: 1, likedBy: ['octocat']});
    assert.equal((await second.listComments('p'))[0].body, 'kept');
    assert.equal((await second.getDraft('d')).format, 'mdx');
  });

  test('removes emptied records', async () => {
    const storage = new FakeStorage();
    const store = createLocalStorageStore(storage, testOptions());
    await store.toggleLike('p', 'octocat');
    await store.toggleLike('p', 'octocat');
    const comment = await store.addComment('p', {body: 'x'});
    await store.deleteComment('p', comment.id, GUEST);
    assert.equal(storage.length, 0);
  });

  test('reads corrupted entries as missing', async () => {
    const storage = new FakeStorage();
    storage.setItem(`${LOCAL_STORAGE_PREFIX}likes:p`, '{not json');
    storage.setItem(`${LOCAL_STORAGE_PREFIX}comments:p`, JSON.stringify({comments: [{id: 1}, 'junk']}));
    storage.setItem(`${LOCAL_STORAGE_PREFIX}drafts:bad`, JSON.stringify({key: 'bad', content: 1}));
    storage.setItem(`${LOCAL_STORAGE_PREFIX}likes:q`, JSON.stringify({likedBy: ['a', 'a', '', 7, 'b']}));
    const store = createLocalStorageStore(storage, testOptions());
    assert.deepEqual(await store.getLikes('p'), {count: 0, likedBy: []});
    assert.deepEqual(await store.listComments('p'), []);
    assert.deepEqual(await store.listDrafts(), []);
    assert.deepEqual(await store.getLikes('q'), {count: 2, likedBy: ['a', 'b']});
    assert.deepEqual(await store.toggleLike('p', 'octocat'), {count: 1, likedBy: ['octocat']});
  });

  test('a full storage gives a readable error', async () => {
    const store = createLocalStorageStore(new FakeStorage({quota: 10}), testOptions());
    await assert.rejects(store.addComment('p', {body: 'too big for the quota'}), {
      message: 'could not save in this browser: storage is full or blocked',
    });
  });

  test('needs a storage object', () => {
    assert.throws(() => createLocalStorageStore(null), TypeError);
  });
});

describe('createIndexedDbStore', () => {
  test("opens database 'rid-community' v1 with stores likes, comments, drafts", async () => {
    const factory = createFakeIndexedDb();
    const store = createIndexedDbStore(factory, testOptions());
    assert.equal(factory.opens.length, 0, 'opens lazily');
    await store.getLikes('p');
    assert.deepEqual(factory.opens, [{name: 'rid-community', version: 1}]);
    const record = factory.databases.get('rid-community');
    assert.equal(record.version, 1);
    assert.deepEqual([...record.stores.keys()], ['likes', 'comments', 'drafts']);
    await store.getLikes('q');
    assert.equal(factory.opens.length, 1, 'reuses the connection');
  });

  test('persists across store instances (a page reload)', async () => {
    const factory = createFakeIndexedDb();
    const first = createIndexedDbStore(factory, testOptions());
    await first.toggleLike('p', 'octocat');
    await first.addComment('p', {author: {login: 'octocat'}, body: 'kept'});
    await first.saveDraft('d', {content: 'draft', format: 'md'});

    const second = createIndexedDbStore(factory, testOptions());
    assert.deepEqual(await second.getLikes('p'), {count: 1, likedBy: ['octocat']});
    assert.equal((await second.listComments('p'))[0].body, 'kept');
    assert.equal((await second.getDraft('d')).content, 'draft');
  });

  test('keeps one record per page and removes emptied ones', async () => {
    const factory = createFakeIndexedDb();
    const store = createIndexedDbStore(factory, testOptions());
    await store.toggleLike('p', 'a');
    await store.toggleLike('p', 'b');
    const stores = factory.databases.get('rid-community').stores;
    assert.deepEqual(stores.get('likes').get('p'), {pageId: 'p', likedBy: ['a', 'b']});
    await store.toggleLike('p', 'a');
    await store.toggleLike('p', 'b');
    assert.equal(stores.get('likes').has('p'), false);
  });

  test('a failed write rejects and changes nothing', async () => {
    const factory = createFakeIndexedDb({failWrites: true});
    const store = createIndexedDbStore(factory, testOptions());
    await assert.rejects(store.toggleLike('p', 'octocat'), {name: 'QuotaExceededError'});
    assert.deepEqual(await store.getLikes('p'), {count: 0, likedBy: []});
  });

  test('rejects every call when the database cannot be opened', async () => {
    const store = createIndexedDbStore(createFakeIndexedDb({failOpen: true}), testOptions());
    await assert.rejects(store.getLikes('p'), {message: 'IndexedDB is disabled'});
    const throwing = createIndexedDbStore(createFakeIndexedDb({throwOnOpen: true}), testOptions());
    await assert.rejects(throwing.listDrafts(), {name: 'SecurityError'});
  });

  test('retries opening after a failure', async () => {
    const factory = createFakeIndexedDb({failOpen: true});
    const store = createIndexedDbStore(factory, testOptions());
    await assert.rejects(store.getLikes('p'));
    await assert.rejects(store.getLikes('p'));
    assert.equal(factory.opens.length, 2);
  });

  test('needs an IDBFactory', () => {
    assert.throws(() => createIndexedDbStore(undefined), TypeError);
  });
});

describe('createCommunityStore', () => {
  test('unknown kinds throw (where a remote adapter will plug in)', () => {
    assert.throws(() => createCommunityStore({kind: 'remote'}), {message: 'unknown community store: remote'});
    assert.throws(() => createCommunityStore({kind: 'memory'}), {message: 'unknown community store: memory'});
  });

  test('without a window (SSR, Node) it is a working memory store', async () => {
    const ssr = createCommunityStore();
    assert.equal(await ssr.backend(), 'memory');
    assert.equal((await ssr.toggleLike('p', GUEST)).count, 1);
    assert.equal(await createCommunityStore({env: {}}).backend(), 'memory');
    assert.equal(await createCommunityStore({env: null}).backend(), 'memory');
  });

  test('prefers IndexedDB', async () => {
    const factory = createFakeIndexedDb();
    const storage = new FakeStorage();
    const store = createCommunityStore({env: {window: {}, indexedDB: factory, localStorage: storage}});
    await store.toggleLike('p', 'octocat');
    assert.equal(await store.backend(), 'indexedDB');
    assert.equal(storage.length, 0);
  });

  test('falls back to localStorage when IndexedDB fails, throws or hangs', async () => {
    for (const options of [{failOpen: true}, {throwOnOpen: true}, {neverOpen: true}]) {
      const storage = new FakeStorage();
      const store = createCommunityStore({
        env: {window: {}, indexedDB: createFakeIndexedDb(options), localStorage: storage},
        openTimeoutMs: 20,
      });
      assert.equal(await store.backend(), 'localStorage', JSON.stringify(options));
      await store.toggleLike('p', 'octocat');
      assert.ok(storage.getItem(`${LOCAL_STORAGE_PREFIX}likes:p`));
    }
    const noIdb = createCommunityStore({env: {window: {}, localStorage: new FakeStorage()}});
    assert.equal(await noIdb.backend(), 'localStorage');
  });

  test('falls back to memory when localStorage refuses writes or is blocked', async () => {
    const refusing = createCommunityStore({env: {window: {}, localStorage: new FakeStorage({failSetItem: true})}});
    assert.equal(await refusing.backend(), 'memory');
    assert.equal((await refusing.toggleLike('p', GUEST)).count, 1);

    const blocked = {window: {}};
    Object.defineProperty(blocked, 'localStorage', {
      get() {
        throw Object.assign(new Error('blocked'), {name: 'SecurityError'});
      },
    });
    Object.defineProperty(blocked, 'indexedDB', {
      get() {
        throw Object.assign(new Error('blocked'), {name: 'SecurityError'});
      },
    });
    const store = createCommunityStore({env: blocked});
    assert.equal(await store.backend(), 'memory');
  });

  test('touches no storage until the first call', async () => {
    const touched = [];
    const factory = createFakeIndexedDb();
    const env = {window: {}};
    Object.defineProperty(env, 'indexedDB', {
      get() {
        touched.push('indexedDB');
        return factory;
      },
    });
    Object.defineProperty(env, 'localStorage', {
      get() {
        touched.push('localStorage');
        return new FakeStorage();
      },
    });
    const store = createCommunityStore({env});
    assert.deepEqual(touched, []);
    await store.getLikes('p');
    assert.deepEqual(touched, ['indexedDB']);
    await store.getLikes('p');
    assert.deepEqual(touched, ['indexedDB'], 'the backend is chosen once');
  });

  test('passes the clock and id generator through', async () => {
    const store = createCommunityStore({env: {}, ...testOptions('2030-01-01T00:00:00.000Z')});
    const comment = await store.addComment('p', {body: 'x'});
    assert.equal(comment.id, 'id-1');
    assert.equal(comment.createdAt, '2030-01-01T00:00:00.000Z');
  });

  test('default ids are unique strings', async () => {
    const store = createMemoryStore();
    const a = await store.addComment('p', {body: 'a'});
    const b = await store.addComment('p', {body: 'b'});
    assert.equal(typeof a.id, 'string');
    assert.notEqual(a.id, b.id);
    assert.ok(!Number.isNaN(Date.parse(a.createdAt)));
  });
});
