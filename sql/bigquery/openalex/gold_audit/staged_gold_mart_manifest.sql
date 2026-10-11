-- BigQuery staged Gold audit build: `openalex.staged_gold_mart_manifest`
-- #103 port of analytics/gold/semantic_build.build_staged_gold_audit_tables.
-- DEFINED, NOT YET DEPLOYED / NOT EXECUTED here.
-- Rendered by research_platform.analytics.gold.bigquery_audit; edit the
-- renderer, not this file.
-- Read by quality/active_gold_deleted_work_exclusion.sql
-- (HARD_GATE gold.deleted_source_work_exclusion).
-- Grain: one row per load_gold_registry() mart_id
-- The list must equal the expected_marts UNNEST in the quality file.

CREATE OR REPLACE TABLE `openalex.staged_gold_mart_manifest` AS
SELECT
  mart_id
FROM UNNEST([
  'research_discovery',
  'journal_author_stats',
  'publisher_author_stats',
  'publisher_topic_year_stats',
  'publisher_topic_license_year_stats',
  'institution_topic_stats',
  'publication_trends',
  'open_access_trends',
  'citation_edges'
]) AS mart_id
;
