// The optional, deliberately simple GitHub "sign-in".
//
// The visitor types a GitHub username; the site reads that account's PUBLIC
// profile from https://api.github.com/users/<login> and keeps
// {login, name, avatarUrl, htmlUrl} in localStorage, so it survives a reload.
// There is no password, no OAuth and no backend: it is not authentication,
// it only labels likes and comments in this browser.
//
// An optional GitHub token can be kept too (localStorage, this device only).
// It is used only for the blog editor's "Open pull request" and for higher
// GitHub API rate limits, and src/lib/github.mjs sends it to api.github.com
// only.
//
// SSR-safe: localStorage is read in an effect after mount, so the server and
// the first client render both start signed out (status 'loading').

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {fetchUser, LOGIN_PATTERN, type GitHubUser} from './github.mjs';
import {GUEST} from './storage.mjs';

export type {GitHubUser};

/** Storage keys (localStorage). */
export const USER_STORAGE_KEY = 'rid-github:v1:user';
export const TOKEN_STORAGE_KEY = 'rid-github:v1:token';

/**
 * - 'loading': before the saved user has been read (server render, first client render)
 * - 'signed-out' / 'signed-in'
 * - 'signing-in': a signIn() call is waiting for GitHub
 */
export type GitHubUserStatus = 'loading' | 'signed-out' | 'signing-in' | 'signed-in';

export type SignInResult =
  | {ok: true; user: GitHubUser}
  | {ok: false; error: string; message: string};

export type GitHubUserContextValue = {
  /** The signed-in user's public profile, or null. */
  user: GitHubUser | null;
  /** Reads the public profile of `login` and keeps it. Never rejects. */
  signIn: (login: string) => Promise<SignInResult>;
  signOut: () => void;
  /** The optional GitHub token, or null. Only send it to api.github.com. */
  token: string | null;
  /** Saves the token on this device (trimmed); null or '' removes it. Returns false for a malformed token. */
  setToken: (token: string | null) => boolean;
  status: GitHubUserStatus;
  /** Key for likes and comment ownership: the login, or 'guest' when signed out. */
  userKey: string;
};

/** Printable ASCII, no spaces: what a GitHub token looks like. */
const TOKEN_PATTERN = /^[\x21-\x7E]{1,1000}$/;

const noopSignIn = async (): Promise<SignInResult> => ({
  ok: false,
  error: 'unavailable',
  message: 'Sign-in is not available here.',
});

const DEFAULT_VALUE: GitHubUserContextValue = {
  user: null,
  signIn: noopSignIn,
  signOut: () => {},
  token: null,
  setToken: () => false,
  status: 'loading',
  userKey: GUEST,
};

const GitHubUserContext = createContext<GitHubUserContextValue>(DEFAULT_VALUE);

/** localStorage, or null when the browser blocks it (or during SSR). */
function getLocalStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') {
      return null;
    }
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

function readItem(key: string): string | null {
  try {
    return getLocalStorage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeItem(key: string, value: string | null): void {
  try {
    const storage = getLocalStorage();
    if (!storage) {
      return;
    }
    if (value === null) {
      storage.removeItem(key);
    } else {
      storage.setItem(key, value);
    }
  } catch {
    // Full or blocked storage: the value lasts until the page is closed.
  }
}

function isHttpsUrl(value: unknown): value is string {
  if (typeof value !== 'string') {
    return false;
  }
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

/** A saved user, checked again: localStorage is not trusted input. */
export function parseStoredUser(raw: string | null): GitHubUser | null {
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw) as Partial<GitHubUser> | null;
    if (!value || typeof value.login !== 'string' || !LOGIN_PATTERN.test(value.login)) {
      return null;
    }
    const login = value.login;
    return {
      login,
      name: typeof value.name === 'string' && value.name.trim() ? value.name.trim().slice(0, 100) : login,
      avatarUrl: isHttpsUrl(value.avatarUrl) ? value.avatarUrl : `https://github.com/${login}.png`,
      htmlUrl: isHttpsUrl(value.htmlUrl) ? value.htmlUrl : `https://github.com/${login}`,
    };
  } catch {
    return null;
  }
}

function parseStoredToken(raw: string | null): string | null {
  const value = raw?.trim() ?? '';
  return TOKEN_PATTERN.test(value) ? value : null;
}

export function GitHubUserProvider({children}: {children: ReactNode}): React.JSX.Element {
  const [user, setUser] = useState<GitHubUser | null>(null);
  const [token, setTokenState] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState(0);
  // Only the latest signIn() call may change the user.
  const latestRequest = useRef(0);

  useEffect(() => {
    setUser(parseStoredUser(readItem(USER_STORAGE_KEY)));
    setTokenState(parseStoredToken(readItem(TOKEN_STORAGE_KEY)));
    setLoaded(true);

    // Keep other tabs of the site in step.
    const onStorage = (event: StorageEvent) => {
      if (event.key === null) {
        setUser(null);
        setTokenState(null);
      } else if (event.key === USER_STORAGE_KEY) {
        setUser(parseStoredUser(event.newValue));
      } else if (event.key === TOKEN_STORAGE_KEY) {
        setTokenState(parseStoredToken(event.newValue));
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const tokenRef = useRef<string | null>(null);
  tokenRef.current = token;

  const signIn = useCallback(async (login: string): Promise<SignInResult> => {
    const request = ++latestRequest.current;
    setPending((count) => count + 1);
    try {
      const result = await fetchUser(login, {token: tokenRef.current});
      if (request !== latestRequest.current) {
        return {ok: false, error: 'superseded', message: 'A newer sign-in replaced this one.'};
      }
      if (!result.ok) {
        return {ok: false, error: result.error, message: result.message};
      }
      setUser(result.data);
      writeItem(USER_STORAGE_KEY, JSON.stringify(result.data));
      return {ok: true, user: result.data};
    } catch {
      return {ok: false, error: 'network error', message: 'Could not reach GitHub.'};
    } finally {
      setPending((count) => count - 1);
    }
  }, []);

  const signOut = useCallback(() => {
    latestRequest.current += 1;
    setUser(null);
    writeItem(USER_STORAGE_KEY, null);
  }, []);

  const setToken = useCallback((next: string | null): boolean => {
    const value = next?.trim() ?? '';
    if (!value) {
      setTokenState(null);
      writeItem(TOKEN_STORAGE_KEY, null);
      return true;
    }
    if (!TOKEN_PATTERN.test(value)) {
      return false;
    }
    setTokenState(value);
    writeItem(TOKEN_STORAGE_KEY, value);
    return true;
  }, []);

  const status: GitHubUserStatus = !loaded
    ? 'loading'
    : pending > 0
      ? 'signing-in'
      : user
        ? 'signed-in'
        : 'signed-out';

  const value = useMemo<GitHubUserContextValue>(
    () => ({
      user,
      signIn,
      signOut,
      token,
      setToken,
      status,
      userKey: user ? user.login : GUEST,
    }),
    [user, signIn, signOut, token, setToken, status],
  );

  return <GitHubUserContext.Provider value={value}>{children}</GitHubUserContext.Provider>;
}

/** The visitor's GitHub identity and token. Works signed out (user null). */
export function useGitHubUser(): GitHubUserContextValue {
  return useContext(GitHubUserContext);
}
