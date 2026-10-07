# BigQuery OpenAlex DDL (Step 15 / #22)

BigQuery Standard SQL `CREATE TABLE` contracts for canonical analytical tables.

- Source of truth for column names/types: Step 11 PyArrow schemas in
  `research_platform.canonical.openalex.schemas`
- Typed contracts: `research_platform.analytics.bigquery.contracts`
- These files are **DEFINED IN STEP 15** and **NOT YET DEPLOYED**
- Flattened lineage date column is `lineage_source_updated_date` (distinct from
  Work record `source_updated_date` on `works`)
- `works` uses `PARTITION BY RANGE_BUCKET(publication_year, GENERATE_ARRAY(...))`

Do not invent `sources.issn` / `sources.eissn`. ISSN/eISSN lookup remains
UNRESOLVED per Step 14.
