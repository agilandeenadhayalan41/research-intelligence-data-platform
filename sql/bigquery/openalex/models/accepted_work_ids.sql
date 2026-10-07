-- Work publication acceptance set (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Derived from frozen `work_publication_decisions` (no precedence CASE here).
-- Controls Work-row publication (MERGE apply/insert).
--
-- Does NOT authorize relationship DELETE+INSERT. Deletion tombstones are
-- APPLY_UPDATE for Works but must preserve owned relationships (Step 13 Policy A).
-- Use `relationship_publish_work_ids` for relationship REPLACE scope.

CREATE OR REPLACE TABLE `openalex.accepted_work_ids` AS
SELECT
  d.`work_id`
FROM `openalex.work_publication_decisions` AS d
WHERE d.`publication_decision` IN ('INSERT', 'APPLY_UPDATE');
