// The optional GitHub sign-in, on the browser side.
//
// Visitors are guests by default: reading, likes and comments never need an
// account, and nothing asks anyone to sign in on arrival. A visitor who wants
// their likes and comments to carry their GitHub name signs in:
//
// 1. signIn() navigates to /api/auth/login?returnTo=<this page>, which sends
//    the browser to GitHub's own sign-in and authorize page.
// 2. GitHub sends the browser back to /api/auth/callback; the server reads the
//    public profile, sets a signed HttpOnly session cookie (400 days, renewed)
//    and redirects to the page with #signed-in (or #signin-error=<code>).
// 3. On every load this provider asks /api/auth/me who is signed in. The last
//    known profile is kept in localStorage so the navbar shows it at once,
//    and the accounts used before are kept for the account switcher.
//
// Nothing signs a visitor out except "Sign out" in the account menu, and
// signing in as someone else only replaces the active account: the account
// list and everything posted stay.
//
// Without the server functions (`npm start`, `npm run serve`) /api/auth/me
// cannot answer: the saved profile is then shown as "not verified on this
// deployment", and the old typed-username form is offered as the fallback.
// When the deployment has no OAuth settings (me answers configured: false),
// sign-in is hidden: visitors are guests, and the same typed-username
// fallback is offered (a typed profile is kept there). A typed username is
// never verified; with the shared community store, a saved GitHub token is
// what lets the site confirm it (see CommunityStoreContext).
//
// An optional GitHub token can be kept too (localStorage, this device only).
// It is used for the blog editor's "Open pull request" and for higher GitHub
// API rate limits (src/lib/github.mjs sends it to api.github.com), and, with
// the shared community store, it can be sent with likes and comments to this
// site's own /api/community, which checks it with GitHub to show the
// visitor's verified name and never stores it (see CommunityStoreContext for
// when). Nothing else receives it. It has nothing to do with the sign-in
// session.
//
// SSR-safe: storage and the network are used in effects after mount, so the
// server and the first client render both start with status 'loading'.

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from 'react';
import {useLocation} from '@docusaurus/router';
import {fetchUser, type GitHubUser} from './github.mjs';
import {GUEST} from './storage.mjs';
import {
  ACCOUNTS_STORAGE_KEY,
  USER_STORAGE_KEY,
  buildLoginUrl,
  fetchMe,
  mustStaySignedIn,
  parseAccounts,
  parseStoredProfile,
  readAuthHash,
  rememberAccount,
  requestSignOut,
  serializeAccounts,
  serializeStoredProfile,
  urlWithoutHash,
  SIGNIN_COOKIE_ERROR,
  type Account,
  type AuthHash,
  type MeResult,
} from './auth.mjs';

export type {GitHubUser, Account};
export {USER_STORAGE_KEY, ACCOUNTS_STORAGE_KEY};

/** Storage key of the optional GitHub token (localStorage). */
export const TOKEN_STORAGE_KEY = 'rid-github:v1:token';

/**
 * - 'loading': before the saved profile has been read (server render, first client render)
 * - 'signed-out' / 'signed-in'
 * - 'signing-in': on the way to GitHub, or looking up a typed username (fallback)
 */
export type GitHubUserStatus = 'loading' | 'signed-out' | 'signing-in' | 'signed-in';

/**
 * Whether GitHub sign-in works on this deployment:
 * - 'checking': /api/auth/me has not answered yet
 * - 'available': the auth functions answered and are configured
 * - 'not-configured': they answered, but the OAuth settings are missing (sign-in hidden)
 * - 'unreachable': no auth functions here (local `npm start` / `npm run serve`)
 */
export type AuthAvailability = 'checking' | 'available' | 'not-configured' | 'unreachable';

export type AuthNotice = {id: number; kind: 'success' | 'error' | 'info'; message: string};

export type SignInResult =
  | {ok: true; user: GitHubUser}
  | {ok: false; error: string; message: string};

export type GitHubUserContextValue = {
  /** The active profile, or null for a guest. */
  user: GitHubUser | null;
  /** True when /api/auth/me confirmed `user` on this deployment (a GitHub sign-in). */
  verified: boolean;
  status: GitHubUserStatus;
  availability: AuthAvailability;
  /** Accounts used before in this browser, most recent first (the account switcher). */
  accounts: Account[];
  /** Goes to GitHub to sign in, then back to this page. */
  signIn: () => void;
  /** Same, asking GitHub for its account picker (or suggesting `login`). */
  switchAccount: (login?: string) => void;
  /** Ends the session (manual only). Keeps the account list and everything posted. */
  signOut: () => Promise<void>;
  /**
   * Fallback when the auth functions are unreachable: labels this browser
   * with a typed username's public profile. Not verified. Never rejects.
   */
  signInWithUsername: (login: string) => Promise<SignInResult>;
  /** A message about the last sign-in or sign-out (shown as a toast). */
  notice: AuthNotice | null;
  dismissNotice: () => void;
  /** The optional GitHub token, or null. Only send it to api.github.com and this site's /api/community. */
  token: string | null;
  /** Saves the token on this device (trimmed); null or '' removes it. Returns false for a malformed token. */
  setToken: (token: string | null) => boolean;
  /** Key for likes and comment ownership in this browser: the login, or 'guest'. */
  userKey: string;
  /** Goes up by one each time requestAccountMenu() is called (the navbar item opens its menu). */
  accountMenuRequest: number;
  /** Asks the navbar account item to open its menu with the token field shown. */
  requestAccountMenu: () => void;
};

/** Printable ASCII, no spaces: what a GitHub token looks like. */
const TOKEN_PATTERN = /^[\x21-\x7E]{1,1000}$/;

const DEFAULT_VALUE: GitHubUserContextValue = {
  user: null,
  verified: false,
  status: 'loading',
  availability: 'checking',
  accounts: [],
  signIn: () => {},
  switchAccount: () => {},
  signOut: async () => {},
  signInWithUsername: async () => ({ok: false, error: 'unavailable', message: 'Sign-in is not available here.'}),
  notice: null,
  dismissNotice: () => {},
  token: null,
  setToken: () => false,
  userKey: GUEST,
  accountMenuRequest: 0,
  requestAccountMenu: () => {},
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

function parseStoredToken(raw: string | null): string | null {
  const value = raw?.trim() ?? '';
  return TOKEN_PATTERN.test(value) ? value : null;
}

function toUser(profile: GitHubUser): GitHubUser {
  const {login, name, avatarUrl, htmlUrl} = profile;
  return {login, name, avatarUrl, htmlUrl};
}

function sameLogin(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

/** Removes #signed-in / #signin-error=… from the address bar (no navigation, no scroll). */
function cleanAuthHash(): void {
  try {
    window.history.replaceState(window.history.state, '', urlWithoutHash(window.location));
  } catch {
    // Some embedded browsers forbid replaceState: the fragment stays, harmlessly.
  }
}

export function GitHubUserProvider({children}: {children: ReactNode}): React.JSX.Element {
  const [user, setUser] = useState<GitHubUser | null>(null);
  const [verified, setVerified] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [availability, setAvailability] = useState<AuthAvailability>('checking');
  const [token, setTokenState] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState(0);
  const [navigating, setNavigating] = useState(false);
  const [notice, setNotice] = useState<AuthNotice | null>(null);
  const [accountMenuRequest, setAccountMenuRequest] = useState(0);
  const noticeId = useRef(0);
  // Only the latest sign-in or sign-out may change the user.
  const latestRequest = useRef(0);
  // The mount-time /api/auth/me request: its answer may renew the session
  // cookie, so a sign-out waits for it before clearing the cookie.
  const mePromise = useRef<Promise<MeResult> | null>(null);
  const availabilityRef = useRef<AuthAvailability>('checking');
  availabilityRef.current = availability;
  const tokenRef = useRef<string | null>(null);
  tokenRef.current = token;

  const show = useCallback((kind: AuthNotice['kind'], message: string) => {
    noticeId.current += 1;
    setNotice({id: noticeId.current, kind, message});
  }, []);

  const saveAccounts = useCallback((update: (current: Account[]) => Account[]) => {
    setAccounts((current) => {
      const next = update(current);
      if (next !== current) {
        writeItem(ACCOUNTS_STORAGE_KEY, serializeAccounts(next));
      }
      return next;
    });
  }, []);

  useEffect(() => {
    const stored = parseStoredProfile(readItem(USER_STORAGE_KEY));
    setUser(stored ? toUser(stored) : null);
    // A profile from a real sign-in counts as verified until the server says otherwise.
    setVerified(stored?.source === 'session');
    setAccounts(parseAccounts(readItem(ACCOUNTS_STORAGE_KEY)));
    setTokenState(parseStoredToken(readItem(TOKEN_STORAGE_KEY)));
    setLoaded(true);

    // The way back from GitHub: read the fragment once, then clean it.
    const hash: AuthHash | null = readAuthHash(window.location.hash);
    if (hash) {
      cleanAuthHash();
    }

    let active = true;
    const request = latestRequest.current;
    const pendingMe = fetchMe();
    mePromise.current = pendingMe;
    void pendingMe.then((result: MeResult) => {
      if (!active) {
        return;
      }
      // A sign-out (or a typed sign-in) since the request: keep that.
      const superseded = request !== latestRequest.current;
      if (result.kind === 'signed-in') {
        setAvailability('available');
        if (!superseded) {
          setUser(toUser(result.user));
          setVerified(true);
          writeItem(USER_STORAGE_KEY, serializeStoredProfile(result.user, 'session'));
          saveAccounts((current) => rememberAccount(current, result.user));
        }
        if (hash?.kind === 'signed-in') {
          show('success', `Signed in as @${result.user.login}.`);
        } else if (hash?.kind === 'error') {
          show('error', hash.message);
        }
        return;
      }
      if (result.kind === 'signed-out') {
        setAvailability(result.configured ? 'available' : 'not-configured');
        if (!superseded && !result.configured && stored?.source === 'typed') {
          // No GitHub sign-in on this deployment: the typed username is the
          // fallback, so it stays (not verified).
          setVerified(false);
        } else if (!superseded) {
          // The server is the truth: no session here, so the visitor is a
          // guest. A profile from a real sign-in stays in the account list.
          setUser(null);
          setVerified(false);
          writeItem(USER_STORAGE_KEY, null);
          if (stored?.source === 'session') {
            saveAccounts((current) =>
              current.some((account) => sameLogin(account.login, stored.login))
                ? current
                : rememberAccount(current, stored),
            );
          }
        }
        if (result.configured && hash?.kind === 'signed-in') {
          show('error', SIGNIN_COOKIE_ERROR);
        } else if (result.configured && hash?.kind === 'error') {
          show('error', hash.message);
        }
        return;
      }
      // Unreachable: keep the saved profile, shown as not verified here.
      setAvailability('unreachable');
      if (!superseded) {
        setVerified(false);
      }
      if (hash?.kind === 'error') {
        show('error', hash.message);
      }
    });

    // Keep other tabs of the site in step.
    const onStorage = (event: StorageEvent) => {
      if (event.key === null) {
        latestRequest.current += 1;
        setUser(null);
        setVerified(false);
        setAccounts([]);
        setTokenState(null);
      } else if (event.key === USER_STORAGE_KEY) {
        latestRequest.current += 1;
        const next = parseStoredProfile(event.newValue);
        setUser(next ? toUser(next) : null);
        setVerified(Boolean(next && next.source === 'session' && availabilityRef.current === 'available'));
      } else if (event.key === ACCOUNTS_STORAGE_KEY) {
        setAccounts(parseAccounts(event.newValue));
      } else if (event.key === TOKEN_STORAGE_KEY) {
        setTokenState(parseStoredToken(event.newValue));
      }
    };
    window.addEventListener('storage', onStorage);
    return () => {
      active = false;
      window.removeEventListener('storage', onStorage);
    };
  }, [saveAccounts, show]);

  // Coming back from GitHub with the browser's Back button can restore this
  // page from the back-forward cache, still showing "Signing in…".
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        setNavigating(false);
      }
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  const goToGitHub = useCallback((options?: {switchAccount?: boolean; login?: string}) => {
    setNavigating(true);
    window.location.assign(buildLoginUrl(window.location, options));
  }, []);

  const signIn = useCallback(() => goToGitHub(), [goToGitHub]);

  const switchAccount = useCallback(
    // Always ask for GitHub's account picker: `login` alone is only a hint.
    (login?: string) => goToGitHub(login ? {switchAccount: true, login} : {switchAccount: true}),
    [goToGitHub],
  );

  const signOut = useCallback(async () => {
    // Supersedes a pending /api/auth/me answer or typed sign-in.
    const request = ++latestRequest.current;
    const mode = availabilityRef.current;
    if (mode === 'available' || mode === 'checking') {
      // The server clears the session cookie first; only then does the
      // visitor become a guest here. Likes and comments are read again as
      // soon as the identity changes, and the shared API reads the cookie, so
      // it must be gone by then (or the API would still answer, and write,
      // for the signed-in user).
      // A late /api/auth/me answer could re-issue the cookie after the clear:
      // let it land first (fetchMe never rejects and gives up after 8 s).
      // Decide from that answer, not from `mode`: a click while the check
      // was still running must not end in a local-only sign-out that leaves
      // a valid cookie behind.
      const me = await mePromise.current?.catch(() => undefined);
      const ok = await requestSignOut();
      if (mustStaySignedIn({ok, availability: availabilityRef.current, me})) {
        show('error', 'Could not sign out: the site could not be reached. Try again.');
        return;
      }
    }
    if (request !== latestRequest.current) {
      return; // a newer sign-in or sign-out took over meanwhile
    }
    setUser(null);
    setVerified(false);
    writeItem(USER_STORAGE_KEY, null);
    show('info', 'Signed out. Your likes and comments stay.');
  }, [show]);

  const signInWithUsername = useCallback(async (login: string): Promise<SignInResult> => {
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
      setVerified(false);
      writeItem(USER_STORAGE_KEY, serializeStoredProfile(result.data, 'typed'));
      return {ok: true, user: result.data};
    } catch {
      return {ok: false, error: 'network error', message: 'Could not reach GitHub.'};
    } finally {
      setPending((count) => count - 1);
    }
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

  const dismissNotice = useCallback(() => setNotice(null), []);

  const requestAccountMenu = useCallback(() => {
    setAccountMenuRequest((count) => count + 1);
  }, []);

  const status: GitHubUserStatus = !loaded
    ? 'loading'
    : pending > 0 || navigating
      ? 'signing-in'
      : user
        ? 'signed-in'
        : 'signed-out';

  const value = useMemo<GitHubUserContextValue>(
    () => ({
      user,
      verified: Boolean(user) && verified,
      status,
      availability,
      accounts,
      signIn,
      switchAccount,
      signOut,
      signInWithUsername,
      notice,
      dismissNotice,
      token,
      setToken,
      userKey: user ? user.login : GUEST,
      accountMenuRequest,
      requestAccountMenu,
    }),
    [
      user,
      verified,
      status,
      availability,
      accounts,
      signIn,
      switchAccount,
      signOut,
      signInWithUsername,
      notice,
      dismissNotice,
      token,
      setToken,
      accountMenuRequest,
      requestAccountMenu,
    ],
  );

  return <GitHubUserContext.Provider value={value}>{children}</GitHubUserContext.Provider>;
}

/** The visitor's GitHub identity and token. Works signed out (user null). */
export function useGitHubUser(): GitHubUserContextValue {
  return useContext(GitHubUserContext);
}

/**
 * The sign-in URL for the current page, for a plain <a href> (a full-page
 * navigation: /api/ routes are server functions, not site pages). Works in
 * the server render too.
 */
export function useSignInHref(options?: {switchAccount?: boolean; login?: string}): string {
  const location = useLocation();
  return buildLoginUrl(location, options);
}

/**
 * onClick for a sign-in link: a plain left click goes through signIn() (so
 * the page can show "Signing in…"); a modified click (new tab, new window)
 * is left to the browser.
 */
export function useSignInClick(): (event: MouseEvent<HTMLAnchorElement>) => void {
  const {signIn} = useGitHubUser();
  return useCallback(
    (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      event.preventDefault();
      signIn();
    },
    [signIn],
  );
}
