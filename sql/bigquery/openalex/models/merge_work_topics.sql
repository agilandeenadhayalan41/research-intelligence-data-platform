-- Relationship refresh for `openalex.work_topics` (Step 15 publication boundary).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- REPLACE_BY_WORK_ID template for all relationship tables.
--
-- Scope DELETE+INSERT to `relationship_publish_work_ids` only:
--   accepted ACTIVE Work projections (INSERT/APPLY_UPDATE + source ACTIVE).
--
-- Never use:
--   accepted_work_ids (includes DELETED tombstones → would wipe Policy A rows)
--   changed_work_ids (includes rejected STALE/CONFLICT/RESTORE)
--
-- Step 13 Policy A: deleted Works keep physical relationship observations;
-- consumer queries exclude them via works.activity_state = 'ACTIVE'.
--
-- Requires frozen decision tables already materialized pre-MERGE.

BEGIN TRANSACTION;

DELETE FROM `openalex.work_topics` AS target
WHERE target.`work_id` IN (
  SELECT `work_id` FROM `openalex.relationship_publish_work_ids`
);

INSERT INTO `openalex.work_topics` (
  `work_id`,
  `topic_id`,
  `score`,
  `source_asset_id`,
  `source_checksum_sha256`,
  `lineage_source_updated_date`,
  `run_id`,
  `processed_at`,
  `activity_state`,
  `deleted_at`
)
SELECT
  source.`work_id`,
  source.`topic_id`,
  source.`score`,
  source.`source_asset_id`,
  source.`source_checksum_sha256`,
  source.`lineage_source_updated_date`,
  source.`run_id`,
  source.`processed_at`,
  source.`activity_state`,
  source.`deleted_at`
FROM `openalex.work_topics_staging` AS source
WHERE source.`work_id` IN (
  SELECT `work_id` FROM `openalex.relationship_publish_work_ids`
);

COMMIT TRANSACTION;
