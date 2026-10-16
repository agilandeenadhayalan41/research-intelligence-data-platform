import React, {type ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Icon, {type IconName} from './Icon';
import styles from './FeatureCards.module.css';

type FeatureCardsProps = {
  /** <FeatureCard> elements. */
  children: ReactNode;
  /** Accessible name for the list, when the section heading is not enough. */
  label?: string;
};

/** A responsive grid of cards (a list, so screen readers announce the count). */
export default function FeatureCards({children, label}: FeatureCardsProps): React.JSX.Element {
  return (
    <ul className={styles.grid} aria-label={label}>
      {children}
    </ul>
  );
}

type FeatureCardProps = {
  title: string;
  /** Site route the whole card links to. Without it the card is plain text. */
  to?: string;
  icon?: IconName;
  /** The card text, written in the MDX page. */
  children?: ReactNode;
};

/**
 * One card. When linked, the title is the only link (so its accessible name
 * is the title) and a stretched ::after makes the whole card clickable.
 */
export function FeatureCard({title, to, icon, children}: FeatureCardProps): React.JSX.Element {
  return (
    <li className={clsx(styles.card, to && styles.linked, icon && styles.withIcon)}>
      {icon && (
        <span className={styles.icon}>
          <Icon name={icon} />
        </span>
      )}
      <h3 className={styles.title}>
        {to ? (
          <Link to={to} className={styles.link}>
            {title}
          </Link>
        ) : (
          title
        )}
      </h3>
      {children != null && <div className={styles.body}>{children}</div>}
      {to && (
        <span className={styles.arrow} aria-hidden="true">
          →
        </span>
      )}
    </li>
  );
}
