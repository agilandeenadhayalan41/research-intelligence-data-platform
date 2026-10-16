// Small formatting and URL helpers shared by the page components.

import type {RepoConfig} from './usePageMeta';

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const time = Date.parse(iso);
  return Number.isFinite(time) ? new Date(time) : null;
}

/**
 * A calendar day such as "Oct 16, 2026". Formatted in UTC so the server
 * render and the browser agree (no hydration mismatch).
 */
export function formatDay(iso: string | null | undefined, locale: string): string | null {
  const date = toDate(iso);
  if (!date) {
    return null;
  }
  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC',
    }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

/**
 * Day and time in the visitor's time zone, such as "Oct 16, 2026, 6:05 PM".
 * Only use it for content rendered in the browser (after mount).
 */
export function formatDateTime(iso: string | null | undefined, locale: string): string | null {
  const date = toDate(iso);
  if (!date) {
    return null;
  }
  try {
    return new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeStyle: 'short'}).format(date);
  } catch {
    return date.toISOString().replace('T', ' ').slice(0, 16);
  }
}

/** "1 commit", "3 commits". */
export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function encodePath(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

/** The file's history on GitHub (the commits that changed it). */
export function fileHistoryUrl(repo: RepoConfig, path: string): string {
  return `https://github.com/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.name)}/commits/${encodeURIComponent(repo.branch)}/${encodePath(path)}`;
}

/** One commit on GitHub. */
export function commitUrl(repo: RepoConfig, sha: string): string {
  return `https://github.com/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.name)}/commit/${encodeURIComponent(sha)}`;
}

/** A GitHub profile. */
export function profileUrl(login: string): string {
  return `https://github.com/${encodeURIComponent(login)}`;
}
