.PHONY: install test test-integration test-all lint format run clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := uv pip

install:  ## Install the project with dev extras and register pre-commit hooks
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install

test:  ## Unit + security suites (fast, no API keys, no real audio)
	$(PY) -m pytest tests/unit tests/security -v

test-integration:  ## End-to-end pipeline tests (mocked Whisper + LLM)
	$(PY) -m pytest tests/integration -v

test-all:  ## Everything
	$(PY) -m pytest tests/ -v

lint:  ## Check without modifying
	$(VENV)/bin/ruff check .

format:  ## Autofix and format
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

run:  ## Start the Gradio app on http://localhost:7860
	$(PY) app.py

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
