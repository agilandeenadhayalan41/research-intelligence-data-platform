// Gives components the CommunityStore (likes, comments, editor drafts).
//
// Which store is used comes from customFields.communityStore in
// docusaurus.config.ts:
// - 'shared' (the site's setting): likes and comments are shared by every
//   visitor through the site's /api/community function, which keeps them in
//   the GitHub repository. Guests come first: without signing in, writes carry
//   this browser's random guest id and show as "Guest". Who writes follows
//   useGitHubUser() at each call:
//   - signed in with GitHub (`verified`: /api/auth/me confirmed the session):
//     the session cookie goes with every request and the function reads the
//     user from it. No token is sent, even when one is saved;
//   - otherwise, a username in the menu (the typed fallback, where sign-in is
//     not available) AND a saved GitHub token: the token goes with each write
//     so the function can check who they are with GitHub;
//   - otherwise a guest.
//   Drafts stay in the browser. Where the API is missing or not configured
//   (`npm start`), it behaves like 'browser'.
// - 'browser': everything in this browser (IndexedDB, else localStorage, else
//   memory).
//
// SSR-safe: createCommunityStore touches no storage and makes no request
// until its first method call (and returns a memory store when there is no
// window), so it can be created during render. Call the methods from effects
// or event handlers.

import React, {createContext, useContext, useEffect, useRef, useState, type ReactNode} from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {useGitHubUser} from './GitHubUserContext';
import {
  createCommunityStore,
  createMemoryStore,
  type CommunityStore,
} from './storage.mjs';

export type {CommunityStore};

/** Where drafts (and, without the shared API, likes and comments) are kept in this browser. */
export type BrowserBackend = 'indexedDB' | 'localStorage' | 'memory';

/** Where likes and comments are kept: see CommunityStore.backend(). */
export type CommunityBackend = 'shared' | BrowserBackend;

const CommunityStoreContext = createContext<CommunityStore | null>(null);

function makeStore(
  kind: string,
  getToken: () => string | null,
  getSessionLogin: () => string | null,
): CommunityStore {
  try {
    return createCommunityStore({kind, getToken, getSessionLogin});
  } catch (error) {
    // An unknown kind is a configuration mistake; keep the site usable.
    console.error(
      `[community] ${error instanceof Error ? error.message : String(error)}; using an in-memory store.`,
    );
    return createMemoryStore();
  }
}

export function CommunityStoreProvider({children}: {children: ReactNode}): React.JSX.Element {
  const {siteConfig} = useDocusaurusContext();
  const {user, verified, token} = useGitHubUser();
  // The store reads both at each call, so it never has to be recreated.
  // Signed in with GitHub: the session (no token). Otherwise a token counts
  // only with a username in the menu; with neither the visitor is a guest.
  const sessionLoginRef = useRef<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  sessionLoginRef.current = user && verified ? user.login : null;
  tokenRef.current = user && !verified ? token : null;
  const configured = siteConfig.customFields?.communityStore;
  const kind = typeof configured === 'string' && configured ? configured : 'browser';
  // One store for the whole session (it is not recreated on navigation).
  const [store] = useState(() =>
    makeStore(
      kind,
      () => tokenRef.current,
      () => sessionLoginRef.current,
    ),
  );
  return <CommunityStoreContext.Provider value={store}>{children}</CommunityStoreContext.Provider>;
}

let fallbackStore: CommunityStore | null = null;

/** The site's CommunityStore. All methods are async. */
export function useCommunityStore(): CommunityStore {
  const store = useContext(CommunityStoreContext);
  if (store) {
    return store;
  }
  // Rendered outside <Root> (should not happen): a memory store keeps it working.
  fallbackStore ??= createMemoryStore();
  return fallbackStore;
}

/** The value of an async store method, once it resolves (null before; onError if it rejects). */
function useResolved<T extends string>(read: (store: CommunityStore) => Promise<T>, onError: T): T | null {
  const store = useCommunityStore();
  const [value, setValue] = useState<T | null>(null);
  const readRef = useRef(read);
  readRef.current = read;
  useEffect(() => {
    let active = true;
    readRef
      .current(store)
      .then((kind) => {
        if (active) {
          setValue(kind);
        }
      })
      .catch(() => {
        if (active) {
          setValue(onError);
        }
      });
    return () => {
      active = false;
    };
  }, [store, onError]);
  return value;
}

/**
 * Where likes and comments are kept, once known (null before that, and during
 * the server render). 'shared': in the site's GitHub repository, seen by
 * everyone. 'memory' means the browser blocks storage, so the data lasts only
 * until the page is closed.
 */
export function useCommunityBackend(): CommunityBackend | null {
  return useResolved<CommunityBackend>((store) => store.backend(), 'memory');
}

/** Where editor drafts are kept (always this browser), once known. */
export function useDraftsBackend(): BrowserBackend | null {
  return useResolved<BrowserBackend>((store) => store.draftsBackend(), 'memory');
}

/**
 * Who likes and comments are made as on the shared store, from useGitHubUser():
 * 'session' (signed in with GitHub), 'token' (a typed username with a saved
 * token, where sign-in is not available) or 'guest', with the login. `key`
 * changes whenever the identity does, so a component can read the page again.
 * Matches what CommunityStoreProvider gives the store.
 */
export function useCommunityIdentity(): {kind: 'session' | 'token' | 'guest'; login: string | null; key: string} {
  const {user, verified, token} = useGitHubUser();
  if (user && verified) {
    return {kind: 'session', login: user.login, key: `session:${user.login.toLowerCase()}`};
  }
  if (user && token) {
    return {kind: 'token', login: user.login, key: `token:${user.login.toLowerCase()}`};
  }
  return {kind: 'guest', login: null, key: 'guest'};
}
