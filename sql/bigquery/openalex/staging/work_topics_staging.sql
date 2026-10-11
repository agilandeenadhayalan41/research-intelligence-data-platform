-- BigQuery Standard SQL DDL for `openalex.work_topics_staging`
-- #103 publication staging; NOT DEPLOYED / NOT EXECUTED here.
-- Read by models/merge_work_topics.sql.
-- Rendered by research_platform.analytics.bigquery.staging.render_staging_ddl.
-- Every column is NULLABLE on purpose: it accepts the canonical Parquet and
-- the #103 injection Parquet (one NULL topic_id); the REQUIRED
-- `openalex.work_topics` columns reject the NULL inside the
-- merge_work_topics.sql transaction. Unpartitioned and unclustered:
-- staging is truncated and reloaded per batch and read in full.

CREATE TABLE IF NOT EXISTS `openalex.work_topics_staging` (
  `work_id` STRING,
  `topic_id` STRING,
  `score` FLOAT64,
  `source_asset_id` STRING,
  `source_checksum_sha256` STRING,
  `lineage_source_updated_date` DATE,
  `run_id` STRING,
  `processed_at` TIMESTAMP,
  `activity_state` STRING,
  `deleted_at` TIMESTAMP
)
;
