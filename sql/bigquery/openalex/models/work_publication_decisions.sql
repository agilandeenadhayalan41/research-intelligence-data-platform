-- Frozen Work publication decisions (Step 15 publication boundary).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- THIS FILE IS THE SINGLE PRECEDENCE CASE for analytical Work publication.
-- Derived sets (accepted_work_ids, relationship_publish_work_ids,
-- merge_works_preconditions) MUST SELECT from this relation — do not copy CASE.
--
-- MUST be materialized against the pre-MERGE target `openalex.works` state.
-- Do not recalculate after merge_works.sql mutates works (APPLY_UPDATE would
-- incorrectly become IDENTICAL).
--
-- Decisions: INSERT | IDENTICAL | APPLY_UPDATE | STALE | CONFLICT | RESTORE_REQUIRED
--
-- Equal-date Step 13: ACTIVE T2 + DELETED T2 → APPLY_UPDATE (tombstone wins).

CREATE OR REPLACE TABLE `openalex.work_publication_decisions` AS
SELECT
  source.`work_id`,
  source.`activity_state` AS `source_activity_state`,
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
