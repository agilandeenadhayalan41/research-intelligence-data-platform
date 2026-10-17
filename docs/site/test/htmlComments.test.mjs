import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import * as mdx from '@mdx-js/mdx';

import {checkSource} from '../scripts/check-mdx.mjs';
import {htmlCommentsToMdx} from '../src/lib/htmlComments.mjs';

// What the blog editor's preview does: the transform, then a plain MDX
// compile (no remark-comment). Returns the error message, or null.
async function previewError(source) {
  try {
    await mdx.compile(htmlCommentsToMdx(source, mdx), {format: 'mdx', development: false});
    return null;
  } catch (error) {
    return String(error.reason ?? error.message ?? error);
  }
}

const lines = (text) => text.split('\n').length;

describe('htmlCommentsToMdx', () => {
  test('a source without HTML comments is returned as is', () => {
    const source = '# Title\n\nText with {braces} and <Kbd>K</Kbd>.\n';
    assert.equal(htmlCommentsToMdx(source, mdx), source);
  });

  test('a comment block becomes an MDX comment', async () => {
    const source = 'Intro.\n\n<!-- truncate -->\n\nMore.\n';
    assert.equal(htmlCommentsToMdx(source, mdx), 'Intro.\n\n{/* truncate */}\n\nMore.\n');
    assert.equal(await previewError(source), null);
  });

  test('a comment followed by text on the same line', async () => {
    const source = '<!-- a --> text\n';
    assert.equal(htmlCommentsToMdx(source, mdx), '{/* a */} text\n');
    assert.equal(await previewError(source), null);
    assert.deepEqual(await checkSource(source), [], 'the build accepts it too');
  });

  test('a comment inside an HTML block that starts with a JSX tag', async () => {
    const source = '<Callout type="tip">\n<!-- note -->\n\nText\n\n</Callout>\n';
    assert.equal(
      htmlCommentsToMdx(source, mdx),
      '<Callout type="tip">\n{/* note */}\n\nText\n\n</Callout>\n',
    );
    assert.equal(await previewError(source), null);
    assert.deepEqual(await checkSource(source), [], 'the build accepts it too');
  });

  test('several comments in one HTML block are all converted', () => {
    const source = '<!-- one --> and <!-- two -->\n';
    assert.equal(htmlCommentsToMdx(source, mdx), '{/* one */} and {/* two */}\n');
  });

  test('a multi-line comment keeps the line count', async () => {
    const source = 'A\n\n<!--\nline one\nline two\n-->\n\n{oops\n';
    const result = htmlCommentsToMdx(source, mdx);
    assert.equal(lines(result), lines(source));
    assert.ok(result.includes('{/*\nline one\nline two\n*/}'));
  });

  test('"*/" inside a comment cannot close the MDX comment early', async () => {
    const source = '<!-- a */ b -->\n';
    assert.equal(htmlCommentsToMdx(source, mdx), '{/* a * / b */}\n');
    assert.equal(await previewError(source), null);
  });

  test('comments inside code are left alone', () => {
    const source = '```html\n<!-- in a fence -->\n```\n\nUse `<!-- inline -->` here.\n';
    assert.equal(htmlCommentsToMdx(source, mdx), source);
  });
});
