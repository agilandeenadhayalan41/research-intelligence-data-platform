-- #23 PROTOTYPE: selective PostgreSQL/AlloyDB operational projection for Work point lookups.
-- The #23 gate was NOT met: in #118 (personal DEV, synthetic data) BigQuery + an
-- in-process cache missed the operator-stated 200 ms p95 cold but met it prewarmed,
-- and #23 was closed not_planned. This is local-only prototype DDL, not an
-- approved serving store. Idempotent.
--
-- Grain: one row per ACTIVE Work in the published consumer snapshot. DELETED
-- Works are absent. No author/topic/institution/citation relationship tables:
-- multi-valued ids are carried as arrays for the lookup payload only.

CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS serving.work_lookup (
    work_id              text PRIMARY KEY,
    doi                  text,
    title                text,
    publication_year     integer,
    publication_date     text,
    work_type            text,
    language             text,
    is_oa                boolean,
    oa_status            text,
    primary_source_id    text,
    primary_publisher_id text,
    author_ids           text[] NOT NULL DEFAULT '{}',
    topic_ids            text[] NOT NULL DEFAULT '{}',
    institution_ids      text[] NOT NULL DEFAULT '{}'
);

-- DOI is not guaranteed unique (registry: collisions at full scale unknown), so
-- the index is non-unique and lookups take the lowest work_id.
CREATE INDEX IF NOT EXISTS work_lookup_doi_idx
    ON serving.work_lookup (doi, work_id)
    WHERE doi IS NOT NULL;

-- One row: what the projection currently serves and where it came from.
CREATE TABLE IF NOT EXISTS serving.work_lookup_state (
    singleton             boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    rows_fingerprint      text NOT NULL,
    row_count             integer NOT NULL CHECK (row_count >= 0),
    freshness_status      text NOT NULL,
    generated_at          text,
    source_max_updated_at text,
    as_of_run_id          text,
    freshness_notes       text NOT NULL DEFAULT '',
    published_at          timestamptz NOT NULL
);
