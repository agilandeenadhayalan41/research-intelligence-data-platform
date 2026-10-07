-- Quality HARD_GATE: gold.deleted_source_work_exclusion
-- Exact Step-16 Gold inventory membership (not count-only).
-- Manifest: exactly one row per expected mart_id.
-- Source work_id must exist in canonical works AND be ACTIVE.
-- Citation TARGET may be DELETED/ABSENT; citation SOURCE must exist + ACTIVE.
-- Aggregate counts only — never emit work_ids / mart_ids.

WITH expected_marts AS (
  SELECT mart_id FROM UNNEST([
    'research_discovery',
    'journal_author_stats',
    'publisher_author_stats',
    'publisher_topic_year_stats',
    'publisher_topic_license_year_stats',
    'institution_topic_stats',
    'publication_trends',
    'open_access_trends',
    'citation_edges'
  ]) AS mart_id
),
manifest AS (
  SELECT m.`mart_id`
  FROM `openalex.staged_gold_mart_manifest` AS m
),
manifest_counts AS (
  SELECT
    m.`mart_id`,
    COUNT(*) AS `row_count`
  FROM manifest AS m
  GROUP BY m.`mart_id`
),
coverage AS (
  SELECT
    (
      SELECT COUNT(*)
      FROM expected_marts AS e
      LEFT JOIN manifest_counts AS mc
        ON mc.`mart_id` = e.`mart_id`
      WHERE mc.`mart_id` IS NULL
    ) AS `missing_manifest_mart_count`,
    (
      SELECT COUNT(*)
      FROM manifest_counts AS mc
      LEFT JOIN expected_marts AS e
        ON e.`mart_id` = mc.`mart_id`
      WHERE e.`mart_id` IS NULL
    ) AS `unexpected_manifest_mart_count`,
    (
      SELECT COALESCE(SUM(mc.`row_count` - 1), 0)
      FROM manifest_counts AS mc
      WHERE mc.`row_count` > 1
    ) AS `duplicate_manifest_mart_count`
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
  cov.`missing_manifest_mart_count`,
  cov.`unexpected_manifest_mart_count`,
  cov.`duplicate_manifest_mart_count`,
  cm.`missing_source_work_count`
    + xm.`missing_citation_source_count` AS `missing_source_work_count`,
  ci.`inactive_source_work_count`
    + xi.`inactive_citation_source_count` AS `inactive_source_work_count`
FROM coverage AS cov
CROSS JOIN contrib_missing AS cm
CROSS JOIN contrib_inactive AS ci
CROSS JOIN cite_missing AS xm
CROSS JOIN cite_inactive AS xi
;
