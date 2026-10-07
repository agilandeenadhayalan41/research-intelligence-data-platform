-- Gold: journal_author_stats
-- Grain: one row per source_id
-- Fan-out safety: DISTINCT source_id, work_id; author and work counts computed independently.
-- Materialization: MATERIALIZATION_CANDIDATE (Step 14 journal-author-counts)

WITH source_works AS (
  SELECT DISTINCT
    wl.`source_id`,
    wl.`work_id`
  FROM `openalex.work_locations` AS wl
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = wl.`work_id`
  WHERE w.`activity_state` = 'ACTIVE'
    AND wl.`source_id` IS NOT NULL
),
work_counts AS (
  SELECT
    sw.`source_id`,
    COUNT(*) AS `active_work_count`
  FROM source_works AS sw
  GROUP BY sw.`source_id`
),
author_counts AS (
  SELECT
    sw.`source_id`,
    COUNT(DISTINCT wa.`author_id`) AS `unique_author_count`
  FROM source_works AS sw
  INNER JOIN `openalex.work_authors` AS wa
    ON wa.`work_id` = sw.`work_id`
  WHERE wa.`author_id` IS NOT NULL
  GROUP BY sw.`source_id`
)
SELECT
  wc.`source_id`,
  COALESCE(ac.`unique_author_count`, 0) AS `unique_author_count`,
  wc.`active_work_count`
FROM work_counts AS wc
LEFT JOIN author_counts AS ac
  ON ac.`source_id` = wc.`source_id`
;
