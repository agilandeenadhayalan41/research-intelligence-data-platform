PYTHON ?= python

.PHONY: install test test-unit test-postgres-ingestion check build postgres-up postgres-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -m "not postgres"

test-unit:
	$(PYTHON) -m pytest tests/unit -m "not postgres"

test-postgres-ingestion:
	$(PYTHON) -m pip install -e ".[dev,postgres]"
	$(PYTHON) -m pytest tests/integration/test_postgres_ingestion.py -m postgres

check:
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -m pip check

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down
