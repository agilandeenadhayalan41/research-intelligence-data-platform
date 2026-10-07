-- Quality HARD_GATE: canonical.works.work_id_not_null
-- Expected: violation_count = 0
-- Aggregate diagnostics only — never emit raw rows.

SELECT
  COUNT(*) AS `violation_count`
FROM `openalex.works` AS w
WHERE w.`work_id` IS NULL
;
