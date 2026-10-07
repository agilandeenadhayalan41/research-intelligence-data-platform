-- Quality HARD_GATE: gold.deleted_source_work_exclusion
-- Consumer Gold must exclude DELETED/inactive SOURCE Works.
-- Citation TARGET may be DELETED; citation SOURCE must be ACTIVE.
-- Aggregate count only — never emit work_ids.

WITH deleted_contributions AS (
  SELECT
    COUNT(*) AS `deleted_source_contribution_count`
  FROM `openalex.staged_gold_work_contributions` AS g
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = g.`work_id`
  WHERE w.`activity_state` != 'ACTIVE'
),
deleted_citation_sources AS (
  SELECT
    COUNT(*) AS `deleted_citation_source_count`
  FROM `openalex.staged_gold_citation_edges` AS c
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = c.`source_work_id`
  WHERE w.`activity_state` != 'ACTIVE'
)
SELECT
  dc.`deleted_source_contribution_count`
    + ds.`deleted_citation_source_count` AS `deleted_source_contribution_count`
FROM deleted_contributions AS dc
CROSS JOIN deleted_citation_sources AS ds
;
