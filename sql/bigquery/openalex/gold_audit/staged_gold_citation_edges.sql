-- BigQuery staged Gold audit build: `openalex.staged_gold_citation_edges`
-- #103 port of analytics/gold/semantic_build.build_staged_gold_audit_tables.
-- DEFINED, NOT YET DEPLOYED / NOT EXECUTED here.
-- Rendered by research_platform.analytics.gold.bigquery_audit; edit the
-- renderer, not this file.
-- Read by quality/active_gold_deleted_work_exclusion.sql
-- (HARD_GATE gold.deleted_source_work_exclusion).
-- Grain: one row per (source_work_id, reference_index) from gold/citation_edges.sql
-- Inner SELECT copied verbatim from sql/bigquery/openalex/gold/citation_edges.sql.

CREATE OR REPLACE TABLE `openalex.staged_gold_citation_edges` AS
SELECT
  edges.`source_work_id`,
  edges.`reference_index`,
  edges.`referenced_work_id`,
  edges.`target_activity_state`
FROM (
  SELECT
    wr.work_id AS source_work_id,
    wr.reference_index,
    wr.referenced_work_id,
    wr.reference_status,
    tw.activity_state AS target_activity_state
  FROM `openalex.work_references` AS wr
  INNER JOIN `openalex.works` AS sw
    ON sw.work_id = wr.work_id
  LEFT JOIN `openalex.works` AS tw
    ON tw.work_id = wr.referenced_work_id
  WHERE sw.activity_state = 'ACTIVE'
) AS edges
;
