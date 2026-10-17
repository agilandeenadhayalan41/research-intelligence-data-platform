import React, {Children, isValidElement, type ReactNode} from 'react';
import clsx from 'clsx';
import {badgeTone, isBadgeTone, type BadgeTone} from '@site/src/lib/badgeTone.mjs';
import styles from './styles.module.css';

type BadgeProps = {
  /** The status label, for example NOT_READY. Defaults to the text inside. */
  status?: string;
  /** Overrides the colour picked from the status. */
  tone?: BadgeTone;
  /** What the badge shows. Defaults to the status. */
  children?: ReactNode;
};

/** The plain text of rendered MDX children (strings inside <code>, <strong>...). */
function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') {
        return String(child);
      }
      if (isValidElement<{children?: ReactNode}>(child)) {
        return textOf(child.props.children);
      }
      return '';
    })
    .join('');
}

/**
 * A status label in a small coloured pill: green for proven (VERIFIED, MET),
 * amber for partial or unverified, red for missing or blocking (NOT_READY,
 * BLOCKED), blue for defined or local, grey otherwise. The colour comes from
 * src/lib/badgeTone.mjs; the text is shown as written.
 */
export default function Badge({status, tone, children}: BadgeProps): React.JSX.Element {
  const label = status ?? textOf(children);
  const resolved = isBadgeTone(tone) ? tone : badgeTone(label);
  return (
    <span className={clsx(styles.badge, styles[resolved])}>{children ?? status}</span>
  );
}
