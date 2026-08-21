.PHONY: install test test-integration test-all lint format run demo docker-build docker-run clean

VENV := .venv
PY   := $(VENV)/bin/python

# The interpreter used to create the venv. The project needs 3.11+, and stock
# macOS still ships 3.9 as python3 — so search for the first one new enough
# rather than assuming. Override explicitly to pin a particular build:
#   make install PYTHON=python3.12
PYTHON ?= $(shell for p in python3 python3.13 python3.12 python3.11; do \
	  command -v $$p >/dev/null 2>&1 \
	    && $$p -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
	    && echo $$p && break; \
	done)

install:  ## Create the venv, install with dev extras, register hooks
	@test -n "$(PYTHON)" || { \
	  echo "No Python 3.11+ found. Install one, or point at it directly:"; \
	  echo "    make install PYTHON=/path/to/python3.12"; \
	  exit 1; \
	}
	@echo "Using $(PYTHON) ($$($(PYTHON) -V 2>&1))"
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
# uv-created virtualenvs deliberately ship without pip, so make sure it exists
# before using it. Harmless where pip is already present.
	@$(PY) -m ensurepip --upgrade >/dev/null 2>&1 || true
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"
# Hooks are for contributors and need a git repo. A reviewer working from the
# submitted zip has no .git, so skip rather than fail — and say so, because
# pre-commit's own error reads like the install broke when it did not.
	@test -d .git \
	  && $(VENV)/bin/pre-commit install \
	  || echo "Skipped pre-commit hooks (not a git repository). Install complete."

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

demo:  ## Start the app configured for recording (see docs/DEMO.md)
	WHISPER_MODEL_SIZE=base $(PY) app.py

docker-build:  ## Build the container image
	docker build -t callsense .

docker-run: docker-build  ## Build and serve on http://localhost:7860
	docker run --rm -p 7860:7860 --env-file .env \
	  -v callsense-data:/app/data \
	  -v callsense-models:/app/.cache/huggingface \
	  callsense

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
