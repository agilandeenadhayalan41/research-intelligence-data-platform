PYTHON ?= python

.PHONY: install test test-unit test-openalex-live test-gcs-live test-canonical-gcs-live test-orchestration test-postgres-ingestion test-postgres-warehouse test-postgres-serving check build postgres-up postgres-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -m "not postgres and not openalex_live and not gcs_live"

test-unit:
	$(PYTHON) -m pytest tests/unit -m "not postgres and not openalex_live and not gcs_live"

test-openalex-live:
	@test -n "$(OPENALEX_LIVE_SCRATCH)" -a -n "$(OPENALEX_LIVE_BUDGET_LEDGER)" || { echo "set OPENALEX_LIVE_SCRATCH and OPENALEX_LIVE_BUDGET_LEDGER (outside the repo)"; exit 2; }
	mkdir -p $(OPENALEX_LIVE_SCRATCH)/tmp
	OPENALEX_LIVE_TESTS=1 TMPDIR=$(OPENALEX_LIVE_SCRATCH)/tmp $(PYTHON) -m pytest tests/integration/test_real_source_profile_live.py -m openalex_live -p no:cacheprovider --basetemp=$(OPENALEX_LIVE_SCRATCH)/pytest

test-gcs-live:
	@test -n "$(GCS_LIVE_BUCKET)" -a -n "$(GCS_LIVE_CONFIRM_BUCKET)" -a -n "$(GCS_LIVE_CONFIRM_PROJECT)" -a -n "$(GCS_LIVE_PROJECT_CLASS)" -a -n "$(GCS_LIVE_EVIDENCE_DIR)" || { echo "set GCS_LIVE_BUCKET, GCS_LIVE_CONFIRM_BUCKET, GCS_LIVE_CONFIRM_PROJECT, GCS_LIVE_PROJECT_CLASS and GCS_LIVE_EVIDENCE_DIR (outside the repo); GOOGLE_CLOUD_PROJECT must equal GCS_LIVE_CONFIRM_PROJECT"; exit 2; }
	@case "$(abspath $(GCS_LIVE_EVIDENCE_DIR))/" in "$(CURDIR)/"*) echo "GCS_LIVE_EVIDENCE_DIR must be outside the repository"; exit 2;; esac
	@$(PYTHON) -c 'import sys; from pathlib import Path; from research_platform.analytics.bigquery.sandbox.live import evidence_dir; evidence_dir(Path(sys.argv[1]), Path.cwd().resolve())' "$(GCS_LIVE_EVIDENCE_DIR)" 2>/dev/null || { echo "GCS_LIVE_EVIDENCE_DIR must be outside the repository"; exit 2; }
	@mkdir -p "$(GCS_LIVE_EVIDENCE_DIR)/tmp"
	@GCS_LIVE_TESTS=1 DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true TMPDIR="$(GCS_LIVE_EVIDENCE_DIR)/tmp" $(PYTHON) -m pytest tests/integration/test_approved_gcs_raw_landing.py -m gcs_live -p no:cacheprovider -q --tb=no -rN --junitxml="$(GCS_LIVE_EVIDENCE_DIR)/junit.xml" --basetemp="$(GCS_LIVE_EVIDENCE_DIR)/pytest"

test-canonical-gcs-live:
	@test -n "$(GCS_LIVE_BUCKET)" -a -n "$(GCS_LIVE_CONFIRM_BUCKET)" -a -n "$(GCS_LIVE_CONFIRM_PROJECT)" -a -n "$(GCS_LIVE_PROJECT_CLASS)" -a -n "$(GCS_LIVE_EVIDENCE_DIR)" || { echo "set GCS_LIVE_BUCKET, GCS_LIVE_CONFIRM_BUCKET, GCS_LIVE_CONFIRM_PROJECT, GCS_LIVE_PROJECT_CLASS and GCS_LIVE_EVIDENCE_DIR (outside the repo); GOOGLE_CLOUD_PROJECT must equal GCS_LIVE_CONFIRM_PROJECT"; exit 2; }
	@case "$(abspath $(GCS_LIVE_EVIDENCE_DIR))/" in "$(CURDIR)/"*) echo "GCS_LIVE_EVIDENCE_DIR must be outside the repository"; exit 2;; esac
	@$(PYTHON) -c 'import sys; from pathlib import Path; from research_platform.analytics.bigquery.sandbox.live import evidence_dir; evidence_dir(Path(sys.argv[1]), Path.cwd().resolve())' "$(GCS_LIVE_EVIDENCE_DIR)" 2>/dev/null || { echo "GCS_LIVE_EVIDENCE_DIR must be outside the repository"; exit 2; }
	@mkdir -p "$(GCS_LIVE_EVIDENCE_DIR)/tmp"
	@CANONICAL_GCS_LIVE_TESTS=1 DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true TMPDIR="$(GCS_LIVE_EVIDENCE_DIR)/tmp" $(PYTHON) -m pytest tests/integration/test_canonical_gcs_release.py -m gcs_live -p no:cacheprovider -q --tb=no -rN --junitxml="$(GCS_LIVE_EVIDENCE_DIR)/junit-canonical.xml" --basetemp="$(GCS_LIVE_EVIDENCE_DIR)/pytest-canonical"

test-orchestration:
	$(PYTHON) -m pytest tests/unit/test_orchestration_contracts.py -m "not postgres"

test-postgres-ingestion:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_ingestion.py tests/integration/test_postgres_deletion_migration.py -m postgres

test-postgres-warehouse:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_warehouse_integration.py -m postgres

test-postgres-serving:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_work_lookup_integration.py -m postgres

check:
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -m pip check

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down
