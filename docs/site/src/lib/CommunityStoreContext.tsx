// Gives components the CommunityStore (likes, comments, editor drafts).
//
// Which store is used comes from customFields.communityStore in
// docusaurus.config.ts ('browser' today: IndexedDB, else localStorage, else
// memory). Components only call the CommunityStore methods, so a future
// 'remote' store (an API plus a database) plugs in through
// createCommunityStore without changing them.
//
// SSR-safe: createCommunityStore touches no storage until its first method
// call (and returns a memory store when there is no window), so it can be
// created during render. Call the methods from effects or event handlers.

import React, {createContext, useContext, useEffect, useState, type ReactNode} from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {
  createCommunityStore,
  createMemoryStore,
  type CommunityStore,
} from './storage.mjs';

export type {CommunityStore};

/** Where the data is kept: see CommunityStore.backend(). */
export type CommunityBackend = 'indexedDB' | 'localStorage' | 'memory';

const CommunityStoreContext = createContext<CommunityStore | null>(null);

function makeStore(kind: string): CommunityStore {
  try {
    return createCommunityStore({kind});
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
  const configured = siteConfig.customFields?.communityStore;
  const kind = typeof configured === 'string' && configured ? configured : 'browser';
  // One store for the whole session (it is not recreated on navigation).
  const [store] = useState(() => makeStore(kind));
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

/**
 * Where the store keeps its data, once known (null before that, and during
 * the server render). 'memory' means the browser blocks storage, so the data
 * lasts only until the page is closed.
 */
export function useCommunityBackend(): CommunityBackend | null {
  const store = useCommunityStore();
  const [backend, setBackend] = useState<CommunityBackend | null>(null);
  useEffect(() => {
    let active = true;
    store
      .backend()
      .then((kind) => {
        if (active) {
          setBackend(kind);
        }
      })
      .catch(() => {
        if (active) {
          setBackend('memory');
        }
      });
    return () => {
      active = false;
    };
  }, [store]);
  return backend;
}
