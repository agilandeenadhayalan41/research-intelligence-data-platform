// Gives components the CommunityStore (likes, comments, editor drafts).
//
// Which store is used comes from customFields.communityStore in
// docusaurus.config.ts:
// - 'shared' (the site's setting): likes and comments are shared by every
//   visitor through the site's /api/community function, which keeps them in
//   the GitHub repository. Guests come first: without signing in, writes carry
//   this browser's random guest id and show as "Guest". A visitor who is
//   signed in AND has saved a GitHub token (useGitHubUser) writes under their
//   verified GitHub name: the token goes with each write so the function can
//   check who they are. Drafts stay in the browser. Where the API is missing
//   or not configured (`npm start`), it behaves like 'browser'.
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

function makeStore(kind: string, getToken: () => string | null): CommunityStore {
  try {
    return createCommunityStore({kind, getToken});
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
  const {user, token} = useGitHubUser();
  // The store reads the token at each write, so it never has to be recreated.
  // Signed out, a saved token is not used: the visitor is a guest.
  const tokenRef = useRef<string | null>(null);
  tokenRef.current = user ? token : null;
  const configured = siteConfig.customFields?.communityStore;
  const kind = typeof configured === 'string' && configured ? configured : 'browser';
  // One store for the whole session (it is not recreated on navigation).
  const [store] = useState(() => makeStore(kind, () => tokenRef.current));
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
