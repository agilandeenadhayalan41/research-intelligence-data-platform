-- INFORMATIONAL: metric.active_works.reference_reconciliation
-- Categories for ACTIVE source Works only.
-- TARGET_DELETED / TARGET_ABSENT do NOT fail integrity gates.
-- Category totals must equal total source-observed reference rows.

SELECT
  CASE
    WHEN wr.`reference_status` = 'MISSING' THEN 'REFERENCE_MISSING'
    WHEN wr.`reference_status` = 'MALFORMED' THEN 'REFERENCE_MALFORMED'
    WHEN wr.`reference_status` = 'RESOLVED_ID'
         AND tw.`activity_state` = 'ACTIVE' THEN 'TARGET_ACTIVE'
    WHEN wr.`reference_status` = 'RESOLVED_ID'
         AND tw.`activity_state` = 'DELETED' THEN 'TARGET_DELETED'
    WHEN wr.`reference_status` = 'RESOLVED_ID'
         AND tw.`work_id` IS NULL THEN 'TARGET_ABSENT'
    ELSE 'TARGET_ABSENT'
  END AS `category`,
  COUNT(*) AS `reference_count`
FROM `openalex.work_references` AS wr
INNER JOIN `openalex.works` AS sw
  ON sw.`work_id` = wr.`work_id`
 AND sw.`activity_state` = 'ACTIVE'
LEFT JOIN `openalex.works` AS tw
  ON tw.`work_id` = wr.`referenced_work_id`
GROUP BY 1
;
