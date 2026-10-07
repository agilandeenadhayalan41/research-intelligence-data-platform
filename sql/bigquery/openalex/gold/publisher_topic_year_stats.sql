-- Gold: publisher_topic_year_stats
-- Grain: publisher_id + topic_id + publication_year
-- Fan-out safety: ACTIVE works JOIN work_topics only (no authors/locations).
-- Materialization: MATERIALIZATION_CANDIDATE
-- Evidence: Step 14 publisher-topic-counts (candidate_materialization:
-- publisher-topic-counts); year grain may intentionally roll up to publisher+topic.

SELECT
  w.`primary_publisher_id` AS `publisher_id`,
  wt.`topic_id`,
  w.`publication_year`,
  COUNT(DISTINCT w.`work_id`) AS `active_work_count`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL
GROUP BY
  w.`primary_publisher_id`,
  wt.`topic_id`,
  w.`publication_year`
;
