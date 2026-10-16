import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {describe, test} from 'node:test';

import {
  GLOBAL_COMPONENTS,
  SITE_DIR,
  checkFiles,
  checkSource,
  defaultFiles,
  formatFailure,
} from '../scripts/check-mdx.mjs';
import {newPostTemplate} from '../src/lib/github.mjs';

describe('check-mdx: the real docs', () => {
  test('every doc and blog post compiles as MDX', async () => {
    const files = defaultFiles();
    assert.ok(files.length >= 30, `expected the docs, found ${files.length} files`);
    assert.ok(files.some((file) => file.endsWith(path.join('overview', 'introduction.md'))));
    assert.ok(!files.some((file) => file.includes(`${path.sep}node_modules${path.sep}`)));
    const failures = await checkFiles(files);
    assert.deepEqual(failures.map(formatFailure), []);
  });

  test('the site parses .md as MDX and keeps HTML comments valid', () => {
    const config = readFileSync(path.join(SITE_DIR, 'docusaurus.config.ts'), 'utf8');
    assert.match(config, /^\s*format: 'mdx',$/m);
    assert.match(config, /mdx1Compat: \{\s*comments: true,\s*\}/);
  });

  test('src/theme/MDXComponents.tsx registers every global component', () => {
    const source = readFileSync(path.join(SITE_DIR, 'src/theme/MDXComponents.tsx'), 'utf8');
    const registered = /const MDXComponents = \{([\s\S]*?)\};/.exec(source)?.[1] ?? '';
    const names = [...registered.matchAll(/^\s*([A-Z]\w*),$/gm)].map((match) => match[1]);
    // Details and Head come from the theme's own MDXComponents.
    assert.deepEqual([...names, 'Details', 'Head'].sort(), [...GLOBAL_COMPONENTS].sort());
  });

  test('new blog posts from the editor template compile', async () => {
    for (const format of ['md', 'mdx']) {
      const source = newPostTemplate({title: 'A post', format, date: '2026-10-16'});
      assert.deepEqual(await checkSource(source, `post.${format}`), [], format);
    }
  });
});

describe('check-mdx: checkSource', () => {
  test('a stray "<" is reported with its line and column', async () => {
    const [problem, ...rest] = await checkSource('# Title\n\nSize <N> rows.\n');
    assert.equal(rest.length, 0);
    assert.equal(problem.line, 3);
    assert.match(problem.message, /closing tag/);
  });

  test('a brace expression that would run as JavaScript is reported', async () => {
    const problems = await checkSource('Lookup by\n\nGET /works/{doi} here.\n');
    assert.equal(problems.length, 1);
    assert.equal(problems[0].line, 3);
    assert.equal(problems[0].column, 12);
    assert.match(problems[0].message, /\{doi\} runs as JavaScript/);
  });

  test('a syntax error inside braces is reported', async () => {
    const [problem] = await checkSource('POST /sql { "query": "x" } now\n');
    assert.equal(problem.line, 1);
    assert.match(problem.message, /acorn/);
  });

  test('escapes, code, comments and string literals are fine', async () => {
    const source = [
      '---',
      'title: Fine',
      '---',
      '',
      'Escaped \\{doi\\} and \\<N>, code `GET /works/{doi}` and `<N>`.',
      '',
      '```js',
      'const x = {a: 1} < 2;',
      '```',
      '',
      '<!-- an HTML comment -->',
      '',
      '{/* an MDX comment */}',
      '',
      "A literal {'<'} sign.",
      '',
      ':::note',
      'An admonition.',
      ':::',
      '',
      '| a | b |',
      '| --- | --- |',
      '| 1 | 2 |',
      '',
    ].join('\n');
    assert.deepEqual(await checkSource(source), []);
  });

  test('unknown components are reported; global and imported ones are fine', async () => {
    const source = [
      "import Thing from '@site/src/components/Thing';",
      '',
      '<Cards>',
      '<Card title="A" href="a.md" />',
      '</Cards>',
      '',
      '<Thing />',
      '',
      'Inline <Badge>VERIFIED</Badge> and <Crad title="typo" />.',
      '',
    ].join('\n');
    const problems = await checkSource(source);
    assert.equal(problems.length, 1);
    assert.equal(problems[0].line, 9);
    assert.match(problems[0].message, /unknown component <Crad>/);
  });
});
