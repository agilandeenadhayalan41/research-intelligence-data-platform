-- Relationship refresh for `openalex.work_topics` (Step 15 / #22).
-- DEFINED IN STEP 15 — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Chosen strategy: REPLACE_BY_WORK_ID
--   1) DELETE existing relationships for the changed work_id set
--   2) INSERT the current projection for those Works
--
-- Rationale: topic membership for a Work is a full current set from the source
-- asset. MERGE at (work_id, topic_id) alone would leave stale topics that
-- disappeared from the source array. DELETE+INSERT for the work_id set preserves
-- grain (work_id, topic_id) without duplicates and is idempotent when replayed
-- with the same staging projection.
--
-- Coordinate with works MERGE at the orchestration layer. BigQuery does not
-- provide multi-table ACID identical to local Postgres ingestion transactions.
--
-- Staging tables (logical):
--   openalex.changed_work_ids(work_id STRING)
--   openalex.work_topics_staging(... same columns as work_topics ...)

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
  `source_updated_date`,
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
  source.`source_updated_date`,
  source.`run_id`,
  source.`processed_at`,
  source.`activity_state`,
  source.`deleted_at`
FROM `openalex.work_topics_staging` AS source
WHERE source.`work_id` IN (
  SELECT `work_id` FROM `openalex.changed_work_ids`
);
