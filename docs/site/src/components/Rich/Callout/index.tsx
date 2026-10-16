import React, {type ReactNode} from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

export type CalloutType = 'note' | 'tip' | 'info' | 'warning' | 'danger';

const LABELS: Record<CalloutType, string> = {
  note: 'Note',
  tip: 'Tip',
  info: 'Info',
  warning: 'Warning',
  danger: 'Danger',
};

// Line icons drawn inline (nothing fetched); decorative, the type is also
// given as text to screen readers.
const ICONS: Record<CalloutType, ReactNode> = {
  note: <path d="M5 4h10l4 4v12H5zM14 4v5h5M8 13h8M8 17h5" />,
  tip: (
    <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" />
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.01" />
    </>
  ),
  warning: <path d="M12 4l9 16H3zM12 10v4.5M12 17.5v.01" />,
  danger: (
    <>
      <path d="M8.2 3h7.6L21 8.2v7.6L15.8 21H8.2L3 15.8V8.2z" />
      <path d="M12 7.5v5.5M12 16.5v.01" />
    </>
  ),
};

function isCalloutType(value: unknown): value is CalloutType {
  return typeof value === 'string' && Object.hasOwn(LABELS, value);
}

type CalloutProps = {
  /** note (default), tip, info, warning or danger. */
  type?: CalloutType;
  /** Optional title. Without it the content starts at once. */
  title?: ReactNode;
  children?: ReactNode;
};

/**
 * A highlighted block for a note, a tip or a warning, coloured by type. It
 * sits alongside the theme's :::note admonitions; unlike them it is plain JSX,
 * so on GitHub its content still reads as normal Markdown.
 */
export default function Callout({type = 'note', title, children}: CalloutProps): React.JSX.Element {
  const kind = isCalloutType(type) ? type : 'note';
  return (
    <div className={clsx(styles.callout, styles[kind])} role="note">
      <svg
        className={styles.icon}
        viewBox="0 0 24 24"
        width="20"
        height="20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false">
        {ICONS[kind]}
      </svg>
      <div className={styles.content}>
        {title != null ? (
          <p className={styles.title}>
            <span className="rid-sr-only">{LABELS[kind]}: </span>
            {title}
          </p>
        ) : (
          <span className="rid-sr-only">{LABELS[kind]}: </span>
        )}
        {children}
      </div>
    </div>
  );
}
