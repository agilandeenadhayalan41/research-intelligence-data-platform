-- Gold: publisher_topic_license_year_stats
-- Grain: publisher_id + topic_id + primary_location_license + publication_year
-- License: primary location only (is_primary = TRUE).
-- Do not select license via MIN, MAX, or ANY_VALUE aggregates.
-- NULL primary license is an explicit UNKNOWN bucket.
-- Materialization: MATERIALIZATION_CANDIDATE

WITH primary_license AS (
  SELECT
    wl.`work_id`,
    wl.`license` AS `primary_location_license`
  FROM `openalex.work_locations` AS wl
  WHERE wl.`is_primary` = TRUE
),
active_work_topics AS (
  SELECT
    w.`work_id`,
    w.`primary_publisher_id` AS `publisher_id`,
    w.`publication_year`,
    wt.`topic_id`
  FROM `openalex.works` AS w
  INNER JOIN `openalex.work_topics` AS wt
    ON wt.`work_id` = w.`work_id`
  WHERE w.`activity_state` = 'ACTIVE'
    AND w.`primary_publisher_id` IS NOT NULL
    AND wt.`topic_id` IS NOT NULL
)
SELECT
  awt.`publisher_id`,
  awt.`topic_id`,
  pl.`primary_location_license`,
  awt.`publication_year`,
  COUNT(DISTINCT awt.`work_id`) AS `active_work_count`
FROM active_work_topics AS awt
LEFT JOIN primary_license AS pl
  ON pl.`work_id` = awt.`work_id`
GROUP BY
  awt.`publisher_id`,
  awt.`topic_id`,
  pl.`primary_location_license`,
  awt.`publication_year`
;
