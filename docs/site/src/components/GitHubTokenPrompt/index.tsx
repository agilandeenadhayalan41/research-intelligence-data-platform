// The one prompt for a missing GitHub token, shared by the blog editor's
// "Open pull request" and the blog's "Delete" dialog, with the one rule for
// when a token is there (usableToken).

import React, {type ReactNode} from 'react';
import clsx from 'clsx';

import styles from './styles.module.css';

/** The token when it can be used (a non-empty string), else null. */
export function usableToken(token: string | null | undefined): string | null {
  return typeof token === 'string' && token.trim() !== '' ? token : null;
}

type Props = {
  /** Bold first sentence, e.g. "Want to open a pull request from here?". */
  lead: ReactNode;
  /** What to do without a token, after the instructions. */
  children?: ReactNode;
  className?: string;
};

export default function GitHubTokenPrompt({lead, children, className}: Props): ReactNode {
  return (
    <div className={clsx('alert alert--secondary', styles.prompt, className)} role="note">
      <strong>{lead}</strong> Add a GitHub token in the account menu at the right of the
      navigation bar (<em>Sign in</em>, or your username when signed in). The token is kept on this
      device only and is sent only to api.github.com.
      {children ? <> {children}</> : null}
    </div>
  );
}
