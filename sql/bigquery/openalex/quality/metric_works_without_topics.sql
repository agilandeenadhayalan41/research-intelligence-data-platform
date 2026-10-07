-- INFORMATIONAL: metric.active_works.without_topics
-- No usable current topic relationship (not topics_presence source state).

SELECT
  COUNTIF(t.`work_id` IS NULL) AS `missing_count`,
  COUNT(*) AS `active_work_denominator`,
  SAFE_DIVIDE(COUNTIF(t.`work_id` IS NULL), COUNT(*)) AS `missing_rate`
FROM `openalex.works` AS w
LEFT JOIN (
  SELECT DISTINCT wt.`work_id`
  FROM `openalex.work_topics` AS wt
  WHERE wt.`topic_id` IS NOT NULL
) AS t
  ON t.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
;
