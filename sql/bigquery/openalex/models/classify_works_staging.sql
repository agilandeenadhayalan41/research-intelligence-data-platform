-- Deterministic publication decision for each staged Work (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Single precedence contract shared by:
--   - works MERGE (merge_works.sql mirrors these outcomes)
--   - accepted_work_ids (INSERT + APPLY_UPDATE only)
--   - relationship REPLACE_BY_WORK_ID scope
--
-- Decisions: INSERT | IDENTICAL | APPLY_UPDATE | STALE | CONFLICT | RESTORE_REQUIRED
--
-- Equal-date Step 13 rule: target ACTIVE + source DELETED at the same
-- lineage_source_updated_date → APPLY_UPDATE (tombstone wins).
-- Equal-date ACTIVE vs ACTIVE (different checksum) → CONFLICT.
-- IDENTICAL → no ordinary relationship replacement.

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
  ON target.`work_id` = source.`work_id`;
