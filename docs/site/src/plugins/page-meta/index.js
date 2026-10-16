// @ts-check
// Build-time page metadata for every doc and blog post.
//
// For each Markdown/MDX file under docs/ (except this site's folder,
// docs/site/) and under docs/site/blog/, the plugin computes the reading time
// (src/lib/readingTime.mjs) and reads the file's git history. It publishes the
// result with setGlobalData, so components read it with
// usePluginData('page-meta') and it is in the server-rendered HTML:
//
//   {
//     version: 1,
//     pages: {
//       'docs/architecture/roadmap.md': {
//         readingMinutes, words,          // null when the file cannot be read
//         lastAuthor, lastDate, lastSha,  // latest commit by a person; null when git has no answer
//         commits,                        // commits by people; null when git failed
//         authors: [{name, commits, lastDate, lastSha}] | null,
//         automation?: {commits, lastDate, lastSha}, // only when bots or AI tools changed the file
//       },
//     },
//     blogSources: [{repoPath, file, title, format, content}],
//   }
//
// Keys are repository paths, the same as repoPathForDoc(metadata.source) in
// src/lib/github.mjs gives in the browser. blogSources embeds the raw source of
// each blog post for the editor at /blog/editor.
//
// Only people are credited: commits by automation authors (bots and AI tools,
// isAutomationAuthor in src/lib/github.mjs with
// customFields.pageHistory.excludeAuthors) are left out of the authors, the
// last change and the commit count, and only counted in `automation`, without
// a name. The author email is read from git for that check only; it is never
// published.
//
// Git never fails the build: any error (no git, not a repository, a timeout)
// leaves the git fields null. In a shallow clone (Vercel builds from one) the
// boundary commits look as if they added every file, so they are ignored: a
// page whose history lies behind the boundary has no build-time update (its
// git fields are null) until the page history component loads the GitHub data
// in the browser.
//
// This file runs in Node.js at build time (CommonJS, loaded by Docusaurus).

'use strict';

const fs = require('fs');
const path = require('path');
const {execFileSync} = require('child_process');

const PLUGIN_NAME = 'page-meta';
const DATA_VERSION = 1;
const MARKDOWN_FILE = /\.mdx?$/i;
const GIT_TIMEOUT_MS = 15000;
const GIT_MAX_BUFFER = 16 * 1024 * 1024;
const FIELD_SEPARATOR = '\x1f';
// sha, author name, author date, author email. The email comes last so that
// three-field lines (no email) still parse.
const GIT_LOG_FORMAT = '%H%x1f%an%x1f%aI%x1f%ae';
const SHA = /^[0-9a-f]{40}$/i;

/**
 * @typedef {object} AuthorSummary
 * @property {string} name Git author name.
 * @property {number} commits Commits by this author to the file.
 * @property {string | null} lastDate ISO date of their latest commit to it.
 * @property {string | null} lastSha That commit.
 */

/**
 * Commits by automation authors: counted, never named.
 *
 * @typedef {object} AutomationSummary
 * @property {number} commits
 * @property {string | null} lastDate ISO date of the most recent one.
 * @property {string | null} lastSha
 */

/**
 * @typedef {object} PageMeta
 * @property {number | null} readingMinutes
 * @property {number | null} words
 * @property {string | null} lastAuthor Author of the latest commit by a person.
 * @property {string | null} lastDate ISO 8601.
 * @property {string | null} lastSha
 * @property {number | null} commits Commits by people.
 * @property {AuthorSummary[] | null} authors People only. Most commits first; ties: most recent first.
 * @property {AutomationSummary} [automation] Only when automation authors changed the file.
 */

/**
 * @typedef {object} GitLogOptions
 * @property {(author: {name: string, email: string}) => boolean} [isExcluded] True for an automation author; default: nobody.
 */

/**
 * @typedef {object} BlogSource
 * @property {string} repoPath e.g. 'docs/site/blog/2026-10-16-welcome.md'.
 * @property {string} file Path inside the blog folder, e.g. '2026-10-16-welcome.md'.
 * @property {string} title Front matter title, else the first heading, else the file name.
 * @property {'md' | 'mdx'} format
 * @property {string} content Raw source, front matter included.
 */

/**
 * @typedef {object} PageMetaData
 * @property {number} version
 * @property {Record<string, PageMeta>} pages
 * @property {BlogSource[]} blogSources
 */

/**
 * @param {import('@docusaurus/types').LoadContext} context
 * @returns {Promise<import('@docusaurus/types').Plugin<PageMetaData>>}
 */
module.exports = async function pageMetaPlugin(context) {
  const {readingTime} = await import('../../lib/readingTime.mjs');
  const {isAutomationAuthor, normalizeExcludeList} = await import('../../lib/github.mjs');
  const customFields = /** @type {Record<string, any> | undefined} */ (context.siteConfig.customFields);
  const excludeAuthors = normalizeExcludeList(customFields?.pageHistory?.excludeAuthors);
  /** @type {GitLogOptions} */
  const gitLogOptions = {
    isExcluded: (author) => isAutomationAuthor(author, excludeAuthors),
  };

  const siteDir = path.resolve(context.siteDir);
  // The site lives in <repo>/docs/site, as repoPathForDoc assumes.
  const docsDir = path.resolve(siteDir, '..');
  const repoRoot = path.resolve(docsDir, '..');
  const blogDir = path.join(siteDir, 'blog');

  /**
   * @param {string} file Absolute path.
   * @returns {string} Repository path with forward slashes.
   */
  const toRepoPath = (file) => path.relative(repoRoot, file).split(path.sep).join('/');

  let gitWarningShown = false;
  const boundary = shallowBoundary(repoRoot);

  /**
   * `git log --follow` for one file. Never throws.
   *
   * @param {string} repoPath
   * @returns {GitSummary}
   */
  function gitHistory(repoPath) {
    try {
      const output = execFileSync(
        'git',
        ['log', '--follow', `--format=${GIT_LOG_FORMAT}`, '--', repoPath],
        {
          cwd: repoRoot,
          encoding: 'utf8',
          timeout: GIT_TIMEOUT_MS,
          maxBuffer: GIT_MAX_BUFFER,
          stdio: ['ignore', 'pipe', 'ignore'],
          windowsHide: true,
        },
      );
      return summarizeGitLog(output, boundary, gitLogOptions);
    } catch (error) {
      if (!gitWarningShown) {
        gitWarningShown = true;
        const reason = error instanceof Error ? error.message.split('\n')[0] : String(error);
        console.warn(
          `[${PLUGIN_NAME}] git history is unavailable (${reason}); pages will show no build-time history.`,
        );
      }
      return {lastAuthor: null, lastDate: null, lastSha: null, commits: null, authors: null};
    }
  }

  /**
   * @param {string} file Absolute path.
   * @returns {Promise<{meta: PageMeta, content: string | null}>}
   */
  async function describe(file) {
    /** @type {string | null} */
    let content = null;
    let readingMinutes = null;
    let words = null;
    try {
      content = await fs.promises.readFile(file, 'utf8');
      const estimate = readingTime(content);
      readingMinutes = estimate.minutes;
      words = estimate.words;
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      console.warn(`[${PLUGIN_NAME}] could not read ${toRepoPath(file)}: ${reason}`);
    }
    return {
      meta: {readingMinutes, words, ...gitHistory(toRepoPath(file))},
      content,
    };
  }

  return {
    name: PLUGIN_NAME,

    getPathsToWatch() {
      // Narrow globs: watching docs/** would also watch this site's
      // node_modules.
      const globs = [`${posix(docsDir)}/*.{md,mdx}`, `${posix(blogDir)}/**/*.{md,mdx}`];
      for (const entry of safeReadDir(docsDir)) {
        if (entry.isDirectory() && !skipName(entry.name) && path.join(docsDir, entry.name) !== siteDir) {
          globs.push(`${posix(path.join(docsDir, entry.name))}/**/*.{md,mdx}`);
        }
      }
      return globs;
    },

    async loadContent() {
      const docFiles = await listMarkdown(docsDir, (dir) => dir === siteDir);
      const blogFiles = await listMarkdown(blogDir, () => false);

      /** @type {Record<string, PageMeta>} */
      const pages = {};
      for (const file of docFiles) {
        pages[toRepoPath(file)] = (await describe(file)).meta;
      }

      /** @type {BlogSource[]} */
      const blogSources = [];
      for (const file of blogFiles) {
        const {meta, content} = await describe(file);
        const repoPath = toRepoPath(file);
        pages[repoPath] = meta;
        if (content === null) {
          continue;
        }
        const relative = path.relative(blogDir, file).split(path.sep).join('/');
        blogSources.push({
          repoPath,
          file: relative,
          title: postTitle(content, relative),
          format: /\.mdx$/i.test(file) ? 'mdx' : 'md',
          content,
        });
      }
      // Date-prefixed file names: newest first.
      blogSources.sort((a, b) => (a.file < b.file ? 1 : a.file > b.file ? -1 : 0));

      return {version: DATA_VERSION, pages, blogSources};
    },

    async contentLoaded({content, actions}) {
      actions.setGlobalData(content);
    },

    // The docs plugin reads docs/ in place (path '..'), so its MDX webpack
    // rule includes the whole docs/ folder, this site included. Without this,
    // the home page (src/pages/*.mdx) and blog posts would also go through the
    // docs MDX loader, be compiled twice, and fail. Exclude the site folder
    // from every rule that includes docs/; the docs plugin itself excludes
    // site/** from its content, so it loses nothing.
    configureWebpack(config) {
      const docsInclude = docsDir + path.sep;
      const siteExclude = siteDir + path.sep;
      for (const rule of config.module?.rules ?? []) {
        if (
          rule &&
          typeof rule === 'object' &&
          Array.isArray(rule.include) &&
          rule.include.includes(docsInclude)
        ) {
          const current = rule.exclude == null ? [] : [rule.exclude].flat();
          if (!current.includes(siteExclude)) {
            rule.exclude = [...current, siteExclude];
          }
        }
      }
      return {};
    },
  };
};

/**
 * The boundary commits of a shallow clone (from `.git/shallow`), lowercase.
 * Empty for a full clone or when git has no answer. Never throws.
 *
 * @param {string} repoRoot
 * @returns {Set<string>}
 */
function shallowBoundary(repoRoot) {
  /** @type {import('child_process').ExecFileSyncOptionsWithStringEncoding} */
  const options = {
    cwd: repoRoot,
    encoding: 'utf8',
    timeout: GIT_TIMEOUT_MS,
    stdio: ['ignore', 'pipe', 'ignore'],
    windowsHide: true,
  };
  try {
    const shallow = execFileSync('git', ['rev-parse', '--is-shallow-repository'], options).trim();
    if (shallow !== 'true') {
      return new Set();
    }
    const file = execFileSync('git', ['rev-parse', '--git-path', 'shallow'], options).trim();
    return new Set(
      fs
        .readFileSync(path.resolve(repoRoot, file), 'utf8')
        .split('\n')
        .map((line) => line.trim().toLowerCase())
        .filter((line) => SHA.test(line)),
    );
  } catch {
    return new Set();
  }
}

/** @typedef {Pick<PageMeta, 'lastAuthor' | 'lastDate' | 'lastSha' | 'commits' | 'authors' | 'automation'>} GitSummary */

/**
 * Parses `git log --format=%H%x1f%an%x1f%aI%x1f%ae` output (newest first;
 * the email field is optional).
 *
 * Commits in `boundary` (the grafted roots of a shallow clone, which seem to
 * add every file) are skipped. Commits whose author `isExcluded` (bots and AI
 * tools) are left out of the authors, the last change and the count; they
 * are only counted in `automation`, without a name or email.
 *
 * A file with no commit by a person but some boundary commits has an unknown
 * history (null fields), not an empty one: people may have changed it before
 * the boundary.
 *
 * @param {string} output
 * @param {Set<string>} [boundary]
 * @param {GitLogOptions} [options]
 * @returns {GitSummary}
 */
function summarizeGitLog(output, boundary = new Set(), {isExcluded} = {}) {
  /** @type {Map<string, AuthorSummary & {time: number, order: number}>} */
  const byName = new Map();
  /** @type {{name: string, date: string | null, sha: string, time: number} | null} */
  let last = null;
  /** @type {{commits: number, lastDate: string | null, lastSha: string | null, time: number} | null} */
  let automation = null;
  let commits = 0;
  let skipped = 0;

  for (const line of output.split('\n')) {
    const [sha = '', rawName = '', rawDate = '', rawEmail = ''] = line.trim().split(FIELD_SEPARATOR);
    if (!SHA.test(sha)) {
      continue;
    }
    if (boundary.has(sha.toLowerCase())) {
      skipped += 1;
      continue;
    }
    const name = rawName.trim() || 'Unknown';
    const parsed = Date.parse(rawDate);
    const time = Number.isFinite(parsed) ? parsed : -Infinity;
    const date = Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;

    if (isExcluded && isExcluded({name: rawName.trim(), email: rawEmail.trim()})) {
      if (automation === null) {
        automation = {commits: 0, lastDate: null, lastSha: null, time: -Infinity};
      }
      automation.commits += 1;
      if (automation.commits === 1 || time > automation.time) {
        automation.time = time;
        automation.lastDate = date;
        automation.lastSha = sha;
      }
      continue;
    }
    commits += 1;

    if (last === null || time > last.time) {
      last = {name, date, sha, time};
    }

    const key = name.toLowerCase();
    let entry = byName.get(key);
    if (!entry) {
      entry = {name, commits: 0, lastDate: null, lastSha: null, time: -Infinity, order: byName.size};
      byName.set(key, entry);
    }
    entry.commits += 1;
    if (entry.commits === 1 || time > entry.time) {
      entry.time = time;
      entry.name = name;
      entry.lastDate = date;
      entry.lastSha = sha;
    }
  }

  if (commits === 0 && skipped > 0) {
    return {lastAuthor: null, lastDate: null, lastSha: null, commits: null, authors: null};
  }

  const authors = [...byName.values()]
    .sort((a, b) => b.commits - a.commits || compareDesc(a.time, b.time) || a.order - b.order)
    .map(({time, order, ...author}) => author);

  /** @type {GitSummary} */
  const summary = {
    lastAuthor: last ? last.name : null,
    lastDate: last ? last.date : null,
    lastSha: last ? last.sha : null,
    commits,
    authors,
  };
  if (automation !== null) {
    const {time, ...counted} = automation;
    summary.automation = counted;
  }
  return summary;
}

// Exposed for the unit tests (test/pageMeta.test.mjs).
module.exports.summarizeGitLog = summarizeGitLog;
module.exports.shallowBoundary = shallowBoundary;

/**
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
function compareDesc(a, b) {
  if (a === b) {
    return 0;
  }
  return a > b ? -1 : 1;
}

/**
 * Markdown and MDX files under dir, sorted, skipping hidden and node_modules
 * folders and any folder for which skipDir returns true. Missing dir: [].
 *
 * @param {string} dir
 * @param {(dir: string) => boolean} skipDir
 * @returns {Promise<string[]>}
 */
async function listMarkdown(dir, skipDir) {
  /** @type {string[]} */
  const files = [];
  /** @param {string} current */
  async function walk(current) {
    let entries;
    try {
      entries = await fs.promises.readdir(current, {withFileTypes: true});
    } catch {
      return;
    }
    entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!skipName(entry.name) && !skipDir(full)) {
          await walk(full);
        }
      } else if (entry.isFile() && MARKDOWN_FILE.test(entry.name)) {
        files.push(full);
      }
    }
  }
  await walk(dir);
  return files;
}

/**
 * @param {string} name
 * @returns {boolean}
 */
function skipName(name) {
  return name.startsWith('.') || name === 'node_modules';
}

/**
 * @param {string} dir
 * @returns {fs.Dirent[]}
 */
function safeReadDir(dir) {
  try {
    return fs.readdirSync(dir, {withFileTypes: true});
  } catch {
    return [];
  }
}

/**
 * @param {string} file
 * @returns {string}
 */
function posix(file) {
  return file.split(path.sep).join('/');
}

/**
 * Title of a blog post: the front matter `title`, else the first `# heading`,
 * else the file name.
 *
 * @param {string} content
 * @param {string} file
 * @returns {string}
 */
function postTitle(content, file) {
  const text = content.replace(/^﻿/, '').replace(/\r\n?/g, '\n');
  const frontMatter = /^---[ \t]*\n([\s\S]*?)\n---[ \t]*(?:\n|$)/.exec(text);
  if (frontMatter) {
    const line = /^title:[ \t]*(.+?)[ \t]*$/m.exec(frontMatter[1]);
    const value = line ? unquote(line[1]) : '';
    if (value) {
      return value;
    }
  }
  const body = frontMatter ? text.slice(frontMatter[0].length) : text;
  const heading = /^#[ \t]+(.+?)[ \t#]*$/m.exec(body);
  if (heading && heading[1].trim()) {
    return heading[1].trim();
  }
  return path.posix.basename(file).replace(MARKDOWN_FILE, '');
}

/**
 * A YAML scalar as written on one line: "double", 'single' or plain.
 *
 * @param {string} raw
 * @returns {string}
 */
function unquote(raw) {
  const value = raw.trim();
  if (value.startsWith('"')) {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === 'string' ? parsed.trim() : '';
    } catch {
      return value.replace(/^"|"$/g, '').trim();
    }
  }
  if (value.startsWith("'")) {
    return value.replace(/^'|'$/g, '').replace(/''/g, "'").trim();
  }
  // Plain scalar: drop a trailing comment.
  return value.replace(/[ \t]+#.*$/, '').trim();
}
