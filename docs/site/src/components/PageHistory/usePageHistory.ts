// Who changed a page, and when.
//
// Starts from the build-time git history of the page-meta plugin (always
// there, also in the server-rendered HTML). In the browser it then asks the
// public GitHub API for the file's commits (src/lib/github.mjs: cached in
// localStorage for an hour, the visitor's token if they set one) and shows the
// GitHub accounts behind them. On any error (rate limit, offline) it keeps the
// build-time data.
//
// Only people are credited. Commits by bots and AI tools (isAutomationAuthor
// in src/lib/github.mjs, with customFields.pageHistory.excludeAuthors) are
// left out of both sources: they never name anyone, they only date the last
// change of a page that nobody but automation has changed yet.

import {useEffect, useState} from 'react';
import {
  fetchPageCommits,
  preferGitHubHistory,
  type CommitSummary,
  type PageCommitsResult,
} from '@site/src/lib/github.mjs';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';
import {
  useExcludedAuthors,
  usePageMeta,
  useRepoConfig,
  type PageMetaEntry,
  type RepoConfig,
} from '../PageMetaBar/usePageMeta';
import {commitUrl, fileHistoryUrl, profileUrl} from '../PageMetaBar/format';

export type HistoryPerson = {
  key: string;
  /** GitHub login; null when only the git author name is known. */
  login: string | null;
  name: string;
  avatarUrl: string | null;
  profileUrl: string | null;
  commits: number;
  lastDate: string | null;
  /** Their commits to this file (or their latest one). */
  url: string | null;
};

export type HistoryLastChange = {
  login: string | null;
  name: string;
  avatarUrl: string | null;
  profileUrl: string | null;
  date: string | null;
  /** The commit. */
  url: string | null;
};

/** The latest change of a page changed only by automation: no name. */
export type HistoryAutomatedChange = {
  date: string | null;
  /** The commit. */
  url: string | null;
};

export type PageHistory = {
  /** Where the people and the last change come from. */
  source: 'github' | 'build' | 'none';
  /** True while GitHub is being asked. */
  loading: boolean;
  /** Why GitHub could not be used, when it could not. */
  error: {error: string; message: string} | null;
  /** The latest change by a person. */
  last: HistoryLastChange | null;
  /** People only (never bots or AI tools). */
  people: HistoryPerson[];
  /**
   * Set when the page has changes but none by a person: the latest of them,
   * without a name. Null otherwise.
   */
  automatedOnly: HistoryAutomatedChange | null;
  /** Commits by automation authors, not counted in `commits`. */
  automationCommits: number;
  /** Commits by people (GitHub returns at most 100 commits in all). */
  commits: number;
  /** True when GitHub's answer was cut at 100 commits. */
  commitsCapped: boolean;
  /** The file's history on GitHub. */
  historyUrl: string | null;
  /** Build-time data of the page (reading time and git history). */
  meta: PageMetaEntry | null;
};

const PER_PAGE = 100;
const SUCCESS_TTL_MS = 60 * 60 * 1000;
const FAILURE_TTL_MS = 5 * 60 * 1000;

// One request per file and session, shared by every component on the page
// (the meta bar and the history ask at the same time).
const requests = new Map<string, {promise: Promise<PageCommitsResult>; expires: number}>();

function browserCache(): Storage | null {
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

function loadCommits(
  repo: RepoConfig,
  path: string,
  token: string | null,
  excludeAuthors: readonly string[] | undefined,
): Promise<PageCommitsResult> {
  const exclude = excludeAuthors ? excludeAuthors.join(',') : 'default';
  const key = `${repo.owner}/${repo.name}:${path}:${token ? 'token' : 'anonymous'}:${exclude}`;
  const now = Date.now();
  const known = requests.get(key);
  if (known && known.expires > now) {
    return known.promise;
  }
  const entry = {
    promise: fetchPageCommits({
      owner: repo.owner,
      repo: repo.name,
      path,
      token,
      cache: browserCache(),
      excludeAuthors,
    }).catch(
      (): PageCommitsResult => ({
        ok: false,
        error: 'network error',
        message: 'Could not reach GitHub.',
      }),
    ),
    expires: now + SUCCESS_TTL_MS,
  };
  requests.set(key, entry);
  void entry.promise.then((result) => {
    if (!result.ok) {
      entry.expires = Date.now() + FAILURE_TTL_MS;
    }
  });
  return entry.promise;
}

function fromGitHub(summary: CommitSummary, repo: RepoConfig, path: string) {
  const historyUrl = fileHistoryUrl(repo, path);
  const people: HistoryPerson[] = summary.contributors.map((person) => ({
    key: person.login ? `login:${person.login.toLowerCase()}` : `name:${person.name.toLowerCase()}`,
    login: person.login,
    name: person.name,
    avatarUrl: person.avatarUrl,
    profileUrl: person.htmlUrl ?? (person.login ? profileUrl(person.login) : null),
    commits: person.commits,
    lastDate: person.lastDate,
    url: person.login ? `${historyUrl}?author=${encodeURIComponent(person.login)}` : person.lastCommitUrl,
  }));
  let last: HistoryLastChange | null = null;
  if (summary.last) {
    const lastLogin = summary.last.login;
    const match = lastLogin
      ? people.find((person) => person.login?.toLowerCase() === lastLogin.toLowerCase())
      : undefined;
    last = {
      login: lastLogin,
      name: summary.last.name,
      avatarUrl: match?.avatarUrl ?? null,
      profileUrl: match?.profileUrl ?? (lastLogin ? profileUrl(lastLogin) : null),
      date: summary.last.date,
      url: summary.last.htmlUrl ?? (summary.last.sha ? commitUrl(repo, summary.last.sha) : null),
    };
  }
  const commits = people.reduce((total, person) => total + person.commits, 0);
  const automation = summary.automation ?? null;
  const automationCommits = automation?.commits ?? 0;
  const automatedOnly: HistoryAutomatedChange | null =
    !last && automation
      ? {
          date: automation.lastDate,
          url: automation.htmlUrl ?? (automation.lastSha ? commitUrl(repo, automation.lastSha) : null),
        }
      : null;
  return {
    people,
    last,
    automatedOnly,
    automationCommits,
    commits,
    // GitHub's answer holds at most PER_PAGE commits, people's and automation's together.
    commitsCapped: commits + automationCommits >= PER_PAGE,
  };
}

function fromBuild(meta: PageMetaEntry | null, repo: RepoConfig | null) {
  const authors = meta?.authors ?? [];
  const people: HistoryPerson[] = authors.map((author) => ({
    key: `name:${author.name.toLowerCase()}`,
    login: null,
    name: author.name,
    avatarUrl: null,
    profileUrl: null,
    commits: author.commits,
    lastDate: author.lastDate,
    url: repo && author.lastSha ? commitUrl(repo, author.lastSha) : null,
  }));
  const last: HistoryLastChange | null =
    meta && (meta.lastAuthor || meta.lastDate)
      ? {
          login: null,
          name: meta.lastAuthor ?? 'Unknown',
          avatarUrl: null,
          profileUrl: null,
          date: meta.lastDate,
          url: repo && meta.lastSha ? commitUrl(repo, meta.lastSha) : null,
        }
      : null;
  const automation = meta?.automation ?? null;
  const automatedOnly: HistoryAutomatedChange | null =
    !last && automation
      ? {
          date: automation.lastDate,
          url: repo && automation.lastSha ? commitUrl(repo, automation.lastSha) : null,
        }
      : null;
  return {
    people,
    last,
    automatedOnly,
    automationCommits: automation?.commits ?? 0,
    commits: meta?.commits ?? 0,
    commitsCapped: false,
  };
}

type Live =
  | {state: 'idle'}
  | {state: 'loading'}
  | {state: 'done'; summary: CommitSummary}
  | {state: 'error'; error: string; message: string};

/**
 * The history of the file at `repoPath` (a repository path, from
 * repoPathForDoc(metadata.source)). Pass null when the page has no file.
 */
export function usePageHistory(repoPath: string | null): PageHistory {
  const meta = usePageMeta(repoPath);
  const repo = useRepoConfig();
  const {token, status} = useGitHubUser();
  const excludeAuthors = useExcludedAuthors();
  const excludeKey = excludeAuthors ? excludeAuthors.join(',') : null;
  const [live, setLive] = useState<Live>({state: 'idle'});

  // Wait for the saved token to be read, so a visitor with a token does not
  // spend an anonymous request first.
  const ready = status !== 'loading';

  useEffect(() => {
    if (!repoPath || !repo || !ready) {
      return undefined;
    }
    let active = true;
    setLive({state: 'loading'});
    loadCommits(repo, repoPath, token, excludeAuthors).then((result) => {
      if (!active) {
        return;
      }
      setLive(
        result.ok
          ? {state: 'done', summary: result.data}
          : {state: 'error', error: result.error, message: result.message},
      );
    });
    return () => {
      active = false;
    };
    // repo and excludeAuthors are rebuilt on every render from the site
    // config; their contents (fields, excludeKey) are the dependencies.
  }, [repoPath, repo?.owner, repo?.name, repo?.branch, token, ready, excludeKey]);

  const historyUrl = repo && repoPath ? fileHistoryUrl(repo, repoPath) : null;
  const build = fromBuild(meta, repo);
  const base = {
    loading: live.state === 'loading',
    error: live.state === 'error' ? {error: live.error, message: live.message} : null,
    historyUrl,
    meta,
  };

  // GitHub knows nothing of a file that is not on the default branch yet
  // (a new page in a preview build): keep the build-time data then. GitHub's
  // automation-only answer never hides people the build found (renames).
  if (
    live.state === 'done' &&
    repo &&
    repoPath &&
    preferGitHubHistory(live.summary, {peopleCount: build.people.length, hasLast: build.last !== null})
  ) {
    return {...base, source: 'github', ...fromGitHub(live.summary, repo, repoPath)};
  }
  const hasBuild = build.people.length > 0 || build.last !== null || build.automatedOnly !== null;
  return {...base, source: hasBuild ? 'build' : 'none', ...build};
}
