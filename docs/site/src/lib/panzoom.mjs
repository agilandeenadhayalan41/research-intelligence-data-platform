// Pan and zoom math for the image and diagram zoom (the lightbox and the
// in-page zoom). Pure functions on plain numbers: no DOM, no browser globals,
// so `node --test` can load it and the server render never touches it.
//
// Model. The content (an image or a diagram) has a natural size in CSS pixels
// ({width, height}: the image's natural size, the diagram's viewBox size). It
// is drawn inside a viewport (the full-screen overlay, or the element's own
// box in the page) at `scale`, with its top-left corner at (x, y) in viewport
// pixels:
//
//   screen point = (x + scale * u, y + scale * v)   for a content point (u, v)
//
// So `scale` 1 is the actual size (100 %), and the content is rendered by
// giving it the size (width * scale, height * scale) and translating it by
// (x, y). Sizing instead of a CSS scale() keeps vector diagrams crisp.

/** Zoom factor of one zoom-in or zoom-out step (buttons and + / - keys). */
export const ZOOM_STEP = 1.25;
/** Arrow-key pan step in pixels, and with Shift. */
export const PAN_STEP = 40;
export const PAN_STEP_LARGE = 200;
/** A pointer that moves less than this (pixels) is a click or a tap, not a drag. */
export const DRAG_THRESHOLD = 4;
/** Two taps closer than this in time (ms) and space (px) are a double tap. */
export const DOUBLE_TAP_MS = 300;
export const DOUBLE_TAP_DISTANCE = 24;
/** Zoom limits relative to the fit scale: 0.5x to 8x. */
export const MIN_FIT_FACTOR = 0.5;
export const MAX_FIT_FACTOR = 8;
/** Images may also zoom to 4x their natural size when that is larger than 8x fit. */
export const IMAGE_MAX_NATURAL_SCALE = 4;

/**
 * @typedef {object} Size
 * @property {number} width
 * @property {number} height
 */

/**
 * @typedef {object} Point
 * @property {number} x
 * @property {number} y
 */

/**
 * @typedef {object} View
 * @property {number} scale Content pixels to viewport pixels; 1 is the actual size.
 * @property {number} x Left edge of the content in the viewport, in pixels.
 * @property {number} y Top edge of the content in the viewport, in pixels.
 */

/**
 * @typedef {object} Limits
 * @property {number} min Smallest scale.
 * @property {number} max Largest scale.
 */

/**
 * @typedef {object} ViewContext
 * @property {Size} content Natural size of the content.
 * @property {Size} viewport Size of the viewport.
 * @property {Limits} limits
 * @property {number} [margin] How far (px) an edge of content larger than the viewport may move inside it.
 */

/**
 * `value` limited to [min, max]. When min > max, max wins.
 *
 * @param {number} value
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

/**
 * True when `size` has a finite, positive width and height.
 *
 * @param {Size | null | undefined} size
 * @returns {size is Size}
 */
export function isValidSize(size) {
  return Boolean(
    size &&
      Number.isFinite(size.width) &&
      Number.isFinite(size.height) &&
      size.width > 0 &&
      size.height > 0,
  );
}

/**
 * The scale at which the whole content fits in the viewport, `padding` pixels
 * from each edge, and never above `maxScale` (1 keeps a bitmap from being
 * enlarged past its natural size). 1 when a size is unusable.
 *
 * @param {Size} content
 * @param {Size} viewport
 * @param {{padding?: number, maxScale?: number}} [options]
 * @returns {number}
 */
export function fitScale(content, viewport, {padding = 0, maxScale = Infinity} = {}) {
  if (!isValidSize(content) || !isValidSize(viewport)) {
    return 1;
  }
  const width = Math.max(1, viewport.width - 2 * padding);
  const height = Math.max(1, viewport.height - 2 * padding);
  return Math.min(width / content.width, height / content.height, maxScale);
}

/**
 * Zoom limits for a given fit scale: from 0.5x to 8x the fit scale. For
 * images `naturalMax` is 4, so a small fit can still reach 4x the natural
 * size. The range always contains 1 (actual size) and the fit scale, so the
 * fit and 100 % controls always work.
 *
 * @param {number} fit
 * @param {{naturalMax?: number}} [options]
 * @returns {Limits}
 */
export function zoomLimits(fit, {naturalMax = 1} = {}) {
  const base = Number.isFinite(fit) && fit > 0 ? fit : 1;
  return {
    min: Math.min(base * MIN_FIT_FACTOR, 1),
    max: Math.max(base * MAX_FIT_FACTOR, naturalMax, 1),
  };
}

/**
 * The view that shows the content at `scale`, centred in the viewport.
 *
 * @param {number} scale
 * @param {Size} content
 * @param {Size} viewport
 * @returns {View}
 */
export function centerView(scale, content, viewport) {
  return {
    scale,
    x: (viewport.width - content.width * scale) / 2,
    y: (viewport.height - content.height * scale) / 2,
  };
}

/**
 * The fit-to-viewport view: the fit scale, centred.
 *
 * @param {Size} content
 * @param {Size} viewport
 * @param {{padding?: number, maxScale?: number}} [options]
 * @returns {View}
 */
export function fitView(content, viewport, options) {
  return centerView(fitScale(content, viewport, options), content, viewport);
}

/**
 * Keeps the content from being lost off-screen. On each axis, content that is
 * smaller than the viewport (less 2 * margin) is centred; larger content must
 * cover the viewport, except that each edge may come up to `margin` pixels
 * inside it.
 *
 * @param {View} view
 * @param {Size} content
 * @param {Size} viewport
 * @param {{margin?: number}} [options]
 * @returns {View}
 */
export function clampView(view, content, viewport, {margin = 0} = {}) {
  const width = content.width * view.scale;
  const height = content.height * view.scale;
  const axis = (/** @type {number} */ position, /** @type {number} */ size, /** @type {number} */ room) =>
    size <= room - 2 * margin ? (room - size) / 2 : clamp(position, room - size - margin, margin);
  return {
    scale: view.scale,
    x: axis(view.x, width, viewport.width),
    y: axis(view.y, height, viewport.height),
  };
}

/**
 * Zooms by `factor` around `point` (viewport pixels): the content point under
 * `point` stays under it, as when zooming at the mouse cursor. The scale is
 * limited to `limits` and the result is clamped (see clampView).
 *
 * @param {View} view
 * @param {Point} point
 * @param {number} factor
 * @param {ViewContext} context
 * @returns {View}
 */
export function zoomAt(view, point, factor, {content, viewport, limits, margin = 0}) {
  const safeFactor = Number.isFinite(factor) && factor > 0 ? factor : 1;
  const scale = clamp(view.scale * safeFactor, limits.min, limits.max);
  const ratio = scale / view.scale;
  return clampView(
    {
      scale,
      x: point.x - (point.x - view.x) * ratio,
      y: point.y - (point.y - view.y) * ratio,
    },
    content,
    viewport,
    {margin},
  );
}

/**
 * Zooms to an absolute `scale` around `point` (default: the viewport centre).
 *
 * @param {View} view
 * @param {number} scale
 * @param {ViewContext} context
 * @param {Point} [point]
 * @returns {View}
 */
export function zoomTo(view, scale, context, point) {
  const at = point ?? {x: context.viewport.width / 2, y: context.viewport.height / 2};
  return zoomAt(view, at, scale / view.scale, context);
}

/**
 * Moves the content by (dx, dy) pixels, clamped (see clampView).
 *
 * @param {View} view
 * @param {number} dx
 * @param {number} dy
 * @param {ViewContext} context
 * @returns {View}
 */
export function panBy(view, dx, dy, {content, viewport, margin = 0}) {
  return clampView({scale: view.scale, x: view.x + dx, y: view.y + dy}, content, viewport, {margin});
}

/**
 * True when two scales are equal within a relative tolerance.
 *
 * @param {number} a
 * @param {number} b
 * @param {number} [tolerance]
 * @returns {boolean}
 */
export function sameScale(a, b, tolerance = 1e-3) {
  return Math.abs(a - b) <= tolerance * Math.max(Math.abs(a), Math.abs(b), 1e-9);
}

/**
 * Target scale of a double click or double tap: from the fit scale to 2x the
 * fit scale, and from any other scale back to the fit scale.
 *
 * @param {number} scale Current scale.
 * @param {number} fit
 * @param {Limits} limits
 * @returns {number}
 */
export function toggleScale(scale, fit, limits) {
  return sameScale(scale, fit) ? clamp(fit * 2, limits.min, limits.max) : fit;
}

/**
 * Zoom factor of one wheel event. A trackpad pinch arrives as a wheel event
 * with ctrlKey set and small deltas, so it uses a stronger rate. deltaMode 1
 * (lines) and 2 (pages) are converted to pixels. Limited to [0.5, 2] per event.
 *
 * @param {{deltaY: number, deltaMode?: number, ctrlKey?: boolean}} event
 * @param {{pageHeight?: number}} [options]
 * @returns {number}
 */
export function wheelZoomFactor({deltaY, deltaMode = 0, ctrlKey = false}, {pageHeight = 800} = {}) {
  if (!Number.isFinite(deltaY) || deltaY === 0) {
    return 1;
  }
  const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? pageHeight : 1);
  const rate = ctrlKey ? 0.01 : 0.002;
  return clamp(Math.exp(-pixels * rate), 0.5, 2);
}

/**
 * @param {Point} a
 * @param {Point} b
 * @returns {number}
 */
export function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * @param {Point} a
 * @param {Point} b
 * @returns {Point}
 */
export function midpoint(a, b) {
  return {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
}

/**
 * The view after a two-finger pinch moved from (a0, b0) to (a1, b1): zoomed by
 * the change in finger distance around the new midpoint, and panned by the
 * movement of the midpoint.
 *
 * @param {View} view
 * @param {[Point, Point]} before
 * @param {[Point, Point]} after
 * @param {ViewContext} context
 * @returns {View}
 */
export function pinchView(view, before, after, context) {
  const startDistance = distance(before[0], before[1]);
  const endDistance = distance(after[0], after[1]);
  const startCenter = midpoint(before[0], before[1]);
  const endCenter = midpoint(after[0], after[1]);
  const panned = panBy(view, endCenter.x - startCenter.x, endCenter.y - startCenter.y, context);
  const factor = startDistance > 0 ? endDistance / startDistance : 1;
  return zoomAt(panned, endCenter, factor, context);
}

/**
 * True when the point (viewport pixels) is on the content.
 *
 * @param {Point} point
 * @param {View} view
 * @param {Size} content
 * @returns {boolean}
 */
export function isPointOnContent(point, view, content) {
  return (
    point.x >= view.x &&
    point.y >= view.y &&
    point.x <= view.x + content.width * view.scale &&
    point.y <= view.y + content.height * view.scale
  );
}

/**
 * True when the second tap completes a double tap.
 *
 * @param {{time: number, x: number, y: number} | null} previous
 * @param {{time: number, x: number, y: number}} next
 * @returns {boolean}
 */
export function isDoubleTap(previous, next) {
  return Boolean(
    previous &&
      next.time - previous.time >= 0 &&
      next.time - previous.time <= DOUBLE_TAP_MS &&
      distance(previous, next) <= DOUBLE_TAP_DISTANCE,
  );
}

/**
 * @typedef {{type: 'zoom', factor: number}
 *   | {type: 'pan', dx: number, dy: number}
 *   | {type: 'fit'}
 *   | {type: 'actual'}
 *   | {type: 'close'}} KeyAction
 */

/**
 * The zoom action of a key: + / = zoom in, - zooms out, 0 fits, 1 is the
 * actual size, the arrows pan (Shift: bigger steps) and Escape closes. An
 * arrow moves the view that way, so the content moves the other way. null
 * for any other key, or when Ctrl, Alt or Meta is held (browser shortcuts).
 *
 * @param {{key: string, shiftKey?: boolean, ctrlKey?: boolean, altKey?: boolean, metaKey?: boolean}} event
 * @returns {KeyAction | null}
 */
export function keyAction({key, shiftKey = false, ctrlKey = false, altKey = false, metaKey = false}) {
  if (key === 'Escape') {
    return {type: 'close'};
  }
  if (ctrlKey || altKey || metaKey) {
    return null;
  }
  const step = shiftKey ? PAN_STEP_LARGE : PAN_STEP;
  switch (key) {
    case '+':
    case '=':
      return {type: 'zoom', factor: ZOOM_STEP};
    case '-':
    case '_':
    case '−':
      return {type: 'zoom', factor: 1 / ZOOM_STEP};
    case '0':
      return {type: 'fit'};
    case '1':
      return {type: 'actual'};
    case 'ArrowLeft':
      return {type: 'pan', dx: step, dy: 0};
    case 'ArrowRight':
      return {type: 'pan', dx: -step, dy: 0};
    case 'ArrowUp':
      return {type: 'pan', dx: 0, dy: step};
    case 'ArrowDown':
      return {type: 'pan', dx: 0, dy: -step};
    default:
      return null;
  }
}

/**
 * CSS for the content element at `view`: its size and its translation.
 *
 * @param {View} view
 * @param {Size} content
 * @returns {{width: string, height: string, transform: string}}
 */
export function viewStyle(view, content) {
  const px = (/** @type {number} */ value) => `${Math.round(value * 100) / 100}px`;
  return {
    width: px(content.width * view.scale),
    height: px(content.height * view.scale),
    transform: `translate(${px(view.x)}, ${px(view.y)})`,
  };
}

/**
 * The scale as a percentage label, e.g. "150%".
 *
 * @param {number} scale
 * @returns {string}
 */
export function scaleLabel(scale) {
  return `${Math.round(scale * 100)}%`;
}

/**
 * True when an image opts out of zooming: its title is "no-zoom" (as in
 * ![alt](src "no-zoom")) or its class list contains "no-zoom".
 *
 * @param {{title?: string | null, className?: string | null}} props
 * @returns {boolean}
 */
export function isZoomOptOut({title, className}) {
  if (typeof title === 'string' && title.trim().toLowerCase() === 'no-zoom') {
    return true;
  }
  return typeof className === 'string' && className.split(/\s+/).includes('no-zoom');
}
