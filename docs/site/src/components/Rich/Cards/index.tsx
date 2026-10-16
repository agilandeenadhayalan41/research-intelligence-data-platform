import React, {createContext, useContext, type ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Icon, {type IconName} from '@site/src/components/Home/Icon';
import styles from './styles.module.css';

// True inside <Cards>, so a <Card> knows whether it is a list item.
const InCards = createContext(false);

type CardsProps = {
  /** <Card> elements. */
  children: ReactNode;
  /** Accessible name for the list, when the nearby heading is not enough. */
  label?: string;
};

/** A responsive grid of cards: a list, so screen readers announce the count. */
export function Cards({children, label}: CardsProps): React.JSX.Element {
  return (
    <ul className={styles.grid} aria-label={label}>
      <InCards.Provider value={true}>{children}</InCards.Provider>
    </ul>
  );
}

export type CardProps = {
  /**
   * The card title. Leave it out to use the Markdown form: the card's text
   * then starts with a Markdown link, which becomes the title and the card's
   * link, so the card still reads as a link and a sentence on GitHub.
   */
  title?: string;
  /**
   * Where the card links: a relative doc path as in a Markdown link
   * (`status.md#environment-readiness`, resolved at build time by
   * src/remark/jsx-links.mjs), a site route or an external URL.
   */
  href?: string;
  icon?: IconName;
  /** A one-line description; longer text can go in the children. */
  description?: ReactNode;
  children?: ReactNode;
};

/**
 * One card. When linked, the title is the only link (its accessible name is
 * the title) and a stretched ::after makes the whole card clickable.
 */
export function Card({title, href, icon, description, children}: CardProps): React.JSX.Element {
  const Item = useContext(InCards) ? 'li' : 'div';
  const iconBox = icon && (
    <span className={styles.icon}>
      <Icon name={icon} />
    </span>
  );
  if (title == null) {
    // Markdown form: CSS turns the leading Markdown link into the title.
    return (
      <Item className={clsx(styles.card, styles.markdownCard, icon && styles.withIcon)}>
        {iconBox}
        <div className={styles.body}>
          {description != null && <p>{description}</p>}
          {children}
        </div>
        <span className={styles.arrow} aria-hidden="true">
          →
        </span>
      </Item>
    );
  }
  return (
    <Item className={clsx(styles.card, href && styles.linked)}>
      <div className={styles.header}>
        {iconBox}
        <p className={styles.title}>
          {href ? (
            <Link to={href} className={styles.link}>
              {title}
            </Link>
          ) : (
            title
          )}
        </p>
        {href && (
          <span className={styles.arrow} aria-hidden="true">
            →
          </span>
        )}
      </div>
      {description != null && <p className={styles.description}>{description}</p>}
      {children != null && <div className={styles.body}>{children}</div>}
    </Item>
  );
}
