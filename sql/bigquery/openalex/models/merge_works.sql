-- BigQuery MERGE contract for `openalex.works` (Step 15 / #22 hardening).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Strategy: MERGE_BY_PRIMARY_KEY on work_id with Steps 11–13 precedence.
--
-- Precedence (aligned with compare_work_versions + assert_active_upsert_allowed):
--   1) same source_checksum_sha256 → IDENTICAL (no substantive overwrite)
--   2) target DELETED + source ACTIVE → RESTORE_REQUIRED (skip; no silent restore)
--   3) incoming lineage_source_updated_date older → STALE (skip)
--   4) equal lineage dates + different checksum → CONFLICT (skip; detect via
--      merge_works_preconditions.sql before/after publish)
--   5) incoming newer lineage date (or dated vs undated target) → APPLY
--      including ACTIVE→DELETED when deletion date is same/newer
--
-- Work.source_updated_date (record field) and lineage_source_updated_date are
-- distinct columns. Deletion dates live in lineage_source_updated_date.
--
-- BigQuery multi-statement transactions differ operationally from local
-- Postgres ingestion ACID; this MERGE is a single DML statement.

MERGE `openalex.works` AS target
USING `openalex.works_staging` AS source
ON target.`work_id` = source.`work_id`

-- Idempotent replay: checksum match → no harmful attribute overwrite.
WHEN MATCHED
  AND source.`source_checksum_sha256` = target.`source_checksum_sha256`
THEN
  UPDATE SET
    `processed_at` = source.`processed_at`,
    `run_id` = source.`run_id`

-- Apply newer / dated-over-undated projections, including tombstones.
-- Never apply DELETED → ACTIVE through ordinary MERGE.
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
