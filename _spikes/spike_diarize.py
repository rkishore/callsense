"""SPIKE — throwaway. Does the heuristic diarizer have enough signal to work?

Three questions:
  1. Is the 1.2s gap threshold real? What fraction of gaps clear it?
  2. What does an actual turn boundary look like — does the gap discriminate?
  3. What agent/customer phrasing recurs across the samples?

Transcribing all ten takes ~45s, so results are cached to JSON after the first
run. Delete the cache file to re-transcribe.

Run:  .venv/bin/python <this file>
"""

import json
import statistics
import sys
from pathlib import Path

# Spikes live one directory below the repo root, and are run from anywhere:
#   .venv/bin/python _spikes/<this file>
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.agents.transcription import _get_whisper_model  # noqa: E402

SAMPLES = REPO / "data" / "samples"
CACHE = Path(__file__).parent / "diarize_spike_cache.json"

GAP_THRESHOLD = 1.2  # the value M3 hands you — this spike is here to check it


def transcribe_all() -> dict[str, list[dict]]:
    """Transcribe every sample, returning {filename: [segment dicts]}."""
    model = _get_whisper_model("tiny")
    out = {}
    for path in sorted(SAMPLES.glob("*.mp3")):
        segments, _info = model.transcribe(
            str(path),
            beam_size=1,
            language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        out[path.name] = [
            {"text": s.text, "start": float(s.start), "end": float(s.end)} for s in segments
        ]
        print(f"  {path.name}: {len(out[path.name])} segments")
    return out


def load_or_transcribe() -> dict[str, list[dict]]:
    """Read the cached transcripts, or build them on first run."""
    if CACHE.exists():
        print(f"using cache: {CACHE.name}")
        return json.loads(CACHE.read_text())
    print("transcribing (first run, ~45s)...")
    data = transcribe_all()
    CACHE.write_text(json.dumps(data))
    return data


def gaps_for(segments: list[dict]) -> list[float]:
    """Silence between each segment and the one before it."""
    return [segments[i]["start"] - segments[i - 1]["end"] for i in range(1, len(segments))]


if __name__ == "__main__":
    transcripts = load_or_transcribe()
    print(f"\n{len(transcripts)} samples, {sum(len(s) for s in transcripts.values())} segments\n")

    # ── Q1: is 1.2s a meaningful threshold? ──────────────────────────────
    # TODO: collect gaps across every sample into one list.
    #       Print min / mean / max, a few percentiles, and what fraction
    #       exceed GAP_THRESHOLD.
    #       If almost none clear it, the gap signal barely fires. If most do,
    #       it will flip speakers mid-turn. Either changes the design.

    silence_gaps = []
    for segments in transcripts.values():
        silence_gaps.extend(gaps_for(segments))

    percentiles = statistics.quantiles(silence_gaps, n=100)
    mean_silence_gap = sum(silence_gaps) / len(silence_gaps)
    stddev_silence_gap = statistics.stdev(silence_gaps)
    print(
        f"Mean gap: {mean_silence_gap:.3f}, stddev: {stddev_silence_gap:.3f}, min: {min(silence_gaps)}, max: {max(silence_gaps)}"
    )
    print(
        f"Median gap: {percentiles[49]:.3f}, 25th percentile: {percentiles[24]:.3f} 75th percentile: {percentiles[74]:.3f}"
    )
    print(
        f"Number of gaps exceeding GAP_THRESHOLD: {sum(1 for gap in silence_gaps if gap > GAP_THRESHOLD)} out of total: {len(silence_gaps)}"
    )

    # ── Q2: what does a real turn boundary look like? ────────────────────
    # TODO: for sample_01.mp3, print each segment with the gap before it and
    #       its text. You already know segment 1 -> 2 is a genuine speaker
    #       change ("Thank you for calling Nissan" -> "Yeah, my name is John
    #       Smith"). Compare that gap against gaps *within* one speaker's turn.
    #       This is the check no summary statistic will give you.
    first_sample_segments = transcripts["sample_01.mp3"]
    first_sample_gaps = gaps_for(first_sample_segments)
    prev_end = -1
    for idx, s in enumerate(first_sample_segments):
        if prev_end == -1:
            print(
                f"start: {s['start']}, end: {s['end']} length: {s['end'] - s['start']} gap: {-1} text: {s['text']}"
            )
        else:
            print(
                f"start: {s['start']}, end: {s['end']} length: {s['end'] - s['start']} gap: {s['start'] - prev_end} text: {s['text']}"
            )
        prev_end = s["end"]

    # ── Q3: what phrasing recurs? ────────────────────────────────────────
    # TODO: print the first two segments of every sample — the opening
    #       exchange is where agent greetings live.
    # TODO: count how often candidate phrases appear across all transcripts,
    #       e.g. "thank you for calling", "how can I help", "my name is",
    #       "I'm calling about", "I need", "can you".
    #       These become _AGENT_PATTERNS and _CUSTOMER_PATTERNS — built from
    #       evidence rather than imagination.
    print("\n*********** A3: opening exchanges ***************")
    for name, segments in transcripts.items():
        print(f"\n{name}")
        for s in segments[:2]:
            print(f"  [{s['start']:6.2f}-{s['end']:6.2f}] {s['text'].strip()}")

    # Candidate patterns, guessed before looking. The counting below is what
    # decides which survive into _AGENT_PATTERNS / _CUSTOMER_PATTERNS.
    CANDIDATES = {
        "agent": [
            "thank you for calling",
            "how can i help",
            "how may i help",
            "my name is",
            "speaking",
            "let me check",
            "let me look",
            "i can help",
            "is there anything else",
            "for security",
            "can you verify",
            "may i have",
            "i apologize",
            "bear with me",
            "one moment",
            "have a great day",
        ],
        "customer": [
            "i'm calling about",
            "i am calling about",
            "i need",
            "i want",
            "can you",
            "could you",
            "my account",
            "i have a problem",
            "i was charged",
            "thank you so much",
            "yes",
            "okay",
            "that's right",
            "correct",
        ],
    }

    print("\n*********** A3: phrase counts ***************")
    print(f"{'phrase':26} {'role':9} {'hits':>5} {'files':>6} {'pos':>6}")
    print("-" * 56)
    for role, phrases in CANDIDATES.items():
        for phrase in phrases:
            hits = 0
            files = 0
            positions = []
            for segments in transcripts.values():
                in_this_file = 0
                for idx, s in enumerate(segments):
                    if phrase in s["text"].lower():
                        in_this_file += 1
                        # Where in the call, as a fraction — greetings cluster
                        # near 0.0, sign-offs near 1.0, filler spreads evenly.
                        positions.append(idx / max(1, len(segments) - 1))
                if in_this_file:
                    hits += in_this_file
                    files += 1
            pos = f"{statistics.median(positions):.2f}" if positions else "-"
            print(f"{phrase:26} {role:9} {hits:5} {files:6} {pos:>6}")
