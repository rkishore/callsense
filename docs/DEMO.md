# Demo runbook

Everything needed to record the walkthrough, written while the reasons were still fresh rather than
reconstructed on the day.

## Before recording

**Run `make demo`, not `make run`.** It sets `WHISPER_MODEL_SIZE=base`.

This is not cosmetic, and it is a trade rather than an upgrade. On `tiny` a dictated card number
transcribes as `4.539-8712-3456-789-0`, leaving `4.` outside the matched span and showing
`4.[REDACTED_CREDIT_CARD]` on camera. `base` renders it cleanly.

**But `base` costs you the phone number in `sample_01`.** It transcribes spoken "eight oh one" as
`A-01-431-1000` — a letter-prefixed reference code — which matches no phone pattern, where `tiny`
writes `801-431-1000` and it redacts. No single setting gets both. The card number is the stronger
beat, so `base` wins and the phone-number claim comes out of beat 2 below.

**Decide on `launch(show_error=True)`.** Right for development, probably wrong for a recording —
it surfaces internal detail in the error toast.

**Check the temp directory.** Gradio copies every upload into its own hashed temp folder, separate
from the PDFs and JSON the pipeline writes. A stale one from testing can confuse a live walkthrough.

## Audio

| File | Purpose | Notes |
|---|---|---|
| `data/samples/sample_01.mp3` | the normal path | Real service call. Its spoken phone number redacts on `tiny` but **not** on `base` — do not demo redaction from this file |
| `data/demo/card.mp3` | PII redaction | TTS. Varied digits deliberately: repeated digits defeat ASR counting, and `4111-1111-1111-1111` transcribes as 25 digits |
| `data/demo/injection.mp3` | injection blocked | TTS. Trips `ignore_previous` and `prompt_leak` — two patterns, so the report shows a list rather than a single name |
| an `.ogg`, and a 51 MB file | rejection paths | Trivially synthetic. `gr.Audio` has no format allow-list, so the validator is what rejects them |

`data/samples/` is gitignored — the reference audio is not ours to republish. `data/demo/` is
tracked, so a reviewer who clones the repo can reproduce the security beats.

## The spine

Seven beats, in this order. Each one shows something the previous cannot.

1. **`sample_01.mp3` end to end** — speaker-labelled transcript, summary, QA scorecard, PDF.
   Establishes that the ordinary path works before anything clever.
2. **The transcript, with `[LOW CONF]` markers** — real diarization, real per-segment confidence.
   Markers appear in blocks, not singly, because `avg_logprob` is computed per 30-second decode
   window. Do **not** promise phone-number redaction here: on `base` that number transcribes as
   `A-01-431-1000` and legitimately does not match. See the note above.
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

The strongest one to volunteer, because it is counterintuitive and measured: **a larger Whisper
model can reduce PII detection.** `base` renders a spoken "eight oh one" as the reference code
`A-01-431-1000`, which matches no phone pattern, where `tiny` writes `801-431-1000` and it redacts.
Redaction acts on the transcript, so the model's interpretive choices decide what is detectable —
and more capable is not the same as more literal.
