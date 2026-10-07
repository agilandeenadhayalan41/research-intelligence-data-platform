# Research Intelligence Public Data Platform roadmap

This roadmap preserves completed work and sequences implementation from bounded
source profiling through consumer delivery. It does not authorize work beyond an
explicitly scoped task. Steps 01–07 are complete; later steps are planned.

## Completed foundation

| Step | Work | Status |
| --- | --- | --- |
| 01 | Repository foundation | Complete |
| 02 | Configuration framework | Complete |
| 03 | Storage abstraction/contracts | Complete contract; local/GCS runtime separate |
| 04 | Warehouse abstraction/contracts | Complete contract; DuckDB/BigQuery/PostgreSQL runtime adapters separate |
| 05 | Bounded public OpenAlex connector | Complete |
| 06 | OpenAlex Works manifest parser | Complete |
| 07 | Bounded deterministic development sample selector | Complete |

## Planned steps

| Step | Work |
| --- | --- |
| 08 | Profile real/tiny selected OpenAlex data: inspect actual format and nested schema, use PyArrow where Parquet applies, and bound memory/resources. |
| 09 | Implement immutable raw/canonical landing, local first and GCS later; preserve source bytes, immutable versioned paths, checksums, replay, and conflict semantics. |
| 10 | Add pipeline control and provenance: runs, source files, record provenance, statuses, retries, lineage, and claim semantics. |
| 11 | Define the canonical OpenAlex logical model: works, authors, institutions, sources, publishers, topics, funders, authorships, work-topics, work-institutions, work-references, and work-MeSH where available. Keep normalized relationships; do not flatten. |
| 12 | Implement incremental ingestion/upsert: manifest → discover → select/claim → download → immutable raw landing → staging → canonical update → provenance → success. |
| 13 | Support deletion, changed/new records, schema changes, replay, and reprocessing. |
| 14 | Build a query-pattern and benchmark registry before choosing operational serving technology. Cover DOI/OpenAlex ID/eISSN/ISSN/publisher lookups; journal/publisher authors; publisher-topic-license-year counts; institution/topic and citation/reference relationships; publication and open-access trends. Measure latency, concurrency, bytes scanned, cost, result size, freshness, frequency, and materialization suitability. |
| 15 | Implement BigQuery-first analytics on GCP; keep DuckDB for local tests, define SQL dialect contracts, and derive partitioning/clustering from evidence. Keep queries bounded and cost-safe. |
| 16 | Build gold models, analytical marts, and justified materialized aggregates for research discovery, journal/publisher-topic/institution-topic metrics, trends, open access, and citations. Avoid relationship fan-out. |
| 17 | Add data quality gates for non-null/unique canonical IDs, valid relationships, deleted-record exclusion, and reconciliation; measure DOI/title/topic gaps, authorless works, publication-year/type distributions, and reference reconciliation. |
| 18 | Define storage-independent Data Service/API capabilities for metadata lookup and journal/publisher analytics. Add optional PostgreSQL/AlloyDB only if benchmarks show BigQuery plus cache/API is insufficient. |
| 19 | Deliver a bounded end-to-end pipeline: discover → ingest → validate → canonicalize → apply deletions → build analytical models → quality gate → publish marts/views → final validation. Default `MAX_FILES=1`. |
| 20 | After Step 19 works, evaluate local Airflow and future Composer compatibility, CI/CD, Dataform alignment, retries, scheduling, and monitoring. Keep business logic out of DAGs. |
| 21 | Prepare sandbox/dev/QA/prod readiness documentation for IAM, workload identity, secrets, monitoring, recovery, cost controls, deployment, rollback, lineage, and governance. Do not automatically deploy production. |

## Source extension

OpenAlex is first. AACT / ClinicalTrials is the next planned source in
[issue #2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2);
it is not implemented here. Discover AACT's existing cleaning and semantics
before deciding normalization. Future global clinical-trial sources may include
US, Europe, China, WHO/global, and other regional data. OpenFDA, grants, patents,
news/releases, and other public sources require separately scoped work and
source-specific normalization.

## Architecture decision guardrails

- BigQuery is the first analytical implementation on GCP; PostgreSQL/AlloyDB is
  optional and follows query-pattern evidence, not precedes it.
- Canonical models remain portable, with Parquet where applicable. High-volume
  relationships are primarily analytical.
- Materialize repeated expensive queries only when measured usage warrants it.
- The Data Service/API is a domain-specific, storage-independent consumer
  contract; unrestricted raw SQL is not the product API.
- Spark/Dataproc, Iceberg/BigLake, orchestration, infrastructure, and deployed
  services are not mandatory first-iteration technologies.
- Preserve completed steps, tests, contracts, sample limits, security rules, and
  CI. This roadmap does not close or reopen issues or automatically start Step 08.
