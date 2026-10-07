-- BigQuery Standard SQL DDL for `openalex.work_authors`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per (work_id, authorship_index)
-- Incremental: REPLACE_BY_WORK_ID

CREATE TABLE IF NOT EXISTS `openalex.work_authors` (
  `work_id` STRING NOT NULL,
  `authorship_index` INT64 NOT NULL,
  `author_id` STRING,
  `author_position` STRING,
  `is_corresponding` BOOL,
  `raw_author_name` STRING,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `lineage_source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
PARTITION BY `lineage_source_updated_date`
CLUSTER BY `work_id`, `author_id`
;
