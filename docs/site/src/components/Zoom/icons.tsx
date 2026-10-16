// Small stroke icons for the zoom controls (24 x 24, currentColor). They are
// decorative: every button that shows one has its own label.

import React from 'react';

export type ZoomIconName =
  | 'zoomIn'
  | 'zoomOut'
  | 'fit'
  | 'actual'
  | 'reset'
  | 'fullscreen'
  | 'external'
  | 'download'
  | 'close';

const PATHS: Record<ZoomIconName, React.ReactNode> = {
  zoomIn: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M15.5 15.5 20 20M10.5 7.5v6M7.5 10.5h6" />
    </>
  ),
  zoomOut: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M15.5 15.5 20 20M7.5 10.5h6" />
    </>
  ),
  fit: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  actual: (
    <>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2" />
      <path d="M8 10l1.5-1v6M14.5 10l1.5-1v6M12 11v.01M12 14v.01" />
    </>
  ),
  reset: (
    <>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M4.5 4.5v4h4" />
    </>
  ),
  fullscreen: <path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5M4 4l6 6M20 4l-6 6M4 20l6-6M20 20l-6-6" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  download: <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
};

export default function ZoomIcon({name}: {name: ZoomIconName}): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false">
      {PATHS[name]}
    </svg>
  );
}
