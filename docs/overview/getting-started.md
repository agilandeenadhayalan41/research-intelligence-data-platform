---
title: Getting started
description: How the repository is laid out, what the stack is, and how to run the offline tests on a laptop.
---

# Getting started

This page covers how the repository is laid out and how to run it locally.
Everything here works offline, with no cloud credentials.

## What is in the repository

| Path | What it holds |
| --- | --- |
| [`src/research_platform/`](../../src/research_platform/) | The Python package (see the list below the table) |
| [`tests/`](../../tests/) | Offline unit, contract and integration tests with synthetic fixtures. PostgreSQL suites are opt-in |
| [`sql/`](../../sql/) | BigQuery Standard SQL under `sql/bigquery/openalex/`, and PostgreSQL DDL specs (see below). Applying any of it is a separate, explicit step |
| [`config/`](../../config/) | `local.yaml` (safe offline default), `gcp-sandbox.yaml` (cloud *shape* only, from environment variables), `dev`/`qa`/`prod` local-only placeholders, and the query-pattern registry under `config/query-patterns/`. The manual cloud scripts do not read these YAML files |
| [`scripts/`](../../scripts/) | Manual, operator-run cloud smoke and measurement scripts (#103, #108, #118; the two smoke scripts repeat the #91 and #92 checks, which ran before they existed). Their live runs are never part of `make test` or CI; offline unit tests exercise them with fake clients |
| [`docs/`](../) | `architecture/` (detailed design and evidence), `overview/` (these pages), `requirements/` (the lead coverage register), `proposed/` (the proposed next target architecture) and `site/` (the Docusaurus project that renders this site). `data-model/`, `development/` and `runbooks/` are empty placeholders |
| [`.github/`](../../.github/) | The CI workflow (`tests.yml`) and the engineering rules in `copilot-instructions.md` |
| [`dags/`](../../dags/), [`infrastructure/terraform/`](../../infrastructure/terraform/), [`data/`](../../data/) | Reserved, empty placeholders. No Airflow DAGs, no Terraform resources and no committed datasets |
| [`Makefile`](../../Makefile), [`pyproject.toml`](../../pyproject.toml), [`docker-compose.yml`](../../docker-compose.yml) | Build and test targets, package metadata and pinned dependencies, and an optional local PostgreSQL 16 service |

The Python package under `src/research_platform/` holds:

- the OpenAlex source code, and the storage and warehouse adapters;
- pipeline control, the canonical model, and ingestion and deletions;
- the query-pattern registry, and the BigQuery and Gold contracts;
- data quality, the Data Service and the end-to-end runner;
- the orchestration contracts and the readiness registry;
- the #23 lookup prototype and the #118 lookup cache.

The `sql/` folder holds the BigQuery DDL, models, queries, Gold marts and
quality checks under `sql/bigquery/openalex/`, plus PostgreSQL DDL specs for
pipeline control, the canonical model and the #23 prototype.

The stack is Python 3.12+ built with setuptools. Runtime dependencies are
Pydantic, PyYAML, PyArrow, `google-cloud-storage` and `google-cloud-bigquery`.
Development uses pytest and DuckDB, and the optional `postgres` extra adds
psycopg. CI runs install, check, test and build on Python 3.12 and 3.14
([pyproject.toml](../../pyproject.toml),
[tests.yml](../../.github/workflows/tests.yml)).

## Running it locally

Default tests need no cloud credentials, no network and no external services.
From the repository root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
make install
make test
make check
make build
```

On Linux or macOS, activate with `source .venv/bin/activate` instead.

`make test` runs the offline suite and excludes PostgreSQL-marked tests. The
PostgreSQL suites are separate targets (`make test-postgres-ingestion`,
`make test-postgres-warehouse`, `make test-postgres-serving`), and they need
`POSTGRES_DSN`. `make check` compiles the code and checks dependency
consistency. It is not a linter, formatter or type checker; no such target
exists. See the [README](../../README.md) and the [Makefile](../../Makefile).

## Working on these docs

The documentation site lives in [`docs/site/`](../site/README.md). Its README
explains how to build it locally and how it deploys.
