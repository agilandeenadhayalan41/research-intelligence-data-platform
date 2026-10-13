// Remark plugin: rewrite relative links that leave the docs/ folder.
//
// The Markdown under docs/ is written for GitHub, so it links to source files
// with paths such as ../../sql/control/001_pipeline_control.sql. Those files
// are not pages of this site. This plugin turns such links into GitHub URLs
// (blob/main/<path> for files, tree/main/<path> for folders) and keeps any
// #anchor or ?query. Links between docs pages (other .md/.mdx files under
// docs/) are left alone so Docusaurus resolves them as doc links.
//
// It must run before Docusaurus' own link resolution, so it is registered with
// `beforeDefaultRemarkPlugins` in docusaurus.config.ts.

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// src/remark -> docs/site -> docs -> repository root
const DEFAULT_DOCS_DIR = path.resolve(HERE, '..', '..', '..');
const DEFAULT_REPO_ROOT = path.resolve(DEFAULT_DOCS_DIR, '..');

const DEFAULTS = {
  repoUrl:
    'https://github.com/agilandeenadhayalan41/research-intelligence-data-platform',
  branch: 'main',
  docsDir: DEFAULT_DOCS_DIR,
  repoRoot: DEFAULT_REPO_ROOT,
};

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const DOC_EXTENSION = /\.mdx?$/i;

function isInside(parent, child) {
  const rel = path.relative(parent, child);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function splitUrl(url) {
  const cut = url.search(/[?#]/);
  return cut === -1 ? [url, ''] : [url.slice(0, cut), url.slice(cut)];
}

function safeDecode(value) {
  try {
    return decodeURI(value);
  } catch {
    return value;
  }
}

function statOrNull(target) {
  try {
    return fs.statSync(target);
  } catch {
    return null;
  }
}

/**
 * @param {Partial<typeof DEFAULTS>} [options]
 */
export default function repoLinks(options = {}) {
  const settings = {...DEFAULTS, ...options};
  const docsDir = path.resolve(settings.docsDir);
  const repoRoot = path.resolve(settings.repoRoot);
  const siteDir = path.join(docsDir, 'site');

  function repoPath(absolutePath) {
    return path.relative(repoRoot, absolutePath).split(path.sep).join('/');
  }

  function githubUrl(absolutePath, isDirectory, suffix) {
    const kind = isDirectory ? 'tree' : 'blob';
    return `${settings.repoUrl}/${kind}/${settings.branch}/${encodeURI(repoPath(absolutePath))}${suffix}`;
  }

  function rewrite(url, sourceFile) {
    if (
      !url ||
      url.startsWith('#') ||
      url.startsWith('/') ||
      SCHEME.test(url)
    ) {
      return null;
    }
    const [pathname, suffix] = splitUrl(url);
    if (!pathname) {
      return null;
    }
    const target = path.resolve(path.dirname(sourceFile), safeDecode(pathname));
    if (!isInside(repoRoot, target)) {
      // Points outside the repository: leave it for Docusaurus to report.
      return null;
    }
    const stat = statOrNull(target);
    const isDirectory = stat ? stat.isDirectory() : pathname.endsWith('/');
    const insideDocs = isInside(docsDir, target) && !isInside(siteDir, target);

    if (insideDocs) {
      // Doc pages and assets under docs/ are handled by Docusaurus. Only
      // folders (which are not pages) go to GitHub.
      if (!isDirectory || DOC_EXTENSION.test(pathname)) {
        return null;
      }
    }
    if (!stat) {
      throw new Error(
        `Broken repository link "${url}" in ${repoPath(sourceFile)}: ` +
          `${repoPath(target)} does not exist.`,
      );
    }
    return githubUrl(target, isDirectory, suffix);
  }

  function walk(node, sourceFile) {
    if ((node.type === 'link' || node.type === 'definition') && node.url) {
      const next = rewrite(node.url, sourceFile);
      if (next) {
        node.url = next;
      }
    }
    if (Array.isArray(node.children)) {
      for (const child of node.children) {
        walk(child, sourceFile);
      }
    }
  }

  return (tree, file) => {
    if (file.path) {
      walk(tree, path.resolve(file.path));
    }
  };
}
