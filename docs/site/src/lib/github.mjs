// GitHub helpers for the docs site: pure functions plus thin wrappers around
// the public GitHub REST API (https://api.github.com).
//
// Plain ESM with no imports so `node --test` can load it. No browser global is
// read at import time (SSR-safe). Every network call takes an injected
// `fetchImpl` so tests never touch the network.
//
// Tokens: a token is only ever sent to api.github.com, in the Authorization
// header. It never appears in a returned or thrown message: every message is
// passed through redact() before it leaves this module.

export const GITHUB_API = 'https://api.github.com';

/** GitHub login: letters, digits and hyphens, 1-39 characters, not starting with a hyphen. */
export const LOGIN_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

export const COMMITS_CACHE_TTL_MS = 60 * 60 * 1000;
// v2: summaries leave out automation authors (v1 entries are ignored).
export const COMMITS_CACHE_PREFIX = 'rid-github:v2:commits:';

/** Folder of the blog posts in the repository, with a trailing slash. */
export const BLOG_POSTS_DIR = 'docs/site/blog/';

/**
 * Commit authors left out of the page history by default: AI coding tools and
 * bots. Matched against the GitHub login and the git author name, ignoring
 * case, a trailing "[bot]", and spaces, hyphens, underscores and dots (so the
 * git name "Cursor Agent" matches "cursoragent"). Set
 * customFields.pageHistory.excludeAuthors in docusaurus.config.ts to replace it.
 */
export const DEFAULT_EXCLUDED_AUTHORS = Object.freeze([
  'claude',
  'claude-code',
  'anthropic-claude',
  'copilot',
  'github-copilot',
  'copilot-swe-agent',
  'github-actions',
  'dependabot',
  'renovate',
  'vercel',
  'cursor',
  'cursoragent',
  'devin-ai-integration',
  'codex',
  'chatgpt-codex-connector',
  'gemini-code-assist',
  'sweep-ai',
  'coderabbitai',
]);

const DEFAULT_PR_BODY = 'Blog post submitted from the docs site blog editor.';
const DEFAULT_DELETE_PR_BODY = 'Deletes a blog post. Submitted from the docs site blog.';
const PR_TITLE_MAX_LENGTH = 200;
const READ_TIMEOUT_MS = 15000;
const SLUG_MAX_LENGTH = 60;
const OWNER_OR_REPO = /^[A-Za-z0-9_.-]{1,100}$/;
const TOKEN_PATTERN = /^[\x21-\x7E]+$/;
const SHA_PATTERN = /^[0-9a-f]{40}$/i;

/**
 * @typedef {(url: string, init?: {method?: string, headers?: Record<string, string>, body?: string, signal?: AbortSignal}) => Promise<Response>} FetchLike
 */

/**
 * Minimal Web Storage shape used as the commits cache.
 *
 * @typedef {object} CacheLike
 * @property {(key: string) => string | null} getItem
 * @property {(key: string, value: string) => void} setItem
 * @property {(key: string) => void} [removeItem]
 */

/**
 * @typedef {object} Contributor
 * @property {string | null} login GitHub login, or null when the commit is not linked to a GitHub user.
 * @property {string} name Git author name of their latest commit (login when missing).
 * @property {string | null} avatarUrl
 * @property {string | null} htmlUrl GitHub profile URL.
 * @property {number} commits Commits to this file.
 * @property {string | null} lastDate ISO date of their latest commit to this file.
 * @property {string | null} lastCommitUrl URL of that commit.
 */

/**
 * @typedef {object} LastChange
 * @property {string | null} login
 * @property {string} name
 * @property {string | null} date ISO date.
 * @property {string} sha
 * @property {string | null} htmlUrl Commit URL.
 */

/**
 * Commits by automation authors (bots and AI tools, see isAutomationAuthor):
 * counted, but never named.
 *
 * @typedef {object} AutomationSummary
 * @property {number} commits
 * @property {string | null} lastDate ISO date of the most recent one.
 * @property {string | null} lastSha
 * @property {string | null} htmlUrl Commit URL of the most recent one.
 */

/**
 * @typedef {object} CommitSummary
 * @property {Contributor[]} contributors People only. Most commits first; ties: most recent first.
 * @property {LastChange | null} last The most recent commit by a person.
 * @property {AutomationSummary} [automation] Present only when automation authors changed the file.
 */

/**
 * The parts of a commit author that isAutomationAuthor looks at.
 *
 * @typedef {object} CommitAuthor
 * @property {string | null} [login] GitHub login.
 * @property {string | null} [name] Git author name.
 * @property {string | null} [email] Git author email: only checked, never kept or shown.
 * @property {string | null} [type] GitHub account type: 'User', 'Bot', ...
 */

/**
 * @typedef {object} GitHubFailure
 * @property {false} ok
 * @property {string} error Short reason: 'not found', 'rate limited', 'bad credentials', 'network error', 'invalid login', 'not a user', 'invalid token', 'invalid request', 'invalid response' or 'http <status>'.
 * @property {string} message A sentence to show to the visitor.
 * @property {number} [status] HTTP status, when GitHub answered.
 * @property {string | null} [resetAt] When the rate limit resets (ISO), for 'rate limited'.
 */

/**
 * @typedef {object} PageCommitsSuccess
 * @property {true} ok
 * @property {CommitSummary} data
 * @property {boolean} cached True when served from the 1-hour cache.
 * @property {number} fetchedAt When GitHub was asked (ms since epoch).
 */

/** @typedef {PageCommitsSuccess | GitHubFailure} PageCommitsResult */

/**
 * @typedef {object} GitHubUser
 * @property {string} login
 * @property {string} name
 * @property {string} avatarUrl
 * @property {string} htmlUrl
 */

/** @typedef {{ok: true, data: GitHubUser} | GitHubFailure} FetchUserResult */

/**
 * @typedef {object} PullRequestResult
 * @property {string} url Pull request page.
 * @property {number} number
 * @property {string} branch Branch the post was committed to.
 * @property {string} head 'branch' or 'forkOwner:branch'.
 * @property {boolean} forked True when the post went through a fork.
 */

/** Error thrown by openBlogPullRequest and openBlogDeletePullRequest. The message never contains the token. */
export class GitHubError extends Error {
  /**
   * @param {string} message
   * @param {{status?: number | null, step?: string | null}} [details]
   */
  constructor(message, {status = null, step = null} = {}) {
    super(message);
    this.name = 'GitHubError';
    /** @type {number | null} HTTP status, when GitHub answered. */
    this.status = status;
    /** @type {string | null} The step that failed. */
    this.step = step;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers

/**
 * Maps a Docusaurus `metadata.source` to a path in the repository. The site
 * lives in `<docsDir>/site`, so '@site/' is that folder:
 * '@site/../architecture/x.md' -> 'docs/architecture/x.md',
 * '@site/blog/y.md' -> 'docs/site/blog/y.md'.
 * Returns null for anything else or a path that leaves the repository.
 *
 * @param {string} source
 * @param {{docsDir?: string}} [options]
 * @returns {string | null}
 */
export function repoPathForDoc(source, {docsDir = 'docs'} = {}) {
  if (typeof source !== 'string') {
    return null;
  }
  const value = source.trim().replace(/\\/g, '/');
  const prefix = '@site/';
  if (!value.startsWith(prefix)) {
    return null;
  }
  const segments = [...splitSegments(String(docsDir ?? '')), 'site'];
  for (const part of value.slice(prefix.length).split('/')) {
    if (part === '' || part === '.') {
      continue;
    }
    if (part === '..') {
      if (segments.length === 0) {
        return null;
      }
      segments.pop();
      continue;
    }
    segments.push(part);
  }
  return segments.length > 0 ? segments.join('/') : null;
}

/**
 * The exclude list as isAutomationAuthor compares it: each entry lower case,
 * without a trailing "[bot]", spaces, hyphens, underscores or dots; empty and
 * non-string entries dropped, duplicates removed. Anything but an array gives
 * the default list (DEFAULT_EXCLUDED_AUTHORS); an empty array excludes no
 * names (the bot rules still apply).
 *
 * @param {unknown} [list]
 * @returns {string[]}
 */
export function normalizeExcludeList(list) {
  const source = Array.isArray(list) ? list : DEFAULT_EXCLUDED_AUTHORS;
  /** @type {string[]} */
  const result = [];
  for (const entry of source) {
    const key = typeof entry === 'string' ? authorKey(entry) : '';
    if (key && !result.includes(key)) {
      result.push(key);
    }
  }
  return result;
}

/**
 * True when a commit author is automation (a bot or an AI tool), not a
 * person. Any one of these is enough:
 * - the GitHub account type is 'Bot';
 * - the login or the name ends with "[bot]";
 * - the login or the name is in `excludeList` (see normalizeExcludeList for
 *   how they are compared);
 * - the email ends with "@anthropic.com" (noreply@anthropic.com included) or
 *   is a "<name>[bot]@users.noreply.github.com" address.
 * The email is only checked, never returned.
 *
 * @param {CommitAuthor | null | undefined} author
 * @param {unknown} [excludeList] Default DEFAULT_EXCLUDED_AUTHORS.
 * @returns {boolean}
 */
export function isAutomationAuthor(author, excludeList = DEFAULT_EXCLUDED_AUTHORS) {
  if (!author || typeof author !== 'object') {
    return false;
  }
  if (author.type === 'Bot') {
    return true;
  }
  const excluded = normalizeExcludeList(excludeList);
  for (const value of [author.login, author.name]) {
    if (typeof value !== 'string' || !value.trim()) {
      continue;
    }
    if (BOT_SUFFIX.test(value.trim())) {
      return true;
    }
    const key = authorKey(value);
    if (key && excluded.includes(key)) {
      return true;
    }
  }
  const email = typeof author.email === 'string' ? author.email.trim().toLowerCase() : '';
  return email !== '' && (email.endsWith('@anthropic.com') || BOT_NOREPLY_EMAIL.test(email));
}

const BOT_SUFFIX = /\[bot\]$/i;
const BOT_NOREPLY_EMAIL = /\[bot\]@users\.noreply\.github\.com$/;

/**
 * @param {string} value
 * @returns {string}
 */
function authorKey(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(BOT_SUFFIX, '')
    .replace(/[\s._-]+/g, '');
}

/**
 * Summarises `GET /repos/{owner}/{repo}/commits?path=...` output.
 * Contributors are grouped by GitHub login (case-insensitive), or by git
 * author name when a commit is not linked to a GitHub user. Commits by
 * automation authors (isAutomationAuthor with `excludeAuthors`) are left out
 * of the contributors and of `last`; they are only counted, in `automation`.
 *
 * @param {unknown} commitsJson
 * @param {{excludeAuthors?: unknown}} [options] excludeAuthors: default DEFAULT_EXCLUDED_AUTHORS.
 * @returns {CommitSummary}
 */
export function summarizeCommits(commitsJson, {excludeAuthors} = {}) {
  const list = Array.isArray(commitsJson) ? commitsJson : [];
  const excluded = normalizeExcludeList(excludeAuthors);
  /** @type {Map<string, Contributor & {time: number, order: number}>} */
  const byKey = new Map();
  /** @type {LastChange | null} */
  let last = null;
  let lastTime = -Infinity;
  /** @type {(AutomationSummary & {time: number}) | null} */
  let automation = null;

  list.forEach((item) => {
    if (!item || typeof item !== 'object') {
      return;
    }
    const sha = typeof item.sha === 'string' ? item.sha : '';
    const commit = item.commit && typeof item.commit === 'object' ? item.commit : {};
    const gitAuthor = commit.author && typeof commit.author === 'object' ? commit.author : {};
    const gitCommitter =
      commit.committer && typeof commit.committer === 'object' ? commit.committer : {};
    const user = item.author && typeof item.author === 'object' ? item.author : null;
    const login = user && nonEmpty(user.login) ? user.login : null;
    const name = nonEmpty(gitAuthor.name) ? gitAuthor.name.trim() : (login ?? 'Unknown');
    const date = nonEmpty(gitAuthor.date)
      ? gitAuthor.date
      : nonEmpty(gitCommitter.date)
        ? gitCommitter.date
        : null;
    const parsed = date ? Date.parse(date) : NaN;
    const time = Number.isFinite(parsed) ? parsed : -Infinity;
    const commitUrl = httpsOrNull(item.html_url);

    if (!sha && !date && !login && !nonEmpty(gitAuthor.name)) {
      return; // not a commit object
    }

    if (
      isAutomationAuthor(
        {
          login,
          name: typeof gitAuthor.name === 'string' ? gitAuthor.name : null,
          email: typeof gitAuthor.email === 'string' ? gitAuthor.email : null,
          type: user && typeof user.type === 'string' ? user.type : null,
        },
        excluded,
      )
    ) {
      if (automation === null) {
        automation = {commits: 0, lastDate: null, lastSha: null, htmlUrl: null, time: -Infinity};
      }
      automation.commits += 1;
      if (automation.commits === 1 || time > automation.time) {
        automation.time = time;
        automation.lastDate = date;
        automation.lastSha = sha || null;
        automation.htmlUrl = commitUrl;
      }
      return;
    }

    const key = login ? `login:${login.toLowerCase()}` : `name:${name.toLowerCase()}`;
    let entry = byKey.get(key);
    if (!entry) {
      entry = {
        login,
        name,
        avatarUrl: null,
        htmlUrl: null,
        commits: 0,
        lastDate: null,
        lastCommitUrl: null,
        time: -Infinity,
        order: byKey.size,
      };
      byKey.set(key, entry);
    }
    entry.commits += 1;
    // GitHub lists newest first, so on equal dates the earlier item wins.
    if (entry.commits === 1 || time > entry.time) {
      entry.time = time;
      entry.name = name;
      entry.lastDate = date;
      entry.lastCommitUrl = commitUrl;
      if (user) {
        entry.login = login;
        entry.avatarUrl = httpsOrNull(user.avatar_url);
        entry.htmlUrl = httpsOrNull(user.html_url);
      }
    }

    if (last === null || time > lastTime) {
      lastTime = time;
      last = {login, name, date, sha, htmlUrl: commitUrl};
    }
  });

  const contributors = [...byKey.values()]
    .sort(
      (a, b) =>
        b.commits - a.commits || compareTimesDesc(a.time, b.time) || a.order - b.order,
    )
    .map(({time, order, ...contributor}) => contributor);
  if (automation === null) {
    return {contributors, last};
  }
  const {time, ...automationSummary} = /** @type {AutomationSummary & {time: number}} */ (automation);
  return {contributors, last, automation: automationSummary};
}

/**
 * Whether page history from GitHub (a summarizeCommits result) replaces the
 * build-time history. It does when GitHub names people. When it holds only
 * automation commits, it does only if the build names nobody either: GitHub's
 * `commits?path=` does not follow renames and returns at most one page, so
 * "automation only" from GitHub must not hide people that the build's
 * `git log --follow` found (a page written by people, then moved by a bot).
 *
 * @param {{contributors: readonly unknown[], automation?: {commits: number} | null}} summary
 * @param {{peopleCount: number, hasLast: boolean}} build
 * @returns {boolean}
 */
export function preferGitHubHistory(summary, build) {
  if (summary.contributors.length > 0) {
    return true;
  }
  return (summary.automation?.commits ?? 0) > 0 && build.peopleCount === 0 && !build.hasLast;
}

/**
 * URL-safe slug: lower case ASCII letters, digits and single hyphens, at most
 * 60 characters. Returns '' when nothing usable is left.
 *
 * @param {string} title
 * @returns {string}
 */
export function slugify(title) {
  if (typeof title !== 'string') {
    return '';
  }
  const slug = title
    .replace(/[ßæœøđłþð]/gi, (char) => TRANSLITERATION[char.toLowerCase()] ?? char)
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '')
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (slug.length <= SLUG_MAX_LENGTH) {
    return slug;
  }
  const cut = slug.slice(0, SLUG_MAX_LENGTH);
  const hyphen = cut.lastIndexOf('-');
  return (hyphen >= SLUG_MAX_LENGTH / 2 ? cut.slice(0, hyphen) : cut).replace(/-+$/, '');
}

/** @type {Record<string, string>} */
const TRANSLITERATION = {
  ß: 'ss',
  æ: 'ae',
  œ: 'oe',
  ø: 'o',
  đ: 'd',
  ł: 'l',
  þ: 'th',
  ð: 'd',
};

/**
 * @typedef {object} PostTemplateOptions
 * @property {string} [title] Default 'Untitled post'.
 * @property {'md' | 'mdx'} [format] Default 'md'.
 * @property {Date | string} [date] A Date (its local calendar day) or 'YYYY-MM-DD'; default today.
 */

/**
 * Source of a new blog post: front matter (title, description, tags, date),
 * an introduction, the truncate marker of the format, and a section.
 *
 * @param {PostTemplateOptions} [options]
 * @returns {string}
 */
export function newPostTemplate({title, format = 'md', date} = {}) {
  checkFormat(format);
  const cleanTitle = cleanPostTitle(title);
  const day = formatPostDate(date);
  const truncate = format === 'mdx' ? '{/* truncate */}' : '<!-- truncate -->';
  const formatNote =
    format === 'mdx'
      ? 'This post is MDX, so it can use JSX and the site components as well as Markdown.'
      : 'This post is Markdown (CommonMark): headings, lists, tables, links, images and code blocks.';
  return [
    '---',
    `title: ${JSON.stringify(cleanTitle)}`,
    `description: ${JSON.stringify('A one-sentence summary of the post.')}`,
    'tags: []',
    `date: ${day}`,
    '---',
    '',
    'Write a short introduction here. It is shown on the blog list page.',
    '',
    truncate,
    '',
    '## First section',
    '',
    formatNote,
    '',
  ].join('\n');
}

/**
 * File name for a new post: 'YYYY-MM-DD-<slug>.<format>' (slug 'post' when the
 * title has no usable characters).
 *
 * @param {PostTemplateOptions} [options]
 * @returns {string}
 */
export function postFileName({title, format = 'md', date} = {}) {
  checkFormat(format);
  return `${formatPostDate(date)}-${slugify(cleanPostTitle(title)) || 'post'}.${format}`;
}

/**
 * UTF-8 text to base64, as the GitHub contents API expects.
 *
 * @param {string} text
 * @returns {string}
 */
export function utf8ToBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

/**
 * Trims a login, drops a leading '@' or a github.com profile URL, and checks
 * it against LOGIN_PATTERN. Returns null when it is not a valid login.
 *
 * @param {unknown} login
 * @returns {string | null}
 */
export function normalizeLogin(login) {
  if (typeof login !== 'string') {
    return null;
  }
  const value = login
    .trim()
    .replace(/^(?:https?:\/\/)?(?:www\.)?github\.com\//i, '')
    .replace(/\/+$/, '')
    .replace(/^@/, '');
  return LOGIN_PATTERN.test(value) ? value : null;
}

/**
 * Cache key of a file's commits.
 *
 * @param {string} owner
 * @param {string} repo
 * @param {string} path
 * @returns {string}
 */
export function commitsCacheKey(owner, repo, path) {
  return `${COMMITS_CACHE_PREFIX}${owner}/${repo}:${path}`;
}

// ---------------------------------------------------------------------------
// Read-only API calls (return results, never throw)

/**
 * @typedef {object} FetchPageCommitsOptions
 * @property {string} owner
 * @property {string} repo
 * @property {string} path Repository path of the file.
 * @property {string | null} [token] Optional; raises the rate limit.
 * @property {FetchLike} [fetchImpl]
 * @property {CacheLike | null} [cache] Web Storage-like cache (1 hour per path); none: no caching.
 * @property {() => Date | number} [now]
 * @property {unknown} [excludeAuthors] Automation authors to leave out (see summarizeCommits); default DEFAULT_EXCLUDED_AUTHORS.
 */

/**
 * Who changed a file: `GET /repos/{owner}/{repo}/commits?path=<path>&per_page=100`,
 * summarised with summarizeCommits and cached for an hour. Only successful
 * answers are cached. A cached summary is used only with the same exclude
 * list it was made with.
 *
 * @param {FetchPageCommitsOptions} options
 * @returns {Promise<PageCommitsResult>}
 */
export async function fetchPageCommits({owner, repo, path, token, fetchImpl, cache, now, excludeAuthors}) {
  const filePath = typeof path === 'string' ? path.trim().replace(/^\/+/, '') : '';
  if (!nonEmpty(owner) || !nonEmpty(repo) || !filePath) {
    return failure('invalid request', 'owner, repo and path are required.');
  }
  const clock = () => toMillis(now ? now() : Date.now());
  const key = commitsCacheKey(owner, repo, filePath);
  const excluded = normalizeExcludeList(excludeAuthors);
  const exclude = excluded.join(',');

  const cached = readCache(cache, key, clock(), exclude);
  if (cached) {
    return {ok: true, data: cached.data, cached: true, fetchedAt: cached.fetchedAt};
  }

  const auth = cleanToken(token);
  if (auth === false) {
    return failure('invalid token', 'The GitHub token contains characters that are not allowed.');
  }
  const url =
    `${GITHUB_API}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}` +
    `/commits?path=${encodeURIComponent(filePath).replace(/%2F/g, '/')}&per_page=100`;
  const answer = await readRequest(fetchImpl, url, auth);
  if (!answer.ok) {
    return answer.failure;
  }
  const {res, json} = answer;
  if (!res.ok) {
    return readFailure(res, json, `GitHub has no repository ${owner}/${repo}, or it is private.`, auth);
  }
  if (!Array.isArray(json)) {
    return failure('invalid response', 'GitHub returned an unexpected answer.', res.status);
  }
  const data = summarizeCommits(json, {excludeAuthors: excluded});
  const fetchedAt = clock();
  writeCache(cache, key, {v: 2, fetchedAt, exclude, data});
  return {ok: true, data, cached: false, fetchedAt};
}

/**
 * Public profile of a GitHub user (`GET /users/<login>`). The login is
 * validated first (no request for an invalid one); '@login' and a
 * github.com profile URL are accepted.
 *
 * @param {string} login
 * @param {{fetchImpl?: FetchLike, token?: string | null}} [options]
 * @returns {Promise<FetchUserResult>}
 */
export async function fetchUser(login, {fetchImpl, token} = {}) {
  const name = normalizeLogin(login);
  if (!name) {
    return failure(
      'invalid login',
      'Enter a GitHub username: letters, numbers and hyphens, up to 39 characters.',
    );
  }
  const auth = cleanToken(token);
  if (auth === false) {
    return failure('invalid token', 'The GitHub token contains characters that are not allowed.');
  }
  const answer = await readRequest(fetchImpl, `${GITHUB_API}/users/${encodeURIComponent(name)}`, auth);
  if (!answer.ok) {
    return answer.failure;
  }
  const {res, json} = answer;
  if (!res.ok) {
    return readFailure(res, json, `There is no GitHub user named "${name}".`, auth);
  }
  if (!json || typeof json.login !== 'string' || !LOGIN_PATTERN.test(json.login)) {
    return failure('invalid response', 'GitHub returned an unexpected answer.', res.status);
  }
  if (json.type === 'Organization') {
    return failure('not a user', `"${json.login}" is a GitHub organization, not a user account.`, res.status);
  }
  const canonical = json.login;
  return {
    ok: true,
    data: {
      login: canonical,
      name: nonEmpty(json.name) ? json.name.trim() : canonical,
      avatarUrl: httpsOrNull(json.avatar_url) ?? `https://github.com/${canonical}.png`,
      htmlUrl: httpsOrNull(json.html_url) ?? `https://github.com/${canonical}`,
    },
  };
}

// ---------------------------------------------------------------------------
// Pull request flows (throw GitHubError)
//
// Both flows share the same steps (internal helpers below): read the
// repository and the base branch, make a work branch (in the repository with
// push access, else on the visitor's fork), commit one change to it, and open
// the pull request in the upstream repository.

/**
 * @typedef {object} OpenBlogPullRequestOptions
 * @property {string} owner Upstream repository owner.
 * @property {string} repo Upstream repository name.
 * @property {string} [base] Branch to merge into (default 'main').
 * @property {string} token GitHub token of the visitor.
 * @property {string} path Repository path of the post, e.g. 'docs/site/blog/2026-10-16-hello.md'.
 * @property {string} content Post source (UTF-8 text).
 * @property {string} [message] Commit message (default 'Add blog post <file>' / 'Update blog post <file>').
 * @property {string} [title] Pull request title (default: the commit message).
 * @property {string} [body] Pull request description.
 * @property {FetchLike} [fetchImpl]
 * @property {() => Date | number} [now] Clock for the branch name.
 * @property {(ms: number) => Promise<void>} [sleep] Wait between fork polls (for tests).
 * @property {number} [forkPollAttempts] GET attempts while the fork is being created (default 10).
 * @property {number} [forkPollIntervalMs] Wait between attempts (default 2000).
 */

/**
 * Commits a blog post to a new branch and opens a pull request against `base`.
 *
 * 1. GET the repository: can the token's user push?
 * 2. GET the sha of `base`.
 * 3a. Push access: create branch `blog/<slug>-<yyyymmddHHMMss>` in the repository.
 * 3b. Otherwise: POST a fork, poll its base branch until GitHub has copied the git data (bounded),
 *     sync it with merge-upstream (best effort: the branch is made from the
 *     upstream commit either way), and create the branch on the fork.
 * 4. GET the file on the branch to learn its sha (404: a new file).
 * 5. PUT the file (base64 UTF-8).
 * 6. POST the pull request (head 'branch' or 'forkOwner:branch').
 *
 * @param {OpenBlogPullRequestOptions} options
 * @returns {Promise<PullRequestResult>}
 */
export async function openBlogPullRequest({
  owner,
  repo,
  base = 'main',
  token,
  path,
  content,
  message,
  title,
  body,
  fetchImpl,
  now,
  sleep,
  forkPollAttempts = 10,
  forkPollIntervalMs = 2000,
}) {
  const auth = checkPullRequestInput({token, owner, repo, base});
  if (!isPostPath(path)) {
    throw new GitHubError(
      'The post path must be a relative repository path ending in .md or .mdx, without "..".',
    );
  }
  if (typeof content !== 'string') {
    throw new GitHubError('The post content must be text.');
  }
  const flow = startFlow({owner, repo, base, auth, fetchImpl, sleep, forkPollAttempts, forkPollIntervalMs});
  const fileName = baseNameOf(path);
  const branch = workBranchName('blog/', fileName, now);

  // 1-2. Push permission and the commit at the tip of the base branch.
  const {canPush, baseSha} = await readBase(flow);

  // 3. A branch to commit to: in the repository, or on the visitor's fork.
  const {target, head, forked} = await createWorkBranch(flow, {canPush, baseSha, branch});

  // 4. Existing file on the branch?
  const contentsUrl = `${target}/contents/${encodePath(path)}`;
  const existing = await flow.call('GET', `${contentsUrl}?ref=${encodeURIComponent(branch)}`, {
    step: 'Checking for an existing file',
    allow: [404],
  });
  /** @type {string | undefined} */
  let existingSha;
  if (existing.status !== 404) {
    existingSha = fileShaOf(existing.json, path, 'Checking for an existing file') ?? undefined;
  }

  // 5. Commit the post.
  const commitMessage = nonEmpty(message)
    ? message.trim()
    : `${existingSha ? 'Update' : 'Add'} blog post ${fileName}`;
  await flow.call('PUT', contentsUrl, {
    step: 'Saving the post',
    body: {
      message: commitMessage,
      content: utf8ToBase64(content),
      branch,
      ...(existingSha ? {sha: existingSha} : {}),
    },
  });

  // 6. Open the pull request in the upstream repository.
  const {url, number} = await openPull(flow, {
    title: nonEmpty(title) ? title.trim() : commitMessage,
    head,
    forked,
    body: typeof body === 'string' ? body : DEFAULT_PR_BODY,
  });
  return {url, number, branch, head, forked};
}

/**
 * @typedef {object} OpenBlogDeletePullRequestOptions
 * @property {string} owner Upstream repository owner.
 * @property {string} repo Upstream repository name.
 * @property {string} [base] Branch to merge into (default 'main').
 * @property {string} token GitHub token of the visitor.
 * @property {string} path Repository path of the post: a .md or .mdx file under docs/site/blog/.
 * @property {string} [title] Title of the post, for the pull request title (default: the file name).
 * @property {string} [body] Pull request description.
 * @property {FetchLike} [fetchImpl]
 * @property {() => Date | number} [now] Clock for the branch name.
 * @property {(ms: number) => Promise<void>} [sleep] Wait between fork polls (for tests).
 * @property {number} [forkPollAttempts] GET attempts while the fork is being created (default 10).
 * @property {number} [forkPollIntervalMs] Wait between attempts (default 2000).
 */

/**
 * Opens a pull request that deletes a blog post. The post stays on the site
 * until the pull request is merged.
 *
 * Only paths under docs/site/blog/ ending in .md or .mdx are accepted; any
 * other path throws before a request is made.
 *
 * 1. GET the repository: can the token's user push?
 * 2. GET the sha of `base`.
 * 3. GET the post at that commit, for its blob sha (404: 'post not found on <base>').
 * 4a. Push access: create branch `blog/delete-<slug>-<yyyymmddHHMMss>` in the repository.
 * 4b. Otherwise: fork, wait for the fork, sync it and create the branch on it
 *     (as in openBlogPullRequest).
 * 5. DELETE the file on the branch, with its sha.
 * 6. POST the pull request, titled 'Delete blog post: <title>'.
 *
 * The post is looked up before the branch is made, so a missing post leaves
 * no branch behind.
 *
 * @param {OpenBlogDeletePullRequestOptions} options
 * @returns {Promise<PullRequestResult>}
 */
export async function openBlogDeletePullRequest({
  owner,
  repo,
  base = 'main',
  token,
  path,
  title,
  body,
  fetchImpl,
  now,
  sleep,
  forkPollAttempts = 10,
  forkPollIntervalMs = 2000,
}) {
  const auth = checkPullRequestInput({token, owner, repo, base});
  if (!isBlogPostPath(path)) {
    throw new GitHubError(
      `Only blog posts can be deleted here: the path must be a file under ${BLOG_POSTS_DIR} ending in .md or .mdx, without "..".`,
    );
  }
  const flow = startFlow({owner, repo, base, auth, fetchImpl, sleep, forkPollAttempts, forkPollIntervalMs});
  const fileName = baseNameOf(path);
  const branch = workBranchName('blog/delete-', fileName, now);
  const contentsPath = `/contents/${encodePath(path)}`;

  // 1-2. Push permission and the commit at the tip of the base branch.
  const {canPush, baseSha} = await readBase(flow);

  // 3. The post at that commit: the branch is made from it, so the sha matches.
  const step = 'Finding the post';
  const file = await flow.call('GET', `${flow.upstream}${contentsPath}?ref=${encodeURIComponent(baseSha)}`, {
    step,
    notFound: `post not found on ${base} (${path}). It may have been deleted or renamed already.`,
  });
  const fileSha = fileShaOf(file.json, path, step);
  if (!fileSha) {
    throw new GitHubError(`GitHub returned no version of ${path} on ${base}.`, {step});
  }

  // 4. A branch for the change: in the repository, or on the visitor's fork.
  const {target, head, forked} = await createWorkBranch(flow, {canPush, baseSha, branch});

  // 5. Delete the post on the branch.
  await flow.call('DELETE', `${target}${contentsPath}`, {
    step: 'Deleting the post on the branch',
    body: {message: `Delete blog post ${fileName}`, sha: fileSha, branch},
  });

  // 6. Open the pull request in the upstream repository.
  const postTitle = cleanTitle(title) || fileName;
  const {url, number} = await openPull(flow, {
    title: `Delete blog post: ${postTitle}`,
    head,
    forked,
    body: typeof body === 'string' ? body : `${DEFAULT_DELETE_PR_BODY}\n\nFile: \`${path}\``,
  });
  return {url, number, branch, head, forked};
}

/**
 * True for a repository path of a blog post: a relative path under
 * docs/site/blog/ ending in .md or .mdx, with no empty, "." or ".." segment.
 *
 * @param {unknown} value
 * @returns {value is string}
 */
export function isBlogPostPath(value) {
  return (
    isPostPath(value) && value.startsWith(BLOG_POSTS_DIR) && value.length > BLOG_POSTS_DIR.length
  );
}

/**
 * @typedef {object} Flow
 * @property {(method: string, apiPath: string, options: CallOptions) => Promise<{status: number, json: any}>} call
 * @property {(ms: number) => Promise<void>} wait
 * @property {string} owner
 * @property {string} repo
 * @property {string} base
 * @property {string} upstream API path of the upstream repository, '/repos/<owner>/<repo>'.
 * @property {number} forkPollAttempts
 * @property {number} forkPollIntervalMs
 */

/**
 * Checks the token, the repository and the base branch. Throws GitHubError
 * before any request.
 *
 * @param {{token: unknown, owner: unknown, repo: unknown, base: unknown}} input
 * @returns {string} The token, trimmed.
 */
function checkPullRequestInput({token, owner, repo, base}) {
  const auth = cleanToken(token);
  if (auth === null) {
    throw new GitHubError(
      'A GitHub token is required to open a pull request. Add one in the account menu of the navigation bar.',
    );
  }
  if (auth === false) {
    throw new GitHubError(
      'The GitHub token contains spaces or characters that are not allowed. Paste it again.',
    );
  }
  if (!OWNER_OR_REPO.test(String(owner ?? '')) || !OWNER_OR_REPO.test(String(repo ?? ''))) {
    throw new GitHubError('The repository owner and name are missing or invalid.');
  }
  if (!isBranchName(base)) {
    throw new GitHubError(`"${String(base)}" is not a valid branch name.`);
  }
  return auth;
}

/**
 * @param {{owner: string, repo: string, base: string, auth: string, fetchImpl?: FetchLike, sleep?: (ms: number) => Promise<void>, forkPollAttempts: number, forkPollIntervalMs: number}} options
 * @returns {Flow}
 */
function startFlow({owner, repo, base, auth, fetchImpl, sleep, forkPollAttempts, forkPollIntervalMs}) {
  const doFetch = fetchImpl ?? defaultFetch();
  if (!doFetch) {
    throw new GitHubError('This browser cannot make network requests (fetch is not available).');
  }
  return {
    call: makeCaller(doFetch, auth),
    wait: sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
    owner,
    repo,
    base,
    upstream: `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
    forkPollAttempts: Number.isFinite(forkPollAttempts) ? Math.max(1, Math.floor(forkPollAttempts)) : 10,
    forkPollIntervalMs,
  };
}

/**
 * Steps 1-2: push permission, and the commit at the tip of the base branch.
 *
 * @param {Flow} flow
 * @returns {Promise<{canPush: boolean, baseSha: string}>}
 */
async function readBase({call, owner, repo, base, upstream}) {
  const repoInfo = await call('GET', upstream, {
    step: 'Reading the repository',
    notFound: `repository ${owner}/${repo} was not found, or the token cannot see it.`,
  });
  const canPush = Boolean(repoInfo.json?.permissions?.push);

  const baseRef = await call('GET', `${upstream}/git/ref/heads/${encodePath(base)}`, {
    step: `Reading branch "${base}"`,
    notFound: `branch "${base}" does not exist in ${owner}/${repo}.`,
  });
  const baseSha = baseRef.json?.object?.sha;
  if (typeof baseSha !== 'string' || !SHA_PATTERN.test(baseSha)) {
    throw new GitHubError(`GitHub returned no commit for branch "${base}".`, {
      step: `Reading branch "${base}"`,
    });
  }
  return {canPush, baseSha};
}

/**
 * Step 3: the work branch, made from `baseSha`. With push access it is
 * created in the repository; otherwise the repository is forked (or the fork
 * reused), the fork's base branch is polled until GitHub has copied the git
 * data (bounded), the fork is synced (best effort) and the branch is created
 * on the fork.
 *
 * @param {Flow} flow
 * @param {{canPush: boolean, baseSha: string, branch: string}} options
 * @returns {Promise<{target: string, head: string, forked: boolean}>}
 */
async function createWorkBranch(flow, {canPush, baseSha, branch}) {
  const {call, wait, base, upstream} = flow;
  if (canPush) {
    await call('POST', `${upstream}/git/refs`, {
      step: 'Creating the branch',
      body: {ref: `refs/heads/${branch}`, sha: baseSha},
    });
    return {target: upstream, head: branch, forked: false};
  }

  const fork = await call('POST', `${upstream}/forks`, {
    step: 'Forking the repository',
    body: {default_branch_only: true},
  });
  const forkOwner = fork.json?.owner?.login;
  const forkName = fork.json?.name;
  if (!OWNER_OR_REPO.test(String(forkOwner ?? '')) || !OWNER_OR_REPO.test(String(forkName ?? ''))) {
    throw new GitHubError('GitHub did not say which fork it created.', {
      step: 'Forking the repository',
    });
  }
  const target = `/repos/${encodeURIComponent(forkOwner)}/${encodeURIComponent(forkName)}`;

  let ready = false;
  for (let attempt = 1; attempt <= flow.forkPollAttempts; attempt += 1) {
    // GitHub creates forks asynchronously: the fork's repository answers
    // straight away, before its git data exists. Wait for the base branch
    // on the fork instead (409 "Git Repository is empty" or 404 while the
    // copy is still running).
    const probe = await call('GET', `${target}/git/ref/heads/${encodePath(base)}`, {
      step: 'Waiting for your fork',
      allow: [404, 409],
    });
    if (probe.status !== 404 && probe.status !== 409) {
      ready = true;
      break;
    }
    if (attempt < flow.forkPollAttempts) {
      await wait(flow.forkPollIntervalMs);
    }
  }
  if (!ready) {
    throw new GitHubError(
      `GitHub is still creating your fork ${forkOwner}/${forkName}. Wait a minute, then try again.`,
      {step: 'Waiting for your fork'},
    );
  }

  // Best effort: a fork whose base branch has diverged (409), cannot be
  // merged (422) or cannot be synced by this token (403, e.g. upstream
  // workflow changes) still gets a branch made from the upstream commit.
  await call('POST', `${target}/merge-upstream`, {
    step: 'Syncing your fork',
    body: {branch: base},
    allow: [403, 404, 409, 422],
  });
  await call('POST', `${target}/git/refs`, {
    step: 'Creating the branch on your fork',
    body: {ref: `refs/heads/${branch}`, sha: baseSha},
  });
  return {target, head: `${forkOwner}:${branch}`, forked: true};
}

/**
 * Last step: the pull request in the upstream repository.
 *
 * @param {Flow} flow
 * @param {{title: string, head: string, forked: boolean, body: string}} options
 * @returns {Promise<{url: string, number: number}>}
 */
async function openPull({call, base, upstream}, {title, head, forked, body}) {
  const pull = await call('POST', `${upstream}/pulls`, {
    step: 'Opening the pull request',
    body: {
      title,
      head,
      base,
      body,
      ...(forked ? {maintainer_can_modify: true} : {}),
    },
  });
  const url = httpsOrNull(pull.json?.html_url);
  const number = pull.json?.number;
  if (!url || typeof number !== 'number') {
    throw new GitHubError('GitHub did not return the new pull request.', {
      step: 'Opening the pull request',
    });
  }
  return {url, number};
}

/**
 * The blob sha from a contents API answer for a file. Throws when the path is
 * a folder; null when GitHub sent no sha.
 *
 * @param {any} json
 * @param {string} path
 * @param {string} step
 * @returns {string | null}
 */
function fileShaOf(json, path, step) {
  if (Array.isArray(json) || json?.type !== 'file') {
    throw new GitHubError(`${path} is a folder in the repository, not a file.`, {step});
  }
  return nonEmpty(json.sha) ? json.sha.trim() : null;
}

/**
 * `<prefix><slug>-<yyyymmddHHMMss>`, the slug from the file name ('post' when
 * it has no usable characters), the time in UTC.
 *
 * @param {string} prefix
 * @param {string} fileName
 * @param {(() => Date | number) | undefined} now
 * @returns {string}
 */
function workBranchName(prefix, fileName, now) {
  const slug = slugify(fileName.replace(/\.mdx?$/i, '')) || 'post';
  return `${prefix}${slug}-${compactTimestamp(toDate(now ? now() : new Date()))}`;
}

/**
 * @param {string} path
 * @returns {string}
 */
function baseNameOf(path) {
  return /** @type {string} */ (path.split('/').pop());
}

/**
 * A title on one line, at most PR_TITLE_MAX_LENGTH characters; '' when empty.
 *
 * @param {unknown} title
 * @returns {string}
 */
function cleanTitle(title) {
  const value = typeof title === 'string' ? title.replace(/\s+/g, ' ').trim() : '';
  return value.length > PR_TITLE_MAX_LENGTH ? `${value.slice(0, PR_TITLE_MAX_LENGTH - 1).trimEnd()}…` : value;
}

// ---------------------------------------------------------------------------
// HTTP plumbing

/** @returns {FetchLike | null} */
function defaultFetch() {
  // Called through globalThis: an unbound window.fetch throws "Illegal invocation".
  return typeof globalThis.fetch === 'function'
    ? (url, init) => globalThis.fetch(url, init)
    : null;
}

/**
 * @param {string | null} token
 * @param {boolean} [json]
 * @returns {Record<string, string>}
 */
function headersFor(token, json = false) {
  /** @type {Record<string, string>} */
  const headers = {Accept: 'application/vnd.github+json'};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (json) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

/**
 * Body as JSON: null when empty, undefined when it is not JSON.
 *
 * @param {Response} res
 * @returns {Promise<any>}
 */
async function readJson(res) {
  let text;
  try {
    text = await res.text();
  } catch {
    return undefined;
  }
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/**
 * GET for the read-only helpers: never throws.
 *
 * @param {FetchLike | undefined} fetchImpl
 * @param {string} url
 * @param {string | null} token
 * @returns {Promise<{ok: true, res: Response, json: any} | {ok: false, failure: GitHubFailure}>}
 */
async function readRequest(fetchImpl, url, token) {
  const doFetch = fetchImpl ?? defaultFetch();
  if (!doFetch) {
    return {ok: false, failure: failure('network error', 'This browser cannot reach GitHub.')};
  }
  /** @type {AbortSignal | undefined} */
  let signal;
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
    signal = AbortSignal.timeout(READ_TIMEOUT_MS);
  }
  try {
    const res = await doFetch(url, {method: 'GET', headers: headersFor(token), signal});
    return {ok: true, res, json: await readJson(res)};
  } catch {
    return {
      ok: false,
      failure: failure('network error', 'Could not reach GitHub (offline, blocked or timed out).'),
    };
  }
}

/**
 * @param {Response} res
 * @param {any} json
 * @param {string} notFoundMessage
 * @param {string | null} token
 * @returns {GitHubFailure}
 */
function readFailure(res, json, notFoundMessage, token) {
  const limit = rateLimit(res, json);
  if (limit.limited) {
    return {
      ok: false,
      error: 'rate limited',
      message: token
        ? "GitHub's API rate limit was reached. Try again later."
        : "GitHub's API rate limit was reached. Try again later, or add a GitHub token to raise the limit.",
      status: res.status,
      resetAt: limit.resetAt,
    };
  }
  if (res.status === 401) {
    return failure('bad credentials', 'GitHub rejected the token. Check it, or remove it, in the account menu.', 401);
  }
  if (res.status === 404) {
    return failure('not found', redact(notFoundMessage, token), 404);
  }
  const detail = redact(githubDetail(json), token);
  return failure(
    `http ${res.status}`,
    `GitHub answered with HTTP ${res.status}${detail ? ` (${detail})` : ''}.`,
    res.status,
  );
}

/**
 * @param {string} error
 * @param {string} message
 * @param {number} [status]
 * @returns {GitHubFailure}
 */
function failure(error, message, status) {
  return status === undefined ? {ok: false, error, message} : {ok: false, error, message, status};
}

/**
 * @param {Response} res
 * @param {any} json
 * @returns {{limited: boolean, resetAt: string | null}}
 */
function rateLimit(res, json) {
  const remaining = headerOf(res, 'x-ratelimit-remaining');
  const reset = headerOf(res, 'x-ratelimit-reset');
  const text = json && typeof json.message === 'string' ? json.message : '';
  const limited =
    res.status === 429 || (res.status === 403 && (remaining === '0' || /rate limit/i.test(text)));
  const resetAt = reset && /^\d+$/.test(reset) ? new Date(Number(reset) * 1000).toISOString() : null;
  return {limited, resetAt};
}

/**
 * @param {Response} res
 * @param {string} name
 * @returns {string | null}
 */
function headerOf(res, name) {
  try {
    return res.headers && typeof res.headers.get === 'function' ? res.headers.get(name) : null;
  } catch {
    return null;
  }
}

/**
 * GitHub's error message plus its validation errors, shortened.
 *
 * @param {any} json
 * @returns {string}
 */
function githubDetail(json) {
  if (!json || typeof json !== 'object') {
    return '';
  }
  /** @type {string[]} */
  const parts = [];
  if (nonEmpty(json.message)) {
    parts.push(json.message.trim());
  }
  if (Array.isArray(json.errors)) {
    for (const item of json.errors) {
      const text = typeof item === 'string' ? item : item && (item.message || item.code);
      if (nonEmpty(text)) {
        parts.push(text.trim());
      }
    }
  }
  const joined = parts.join('; ');
  return joined.length > 300 ? `${joined.slice(0, 297)}...` : joined;
}

/**
 * @typedef {object} CallOptions
 * @property {string} step What the call does, used in error messages.
 * @property {unknown} [body] JSON body.
 * @property {string} [notFound] Message for a 404 (after "<step> failed: ").
 * @property {number[]} [allow] Non-2xx statuses returned instead of thrown.
 */

/**
 * @param {FetchLike} doFetch
 * @param {string} token
 * @returns {(method: string, apiPath: string, options: CallOptions) => Promise<{status: number, json: any}>}
 */
function makeCaller(doFetch, token) {
  return async (method, apiPath, {step, body, notFound, allow = []}) => {
    const hasBody = body !== undefined;
    let res;
    try {
      res = await doFetch(`${GITHUB_API}${apiPath}`, {
        method,
        headers: headersFor(token, hasBody),
        ...(hasBody ? {body: JSON.stringify(body)} : {}),
      });
    } catch (error) {
      const reason = error instanceof Error && error.message ? ` (${error.message})` : '';
      throw new GitHubError(
        redact(`${step} failed: could not reach GitHub${reason}. Check your connection and try again.`, token),
        {step},
      );
    }
    const json = await readJson(res);
    if (res.ok || allow.includes(res.status)) {
      return {status: res.status, json};
    }
    throw new GitHubError(redact(describeFailure(step, res, json, notFound), token), {
      status: res.status,
      step,
    });
  };
}

/**
 * @param {string} step
 * @param {Response} res
 * @param {any} json
 * @param {string | undefined} notFound
 * @returns {string}
 */
function describeFailure(step, res, json, notFound) {
  const detail = githubDetail(json);
  const limit = rateLimit(res, json);
  if (limit.limited) {
    return `${step} failed: GitHub's API rate limit was reached${
      limit.resetAt ? ` (it resets at ${limit.resetAt})` : ''
    }. Try again later.`;
  }
  switch (res.status) {
    case 401:
      return `${step} failed: GitHub rejected the token (401 Bad credentials). Check that it is correct and has not expired.`;
    case 403:
      return `${step} failed: the token is not allowed to do this (403${detail ? `: ${detail}` : ''}). It needs permission to write repository contents and open pull requests.`;
    case 404:
      return `${step} failed: ${notFound ?? `not found (404${detail ? `: ${detail}` : ''}). The token may not have access to this repository.`}`;
    default:
      return `${step} failed: GitHub answered HTTP ${res.status}${detail ? ` (${detail})` : ''}.`;
  }
}

/**
 * Removes every occurrence of the token from a message.
 *
 * @param {string} text
 * @param {string | null} token
 * @returns {string}
 */
function redact(text, token) {
  return token ? text.split(token).join('[token]') : text;
}

// ---------------------------------------------------------------------------
// Cache

/**
 * @param {CacheLike | null | undefined} cache
 * @param {string} key
 * @param {number} nowMs
 * @param {string} exclude The normalised exclude list, comma-separated.
 * @returns {{data: CommitSummary, fetchedAt: number} | null}
 */
function readCache(cache, key, nowMs, exclude) {
  if (!cache || typeof cache.getItem !== 'function') {
    return null;
  }
  try {
    const raw = cache.getItem(key);
    if (!raw) {
      return null;
    }
    const entry = JSON.parse(raw);
    const fetchedAt = entry && entry.fetchedAt;
    const data = entry && entry.data;
    const fresh =
      typeof fetchedAt === 'number' &&
      fetchedAt <= nowMs &&
      nowMs - fetchedAt < COMMITS_CACHE_TTL_MS;
    if (!fresh || entry.exclude !== exclude || !data || !Array.isArray(data.contributors)) {
      return null;
    }
    /** @type {CommitSummary} */
    const summary = {contributors: data.contributors, last: data.last ?? null};
    const automation = data.automation;
    if (automation && typeof automation === 'object' && typeof automation.commits === 'number') {
      summary.automation = automation;
    }
    return {data: summary, fetchedAt};
  } catch {
    return null;
  }
}

/**
 * @param {CacheLike | null | undefined} cache
 * @param {string} key
 * @param {unknown} entry
 */
function writeCache(cache, key, entry) {
  if (!cache || typeof cache.setItem !== 'function') {
    return;
  }
  try {
    cache.setItem(key, JSON.stringify(entry));
  } catch {
    // Full or blocked storage: the next visit simply asks GitHub again.
  }
}

// ---------------------------------------------------------------------------
// Small helpers

/**
 * @param {unknown} value
 * @returns {value is string}
 */
function nonEmpty(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

/**
 * @param {unknown} value
 * @returns {string | null}
 */
function httpsOrNull(value) {
  return typeof value === 'string' && /^https:\/\//i.test(value) ? value : null;
}

/**
 * null: no token; false: unusable token; otherwise the trimmed token.
 *
 * @param {unknown} token
 * @returns {string | null | false}
 */
function cleanToken(token) {
  if (token === undefined || token === null) {
    return null;
  }
  if (typeof token !== 'string') {
    return false;
  }
  const value = token.trim();
  if (!value) {
    return null;
  }
  return TOKEN_PATTERN.test(value) ? value : false;
}

/**
 * @param {string} value
 * @returns {string[]}
 */
function splitSegments(value) {
  return value
    .replace(/\\/g, '/')
    .split('/')
    .filter((part) => part !== '' && part !== '.');
}

/**
 * Each path segment URL-encoded, slashes kept.
 *
 * @param {string} value
 * @returns {string}
 */
function encodePath(value) {
  return value.split('/').map(encodeURIComponent).join('/');
}

/**
 * @param {unknown} value
 * @returns {value is string}
 */
function isBranchName(value) {
  return (
    typeof value === 'string' &&
    /^[A-Za-z0-9._/-]{1,200}$/.test(value) &&
    !value.startsWith('/') &&
    !value.endsWith('/') &&
    !value.includes('..') &&
    !value.includes('//')
  );
}

/**
 * @param {unknown} value
 * @returns {value is string}
 */
function isPostPath(value) {
  if (typeof value !== 'string' || !/\.mdx?$/i.test(value) || value.length > 500) {
    return false;
  }
  if (value.startsWith('/') || value.includes('\\') || /[\u0000-\u001f\u007f]/.test(value)) {
    return false;
  }
  return value.split('/').every((part) => part !== '' && part !== '.' && part !== '..');
}

/**
 * @param {'md' | 'mdx' | string} format
 */
function checkFormat(format) {
  if (format !== 'md' && format !== 'mdx') {
    throw new Error("format must be 'md' or 'mdx'");
  }
}

/**
 * @param {unknown} title
 * @returns {string}
 */
function cleanPostTitle(title) {
  const value = typeof title === 'string' ? title.replace(/\s+/g, ' ').trim() : '';
  return value || 'Untitled post';
}

/**
 * 'YYYY-MM-DD' from a 'YYYY-MM-DD' string or a Date's local calendar day.
 *
 * @param {Date | string | undefined} date
 * @returns {string}
 */
function formatPostDate(date) {
  if (typeof date === 'string') {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date.trim());
    if (match) {
      const [year, month, dayOfMonth] = [Number(match[1]), Number(match[2]), Number(match[3])];
      const check = new Date(Date.UTC(year, month - 1, dayOfMonth));
      if (check.getUTCMonth() === month - 1 && check.getUTCDate() === dayOfMonth) {
        return `${match[1]}-${match[2]}-${match[3]}`;
      }
    }
    throw new Error("date must be a Date or a 'YYYY-MM-DD' string");
  }
  const day = date === undefined ? new Date() : date;
  if (!(day instanceof Date) || Number.isNaN(day.getTime())) {
    throw new Error("date must be a Date or a 'YYYY-MM-DD' string");
  }
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

/**
 * yyyymmddHHMMss in UTC.
 *
 * @param {Date} date
 * @returns {string}
 */
function compactTimestamp(date) {
  return (
    `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}` +
    `${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}`
  );
}

/** @param {number} value */
function pad(value) {
  return String(value).padStart(2, '0');
}

/**
 * @param {Date | number} value
 * @returns {Date}
 */
function toDate(value) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

/**
 * @param {Date | number} value
 * @returns {number}
 */
function toMillis(value) {
  return value instanceof Date ? value.getTime() : Number(value);
}

/**
 * Newer first; -Infinity (no date) last.
 *
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
function compareTimesDesc(a, b) {
  return a === b ? 0 : b > a ? 1 : -1;
}
