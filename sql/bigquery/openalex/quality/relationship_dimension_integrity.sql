-- Quality HARD_GATE: canonical.relationships.dimension_integrity
-- Non-null dimension IDs must exist in entity tables.
-- Allowed NULL IDs are not failures. No keyword/mesh dimension tables.

WITH orphans AS (
  SELECT 'work_authors.author_id' AS `ref_name`, COUNT(*) AS `orphan_count`
  FROM `openalex.work_authors` AS r
  LEFT JOIN `openalex.authors` AS d ON d.`author_id` = r.`author_id`
  WHERE r.`author_id` IS NOT NULL AND d.`author_id` IS NULL
  UNION ALL
  SELECT 'work_author_institutions.institution_id', COUNT(*)
  FROM `openalex.work_author_institutions` AS r
  LEFT JOIN `openalex.institutions` AS d ON d.`institution_id` = r.`institution_id`
  WHERE r.`institution_id` IS NOT NULL AND d.`institution_id` IS NULL
  UNION ALL
  SELECT 'work_topics.topic_id', COUNT(*)
  FROM `openalex.work_topics` AS r
  LEFT JOIN `openalex.topics` AS d ON d.`topic_id` = r.`topic_id`
  WHERE r.`topic_id` IS NOT NULL AND d.`topic_id` IS NULL
  UNION ALL
  SELECT 'work_locations.source_id', COUNT(*)
  FROM `openalex.work_locations` AS r
  LEFT JOIN `openalex.sources` AS d ON d.`source_id` = r.`source_id`
  WHERE r.`source_id` IS NOT NULL AND d.`source_id` IS NULL
  UNION ALL
  SELECT 'work_grants.funder_id', COUNT(*)
  FROM `openalex.work_grants` AS r
  LEFT JOIN `openalex.funders` AS d ON d.`funder_id` = r.`funder_id`
  WHERE r.`funder_id` IS NOT NULL AND d.`funder_id` IS NULL
)
SELECT
  o.`ref_name`,
  o.`orphan_count`
FROM orphans AS o
;
