-- Relationship refresh for `openalex.work_topics` (Step 15 / #22 hardening).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Chosen strategy: REPLACE_BY_WORK_ID inside a BigQuery multi-statement
-- transaction so DELETE+INSERT commit atomically. A failure between the
-- statements rolls back; the analytical table is not left empty for those Works.
--
-- Template for all REPLACE_BY_WORK_ID relationship tables.
--
-- BigQuery DML transactions can mutate multiple tables atomically, but they
-- differ operationally from the local PostgreSQL ingestion transaction
-- (connection model, scripting, orchestration). Do not claim identical ACID
-- behavior to Step 12 Postgres UoW — do claim atomic replacement of the
-- relationship projection for the changed work_id set.
--
-- Staging tables (logical):
--   openalex.changed_work_ids(work_id STRING)
--   openalex.work_topics_staging(... same columns as work_topics ...)

BEGIN TRANSACTION;

-- Step A: remove current topics for changed Works.
DELETE FROM `openalex.work_topics` AS target
WHERE target.`work_id` IN (
  SELECT `work_id` FROM `openalex.changed_work_ids`
);

-- Step B: insert current projection for those Works.
INSERT INTO `openalex.work_topics` (
  `work_id`,
  `topic_id`,
  `score`,
  `source_asset_id`,
  `source_checksum_sha256`,
  `lineage_source_updated_date`,
  `run_id`,
  `processed_at`,
  `activity_state`,
  `deleted_at`
)
SELECT
  source.`work_id`,
  source.`topic_id`,
  source.`score`,
  source.`source_asset_id`,
  source.`source_checksum_sha256`,
  source.`lineage_source_updated_date`,
  source.`run_id`,
  source.`processed_at`,
  source.`activity_state`,
  source.`deleted_at`
FROM `openalex.work_topics_staging` AS source
WHERE source.`work_id` IN (
  SELECT `work_id` FROM `openalex.changed_work_ids`
);

COMMIT TRANSACTION;
