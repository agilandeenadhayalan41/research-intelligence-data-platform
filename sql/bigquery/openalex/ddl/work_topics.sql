-- BigQuery Standard SQL DDL for `openalex.work_topics`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per (work_id, topic_id)
-- Incremental: REPLACE_BY_WORK_ID

CREATE TABLE IF NOT EXISTS `openalex.work_topics` (
  `work_id` STRING NOT NULL,
  `topic_id` STRING NOT NULL,
  `score` FLOAT64,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
PARTITION BY `source_updated_date`
CLUSTER BY `work_id`, `topic_id`
;
