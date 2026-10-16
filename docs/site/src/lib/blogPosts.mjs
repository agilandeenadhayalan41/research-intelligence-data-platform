// Links between the blog and its editor, as pure functions (`node --test`
// loads this file; it reads no browser global).
//
// The editor at /blog/editor opens a published post directly when its URL
// names it: /blog/editor?post=docs/site/blog/2026-10-16-welcome-to-the-blog.md
// (the repository path; a path inside docs/site/blog/ alone also works).

import {BLOG_POSTS_DIR, isBlogPostPath} from './github.mjs';

/** Route of the blog editor. */
export const BLOG_EDITOR_ROUTE = '/blog/editor';

/** Query parameter that names the post to open. */
export const POST_QUERY_PARAM = 'post';

/** Editor draft keys: 'blog:<repo path>' for a published post, 'blog-new:<id>' for a new one. */
export const POST_DRAFT_PREFIX = 'blog:';
export const NEW_DRAFT_PREFIX = 'blog-new:';

/**
 * The draft the editor reopens when its address names no post (the blog's
 * "Write a post" button): the newest unfinished NEW post, or null to start a
 * new post. Drafts of published posts (left by Edit) are not reopened here;
 * they open through '?post=' and the editor's picker.
 *
 * @template {{key: string}} T
 * @param {readonly T[]} drafts Newest first (as listDrafts returns them).
 * @returns {T | null}
 */
export function draftToResume(drafts) {
  return drafts.find((draft) => typeof draft?.key === 'string' && draft.key.startsWith(NEW_DRAFT_PREFIX)) ?? null;
}

/** Longest `?post=` value kept (for the message about an invalid one). */
const MAX_VALUE_LENGTH = 300;

/**
 * @typedef {{kind: 'none'} | {kind: 'post', repoPath: string} | {kind: 'invalid', value: string}} PostQuery
 */

/**
 * The editor route, opening `repoPath` when it is a blog post path:
 * '/blog/editor?post=docs/site/blog/x.md'. The slashes are kept readable;
 * every other reserved character is percent-encoded.
 *
 * @param {string | null} [repoPath]
 * @returns {string}
 */
export function blogEditorUrl(repoPath) {
  if (!isBlogPostPath(repoPath)) {
    return BLOG_EDITOR_ROUTE;
  }
  return `${BLOG_EDITOR_ROUTE}${postQueryString(repoPath)}`;
}

/**
 * '?post=<repo path>' for a blog post path, '' for anything else.
 *
 * @param {string | null} [repoPath]
 * @returns {string}
 */
export function postQueryString(repoPath) {
  if (!isBlogPostPath(repoPath)) {
    return '';
  }
  const value = repoPath.split('/').map(encodeURIComponent).join('/');
  return `?${POST_QUERY_PARAM}=${value}`;
}

/**
 * Reads the post named by the editor URL's query string ('?post=...', with
 * or without the '?').
 *
 * - no `post` parameter, or an empty one: {kind: 'none'};
 * - a blog post path: {kind: 'post', repoPath}. Accepted: the repository path
 *   ('docs/site/blog/x.md'), or a path inside the blog folder ('x.md',
 *   'folder/x.mdx'); surrounding spaces and a leading '/' are ignored;
 * - anything else (another folder, another extension, '..'):
 *   {kind: 'invalid', value} with the value as given (trimmed, at most 300
 *   characters), for a message.
 *
 * @param {unknown} search
 * @returns {PostQuery}
 */
export function parsePostQuery(search) {
  if (typeof search !== 'string' || !search) {
    return {kind: 'none'};
  }
  let raw;
  try {
    raw = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search).get(POST_QUERY_PARAM);
  } catch {
    return {kind: 'none'};
  }
  const value = (raw ?? '').trim();
  if (!value) {
    return {kind: 'none'};
  }
  const path = value.replace(/^\/+/, '');
  const repoPath = path.startsWith('docs/') ? path : `${BLOG_POSTS_DIR}${path}`;
  if (isBlogPostPath(repoPath)) {
    return {kind: 'post', repoPath};
  }
  return {kind: 'invalid', value: value.slice(0, MAX_VALUE_LENGTH)};
}
