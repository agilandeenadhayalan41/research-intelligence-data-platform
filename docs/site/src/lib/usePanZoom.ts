// The zoom and pan engine shared by the lightbox and the in-page zoom.
//
// The math is in panzoom.mjs; this hook measures the viewport, keeps the
// view, applies it to the content element (its size and a translation, so an
// SVG is redrawn crisply at every scale) and turns pointer, wheel and key
// input into view changes:
//
// - drag with a mouse, pen or finger (pointer capture) pans;
// - two fingers pinch-zoom and pan;
// - the wheel (or a trackpad pinch) zooms at the cursor;
// - a double click or double tap on the content toggles fit and 2x fit;
// - a click or tap that did not drag is reported through onTap (on its click
//   event, so a touch screen's click cannot land on the page below).
//
// Everything that touches the DOM runs in effects and event handlers, never
// during render, so the hook is safe on the server.

import {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject} from 'react';
import {
  DRAG_THRESHOLD,
  ZOOM_STEP,
  clampView,
  distance,
  fitScale,
  fitView,
  isDoubleTap,
  isPointOnContent,
  isValidSize,
  keyAction,
  panBy as panView,
  pinchView,
  sameScale,
  toggleScale,
  viewStyle,
  wheelZoomFactor,
  zoomAt,
  zoomLimits,
  zoomTo,
  type Limits,
  type Point,
  type Size,
  type View,
} from '@site/src/lib/panzoom.mjs';

export type {Size, View};

// useLayoutEffect warns on the server; the effects below only run in the browser anyway.
const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** How long the animated (button and key) zooms take; matches the CSS transition. */
const ANIMATION_MS = 220;

/** Inline styles the hook sets on the content element (and restores). */
const STYLE_PROPERTIES = [
  'position',
  'left',
  'top',
  'max-width',
  'max-height',
  'width',
  'height',
  'transform',
  'transform-origin',
] as const;

export interface PanZoomOptions {
  /** Measure, listen and apply the view. When false the content is left as rendered. */
  enabled: boolean;
  /** The element the content is drawn in (its size is the viewport). */
  viewportRef: RefObject<HTMLElement | null>;
  /** Where pointer and wheel events are listened for; default: the viewport. */
  eventTargetRef?: RefObject<HTMLElement | null>;
  /** The element that is sized and moved: an <img>, or the element holding an <svg>. */
  getContent: () => HTMLElement | null;
  /** Natural size of the content in CSS pixels; null until known. */
  natural: Size | null;
  /** Space (px) kept free around the content at the fit scale. */
  padding?: number;
  /** The fit scale never exceeds this (1 keeps a bitmap at most at its natural size). */
  maxFitScale?: number;
  /** Zooming may always reach this scale (4 for images: 4x the natural size). */
  naturalMax?: number;
  /** How far (px) an edge of zoomed content may move inside the viewport. */
  margin?: number;
  /**
   * 'always': the wheel always zooms (the lightbox). 'focused-or-modifier'
   * (the in-page zoom): at the fit scale only Ctrl or Cmd starts a wheel
   * zoom; once zoomed, focus inside the element (from a drag or the toolbar)
   * is enough. So the page still scrolls normally.
   */
  wheel: 'always' | 'focused-or-modifier';
  /** Whether drag and arrow keys pan at the fit scale (the lightbox) or only once zoomed (the page). */
  panAtFit: boolean;
  /** Whether the view is applied at the fit scale too. False in the page, where the content then keeps its normal layout. */
  applyAtFit: boolean;
  /** Class added to the content during an animated zoom (buttons and keys). */
  animateClassName?: string;
  /** A click or tap without a drag, on the content or beside it. */
  onTap?: (onContent: boolean) => void;
  /** Called on a mouse press in the viewport (whose default is prevented), to move focus. */
  onPointerFocus?: () => void;
}

export interface PanZoom {
  /** True once the natural size and the viewport size are known. */
  ready: boolean;
  view: View | null;
  /** The fit scale. */
  fit: number;
  limits: Limits;
  /** True while the view follows the fit (it re-fits when the viewport changes). */
  atFit: boolean;
  /** False at the largest (smallest) scale, or before the engine is ready. */
  canZoomIn: boolean;
  canZoomOut: boolean;
  /** The measured viewport size. */
  viewport: Size | null;
  zoomIn: () => void;
  zoomOut: () => void;
  fitToView: () => void;
  actualSize: () => void;
  /** Handles a zoom or pan key (see keyAction); true when it did, after preventDefault(). */
  handleKey: (event: {
    key: string;
    shiftKey: boolean;
    ctrlKey: boolean;
    altKey: boolean;
    metaKey: boolean;
    preventDefault: () => void;
  }) => boolean;
}

interface EngineState {
  ready: boolean;
  view: View;
  natural: Size;
  viewport: Size;
  fit: number;
  limits: Limits;
  margin: number;
  atFit: boolean;
}

const ORIGIN: View = {scale: 1, x: 0, y: 0};
const NO_SIZE: Size = {width: 1, height: 1};

export function usePanZoom(options: PanZoomOptions): PanZoom {
  const {
    enabled,
    viewportRef,
    natural,
    padding = 0,
    maxFitScale = Infinity,
    naturalMax = 1,
    margin = 0,
    applyAtFit,
  } = options;

  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [viewport, setViewport] = useState<Size | null>(null);
  // null: follow the fit. Otherwise the view the user zoomed or panned to.
  const [freeView, setFreeView] = useState<View | null>(null);

  // Measure the viewport.
  useIsomorphicLayoutEffect(() => {
    const element = viewportRef.current;
    if (!enabled || !element) {
      return undefined;
    }
    const measure = () => {
      // Fractional CSS pixels (clientWidth would round), so a box locked to
      // this size while zoomed keeps exactly its size in the page.
      const {width, height} = element.getBoundingClientRect();
      setViewport((previous) =>
        previous && previous.width === width && previous.height === height ? previous : {width, height},
      );
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [enabled, viewportRef]);

  const ready = Boolean(enabled && isValidSize(natural) && isValidSize(viewport));
  const content = ready ? (natural as Size) : NO_SIZE;
  const room = ready ? (viewport as Size) : NO_SIZE;
  const fit = ready ? fitScale(content, room, {padding, maxScale: maxFitScale}) : 1;
  const limits = useMemo(() => zoomLimits(fit, {naturalMax}), [fit, naturalMax]);
  const atFit = freeView === null;
  const view: View | null = !ready
    ? null
    : atFit
      ? fitView(content, room, {padding, maxScale: maxFitScale})
      : clampView(freeView, content, room, {margin});

  // The latest state for event handlers. commit() also updates it at once,
  // so several events between two renders build on each other.
  const stateRef = useRef<EngineState>({
    ready: false,
    view: ORIGIN,
    natural: NO_SIZE,
    viewport: NO_SIZE,
    fit: 1,
    limits,
    margin,
    atFit: true,
  });
  stateRef.current = {
    ready,
    view: view ?? ORIGIN,
    natural: content,
    viewport: room,
    fit,
    limits,
    margin,
    atFit,
  };

  const animationTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animate = useCallback(() => {
    const className = optionsRef.current.animateClassName;
    const element = optionsRef.current.getContent();
    if (!className || !element) {
      return;
    }
    element.classList.add(className);
    if (animationTimer.current) {
      clearTimeout(animationTimer.current);
    }
    animationTimer.current = setTimeout(() => {
      element.classList.remove(className);
      animationTimer.current = null;
    }, ANIMATION_MS + 30);
  }, []);
  useEffect(
    () => () => {
      if (animationTimer.current) {
        clearTimeout(animationTimer.current);
      }
    },
    [],
  );

  const commit = useCallback(
    (next: View, {animated = false}: {animated?: boolean} = {}) => {
      const state = stateRef.current;
      if (!state.ready) {
        return;
      }
      if (animated) {
        animate();
      }
      // Back at the fit scale: follow the fit again (in the page this ends the zoom).
      const backToFit = sameScale(next.scale, state.fit);
      stateRef.current = {...state, view: backToFit ? state.view : next, atFit: backToFit};
      if (backToFit) {
        stateRef.current.view = fitView(state.natural, state.viewport, {
          padding: optionsRef.current.padding ?? 0,
          maxScale: optionsRef.current.maxFitScale ?? Infinity,
        });
      }
      setFreeView(backToFit ? null : next);
    },
    [animate],
  );

  const context = () => {
    const state = stateRef.current;
    return {content: state.natural, viewport: state.viewport, limits: state.limits, margin: state.margin};
  };
  const center = (): Point => {
    const {width, height} = stateRef.current.viewport;
    const element = viewportRef.current;
    if (!element) {
      return {x: width / 2, y: height / 2};
    }
    // The middle of the part of the box that is on screen (a tall diagram may extend past the window).
    const rect = element.getBoundingClientRect();
    const top = Math.max(0, -rect.top);
    const bottom = Math.min(height, window.innerHeight - rect.top);
    return {x: width / 2, y: bottom > top ? (top + bottom) / 2 : height / 2};
  };

  const zoomIn = useCallback(() => {
    commit(zoomAt(stateRef.current.view, center(), ZOOM_STEP, context()), {animated: true});
  }, [commit]);
  const zoomOut = useCallback(() => {
    commit(zoomAt(stateRef.current.view, center(), 1 / ZOOM_STEP, context()), {animated: true});
  }, [commit]);
  const fitToView = useCallback(() => {
    if (stateRef.current.ready && !stateRef.current.atFit) {
      animate();
    }
    setFreeView(null);
    stateRef.current = {...stateRef.current, atFit: true};
  }, [animate]);
  const actualSize = useCallback(() => {
    commit(zoomTo(stateRef.current.view, 1, context()), {animated: true});
  }, [commit]);

  const handleKey = useCallback<PanZoom['handleKey']>(
    (event) => {
      const state = stateRef.current;
      if (!state.ready) {
        return false;
      }
      const action = keyAction(event);
      if (!action || action.type === 'close') {
        return false;
      }
      const canPan = optionsRef.current.panAtFit || !state.atFit;
      switch (action.type) {
        case 'zoom':
          commit(zoomAt(state.view, center(), action.factor, context()), {animated: true});
          break;
        case 'fit':
          fitToView();
          break;
        case 'actual':
          commit(zoomTo(state.view, 1, context()), {animated: true});
          break;
        case 'pan':
          if (!canPan) {
            return false;
          }
          commit(panView(state.view, action.dx, action.dy, context()), {animated: true});
          break;
      }
      event.preventDefault();
      return true;
    },
    [commit, fitToView],
  );

  // Apply the view to the content element.
  const applying = ready && view !== null && (applyAtFit || !atFit);
  const savedStyle = useRef<string | null>(null);
  useIsomorphicLayoutEffect(() => {
    const element = optionsRef.current.getContent();
    if (!element) {
      return;
    }
    if (!applying || !view) {
      if (savedStyle.current !== null) {
        for (const property of STYLE_PROPERTIES) {
          element.style.removeProperty(property);
        }
        if (savedStyle.current) {
          element.style.cssText = savedStyle.current;
        }
        savedStyle.current = null;
      }
      return;
    }
    if (savedStyle.current === null) {
      savedStyle.current = element.style.cssText;
    }
    const style = viewStyle(view, content);
    element.style.position = 'absolute';
    element.style.left = '0';
    element.style.top = '0';
    element.style.maxWidth = 'none';
    element.style.maxHeight = 'none';
    element.style.width = style.width;
    element.style.height = style.height;
    element.style.transform = style.transform;
    element.style.transformOrigin = '0 0';
  });

  // Pointer and wheel input.
  useEffect(() => {
    const viewportElement = viewportRef.current;
    const target = optionsRef.current.eventTargetRef?.current ?? viewportElement;
    if (!enabled || !viewportElement || !target) {
      return undefined;
    }
    const pointers = new Map<number, Point>();
    let moved = false;
    let downAt: Point = {x: 0, y: 0};
    let lastTap: {time: number; x: number; y: number} | null = null;
    let pendingTap: {onContent: boolean; time: number} | null = null;

    const local = (event: {clientX: number; clientY: number}): Point => {
      const rect = viewportElement.getBoundingClientRect();
      return {x: event.clientX - rect.left, y: event.clientY - rect.top};
    };
    const canPan = () => optionsRef.current.panAtFit || !stateRef.current.atFit;

    const onPointerDown = (event: PointerEvent) => {
      if (!stateRef.current.ready || (event.pointerType === 'mouse' && event.button !== 0)) {
        return;
      }
      if ((event.target as Element | null)?.closest?.('[data-panzoom-ignore]')) {
        return;
      }
      if (pointers.size === 0 && !canPan()) {
        // In the page at the fit scale: leave clicks and page scrolling alone.
        return;
      }
      const point = local(event);
      pointers.set(event.pointerId, point);
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // The pointer may already be gone.
      }
      if (pointers.size === 1) {
        moved = false;
        downAt = point;
        pendingTap = null;
      } else {
        moved = true; // A second finger: a pinch, never a tap.
      }
      if (event.pointerType === 'mouse') {
        event.preventDefault(); // No text selection or native image drag.
        optionsRef.current.onPointerFocus?.();
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      const previous = pointers.get(event.pointerId);
      if (!previous) {
        return;
      }
      const point = local(event);
      const state = stateRef.current;
      if (pointers.size === 1) {
        pointers.set(event.pointerId, point);
        if (!moved) {
          if (distance(point, downAt) <= DRAG_THRESHOLD) {
            return;
          }
          moved = true;
          commit(panView(state.view, point.x - downAt.x, point.y - downAt.y, context()));
          return;
        }
        commit(panView(state.view, point.x - previous.x, point.y - previous.y, context()));
        return;
      }
      const ids = [...pointers.keys()].slice(0, 2);
      if (!ids.includes(event.pointerId)) {
        pointers.set(event.pointerId, point);
        return;
      }
      const before = ids.map((id) => pointers.get(id) as Point) as [Point, Point];
      pointers.set(event.pointerId, point);
      const after = ids.map((id) => pointers.get(id) as Point) as [Point, Point];
      moved = true;
      commit(pinchView(state.view, before, after, context()));
    };

    const onPointerEnd = (event: PointerEvent) => {
      if (!pointers.has(event.pointerId)) {
        return;
      }
      pointers.delete(event.pointerId);
      try {
        target.releasePointerCapture(event.pointerId);
      } catch {
        // Already released.
      }
      if (pointers.size > 0 || moved || event.type === 'pointercancel') {
        return;
      }
      const state = stateRef.current;
      const point = local(event);
      const onContent = isPointOnContent(point, state.view, state.natural);
      const tap = {time: event.timeStamp, x: point.x, y: point.y};
      if (onContent && isDoubleTap(lastTap, tap)) {
        lastTap = null;
        const scale = toggleScale(state.view.scale, state.fit, state.limits);
        if (sameScale(scale, state.fit)) {
          fitToView();
        } else {
          commit(zoomTo(state.view, scale, context(), point), {animated: true});
        }
        return;
      }
      lastTap = onContent ? tap : null;
      // Reported on the click that follows, not now: closing the lightbox on
      // pointerup would let a touch screen's click land on the page below.
      pendingTap = {onContent, time: performance.now()};
    };

    const onClick = () => {
      const tap = pendingTap;
      pendingTap = null;
      if (tap && performance.now() - tap.time < 1000) {
        optionsRef.current.onTap?.(tap.onContent);
      }
    };

    const onWheel = (event: WheelEvent) => {
      const state = stateRef.current;
      if (!state.ready) {
        return;
      }
      if (optionsRef.current.wheel === 'focused-or-modifier') {
        // At the fit scale only Ctrl or Cmd starts a wheel zoom: focus alone
        // is not enough (it returns to the element after the lightbox closes,
        // and the wheel must then still scroll the page). Once zoomed, focus
        // inside the element (from a drag or the toolbar) is enough.
        const modifier = event.ctrlKey || event.metaKey;
        const active = document.activeElement;
        const focused = active !== null && target.contains(active) && !state.atFit;
        if (!modifier && !focused) {
          return;
        }
      }
      event.preventDefault();
      const factor = wheelZoomFactor(event, {pageHeight: state.viewport.height});
      commit(zoomAt(state.view, local(event), factor, context()));
    };

    // A dragged image must not start the browser's own image drag.
    const onDragStart = (event: DragEvent) => event.preventDefault();

    target.addEventListener('pointerdown', onPointerDown);
    target.addEventListener('pointermove', onPointerMove);
    target.addEventListener('pointerup', onPointerEnd);
    target.addEventListener('pointercancel', onPointerEnd);
    target.addEventListener('wheel', onWheel, {passive: false});
    target.addEventListener('dragstart', onDragStart);
    target.addEventListener('click', onClick);
    return () => {
      target.removeEventListener('click', onClick);
      target.removeEventListener('pointerdown', onPointerDown);
      target.removeEventListener('pointermove', onPointerMove);
      target.removeEventListener('pointerup', onPointerEnd);
      target.removeEventListener('pointercancel', onPointerEnd);
      target.removeEventListener('wheel', onWheel);
      target.removeEventListener('dragstart', onDragStart);
    };
    // context() and center() only read refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, viewportRef, commit, fitToView]);

  const scale = view?.scale ?? 1;
  return {
    ready,
    view,
    fit,
    limits,
    atFit,
    canZoomIn: ready && scale < limits.max && !sameScale(scale, limits.max),
    canZoomOut: ready && scale > limits.min && !sameScale(scale, limits.min),
    viewport,
    zoomIn,
    zoomOut,
    fitToView,
    actualSize,
    handleKey,
  };
}
