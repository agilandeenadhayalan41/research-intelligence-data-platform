-- Gold: institution_topic_stats
-- Grain: institution_id + topic_id (Step 14; no year)
-- Fan-out safety: DISTINCT institution_id, work_id before topic join.
-- Materialization: COMPUTE_ON_READ

WITH institution_works AS (
  SELECT DISTINCT
    wai.institution_id,
    wai.work_id
  FROM `openalex.work_author_institutions` AS wai
  INNER JOIN `openalex.works` AS w
    ON w.work_id = wai.work_id
  WHERE w.activity_state = 'ACTIVE'
    AND wai.institution_id IS NOT NULL
)
SELECT
  iw.institution_id,
  wt.topic_id,
  COUNT(DISTINCT iw.work_id) AS active_work_count
FROM institution_works AS iw
INNER JOIN `openalex.work_topics` AS wt
  ON wt.work_id = iw.work_id
WHERE wt.topic_id IS NOT NULL
GROUP BY
  iw.institution_id,
  wt.topic_id
;
