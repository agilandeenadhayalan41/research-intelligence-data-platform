-- Quality HARD_GATE: canonical.relationships.source_work_integrity
-- Owned relationship source work_id must exist in works.
-- Citation TARGET absence is NOT an orphan (separate informational metric).
-- Aggregate orphan counts per relationship type.

WITH orphans AS (
  SELECT 'work_authors' AS `relationship_name`, COUNT(*) AS `orphan_count`
  FROM `openalex.work_authors` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_author_institutions', COUNT(*)
  FROM `openalex.work_author_institutions` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_topics', COUNT(*)
  FROM `openalex.work_topics` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_keywords', COUNT(*)
  FROM `openalex.work_keywords` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_references', COUNT(*)
  FROM `openalex.work_references` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_mesh', COUNT(*)
  FROM `openalex.work_mesh` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_locations', COUNT(*)
  FROM `openalex.work_locations` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
  UNION ALL
  SELECT 'work_grants', COUNT(*)
  FROM `openalex.work_grants` AS r
  LEFT JOIN `openalex.works` AS w ON w.`work_id` = r.`work_id`
  WHERE w.`work_id` IS NULL
)
SELECT
  o.`relationship_name`,
  o.`orphan_count`
FROM orphans AS o
;
