import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  DOUBLE_TAP_MS,
  IMAGE_MAX_NATURAL_SCALE,
  PAN_STEP,
  PAN_STEP_LARGE,
  ZOOM_STEP,
  centerView,
  clamp,
  clampView,
  distance,
  fitScale,
  fitView,
  isDoubleTap,
  isPointOnContent,
  isValidSize,
  isZoomOptOut,
  keyAction,
  midpoint,
  panBy,
  pinchView,
  sameScale,
  scaleLabel,
  toggleScale,
  viewStyle,
  wheelZoomFactor,
  zoomAt,
  zoomLimits,
  zoomTo,
} from '../src/lib/panzoom.mjs';

const close = (actual, expected, epsilon = 1e-9) =>
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} is not close to ${expected}`);

const IMAGE = {width: 2000, height: 1000};
const SCREEN = {width: 1000, height: 800};

/** Screen position of a content point at a view. */
const toScreen = (view, u, v) => ({x: view.x + view.scale * u, y: view.y + view.scale * v});

describe('clamp and sizes', () => {
  test('clamp keeps a value inside the range', () => {
    assert.equal(clamp(5, 0, 10), 5);
    assert.equal(clamp(-1, 0, 10), 0);
    assert.equal(clamp(11, 0, 10), 10);
    assert.equal(clamp(5, 10, 0), 0, 'max wins when min > max');
  });

  test('isValidSize needs a finite positive width and height', () => {
    assert.equal(isValidSize({width: 1, height: 2}), true);
    assert.equal(isValidSize({width: 0, height: 2}), false);
    assert.equal(isValidSize({width: Number.NaN, height: 2}), false);
    assert.equal(isValidSize({width: Infinity, height: 2}), false);
    assert.equal(isValidSize(null), false);
    assert.equal(isValidSize(undefined), false);
  });
});

describe('fit', () => {
  test('fitScale is limited by the tighter side', () => {
    assert.equal(fitScale(IMAGE, SCREEN), 0.5);
    assert.equal(fitScale({width: 100, height: 400}, SCREEN), 2);
  });

  test('padding is taken from each edge', () => {
    assert.equal(fitScale({width: 900, height: 100}, SCREEN, {padding: 50}), 1);
  });

  test('maxScale keeps a small bitmap at its natural size', () => {
    assert.equal(fitScale({width: 100, height: 50}, SCREEN, {maxScale: 1}), 1);
    assert.equal(fitScale({width: 100, height: 50}, SCREEN), 10);
  });

  test('an unusable size fits at 1', () => {
    assert.equal(fitScale({width: 0, height: 10}, SCREEN), 1);
    assert.equal(fitScale(IMAGE, {width: 0, height: 0}), 1);
  });

  test('fitView centres the content', () => {
    assert.deepEqual(fitView(IMAGE, SCREEN), {scale: 0.5, x: 0, y: 150});
    assert.deepEqual(centerView(1, {width: 200, height: 100}, SCREEN), {scale: 1, x: 400, y: 350});
  });
});

describe('zoomLimits', () => {
  test('diagrams: 0.5x to 8x the fit scale', () => {
    assert.deepEqual(zoomLimits(0.5), {min: 0.25, max: 4});
    assert.deepEqual(zoomLimits(1), {min: 0.5, max: 8});
  });

  test('images: up to 4x the natural size when that is more than 8x fit', () => {
    assert.equal(IMAGE_MAX_NATURAL_SCALE, 4);
    assert.deepEqual(zoomLimits(0.1, {naturalMax: 4}), {min: 0.05, max: 4});
    assert.deepEqual(zoomLimits(1, {naturalMax: 4}), {min: 0.5, max: 8});
  });

  test('the actual size (1) is always reachable', () => {
    assert.equal(zoomLimits(0.05).max, 1);
    assert.equal(zoomLimits(3).min, 1);
  });

  test('a bad fit falls back to 1', () => {
    assert.deepEqual(zoomLimits(0), {min: 0.5, max: 8});
    assert.deepEqual(zoomLimits(Number.NaN), {min: 0.5, max: 8});
  });
});

describe('clampView', () => {
  test('content smaller than the viewport is centred', () => {
    assert.deepEqual(clampView({scale: 0.25, x: -400, y: 900}, IMAGE, SCREEN), {scale: 0.25, x: 250, y: 275});
  });

  test('larger content always covers the viewport', () => {
    // 2000 x 1000 at scale 1 in 1000 x 800: x in [-1000, 0], y in [-200, 0].
    assert.deepEqual(clampView({scale: 1, x: 300, y: 300}, IMAGE, SCREEN), {scale: 1, x: 0, y: 0});
    assert.deepEqual(clampView({scale: 1, x: -5000, y: -5000}, IMAGE, SCREEN), {scale: 1, x: -1000, y: -200});
    assert.deepEqual(clampView({scale: 1, x: -400, y: -100}, IMAGE, SCREEN), {scale: 1, x: -400, y: -100});
  });

  test('a margin lets each edge come that far inside', () => {
    assert.deepEqual(clampView({scale: 1, x: 300, y: -5000}, IMAGE, SCREEN, {margin: 40}), {
      scale: 1,
      x: 40,
      y: -240,
    });
  });

  test('the image can never be lost off-screen', () => {
    for (const scale of [0.1, 0.5, 1, 3, 8]) {
      for (const [x, y] of [
        [-1e6, -1e6],
        [1e6, 1e6],
        [-1e6, 1e6],
        [1e6, -1e6],
      ]) {
        const view = clampView({scale, x, y}, IMAGE, SCREEN, {margin: 48});
        const right = view.x + IMAGE.width * scale;
        const bottom = view.y + IMAGE.height * scale;
        assert.ok(right > 0 && view.x < SCREEN.width, `x visible at ${scale}`);
        assert.ok(bottom > 0 && view.y < SCREEN.height, `y visible at ${scale}`);
      }
    }
  });
});

describe('zoomAt', () => {
  const context = {content: IMAGE, viewport: SCREEN, limits: zoomLimits(0.5, {naturalMax: 4})};

  test('the content point under the cursor stays under it', () => {
    const start = {scale: 1, x: -500, y: -100};
    const cursor = {x: 300, y: 250};
    const before = {u: (cursor.x - start.x) / start.scale, v: (cursor.y - start.y) / start.scale};
    const view = zoomAt(start, cursor, 2, context);
    assert.equal(view.scale, 2);
    const after = toScreen(view, before.u, before.v);
    close(after.x, cursor.x);
    close(after.y, cursor.y);
  });

  test('the scale stays inside the limits', () => {
    const fit = fitView(IMAGE, SCREEN);
    assert.equal(zoomAt(fit, {x: 0, y: 0}, 1000, context).scale, 4);
    assert.equal(zoomAt(fit, {x: 0, y: 0}, 0.001, context).scale, 0.25);
  });

  test('a bad factor leaves the scale alone', () => {
    const fit = fitView(IMAGE, SCREEN);
    assert.equal(zoomAt(fit, {x: 10, y: 10}, 0, context).scale, 0.5);
    assert.equal(zoomAt(fit, {x: 10, y: 10}, Number.NaN, context).scale, 0.5);
  });

  test('zoomTo goes to an absolute scale around the centre by default', () => {
    const fit = fitView(IMAGE, SCREEN);
    const view = zoomTo(fit, 1, context);
    assert.equal(view.scale, 1);
    // The centre of the image stays at the centre of the screen.
    const centre = toScreen(view, IMAGE.width / 2, IMAGE.height / 2);
    close(centre.x, SCREEN.width / 2);
    close(centre.y, SCREEN.height / 2);
  });

  test('zooming in then out by the same step returns to the start', () => {
    const fit = fitView(IMAGE, SCREEN);
    const point = {x: 400, y: 400};
    const back = zoomAt(zoomAt(fit, point, ZOOM_STEP, context), point, 1 / ZOOM_STEP, context);
    close(back.scale, fit.scale);
    close(back.x, fit.x);
    close(back.y, fit.y);
  });
});

describe('panBy and pinch', () => {
  const context = {content: IMAGE, viewport: SCREEN, limits: zoomLimits(0.5, {naturalMax: 4})};

  test('panBy moves the content and clamps it', () => {
    const start = {scale: 1, x: -500, y: -100};
    assert.deepEqual(panBy(start, 100, 50, context), {scale: 1, x: -400, y: -50});
    assert.deepEqual(panBy(start, 10000, 10000, context), {scale: 1, x: 0, y: 0});
  });

  test('panning at the fit scale keeps the image centred', () => {
    const fit = fitView(IMAGE, SCREEN);
    assert.deepEqual(panBy(fit, 0, 300, context), fit);
  });

  test('pinch zooms by the change in finger distance around their midpoint', () => {
    const start = {scale: 1, x: -500, y: -100};
    const before = [
      {x: 400, y: 400},
      {x: 500, y: 400},
    ];
    const after = [
      {x: 350, y: 400},
      {x: 550, y: 400},
    ];
    const view = pinchView(start, before, after, context);
    assert.equal(view.scale, 2);
    const centre = {u: (450 - start.x) / start.scale, v: (400 - start.y) / start.scale};
    const screen = toScreen(view, centre.u, centre.v);
    close(screen.x, 450);
    close(screen.y, 400);
  });

  test('pinch with a moving midpoint also pans', () => {
    const start = {scale: 1, x: -500, y: -100};
    const view = pinchView(
      start,
      [
        {x: 400, y: 400},
        {x: 500, y: 400},
      ],
      [
        {x: 420, y: 380},
        {x: 520, y: 380},
      ],
      context,
    );
    assert.deepEqual(view, {scale: 1, x: -480, y: -120});
  });

  test('distance and midpoint', () => {
    assert.equal(distance({x: 0, y: 0}, {x: 3, y: 4}), 5);
    assert.deepEqual(midpoint({x: 0, y: 2}, {x: 4, y: 6}), {x: 2, y: 4});
  });
});

describe('gestures', () => {
  test('toggleScale goes from fit to 2x fit and back', () => {
    const limits = zoomLimits(0.5);
    assert.equal(toggleScale(0.5, 0.5, limits), 1);
    assert.equal(toggleScale(1, 0.5, limits), 0.5);
    assert.equal(toggleScale(3, 0.5, limits), 0.5);
    assert.equal(toggleScale(0.5000001, 0.5, limits), 1, 'within rounding of fit counts as fit');
  });

  test('sameScale uses a relative tolerance', () => {
    assert.equal(sameScale(1, 1.0005), true);
    assert.equal(sameScale(1, 1.01), false);
  });

  test('wheelZoomFactor: wheel down zooms out, up zooms in, pinch is stronger', () => {
    assert.ok(wheelZoomFactor({deltaY: 100}) < 1);
    assert.ok(wheelZoomFactor({deltaY: -100}) > 1);
    close(wheelZoomFactor({deltaY: -100}) * wheelZoomFactor({deltaY: 100}), 1);
    assert.ok(wheelZoomFactor({deltaY: -10, ctrlKey: true}) > wheelZoomFactor({deltaY: -10}));
    assert.equal(wheelZoomFactor({deltaY: 0}), 1);
    assert.equal(wheelZoomFactor({deltaY: Number.NaN}), 1);
  });

  test('wheelZoomFactor converts lines and pages, and is limited per event', () => {
    close(wheelZoomFactor({deltaY: 3, deltaMode: 1}), wheelZoomFactor({deltaY: 48}));
    assert.equal(wheelZoomFactor({deltaY: 1, deltaMode: 2}), 0.5);
    assert.equal(wheelZoomFactor({deltaY: -100000}), 2);
  });

  test('isPointOnContent', () => {
    const view = {scale: 0.5, x: 0, y: 150};
    assert.equal(isPointOnContent({x: 10, y: 200}, view, IMAGE), true);
    assert.equal(isPointOnContent({x: 10, y: 100}, view, IMAGE), false);
    assert.equal(isPointOnContent({x: 1000, y: 650}, view, IMAGE), true);
    assert.equal(isPointOnContent({x: 1001, y: 650}, view, IMAGE), false);
  });

  test('isDoubleTap needs two taps close in time and space', () => {
    const first = {time: 1000, x: 100, y: 100};
    assert.equal(isDoubleTap(null, first), false);
    assert.equal(isDoubleTap(first, {time: 1000 + DOUBLE_TAP_MS, x: 110, y: 110}), true);
    assert.equal(isDoubleTap(first, {time: 1001 + DOUBLE_TAP_MS, x: 100, y: 100}), false);
    assert.equal(isDoubleTap(first, {time: 1100, x: 200, y: 100}), false);
  });
});

describe('keyAction', () => {
  test('zoom, fit, actual size and close keys', () => {
    assert.deepEqual(keyAction({key: '+'}), {type: 'zoom', factor: ZOOM_STEP});
    assert.deepEqual(keyAction({key: '='}), {type: 'zoom', factor: ZOOM_STEP});
    assert.deepEqual(keyAction({key: '-'}), {type: 'zoom', factor: 1 / ZOOM_STEP});
    assert.deepEqual(keyAction({key: '−'}), {type: 'zoom', factor: 1 / ZOOM_STEP});
    assert.deepEqual(keyAction({key: '0'}), {type: 'fit'});
    assert.deepEqual(keyAction({key: '1'}), {type: 'actual'});
    assert.deepEqual(keyAction({key: 'Escape'}), {type: 'close'});
  });

  test('arrows move the view that way (the content the other way); Shift is bigger', () => {
    assert.deepEqual(keyAction({key: 'ArrowLeft'}), {type: 'pan', dx: PAN_STEP, dy: 0});
    assert.deepEqual(keyAction({key: 'ArrowRight'}), {type: 'pan', dx: -PAN_STEP, dy: 0});
    assert.deepEqual(keyAction({key: 'ArrowUp'}), {type: 'pan', dx: 0, dy: PAN_STEP});
    assert.deepEqual(keyAction({key: 'ArrowDown', shiftKey: true}), {type: 'pan', dx: 0, dy: -PAN_STEP_LARGE});
  });

  test('other keys and browser shortcuts are left alone', () => {
    assert.equal(keyAction({key: 'a'}), null);
    assert.equal(keyAction({key: 'Tab'}), null);
    assert.equal(keyAction({key: '+', ctrlKey: true}), null);
    assert.equal(keyAction({key: '0', metaKey: true}), null);
    assert.equal(keyAction({key: 'ArrowLeft', altKey: true}), null);
    assert.deepEqual(keyAction({key: 'Escape', ctrlKey: true}), {type: 'close'});
  });
});

describe('rendering helpers', () => {
  test('viewStyle sizes and translates the content', () => {
    assert.deepEqual(viewStyle({scale: 0.5, x: 10.123, y: -3}, IMAGE), {
      width: '1000px',
      height: '500px',
      transform: 'translate(10.12px, -3px)',
    });
  });

  test('scaleLabel', () => {
    assert.equal(scaleLabel(1), '100%');
    assert.equal(scaleLabel(0.333), '33%');
    assert.equal(scaleLabel(2.5), '250%');
  });

  test('isZoomOptOut: title or class "no-zoom"', () => {
    assert.equal(isZoomOptOut({title: 'no-zoom'}), true);
    assert.equal(isZoomOptOut({title: ' No-Zoom '}), true);
    assert.equal(isZoomOptOut({className: 'wide no-zoom'}), true);
    assert.equal(isZoomOptOut({title: 'A diagram, no-zoom please'}), false);
    assert.equal(isZoomOptOut({className: 'no-zoomies'}), false);
    assert.equal(isZoomOptOut({}), false);
    assert.equal(isZoomOptOut({title: null, className: null}), false);
  });
});
