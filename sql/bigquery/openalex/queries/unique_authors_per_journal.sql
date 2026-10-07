-- pattern_id: unique-authors-per-journal
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Safe shape: identify journal works first, then aggregate authors independently.
-- Do NOT join authors × topics in this query.

WITH journal_works AS (
  SELECT DISTINCT wl.`work_id`
  FROM `openalex.work_locations` AS wl
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = wl.`work_id`
  WHERE w.`activity_state` = 'ACTIVE'
    AND wl.`source_id` = @source_id
)
SELECT
  @source_id AS `source_id`,
  COUNT(DISTINCT wa.`author_id`) AS `unique_author_count`
FROM journal_works AS jw
INNER JOIN `openalex.work_authors` AS wa
  ON wa.`work_id` = jw.`work_id`
WHERE wa.`author_id` IS NOT NULL;
