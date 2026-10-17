import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, test} from 'node:test';

import {compile} from '@mdx-js/mdx';

import {jsxLinksAfter, jsxLinksBefore} from '../src/remark/jsx-links.mjs';
import repoLinks from '../src/remark/repo-links.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DOCS_DIR = path.resolve(HERE, '..', '..');
const SOURCE = path.join(DOCS_DIR, 'overview', 'introduction.md');

// Stands in for Docusaurus' resolveMarkdownLinks: relative .md paths become
// routes, anything else is left alone, and an unknown page throws.
function fakeResolver() {
  return (tree) => {
    const walk = (node) => {
      if ((node.type === 'definition' || node.type === 'link') && /^[^/:#?]+\.md(#|$)/.test(node.url)) {
        const [file, hash] = node.url.split('#');
        if (file === 'missing.md') {
          throw new Error(`broken markdown link ${node.url} at line ${node.position?.start.line}`);
        }
        node.url = `/overview/${file.replace(/\.md$/, '')}${hash ? `#${hash}` : ''}`;
      }
      for (const child of node.children ?? []) {
        walk(child);
      }
    };
    walk(tree);
  };
}

async function compileDoc(source) {
  const output = await compile(
    {value: source, path: SOURCE},
    {
      // The site parses .md files as MDX (markdown.format: 'mdx').
      format: 'mdx',
      remarkPlugins: [jsxLinksBefore, repoLinks, fakeResolver, jsxLinksAfter],
      development: false,
    },
  );
  return String(output);
}

describe('jsx-links', () => {
  test('a Card href with a relative .md path is resolved like a Markdown link', async () => {
    const js = await compileDoc('<Card title="Status" href="status.md#environment-readiness" />\n');
    assert.match(js, /href: "\/overview\/status#environment-readiness"/);
  });

  test('inline Cards, site routes and external URLs', async () => {
    const js = await compileDoc(
      [
        'Text <Card title="A" href="glossary.md" /> inline.',
        '',
        '<Card title="B" href="/overview/status" />',
        '',
        '<Card title="C" href="https://example.org/x.md" />',
        '',
      ].join('\n'),
    );
    assert.match(js, /href: "\/overview\/glossary"/);
    assert.match(js, /href: "\/overview\/status"/);
    assert.match(js, /href: "https:\/\/example.org\/x.md"/);
  });

  test('a link out of docs/ becomes a GitHub URL (repo-links)', async () => {
    const js = await compileDoc('<Card title="README" href="../../README.md" />\n');
    assert.match(
      js,
      /href: "https:\/\/github.com\/agilandeenadhayalan41\/research-intelligence-data-platform\/blob\/main\/README.md"/,
    );
  });

  test('other components, other attributes and expressions are untouched', async () => {
    const js = await compileDoc(
      [
        '<Badge href="status.md">x</Badge>',
        '',
        '<Card title="status.md" href={"status.md"} />',
        '',
      ].join('\n'),
    );
    assert.equal((js.match(/\/overview\/status/g) ?? []).length, 0);
    assert.match(js, /title: "status.md"/);
  });

  test('a broken Card link fails like a broken Markdown link, with its line', async () => {
    await assert.rejects(
      compileDoc('# Title\n\n<Card title="Nope" href="missing.md" />\n'),
      /broken markdown link missing.md at line 3/,
    );
  });

  test('the helper nodes are removed before rendering', async () => {
    const js = await compileDoc('<Card title="Status" href="status.md" />\n\n[ref]: status.md\n');
    assert.ok(!js.includes('rid-jsx-link'));
  });
});
