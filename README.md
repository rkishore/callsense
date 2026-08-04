# callsense

Call Center Intelligence System — a LangGraph pipeline that turns raw call-center audio into
speaker-labeled transcripts, structured summaries, weighted QA scores, compliance flags, and
downloadable PDF/JSON reports.

> 🚧 Under construction. This README is filled out at Milestone 6 with the architecture overview,
> setup instructions, run/test commands, GPU deployment notes, and sample usage.

## Quick start

```bash
make install     # deps + pre-commit hooks
cp .env.example .env   # then fill in your keys
make test        # unit + security suites (no API keys needed)
make run         # http://localhost:7860
```

## Layout

```
app.py                  thin entrypoint
src/
  agents/               one module per pipeline stage
  graph/                PipelineState, routing edges, workflow assembly
  security/             injection detection, PII redaction, audit logging
  services/             pipeline orchestration + observability queries
  ui/                   Gradio tabs
  database/             SQLAlchemy models and connection handling
  utils/                config, audio helpers, LLM factory, formatters
tests/
  unit/                 agents, security functions, routing, models, formatters
  integration/          end-to-end pipeline and persistence (mocked Whisper + LLM)
  security/             PII format coverage, injection payload coverage
```

## Requirements

Python 3.11+ and `ffmpeg` on the PATH.
