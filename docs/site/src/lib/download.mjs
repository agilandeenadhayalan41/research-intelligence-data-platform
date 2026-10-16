// Download helpers for the image and diagram zoom: file names, the standalone
// SVG of a diagram, and the pixel size of its PNG export.
//
// Pure functions on strings and numbers: no DOM, no browser globals, so
// `node --test` can load them. The browser side (reading computed styles,
// drawing on a canvas, starting the download) is in
// src/components/Zoom/download.ts.

import {slugify} from './github.mjs';

/** PNG export: 3x the displayed size ... */
export const PNG_SCALE = 3;
/** ... and at least this many pixels on the longest side ... */
export const PNG_MIN_LONG_SIDE = 2000;
/** ... but never more than a browser canvas can hold. */
export const PNG_MAX_SIDE = 16384;
export const PNG_MAX_PIXELS = 16_777_216; // 4096 x 4096: the canvas area limit of Safari on iOS and iPadOS

const SVG_NS = 'http://www.w3.org/2000/svg';
const XLINK_NS = 'http://www.w3.org/1999/xlink';
const NAME_MAX_LENGTH = 100;

/** File extension (with the dot) of the image types a data: URL may carry. */
const EXTENSION_BY_MIME = /** @type {Record<string, string>} */ ({
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/gif': '.gif',
  'image/webp': '.webp',
  'image/avif': '.avif',
  'image/svg+xml': '.svg',
  'image/bmp': '.bmp',
});

// Docusaurus copies Markdown images to /assets/images/<name>-<contenthash>.<ext>.
const ASSET_HASH = /-[0-9a-f]{6,32}$/i;
const EXTENSION = /\.([A-Za-z0-9]{1,5})$/;
const SVG_OPEN_TAG = /^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!DOCTYPE[^>]*>\s*)?<svg\b((?:[^>"']|"[^"]*"|'[^']*')*)>/i;
const ATTRIBUTE = /([^\s=/>]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?/g;
const SAFE_COLOR = /^[#A-Za-z0-9(),.%\s-]{1,64}$/;

/**
 * @typedef {object} ImageNameInput
 * @property {string} src The image URL (absolute, root-relative, relative or data:).
 * @property {string} [alt] Alt text, used when the URL has no usable file name.
 */

/**
 * @typedef {object} DiagramNameInput
 * @property {string} pagePath The page's path, e.g. "/overview/how-it-works".
 * @property {number} index 1-based position of the diagram on the page.
 * @property {string} extension "svg" or "png" (with or without the dot).
 */

/**
 * A safe download file name.
 *
 * - Images ({src, alt}): the file name of the URL (decoded, without the query
 *   and the Docusaurus content hash), e.g. "/assets/images/flow-3f2a9c1b.png"
 *   gives "flow.png". When the URL has no usable name (a data: URL, a name
 *   with no letter), the alt text slugified plus the original extension;
 *   "image" plus the extension when there is no alt text either.
 * - Diagrams ({pagePath, index, extension}): the page slug, "diagram" and the
 *   index, e.g. "overview-how-it-works-diagram-2.svg" ("home-..." for "/").
 *
 * @param {ImageNameInput | DiagramNameInput} input
 * @returns {string}
 */
export function fileNameFor(input) {
  if ('src' in input && typeof input.src === 'string') {
    return imageFileName(input.src, input.alt ?? '');
  }
  if ('pagePath' in input) {
    return diagramFileName(input.pagePath, input.index, input.extension);
  }
  throw new TypeError('fileNameFor needs {src} or {pagePath, index, extension}');
}

/**
 * @param {string} src
 * @param {string} alt
 * @returns {string}
 */
function imageFileName(src, alt) {
  const {base, extension} = splitSrc(src);
  if (/\p{L}/u.test(base)) {
    return `${base}${extension}`;
  }
  return `${slugify(alt) || 'image'}${extension}`;
}

/**
 * The cleaned base name and the extension (".png", or '') of an image URL.
 *
 * @param {string} src
 * @returns {{base: string, extension: string}}
 */
function splitSrc(src) {
  const trimmed = src.trim();
  const data = /^data:([^;,]*)/i.exec(trimmed);
  if (data) {
    return {base: '', extension: EXTENSION_BY_MIME[data[1].toLowerCase()] ?? ''};
  }
  if (/^blob:/i.test(trimmed)) {
    return {base: '', extension: ''};
  }
  const path = trimmed.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/?#]*/i, '').replace(/[?#].*$/s, '');
  let segment = path.split('/').filter(Boolean).pop() ?? '';
  try {
    // An encoded slash or backslash must not smuggle a directory into the name.
    segment = decodeURIComponent(segment).split(/[\\/]/).pop() ?? '';
  } catch {
    // Keep the raw segment when it is not valid percent-encoding.
  }
  const match = EXTENSION.exec(segment);
  const extension = match ? `.${match[1].toLowerCase()}` : '';
  let base = match ? segment.slice(0, -match[0].length) : segment;
  if (/(?:^|\/)assets\//.test(path)) {
    base = base.replace(ASSET_HASH, '');
  }
  return {base: sanitizeBaseName(base), extension};
}

/**
 * Letters, digits, dots, underscores and single hyphens; no leading or
 * trailing dot or hyphen; at most 100 characters.
 *
 * @param {string} name
 * @returns {string}
 */
function sanitizeBaseName(name) {
  return name
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '')
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .slice(0, NAME_MAX_LENGTH)
    .replace(/^[-.]+|[-.]+$/g, '');
}

/**
 * @param {string} pagePath
 * @param {number} index
 * @param {string} extension
 * @returns {string}
 */
function diagramFileName(pagePath, index, extension) {
  const ext = String(extension ?? '').replace(/^\./, '').toLowerCase();
  if (!/^[a-z0-9]{1,5}$/.test(ext)) {
    throw new RangeError(`invalid extension: ${extension}`);
  }
  if (!Number.isInteger(index) || index < 1) {
    throw new RangeError(`invalid diagram index: ${index}`);
  }
  const path = String(pagePath ?? '').replace(/[?#].*$/s, '');
  const slug = slugify(path.replace(/\//g, ' ')) || 'home';
  return `${slug}-diagram-${index}.${ext}`;
}

/**
 * @typedef {object} ViewBox
 * @property {number} x
 * @property {number} y
 * @property {number} width
 * @property {number} height
 */

/**
 * Parses an SVG viewBox ("minX minY width height", spaces or commas). null
 * when it is missing or has no positive size.
 *
 * @param {string | null | undefined} value
 * @returns {ViewBox | null}
 */
export function parseViewBox(value) {
  if (typeof value !== 'string') {
    return null;
  }
  const parts = value.trim().split(/[\s,]+/).map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) {
    return null;
  }
  const [x, y, width, height] = parts;
  return width > 0 && height > 0 ? {x, y, width, height} : null;
}

/**
 * @typedef {object} StandaloneOptions
 * @property {number} [width] Width (px) written on the root element; default: the viewBox width.
 * @property {number} [height] Height (px); default: the viewBox height.
 * @property {string} [background] A CSS colour painted behind the drawing (a rect covering the viewBox).
 */

/**
 * A standalone SVG file from the markup of an inline <svg> (serialized with
 * XMLSerializer, after its computed styles were inlined): an XML declaration,
 * the SVG (and, when used, XLink) namespace, an explicit pixel width and
 * height in place of the page's "100%" and max-width, a viewBox, and an
 * optional background rect so a dark-theme diagram stays readable in any
 * viewer. The drawing itself is not changed.
 *
 * @param {string} markup
 * @param {StandaloneOptions} [options]
 * @returns {string}
 */
export function svgToStandaloneString(markup, {width, height, background} = {}) {
  if (typeof markup !== 'string') {
    throw new TypeError('svg markup must be a string');
  }
  const open = SVG_OPEN_TAG.exec(markup);
  if (!open) {
    throw new Error('not an <svg> element');
  }
  let rawAttributes = open[1];
  const selfClosing = /\/\s*$/.test(rawAttributes);
  if (selfClosing) {
    rawAttributes = rawAttributes.replace(/\/\s*$/, '');
  }
  const attributes = parseAttributes(rawAttributes);
  const get = (/** @type {string} */ name) => attributes.find(([key]) => key === name)?.[1] ?? null;

  let viewBox = parseViewBox(get('viewBox'));
  if (!viewBox) {
    const w = parseFloat(get('width') ?? '');
    const h = parseFloat(get('height') ?? '');
    if (w > 0 && h > 0 && !/%/.test(`${get('width')}${get('height')}`)) {
      viewBox = {x: 0, y: 0, width: w, height: h};
    }
  }
  const outWidth = positiveOr(width, viewBox?.width);
  const outHeight = positiveOr(height, viewBox?.height);

  const style = (get('style') ?? '')
    .split(';')
    .map((declaration) => declaration.trim())
    .filter((declaration) => declaration && !/^(?:max-|min-)?(?:width|height)\s*:/i.test(declaration))
    .join('; ');

  /** @type {Array<[string, string | null]>} */
  const kept = attributes.filter(
    ([name]) => !['width', 'height', 'style', 'xmlns', 'xmlns:xlink', 'version', 'viewBox'].includes(name),
  );
  /** @type {Array<[string, string | null]>} */
  const result = [
    ['xmlns', SVG_NS],
    ...(/\bxlink:/.test(markup) ? /** @type {Array<[string, string]>} */ ([['xmlns:xlink', XLINK_NS]]) : []),
    ['version', '1.1'],
    ...kept,
  ];
  if (outWidth !== null && outHeight !== null) {
    result.push(['width', formatNumber(outWidth)], ['height', formatNumber(outHeight)]);
  }
  if (viewBox) {
    result.push(['viewBox', [viewBox.x, viewBox.y, viewBox.width, viewBox.height].map(formatNumber).join(' ')]);
  }
  if (style) {
    result.push(['style', style]);
  }

  const openTag = `<svg ${result
    .map(([name, value]) => (value === null ? name : `${name}="${escapeAttribute(value)}"`))
    .join(' ')}>`;
  const box = viewBox ?? (outWidth !== null && outHeight !== null ? {x: 0, y: 0, width: outWidth, height: outHeight} : null);
  const backdrop =
    background && SAFE_COLOR.test(background) && box
      ? `<rect x="${formatNumber(box.x)}" y="${formatNumber(box.y)}" width="${formatNumber(box.width)}" height="${formatNumber(
          box.height,
        )}" fill="${escapeAttribute(background.trim())}"/>`
      : '';
  const rest = markup.slice(open.index + open[0].length);
  const body = selfClosing ? `${backdrop}</svg>` : `${backdrop}${rest.trim()}`;
  return `<?xml version="1.0" encoding="UTF-8"?>\n${openTag}${body}\n`;
}

/**
 * @param {string} raw
 * @returns {Array<[string, string | null]>}
 */
function parseAttributes(raw) {
  /** @type {Array<[string, string | null]>} */
  const attributes = [];
  for (const match of raw.matchAll(ATTRIBUTE)) {
    const value = match[2];
    attributes.push([
      match[1],
      value === undefined ? null : value.replace(/^(["'])([\s\S]*)\1$/, '$2').replace(/&quot;/g, '"').replace(/&amp;/g, '&'),
    ]);
  }
  return attributes;
}

/**
 * @param {string} value
 * @returns {string}
 */
function escapeAttribute(value) {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

/**
 * @param {number | undefined} value
 * @param {number | undefined} fallback
 * @returns {number | null}
 */
function positiveOr(value, fallback) {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return value;
  }
  return typeof fallback === 'number' && Number.isFinite(fallback) && fallback > 0 ? fallback : null;
}

/**
 * @param {number} value
 * @returns {string}
 */
function formatNumber(value) {
  return String(Math.round(value * 100) / 100);
}

/**
 * Every occurrence of an SVG's id in its markup replaced by another, so a
 * copy of an inline diagram (whose element ids, CSS rules and url(#...)
 * references all start with the root id, as Mermaid writes them) can sit in
 * the same page without duplicate ids.
 *
 * @param {string} markup
 * @param {string} oldId
 * @param {string} newId
 * @returns {string}
 */
export function renameSvgId(markup, oldId, newId) {
  if (!oldId) {
    return markup;
  }
  return markup.split(oldId).join(newId);
}

/**
 * A data: URL for an SVG string. A data: URL (not a blob: URL) keeps a canvas
 * that draws it exportable in Chromium when the SVG contains foreignObject
 * labels, as Mermaid flowcharts do.
 *
 * @param {string} svg
 * @returns {string}
 */
export function svgDataUrl(svg) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/**
 * @typedef {object} RasterOptions
 * @property {number} [scale] Default 3.
 * @property {number} [minLongSide] Default 2000.
 * @property {number} [maxSide] Default 16384.
 * @property {number} [maxPixels] Default 16.7 million (4096 x 4096).
 */

/**
 * Pixel size of the PNG export of a diagram displayed at `size` CSS pixels:
 * 3x the displayed size, raised so the longest side is at least 2000 px, and
 * lowered when needed to stay within the canvas limits (16384 px a side, 16.7
 * million pixels, the iOS Safari limit). It does not depend on
 * devicePixelRatio, so every screen gets the same file.
 *
 * @param {{width: number, height: number}} size
 * @param {RasterOptions} [options]
 * @returns {{width: number, height: number, scale: number}}
 */
export function rasterSize(
  size,
  {
    scale = PNG_SCALE,
    minLongSide = PNG_MIN_LONG_SIDE,
    maxSide = PNG_MAX_SIDE,
    maxPixels = PNG_MAX_PIXELS,
  } = {},
) {
  if (!size || !(size.width > 0) || !(size.height > 0) || !Number.isFinite(size.width) || !Number.isFinite(size.height)) {
    throw new RangeError('size must have a positive width and height');
  }
  const longSide = Math.max(size.width, size.height);
  let factor = Math.max(scale, minLongSide / longSide);
  factor = Math.min(factor, maxSide / longSide, Math.sqrt(maxPixels / (size.width * size.height)));
  let width = Math.round(size.width * factor);
  let height = Math.round(size.height * factor);
  if (width * height > maxPixels || width > maxSide || height > maxSide) {
    // Rounding up went past a limit (a canvas that size would fail): round down.
    width = Math.floor(size.width * factor);
    height = Math.floor(size.height * factor);
  }
  return {width: Math.max(1, width), height: Math.max(1, height), scale: factor};
}
