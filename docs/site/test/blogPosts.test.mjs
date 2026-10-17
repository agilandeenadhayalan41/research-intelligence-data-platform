import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  BLOG_EDITOR_ROUTE,
  NEW_DRAFT_PREFIX,
  POST_DRAFT_PREFIX,
  blogEditorUrl,
  draftToResume,
  parsePostQuery,
  postQueryString,
} from '../src/lib/blogPosts.mjs';

const POST = 'docs/site/blog/2026-10-16-welcome-to-the-blog.md';

describe('editor ?post= links', () => {
  test('blogEditorUrl names the post with readable slashes', () => {
    assert.equal(BLOG_EDITOR_ROUTE, '/blog/editor');
    assert.equal(blogEditorUrl(POST), `/blog/editor?post=${POST}`);
    assert.equal(
      blogEditorUrl('docs/site/blog/a b&c#d+e.mdx'),
      '/blog/editor?post=docs/site/blog/a%20b%26c%23d%2Be.mdx',
    );
  });

  test('blogEditorUrl and postQueryString ignore anything that is not a blog post', () => {
    for (const value of [null, undefined, '', 'docs/overview/status.md', 'docs/site/blog/x.txt', 'docs/site/blog/../x.md']) {
      assert.equal(blogEditorUrl(value), '/blog/editor', String(value));
      assert.equal(postQueryString(value), '', String(value));
    }
  });

  test('parsePostQuery: no parameter, or an empty one', () => {
    for (const search of ['', '?', '?other=1', '?post=', '?post=%20%20', 'post=', undefined, null, 42]) {
      assert.deepEqual(parsePostQuery(search), {kind: 'none'}, String(search));
    }
  });

  test('parsePostQuery: a repository path, encoded or not, with or without the "?"', () => {
    for (const search of [
      `?post=${POST}`,
      `post=${POST}`,
      `?post=${encodeURIComponent(POST)}`,
      `?other=1&post=${POST}&x=2`,
      `?post=%20${POST}%20`,
      `?post=/${POST}`,
    ]) {
      assert.deepEqual(parsePostQuery(search), {kind: 'post', repoPath: POST}, search);
    }
  });

  test('parsePostQuery: a path inside the blog folder', () => {
    assert.deepEqual(parsePostQuery('?post=2026-10-16-welcome-to-the-blog.md'), {kind: 'post', repoPath: POST});
    assert.deepEqual(parsePostQuery('?post=series/part-1.mdx'), {
      kind: 'post',
      repoPath: 'docs/site/blog/series/part-1.mdx',
    });
  });

  test('parsePostQuery: anything else is invalid, and the value is kept for the message', () => {
    for (const value of [
      'docs/overview/status.md',
      'docs/site/blog/../../../README.md',
      '../secrets.md',
      'docs/site/blog/x.txt',
      'x.js',
      'docs/site/blog/',
    ]) {
      assert.deepEqual(parsePostQuery(`?post=${encodeURIComponent(value)}`), {kind: 'invalid', value}, value);
    }
    const long = parsePostQuery(`?post=${'a'.repeat(1000)}`);
    assert.equal(long.kind, 'invalid');
    assert.equal(long.value.length, 300);
  });

  test('round trip: the Edit link opens the same post', () => {
    for (const path of [POST, 'docs/site/blog/a b&c#d+e.mdx', 'docs/site/blog/日本.md']) {
      const url = new URL(blogEditorUrl(path), 'https://example.invalid');
      assert.equal(url.pathname, '/blog/editor');
      assert.deepEqual(parsePostQuery(url.search), {kind: 'post', repoPath: path}, path);
    }
  });
});

describe('draft reopened by the bare editor route (Write a post)', () => {
  const edit = {key: `${POST_DRAFT_PREFIX}${POST}`, updatedAt: '2026-10-16T12:00:00.000Z'};
  const older = {key: `${NEW_DRAFT_PREFIX}a1`, updatedAt: '2026-10-16T10:00:00.000Z'};
  const newer = {key: `${NEW_DRAFT_PREFIX}b2`, updatedAt: '2026-10-16T11:00:00.000Z'};

  test('reopens the newest unfinished new post, skipping drafts of published posts', () => {
    assert.equal(POST_DRAFT_PREFIX, 'blog:');
    assert.equal(NEW_DRAFT_PREFIX, 'blog-new:');
    // listDrafts order: newest first; the Edit draft is the newest of all.
    assert.equal(draftToResume([edit, newer, older]), newer);
  });

  test('starts a new post (null) when only drafts of published posts exist, or none', () => {
    assert.equal(draftToResume([edit]), null);
    assert.equal(draftToResume([]), null);
  });
});
