# The checks CI runs, available locally with one word.
# `make check` is the gate; `make fix` is the convenience.

.PHONY: help install check lint format type security audit test cov docker docker-run fix clean

help:
	@echo "make install    — editable install with dev tools"
	@echo "make check      — lint, format check, types, security, audit, tests (what CI runs)"
	@echo "make test       — the test suite only"
	@echo "make cov        — the suite under coverage, with the floor enforced"
	@echo "make docker     — build the container image"
	@echo "make docker-run — build and start it with docker compose"
	@echo "make fix        — autofix lint and formatting"

install:
	python -m pip install -e ".[dev]"

check: lint format type security audit test

# The starter kit is checked beside the project, so its patterns cannot rot
# away from their tests.
lint:
	python -m ruff check src tests research starter

format:
	python -m ruff format --check src tests research starter

type:
	python -m mypy src/goldbot starter/goldkit

security:
	python -m bandit -c pyproject.toml -r src/goldbot starter/goldkit

audit:
	python -m pip_audit .

test:
	python -m pytest tests starter/tests -q

cov:
	python -m coverage run -m pytest tests starter/tests -q
	python -m coverage report

docker:
	docker build -t goldbot:local .

docker-run:
	docker compose up -d --build

fix:
	python -m ruff check --fix src tests research starter
	python -m ruff format src tests research starter

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
