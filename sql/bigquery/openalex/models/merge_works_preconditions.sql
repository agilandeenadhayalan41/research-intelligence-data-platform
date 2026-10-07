-- Precondition / conflict detection for analytical works MERGE (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Run against staging before or after MERGE. Non-empty results mean the publish
-- layer must not treat the MERGE as a silent success:
--   RESTORE_REQUIRED — target DELETED, staging ACTIVE (no silent resurrection)
--   CONFLICT — equal lineage_source_updated_date, different checksum
--   STALE_SKIPPED — staging older than target (MERGE skips; surface for ops)

SELECT
  source.`work_id`,
  'RESTORE_REQUIRED' AS `issue`
FROM `openalex.works_staging` AS source
INNER JOIN `openalex.works` AS target
  ON target.`work_id` = source.`work_id`
WHERE target.`activity_state` = 'DELETED'
  AND source.`activity_state` = 'ACTIVE'
  AND source.`source_checksum_sha256` != target.`source_checksum_sha256`

UNION ALL

SELECT
  source.`work_id`,
  'CONFLICT' AS `issue`
FROM `openalex.works_staging` AS source
INNER JOIN `openalex.works` AS target
  ON target.`work_id` = source.`work_id`
WHERE source.`source_checksum_sha256` != target.`source_checksum_sha256`
  AND source.`lineage_source_updated_date` IS NOT NULL
  AND target.`lineage_source_updated_date` IS NOT NULL
  AND source.`lineage_source_updated_date` = target.`lineage_source_updated_date`
  AND NOT (
    target.`activity_state` = 'DELETED'
    AND source.`activity_state` = 'ACTIVE'
  )

UNION ALL

SELECT
  source.`work_id`,
  'STALE_SKIPPED' AS `issue`
FROM `openalex.works_staging` AS source
INNER JOIN `openalex.works` AS target
  ON target.`work_id` = source.`work_id`
WHERE source.`source_checksum_sha256` != target.`source_checksum_sha256`
  AND (
    (
      source.`lineage_source_updated_date` IS NOT NULL
      AND target.`lineage_source_updated_date` IS NOT NULL
      AND source.`lineage_source_updated_date` < target.`lineage_source_updated_date`
    )
    OR (
      source.`lineage_source_updated_date` IS NULL
      AND target.`lineage_source_updated_date` IS NOT NULL
    )
  )
  AND NOT (
    target.`activity_state` = 'DELETED'
    AND source.`activity_state` = 'ACTIVE'
  );
