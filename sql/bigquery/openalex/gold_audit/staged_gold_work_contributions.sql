-- BigQuery staged Gold audit build: `openalex.staged_gold_work_contributions`
-- #103 port of analytics/gold/semantic_build.build_staged_gold_audit_tables.
-- DEFINED. Executed only by manual scripts on synthetic data (GCP_SANDBOX,
-- personal DEV/QA); NOT DEPLOYED.
-- Rendered by research_platform.analytics.gold.bigquery_audit; edit the
-- renderer, not this file.
-- Read by quality/active_gold_deleted_work_exclusion.sql
-- (HARD_GATE gold.deleted_source_work_exclusion).
-- Grain: one row per (mart_id, contributing ACTIVE work_id); only works that feed each mart
-- Every branch filters ACTIVE works, so the HARD_GATE over this table is
-- close to tautological; check the Gold outputs independently.

CREATE OR REPLACE TABLE `openalex.staged_gold_work_contributions` AS
-- research_discovery: every ACTIVE work
SELECT
  'research_discovery' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
UNION ALL
-- journal_author_stats: ACTIVE works with a non-NULL location source_id
SELECT DISTINCT
  'journal_author_stats' AS `mart_id`,
  wl.`work_id`
FROM `openalex.work_locations` AS wl
INNER JOIN `openalex.works` AS w
  ON w.`work_id` = wl.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND wl.`source_id` IS NOT NULL
UNION ALL
-- publisher_author_stats: ACTIVE works with a primary publisher
SELECT
  'publisher_author_stats' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL
UNION ALL
-- publisher_topic_year_stats: ACTIVE works with a primary publisher and a non-NULL topic
SELECT DISTINCT
  'publisher_topic_year_stats' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL
UNION ALL
-- publisher_topic_license_year_stats: ACTIVE works with a primary publisher and a non-NULL topic
SELECT DISTINCT
  'publisher_topic_license_year_stats' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL
UNION ALL
-- institution_topic_stats: ACTIVE works with a non-NULL institution and a non-NULL topic
SELECT DISTINCT
  'institution_topic_stats' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_author_institutions` AS wai
  ON wai.`work_id` = w.`work_id`
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND wai.`institution_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL
UNION ALL
-- publication_trends: every ACTIVE work
SELECT
  'publication_trends' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
UNION ALL
-- open_access_trends: every ACTIVE work
SELECT
  'open_access_trends' AS `mart_id`,
  w.`work_id`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
UNION ALL
-- citation_edges: ACTIVE source works that have reference rows
SELECT DISTINCT
  'citation_edges' AS `mart_id`,
  wr.`work_id`
FROM `openalex.work_references` AS wr
INNER JOIN `openalex.works` AS w
  ON w.`work_id` = wr.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
;
