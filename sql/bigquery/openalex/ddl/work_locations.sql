-- BigQuery Standard SQL DDL for `openalex.work_locations`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per (work_id, location_index)
-- Incremental: REPLACE_BY_WORK_ID

CREATE TABLE IF NOT EXISTS `openalex.work_locations` (
  `work_id` STRING NOT NULL,
  `location_index` INT64 NOT NULL,
  `location_origin` STRING NOT NULL,
  `source_id` STRING,
  `is_oa` BOOL,
  `landing_page_url` STRING,
  `pdf_url` STRING,
  `license` STRING,
  `version` STRING,
  `is_primary` BOOL NOT NULL,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `lineage_source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
PARTITION BY `lineage_source_updated_date`
CLUSTER BY `work_id`, `source_id`
;
