-- BigQuery Standard SQL DDL for `openalex.publishers`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per publisher_id
-- Incremental: MERGE_BY_PRIMARY_KEY

CREATE TABLE IF NOT EXISTS `openalex.publishers` (
  `publisher_id` STRING NOT NULL,
  `publisher_id_url` STRING NOT NULL,
  `display_name` STRING,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `lineage_source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
CLUSTER BY `publisher_id`
;
