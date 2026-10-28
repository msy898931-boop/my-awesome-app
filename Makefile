# The checks CI runs, available locally with one word.
# `make check` is the gate; `make fix` is the convenience.

.PHONY: help install check lint format type test fix clean

help:
	@echo "make install  — editable install with dev tools"
	@echo "make check    — lint, format check, types, tests (what CI runs)"
	@echo "make fix      — autofix lint and formatting"
	@echo "make test     — the test suite only"

install:
	python -m pip install -e ".[dev]"

check: lint format type test

lint:
	python -m ruff check src tests research

format:
	python -m ruff format --check src tests research

type:
	python -m mypy src/goldbot

test:
	python -m pytest tests -q

fix:
	python -m ruff check --fix src tests research
	python -m ruff format src tests research

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
