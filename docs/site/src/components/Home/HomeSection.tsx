import React, {type ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './HomeSection.module.css';

type HomeSectionProps = {
  /** Heading id, also the #anchor of the section. */
  id: string;
  title: string;
  /** One line under the heading. */
  intro?: ReactNode;
  /**
   * 'steps' lays a Markdown ordered list out as numbered tiles;
   * 'note' renders the body as a tinted panel.
   */
  variant?: 'default' | 'steps' | 'note';
  /** The section body, written in Markdown in the MDX page. */
  children: ReactNode;
};

/** A titled, width-limited band of the home page. */
export default function HomeSection({
  id,
  title,
  intro,
  variant = 'default',
  children,
}: HomeSectionProps): React.JSX.Element {
  return (
    <section
      className={clsx(styles.section, variant !== 'default' && styles[variant])}
      aria-labelledby={id}>
      <div className={styles.header}>
        <Heading as="h2" id={id} className={styles.title}>
          {title}
        </Heading>
        {intro != null && <p className={styles.intro}>{intro}</p>}
      </div>
      <div className={styles.body}>{children}</div>
    </section>
  );
}
