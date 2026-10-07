-- Quality HARD_GATE: gold.deleted_source_work_exclusion
-- Requires complete staged Gold mart manifest coverage (Step 16 inventory).
-- Source work_id must exist in canonical works AND be ACTIVE.
-- Citation TARGET may be DELETED/ABSENT; citation SOURCE must exist + ACTIVE.
-- Aggregate counts only — never emit work_ids.
--
-- Missing required tables are executor ERROR (not modeled as zero here).
-- Empty existing tables are valid (zero counts).

WITH manifest AS (
  SELECT m.`mart_id`
  FROM `openalex.staged_gold_mart_manifest` AS m
),
contrib_missing AS (
  SELECT
    COUNT(*) AS `missing_source_work_count`
  FROM `openalex.staged_gold_work_contributions` AS g
  LEFT JOIN `openalex.works` AS w
    ON w.`work_id` = g.`work_id`
  WHERE w.`work_id` IS NULL
),
contrib_inactive AS (
  SELECT
    COUNT(*) AS `inactive_source_work_count`
  FROM `openalex.staged_gold_work_contributions` AS g
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = g.`work_id`
  WHERE w.`activity_state` != 'ACTIVE'
),
cite_missing AS (
  SELECT
    COUNT(*) AS `missing_citation_source_count`
  FROM `openalex.staged_gold_citation_edges` AS c
  LEFT JOIN `openalex.works` AS w
    ON w.`work_id` = c.`source_work_id`
  WHERE w.`work_id` IS NULL
),
cite_inactive AS (
  SELECT
    COUNT(*) AS `inactive_citation_source_count`
  FROM `openalex.staged_gold_citation_edges` AS c
  INNER JOIN `openalex.works` AS w
    ON w.`work_id` = c.`source_work_id`
  WHERE w.`activity_state` != 'ACTIVE'
)
SELECT
  (SELECT COUNT(*) FROM manifest) AS `manifest_mart_count`,
  cm.`missing_source_work_count`
    + xm.`missing_citation_source_count` AS `missing_source_work_count`,
  ci.`inactive_source_work_count`
    + xi.`inactive_citation_source_count` AS `inactive_source_work_count`
FROM contrib_missing AS cm
CROSS JOIN contrib_inactive AS ci
CROSS JOIN cite_missing AS xm
CROSS JOIN cite_inactive AS xi
;
