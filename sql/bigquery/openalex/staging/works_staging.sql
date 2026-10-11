-- BigQuery Standard SQL DDL for `openalex.works_staging`
-- #103 publication staging; NOT DEPLOYED / NOT EXECUTED here.
-- Read by models/work_publication_decisions.sql and models/merge_works.sql.
-- Rendered by research_platform.analytics.bigquery.staging.render_staging_ddl.
-- LIKE copies the `openalex.works` column schema (REQUIRED modes included),
-- partitioning and clustering, so a staging load must satisfy the works
-- contract. Must be unique on work_id before merge_works.sql (MERGE).

CREATE TABLE IF NOT EXISTS `openalex.works_staging` LIKE `openalex.works`
;
