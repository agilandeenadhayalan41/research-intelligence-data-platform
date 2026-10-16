// One line above a doc's content:
//   7 min read · Updated Oct 14, 2026 by <name> · 3 contributors
// The reading time is build-time data; the update and the contributors start
// from the build-time git history and switch to GitHub's data once it loads
// (see usePageHistory).

import React from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {usePageHistory} from '../PageHistory/usePageHistory';
import {formatDay, plural} from './format';
import styles from './styles.module.css';

type Props = {
  /** Repository path of the page's file, from repoPathForDoc(metadata.source). */
  repoPath: string | null;
  /** Id of the page history section, linked from the contributor count. */
  historyAnchor?: string;
};

function ClockIcon(): React.JSX.Element {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 4.5V8l2.5 1.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function PencilIcon(): React.JSX.Element {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M10.5 2.5l3 3L6 13H3v-3l7.5-7.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PeopleIcon(): React.JSX.Element {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="6" cy="5.5" r="2.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M1.75 13.25c.5-2.25 2.2-3.5 4.25-3.5s3.75 1.25 4.25 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M10.5 3.6a2.25 2.25 0 010 4.3M12 9.9c1.2.5 2 1.6 2.25 3.35" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export default function PageMetaBar({repoPath, historyAnchor = 'page-history'}: Props): React.JSX.Element | null {
  const {i18n} = useDocusaurusContext();
  const history = usePageHistory(repoPath);
  const minutes = history.meta?.readingMinutes ?? null;
  const last = history.last;
  const day = formatDay(last?.date, i18n.currentLocale);
  const contributors = history.people.length;

  if (minutes === null && !last && contributors === 0) {
    return null;
  }

  return (
    <ul className={styles.bar} aria-label="About this page">
      {minutes !== null && (
        <li className={styles.item}>
          <ClockIcon />
          <span>{minutes} min read</span>
        </li>
      )}
      {last && (
        <li className={styles.item}>
          <PencilIcon />
          <span>
            Updated
            {day && (
              <>
                {' '}
                <time dateTime={last.date ?? undefined}>{day}</time>
              </>
            )}{' '}
            by{' '}
            {last.profileUrl ? (
              <a href={last.profileUrl} target="_blank" rel="noopener noreferrer" className={styles.person}>
                {last.name}
              </a>
            ) : (
              <span className={styles.person}>{last.name}</span>
            )}
          </span>
        </li>
      )}
      {contributors > 0 && (
        <li className={styles.item}>
          <PeopleIcon />
          <a href={`#${historyAnchor}`} className={styles.contributors}>
            {plural(contributors, 'contributor', 'contributors')}
          </a>
        </li>
      )}
    </ul>
  );
}
