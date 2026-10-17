#!/usr/bin/env node
// Compile every doc and blog post as MDX, the way the site's build parses them
// (docusaurus.config.ts sets `markdown.format: 'mdx'`), and report
// file:line:column for each problem.
//
// It parses with the same remark plugins Docusaurus uses (front matter,
// directives for `:::note` admonitions, GitHub-flavoured Markdown, and HTML
// comments, which `markdown.mdx1Compat.comments` keeps valid), resolved from
// @docusaurus/mdx-loader so the versions match the build. Besides syntax
// errors it reports two things that compile but would fail or misrender when
// the build renders the page:
//
// - a `{...}` expression that is not a comment or a string literal: in MDX it
//   runs as JavaScript (`{doi}` throws "doi is not defined"), so text braces
//   must be escaped as `\{` and `\}`;
// - a JSX component that is neither registered globally in
//   src/theme/MDXComponents.tsx nor imported by the file.
//
//   npm run check:mdx              # every doc under docs/ and every blog post
//   node scripts/check-mdx.mjs a.md b.md

import {readFileSync, readdirSync, statSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SITE_DIR = path.resolve(HERE, '..');
export const DOCS_DIR = path.resolve(SITE_DIR, '..');
export const REPO_ROOT = path.resolve(DOCS_DIR, '..');

// Components a doc can use without an import: the ones registered in
// src/theme/MDXComponents.tsx (the site's own and the theme's).
export const GLOBAL_COMPONENTS = Object.freeze([
  'Badge',
  'Callout',
  'Card',
  'Cards',
  'Details',
  'FileTree',
  'Head',
  'Kbd',
  'Step',
  'Steps',
  'TabItem',
  'Tabs',
]);

const MDX_EXTENSION = /\.mdx?$/i;

// The plugins Docusaurus itself loads, from its own dependency tree.
const fromMdxLoader = createRequire(
  createRequire(import.meta.url).resolve('@docusaurus/mdx-loader'),
);
async function load(name) {
  const module = await import(pathToFileURL(fromMdxLoader.resolve(name)).href);
  return module.default ?? module;
}
const [mdx, remarkFrontmatter, remarkDirective, remarkGfm, remarkComment] = await Promise.all([
  import(pathToFileURL(fromMdxLoader.resolve('@mdx-js/mdx')).href),
  load('remark-frontmatter'),
  load('remark-directive'),
  load('remark-gfm'),
  load('@slorber/remark-comment'),
]);

function walk(dir, skip, out) {
  for (const entry of readdirSync(dir, {withFileTypes: true})) {
    const full = path.join(dir, entry.name);
    if (skip(full)) {
      continue;
    }
    if (entry.isDirectory()) {
      walk(full, skip, out);
    } else if (MDX_EXTENSION.test(entry.name) && !entry.name.startsWith('_')) {
      out.push(full);
    }
  }
  return out;
}

/** Every doc under docs/ (not docs/site/) and every blog post, sorted. */
export function defaultFiles() {
  const siteDir = path.join(DOCS_DIR, 'site');
  const docs = walk(DOCS_DIR, (full) => full === siteDir, []);
  const blog = walk(path.join(SITE_DIR, 'blog'), () => false, []);
  return [...docs, ...blog].sort();
}

function isHarmlessExpression(node) {
  const body = node.data?.estree?.body ?? [];
  if (body.length === 0) {
    return true; // {/* a comment */}
  }
  if (body.length !== 1 || body[0].type !== 'ExpressionStatement') {
    return false;
  }
  const {expression} = body[0];
  return expression.type === 'Literal' && typeof expression.value === 'string';
}

function collect(tree, found) {
  const visit = (node) => {
    if (
      (node.type === 'mdxJsxFlowElement' || node.type === 'mdxJsxTextElement') &&
      typeof node.name === 'string' &&
      /^[A-Z]/.test(node.name) &&
      !node.name.includes('.')
    ) {
      found.components.push(node);
    }
    if (
      (node.type === 'mdxFlowExpression' || node.type === 'mdxTextExpression') &&
      !isHarmlessExpression(node)
    ) {
      found.expressions.push(node);
    }
    for (const child of node.children ?? []) {
      visit(child);
    }
  };
  visit(tree);
}

function importedNames(tree) {
  const names = new Set();
  for (const node of tree.children ?? []) {
    if (node.type !== 'mdxjsEsm') {
      continue;
    }
    for (const statement of node.data?.estree?.body ?? []) {
      for (const specifier of statement.specifiers ?? []) {
        if (specifier.local?.name) {
          names.add(specifier.local.name);
        }
      }
    }
  }
  return names;
}

function where(node) {
  return {line: node.position?.start.line ?? 1, column: node.position?.start.column ?? 1};
}

function shorten(text) {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > 60 ? `${flat.slice(0, 57)}...` : flat;
}

/**
 * Compile one MDX source. Returns its problems as `{line, column, message}`
 * (an empty list when the file is fine). A syntax error stops the compiler,
 * so it is the only problem reported for that file.
 *
 * @param {string} source
 * @param {string} [filePath]
 * @param {{components?: readonly string[]}} [options]
 */
export async function checkSource(source, filePath = 'input.mdx', options = {}) {
  const known = new Set(options.components ?? GLOBAL_COMPONENTS);
  const found = {components: [], expressions: [], imports: new Set()};
  const remarkCollect = () => (tree) => {
    collect(tree, found);
    found.imports = importedNames(tree);
  };
  try {
    await mdx.compile(
      {value: source, path: filePath},
      {
        format: 'mdx',
        development: false,
        remarkPlugins: [
          remarkFrontmatter,
          remarkDirective,
          remarkGfm,
          remarkComment,
          remarkCollect,
        ],
      },
    );
  } catch (error) {
    const start = error.place?.start ?? error.place ?? error.position?.start ?? {};
    return [
      {
        line: error.line ?? start.line ?? 1,
        column: error.column ?? start.column ?? 1,
        message: String(error.reason ?? error.message ?? error),
      },
    ];
  }
  const problems = [];
  for (const node of found.expressions) {
    problems.push({
      ...where(node),
      message:
        `MDX expression {${shorten(node.value ?? '')}} runs as JavaScript; ` +
        'if the braces are text, escape them as \\{ and \\} (or use inline code)',
    });
  }
  for (const node of found.components) {
    if (!known.has(node.name) && !found.imports.has(node.name)) {
      problems.push({
        ...where(node),
        message:
          `unknown component <${node.name}>: it is not registered in ` +
          'src/theme/MDXComponents.tsx and not imported',
      });
    }
  }
  return problems.sort((a, b) => a.line - b.line || a.column - b.column);
}

/**
 * @param {string[]} files absolute or relative paths
 * @returns {Promise<{file: string, line: number, column: number, message: string}[]>}
 */
export async function checkFiles(files) {
  const failures = [];
  for (const file of files) {
    const absolute = path.resolve(file);
    const problems = await checkSource(readFileSync(absolute, 'utf8'), absolute);
    const relative = path.relative(REPO_ROOT, absolute).split(path.sep).join('/');
    for (const problem of problems) {
      failures.push({file: relative, ...problem});
    }
  }
  return failures;
}

export function formatFailure({file, line, column, message}) {
  return `${file}:${line}:${column}: ${message}`;
}

async function main(argv) {
  for (const file of argv) {
    if (!statSync(file, {throwIfNoEntry: false})?.isFile()) {
      console.error(`check-mdx: no such file: ${file}`);
      return 2;
    }
  }
  const files = argv.length > 0 ? argv : defaultFiles();
  const failures = await checkFiles(files);
  for (const failure of failures) {
    console.error(formatFailure(failure));
  }
  if (failures.length > 0) {
    const broken = new Set(failures.map((failure) => failure.file)).size;
    console.error(
      `check-mdx: ${failures.length} problem(s) in ${broken} of ${files.length} file(s)`,
    );
    return 1;
  }
  console.log(`check-mdx: all ${files.length} file(s) compile as MDX`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = await main(process.argv.slice(2));
}
