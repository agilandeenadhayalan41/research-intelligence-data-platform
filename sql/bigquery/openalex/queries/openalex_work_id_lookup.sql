-- pattern_id: openalex-work-id-lookup
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)

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
  w.`is_oa`,
  w.`oa_status`,
  w.`primary_source_id`,
  w.`primary_publisher_id`,
  w.`activity_state`,
  w.`source_asset_id`,
  w.`source_checksum_sha256`,
  w.`source_updated_date`,
  w.`run_id`,
  w.`processed_at`
FROM `openalex.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`work_id` = @work_id
LIMIT 1;
