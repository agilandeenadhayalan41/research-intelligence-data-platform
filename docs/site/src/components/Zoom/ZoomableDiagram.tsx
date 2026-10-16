// A Mermaid diagram with zoom: click it (or press Enter on it) for the
// full-screen lightbox, or use the hover toolbar to zoom and pan it in place.
// The SVG itself is resized (not scaled as a bitmap), so text stays crisp.
//
// Mermaid renders in the browser, so the zoom button and the toolbar appear
// once the SVG is in the page, and follow it when it is drawn again (for
// example after a colour-mode change).

import React, {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import clsx from 'clsx';
import {useLocation} from '@docusaurus/router';
import {MermaidContainerClassName} from '@docusaurus/theme-mermaid/client';
import type {Size} from '@site/src/lib/panzoom.mjs';
import {usePanZoom} from '@site/src/lib/usePanZoom';

import {downloadDiagram, lightboxSvgMarkup, svgNaturalSize, type DownloadResult} from './download';
import Lightbox from './Lightbox';
import ZoomControls from './ZoomControls';
import {shouldOpenOnClick} from './shouldOpenOnClick';
import {useNote} from './useNote';
import styles from './styles.module.css';

interface DiagramInfo {
  natural: Size;
  label: string;
}

/** 1-based position of this diagram among the zoomable diagrams of the page. */
function diagramIndex(element: HTMLElement | null): number {
  if (!element) {
    return 1;
  }
  const all = Array.from(document.querySelectorAll('[data-rid-zoom="diagram"]'));
  const index = all.indexOf(element);
  return index >= 0 ? index + 1 : 1;
}

/** "Flowchart 2", "Sequence diagram 1", or the diagram's own title. */
function diagramLabel(svg: SVGSVGElement, index: number): string {
  const title = svg.querySelector(':scope > title')?.textContent?.trim() || svg.getAttribute('aria-label')?.trim();
  if (title) {
    return title;
  }
  const type = (svg.getAttribute('aria-roledescription') ?? '')
    .replace(/-v\d+$/i, '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .trim();
  const name = !type ? 'diagram' : /diagram|chart/.test(type) ? type : `${type} diagram`;
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${index}`;
}

export default function ZoomableDiagram({children}: {children: ReactNode}): React.JSX.Element {
  const outerRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [info, setInfo] = useState<DiagramInfo | null>(null);
  const [lightboxMarkup, setLightboxMarkup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, showNote] = useNote();
  const {pathname} = useLocation();

  const getSvg = useCallback(() => viewportRef.current?.querySelector('svg') ?? null, []);
  const getContainer = useCallback(
    () => viewportRef.current?.querySelector<HTMLElement>(`.${MermaidContainerClassName}`) ?? null,
    [],
  );

  // Follow the SVG that Mermaid renders (asynchronously, and again on a colour-mode change).
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return undefined;
    }
    let current: SVGSVGElement | null = null;
    const check = () => {
      const svg = getSvg();
      if (svg === current) {
        return;
      }
      current = svg;
      const natural = svg ? svgNaturalSize(svg) : null;
      setInfo(svg && natural ? {natural, label: diagramLabel(svg, diagramIndex(outerRef.current))} : null);
    };
    check();
    const observer = new MutationObserver(check);
    observer.observe(viewport, {childList: true, subtree: true});
    return () => observer.disconnect();
  }, [getSvg]);

  const pz = usePanZoom({
    enabled: info !== null,
    viewportRef,
    eventTargetRef: outerRef,
    getContent: getContainer,
    natural: info?.natural ?? null,
    wheel: 'focused-or-modifier',
    panAtFit: false,
    applyAtFit: false,
    animateClassName: styles.animating,
    onPointerFocus: () => triggerRef.current?.focus({preventScroll: true}),
  });
  const zoomed = pz.ready && !pz.atFit;
  const label = info?.label ?? 'Diagram';

  const download = useCallback(
    async (format: 'svg' | 'png' = 'svg'): Promise<DownloadResult> => {
      const svg = getSvg();
      if (!svg || !info) {
        return {ok: false, message: 'The diagram is not ready yet.'};
      }
      // The size the diagram is displayed at in the page (not the zoomed size).
      const displayed =
        pz.ready && pz.fit > 0
          ? {width: info.natural.width * pz.fit, height: info.natural.height * pz.fit}
          : {width: svg.getBoundingClientRect().width, height: svg.getBoundingClientRect().height};
      setBusy(true);
      try {
        return await downloadDiagram({
          svg,
          format,
          pagePath: pathname,
          index: diagramIndex(outerRef.current),
          displayed,
        });
      } finally {
        setBusy(false);
      }
    },
    [getSvg, info, pathname, pz.fit, pz.ready],
  );

  const openLightbox = () => {
    const svg = getSvg();
    if (svg) {
      setLightboxMarkup(lightboxSvgMarkup(svg, 'zoom'));
    }
  };

  const lockedHeight = zoomed && pz.viewport ? {height: pz.viewport.height} : undefined;

  return (
    <div
      ref={outerRef}
      className={clsx(styles.zoomable, styles.zoomableDiagram, info && styles.ready, zoomed && styles.zoomed)}
      data-rid-zoom="diagram"
      onClick={(event) => {
        if (info && shouldOpenOnClick(event, outerRef.current, zoomed)) {
          openLightbox();
        }
      }}>
      {/* The zoom button and the toolbar come first: the toolbar sticks below
          the navbar while a tall diagram scrolls past. */}
      {info ? (
        <button
          ref={triggerRef}
          type="button"
          className={styles.trigger}
          aria-label={`Zoom diagram: ${label}`}
          onKeyDown={(event) => {
            if (event.key === 'Escape' && zoomed) {
              event.preventDefault();
              pz.fitToView();
              return;
            }
            pz.handleKey(event);
          }}
        />
      ) : null}
      {info ? (
        <div className={styles.toolbarDock}>
          <ZoomControls
            kind="diagram"
            label={label}
            pz={pz}
            busy={busy}
            onFullScreen={openLightbox}
            onDownload={(format) => {
              void download(format).then((result) => {
                if (!result.ok) {
                  showNote(result.message);
                }
              });
            }}
          />
          <span className={styles.note} role="status">
            {note}
          </span>
        </div>
      ) : null}
      <div ref={viewportRef} className={styles.diagramViewport} style={lockedHeight}>
        {children}
      </div>
      {lightboxMarkup !== null && info ? (
        <Lightbox
          content={{kind: 'diagram', markup: lightboxMarkup}}
          label={label}
          natural={info.natural}
          onClose={() => setLightboxMarkup(null)}
          onDownload={(format) => download(format)}
          returnFocusRef={triggerRef}
        />
      ) : null}
    </div>
  );
}
