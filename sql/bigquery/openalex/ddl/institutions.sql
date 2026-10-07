-- BigQuery Standard SQL DDL for `openalex.institutions`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per institution_id
-- Incremental: MERGE_BY_PRIMARY_KEY

CREATE TABLE IF NOT EXISTS `openalex.institutions` (
  `institution_id` STRING NOT NULL,
  `institution_id_url` STRING NOT NULL,
  `display_name` STRING,
  `ror` STRING,
  `country_code` STRING,
  `institution_type` STRING,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
CLUSTER BY `institution_id`
;
