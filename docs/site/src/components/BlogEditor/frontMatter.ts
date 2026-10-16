// Front matter helpers for the blog editor. Pure functions, no browser APIs.
//
// The browser preview compiles the post with @mdx-js/mdx, which does not know
// about front matter: '---' would become a horizontal rule and the YAML a
// heading. So the editor reads the few fields the preview shows (title,
// description, date, tags) here, and hands the compiler the body with the
// front matter replaced by blank lines, which keeps the line numbers of
// compile errors the same as in the editor.

export interface PostFrontMatter {
  title: string | null;
  description: string | null;
  /** 'YYYY-MM-DD' when the date starts with a valid calendar day. */
  date: string | null;
  tags: string[];
}

export interface SplitSource {
  /** Null when the source has no front matter block. */
  frontMatter: PostFrontMatter | null;
  /** The source with the front matter lines blanked out (same line count). */
  body: string;
  /** Set when the source starts a front matter block that is never closed. */
  problem: string | null;
}

const OPEN = /^---[ \t]*$/;
const CLOSE = /^(?:---|\.\.\.)[ \t]*$/;
const KEY_VALUE = /^([A-Za-z_][\w-]*)[ \t]*:(?:[ \t]+(.*))?$/;
const LIST_ITEM = /^[ \t]*-[ \t]+(.*)$/;
const DAY = /^(\d{4})-(\d{2})-(\d{2})/;

export function splitFrontMatter(source: string): SplitSource {
  const text = source.startsWith('﻿') ? source.slice(1) : source;
  const lines = text.split('\n');
  if (lines.length === 0 || !OPEN.test(stripCr(lines[0]))) {
    return {frontMatter: null, body: text, problem: null};
  }
  let end = -1;
  for (let index = 1; index < lines.length; index += 1) {
    if (CLOSE.test(stripCr(lines[index]))) {
      end = index;
      break;
    }
  }
  if (end === -1) {
    return {
      frontMatter: null,
      body: text,
      problem: 'The front matter starts with "---" but has no closing "---" line.',
    };
  }
  const frontMatter = parseFields(lines.slice(1, end).map(stripCr));
  const body = '\n'.repeat(end + 1) + lines.slice(end + 1).join('\n');
  return {frontMatter, body, problem: null};
}

function parseFields(lines: string[]): PostFrontMatter {
  const result: PostFrontMatter = {title: null, description: null, date: null, tags: []};
  let listKey: string | null = null;
  for (const line of lines) {
    const item = LIST_ITEM.exec(line);
    if (item && listKey === 'tags') {
      const tag = scalar(item[1]);
      if (tag) {
        result.tags.push(tag);
      }
      continue;
    }
    const pair = KEY_VALUE.exec(line);
    if (!pair) {
      if (!/^[ \t]/.test(line)) {
        listKey = null;
      }
      continue;
    }
    const key = pair[1];
    const raw = (pair[2] ?? '').trim();
    listKey = raw === '' ? key : null;
    switch (key) {
      case 'title':
        result.title = scalar(raw) || null;
        break;
      case 'description':
        result.description = scalar(raw) || null;
        break;
      case 'date': {
        const match = DAY.exec(scalar(raw));
        result.date = match && isCalendarDay(match) ? match[0] : null;
        break;
      }
      case 'tags':
        if (raw.startsWith('[')) {
          result.tags = inlineList(raw);
        } else if (raw !== '') {
          const tag = scalar(raw);
          result.tags = tag ? [tag] : [];
        }
        break;
      default:
        break;
    }
  }
  return result;
}

/** A YAML scalar: quotes removed, a trailing comment dropped. */
function scalar(raw: string): string {
  const value = raw.trim();
  if (value.startsWith('"')) {
    const close = findClosingQuote(value);
    const inner = value.slice(1, close === -1 ? undefined : close);
    try {
      return (JSON.parse(`"${inner}"`) as string).trim();
    } catch {
      return inner.trim();
    }
  }
  if (value.startsWith("'")) {
    const close = value.indexOf("'", 1);
    return value.slice(1, close === -1 ? undefined : close).trim();
  }
  return value.replace(/[ \t]+#.*$/, '').trim();
}

function findClosingQuote(value: string): number {
  for (let index = 1; index < value.length; index += 1) {
    if (value[index] === '\\') {
      index += 1;
    } else if (value[index] === '"') {
      return index;
    }
  }
  return -1;
}

/** '[a, "b", c]' -> ['a', 'b', 'c']. Commas inside quotes are not handled. */
function inlineList(raw: string): string[] {
  const close = raw.lastIndexOf(']');
  const inner = raw.slice(1, close === -1 ? undefined : close);
  return inner
    .split(',')
    .map((part) => scalar(part))
    .filter((part) => part !== '');
}

function isCalendarDay(match: RegExpExecArray): boolean {
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  return check.getUTCMonth() === month - 1 && check.getUTCDate() === day;
}

function stripCr(line: string): string {
  return line.endsWith('\r') ? line.slice(0, -1) : line;
}

const MD_TRUNCATE = /^([ \t]*)<!--\s*truncate\s*-->[ \t]*$/gm;
const MDX_TRUNCATE = /^([ \t]*)\{\/\*\s*truncate\s*\*\/\}[ \t]*$/gm;

/**
 * Rewrites the blog truncate marker for the other format: an HTML comment is a
 * syntax error in MDX, and a JS comment is plain text in Markdown.
 */
export function convertTruncateMarker(source: string, to: 'md' | 'mdx'): string {
  return to === 'mdx'
    ? source.replace(MD_TRUNCATE, '$1{/* truncate */}')
    : source.replace(MDX_TRUNCATE, '$1<!-- truncate -->');
}
