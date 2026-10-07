-- BigQuery MERGE contract for `openalex.works` (Step 15 consistency hardening).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Precedence MUST match classify_works_staging.sql / decide_analytical_works_merge:
--   INSERT              — no target row
--   IDENTICAL           — same checksum (touch run_id/processed_at only)
--   RESTORE_REQUIRED    — target DELETED + source ACTIVE (skip; no silent restore)
--   STALE               — older lineage date (skip)
--   APPLY_UPDATE         — newer lineage date, dated-over-undated, OR
--                         equal-date target ACTIVE + source DELETED (Step 13)
--   CONFLICT            — equal date + different checksum otherwise (skip)
--
-- Work.source_updated_date vs lineage_source_updated_date remain distinct.
-- Relationship publication uses accepted_work_ids (INSERT + APPLY_UPDATE only).

MERGE `openalex.works` AS target
USING `openalex.works_staging` AS source
ON target.`work_id` = source.`work_id`

WHEN MATCHED
  AND source.`source_checksum_sha256` = target.`source_checksum_sha256`
THEN
  UPDATE SET
    `processed_at` = source.`processed_at`,
    `run_id` = source.`run_id`

WHEN MATCHED
  AND source.`source_checksum_sha256` != target.`source_checksum_sha256`
  AND NOT (
    target.`activity_state` = 'DELETED'
    AND source.`activity_state` = 'ACTIVE'
  )
  AND (
    (
      source.`lineage_source_updated_date` IS NOT NULL
      AND target.`lineage_source_updated_date` IS NOT NULL
      AND source.`lineage_source_updated_date` > target.`lineage_source_updated_date`
    )
    OR (
      source.`lineage_source_updated_date` IS NOT NULL
      AND target.`lineage_source_updated_date` IS NULL
    )
    OR (
      -- Step 13: ACTIVE T2 + deletion T2 → tombstone wins (not CONFLICT).
      source.`lineage_source_updated_date` IS NOT NULL
      AND target.`lineage_source_updated_date` IS NOT NULL
      AND source.`lineage_source_updated_date` = target.`lineage_source_updated_date`
      AND target.`activity_state` = 'ACTIVE'
      AND source.`activity_state` = 'DELETED'
    )
  )
THEN
  UPDATE SET
    `work_id_url` = source.`work_id_url`,
    `doi` = source.`doi`,
    `title` = source.`title`,
    `publication_year` = source.`publication_year`,
    `publication_date` = source.`publication_date`,
    `work_type` = source.`work_type`,
    `language` = source.`language`,
    `cited_by_count` = source.`cited_by_count`,
    `referenced_works_count` = source.`referenced_works_count`,
    `is_oa` = source.`is_oa`,
    `oa_status` = source.`oa_status`,
    `primary_source_id` = source.`primary_source_id`,
    `primary_publisher_id` = source.`primary_publisher_id`,
    `source_created_date` = source.`source_created_date`,
    `source_updated_date` = source.`source_updated_date`,
    `authorships_presence` = source.`authorships_presence`,
    `topics_presence` = source.`topics_presence`,
    `keywords_presence` = source.`keywords_presence`,
    `mesh_presence` = source.`mesh_presence`,
    `referenced_works_presence` = source.`referenced_works_presence`,
    `locations_presence` = source.`locations_presence`,
    `grants_presence` = source.`grants_presence`,
    `source_asset_id` = source.`source_asset_id`,
    `source_checksum_sha256` = source.`source_checksum_sha256`,
    `lineage_source_updated_date` = source.`lineage_source_updated_date`,
    `run_id` = source.`run_id`,
    `processed_at` = source.`processed_at`,
    `activity_state` = source.`activity_state`,
    `deleted_at` = source.`deleted_at`

WHEN NOT MATCHED THEN
  INSERT (
    `work_id`,
    `work_id_url`,
    `doi`,
    `title`,
    `publication_year`,
    `publication_date`,
    `work_type`,
    `language`,
    `cited_by_count`,
    `referenced_works_count`,
    `is_oa`,
    `oa_status`,
    `primary_source_id`,
    `primary_publisher_id`,
    `source_created_date`,
    `source_updated_date`,
    `authorships_presence`,
    `topics_presence`,
    `keywords_presence`,
    `mesh_presence`,
    `referenced_works_presence`,
    `locations_presence`,
    `grants_presence`,
    `source_asset_id`,
    `source_checksum_sha256`,
    `lineage_source_updated_date`,
    `run_id`,
    `processed_at`,
    `activity_state`,
    `deleted_at`
  )
  VALUES (
    source.`work_id`,
    source.`work_id_url`,
    source.`doi`,
    source.`title`,
    source.`publication_year`,
    source.`publication_date`,
    source.`work_type`,
    source.`language`,
    source.`cited_by_count`,
    source.`referenced_works_count`,
    source.`is_oa`,
    source.`oa_status`,
    source.`primary_source_id`,
    source.`primary_publisher_id`,
    source.`source_created_date`,
    source.`source_updated_date`,
    source.`authorships_presence`,
    source.`topics_presence`,
    source.`keywords_presence`,
    source.`mesh_presence`,
    source.`referenced_works_presence`,
    source.`locations_presence`,
    source.`grants_presence`,
    source.`source_asset_id`,
    source.`source_checksum_sha256`,
    source.`lineage_source_updated_date`,
    source.`run_id`,
    source.`processed_at`,
    source.`activity_state`,
    source.`deleted_at`
  );
