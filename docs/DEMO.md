# Demo runbook

Everything needed to record the walkthrough, written while the reasons were still fresh rather than
reconstructed on the day.

## Before recording

**Run `make demo`, not `make run`.** It sets `WHISPER_MODEL_SIZE=base`.

This is not cosmetic. On `tiny`, a dictated card number transcribes as `4.539-8712-3456-789-0`, and
the leading `4.` sits outside the matched span — so the redaction shows `4.[REDACTED_CREDIT_CARD]`
on camera. `base` renders it as `4539-8712-3456-7890` and redacts cleanly. `tiny` stays the default
everywhere else; the test suite mocks Whisper and never cares.

**Decide on `launch(show_error=True)`.** Right for development, probably wrong for a recording —
it surfaces internal detail in the error toast.

**Check the temp directory.** Gradio copies every upload into its own hashed temp folder, separate
from the PDFs and JSON the pipeline writes. A stale one from testing can confuse a live walkthrough.

## Audio

| File | Purpose | Notes |
|---|---|---|
| `data/samples/sample_01.mp3` | the normal path | Real service call. Contains a genuine spoken phone number — redaction on real audio, not a synthetic file |
| `data/demo/card.mp3` | PII redaction | TTS. Varied digits deliberately: repeated digits defeat ASR counting, and `4111-1111-1111-1111` transcribes as 25 digits |
| `data/demo/injection.mp3` | injection blocked | TTS. Trips `ignore_previous` and `prompt_leak` — two patterns, so the report shows a list rather than a single name |
| an `.ogg`, and a 51 MB file | rejection paths | Trivially synthetic. `gr.Audio` has no format allow-list, so the validator is what rejects them |

`data/samples/` is gitignored — the reference audio is not ours to republish. `data/demo/` is
tracked, so a reviewer who clones the repo can reproduce the security beats.

## The spine

Seven beats, in this order. Each one shows something the previous cannot.

1. **`sample_01.mp3` end to end** — speaker-labelled transcript, summary, QA scorecard, PDF.
   Establishes that the ordinary path works before anything clever.
2. **The real phone number redacted** — from a genuine customer service call, not a prop.
3. **`card.mp3`** — `[REDACTED_CREDIT_CARD]` in the transcript the LLM received. The point is not
   that redaction happened but *where*: before the third-party call, not after.
4. **`injection.mp3`** — pipeline halts before any LLM call, with `ignore_previous` and
   `prompt_leak` named. Contrast with beat 3: PII is redacted and processing continues; injection
   stops the pipeline. Different postures for different risks.
5. **LLM score beside the recomputed score** — the model proposes an overall figure and Python
   discards it, recomputing from the weighted dimensions. Same inputs, reproducible output.
6. **A critical compliance flag routing to supervisor review** — conditional routing, the reason
   this is a graph rather than a sequence of calls.
7. **Flip `LLM_PROVIDER` and re-run** — same contracts across OpenAI, Gemini and Groq.

Then the audit log and the observability tab, briefly.

## Worth saying out loud

The measured decisions are the strongest material here, because they are what separates this from a
spec followed accurately:

- **`CONFIDENCE_THRESHOLD` is 0.6, not the spec's 0.3.** At 0.3 nothing in 771 real segments would
  ever have tripped it — the flag would have been dead code.
- **The 1.2 s diarization gap fires on 3% of boundaries in a fast call and 38% in a slow one.**
  A 12x spread across ten calls, which is why content patterns rank above it.
- **`assist you` scores 14 hits where `how can I help` scores 1**, because Whisper mangles the help
  variants. Patterns have to match what the ASR emits, not what the agent said.
- **`type="filepath"` over the spec's `type="numpy"`**, which deleted a soundfile conversion and a
  temp-file write from the pipeline.

## Known rough edges, if asked

Named in the README rather than hidden: spoken-word digits escape a digit regex; repeated digits
defeat ASR counting; only structured PII is redacted, so names and addresses still reach the LLM;
the 50 MB cap and the 60-minute cap are in tension at ordinary mp3 bitrates; and five of the ten
reference calls open with a narrator, so the first-speaker-is-the-agent assumption mislabels them.
