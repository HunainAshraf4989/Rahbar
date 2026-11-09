PYTHON ?= python3
export PYTHONPATH := app/server
.PHONY: contracts test-contracts lint
contracts:
	$(PYTHON) tools/export_contracts.py
	npm run contracts:types

test-contracts:
	$(PYTHON) -m pytest
	npm run test:contracts

lint:
	$(PYTHON) -m ruff check app/server tools
	$(PYTHON) -m mypy
