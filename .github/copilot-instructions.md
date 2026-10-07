# Research Intelligence Data Platform engineering principles

- Build a local-first, cloud-ready platform in small, explicitly scoped phases.
  Phase 1 is configuration, interfaces, documentation, and tests only. Do not
  implement ingestion, orchestration, or serving workloads without a later task.
- Use Python 3.12+, src-layout, type hints, small modules, clear adapter interfaces,
  Pydantic validation, PyArrow interchange, structured logging, and pytest.
- Use configuration-driven environments. Default to local resources. Never access
  production during development or create expensive cloud resources.
- Build a reusable public research data platform, with OpenAlex first. Use public
  sources or synthetic fixtures only; never use Wiley proprietary data or require
  Wiley enterprise access. AACT/ClinicalTrials (issue #2), OpenFDA, grants, patents,
  news/releases, and other sources are future extensions until explicitly scoped.
- Never commit credentials, `.env` files, service-account JSON, or downloaded large
  datasets. Never hardcode cloud project IDs. Obtain credentials from environment
  variables or future workload identity; keep credential files outside the repo.
  Never log credentials, DSNs, environment contents, or sensitive payloads.
- Separate source discovery/manifests, immutable raw landing, normalized canonical
  modeling, and serving. Do not create a giant flattened canonical table.
- Make ingestion idempotent and attach provenance to every ingestion: run identity,
  source URI, retrieval time, and checksum. Raw objects and their provenance must
  never be overwritten. Atomic creation and conflict handling belong in adapters.
- Put storage behind ObjectStore adapters (local/GCS), sources behind
  SourceConnector, and warehouse operations behind Warehouse where practical.
  Bind query parameters rather than interpolating untrusted SQL values.
- On GCP, use BigQuery as the first analytical implementation and DuckDB for local
  analytical tests. Measure query patterns before choosing serving technology.
  PostgreSQL/AlloyDB is optional: test BigQuery with API/cache first and add an
  operational store only when measured latency, concurrency, or indexed lookup
  requirements justify it. Keep canonical schemas independent of serving engines.
- Keep high-volume work-author, work-topic, work-institution, and citation/reference
  relationships primarily analytical. Materialize repeated expensive aggregates
  when measured use justifies them. Expose storage-independent domain capabilities
  through a Data Service/API; unrestricted raw SQL is not the product API.
- Keep tests offline, deterministic, and based on tiny public or synthetic fixtures.
  Run `make test`, `make check`, and `make build` for code changes.
- Docker Compose is local PostgreSQL tooling only. Airflow DAGs and Terraform
  directories are reserved for later; do not implement them yet.
- Maintain architecture documentation and Mermaid diagrams; Docusaurus publication
  is planned later. Do not introduce LLM, ML, GenAI, NLP, or knowledge graph
  implementations until explicitly requested in a later phase.
