-- Accepted Work IDs for analytical relationship publication (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Contains only Works whose publication_decision is INSERT or APPLY_UPDATE.
-- Excludes STALE, CONFLICT, RESTORE_REQUIRED, and IDENTICAL.
--
-- IDENTICAL: same checksum / canonical version — ordinary publication does not
-- replace relationships (already published for that version). Recovery of
-- missing relationships is an explicit publication-recovery path, not IDENTICAL
-- replay.
--
-- Relationship REPLACE_BY_WORK_ID must scope DELETE+INSERT to this set, never
-- to the raw changed_work_ids / full staging set.

SELECT
  classified.`work_id`
FROM (
  -- Inline the classify_works_staging contract (same CASE).
  SELECT
    source.`work_id`,
    CASE
      WHEN target.`work_id` IS NULL THEN 'INSERT'
      WHEN source.`source_checksum_sha256` = target.`source_checksum_sha256`
        THEN 'IDENTICAL'
      WHEN target.`activity_state` = 'DELETED'
       AND source.`activity_state` = 'ACTIVE'
        THEN 'RESTORE_REQUIRED'
      WHEN source.`lineage_source_updated_date` IS NOT NULL
       AND target.`lineage_source_updated_date` IS NOT NULL
       AND source.`lineage_source_updated_date` < target.`lineage_source_updated_date`
        THEN 'STALE'
      WHEN source.`lineage_source_updated_date` IS NULL
       AND target.`lineage_source_updated_date` IS NOT NULL
        THEN 'STALE'
      WHEN source.`lineage_source_updated_date` IS NOT NULL
       AND target.`lineage_source_updated_date` IS NOT NULL
       AND source.`lineage_source_updated_date` = target.`lineage_source_updated_date`
       AND target.`activity_state` = 'ACTIVE'
       AND source.`activity_state` = 'DELETED'
        THEN 'APPLY_UPDATE'
      WHEN source.`lineage_source_updated_date` IS NOT NULL
       AND target.`lineage_source_updated_date` IS NOT NULL
       AND source.`lineage_source_updated_date` = target.`lineage_source_updated_date`
        THEN 'CONFLICT'
      WHEN source.`lineage_source_updated_date` IS NOT NULL
       AND target.`lineage_source_updated_date` IS NULL
        THEN 'APPLY_UPDATE'
      WHEN source.`lineage_source_updated_date` IS NOT NULL
       AND target.`lineage_source_updated_date` IS NOT NULL
       AND source.`lineage_source_updated_date` > target.`lineage_source_updated_date`
        THEN 'APPLY_UPDATE'
      ELSE 'CONFLICT'
    END AS `publication_decision`
  FROM `openalex.works_staging` AS source
  LEFT JOIN `openalex.works` AS target
    ON target.`work_id` = source.`work_id`
) AS classified
WHERE classified.`publication_decision` IN ('INSERT', 'APPLY_UPDATE');
