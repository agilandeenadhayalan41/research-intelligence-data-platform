# BigQuery OpenAlex DDL (Step 15 / #22)

BigQuery Standard SQL `CREATE TABLE` contracts for canonical analytical tables.

- Source of truth for column names/types: Step 11 PyArrow schemas in
  `research_platform.canonical.openalex.schemas`
- Typed contracts: `research_platform.analytics.bigquery.contracts`
- These files are **DEFINED IN STEP 15**. The manual
  `scripts/bigquery_sandbox_103.py` submitted all 15 byte-for-byte (each
  reported `CREATE`) in runs on synthetic data only, in GCP_SANDBOX (#103, #108 Stages
  1–2) and in operator-controlled personal projects labelled DEV and QA (#108
  Stage 3). They are **not deployed** to any enterprise environment or PROD, and
  no deployment pipeline applies them (tracked in #134)
- The files are rendered by `render_create_table_ddl()` and a test compares
  them byte-for-byte, so change the renderer, not the files. Their rendered
  header still reads "contract only; NOT DEPLOYED / NOT EXECUTED here": default
  CI and the library code never deploy or execute them; only the manual
  script above does
- Flattened lineage date column is `lineage_source_updated_date` (distinct from
  Work record `source_updated_date` on `works`)
- `works` uses `PARTITION BY RANGE_BUCKET(publication_year, GENERATE_ARRAY(...))`

Do not invent `sources.issn` / `sources.eissn`. ISSN/eISSN lookup remains
UNRESOLVED per Step 14.
