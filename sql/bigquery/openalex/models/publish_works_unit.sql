-- Analytical Works publication unit (Step 15).
-- DEFINED — NOT YET DEPLOYED / NOT EXECUTED here.
--
-- Freezes publication decisions against the pre-MERGE target, then publishes
-- Works and relationship projections. Do not recalculate decisions after
-- merge_works mutates `openalex.works`.
--
-- Order (required):
--   1) work_publication_decisions  (single precedence CASE, pre-MERGE)
--   2) accepted_work_ids           (Work acceptance: INSERT/APPLY_UPDATE)
--   3) relationship_publish_work_ids (ACTIVE accepted only)
--   4) merge_works                 (uses frozen decisions)
--   5) relationship REPLACE        (uses relationship_publish_work_ids)
--
-- Work acceptance != relationship replacement eligibility.
-- Deletion: tombstone Work; preserve owned relationships (Step 13 Policy A).
--
-- Note: BigQuery scripting/TEMP TABLE variants are equivalent if they freeze
-- the same pre-MERGE snapshot. Persistent staging tables are shown here so
-- contracts remain file-addressable. This unit does not claim one global
-- ACID commit across every canonical table beyond the scripted order.

-- Step 1–3: materialize frozen decision sets (see sibling .sql files).
--   RUN: work_publication_decisions.sql
--   RUN: accepted_work_ids.sql
--   RUN: relationship_publish_work_ids.sql

-- Step 4: publish Works from frozen decisions.
--   RUN: merge_works.sql

-- Step 5: replace relationships for ACTIVE accepted Works only.
--   RUN: merge_work_topics.sql  (template for all REPLACE_BY_WORK_ID tables)

-- Rejected outcomes for ops visibility (optional):
--   RUN: merge_works_preconditions.sql
