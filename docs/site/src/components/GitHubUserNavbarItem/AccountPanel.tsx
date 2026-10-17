import React, {useEffect, useId, useRef, useState, type FormEvent} from 'react';
import clsx from 'clsx';
import {useGitHubUser, useSignInClick, useSignInHref, type Account} from '@site/src/lib/GitHubUserContext';
import {useCommunityBackend} from '@site/src/lib/CommunityStoreContext';
import Avatar from './Avatar';
import styles from './styles.module.css';

type Props = {
  /** Move the focus into the panel when it opens. */
  autoFocus?: boolean;
  /** Open with the token field shown (the panel was opened to add a token). */
  revealToken?: boolean;
};

function sameLogin(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

/**
 * The content of the account menu.
 *
 * Signed in: the profile (link to GitHub), "Switch account", "Sign out", the
 * other accounts used in this browser, and the optional GitHub token.
 * Signed out: "Continue with GitHub", the accounts used before, the
 * unverified username fallback (only where GitHub sign-in is not available:
 * no auth functions here, or no OAuth settings), and the token.
 */
export default function AccountPanel({autoFocus = false, revealToken = false}: Props): React.JSX.Element {
  const {user, status, token} = useGitHubUser();
  // With shared likes and comments the token can show the visitor's verified name.
  const shared = useCommunityBackend() === 'shared';
  // Signing in or out replaces the section that had the focus. Once that has
  // happened, move the focus into the new section (the mobile panel opens
  // without autoFocus), so it does not drop to the page.
  const signedIn = Boolean(user);
  const initiallySignedIn = useRef(signedIn);
  const [switched, setSwitched] = useState(false);
  useEffect(() => {
    if (signedIn !== initiallySignedIn.current) {
      setSwitched(true);
    }
  }, [signedIn]);
  const focusSection = autoFocus || switched;
  // Opened to add a token: the token field gets the focus.
  const focusToken = revealToken && !token && (autoFocus || switched);
  return (
    <div className={styles.panel}>
      {user ? (
        <SignedIn autoFocus={focusSection && !focusToken} shared={shared} />
      ) : (
        <SignedOut autoFocus={focusSection && !focusToken} busy={status === 'signing-in'} shared={shared} />
      )}
      <TokenSection defaultOpen={revealToken} autoFocus={focusToken} shared={shared} />
    </div>
  );
}

function SignedOut({
  autoFocus,
  busy,
  shared,
}: {
  autoFocus: boolean;
  busy: boolean;
  shared: boolean;
}): React.JSX.Element {
  const {availability, accounts} = useGitHubUser();
  const href = useSignInHref();
  const onSignInClick = useSignInClick();
  const first = useRef<HTMLAnchorElement>(null);
  const heading = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (autoFocus) {
      (first.current ?? heading.current)?.focus();
    }
  }, [autoFocus]);

  if (availability === 'not-configured') {
    return (
      <>
        <div className={styles.section}>
          <p ref={heading} className={styles.heading} tabIndex={-1}>
            Sign-in is not available on this deployment
          </p>
          <p className="rid-muted-note">Reading, likes and comments work as a guest.</p>
        </div>
        <UsernameFallback busy={busy} shared={shared} />
      </>
    );
  }

  return (
    <>
      <div className={styles.section}>
        <p className={styles.heading}>Sign in with GitHub</p>
        <p className="rid-muted-note">
          Optional: reading, likes and comments work as a guest. Sign in to show your GitHub name on
          them. GitHub checks who you are; this site keeps only your public profile (username, name
          and avatar).
        </p>
        <div className={styles.actions}>
          <a
            ref={first}
            href={href}
            className="button button--primary button--sm"
            onClick={onSignInClick}
            aria-busy={busy || undefined}>
            {busy ? 'Signing in…' : 'Continue with GitHub'}
          </a>
        </div>
        {availability === 'unreachable' && (
          <p className="rid-muted-note">
            The sign-in service does not answer here (it runs only on the deployed site), so this link may
            fail on a local preview.
          </p>
        )}
      </div>
      {accounts.length > 0 && (
        <AccountList title="Accounts used in this browser" accounts={accounts} action="Sign in" />
      )}
      {availability === 'unreachable' && <UsernameFallback busy={busy} shared={shared} />}
    </>
  );
}

function AccountList({
  title,
  accounts,
  action,
}: {
  title: string;
  accounts: Account[];
  action: string;
}): React.JSX.Element {
  const {switchAccount} = useGitHubUser();
  const titleId = useId();
  return (
    <div className={styles.section}>
      <p id={titleId} className={styles.subheading}>
        {title}
      </p>
      <ul className={styles.accounts} aria-labelledby={titleId}>
        {accounts.map((account) => (
          <li key={account.login} className={styles.account}>
            <Avatar src={account.avatarUrl} name={account.name} size="sm" />
            <span className={styles.accountText}>
              <span className={styles.accountName}>{account.name}</span>
              {account.name !== account.login && <span className={styles.accountLogin}>@{account.login}</span>}
            </span>
            <button
              type="button"
              className="button button--secondary button--sm"
              onClick={() => switchAccount(account.login)}
              aria-label={`${action} as @${account.login}`}>
              {action}
            </button>
          </li>
        ))}
      </ul>
      <p className="rid-muted-note">GitHub confirms the account: you may be asked to sign in there.</p>
    </div>
  );
}

/** The old typed-username sign-in, kept only for where GitHub sign-in is not available. */
function UsernameFallback({busy, shared}: {busy: boolean; shared: boolean}): React.JSX.Element {
  const {signInWithUsername} = useGitHubUser();
  const [login, setLogin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!login.trim()) {
      setError('Enter a GitHub username.');
      input.current?.focus();
      return;
    }
    setError(null);
    const result = await signInWithUsername(login);
    if (!result.ok && result.error !== 'superseded') {
      setError(result.message);
      input.current?.focus();
    }
  };

  return (
    <details className={clsx(styles.section, styles.fallback)}>
      <summary className={styles.fallbackSummary}>
        Use a GitHub username instead <span className={styles.unverifiedBadge}>not verified</span>
      </summary>
      <form className={styles.fallbackForm} onSubmit={onSubmit} noValidate>
        <label className={styles.label} htmlFor={inputId}>
          GitHub username
        </label>
        <div className={styles.inputRow}>
          <input
            ref={input}
            id={inputId}
            className={styles.input}
            type="text"
            name="github-username"
            value={login}
            onChange={(event) => setLogin(event.target.value)}
            placeholder="octocat"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            maxLength={100}
            aria-invalid={error ? true : undefined}
            aria-describedby={clsx(hintId, error && errorId)}
          />
          <button type="submit" className="button button--secondary button--sm" disabled={busy} aria-busy={busy || undefined}>
            {busy ? 'Looking up…' : 'Use'}
          </button>
        </div>
        {error && (
          <p id={errorId} className={styles.error} role="alert">
            {error}
          </p>
        )}
        <p id={hintId} className="rid-muted-note">
          {shared ? (
            <>
              Not verified on this deployment: the site reads the public profile of the username you type and
              keeps it in this browser. Nobody checks that it is yours, so likes and comments stay a
              guest&apos;s unless you also add a GitHub token below: the site then checks it with GitHub and
              shows your name.
            </>
          ) : (
            <>
              Not verified on this deployment: the site reads the public profile of the username you type and
              keeps it in this browser. Nobody checks that it is yours; it only labels your likes and comments
              here.
            </>
          )}
        </p>
      </form>
    </details>
  );
}

function SignedIn({autoFocus, shared}: {autoFocus: boolean; shared: boolean}): React.JSX.Element | null {
  const {user, verified, availability, accounts, signOut, switchAccount, token} = useGitHubUser();
  const button = useRef<HTMLButtonElement>(null);
  // Signing out waits for the server to clear the session cookie.
  const [signingOut, setSigningOut] = useState(false);
  const onSignOut = async () => {
    if (signingOut) {
      return;
    }
    setSigningOut(true);
    try {
      await signOut();
    } finally {
      setSigningOut(false);
    }
  };

  useEffect(() => {
    if (autoFocus) {
      button.current?.focus();
    }
  }, [autoFocus]);

  if (!user) {
    return null;
  }
  const canSwitch = availability !== 'not-configured';
  const others = canSwitch ? accounts.filter((account) => !sameLogin(account.login, user.login)) : [];
  return (
    <>
      <div className={styles.section}>
        <div className={styles.profile}>
          <Avatar src={user.avatarUrl} name={user.name} size="lg" />
          <div className={styles.profileText}>
            <span className={styles.profileName}>{user.name}</span>
            <a href={user.htmlUrl} target="_blank" rel="noopener noreferrer" className={styles.profileLogin}>
              @{user.login}
              <span className="rid-sr-only"> on GitHub (opens in a new tab)</span>
            </a>
          </div>
        </div>
        {verified ? (
          <p className="rid-muted-note">Signed in with GitHub. Your likes and comments show this account.</p>
        ) : (
          <p className="rid-muted-note">
            <span className={styles.unverifiedBadge}>not verified</span> Not verified on this deployment: this
            profile is only remembered by this browser.
            {shared &&
              (token
                ? ' Your GitHub token confirms your likes and comments; they show the account it belongs to.'
                : ' Without a token your likes and comments are posted as a guest. Add one below to show your name.')}
          </p>
        )}
        <div className={styles.actions}>
          {canSwitch && (
            <button type="button" className="button button--secondary button--sm" onClick={() => switchAccount()}>
              Switch account
            </button>
          )}
          <button
            ref={button}
            type="button"
            className="button button--secondary button--sm"
            onClick={() => void onSignOut()}
            aria-busy={signingOut || undefined}>
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </div>
      {others.length > 0 && <AccountList title="Other accounts used in this browser" accounts={others} action="Switch" />}
    </>
  );
}

function TokenSection({
  defaultOpen,
  autoFocus,
  shared,
}: {
  defaultOpen: boolean;
  autoFocus: boolean;
  shared: boolean;
}): React.JSX.Element {
  const {token, setToken} = useGitHubUser();
  const [expanded, setExpanded] = useState(defaultOpen);
  const [value, setValue] = useState('');
  const [message, setMessage] = useState<{text: string; error: boolean} | null>(null);
  const inputId = useId();
  const noteId = useId();
  // Saving or removing the token swaps the form for the "Remove token" row
  // (and back), which removes the focused control: move the focus to the new
  // one so it does not drop to the page (and Escape still closes the menu).
  const removeRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [focusNext, setFocusNext] = useState<'remove' | 'input' | null>(null);
  useEffect(() => {
    if (focusNext === 'remove') {
      removeRef.current?.focus();
    } else if (focusNext === 'input') {
      inputRef.current?.focus();
    }
    if (focusNext) {
      setFocusNext(null);
    }
  }, [token, focusNext]);
  useEffect(() => {
    if (autoFocus) {
      setExpanded(true);
      inputRef.current?.focus();
    }
  }, [autoFocus]);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!value.trim()) {
      setMessage({text: 'Paste a token first.', error: true});
      return;
    }
    if (setToken(value)) {
      setValue('');
      setMessage({text: 'Token saved on this device.', error: false});
      setFocusNext('remove');
    } else {
      setMessage({text: 'That does not look like a GitHub token (no spaces or special characters).', error: true});
    }
  };

  const remove = () => {
    setToken(null);
    setMessage({text: 'Token removed from this device.', error: false});
    setFocusNext('input');
  };

  return (
    <details
      className={clsx(styles.section, styles.token)}
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className={styles.tokenSummary}>
        GitHub token <span className={styles.tokenState}>{token ? 'saved' : 'optional'}</span>
      </summary>
      <p id={noteId} className="rid-muted-note">
        {shared ? (
          <>
            Optional. Used for the blog editor&apos;s &ldquo;Open pull request&rdquo; and for higher GitHub API
            rate limits. It is kept in this browser on this device only and sent to api.github.com. It is
            separate from signing in: signed in with GitHub, likes and comments never need it. Only with a
            typed username, where sign-in is not available, is it sent with your likes and comments to this
            site&apos;s /api/community, to confirm your login; it is never stored there.
          </>
        ) : (
          <>
            Used only for the blog editor&apos;s &ldquo;Open pull request&rdquo; and for higher GitHub API rate
            limits. It is kept in this browser on this device only and is sent nowhere but api.github.com. It is
            separate from signing in.
          </>
        )}
      </p>
      {token ? (
        <div className={styles.inputRow}>
          <span className={styles.tokenSaved}>A token is saved on this device.</span>
          <button ref={removeRef} type="button" className="button button--secondary button--sm" onClick={remove}>
            Remove token
          </button>
        </div>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <label className={styles.label} htmlFor={inputId}>
            Token
          </label>
          <div className={styles.inputRow}>
            <input
              ref={inputRef}
              id={inputId}
              className={styles.input}
              type="password"
              name="github-token"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              aria-describedby={noteId}
            />
            <button type="submit" className="button button--primary button--sm">
              Save
            </button>
          </div>
        </form>
      )}
      {message && (
        <p className={message.error ? styles.error : styles.success} role={message.error ? 'alert' : 'status'}>
          {message.text}
        </p>
      )}
      <p className="rid-muted-note">
        <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer">
          Create a token on GitHub
        </a>
        . Remove it here on a shared computer.
      </p>
    </details>
  );
}
