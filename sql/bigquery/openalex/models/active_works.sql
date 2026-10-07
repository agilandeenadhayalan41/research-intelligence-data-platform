-- Reusable ACTIVE Works view (BigQuery Standard SQL).
-- Step 15 / #22 — DEFINED contract; NOT YET DEPLOYED.
--
-- Canonical `works` retains both ACTIVE and DELETED rows.
-- Consumer analytical models must filter ACTIVE explicitly (or use this view).

CREATE OR REPLACE VIEW `openalex.active_works` AS
SELECT
  w.`work_id`,
  w.`work_id_url`,
  w.`doi`,
  w.`title`,
  w.`publication_year`,
  w.`publication_date`,
  w.`work_type`,
  w.`language`,
  w.`cited_by_count`,
  w.`referenced_works_count`,
  w.`is_oa`,
  w.`oa_status`,
  w.`primary_source_id`,
  w.`primary_publisher_id`,
  w.`source_created_date`,
  w.`source_updated_date`,
  w.`authorships_presence`,
  w.`topics_presence`,
  w.`keywords_presence`,
  w.`mesh_presence`,
  w.`referenced_works_presence`,
  w.`locations_presence`,
  w.`grants_presence`,
  w.`source_asset_id`,
  w.`source_checksum_sha256`,
  w.`run_id`,
  w.`processed_at`,
  w.`activity_state`,
  w.`deleted_at`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE';
