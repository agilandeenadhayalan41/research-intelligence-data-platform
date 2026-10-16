// "Page history" at the end of a doc or blog post: the last change and the
// people who changed the page (GitHub avatars and logins, linked to their
// commits). Build-time git data first, GitHub's data once it loads.

import React from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Avatar from '../GitHubUserNavbarItem/Avatar';
import {formatDay, plural} from '../PageMetaBar/format';
import {usePageHistory, type HistoryPerson, type PageHistory as History} from './usePageHistory';
import styles from './styles.module.css';

/** Contributors shown before "and N more". */
const MAX_PEOPLE = 12;

type Props = {
  /** Repository path of the page's file, from repoPathForDoc(metadata.source). */
  repoPath: string | null;
  /** Id of the section heading (the meta bar links to it). */
  id?: string;
};

function ExternalLink({href, className, children, title}: {
  href: string | null;
  className?: string;
  title?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  if (!href) {
    return <span className={className}>{children}</span>;
  }
  return (
    <a href={href} className={className} title={title} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

function Person({person, locale}: {person: HistoryPerson; locale: string}): React.JSX.Element {
  const label = person.login ?? person.name;
  const day = formatDay(person.lastDate, locale);
  const description = [
    person.login && person.name !== person.login ? person.name : null,
    plural(person.commits, 'commit', 'commits'),
    day ? `latest ${day}` : null,
  ]
    .filter(Boolean)
    .join(', ');
  return (
    <li className={styles.person}>
      <ExternalLink
        href={person.url}
        className={styles.personLink}
        title={person.url ? `${label}: ${description}. Open on GitHub` : undefined}>
        <Avatar src={person.avatarUrl} name={label} size="md" />
        <span className={styles.personText}>
          <span className={styles.personName}>{label}</span>
          <span className={styles.personCommits}>{plural(person.commits, 'commit', 'commits')}</span>
        </span>
      </ExternalLink>
    </li>
  );
}

function SourceNote({history}: {history: History}): React.JSX.Element {
  let text: string;
  if (history.source === 'github') {
    text = 'From GitHub.';
  } else if (history.loading) {
    text = 'From the site build. Checking GitHub…';
  } else if (history.error) {
    text = `From the site build. ${history.error.message}`;
  } else {
    text = history.source === 'build' ? 'From the site build.' : '';
  }
  return (
    <span className={styles.source} aria-live="polite">
      {text}
    </span>
  );
}

function PageHistoryContent({repoPath, id = 'page-history'}: Props): React.JSX.Element | null {
  const {i18n} = useDocusaurusContext();
  const locale = i18n.currentLocale;
  const history = usePageHistory(repoPath);
  const {last, people, historyUrl} = history;

  if (!repoPath) {
    return null;
  }

  const shown = people.slice(0, MAX_PEOPLE);
  const more = people.length - shown.length;
  const day = formatDay(last?.date, locale);
  const commitsText = history.commitsCapped
    ? `${history.commits}+ commits`
    : plural(history.commits, 'commit', 'commits');

  return (
    <section className={styles.history} aria-labelledby={id}>
      <Heading as="h2" id={id} className={styles.title}>
        Page history
      </Heading>

      {last ? (
        <p className={styles.last}>
          <Avatar src={last.avatarUrl} name={last.login ?? last.name} size="sm" />
          <span>
            Last updated{day && (
              <>
                {' '}
                <time dateTime={last.date ?? undefined}>{day}</time>
              </>
            )}{' '}
            by{' '}
            <ExternalLink href={last.profileUrl} className={styles.strong}>
              {last.login ?? last.name}
            </ExternalLink>
            {last.url && (
              <>
                {' · '}
                <ExternalLink href={last.url}>View the change</ExternalLink>
              </>
            )}
          </span>
        </p>
      ) : (
        <p className={styles.last}>This page has no recorded changes yet.</p>
      )}

      {people.length > 0 && (
        <>
          <p className={styles.summary}>
            {people.length === 1 ? '1 person has' : `${people.length} people have`} changed this page
            {history.commits > 0 ? ` in ${commitsText}` : ''}.
          </p>
          <ul className={styles.people}>
            {shown.map((person) => (
              <Person key={person.key} person={person} locale={locale} />
            ))}
          </ul>
          {more > 0 && <p className={styles.more}>and {plural(more, 'other', 'others')}.</p>}
        </>
      )}

      <p className={clsx('rid-muted-note', styles.footer)}>
        <SourceNote history={history} />
        {historyUrl && (
          <ExternalLink href={historyUrl} className={styles.historyLink}>
            Full history on GitHub
          </ExternalLink>
        )}
      </p>
    </section>
  );
}

/** Page history of the file at `repoPath`; renders nothing without one. */
export default function PageHistory(props: Props): React.JSX.Element {
  // A new page (another file) starts with fresh state.
  return <PageHistoryContent key={props.repoPath ?? ''} {...props} />;
}
