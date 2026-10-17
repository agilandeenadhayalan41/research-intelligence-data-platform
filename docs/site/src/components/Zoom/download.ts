// Browser side of the downloads: the original image file, and a Mermaid
// diagram as a standalone SVG or a high-resolution PNG. Only called from
// click handlers, never during render.

import {
  fileNameFor,
  parseViewBox,
  rasterSize,
  renameSvgId,
  svgDataUrl,
  svgToStandaloneString,
} from '@site/src/lib/download.mjs';
import type {Size} from '@site/src/lib/panzoom.mjs';

export type DownloadResult = {ok: true; fileName: string} | {ok: false; message: string};

export const SAVE_IMAGE_AS_NOTE = 'Use Save image as… to download';

/** Computed styles copied onto every SVG element of a diagram export. */
const SVG_PROPERTIES = [
  'fill',
  'fill-opacity',
  'fill-rule',
  'stroke',
  'stroke-width',
  'stroke-opacity',
  'stroke-dasharray',
  'stroke-dashoffset',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-miterlimit',
  'opacity',
  'color',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'letter-spacing',
  'text-anchor',
  'dominant-baseline',
  'text-decoration-line',
  'visibility',
];

/** Computed styles copied onto the HTML inside <foreignObject> labels. */
const HTML_PROPERTIES = [
  'display',
  'box-sizing',
  'color',
  'background-color',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'line-height',
  'letter-spacing',
  'text-align',
  'text-decoration-line',
  'white-space',
  'word-break',
  'overflow-wrap',
  'vertical-align',
  'max-width',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'opacity',
  'visibility',
];

const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';

function clickLink(href: string, fileName: string): void {
  const link = document.createElement('a');
  link.href = href;
  link.download = fileName;
  link.rel = 'noopener';
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/** Saves a Blob under a file name. */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  clickLink(url, fileName);
  // Revoked later: the download may start after this task.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * The bytes of a data: URL as a Blob, synchronously (so a new tab can still
 * be opened from the click that asked for it). Docusaurus inlines Markdown
 * images under 10 KB as data: URLs, and browsers do not open data: URLs in a
 * new tab, so the original is opened or saved through a blob: URL instead.
 */
function dataUrlToBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(',');
  const meta = dataUrl.slice(5, comma);
  const data = dataUrl.slice(comma + 1);
  const type = meta.split(';')[0] || 'application/octet-stream';
  if (/;base64$/i.test(meta)) {
    const binary = window.atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return new Blob([bytes], {type});
  }
  return new Blob([decodeURIComponent(data)], {type});
}

/**
 * Opens the original image in a new tab. Returns false when the browser
 * should follow the link itself (any URL but a data: URL).
 */
export function openOriginalImage(src: string): boolean {
  if (!/^data:/i.test(src)) {
    return false;
  }
  const url = URL.createObjectURL(dataUrlToBlob(src));
  window.open(url, '_blank', 'noopener');
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return true;
}

/**
 * Downloads the original image file (its src URL, never a resized copy). A
 * same-origin file goes through <a download> (an inlined data: URL is saved
 * from its bytes). A cross-origin file is fetched as a Blob when its server
 * allows CORS; otherwise it opens in a new tab and the result asks the
 * visitor to use "Save image as…".
 */
export async function downloadImage(src: string, alt: string): Promise<DownloadResult> {
  const url = new URL(src, window.location.href);
  const fileName = fileNameFor({src: url.href, alt});
  if (url.protocol === 'data:') {
    saveBlob(dataUrlToBlob(url.href), fileName);
    return {ok: true, fileName};
  }
  if (url.protocol === 'blob:' || url.origin === window.location.origin) {
    clickLink(url.href, fileName);
    return {ok: true, fileName};
  }
  try {
    const response = await fetch(url.href, {mode: 'cors', credentials: 'omit'});
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    saveBlob(await response.blob(), fileName);
    return {ok: true, fileName};
  } catch {
    window.open(url.href, '_blank', 'noopener,noreferrer');
    return {ok: false, message: SAVE_IMAGE_AS_NOTE};
  }
}

/** The diagram's natural size: its viewBox, or its rendered size. */
export function svgNaturalSize(svg: SVGSVGElement): Size | null {
  const box = parseViewBox(svg.getAttribute('viewBox'));
  if (box) {
    return {width: box.width, height: box.height};
  }
  const rect = svg.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 ? {width: rect.width, height: rect.height} : null;
}

/** Copies the computed styles of `source` and its descendants onto `copy` (a deep clone of it). */
function inlineComputedStyles(source: Element, copy: Element): void {
  const sources = [source, ...Array.from(source.querySelectorAll('*'))];
  const copies = [copy, ...Array.from(copy.querySelectorAll('*'))];
  if (sources.length !== copies.length) {
    return;
  }
  sources.forEach((element, index) => {
    const target = copies[index] as HTMLElement | SVGElement;
    const tag = element.tagName.toLowerCase();
    if (index === 0 || tag === 'style' || tag === 'title' || tag === 'desc' || tag === 'script') {
      return;
    }
    const computed = window.getComputedStyle(element);
    const properties = element.namespaceURI === SVG_NAMESPACE ? SVG_PROPERTIES : HTML_PROPERTIES;
    for (const property of properties) {
      const value = computed.getPropertyValue(property);
      if (value) {
        target.style.setProperty(property, value);
      }
    }
  });
}

/** A deep copy of the diagram with its computed styles inlined, so it looks the same anywhere. */
function styledClone(svg: SVGSVGElement): SVGSVGElement {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  inlineComputedStyles(svg, clone);
  return clone;
}

/**
 * Markup of a copy of the diagram for the lightbox: computed styles inlined
 * (it is shown outside the article, where the page's CSS does not reach) and
 * the ids renamed so they stay unique in the page.
 */
export function lightboxSvgMarkup(svg: SVGSVGElement, suffix: string): string {
  const clone = styledClone(svg);
  clone.removeAttribute('style');
  const markup = clone.outerHTML;
  return svg.id ? renameSvgId(markup, svg.id, `${svg.id}-${suffix}`) : markup;
}

/** The page background of the current colour mode: white, or the dark theme's background. */
export function themeBackground(): string {
  const root = document.documentElement;
  if (root.dataset.theme !== 'dark') {
    return '#ffffff';
  }
  const value = window.getComputedStyle(root).getPropertyValue('--ifm-background-color').trim();
  return value && value !== 'transparent' && /^[#a-zA-Z0-9(),.%\s-]+$/.test(value) ? value : '#1b1b1d';
}

/** The diagram as a standalone SVG file (vector, any size). */
export function diagramSvgString(svg: SVGSVGElement, {width, height}: Partial<Size> = {}): string {
  const clone = styledClone(svg);
  const markup = new XMLSerializer().serializeToString(clone);
  return svgToStandaloneString(markup, {width, height, background: themeBackground()});
}

async function loadImage(url: string): Promise<HTMLImageElement> {
  const image = new Image();
  image.decoding = 'async';
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error('The diagram could not be drawn.'));
    image.src = url;
  });
  return image;
}

/** Draws the standalone SVG on a canvas of `size` pixels and returns the PNG. */
async function svgToPng(svgString: string, size: Size, background: string): Promise<Blob> {
  // A data: URL, not a blob: URL: Chromium taints a canvas that draws a blob:
  // SVG with <foreignObject> labels, which would block the export.
  const image = await loadImage(svgDataUrl(svgString));
  const canvas = document.createElement('canvas');
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('Canvas is not available.');
  }
  context.fillStyle = background;
  context.fillRect(0, 0, size.width, size.height);
  context.drawImage(image, 0, 0, size.width, size.height);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('PNG export failed.'))), 'image/png');
  });
}

export interface DiagramDownload {
  svg: SVGSVGElement;
  format: 'svg' | 'png';
  /** Page path and 1-based diagram index, for the file name. */
  pagePath: string;
  index: number;
  /** Size of the diagram as displayed in the page (CSS px); the PNG is 3x that (at least 2000 px long). */
  displayed: Size;
}

/** Downloads a diagram as SVG (vector) or PNG (3x the displayed size). */
export async function downloadDiagram({svg, format, pagePath, index, displayed}: DiagramDownload): Promise<DownloadResult> {
  const fileName = fileNameFor({pagePath, index, extension: format});
  const natural = svgNaturalSize(svg) ?? displayed;
  try {
    if (format === 'svg') {
      const text = diagramSvgString(svg, natural);
      saveBlob(new Blob([text], {type: 'image/svg+xml;charset=utf-8'}), fileName);
      return {ok: true, fileName};
    }
    const size = rasterSize(displayed);
    const text = diagramSvgString(svg, {width: size.width, height: size.height});
    saveBlob(await svgToPng(text, size, themeBackground()), fileName);
    return {ok: true, fileName};
  } catch (error) {
    const reason = error instanceof Error && error.name === 'SecurityError' ? 'This browser blocks PNG export of this diagram.' : 'The export failed.';
    return {ok: false, message: format === 'png' ? `${reason} Download the SVG instead.` : 'The SVG export failed.'};
  }
}
