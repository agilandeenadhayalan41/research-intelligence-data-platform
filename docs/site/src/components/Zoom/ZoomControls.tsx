// The small in-page toolbar of a zoomable image or diagram: zoom in, zoom
// out, reset, full screen and download. It shows on hover, on keyboard focus
// and while the element is zoomed in place.

import React, {useEffect, useId, useRef, useState} from 'react';
import type {PanZoom} from '@site/src/lib/usePanZoom';

import ZoomIcon from './icons';
import ToolButton from './ToolButton';
import styles from './styles.module.css';

/** The two download choices of a diagram, behind one Download button. */
function DiagramDownloadMenu({
  onDownload,
  busy,
  onOpenChange,
}: {
  onDownload: (format: 'svg' | 'png') => void;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    onOpenChange(open);
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (wrapRef.current && event.target instanceof Node && !wrapRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, onOpenChange]);

  const choose = (format: 'svg' | 'png') => {
    setOpen(false);
    buttonRef.current?.focus();
    onDownload(format);
  };

  return (
    <span
      ref={wrapRef}
      className={styles.downloadWrap}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          buttonRef.current?.focus();
        }
      }}
      onBlur={(event) => {
        if (open && !(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) {
          setOpen(false);
        }
      }}>
      <ToolButton
        ref={buttonRef}
        icon="download"
        label="Download the diagram"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={busy}
        onClick={() => setOpen((value) => !value)}
      />
      {open ? (
        <span className={styles.downloadMenu} id={menuId} role="group" aria-label="Download format">
          <button type="button" className={styles.toolButton} onClick={() => choose('svg')}>
            <ZoomIcon name="download" />
            SVG<span className={styles.choiceHint}>vector</span>
          </button>
          <button type="button" className={styles.toolButton} onClick={() => choose('png')}>
            <ZoomIcon name="download" />
            PNG<span className={styles.choiceHint}>high resolution</span>
          </button>
        </span>
      ) : null}
    </span>
  );
}

export interface ZoomControlsProps {
  kind: 'image' | 'diagram';
  /** The alt text or the diagram label, for the group's name. */
  label: string;
  pz: PanZoom;
  onFullScreen: () => void;
  onDownload: (format?: 'svg' | 'png') => void;
  busy: boolean;
}

export default function ZoomControls({kind, label, pz, onFullScreen, onDownload, busy}: ZoomControlsProps): React.JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);
  const noun = kind === 'image' ? 'image' : 'diagram';
  return (
    <span
      className={styles.toolbar}
      role="group"
      aria-label={`Zoom controls: ${label}`}
      data-panzoom-ignore=""
      data-open={menuOpen ? 'true' : undefined}>
      <ToolButton icon="zoomIn" label={`Zoom in on the ${noun}`} onClick={pz.zoomIn} disabled={!pz.canZoomIn} />
      <ToolButton icon="zoomOut" label={`Zoom out of the ${noun}`} onClick={pz.zoomOut} disabled={!pz.canZoomOut} />
      <ToolButton icon="reset" label="Reset zoom" onClick={pz.fitToView} disabled={pz.atFit} />
      <ToolButton icon="fullscreen" label={`View the ${noun} full screen`} onClick={onFullScreen} />
      {kind === 'image' ? (
        <ToolButton icon="download" label="Download the original image" onClick={() => onDownload()} disabled={busy} />
      ) : (
        <DiagramDownloadMenu onDownload={onDownload} busy={busy} onOpenChange={setMenuOpen} />
      )}
    </span>
  );
}
