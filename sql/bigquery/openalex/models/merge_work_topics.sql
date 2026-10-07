-- Relationship refresh for `openalex.work_topics` (Step 15 consistency).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- REPLACE_BY_WORK_ID template for all relationship tables.
--
-- CRITICAL: scope DELETE+INSERT to accepted_work_ids only
-- (publication_decision IN INSERT, APPLY_UPDATE from classify_works_staging).
-- Never publish relationships for STALE / CONFLICT / RESTORE_REQUIRED /
-- IDENTICAL staging rows — even if those work_ids appear in a broader
-- changed_work_ids discovery set.
--
-- Consistency model:
--   1) classify staged Works
--   2) MERGE works using the same decisions
--   3) REPLACE relationships only for accepted Work IDs
-- Therefore newer Work + stale relationships cannot arise from normal publish.
--
-- DELETE+INSERT is atomic per relationship table via multi-statement txn.
-- This is not a claim that all canonical tables share one global BQ transaction.

BEGIN TRANSACTION;

DELETE FROM `openalex.work_topics` AS target
WHERE target.`work_id` IN (
  SELECT `work_id` FROM `openalex.accepted_work_ids`
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
  SELECT `work_id` FROM `openalex.accepted_work_ids`
);

COMMIT TRANSACTION;
