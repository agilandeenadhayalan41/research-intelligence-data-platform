import React, {useCallback, useEffect, useRef, useState} from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';

type Props = {
  /** Path under static/, e.g. "/diagrams/x.html". */
  src: string;
  title: string;
  /** Height used until the embedded page has loaded and been measured. */
  initialHeight?: number;
};

/**
 * Embeds a same-origin static HTML page at full content width and grows the
 * frame to the page's own height, so the reader scrolls once instead of
 * inside a fixed-size box. The page keeps its own layout and scripts.
 */
export default function AutoHeightFrame({src, title, initialHeight = 900}: Props): React.JSX.Element {
  const url = useBaseUrl(src);
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(initialHeight);

  const measure = useCallback(() => {
    // The body's own height (not the document's), so the frame can also shrink.
    const body = frame.current?.contentDocument?.body;
    if (body && body.scrollHeight > 0) {
      setHeight(Math.ceil(body.scrollHeight));
    }
  }, []);

  useEffect(() => {
    const node = frame.current;
    if (!node) {
      return undefined;
    }
    let observer: ResizeObserver | undefined;
    const onLoad = () => {
      const doc = node.contentDocument;
      if (!doc?.body || doc.URL === 'about:blank') {
        return;
      }
      measure();
      if (!observer && typeof ResizeObserver !== 'undefined') {
        // The page changes height when its zoom tabs switch views.
        observer = new ResizeObserver(measure);
        observer.observe(doc.body);
      }
    };
    node.addEventListener('load', onLoad);
    // The server-rendered frame can finish loading before React hydrates and
    // attaches the listener, so measure now if it already has.
    if (node.contentDocument?.readyState === 'complete') {
      onLoad();
    }
    window.addEventListener('resize', measure);
    return () => {
      node.removeEventListener('load', onLoad);
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, [measure]);

  return (
    <>
      <p>
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open full screen ↗
        </a>
      </p>
      <iframe
        ref={frame}
        src={url}
        title={title}
        loading="lazy"
        scrolling="no"
        style={{display: 'block', width: '100%', height, border: 0, borderRadius: 12}}
      />
    </>
  );
}
