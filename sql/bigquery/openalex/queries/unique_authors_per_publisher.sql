-- pattern_id: unique-authors-per-publisher
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Safe shape: publisher works first, then distinct authors.

WITH publisher_works AS (
  SELECT DISTINCT w.`work_id`
  FROM `openalex.works` AS w
  WHERE w.`activity_state` = 'ACTIVE'
    AND w.`primary_publisher_id` = @publisher_id
)
SELECT
  @publisher_id AS `publisher_id`,
  COUNT(DISTINCT wa.`author_id`) AS `unique_author_count`
FROM publisher_works AS pw
INNER JOIN `openalex.work_authors` AS wa
  ON wa.`work_id` = pw.`work_id`
WHERE wa.`author_id` IS NOT NULL;
