-- Relationship REPLACE eligibility (Step 15 publication boundary).
-- DEFINED. Executed only by manual scripts on synthetic data (GCP_SANDBOX,
-- personal DEV/QA); NOT DEPLOYED.
--
-- Derived from frozen `work_publication_decisions` (no precedence CASE here).
--
-- Eligible when:
--   publication_decision IN ('INSERT', 'APPLY_UPDATE')
--   AND source_activity_state = 'ACTIVE'
--
-- Excludes DELETED projections (tombstone Work, preserve physical relationships),
-- STALE, CONFLICT, RESTORE_REQUIRED, and IDENTICAL ordinary replay.
--
-- Work acceptance != relationship replacement eligibility.

CREATE OR REPLACE TABLE `openalex.relationship_publish_work_ids` AS
SELECT
  d.`work_id`
FROM `openalex.work_publication_decisions` AS d
WHERE d.`publication_decision` IN ('INSERT', 'APPLY_UPDATE')
  AND d.`source_activity_state` = 'ACTIVE';
