# callsense

Turns a call-centre recording into a speaker-labelled transcript, a structured summary, a weighted
quality score and a compliance report — with prompt-injection detection and PII redaction happening
*before* any of it reaches a language model.

Seven pipeline stages orchestrated by a LangGraph state machine, running Whisper locally and any of
three LLM providers behind one interface. 166 tests, no API keys needed to run them.

```bash
make install && cp .env.example .env    # add one provider key
make run                                # http://localhost:7860
```

## Architecture

Seven stages, eight graph nodes, and two decisions that make it a graph rather than a sequence of
function calls.

```
audio ─▶ intake ─▶ transcription ─▶ injection check ─▶ PII redaction ─▶ summarize + QA ─▶ report
           │                              │                                    │
           └── invalid ──▶ error          └── detected ──▶ supervisor          └── critical flag
                                                                                    ──▶ supervisor
```

| # | Stage | What it does |
|---|---|---|
| 1 | **Intake** | Magic-byte format detection, size and duration gates, metadata PII scan |
| 2 | **Transcription** | faster-whisper int8 with VAD, heuristic diarization, per-segment confidence, SHA-256 cache |
| 3 | **Injection detection** | 22 regex patterns over the transcript, **before** any LLM call |
| 4 | **PII redaction** | SSN, credit card, email and phone — in the full text *and* every segment |
| 5 | **Summarization** | Structured output validated by Pydantic, with exponential backoff |
| 6 | **QA scoring** | Five weighted dimensions; receives the summary as context |
| 7 | **Report** | Assembles a `CallReport`, persists a `CallRecord`, writes the audit log |

**Injection detection is a fail-closed input guard**, which is the opposite of the usual posture and
deliberately so. Here the input *is* the transcript: anyone who can speak into a call can write to
the model's prompt. A false positive costs one rejected call; a false negative puts
attacker-controlled text into an LLM. So a match halts the pipeline before stage 5.

**PII redaction replaces the transcript in state** rather than producing a second copy. Downstream
stages read the same key and there is no raw version left to reach for, which makes leaking it
unrepresentable rather than merely discouraged.

**A single critical compliance flag routes the call to supervisor review**, however well it scored.
Severity is never averaged into the score and cannot be outweighed by good handling elsewhere.

### Layout

```
app.py                  entrypoint: schema, model warm-up, graph compile, launch
src/
  ui/                   Gradio tabs
  services/             pipeline orchestration — the seam between UI and graph
  agents/               one module per pipeline stage
  graph/                typed state, routing edges, workflow assembly
  database/             SQLAlchemy models and connection handling
  security/             injection detection, PII redaction, audit logging
  utils/                config, audio helpers, LLM factory, formatters
tests/
  unit/                 agents, security, routing, models, formatters
  integration/          end-to-end pipeline (mocked Whisper and LLM)
  security/             22 injection payloads, 25 PII format variants
```

The specification names five layers; `security/` and `utils/` are their own, so there are seven.

## Setup

Python 3.11+ and `ffmpeg` on the PATH.

```bash
brew install ffmpeg        # or apt-get install ffmpeg
make install               # dependencies + pre-commit hooks
cp .env.example .env
```

Then set **one** provider key in `.env` — `OPENAI_API_KEY`, `GOOGLE_API_KEY` or `GROQ_API_KEY` —
and `LLM_PROVIDER` to match. Every variable is documented inline in `.env.example`.

Whisper weights download on first run and are cached by `faster-whisper`; nothing needs fetching
manually.

## Running

```bash
make run     # http://localhost:7860
make demo    # same, with WHISPER_MODEL_SIZE=base — see docs/DEMO.md
```

Upload a recording or use the microphone. Optional caller ID and department fields are scanned for
PII at intake.

`data/demo/` carries two short files that exercise the security stages: `card.mp3` for redaction and
`injection.mp3` for the injection guard.

## Testing

```bash
make test              # unit + security — no API keys, no audio files, seconds
make test-integration  # end-to-end through the compiled graph
make test-all          # everything
make lint              # ruff
```

166 tests. **The suite needs no API keys and no real audio**: WAV bytes are generated in-process,
Whisper is mocked, and both LLM calls are patched per importing module.

Three tests worth knowing about:

- **The deterministic recomputation.** The model returns `overall_score=3.0` while scoring every
  dimension 5/5; the result must be 5.0. A second test uses five *distinct* dimension scores,
  because any weights summing to 1.0 turn five 5s into 5.0 and would hide a transposed weight.
- **The cache hit.** Two runs over the same audio, asserting `transcribe` was called *once*. Nothing
  else distinguishes a cache hit from work that merely happened to be fast.
- **PII replacement order.** Two SSNs in one string, where left-to-right replacement leaves `321`
  behind. The obvious assertion — that the full value is absent — *passes* on the broken version,
  because the mis-aligned cut destroys the value as a side effect. Only comparing the exact output
  catches it.

## Sample usage

One call through the Analyze tab. Everything below is real output, not illustration.

**Transcript** — speaker-labelled, redacted, with low-confidence segments marked:

```
[00:03] Agent: Thanks for calling Metro Bank, this is Dana. How may I assist you today?
[00:07] Customer: Hi, I need help with a charge I don't recognise on my account.
[00:12] Customer: My card is [REDACTED_CREDIT_CARD].
[00:25] [LOW CONF] Agent: Let me check that for you. I've gone ahead and reversed it.
[00:29] [LOW CONF] Agent: mumbled something about the timing
```

The card number is redacted *in the transcript the language model received* — stage 4 replaces the
transcription in state, so no raw copy survives to be sent. The `[LOW CONF]` markers arrive in
pairs rather than singly because `avg_logprob` is computed per 30-second decode window, so
consecutive segments share a confidence value.

**Summary** — validated Pydantic output rendered as Markdown:

```markdown
### Call Purpose
Dispute an unrecognised $340 charge on a bank statement.

### Action Items
1. Reverse the disputed charge — owner: Dana — due: 2 business days

### Resolution Status
Resolved

### Sentiment Trajectory
Concerned -> Reassured
```

**QA scorecard** — five weighted dimensions, each with a justification citing a timestamp:

```markdown
### Overall Score
3.5

#### Professionalism (15%)
Score: 4
Justification: Courteous throughout, though you closed abruptly at 00:39.

#### Compliance (20%)
Score: 1
Justification: At 00:25 you reversed a disputed charge without verifying identity.

### Compliance Flags
1. 🔴 **00:25** — Account action taken without identity verification.
```

**Two things happened here that are the point of the project.**

The language model proposed an overall score of **3.0**. The score shown is **3.5**, recomputed in
Python from the weighted dimensions — the model's figure was discarded, not reconciled. The weights
are displayed beside each dimension so the arithmetic is checkable rather than asserted.

And the call finished with status **`flagged_for_review`**, not `completed`, despite scoring 3.5.
One critical compliance flag routes the call to a supervisor however well it scored, because
severity is never averaged into the score.

**The two other paths**, for completeness:

```
data/demo/injection.mp3  ->  flagged_for_review
                             "Analysis stopped. A prompt injection attempt was detected in this
                              transcript, so it was never sent to the language model.
                              Patterns matched: ignore_previous, prompt_leak"

an .ogg file             ->  failed
                             "Unsupported audio format: sample_01.ogg"
```

The injection case never reaches stage 5. That is the difference between the two security stages:
PII is redacted and processing continues; an injection stops the pipeline.

## Configuration

All in `.env`, all documented in `.env.example`. The ones that change behaviour most:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai`, `gemini` or `groq` |
| `WHISPER_MODEL_SIZE` | `tiny` | `tiny` for development; `base` or larger for real audio |
| `CONFIDENCE_THRESHOLD` | `0.6` | Measured, not guessed — see above |
| `LOW_CONFIDENCE_HALT_RATIO` | `0.8` | Fraction of low-confidence segments that flags a transcript |
| `DB_PATH` | `data/calls.db` | SQLite |
| `MAX_RETRIES_PER_NODE` | `3` | Attempts, not retries-after-the-first |

## Deployment

**Locally**: `make run`.

**Container**:

```bash
docker build -t callsense .
docker run --env-file .env -p 7860:7860 callsense
```

Verified: builds clean and serves on `http://localhost:7860` within about five seconds. The image is
~2 GB, most of it the CTranslate2 and ONNX runtimes that faster-whisper depends on.

`ffmpeg` is installed in the image because faster-whisper shells out to it to decode anything that
is not already PCM WAV — which is every mp3, m4a and flac the validator accepts.

Two volumes are worth mounting for anything beyond a demo:

```bash
docker run --env-file .env -p 7860:7860 \
  -v callsense-data:/app/data \
  -v callsense-models:/app/.cache/huggingface \
  callsense
```

The first keeps the SQLite database across restarts; the second keeps the Whisper weights, which are
otherwise downloaded on every container start.

A container binds `0.0.0.0` rather than loopback — `127.0.0.1` inside a container is the *container's*
own loopback, so a published port would map to nothing and refuse the connection. The Dockerfile sets
`GRADIO_SERVER_NAME`, and `app.py` reads it.

**GPU.** Device selection is automatic: CUDA with `float16` when available, otherwise CPU with
`int8`. Apple Silicon deliberately falls back to CPU — CTranslate2 has no Metal backend, so MPS
would be slower, not faster. On CPU the throughput measured here is roughly 74× realtime with
`tiny`: fifty-five minutes of audio transcribed in forty-five seconds.

**HuggingFace Spaces**: `SPACE_ID` is set there, which the entrypoint treats the same way. No code
change needed.

## Design decisions worth defending

A few of these came out of arguing with the specification rather than following it.

**Three database tables, not one.** They have different keys, write patterns and lifecycles.
`call_records` is written once per call with `call_id` unique; `audit_log` is append-only with
`call_id` deliberately *not* unique, because one call produces several entries; `transcription_cache`
is content-addressed by SHA-256 with an N:1 relationship to calls — two calls uploading the same
recording share one transcript.

**Append-only is enforced by the API, not the schema.** SQLite will accept an `UPDATE`. What makes
the audit log immutable is that `AuditLogger` exposes `log()` and nothing else.

**PII replacement runs right-to-left.** Matches are collected from the original text first, sorted,
overlaps dropped, then applied in reverse. Left-to-right invalidates every offset behind the first
edit — and since `[REDACTED_CREDIT_CARD]` is longer than the number it replaces, later spans land
early and can leave a fragment of the next value intact.

**Structured output was verified across all three providers before anything was built on it.** That
half-hour found that the specified Groq model no longer exists, and that of the models Groq does
offer, only one supports the tool calling structured output requires. The nested schema — five
dimension objects plus a compliance-flag list — was never the problem.

**The transcript has three formats, deliberately not unified.** One for the LLM prompt
(`[MM:SS - MM:SS]` ranges), one for the UI (`[MM:SS]` with `[LOW CONF]` markers), and one for the
report. The first is read by a model that will imitate its format; the others by a person.

## What is actually interesting here

Most of this project is a specification followed carefully. These are the parts where following it
carefully meant measuring something and finding the specification wrong.

**The confidence threshold is 0.6, not the specified 0.3.** Running all ten reference calls through
Whisper produced 771 segments with a minimum confidence of 0.412 — so at 0.3 the low-confidence flag
could never have fired. It was dead code. At 0.6 it discriminates between calls: eight of ten
samples have no low-confidence segments at all, while two have thirty-two and twenty-two.

**Speaker diarization ranks content patterns above timing gaps, because the gap is unreliable in a
specific way.** The specified 1.2-second gap fires on 16% of segment boundaries overall — but per
call that ranges from 3% to 38%, a twelve-fold spread driven by conversational pace. Hand-labelling
two calls confirmed it: 67% recall on a slow, formal bank call and one-in-eight on a fast sales
call. The signal is inconsistent, not weak, which is why it corrects a run of unmatched segments but
never overrides an explicit content match.

**Content patterns have to match what the ASR emits, not what the agent said.** `assist you` occurs
14 times across 6 of the reference calls; `how can I help` occurs once, because Whisper mangles the
"help" variants into things like *"How can you help you today?"*. Seven of thirty plausible-sounding
candidate phrases occur zero times in real call audio. The pattern lists are built from
measurement — and `my name is`, despite thirteen occurrences across eight calls, is excluded because
both speakers use it.

**A larger Whisper model can reduce PII detection.** `base` transcribes a spoken "eight oh one" as
the reference code `A-01-431-1000`, which matches no phone pattern; `tiny` writes `801-431-1000` and
it redacts. Redaction acts on the transcript, so the model's interpretive choices decide what is
detectable — and more capable is not the same as more literal. See [Known limitations](#known-limitations).

**The QA score the model proposes is discarded, every time.** `overall_score` is recomputed in Python
from five weighted dimensions. Two runs over the same dimension scores produce the same overall,
which a language model cannot guarantee and an auditable QA process requires. The model still
proposes one — the UI shows both, which makes the difference visible rather than asserted.

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
