import React from 'react';
import styles from './FlowGraphic.module.css';

// The stages of the target flow, named as on the How it works diagram.
const STAGES: ReadonlyArray<{title: string; note: string}> = [
  {title: 'Public OpenAlex snapshot', note: 'Anonymous HTTPS, no credentials'},
  {title: 'Immutable raw landing + provenance', note: 'Local folder or GCS'},
  {title: 'Portable canonical model', note: 'Entities and relationships'},
  {title: 'BigQuery analytical models', note: 'Partitioning and clustering follow query evidence'},
  {title: 'Quality gates', note: 'Hard gates before anything is visible'},
  {title: 'Data Service', note: 'Domain capabilities, no raw SQL'},
  {title: 'Consumers', note: 'Apps, APIs, dashboards, data science'},
];

/** A compact, CSS-only picture of the target data flow for the home page. */
export default function FlowGraphic(): React.JSX.Element {
  return (
    <figure className={styles.figure}>
      <ol className={styles.flow} aria-label="The target data flow">
        {STAGES.map((stage) => (
          <li key={stage.title} className={styles.stage}>
            <span className={styles.stageTitle}>{stage.title}</span>
            <span className={styles.stageNote}>{stage.note}</span>
          </li>
        ))}
      </ol>
      <figcaption className={styles.caption}>
        The target flow. It is not a picture of running infrastructure.
      </figcaption>
    </figure>
  );
}
