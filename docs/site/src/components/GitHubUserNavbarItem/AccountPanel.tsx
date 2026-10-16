import React, {useEffect, useId, useRef, useState, type FormEvent} from 'react';
import clsx from 'clsx';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';
import Avatar from './Avatar';
import styles from './styles.module.css';

type Props = {
  /** Move the focus into the panel when it opens. */
  autoFocus?: boolean;
};

/**
 * The content of the account menu: the username field (or the signed-in
 * profile and "Sign out"), and the optional GitHub token.
 */
export default function AccountPanel({autoFocus = false}: Props): React.JSX.Element {
  const {user, status} = useGitHubUser();
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
  return (
    <div className={styles.panel}>
      {user ? (
        <SignedIn autoFocus={focusSection} />
      ) : (
        <SignInForm autoFocus={focusSection} busy={status === 'signing-in'} />
      )}
      <TokenSection />
    </div>
  );
}

function SignInForm({autoFocus, busy}: {autoFocus: boolean; busy: boolean}): React.JSX.Element {
  const {signIn} = useGitHubUser();
  const [login, setLogin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();

  useEffect(() => {
    if (autoFocus) {
      input.current?.focus();
    }
  }, [autoFocus]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!login.trim()) {
      setError('Enter a GitHub username.');
      input.current?.focus();
      return;
    }
    setError(null);
    const result = await signIn(login);
    if (!result.ok && result.error !== 'superseded') {
      setError(result.message);
      input.current?.focus();
    }
  };

  return (
    <form className={styles.section} onSubmit={onSubmit} noValidate>
      <p className={styles.heading}>Sign in with GitHub</p>
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
        <button type="submit" className="button button--primary button--sm" disabled={busy} aria-busy={busy || undefined}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </div>
      {error && (
        <p id={errorId} className={styles.error} role="alert">
          {error}
        </p>
      )}
      <p id={hintId} className="rid-muted-note">
        No password: the site reads your public GitHub profile and keeps it in this browser. It is not a
        verified sign-in; your username only labels your likes and comments here.
      </p>
    </form>
  );
}

function SignedIn({autoFocus}: {autoFocus: boolean}): React.JSX.Element | null {
  const {user, signOut} = useGitHubUser();
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (autoFocus) {
      button.current?.focus();
    }
  }, [autoFocus]);

  if (!user) {
    return null;
  }
  return (
    <div className={styles.section}>
      <div className={styles.profile}>
        <Avatar src={user.avatarUrl} name={user.name} size="lg" />
        <div className={styles.profileText}>
          <span className={styles.profileName}>{user.name}</span>
          <a href={user.htmlUrl} target="_blank" rel="noopener noreferrer" className={styles.profileLogin}>
            @{user.login}
          </a>
        </div>
      </div>
      <p className="rid-muted-note">
        Your likes and comments in this browser carry this username.
      </p>
      <button ref={button} type="button" className="button button--secondary button--sm" onClick={signOut}>
        Sign out
      </button>
    </div>
  );
}

function TokenSection(): React.JSX.Element {
  const {token, setToken} = useGitHubUser();
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
    <details className={clsx(styles.section, styles.token)}>
      <summary className={styles.tokenSummary}>
        GitHub token <span className={styles.tokenState}>{token ? 'saved' : 'optional'}</span>
      </summary>
      <p id={noteId} className="rid-muted-note">
        Used only for the blog editor&apos;s &ldquo;Open pull request&rdquo; and for higher GitHub API rate
        limits. It is kept in this browser on this device only and is sent nowhere but api.github.com.
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
