import React, {useEffect, useId, useRef, useState, type FormEvent, type ReactNode} from 'react';
import clsx from 'clsx';
import {GitHubError, openBlogPullRequest} from '@site/src/lib/github.mjs';

import styles from './styles.module.css';

export interface RepoConfig {
  owner: string;
  name: string;
  branch: string;
}

interface PullRequestPanelProps {
  token: string;
  repo: RepoConfig;
  /** Repository path of the post, e.g. 'docs/site/blog/2026-10-16-hello.md'. */
  path: string;
  content: string;
  postTitle: string;
  isNewPost: boolean;
  /** Reasons the pull request cannot be opened yet (shown, and the button is disabled). */
  blockers: string[];
  onClose: () => void;
}

interface Step {
  label: string;
  state: 'running' | 'done' | 'failed';
}

type Outcome =
  | {kind: 'idle'}
  | {kind: 'running'}
  | {kind: 'done'; url: string; number: number; branch: string; head: string; forked: boolean}
  | {kind: 'error'; message: string};

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

const DEFAULT_BODY = 'Blog post submitted from the docs site blog editor.';

/**
 * Opens a pull request with the post through the GitHub REST API (see
 * openBlogPullRequest in src/lib/github.mjs), and shows each step as it runs.
 * The token is only handed to openBlogPullRequest, which sends it to
 * api.github.com alone; nothing here logs or displays it.
 */
export default function PullRequestPanel({
  token,
  repo,
  path,
  content,
  postTitle,
  isNewPost,
  blockers,
  onClose,
}: PullRequestPanelProps): ReactNode {
  const id = useId();
  const defaultTitle = `${isNewPost ? 'Add' : 'Update'} blog post: ${postTitle}`;
  const [title, setTitle] = useState(defaultTitle);
  const [titleEdited, setTitleEdited] = useState(false);
  const [body, setBody] = useState(DEFAULT_BODY);
  const [steps, setSteps] = useState<Step[]>([]);
  const [outcome, setOutcome] = useState<Outcome>({kind: 'idle'});
  const mounted = useRef(true);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Follow the post title until the pull request title is edited by hand.
  useEffect(() => {
    if (!titleEdited) {
      setTitle(defaultTitle);
    }
  }, [defaultTitle, titleEdited]);

  useEffect(() => {
    if (outcome.kind === 'done' || outcome.kind === 'error') {
      resultRef.current?.focus();
    }
  }, [outcome.kind]);

  const running = outcome.kind === 'running';
  const done = outcome.kind === 'done';
  const blocked = blockers.length > 0;

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (running || done || blocked) {
      return;
    }
    setOutcome({kind: 'running'});
    setSteps([]);
    const upstreamPath = `/repos/${repo.owner}/${repo.name}`;
    const trackingFetch: FetchLike = (url, init) => {
      const label = describeRequest(init?.method ?? 'GET', url, upstreamPath, repo.branch);
      if (mounted.current) {
        setSteps((previous) => advance(previous, label));
      }
      return globalThis.fetch(url, init);
    };
    try {
      const commitTitle = title.trim() || defaultTitle;
      const result = await openBlogPullRequest({
        owner: repo.owner,
        repo: repo.name,
        base: repo.branch,
        token,
        path,
        content,
        message: commitTitle,
        title: commitTitle,
        body: body.trim() ? `${body.trim()}\n\nFile: \`${path}\`` : `File: \`${path}\``,
        fetchImpl: trackingFetch,
      });
      if (!mounted.current) {
        return;
      }
      setSteps((previous) => previous.map((step) => ({...step, state: 'done'})));
      setOutcome({kind: 'done', ...result});
    } catch (error) {
      if (!mounted.current) {
        return;
      }
      setSteps((previous) =>
        previous.map((step, index) =>
          index === previous.length - 1 ? {...step, state: 'failed'} : step,
        ),
      );
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
    <section className={styles.prPanel} aria-labelledby={`${id}-heading`}>
      <div className={styles.prPanelHeader}>
        <h2 id={`${id}-heading`} className={styles.prPanelTitle}>
          Open a pull request
        </h2>
        <button
          type="button"
          className="button button--sm button--secondary"
          onClick={onClose}
          disabled={running}>
          Close
        </button>
      </div>
      <p className={styles.prPanelIntro}>
        This commits <code>{path}</code> to a new branch and opens a pull request against{' '}
        <code>{repo.branch}</code> of{' '}
        <code>
          {repo.owner}/{repo.name}
        </code>
        . If your token cannot push to that repository, the editor forks it in your account and
        opens the pull request from the fork. Your token is sent only to api.github.com.
      </p>

      {blocked && (
        <div className={styles.prBlockers} role="status">
          <p>Before you can open the pull request:</p>
          <ul>
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      )}

      <form className={styles.prForm} onSubmit={(event) => void submit(event)}>
        <label className={styles.field} htmlFor={`${id}-title`}>
          <span className={styles.fieldLabel}>Pull request title</span>
          <input
            id={`${id}-title`}
            className={styles.input}
            type="text"
            value={title}
            maxLength={250}
            onChange={(event) => {
              setTitle(event.target.value);
              setTitleEdited(true);
            }}
            disabled={running || done}
            required
          />
        </label>
        <label className={styles.field} htmlFor={`${id}-body`}>
          <span className={styles.fieldLabel}>Description (optional)</span>
          <textarea
            id={`${id}-body`}
            className={clsx(styles.input, styles.prBody)}
            value={body}
            rows={3}
            maxLength={5000}
            onChange={(event) => setBody(event.target.value)}
            disabled={running || done}
          />
        </label>
        <div className={styles.prActions}>
          <button
            type="submit"
            className="button button--primary"
            disabled={running || done || blocked}>
            {running
              ? 'Opening the pull request…'
              : done
                ? 'Pull request opened'
                : 'Create pull request'}
          </button>
        </div>
      </form>

      {steps.length > 0 && (
        <ol className={styles.prSteps} aria-label="Progress" aria-live="polite">
          {steps.map((step, index) => (
            <li key={`${index}-${step.label}`} className={styles[`step_${step.state}`]}>
              <span className={styles.stepMarker} aria-hidden="true">
                {step.state === 'done' ? '✓' : step.state === 'failed' ? '✕' : '…'}
              </span>
              <span>
                {step.label}
                <span className="rid-sr-only">
                  {step.state === 'done' ? ' (done)' : step.state === 'failed' ? ' (failed)' : ' (in progress)'}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}

      {outcome.kind === 'done' && (
        <div className={styles.prSuccess} role="status" tabIndex={-1} ref={resultRef}>
          <p>
            Pull request{' '}
            <a className={styles.prLink} href={outcome.url} target="_blank" rel="noopener noreferrer">
              #{outcome.number}
            </a>{' '}
            is open
            {outcome.forked ? ` from your fork (${outcome.head})` : ` from branch ${outcome.branch}`}.
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
        <div className={styles.prError} role="alert" tabIndex={-1} ref={resultRef}>
          <p className={styles.prErrorTitle}>The pull request was not opened.</p>
          <p>{outcome.message}</p>
        </div>
      )}
    </section>
  );
}

/** Adds a step, or keeps the current one when a request repeats (fork polling). */
function advance(steps: Step[], label: string): Step[] {
  const last = steps[steps.length - 1];
  if (last && last.label === label && last.state === 'running') {
    return steps;
  }
  return [
    ...steps.map((step): Step => (step.state === 'running' ? {...step, state: 'done'} : step)),
    {label, state: 'running'},
  ];
}

/** A readable label for a GitHub API request. Reads only the method and the URL. */
function describeRequest(method: string, url: string, upstreamPath: string, base: string): string {
  let pathname = url;
  try {
    pathname = decodeURIComponent(new URL(url).pathname);
  } catch {
    // Keep the raw URL.
  }
  const verb = method.toUpperCase();
  if (pathname.endsWith('/forks')) {
    return 'Forking the repository';
  }
  if (pathname.endsWith('/merge-upstream')) {
    return `Syncing your fork with ${base}`;
  }
  if (pathname.includes('/git/ref/heads/')) {
    // On the fork, openBlogPullRequest polls the branch until GitHub has
    // copied the repository.
    return pathname.toLowerCase().startsWith(`${upstreamPath.toLowerCase()}/`)
      ? `Reading branch ${base}`
      : 'Waiting for GitHub to create your fork';
  }
  if (pathname.endsWith('/git/refs')) {
    return 'Creating the branch';
  }
  if (pathname.includes('/contents/')) {
    return verb === 'PUT' ? 'Committing the post' : 'Checking for an existing file';
  }
  if (pathname.endsWith('/pulls')) {
    return 'Opening the pull request';
  }
  if (/^\/repos\/[^/]+\/[^/]+$/.test(pathname)) {
    return pathname.toLowerCase() === upstreamPath.toLowerCase()
      ? 'Checking your access to the repository'
      : 'Waiting for GitHub to create your fork';
  }
  return 'Talking to GitHub';
}
