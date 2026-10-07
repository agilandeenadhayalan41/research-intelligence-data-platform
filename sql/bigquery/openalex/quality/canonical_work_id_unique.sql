-- Quality HARD_GATE: canonical.works.work_id_unique
-- Expected: duplicate_work_id_count = 0
-- Returns duplicate KEY COUNT only — never the IDs themselves.

SELECT
  COUNT(*) AS `duplicate_work_id_count`
FROM (
  SELECT
    w.`work_id`
  FROM `openalex.works` AS w
  WHERE w.`work_id` IS NOT NULL
  GROUP BY w.`work_id`
  HAVING COUNT(*) > 1
) AS d
;
