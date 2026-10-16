import React, {type ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import styles from './Hero.module.css';

type HeroProps = {
  /** The page's only h1. */
  title: string;
  /** Short label above the title. */
  eyebrow?: string;
  /** Line under the title. Defaults to the site tagline from the config. */
  tagline?: string;
  /** Shown beside the text on wide screens and under it on narrow ones. */
  graphic?: ReactNode;
  /** Lead text and <HeroActions>, written in the MDX page. */
  children?: ReactNode;
};

/** The home page banner: title, tagline, lead text and call-to-action buttons. */
export default function Hero({
  title,
  eyebrow,
  tagline,
  graphic,
  children,
}: HeroProps): React.JSX.Element {
  const {siteConfig} = useDocusaurusContext();
  const subtitle = tagline ?? siteConfig.tagline;
  return (
    <header className={clsx(styles.hero, graphic != null && styles.withGraphic)}>
      <div className={styles.text}>
        {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}
        <h1 className={styles.title}>{title}</h1>
        {subtitle && <p className={styles.tagline}>{subtitle}</p>}
        <div className={styles.lead}>{children}</div>
      </div>
      {graphic != null && <div className={styles.graphic}>{graphic}</div>}
    </header>
  );
}

/** A row of call-to-action links, wrapping on narrow screens. */
export function HeroActions({children}: {children: ReactNode}): React.JSX.Element {
  return <div className={styles.actions}>{children}</div>;
}

type ButtonLinkProps = {
  to: string;
  /** The filled style; use it for the one main action. */
  primary?: boolean;
  children: ReactNode;
};

/** A site link styled as a large Infima button. */
export function ButtonLink({to, primary = false, children}: ButtonLinkProps): React.JSX.Element {
  return (
    <Link
      to={to}
      className={clsx(
        'button button--lg',
        primary ? 'button--primary' : 'button--outline button--primary',
        styles.button,
      )}>
      {children}
    </Link>
  );
}
