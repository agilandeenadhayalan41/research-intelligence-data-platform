import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {describe, test} from 'node:test';

import {readingTime} from '../src/lib/readingTime.mjs';

const words = (count, word = 'word') => Array.from({length: count}, () => word).join(' ');

describe('readingTime', () => {
  test('an empty document is one minute and zero words', () => {
    assert.deepEqual(readingTime(''), {minutes: 1, words: 0});
    assert.deepEqual(readingTime('   \n\n'), {minutes: 1, words: 0});
  });

  test('minutes = max(1, ceil(words / 200))', () => {
    assert.deepEqual(readingTime(words(1)), {minutes: 1, words: 1});
    assert.deepEqual(readingTime(words(200)), {minutes: 1, words: 200});
    assert.deepEqual(readingTime(words(201)), {minutes: 2, words: 201});
    assert.deepEqual(readingTime(words(400)), {minutes: 2, words: 400});
    assert.deepEqual(readingTime(words(1001)), {minutes: 6, words: 1001});
  });

  test('wordsPerMinute can be changed and is validated', () => {
    assert.deepEqual(readingTime(words(300), {wordsPerMinute: 100}), {minutes: 3, words: 300});
    assert.deepEqual(readingTime(words(300), {}), {minutes: 2, words: 300});
    assert.throws(() => readingTime('x', {wordsPerMinute: 0}), RangeError);
    assert.throws(() => readingTime('x', {wordsPerMinute: -5}), RangeError);
    assert.throws(() => readingTime('x', {wordsPerMinute: Number.NaN}), RangeError);
    assert.throws(() => readingTime('x', {wordsPerMinute: '200'}), RangeError);
  });

  test('the source must be a string', () => {
    assert.throws(() => readingTime(undefined), TypeError);
    assert.throws(() => readingTime(null), TypeError);
    assert.throws(() => readingTime(42), TypeError);
  });

  test('strips YAML front matter (LF, CRLF, BOM, empty)', () => {
    const body = 'one two three';
    assert.equal(readingTime(`---\ntitle: Many words in the title here\ntags: [a, b]\n---\n${body}`).words, 3);
    assert.equal(readingTime(`---\r\ntitle: Words here\r\n---\r\n${body}`).words, 3);
    assert.equal(readingTime(`﻿---\ntitle: Words here\n---\n${body}`).words, 3);
    assert.equal(readingTime(`---\n---\n${body}`).words, 3);
    // A thematic break later in the document is not front matter.
    assert.equal(readingTime(`${body}\n\n---\n\nfour five`).words, 5);
  });

  test('strips HTML comments, including multi-line ones, and MDX comments', () => {
    assert.equal(readingTime('before <!-- hidden words here --> after').words, 2);
    assert.equal(readingTime('before\n<!--\nhidden\nwords\n-->\nafter').words, 2);
    assert.equal(readingTime('before {/* hidden words */} after').words, 2);
    assert.equal(readingTime('intro\n\n<!-- truncate -->\n\nrest').words, 2);
  });

  test('strips link and image URLs but keeps their text', () => {
    assert.equal(readingTime('Read [the docs](https://example.com/a/b/c?x=1#y) now').words, 4);
    assert.equal(readingTime('See [the guide](../guide/start.md "Getting started") today').words, 4);
    assert.equal(readingTime('A link [with parens](https://en.wikipedia.org/wiki/Foo_(bar)) ok').words, 5);
    assert.equal(readingTime('An image ![alt text](img/logo.svg) here').words, 5);
    assert.equal(readingTime('Use [docs][ref] please\n\n[ref]: https://example.com/long/url "Title"').words, 3);
    assert.equal(readingTime('Visit <https://example.com/path> or https://example.org/x/y today').words, 3);
    assert.equal(readingTime('Link [text](<url with spaces.md>) end').words, 3);
  });

  test('counts fenced code at one third of its weight', () => {
    const code = words(30, 'token');
    assert.equal(readingTime(`intro\n\n\`\`\`js\n${code}\n\`\`\`\n`).words, 1 + 10);
    assert.equal(readingTime(`~~~\n${code}\n~~~`).words, 10);
    assert.equal(readingTime(`\`\`\`\n${words(3, 'x')}\n\`\`\``).words, 1);
    assert.equal(readingTime(`\`\`\`\n${words(1, 'x')}\n\`\`\``).words, 0);
    // The info string is not counted.
    assert.equal(readingTime('```mermaid title="Some long title here"\n```').words, 0);
  });

  test('handles nested and unclosed fences like CommonMark', () => {
    // A ```` fence is only closed by 4+ backticks; the inner ``` lines are code.
    const nested = ['````md', '```js', 'a b c', '```', '````', 'after'].join('\n');
    assert.equal(readingTime(nested).words, 1 + 1);
    // ~~~ does not close a ``` fence.
    const mixed = ['```', 'a b c', '~~~', 'd e f', '```', 'after'].join('\n');
    assert.equal(readingTime(mixed).words, 1 + 2);
    // An unclosed fence runs to the end of the document.
    assert.equal(readingTime(`prose here\n\`\`\`\n${words(9, 'x')}`).words, 2 + 3);
    // Inline triple backticks with backticks in the "info string" are not a fence.
    assert.equal(readingTime('```inline``` words').words, 2);
    // Indented fences (inside list items) are recognised.
    assert.equal(readingTime(`- item\n\n    \`\`\`\n    ${words(6, 'x')}\n    \`\`\``).words, 1 + 2);
  });

  test('comments inside code are code, not comments', () => {
    assert.equal(readingTime('```html\n<!-- a b c -->\n```').words, 1);
  });

  test('does not count Markdown markers', () => {
    const source = [
      '# Heading words',
      '',
      '- first item',
      '* second item',
      '1. third item',
      '',
      '> quoted text',
      '',
      '---',
      '',
      '| col a | col b |',
      '| ----- | ----- |',
      '| 1 | 2 |',
      '',
      '**bold** and _italic_ and `code`',
    ].join('\n');
    assert.equal(readingTime(source).words, 2 + 2 + 2 + 2 + 2 + 4 + 2 + 5);
  });

  test('strips MDX imports/exports and JSX/HTML tags but keeps their text', () => {
    const source = [
      "import Tabs from '@theme/Tabs';",
      "import {Foo, Bar} from '../components/Foo';",
      "import '../styles.css';",
      'export const meta = {draft: true};',
      '',
      '<Tabs groupId="lang">',
      '  <TabItem value="py" label="Python">Hello there</TabItem>',
      '</Tabs>',
      '',
      '<div className="card"><p>Inside a card</p></div>',
      '',
      'We import data and export results.',
    ].join('\n');
    assert.equal(readingTime(source).words, 2 + 3 + 6);
  });

  test('counts each Chinese or Japanese character as a word', () => {
    assert.equal(readingTime('数据平台').words, 4);
    assert.equal(readingTime('データ platform').words, 4);
  });

  test('a realistic document', () => {
    const source = [
      '---',
      'title: Introduction',
      'description: What this is',
      '---',
      '',
      '# Introduction',
      '',
      words(395),
      '',
      '```sql',
      words(30, 'select'),
      '```',
    ].join('\n');
    // 1 heading word + 395 prose words + 30/3 code words.
    assert.deepEqual(readingTime(source), {minutes: 3, words: 406});
  });

  test('works on a real doc in this repository', () => {
    const intro = readFileSync(new URL('../../overview/introduction.md', import.meta.url), 'utf8');
    const result = readingTime(intro);
    assert.ok(result.words > 50, `expected some words, got ${result.words}`);
    assert.equal(result.minutes, Math.max(1, Math.ceil(result.words / 200)));
  });
});
