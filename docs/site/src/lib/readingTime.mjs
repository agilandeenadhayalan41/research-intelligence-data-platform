// Reading-time estimate for a Markdown or MDX source file.
//
// Used at build time by the page-meta plugin and in the browser by the blog
// editor, so it is plain ESM with no imports and no browser globals.
//
// What counts:
// - prose words (a word is a whitespace-separated token with at least one
//   letter or digit, so Markdown markers such as "#", "-", "|", "---" or an
//   ordered-list "1." do not count; each Chinese/Japanese character counts as
//   one word);
// - words inside fenced code blocks (``` or ~~~) at one third of their weight,
//   because readers skim code.
// What is ignored: the front matter, HTML and MDX comments, link and image
// URLs (the link text and alt text still count), reference-link definitions,
// autolinks and bare URLs, HTML/JSX tags (their text content still counts)
// and MDX import/export lines.

const DEFAULT_WORDS_PER_MINUTE = 200;
const CODE_WEIGHT = 1 / 3;

const FRONT_MATTER = /^﻿?---[ \t]*\n(?:[\s\S]*?\n)?---[ \t]*(?:\n|$)/;
const FENCE_OPEN = /^[ \t]*(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^[ \t]*(`{3,}|~{3,})[ \t]*$/;

const HTML_COMMENT = /<!--[\s\S]*?-->/g;
const MDX_COMMENT = /\{\/\*[\s\S]*?\*\/\}/g;
const ESM_IMPORT = /^import\s+(?:[\w*${}\s,]+\s+from\s+)?['"][^'"\n]+['"];?[ \t]*$/gm;
const ESM_EXPORT = /^export\s+(?:const|let|var|function|class|default|\{)[^\n]*$/gm;
const REFERENCE_DEFINITION = /^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*\S+(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\)))?[ \t]*$/gm;
// "](url)" or "](<url> "title")" -> "]", keeping the [link text].
const INLINE_LINK_TARGET =
  /\]\(\s*(?:<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\)))?\s*\)/g;
const REFERENCE_LINK_LABEL = /\]\[[^\]\n]*\]/g;
const AUTOLINK = /<[A-Za-z][A-Za-z0-9+.-]*:[^<>\s]*>/g;
const BARE_URL = /\b(?:https?:\/\/|www\.)[^\s<>()\[\]]+/gi;
const TAG = /<\/?[A-Za-z][\w.:-]*(?:\s[^<>]*)?\/?>/g;
const ORDERED_LIST_MARKER = /^([ \t>]*)\d{1,9}[.)](?=[ \t])/gm;

const CJK_CHARACTER = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu;
const HAS_LETTER_OR_DIGIT = /[\p{L}\p{N}]/u;

/**
 * @typedef {object} ReadingTime
 * @property {number} minutes Whole minutes, at least 1.
 * @property {number} words Weighted word count (code words count 1/3).
 */

/**
 * @param {string} markdownSource Raw file content, front matter included.
 * @param {{wordsPerMinute?: number}} [options]
 * @returns {ReadingTime}
 */
export function readingTime(markdownSource, {wordsPerMinute = DEFAULT_WORDS_PER_MINUTE} = {}) {
  if (typeof markdownSource !== 'string') {
    throw new TypeError('markdownSource must be a string');
  }
  if (!(typeof wordsPerMinute === 'number' && Number.isFinite(wordsPerMinute) && wordsPerMinute > 0)) {
    throw new RangeError('wordsPerMinute must be a positive number');
  }
  const text = markdownSource.replace(/\r\n?/g, '\n').replace(FRONT_MATTER, '');
  const {prose, code} = splitFencedCode(text);
  const words = countWords(cleanProse(prose)) + Math.round(countWords(code) * CODE_WEIGHT);
  const minutes = Math.max(1, Math.ceil(words / wordsPerMinute));
  return {minutes, words};
}

/**
 * Splits the text into prose and the content of fenced code blocks (the
 * fence lines and their info strings belong to neither). An unclosed fence
 * runs to the end of the document, as in CommonMark.
 *
 * @param {string} text
 * @returns {{prose: string, code: string}}
 */
function splitFencedCode(text) {
  /** @type {string[]} */
  const prose = [];
  /** @type {string[]} */
  const code = [];
  /** @type {{char: string, length: number} | null} */
  let fence = null;

  for (const line of text.split('\n')) {
    if (fence) {
      const close = FENCE_CLOSE.exec(line);
      if (close && close[1][0] === fence.char && close[1].length >= fence.length) {
        fence = null;
      } else {
        code.push(line);
      }
      continue;
    }
    const open = FENCE_OPEN.exec(line);
    // A backtick fence's info string may not contain backticks (that is
    // inline code such as ```x```).
    if (open && !(open[1][0] === '`' && open[2].includes('`'))) {
      fence = {char: open[1][0], length: open[1].length};
      continue;
    }
    prose.push(line);
  }
  return {prose: prose.join('\n'), code: code.join('\n')};
}

/**
 * @param {string} text
 * @returns {string}
 */
function cleanProse(text) {
  return text
    .replace(HTML_COMMENT, ' ')
    .replace(MDX_COMMENT, ' ')
    .replace(ESM_IMPORT, ' ')
    .replace(ESM_EXPORT, ' ')
    .replace(REFERENCE_DEFINITION, ' ')
    .replace(INLINE_LINK_TARGET, '] ')
    .replace(REFERENCE_LINK_LABEL, '] ')
    .replace(AUTOLINK, ' ')
    .replace(BARE_URL, ' ')
    .replace(TAG, ' ')
    .replace(ORDERED_LIST_MARKER, '$1');
}

/**
 * @param {string} text
 * @returns {number}
 */
function countWords(text) {
  const cjk = text.match(CJK_CHARACTER);
  let count = cjk ? cjk.length : 0;
  for (const token of text.replace(CJK_CHARACTER, ' ').split(/\s+/)) {
    if (HAS_LETTER_OR_DIGIT.test(token)) {
      count += 1;
    }
  }
  return count;
}
