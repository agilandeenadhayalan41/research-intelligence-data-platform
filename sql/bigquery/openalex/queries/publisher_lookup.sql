-- pattern_id: publisher-lookup
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)

SELECT
  p.`publisher_id`,
  p.`publisher_id_url`,
  p.`display_name`,
  p.`activity_state`,
  p.`source_asset_id`,
  p.`source_checksum_sha256`,
  p.`lineage_source_updated_date`,
  p.`run_id`,
  p.`processed_at`
FROM `openalex.publishers` AS p
WHERE p.`publisher_id` = @publisher_id
LIMIT 1;
