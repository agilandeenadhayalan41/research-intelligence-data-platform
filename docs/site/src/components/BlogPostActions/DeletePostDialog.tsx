// "Delete this post?" A modal dialog that opens a pull request deleting a
// blog post (openBlogDeletePullRequest in src/lib/github.mjs) and shows each
// step and the pull request's link. Without a GitHub token it explains how to
// add one.
//
// Accessibility: a native <dialog> opened with showModal() (the rest of the
// page is inert), labelled by its heading and described by its text; Tab and
// Shift+Tab stay inside it; Escape, Cancel and a click on the backdrop close
// it, except while the pull request is being opened. The opener puts the
// focus back on the Delete button.

import React, {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type SyntheticEvent,
} from 'react';
import clsx from 'clsx';
import {GitHubError, openBlogDeletePullRequest} from '@site/src/lib/github.mjs';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';
import GitHubTokenPrompt, {usableToken} from '@site/src/components/GitHubTokenPrompt';
import PullRequestSteps, {
  advanceStep,
  failLastStep,
  finishSteps,
  trackingFetch,
  type Step,
} from '@site/src/components/BlogEditor/PullRequestSteps';
import editorStyles from '@site/src/components/BlogEditor/styles.module.css';
import {useRepoConfig, type RepoConfig} from '@site/src/components/PageMetaBar/usePageMeta';

import styles from './styles.module.css';

type Props = {
  /** Repository path of the post (a .md or .mdx file under docs/site/blog/). */
  repoPath: string;
  title: string;
  onClose: () => void;
};

type Outcome =
  | {kind: 'idle'}
  | {kind: 'running'}
  | {kind: 'done'; url: string; number: number; branch: string; head: string; forked: boolean}
  | {kind: 'error'; message: string};

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** GitHub's own page for deleting a file (it forks for people who cannot push). */
function githubDeleteUrl(repo: RepoConfig, path: string): string {
  const encoded = path.split('/').map(encodeURIComponent).join('/');
  return `https://github.com/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.name)}/delete/${encodeURIComponent(repo.branch)}/${encoded}`;
}

export default function DeletePostDialog({repoPath, title, onClose}: Props): ReactNode {
  const id = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const {token} = useGitHubUser();
  const repo = useRepoConfig();
  const usable = usableToken(token);
  const [steps, setSteps] = useState<Step[]>([]);
  const [outcome, setOutcome] = useState<Outcome>({kind: 'idle'});
  const running = outcome.kind === 'running';
  const done = outcome.kind === 'done';
  const runningRef = useRef(running);
  runningRef.current = running;
  const fileName = repoPath.split('/').pop() ?? repoPath;

  // Open as a modal, focus Cancel (the safe choice); close on unmount.
  useEffect(() => {
    mounted.current = true;
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === 'function') {
        dialog.showModal();
      } else {
        dialog.setAttribute('open', '');
      }
    }
    cancelRef.current?.focus();
    return () => {
      mounted.current = false;
      if (dialog?.open && typeof dialog.close === 'function') {
        dialog.close();
      }
    };
  }, []);

  useEffect(() => {
    if (outcome.kind === 'done' || outcome.kind === 'error') {
      resultRef.current?.focus();
    }
  }, [outcome.kind]);

  function requestClose(): void {
    if (!runningRef.current) {
      onClose();
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDialogElement>): void {
    if (event.key === 'Escape') {
      // Handled here, so the browser does not close the dialog by itself.
      event.preventDefault();
      event.stopPropagation();
      requestClose();
      return;
    }
    if (event.key !== 'Tab') {
      return;
    }
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }
    const focusable = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (element) => element.getClientRects().length > 0,
    );
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    const inside = active instanceof Node && dialog.contains(active);
    if (event.shiftKey && (active === first || !inside || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !inside)) {
      event.preventDefault();
      first.focus();
    }
  }

  // The browser's own cancel (for example the Android back gesture).
  function onCancel(event: SyntheticEvent<HTMLDialogElement>): void {
    event.preventDefault();
    requestClose();
  }

  // A click on the backdrop lands on the <dialog> itself (its content fills it).
  function onClick(event: MouseEvent<HTMLDialogElement>): void {
    if (event.target === dialogRef.current) {
      requestClose();
    }
  }

  async function confirm(): Promise<void> {
    if (!usable || !repo || running || done) {
      return;
    }
    setOutcome({kind: 'running'});
    setSteps([]);
    const fetchImpl = trackingFetch(
      (label) => {
        if (mounted.current) {
          setSteps((previous) => advanceStep(previous, label));
        }
      },
      repo,
      'delete',
    );
    try {
      const result = await openBlogDeletePullRequest({
        owner: repo.owner,
        repo: repo.name,
        base: repo.branch,
        token: usable,
        path: repoPath,
        title,
        body: `Deletes the blog post "${title}". Submitted from the docs site blog.\n\nFile: \`${repoPath}\``,
        fetchImpl,
      });
      if (!mounted.current) {
        return;
      }
      setSteps(finishSteps);
      setOutcome({kind: 'done', ...result});
    } catch (error) {
      if (!mounted.current) {
        return;
      }
      setSteps(failLastStep);
      setOutcome({
        kind: 'error',
        // GitHubError messages never contain the token (see github.mjs).
        message:
          error instanceof GitHubError
            ? error.message
            : 'Something went wrong while talking to GitHub. Check the network connection and try again.',
      });
    }
  }

  return (
    // The keyboard and click handlers implement Escape, the focus trap and
    // the backdrop click of the modal dialog.
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`}
      aria-modal="true"
      onKeyDown={onKeyDown}
      onCancel={onCancel}
      onClick={onClick}>
      <div className={styles.dialogBody}>
        <h2 id={`${id}-title`} className={styles.dialogTitle}>
          Delete this post?
        </h2>
        <div id={`${id}-description`}>
          <p className={styles.postName}>
            <strong>&ldquo;{title}&rdquo;</strong>
            <br />
            <code>{repoPath}</code>
          </p>
          <p className={styles.dialogText}>
            This opens a pull request that deletes the file. The post stays on the site until the
            pull request is merged, and is removed from the site only then.
          </p>
        </div>

        {!repo ? (
          <div className="alert alert--warning" role="alert">
            The site configuration does not name the repository (customFields.repo), so the pull
            request cannot be opened from here.
          </div>
        ) : !usable ? (
          <GitHubTokenPrompt lead="No GitHub token is set on this device.">
            Then open this dialog again. Without a token, you can delete{' '}
            <code>{fileName}</code>{' '}
            <a href={githubDeleteUrl(repo, repoPath)} target="_blank" rel="noopener noreferrer">
              on GitHub
            </a>{' '}
            yourself, which also ends in a pull request.
          </GitHubTokenPrompt>
        ) : (
          <p className={styles.dialogNote}>
            The pull request goes to <code>{repo.branch}</code> of{' '}
            <code>
              {repo.owner}/{repo.name}
            </code>
            . If your token cannot push there, it comes from a fork in your account. Your token is
            sent only to api.github.com.
          </p>
        )}

        <PullRequestSteps steps={steps} />

        {outcome.kind === 'done' && (
          <div className={editorStyles.prSuccess} role="status" tabIndex={-1} ref={resultRef}>
            <p>
              Pull request{' '}
              <a className={editorStyles.prLink} href={outcome.url} target="_blank" rel="noopener noreferrer">
                #{outcome.number}
              </a>{' '}
              is open
              {outcome.forked ? ` from your fork (${outcome.head})` : ` from branch ${outcome.branch}`}. The
              post is removed from the site when it is merged.
            </p>
            <p>
              <a
                className="button button--sm button--success"
                href={outcome.url}
                target="_blank"
                rel="noopener noreferrer">
                View the pull request on GitHub
              </a>
            </p>
          </div>
        )}

        {outcome.kind === 'error' && (
          <div className={editorStyles.prError} role="alert" tabIndex={-1} ref={resultRef}>
            <p className={editorStyles.prErrorTitle}>The pull request was not opened.</p>
            <p>{outcome.message}</p>
          </div>
        )}

        <div className={styles.dialogActions}>
          <button
            ref={cancelRef}
            type="button"
            className="button button--secondary"
            onClick={requestClose}
            disabled={running}>
            {done ? 'Close' : 'Cancel'}
          </button>
          {usable && repo && !done && (
            <button
              type="button"
              className={clsx('button button--danger', running && 'button--disabled')}
              onClick={() => void confirm()}
              disabled={running}>
              {running
                ? 'Opening the pull request…'
                : outcome.kind === 'error'
                  ? 'Try again'
                  : 'Open a pull request to delete it'}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
}
