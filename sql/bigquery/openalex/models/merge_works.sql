-- BigQuery MERGE contract for `openalex.works` (Step 15 / #22).
-- DEFINED IN STEP 15 — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Strategy: MERGE_BY_PRIMARY_KEY on work_id.
-- Idempotent replay: re-MERGING the same staging rows yields the same current
-- projection (deterministic update of attributes + lineage).
--
-- Deleted Works remain as rows with activity_state = 'DELETED' (no physical
-- delete of Work identity). Staging must carry tombstone rows when applying
-- OpenAlex deletions (Step 13 semantics).
--
-- BigQuery does NOT provide multi-table ACID identical to local Postgres
-- ingestion transactions. Coordinate works + relationships at the application /
-- orchestration layer (changed work_id set).

MERGE `openalex.works` AS target
USING `openalex.works_staging` AS source
ON target.`work_id` = source.`work_id`
WHEN MATCHED THEN UPDATE SET
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
  `run_id` = source.`run_id`,
  `processed_at` = source.`processed_at`,
  `activity_state` = source.`activity_state`,
  `deleted_at` = source.`deleted_at`
WHEN NOT MATCHED THEN INSERT (
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
  source.`run_id`,
  source.`processed_at`,
  source.`activity_state`,
  source.`deleted_at`
);
