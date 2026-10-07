-- Rejected / skipped publication decisions (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Derived from frozen `work_publication_decisions` (no precedence CASE here).
-- Equal-date ACTIVE→DELETED is APPLY_UPDATE and must not appear here.

SELECT
  d.`work_id`,
  d.`publication_decision` AS `issue`
FROM `openalex.work_publication_decisions` AS d
WHERE d.`publication_decision` IN (
  'RESTORE_REQUIRED',
  'CONFLICT',
  'STALE'
);
