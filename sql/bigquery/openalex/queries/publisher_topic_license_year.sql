-- pattern_id: publisher-topic-license-year
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- License = PRIMARY LOCATION LICENSE only (is_primary = TRUE).
-- Do NOT use MIN(license) / MAX(license). NULL primary license → NULL bucket.

WITH primary_license AS (
  SELECT
    wl.`work_id`,
    wl.`license` AS `primary_location_license`
  FROM `openalex.work_locations` AS wl
  WHERE wl.`is_primary` = TRUE
)
SELECT
  w.`primary_publisher_id` AS `publisher_id`,
  wt.`topic_id`,
  pl.`primary_location_license`,
  w.`publication_year`,
  COUNT(DISTINCT w.`work_id`) AS `work_count`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
LEFT JOIN primary_license AS pl
  ON pl.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND (@publisher_id IS NULL OR w.`primary_publisher_id` = @publisher_id)
  AND (@year_from IS NULL OR w.`publication_year` >= @year_from)
  AND (@year_to IS NULL OR w.`publication_year` <= @year_to)
GROUP BY
  w.`primary_publisher_id`,
  wt.`topic_id`,
  pl.`primary_location_license`,
  w.`publication_year`;
