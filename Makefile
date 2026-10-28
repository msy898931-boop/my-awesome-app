# The checks CI runs, available locally with one word.
# `make check` is the gate; `make fix` is the convenience.

.PHONY: help install check lint format type security audit test cov fix clean

help:
	@echo "make install  — editable install with dev tools"
	@echo "make check    — lint, format check, types, security, audit, tests (what CI runs)"
	@echo "make test     — the test suite only"
	@echo "make cov      — the suite under coverage, with the floor enforced"
	@echo "make fix      — autofix lint and formatting"

install:
	python -m pip install -e ".[dev]"

check: lint format type security audit test

lint:
	python -m ruff check src tests research

format:
	python -m ruff format --check src tests research

type:
	python -m mypy src/goldbot

security:
	python -m bandit -c pyproject.toml -r src/goldbot

audit:
	python -m pip_audit

test:
	python -m pytest tests -q

cov:
	python -m coverage run -m pytest tests -q
	python -m coverage report

fix:
	python -m ruff check --fix src tests research
	python -m ruff format src tests research

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
