-- pattern_id: publisher-topic-counts
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- works ⋈ work_topics only — never cross-join authors at raw grain.

SELECT
  w.`primary_publisher_id` AS `publisher_id`,
  wt.`topic_id`,
  COUNT(DISTINCT w.`work_id`) AS `work_count`
FROM `openalex.works` AS w
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` = @publisher_id
GROUP BY w.`primary_publisher_id`, wt.`topic_id`;
