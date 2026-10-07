-- BigQuery Standard SQL DDL for `openalex.sources`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per source_id
-- Incremental: MERGE_BY_PRIMARY_KEY

CREATE TABLE IF NOT EXISTS `openalex.sources` (
  `source_id` STRING NOT NULL,
  `source_id_url` STRING NOT NULL,
  `display_name` STRING,
  `source_type` STRING,
  `issn_l` STRING,
  `host_publisher_id` STRING,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
CLUSTER BY `source_id`
;
