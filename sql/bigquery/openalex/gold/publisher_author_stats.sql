-- Gold: publisher_author_stats
-- Grain: one row per publisher_id
-- Fan-out safety: ACTIVE works; author and work counts computed independently.
-- Materialization: MATERIALIZATION_CANDIDATE (Step 14 publisher-author-counts)

WITH publisher_works AS (
  SELECT
    w.`work_id`,
    w.`primary_publisher_id` AS `publisher_id`
  FROM `openalex.works` AS w
  WHERE w.`activity_state` = 'ACTIVE'
    AND w.`primary_publisher_id` IS NOT NULL
),
work_counts AS (
  SELECT
    pw.`publisher_id`,
    COUNT(*) AS `active_work_count`
  FROM publisher_works AS pw
  GROUP BY pw.`publisher_id`
),
author_counts AS (
  SELECT
    pw.`publisher_id`,
    COUNT(DISTINCT wa.`author_id`) AS `unique_author_count`
  FROM publisher_works AS pw
  INNER JOIN `openalex.work_authors` AS wa
    ON wa.`work_id` = pw.`work_id`
  WHERE wa.`author_id` IS NOT NULL
  GROUP BY pw.`publisher_id`
)
SELECT
  wc.`publisher_id`,
  COALESCE(ac.`unique_author_count`, 0) AS `unique_author_count`,
  wc.`active_work_count`
FROM work_counts AS wc
LEFT JOIN author_counts AS ac
  ON ac.`publisher_id` = wc.`publisher_id`
;
