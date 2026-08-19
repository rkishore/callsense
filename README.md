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

## Known limitations

Measured during development, not discovered by a reviewer.

**PII detection is regex over digits, so spoken-word numbers escape it.** A caller who reads a card
number slowly enough that Whisper transcribes it as words — *"four one one one, one one one one…"* —
produces text no digit pattern matches. Every real spoken number in the ten reference calls came out
as digits, so this is a tail case rather than the common one, but it is a genuine hole.

**Repeated digits defeat ASR counting.** A dictated `4111-1111-1111-1111` — thirteen identical
digits with no acoustic variation to anchor the count — came back from Whisper as 25 digits, and as
19 when read with pauses. Never 16. Card numbers with varied digits transcribe correctly. The
redactor absorbs over-long runs whole rather than letting a shorter pattern match inside one, so
this degrades the transcript rather than leaking, but the digits it redacts are not the digits that
were spoken.

**Model size affects redaction, and not monotonically.** Redaction can only act on what the
transcript says, so the model's interpretive choices decide what is detectable — and a *larger*
model is not reliably better. Measured on the same recording:

| | dictated card number | spoken phone number in `sample_01` |
|---|---|---|
| `tiny` | `4.539-8712-3456-789-0` — leading `4.` escapes | `801-431-1000` — **redacted** |
| `base` | `4539-8712-3456-7890` — **redacted** | `A-01-431-1000` — escapes |
| `small` | — | `A01-431-1000` — escapes |

`base` and `small` hear spoken "eight oh one" and, in context, render it as a letter-prefixed
reference code; `tiny` simply writes the digits. So the more capable model produces a transcript
the redactor cannot act on. No single setting redacts both, and the patterns are deliberately not
widened to tolerate a letter prefix — that would over-fit one ASR artifact at the cost of false
positives on ordinary text.

**Only structured PII is redacted.** SSN, credit card, email and phone are covered. Names,
addresses, employers and dates of birth are not — `sample_01` alone contains *"my name is John
Smith"* and a city, state and ZIP, all of which currently reach the LLM. See the roadmap below.

**The 50 MB file cap and the 60-minute duration cap are in tension.** The reference audio runs about
115 kbps, so a spec-legal 60-minute call is roughly 52 MB and is rejected on size before duration is
ever checked.

**Speaker diarization is heuristic and assumes the first speaker is the agent.** Five of the ten
reference calls open with a narrator rather than an agent — they are training recordings — so the
opening segments mislabel until a content pattern anchors the run.

## Roadmap

**NER-based PII detection on the input gate.** The clear next step, because it closes the
unstructured-PII gap above: names, addresses and employers are exactly what a named-entity model
catches and a regex cannot. Presidio layered over the existing regex, rather than replacing it —
regex stays authoritative for the structured types, where it is faster and has no false-negative
mode. The cost is a spaCy model in the image and added per-call latency, which is why it is not in
the current build.

**Output-side guardrails were considered and deliberately skipped.** The LLM's output here is
already constrained by construction: both calls use Pydantic structured output, so field types,
score ranges and enums are validated at the boundary, and `overall_score` is recomputed in Python
from the weighted dimensions with the model's own figure discarded. A framework like Guardrails AI
would mostly re-check what the schema already guarantees. PII cannot leak through the output either,
since redaction happens before the model sees the transcript — there is nothing left to leak. The
residual risk is hallucinated content grounded in nothing, which is a prompting and evaluation
problem rather than a validation one.

## Requirements

Python 3.11+ and `ffmpeg` on the PATH.
