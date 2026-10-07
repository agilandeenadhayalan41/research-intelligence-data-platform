-- pattern_id: institution-topic-relationships
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Safe shape: DISTINCT institution/work, then join topics (no authorship fan-out).

WITH inst_works AS (
  SELECT DISTINCT wai.`work_id`
  FROM `openalex.work_author_institutions` AS wai
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = wai.`work_id`
  WHERE w.`activity_state` = 'ACTIVE'
    AND wai.`institution_id` = @institution_id
)
SELECT
  @institution_id AS `institution_id`,
  wt.`topic_id`,
  COUNT(DISTINCT iw.`work_id`) AS `work_count`
FROM inst_works AS iw
INNER JOIN `openalex.work_topics` AS wt
  ON wt.`work_id` = iw.`work_id`
GROUP BY wt.`topic_id`;
