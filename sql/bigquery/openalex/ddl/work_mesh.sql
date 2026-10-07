-- BigQuery Standard SQL DDL for `openalex.work_mesh`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per (work_id, mesh_index)
-- Incremental: REPLACE_BY_WORK_ID

CREATE TABLE IF NOT EXISTS `openalex.work_mesh` (
  `work_id` STRING NOT NULL,
  `mesh_index` INT64 NOT NULL,
  `descriptor_ui` STRING NOT NULL,
  `descriptor_name` STRING,
  `qualifier_ui` STRING,
  `qualifier_name` STRING,
  `is_major_topic` BOOL,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
PARTITION BY `source_updated_date`
CLUSTER BY `work_id`, `descriptor_ui`
;
