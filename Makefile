PYTHON ?= python

.PHONY: install test test-unit test-openalex-live test-orchestration test-postgres-ingestion test-postgres-warehouse test-postgres-serving check build postgres-up postgres-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -m "not postgres and not openalex_live"

test-unit:
	$(PYTHON) -m pytest tests/unit -m "not postgres and not openalex_live"

test-openalex-live:
	@test -n "$(OPENALEX_LIVE_SCRATCH)" -a -n "$(OPENALEX_LIVE_BUDGET_LEDGER)" || { echo "set OPENALEX_LIVE_SCRATCH and OPENALEX_LIVE_BUDGET_LEDGER (outside the repo)"; exit 2; }
	mkdir -p $(OPENALEX_LIVE_SCRATCH)/tmp
	OPENALEX_LIVE_TESTS=1 TMPDIR=$(OPENALEX_LIVE_SCRATCH)/tmp $(PYTHON) -m pytest tests/integration/test_real_source_profile_live.py -m openalex_live -p no:cacheprovider --basetemp=$(OPENALEX_LIVE_SCRATCH)/pytest

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
