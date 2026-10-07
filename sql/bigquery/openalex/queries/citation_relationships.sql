-- pattern_id: citation-relationships
-- BigQuery Standard SQL — Step 15 / #22 (NOT YET DEPLOYED)
-- Source Work must be ACTIVE. Target may be ACTIVE, DELETED, or absent.
-- Do NOT inner-join away unresolved citation targets.

SELECT
  wr.`work_id` AS `source_work_id`,
  wr.`reference_index`,
  wr.`referenced_work_id`,
  wr.`reference_status`,
  tw.`activity_state` AS `target_activity_state`
FROM `openalex.work_references` AS wr
INNER JOIN `openalex.works` AS sw
  ON sw.`work_id` = wr.`work_id`
 AND sw.`activity_state` = 'ACTIVE'
LEFT JOIN `openalex.works` AS tw
  ON tw.`work_id` = wr.`referenced_work_id`
WHERE wr.`work_id` = @work_id
ORDER BY wr.`reference_index`;
