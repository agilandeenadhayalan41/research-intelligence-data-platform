# BigQuery OpenAlex DDL (Step 15 / #22)

BigQuery Standard SQL `CREATE TABLE` contracts for canonical analytical tables.

- Source of truth for column names/types: Step 11 PyArrow schemas in
  `research_platform.canonical.openalex.schemas`
- Typed contracts: `research_platform.analytics.bigquery.contracts`
- These files are **DEFINED IN STEP 15** and **NOT YET DEPLOYED**

Do not invent `sources.issn` / `sources.eissn`. ISSN/eISSN lookup remains
UNRESOLVED per Step 14.
