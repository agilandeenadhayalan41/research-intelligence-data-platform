import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {describe, test} from 'node:test';

import {isAutomationAuthor} from '../src/lib/github.mjs';

const require = createRequire(import.meta.url);
const {summarizeGitLog, shallowBoundary} = require('../src/plugins/page-meta/index.js');

const SEP = '\x1f';
const sha = (digit) => digit.repeat(40);
const line = (hash, name, date) => [hash, name, date].join(SEP);
// The plugin's format: sha, name, date, email.
const lineWithEmail = (hash, name, date, email) => [hash, name, date, email].join(SEP);

describe('page-meta summarizeGitLog', () => {
  const log = [
    line(sha('c'), 'Ada', '2026-10-16T10:00:00+00:00'),
    line(sha('b'), 'Grace', '2026-10-15T10:00:00+00:00'),
    line(sha('a'), 'Ada', '2026-10-14T10:00:00+00:00'),
  ].join('\n');

  test('a full history credits the newest commit and counts every author', () => {
    const summary = summarizeGitLog(log);
    assert.equal(summary.lastAuthor, 'Ada');
    assert.equal(summary.lastDate, '2026-10-16T10:00:00.000Z');
    assert.equal(summary.lastSha, sha('c'));
    assert.equal(summary.commits, 3);
    assert.deepEqual(
      summary.authors.map(({name, commits}) => [name, commits]),
      [
        ['Ada', 2],
        ['Grace', 1],
      ],
    );
  });

  test('shallow clone: boundary commits are ignored', () => {
    const summary = summarizeGitLog(log, new Set([sha('a')]));
    assert.equal(summary.lastSha, sha('c'));
    assert.equal(summary.commits, 2);
    assert.deepEqual(
      summary.authors.map(({name, commits}) => [name, commits]),
      [
        ['Ada', 1],
        ['Grace', 1],
      ],
    );
  });

  test('shallow clone: boundary shas match whatever their case', () => {
    const upper = line(sha('D'), 'Boundary', '2026-10-16T12:00:00+00:00');
    const summary = summarizeGitLog(`${upper}\n${log}`, new Set([sha('d')]));
    assert.equal(summary.lastAuthor, 'Ada');
    assert.equal(summary.commits, 3);
  });

  test('shallow clone: a file seen only in the boundary commit has an unknown history', () => {
    const onlyBoundary = line(sha('e'), 'Boundary', '2026-10-16T12:00:00+00:00');
    assert.deepEqual(summarizeGitLog(onlyBoundary, new Set([sha('e')])), {
      lastAuthor: null,
      lastDate: null,
      lastSha: null,
      commits: null,
      authors: null,
    });
  });

  test('a file with no commits (not committed yet) is empty, not unknown, shallow or not', () => {
    const empty = {lastAuthor: null, lastDate: null, lastSha: null, commits: 0, authors: []};
    assert.deepEqual(summarizeGitLog(''), empty);
    assert.deepEqual(summarizeGitLog('', new Set([sha('e')])), empty);
  });
});

describe('page-meta shallowBoundary', () => {
  const git = (cwd, ...args) =>
    execFileSync(
      'git',
      ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', ...args],
      {cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']},
    ).trim();

  let hasGit = true;
  try {
    execFileSync('git', ['--version'], {stdio: 'ignore'});
  } catch {
    hasGit = false;
  }

  test('not a repository: empty set, no throw', () => {
    const dir = mkdtempSync(join(tmpdir(), 'page-meta-none-'));
    try {
      assert.deepEqual([...shallowBoundary(dir)], []);
    } finally {
      rmSync(dir, {recursive: true, force: true});
    }
  });

  test('full clone: empty set; shallow clone: its boundary commit', {skip: !hasGit && 'git is not installed'}, () => {
    const dir = mkdtempSync(join(tmpdir(), 'page-meta-git-'));
    try {
      const full = join(dir, 'full');
      const shallow = join(dir, 'shallow');
      git(dir, 'init', '-q', full);
      for (const text of ['one', 'two', 'three']) {
        writeFileSync(join(full, 'page.md'), `${text}\n`);
        git(full, 'add', 'page.md');
        git(full, 'commit', '-q', '-m', text);
      }
      assert.deepEqual([...shallowBoundary(full)], []);

      git(dir, 'clone', '-q', '--depth', '2', `file://${full}`, shallow);
      const boundary = shallowBoundary(shallow);
      assert.deepEqual([...boundary], [git(shallow, 'rev-parse', 'HEAD~1')]);

      const log = git(shallow, 'log', '--follow', '--format=%H%x1f%an%x1f%aI', '--', 'page.md');
      const summary = summarizeGitLog(log, boundary);
      assert.equal(summary.commits, 1, 'only the commit after the boundary counts');
      assert.equal(summary.lastSha, git(shallow, 'rev-parse', 'HEAD'));
    } finally {
      rmSync(dir, {recursive: true, force: true});
    }
  });
});

describe('page-meta summarizeGitLog without automation', () => {
  // As the plugin calls it: isAutomationAuthor with the default list.
  const options = {isExcluded: (author) => isAutomationAuthor(author)};
  const log = [
    lineWithEmail(sha('f'), 'Claude', '2026-10-16T12:00:00+00:00', 'noreply@anthropic.com'),
    lineWithEmail(sha('e'), 'Helper', '2026-10-16T11:00:00+00:00', 'helper@anthropic.com'),
    lineWithEmail(sha('d'), 'renovate[bot]', '2026-10-16T10:00:00+00:00', '29139614+renovate[bot]@users.noreply.github.com'),
    lineWithEmail(sha('c'), 'Ada', '2026-10-15T10:00:00+00:00', 'ada@example.invalid'),
    lineWithEmail(sha('b'), 'Cursor Agent', '2026-10-14T10:00:00+00:00', 'cursoragent@cursor.com'),
    lineWithEmail(sha('a'), 'Grace', '2026-10-13T10:00:00+00:00', 'grace@example.invalid'),
  ].join('\n');

  test('people only: authors, count and last change; automation is counted without names', () => {
    const summary = summarizeGitLog(log, new Set(), options);
    assert.equal(summary.lastAuthor, 'Ada');
    assert.equal(summary.lastDate, '2026-10-15T10:00:00.000Z');
    assert.equal(summary.lastSha, sha('c'));
    assert.equal(summary.commits, 2);
    assert.deepEqual(
      summary.authors.map(({name, commits}) => [name, commits]),
      [
        ['Ada', 1],
        ['Grace', 1],
      ],
    );
    assert.deepEqual(summary.automation, {commits: 4, lastDate: '2026-10-16T12:00:00.000Z', lastSha: sha('f')});
    const text = JSON.stringify(summary);
    for (const hidden of ['Claude', 'Helper', 'renovate', 'Cursor', '@', 'example.invalid']) {
      assert.ok(!text.includes(hidden), `${hidden} must not appear`);
    }
  });

  test('a page changed only by automation: no author, no count, the date of the last change', () => {
    const onlyAutomation = log
      .split('\n')
      .filter((entry) => !/Ada|Grace/.test(entry))
      .join('\n');
    assert.deepEqual(summarizeGitLog(onlyAutomation, new Set(), options), {
      lastAuthor: null,
      lastDate: null,
      lastSha: null,
      commits: 0,
      authors: [],
      automation: {commits: 4, lastDate: '2026-10-16T12:00:00.000Z', lastSha: sha('f')},
    });
  });

  test('shallow clone: automation alone after the boundary is an unknown history', () => {
    const shallow = [
      lineWithEmail(sha('f'), 'Claude', '2026-10-16T12:00:00+00:00', 'noreply@anthropic.com'),
      lineWithEmail(sha('a'), 'Boundary', '2026-10-13T10:00:00+00:00', 'someone@example.invalid'),
    ].join('\n');
    assert.deepEqual(summarizeGitLog(shallow, new Set([sha('a')]), options), {
      lastAuthor: null,
      lastDate: null,
      lastSha: null,
      commits: null,
      authors: null,
    });
  });

  test('without isExcluded every author counts, and three-field lines still parse', () => {
    const summary = summarizeGitLog(log);
    assert.equal(summary.commits, 6);
    assert.equal(summary.lastAuthor, 'Claude');
    assert.equal('automation' in summary, false);
    assert.equal(summarizeGitLog(line(sha('a'), 'Ada', '2026-10-13T10:00:00+00:00'), new Set(), options).lastAuthor, 'Ada');
  });

  test('the plugin reads the author email from git (sha, name, date, email)', () => {
    const source = readFileSync(new URL('../src/plugins/page-meta/index.js', import.meta.url), 'utf8');
    assert.match(source, /'%H%x1f%an%x1f%aI%x1f%ae'/);
  });
});
