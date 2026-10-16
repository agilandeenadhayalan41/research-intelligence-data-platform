// The toast after coming back from GitHub (or after signing out): "Signed in
// as @login", or why the sign-in did not complete. Rendered once by
// src/theme/Root.tsx. Success messages fade after a few seconds; errors stay
// until dismissed. Both live regions always exist, so screen readers announce
// the message when it appears.

import React, {useEffect} from 'react';
import clsx from 'clsx';
import {useGitHubUser, type AuthNotice as Notice} from '@site/src/lib/GitHubUserContext';
import styles from './styles.module.css';

const AUTO_DISMISS_MS = 6000;

function Toast({notice, onDismiss}: {notice: Notice; onDismiss: () => void}): React.JSX.Element {
  return (
    <div className={clsx(styles.toast, styles[notice.kind])}>
      <p className={styles.message}>{notice.message}</p>
      <button type="button" className={styles.close} onClick={onDismiss} aria-label="Dismiss this message">
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

export default function AuthNotice(): React.JSX.Element {
  const {notice, dismissNotice} = useGitHubUser();

  useEffect(() => {
    if (!notice || notice.kind === 'error') {
      return undefined;
    }
    const timer = window.setTimeout(dismissNotice, AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [notice, dismissNotice]);

  return (
    <div className={styles.region}>
      <div role="status" aria-live="polite">
        {notice && notice.kind !== 'error' && <Toast key={notice.id} notice={notice} onDismiss={dismissNotice} />}
      </div>
      <div role="alert" aria-live="assertive">
        {notice && notice.kind === 'error' && <Toast key={notice.id} notice={notice} onDismiss={dismissNotice} />}
      </div>
    </div>
  );
}
