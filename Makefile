PYTHON ?= python

.PHONY: install test test-unit check build postgres-up postgres-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest tests/unit

check:
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -m pip check

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down
