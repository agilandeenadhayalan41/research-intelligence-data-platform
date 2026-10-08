PYTHON ?= python

.PHONY: install test test-unit test-orchestration test-postgres-ingestion test-postgres-warehouse check build postgres-up postgres-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -m "not postgres"

test-unit:
	$(PYTHON) -m pytest tests/unit -m "not postgres"

test-orchestration:
	$(PYTHON) -m pytest tests/unit/test_orchestration_contracts.py -m "not postgres"

test-postgres-ingestion:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_ingestion.py tests/integration/test_postgres_deletion_migration.py -m postgres

test-postgres-warehouse:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_warehouse.py -m postgres

check:
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -m pip check

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down
