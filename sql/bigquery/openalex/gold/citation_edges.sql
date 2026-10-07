-- Gold: citation_edges
-- Grain: source_work_id + reference_index
-- Source Work must be ACTIVE. Target LEFT JOINed (ACTIVE / DELETED / unresolved).
-- Materialization: COMPUTE_ON_READ — do not pre-aggregate the full network.

SELECT
  wr.work_id AS source_work_id,
  wr.reference_index,
  wr.referenced_work_id,
  wr.reference_status,
  tw.activity_state AS target_activity_state
FROM `openalex.work_references` AS wr
INNER JOIN `openalex.works` AS sw
  ON sw.work_id = wr.work_id
LEFT JOIN `openalex.works` AS tw
  ON tw.work_id = wr.referenced_work_id
WHERE sw.activity_state = 'ACTIVE'
;
