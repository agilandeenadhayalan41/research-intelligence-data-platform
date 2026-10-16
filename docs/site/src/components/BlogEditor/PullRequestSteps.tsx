// Progress of a pull request flow (src/lib/github.mjs): one line per GitHub
// request, shared by the editor's pull request panel and the blog's
// "Delete post" dialog.

import React, {type ReactNode} from 'react';

import styles from './styles.module.css';

export interface Step {
  label: string;
  state: 'running' | 'done' | 'failed';
}

export type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

/** Which flow the requests belong to: saving a post, or deleting one. */
export type PullRequestFlow = 'save' | 'delete';

/** Adds a step, or keeps the current one when a request repeats (fork polling). */
export function advanceStep(steps: Step[], label: string): Step[] {
  const last = steps[steps.length - 1];
  if (last && last.label === label && last.state === 'running') {
    return steps;
  }
  return [
    ...steps.map((step): Step => (step.state === 'running' ? {...step, state: 'done'} : step)),
    {label, state: 'running'},
  ];
}

/** Marks every step done (the flow succeeded). */
export function finishSteps(steps: Step[]): Step[] {
  return steps.map((step) => ({...step, state: 'done'}));
}

/** Marks the last step failed (the flow stopped there). */
export function failLastStep(steps: Step[]): Step[] {
  return steps.map((step, index) => (index === steps.length - 1 ? {...step, state: 'failed'} : step));
}

/**
 * A fetch that reports each request as a step (by its method and URL only)
 * before passing it to the browser's fetch.
 */
export function trackingFetch(
  onRequest: (label: string) => void,
  repo: {owner: string; name: string; branch: string},
  flow: PullRequestFlow,
): FetchLike {
  const upstreamPath = `/repos/${repo.owner}/${repo.name}`;
  return (url, init) => {
    onRequest(describeRequest(init?.method ?? 'GET', url, upstreamPath, repo.branch, flow));
    return globalThis.fetch(url, init);
  };
}

/** A readable label for a GitHub API request. Reads only the method and the URL. */
export function describeRequest(
  method: string,
  url: string,
  upstreamPath: string,
  base: string,
  flow: PullRequestFlow = 'save',
): string {
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
    // On the fork, the flow polls the branch until GitHub has copied the
    // repository.
    return pathname.toLowerCase().startsWith(`${upstreamPath.toLowerCase()}/`)
      ? `Reading branch ${base}`
      : 'Waiting for GitHub to create your fork';
  }
  if (pathname.endsWith('/git/refs')) {
    return 'Creating the branch';
  }
  if (pathname.includes('/contents/')) {
    if (verb === 'DELETE') {
      return 'Deleting the post on the branch';
    }
    if (verb === 'PUT') {
      return 'Committing the post';
    }
    return flow === 'delete' ? `Finding the post on ${base}` : 'Checking for an existing file';
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

/** The steps as a live list: done, failed or in progress. */
export default function PullRequestSteps({steps}: {steps: Step[]}): ReactNode {
  if (steps.length === 0) {
    return null;
  }
  return (
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
  );
}
