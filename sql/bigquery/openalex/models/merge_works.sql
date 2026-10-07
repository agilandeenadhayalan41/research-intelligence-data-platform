-- BigQuery MERGE for `openalex.works` using frozen decisions (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Requires `openalex.work_publication_decisions` already materialized against
-- the pre-MERGE target state. This MERGE does not re-evaluate precedence CASE;
-- it applies frozen publication_decision values to avoid post-MERGE drift
-- (APPLY_UPDATE must not be reclassified as IDENTICAL).
--
-- Decisions:
--   INSERT / APPLY_UPDATE → publish Work attributes (incl. tombstones)
--   IDENTICAL → touch run_id/processed_at only
--   STALE / CONFLICT / RESTORE_REQUIRED → no substantive WHEN MATCHED apply

MERGE `openalex.works` AS target
USING (
  SELECT
    s.*,
    d.`publication_decision`
  FROM `openalex.works_staging` AS s
  INNER JOIN `openalex.work_publication_decisions` AS d
    ON d.`work_id` = s.`work_id`
) AS source
ON target.`work_id` = source.`work_id`

WHEN MATCHED
  AND source.`publication_decision` = 'IDENTICAL'
THEN
  UPDATE SET
    `processed_at` = source.`processed_at`,
    `run_id` = source.`run_id`

WHEN MATCHED
  AND source.`publication_decision` = 'APPLY_UPDATE'
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

WHEN NOT MATCHED
  AND source.`publication_decision` = 'INSERT'
THEN
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
