// The full-screen view of an image or a diagram: fit to the screen over a
// dimmed backdrop, with zoom and pan (usePanZoom), a toolbar, and downloads.
//
// A modal dialog: rendered into <body> with a portal, labelled by the alt
// text, focus kept inside while open and given back to the image on close,
// page scrolling locked. Closes on Escape, the Close button, or a click or
// tap beside the image that is not the end of a drag.

import React, {useCallback, useEffect, useId, useRef, useState, type RefObject} from 'react';
import {createPortal} from 'react-dom';
import {IMAGE_MAX_NATURAL_SCALE, scaleLabel, type Size} from '@site/src/lib/panzoom.mjs';
import {usePanZoom} from '@site/src/lib/usePanZoom';

import {openOriginalImage, type DownloadResult} from './download';
import ZoomIcon from './icons';
import ToolButton from './ToolButton';
import styles from './styles.module.css';

/** Space around the content at the fit scale (px); edges may be dragged that far in. */
const PADDING = 48;
const PADDING_NARROW = 12;
/** A vector (an SVG image or a diagram) may be enlarged this much to fit the screen; a bitmap never. */
const VECTOR_MAX_FIT = 2.5;

const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export type LightboxContent =
  | {kind: 'image'; src: string; vector: boolean}
  | {kind: 'diagram'; markup: string};

export interface LightboxProps {
  content: LightboxContent;
  /** The alt text (or the diagram's label): the dialog's name and caption. */
  label: string;
  /** Natural size, when already known (an image may report it on load). */
  natural: Size | null;
  onClose: () => void;
  /** Image: format is undefined. Diagram: 'svg' or 'png'. */
  onDownload: (format?: 'svg' | 'png') => Promise<DownloadResult>;
  /** Focused again when the lightbox closes. */
  returnFocusRef: RefObject<HTMLElement | null>;
}

function useBodyScrollLock(): void {
  useEffect(() => {
    const root = document.documentElement;
    const {body} = document;
    const scrollbar = window.innerWidth - root.clientWidth;
    const previous = {overflow: root.style.overflow, paddingRight: body.style.paddingRight};
    root.style.overflow = 'hidden';
    if (scrollbar > 0) {
      body.style.paddingRight = `${scrollbar}px`;
    }
    return () => {
      root.style.overflow = previous.overflow;
      body.style.paddingRight = previous.paddingRight;
    };
  }, []);
}

export default function Lightbox({
  content,
  label,
  natural: initialNatural,
  onClose,
  onDownload,
  returnFocusRef,
}: LightboxProps): React.JSX.Element | null {
  const dialogRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLParagraphElement>(null);
  const contentRef = useRef<HTMLElement | null>(null);
  const [natural, setNatural] = useState<Size | null>(initialNatural);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [narrow, setNarrow] = useState(false);
  // Room taken by the toolbar (top) and the caption (bottom): the view is kept
  // clear of them, so the fitted content is never drawn under them.
  const [insets, setInsets] = useState({top: 0, bottom: 0});
  const captionId = useId();
  const hintId = useId();

  useEffect(() => {
    const query = window.matchMedia('(max-width: 600px)');
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  const isVector = content.kind === 'diagram' || content.vector;
  const padding = narrow ? PADDING_NARROW : PADDING;
  const pz = usePanZoom({
    enabled: true,
    viewportRef,
    getContent: () => contentRef.current,
    natural,
    padding,
    margin: padding,
    maxFitScale: isVector ? VECTOR_MAX_FIT : 1,
    naturalMax: content.kind === 'image' ? IMAGE_MAX_NATURAL_SCALE : 1,
    wheel: 'always',
    panAtFit: true,
    applyAtFit: true,
    animateClassName: styles.animating,
    onTap: (onContent) => {
      if (!onContent) {
        onClose();
      }
    },
    onPointerFocus: () => viewportRef.current?.focus({preventScroll: true}),
  });

  useBodyScrollLock();

  useEffect(() => {
    const dialog = dialogRef.current;
    const bar = barRef.current;
    const caption = captionRef.current;
    if (!dialog || !bar || !caption) {
      return undefined;
    }
    const measure = () => {
      const box = dialog.getBoundingClientRect();
      const top = Math.max(0, bar.getBoundingClientRect().bottom - box.top + 4);
      const bottom = Math.max(0, box.bottom - caption.getBoundingClientRect().top + 4);
      setInsets((previous) =>
        previous.top === top && previous.bottom === bottom ? previous : {top, bottom},
      );
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(dialog);
    observer.observe(bar);
    observer.observe(caption);
    return () => observer.disconnect();
  }, []);

  // Keep focus inside the dialog (declared first, so on close this listener
  // is removed before focus goes back to the image).
  useEffect(() => {
    const onFocusIn = (event: FocusEvent) => {
      const dialog = dialogRef.current;
      if (dialog && event.target instanceof Node && !dialog.contains(event.target)) {
        viewportRef.current?.focus({preventScroll: true});
      }
    };
    document.addEventListener('focusin', onFocusIn);
    return () => document.removeEventListener('focusin', onFocusIn);
  }, []);

  // Focus the view on open (without a focus ring: the whole screen is the
  // view); give focus back to the image on close.
  useEffect(() => {
    viewportRef.current?.focus({preventScroll: true, focusVisible: false});
    const returnTo = returnFocusRef;
    return () => {
      returnTo.current?.focus({preventScroll: true});
    };
  }, [returnFocusRef]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key === 'Tab') {
      const dialog = dialogRef.current;
      if (!dialog) {
        return;
      }
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null || element === viewportRef.current,
      );
      if (focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    const onControl = (event.target as HTMLElement).closest?.('button, a');
    if (onControl && (event.key === 'Enter' || event.key === ' ')) {
      return;
    }
    pz.handleKey(event);
  };

  const download = useCallback(
    async (format?: 'svg' | 'png') => {
      setBusy(true);
      setStatus('');
      try {
        const result = await onDownload(format);
        setStatus(result.ok ? '' : result.message);
      } finally {
        setBusy(false);
      }
    },
    [onDownload],
  );

  if (typeof document === 'undefined') {
    return null;
  }

  const scale = pz.view?.scale ?? 1;
  const kindLabel = content.kind === 'image' ? 'image' : 'diagram';

  return createPortal(
    <div
      ref={dialogRef}
      className={styles.lightbox}
      // Focusable by script and by a click on the toolbar's gaps or labels, so
      // focus stays in the dialog (and Escape and the zoom keys keep working).
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby={captionId}
      aria-describedby={hintId}
      data-rid-lightbox=""
      onKeyDown={onKeyDown}>
      {/* A click in the strips beside the view (under the toolbar or the caption) closes too. */}
      <div className={styles.backdrop} aria-hidden="true" onClick={onClose} />
      <div
        ref={viewportRef}
        className={styles.lightboxViewport}
        style={{top: insets.top, bottom: insets.bottom}}
        tabIndex={0}
        role="img"
        aria-label={label}>
        {content.kind === 'image' ? (
          <img
            ref={(element) => {
              contentRef.current = element;
            }}
            className={styles.lightboxContent}
            src={content.src}
            alt=""
            draggable={false}
            decoding="async"
            onLoad={(event) => {
              const image = event.currentTarget;
              if (image.naturalWidth > 0 && image.naturalHeight > 0) {
                setNatural((previous) =>
                  previous && previous.width === image.naturalWidth && previous.height === image.naturalHeight
                    ? previous
                    : {width: image.naturalWidth, height: image.naturalHeight},
                );
              }
            }}
          />
        ) : (
          <div
            ref={(element) => {
              contentRef.current = element;
            }}
            className={styles.lightboxContent}
            // The diagram's own SVG, already rendered by Mermaid in the page.
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{__html: content.markup}}
          />
        )}
      </div>

      <div ref={barRef} className={styles.lightboxBar}>
        <div className={styles.lightboxGroup} role="group" aria-label="Zoom">
          <ToolButton icon="zoomOut" label="Zoom out" onClick={pz.zoomOut} disabled={!pz.canZoomOut} />
          <span className={styles.scaleLabel} aria-hidden="true" data-rid-zoom-scale="">
            {scaleLabel(scale)}
          </span>
          <ToolButton icon="zoomIn" label="Zoom in" onClick={pz.zoomIn} disabled={!pz.canZoomIn} />
          <ToolButton icon="fit" label="Fit to screen" onClick={pz.fitToView} disabled={pz.atFit} />
          <ToolButton icon="actual" label="Actual size (100%)" onClick={pz.actualSize} />
        </div>
        <div className={styles.lightboxGroup} role="group" aria-label="Download and close">
          {content.kind === 'image' ? (
            <>
              <a
                className={styles.toolButton}
                href={content.src}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Open the original image in a new tab"
                title="Open the original image in a new tab"
                onClick={(event) => {
                  if (openOriginalImage(content.src)) {
                    event.preventDefault();
                  }
                }}>
                <ZoomIcon name="external" />
              </a>
              <ToolButton
                icon="download"
                label="Download the original image"
                text={narrow ? undefined : 'Download'}
                onClick={() => void download()}
                disabled={busy}
              />
            </>
          ) : (
            <>
              <ToolButton
                icon="download"
                label="Download the diagram as SVG (vector)"
                text="SVG"
                onClick={() => void download('svg')}
                disabled={busy}
              />
              <ToolButton
                icon="download"
                label="Download the diagram as PNG (high resolution)"
                text="PNG"
                onClick={() => void download('png')}
                disabled={busy}
              />
            </>
          )}
          <ToolButton icon="close" label="Close" onClick={onClose} />
        </div>
      </div>

      <p ref={captionRef} className={styles.caption} id={captionId}>
        {label}
      </p>
      <p className={styles.srOnly} id={hintId}>
        {`Zoomable ${kindLabel}. Drag or use the arrow keys to move, the plus and minus keys or the mouse wheel to zoom, 0 to fit, 1 for the actual size, Escape to close.`}
      </p>
      <p className={styles.status} role="status">
        {status}
      </p>
    </div>,
    document.body,
  );
}
