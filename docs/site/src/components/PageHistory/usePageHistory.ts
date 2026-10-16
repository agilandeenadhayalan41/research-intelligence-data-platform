// Who changed a page, and when.
//
// Starts from the build-time git history of the page-meta plugin (always
// there, also in the server-rendered HTML). In the browser it then asks the
// public GitHub API for the file's commits (src/lib/github.mjs: cached in
// localStorage for an hour, the visitor's token if they set one) and shows the
// GitHub accounts behind them. On any error (rate limit, offline) it keeps the
// build-time data.

import {useEffect, useState} from 'react';
import {fetchPageCommits, type CommitSummary, type PageCommitsResult} from '@site/src/lib/github.mjs';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';
import {usePageMeta, useRepoConfig, type PageMetaEntry, type RepoConfig} from '../PageMetaBar/usePageMeta';
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

export type PageHistory = {
  /** Where the people and the last change come from. */
  source: 'github' | 'build' | 'none';
  /** True while GitHub is being asked. */
  loading: boolean;
  /** Why GitHub could not be used, when it could not. */
  error: {error: string; message: string} | null;
  last: HistoryLastChange | null;
  people: HistoryPerson[];
  /** Commits counted (GitHub returns at most 100). */
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

function loadCommits(repo: RepoConfig, path: string, token: string | null): Promise<PageCommitsResult> {
  const key = `${repo.owner}/${repo.name}:${path}:${token ? 'token' : 'anonymous'}`;
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
  return {people, last, commits, commitsCapped: commits >= PER_PAGE};
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
  return {people, last, commits: meta?.commits ?? 0, commitsCapped: false};
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
    loadCommits(repo, repoPath, token).then((result) => {
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
    // repo is rebuilt on every render from the site config; its fields are the dependencies.
  }, [repoPath, repo?.owner, repo?.name, repo?.branch, token, ready]);

  const historyUrl = repo && repoPath ? fileHistoryUrl(repo, repoPath) : null;
  const build = fromBuild(meta, repo);
  const base = {
    loading: live.state === 'loading',
    error: live.state === 'error' ? {error: live.error, message: live.message} : null,
    historyUrl,
    meta,
  };

  // GitHub knows nothing of a file that is not on the default branch yet
  // (a new page in a preview build): keep the build-time data then.
  if (live.state === 'done' && repo && repoPath && live.summary.contributors.length > 0) {
    return {...base, source: 'github', ...fromGitHub(live.summary, repo, repoPath)};
  }
  const hasBuild = build.people.length > 0 || build.last !== null;
  return {...base, source: hasBuild ? 'build' : 'none', ...build};
}
