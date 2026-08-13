# Spikes

Throwaway scripts written to answer a specific question about an unfamiliar API
or an unmeasured number, before writing the real code. They are kept because
they are the evidence behind several decisions that would otherwise look
arbitrary.

Run any of them from the repo root:

```bash
.venv/bin/python _spikes/spike_transcribe.py
```

Generated caches (`*.json`) are gitignored — they rebuild in about 45 seconds.

---

## `spike_transcribe.py`

**Question:** what does faster-whisper actually return, and what do its
confidence signals look like on real call audio?

Ran all 10 reference samples — 771 segments, 55 minutes — through `tiny`.

**Findings**

- `model.transcribe()` returns a **generator**. It came back in 0.29 s having
  transcribed nothing; the inference happens while iterating. Hence the
  `list(segments)` in `run_transcription`, with a comment saying so.
- Segment attributes: `text`, `start`, `end`, `avg_logprob`, `no_speech_prob`,
  `id`, `seek`, `temperature`, `compression_ratio`, `tokens`, `words`.
  `start`/`end` arrive as `np.float64`, not native floats.
- **Confidence distribution: min 0.412, mean 0.750, max 0.924.**

| threshold | segments below |
|---|---|
| 0.3 | 0 / 771 (0.0%) |
| 0.5 | 12 / 771 (1.6%) |
| 0.6 | 54 / 771 (7.0%) |

  This set `CONFIDENCE_THRESHOLD=0.6`. At 0.3 the low-confidence flag could
  never fire — dead code. At 0.6 it discriminates *between calls*: eight of ten
  samples have zero segments below it, while `sample_02` has 32 and
  `sample_10` has 22.

- `avg_logprob` and `no_speech_prob` are computed **per 30-second decode
  window, not per segment** — 771 segments share only 111 distinct values, so
  roughly 7 consecutive segments carry identical confidence. `[LOW CONF]`
  markers will therefore appear in blocks rather than singly.
- **Artifacts are rare but real**: one "thank you for watching" and three
  three-times-repeated words across 55 minutes. The cleaner is not defensive
  theatre, but clean audio will not exercise it — those tests need constructed
  input.
- Throughput: 55 minutes of audio in 45 s on CPU, ~74× realtime.

## `spike_gradio.py`

**Question:** what does `gr.Audio` actually hand a callback, and does the
`.click().then().then()` chain render its intermediate state?

Thirty minutes, run before building any real UI, to find out whether Gradio
would surprise us during M6.

**Findings**

- **`type="filepath"` beats the spec's `type="numpy"`, decisively.** Uploading
  `sample_01.mp3` through a filepath component returns the **original file,
  untouched** — `b'ID3\x04\x00...'`, 1,769,445 bytes, original filename
  preserved in the temp path. So `Path(p).read_bytes()` is exactly the `bytes`
  that `AudioInput.audio_data` wants, and `detect_audio_format` sees `ID3` and
  returns `mp3`. Magic-byte validation stays reachable from the UI.

- The numpy form of the same file is `(48000, ndarray(5889324, 2), int16)` —
  needing int16→float32, stereo→mono and 48k→16k conversion, **and then
  re-encoding to bytes anyway**, because `AudioInput` takes bytes and the
  validator wants magic bytes. Decoding a file only to rebuild a worse copy of
  it. faster-whisper decodes the file itself regardless.

- **This deletes a step from M6.** The milestone says `process_call()` must
  "handle the Gradio numpy tuple by writing a temp WAV via soundfile". With
  `filepath` there is no tuple, no soundfile call and no temp WAV.

- **Microphone recordings arrive the same way** — `sources=["microphone"]` with
  `type="filepath"` yields a temp `audio.wav` whose first bytes are
  `RIFF....WAVE`, which the existing validator already handles. No special case.
  (Note the filename is always `audio.wav`, so `AudioInput.filename` carries no
  information for mic input.)

- **`.click().then().then()` renders its intermediate state as documented** —
  the status Markdown appears on click and stays up for the duration of the
  second callback. M6's progressive UI works as planned; a single callback
  cannot do this, since it returns only once.

- **Incidental:** the sample is ~115 kbps, so a 60-minute call would be ~52 MB
  — above `MAX_FILE_SIZE_BYTES` (50 MB). `MAX_DURATION_SECONDS = 3600` and the
  size cap are in tension for ordinary mp3. Worth a line in the README's
  limitations.

## `spike_diarize.py`

**Question:** do timing gaps and content patterns carry enough signal for a
heuristic Agent/Customer diarizer, and is the 1.2 s gap threshold from the
spec real?

Caches transcripts to `diarize_spike_cache.json` so the analysis can be
iterated without re-transcribing.

**Findings**

- **The 1.2 s gap threshold fires on 16% of boundaries overall — but that
  aggregate describes no individual call.** Per-sample the rate ranges from
  **3% to 38%**, a 12x spread:

  | sample | >1.2 s | | sample | >1.2 s |
  |---|---|---|---|---|
  | 05 | 3% | | 04 | 15% |
  | 01 | 6% | | 06 | 15% |
  | 07 | 10% | | 08 | 21% |
  | 09 | 10% | | 03 | 33% |
  | 02 | 13% | | 10 | 38% |

  Hand-labelling two samples confirmed the cause is conversational pace, not
  noise. On `sample_03` (slow, formal bank call) the gap caught 8 of 12 speaker
  changes — 67% recall, 80% precision. On `sample_01` (fast Nissan sales call)
  it caught **1 of 8**, with change gaps 0.26–1.24 s and non-change gaps
  0.00–1.24 s: completely overlapping distributions.

  The gap signal is therefore **inconsistent, not weak**. This is why the spec
  ranks content patterns above it.

- **Every sample has a minimum gap of 0.00 s**, and a 0.00 gap occurred on a
  genuine speaker change. Whisper's 30-second decode windows also split
  mid-sentence at 0.00. Zero carries no information in either direction.

- **Content patterns match only 11% of segments** (6.4% agent, 4.6% customer,
  0% ambiguous, **89% no match**). They are *anchors*, not labels — the
  remaining 89% has to come from propagating the current speaker forward. This
  is the finding that justifies the spec's priority-ordered signal list.

- **`thank you for calling` is the strongest pattern in the corpus** — 15 hits
  across **10/10 samples**, median position 0.03 into the call.

- **Patterns must match what Whisper outputs, not what was said.** `assist you`
  scores 14 hits / 6 files; `how can i help` scores **1 / 1**, because Whisper
  mangles it ("How can you help you today?", "Many must surely help me as this
  year today"). Seven of thirty imagined candidates returned zero hits at all,
  including `i'm calling about` and `i was charged`.

- **`my name is` is a false friend** — 13 hits across 8 files, used by *both*
  speakers ("My name is Warren" / "Yeah, my name is John Smith"). High
  frequency, zero discrimination. Excluded deliberately.

- `yes` (20) and `okay` (36) are frequent but non-discriminating, and `yes`
  matches inside "yesterday" — patterns need `\b` boundaries, hence compiled
  regex rather than substring `in`.

- **Five of ten samples open with a narrator**, not an agent ("Call Center,
  handling rude customers, role play"). These are training recordings, so this
  is corpus-specific — but it means "segment 0 is the Agent" mislabels half
  this set. Anchoring on `thank you for calling` wherever it occurs avoids it.
