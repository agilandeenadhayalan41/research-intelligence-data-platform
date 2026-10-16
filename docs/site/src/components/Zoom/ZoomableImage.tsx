// A Markdown/MDX image with zoom: click it (or press Enter on it) for the
// full-screen lightbox, or use the hover toolbar to zoom and pan it in place.
//
// The server renders the image exactly as before: its wrappers are
// `display: contents` until the browser has loaded the image. Then the
// wrapper becomes a box the size of the image (so the zoom button can cover
// it and the toolbar sit on its corner), and the zoom button and the toolbar
// are added. A linked image keeps its link and gets no zoom.

import React, {useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';
import clsx from 'clsx';
import {IMAGE_MAX_NATURAL_SCALE, type Size} from '@site/src/lib/panzoom.mjs';
import {usePanZoom} from '@site/src/lib/usePanZoom';

import {downloadImage, type DownloadResult} from './download';
import Lightbox from './Lightbox';
import ZoomControls from './ZoomControls';
import {shouldOpenOnClick} from './shouldOpenOnClick';
import {useNote} from './useNote';
import styles from './styles.module.css';

export interface ZoomableImageProps {
  /** The image, rendered by the theme's original MDX image component. */
  children: ReactNode;
  src?: string;
  alt?: string;
}

const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

function isVectorSource(src: string): boolean {
  return /^data:image\/svg\+xml/i.test(src) || /\.svg(?:[?#]|$)/i.test(src);
}

export default function ZoomableImage({children, src, alt}: ZoomableImageProps): React.JSX.Element {
  const outerRef = useRef<HTMLSpanElement>(null);
  const viewportRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [enhanced, setEnhanced] = useState(false);
  // An image with no intrinsic width (an SVG with only a viewBox) fills the
  // line in normal flow; in a shrink-to-fit box it would collapse, so its box
  // then takes the full width instead.
  const [fill, setFill] = useState(false);
  const flowSize = useRef<Size>({width: 0, height: 0});
  const [natural, setNatural] = useState<Size | null>(null);
  // The URL shown in the lightbox while it is open (read from the image when it opens).
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, showNote] = useNote();

  const getImage = useCallback(() => viewportRef.current?.querySelector('img') ?? null, []);
  const label = (alt ?? '').trim() || 'Image';

  useEffect(() => {
    const image = getImage();
    if (!image || image.closest('a')) {
      return undefined; // A linked image keeps its link and gets no zoom.
    }
    const read = () => {
      const rect = image.getBoundingClientRect();
      flowSize.current = {width: rect.width, height: rect.height};
      if (image.naturalWidth > 0 && image.naturalHeight > 0) {
        setNatural({width: image.naturalWidth, height: image.naturalHeight});
      } else if (rect.width > 0 && rect.height > 0) {
        setNatural({width: rect.width, height: rect.height});
      } else {
        return; // Not loaded (or broken): stay a plain image.
      }
      setEnhanced(true);
    };
    let active = true;
    if (image.complete && image.naturalWidth > 0) {
      read();
    } else if (image.complete) {
      // Loaded without a natural size (some browsers, for a viewBox-only SVG), or broken.
      image.decode().then(
        () => active && read(),
        () => {},
      );
    }
    image.addEventListener('load', read);
    return () => {
      active = false;
      image.removeEventListener('load', read);
    };
  }, [getImage]);

  // Before paint: if the image collapsed in its new box, let the box fill the line.
  useIsomorphicLayoutEffect(() => {
    const image = getImage();
    const flow = flowSize.current;
    if (!enhanced || fill || !image || flow.width < 1 || flow.height < 1) {
      return;
    }
    const rect = image.getBoundingClientRect();
    if (rect.width < 1) {
      setFill(true);
      // Its "natural" size is arbitrary: use the size it is shown at.
      setNatural({width: flow.width, height: flow.height});
    }
  }, [enhanced, fill, getImage]);

  const pz = usePanZoom({
    enabled: enhanced,
    viewportRef,
    eventTargetRef: outerRef,
    getContent: getImage,
    natural,
    naturalMax: IMAGE_MAX_NATURAL_SCALE,
    wheel: 'focused-or-modifier',
    panAtFit: false,
    applyAtFit: false,
    animateClassName: styles.animating,
    onPointerFocus: () => triggerRef.current?.focus({preventScroll: true}),
  });
  const zoomed = pz.ready && !pz.atFit;

  const openLightbox = () => setLightboxSrc(getImage()?.src || src || '');

  const download = useCallback(async (): Promise<DownloadResult> => {
    setBusy(true);
    try {
      return await downloadImage(getImage()?.src || src || '', alt ?? '');
    } finally {
      setBusy(false);
    }
  }, [alt, getImage, src]);

  const lockedSize = zoomed && pz.viewport ? {width: pz.viewport.width, height: pz.viewport.height} : undefined;

  return (
    <>
      <span
        ref={outerRef}
        className={clsx(
          styles.zoomable,
          styles.zoomableImage,
          enhanced && styles.enhanced,
          fill && styles.fill,
          zoomed && styles.zoomed,
        )}
        data-rid-zoom={enhanced ? 'image' : undefined}
        onClick={(event) => {
          if (enhanced && shouldOpenOnClick(event, outerRef.current, zoomed)) {
            openLightbox();
          }
        }}>
        <span ref={viewportRef} className={styles.imageViewport} style={lockedSize}>
          {children}
        </span>
        {enhanced ? (
          <>
            <button
              ref={triggerRef}
              type="button"
              className={styles.trigger}
              aria-label={`Zoom image: ${label}`}
              onKeyDown={(event) => {
                if (event.key === 'Escape' && zoomed) {
                  event.preventDefault();
                  pz.fitToView();
                  return;
                }
                pz.handleKey(event);
              }}
            />
            <ZoomControls
              kind="image"
              label={label}
              pz={pz}
              busy={busy}
              onFullScreen={openLightbox}
              onDownload={() => {
                void download().then((result) => {
                  if (!result.ok) {
                    showNote(result.message);
                  }
                });
              }}
            />
            <span className={styles.note} role="status">
              {note}
            </span>
          </>
        ) : null}
      </span>
      {lightboxSrc !== null ? (
        <Lightbox
          content={{kind: 'image', src: lightboxSrc, vector: isVectorSource(lightboxSrc)}}
          label={label}
          natural={natural}
          onClose={() => setLightboxSrc(null)}
          onDownload={() => download()}
          returnFocusRef={triggerRef}
        />
      ) : null}
    </>
  );
}
