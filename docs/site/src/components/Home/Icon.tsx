import React from 'react';

/*
 * Small line icons for the home page, drawn inline as SVG (no image files,
 * nothing fetched). They inherit the text colour and are decorative: every
 * icon sits next to a visible title, so it is hidden from screen readers.
 */

const PATHS = {
  flow: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.5 6h7M7.2 8.3l3.6 7.4M16.8 8.3l-3.6 7.4" />
    </>
  ),
  status: <path d="M3 12h4l2.5-6 5 12 2.5-6H21" />,
  roadmap: (
    <>
      <path d="M5 21V4" />
      <path d="M5 4h12l-2.5 4 2.5 4H5" />
    </>
  ),
  decision: (
    <>
      <path d="M12 3v18M8 21h8M5 7h14" />
      <path d="M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z" />
    </>
  ),
  register: (
    <>
      <path d="M11 6h10M11 12h10M11 18h10" />
      <path d="M3 6l1.5 1.5L7.5 4.5M3 12l1.5 1.5 3-3M3 18l1.5 1.5 3-3" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 9l3 3-3 3M13 15h4" />
    </>
  ),
  book: (
    <>
      <path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5z" />
      <path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3M8 7h8M8 11h5" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>
  ),
  laptop: (
    <>
      <rect x="4" y="5" width="16" height="11" rx="1.5" />
      <path d="M2 19h20" />
    </>
  ),
  server: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
} as const;

export type IconName = keyof typeof PATHS;

type Props = {
  name: IconName;
  className?: string;
};

export default function Icon({name, className}: Props): React.JSX.Element {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false">
      {PATHS[name]}
    </svg>
  );
}
