-- BigQuery Standard SQL DDL for `openalex.works`
-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.
-- Grain: one row per work_id
-- Incremental: MERGE_BY_PRIMARY_KEY

CREATE TABLE IF NOT EXISTS `openalex.works` (
  `work_id` STRING NOT NULL,
  `work_id_url` STRING NOT NULL,
  `doi` STRING,
  `title` STRING,
  `publication_year` INT64,
  `publication_date` DATE,
  `work_type` STRING,
  `language` STRING,
  `cited_by_count` INT64,
  `referenced_works_count` INT64,
  `is_oa` BOOL,
  `oa_status` STRING,
  `primary_source_id` STRING,
  `primary_publisher_id` STRING,
  `source_created_date` DATE,
  `source_updated_date` DATE,
  `authorships_presence` STRING NOT NULL,
  `topics_presence` STRING NOT NULL,
  `keywords_presence` STRING NOT NULL,
  `mesh_presence` STRING NOT NULL,
  `referenced_works_presence` STRING NOT NULL,
  `locations_presence` STRING NOT NULL,
  `grants_presence` STRING NOT NULL,
  `source_asset_id` STRING NOT NULL,
  `source_checksum_sha256` STRING NOT NULL,
  `lineage_source_updated_date` DATE,
  `run_id` STRING NOT NULL,
  `processed_at` TIMESTAMP NOT NULL,
  `activity_state` STRING NOT NULL,
  `deleted_at` TIMESTAMP
)
PARTITION BY RANGE_BUCKET(`publication_year`, GENERATE_ARRAY(1000, 3001, 1))
CLUSTER BY `work_id`, `doi`, `primary_publisher_id`, `primary_source_id`
;
