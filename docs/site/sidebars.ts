import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

// Doc ids are paths relative to the repository's docs/ folder, without the
// .md extension. Every docs/architecture/*.md file must appear exactly once.
// Labels are set here so the architecture files keep their own H1s.

const sidebars: SidebarsConfig = {
  docs: [
    {
      type: 'category',
      label: 'Overview',
      collapsible: false,
      items: [
        'overview/introduction',
        'overview/how-it-works',
        'overview/status',
        {type: 'doc', id: 'architecture/roadmap', label: 'Roadmap'},
        'overview/getting-started',
        'overview/glossary',
      ],
    },
    {
      type: 'category',
      label: 'Architecture principles',
      items: [
        {
          type: 'doc',
          id: 'architecture/platform-architecture',
          label: 'Platform architecture',
        },
        {
          type: 'doc',
          id: 'architecture/gcp-bigquery-first',
          label: 'BigQuery-first on GCP',
        },
        {type: 'doc', id: 'architecture/query-routing', label: 'Query routing'},
      ],
    },
    {
      // Roadmap-step order: each page builds on the ones above it.
      type: 'category',
      label: 'Sources and ingestion',
      items: [
        {
          type: 'doc',
          id: 'architecture/public-data-sources',
          label: 'Public data sources',
        },
        {
          type: 'doc',
          id: 'architecture/openalex-manifest-contract',
          label: 'Discovery and manifest',
        },
        {
          type: 'doc',
          id: 'architecture/openalex-source-profiling',
          label: 'Source profiling',
        },
        {
          type: 'doc',
          id: 'architecture/immutable-local-landing',
          label: 'Raw landing (local + GCS)',
        },
        {
          type: 'doc',
          id: 'architecture/pipeline-control',
          label: 'Pipeline control',
        },
        {
          type: 'doc',
          id: 'architecture/openalex-canonical-model',
          label: 'Canonical model',
        },
        {
          type: 'doc',
          id: 'architecture/local-works-ingestion',
          label: 'Works ingestion',
        },
        {
          type: 'doc',
          id: 'architecture/openalex-deletions',
          label: 'Deletions',
        },
      ],
    },
    {
      type: 'category',
      label: 'Analytics',
      items: [
        {
          type: 'doc',
          id: 'architecture/query-pattern-benchmarks',
          label: 'Query-pattern registry',
        },
        {
          type: 'doc',
          id: 'architecture/bigquery-analytical-models',
          label: 'BigQuery models',
        },
        {
          type: 'doc',
          id: 'architecture/gold-analytical-marts',
          label: 'Gold marts',
        },
        {type: 'doc', id: 'architecture/data-quality', label: 'Data quality'},
      ],
    },
    {
      type: 'category',
      label: 'Serving',
      items: [
        {type: 'doc', id: 'architecture/data-service', label: 'Data Service'},
        {
          type: 'doc',
          id: 'architecture/serving-work-lookup-projection',
          label: '#23 lookup prototype',
        },
        {
          type: 'doc',
          id: 'architecture/postgres-warehouse',
          label: 'PostgreSQL adapter (optional)',
        },
      ],
    },
    {
      type: 'category',
      label: 'Operations and readiness',
      items: [
        {
          type: 'doc',
          id: 'architecture/end-to-end-pipeline',
          label: 'End-to-end runner',
        },
        {type: 'doc', id: 'architecture/orchestration', label: 'Orchestration'},
        {
          type: 'doc',
          id: 'architecture/environment-readiness',
          label: 'Environment readiness',
        },
      ],
    },
    {
      type: 'category',
      label: 'Proposed (next) architecture',
      items: [
        {type: 'doc', id: 'proposed/target-architecture', label: 'Target architecture'},
      ],
    },
  ],
};

export default sidebars;
