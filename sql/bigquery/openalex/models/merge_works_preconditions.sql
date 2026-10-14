-- Rejected / skipped publication decisions (Step 15).
-- DEFINED. Executed only by manual scripts on synthetic data (GCP_SANDBOX,
-- personal DEV/QA); NOT DEPLOYED.
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
