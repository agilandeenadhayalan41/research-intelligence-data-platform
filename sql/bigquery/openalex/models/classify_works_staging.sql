-- Logical alias for the frozen publication decision relation (Step 15).
-- DEFINED. Executed only by manual scripts on synthetic data (GCP_SANDBOX,
-- personal DEV/QA); NOT DEPLOYED.
--
-- Precedence CASE lives only in work_publication_decisions.sql.
-- After that table is materialized (pre-MERGE), read decisions from here.

SELECT
  d.`work_id`,
  d.`source_activity_state`,
  d.`publication_decision`
FROM `openalex.work_publication_decisions` AS d;
