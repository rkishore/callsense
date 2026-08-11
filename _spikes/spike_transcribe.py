"""SPIKE — throwaway. Runs the whole sample set to see how confidence and
artifacts vary across calls, rather than generalising from sample_01.
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/kishore.iyer/Code/ik/agentic-ai/callsense")

from src.agents.transcription import _get_whisper_model

SAMPLES = Path("/Users/kishore.iyer/Code/ik/agentic-ai/callsense/data/samples")
THRESHOLDS = (0.3, 0.5, 0.6)
MARKERS = (
    "[BLANK_AUDIO]",
    "thanks for watching",
    "thank you for watching",
    "(music)",
    "(applause)",
    "♪",
)


def analyse(model, path):
    """Transcribe one file and return a summary dict."""
    t = time.time()
    segments, info = model.transcribe(
        str(path),
        beam_size=1,
        language="en",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    segments = list(segments)  # transcription actually runs here
    elapsed = time.time() - t

    confs = [
        max(0.0, min(1.0, 1 + s.avg_logprob)) * 0.7 + (1 - s.no_speech_prob) * 0.3 for s in segments
    ]
    full_text = "".join(s.text for s in segments)

    # Hoisted out of the comprehensions below — both were recomputing these on
    # every iteration, which is ~100x slower and easy to miss inside a genexp.
    lowered = full_text.lower()
    words = full_text.split()

    return {
        "name": path.name,
        "audio_s": info.duration,
        "vad_s": info.duration_after_vad,
        "elapsed": elapsed,
        "segments": len(segments),
        # how many DISTINCT avg_logprob values — i.e. how many decode windows
        "windows": len({round(s.avg_logprob, 6) for s in segments}),
        "confs": confs,
        "below": {t: sum(1 for c in confs if c < t) for t in THRESHOLDS},
        "markers": {m: lowered.count(m.lower()) for m in MARKERS},
        "ellipsis": len(re.findall(r"\.{4,}", full_text)),
        "repeats": sum(
            1 for i, w in enumerate(words) if i >= 2 and w == words[i - 1] == words[i - 2]
        ),
        "chars": len(full_text),
    }


if __name__ == "__main__":
    model = _get_whisper_model("tiny")
    files = sorted(SAMPLES.glob("*.mp3"))
    print(f"{len(files)} samples\n")

    header = f"{'file':16} {'audio':>7} {'segs':>5} {'wins':>5} {'min':>6} {'mean':>6} {'max':>6}"
    header += "".join(f"{'<' + str(t):>7}" for t in THRESHOLDS) + f"{'arte':>6} {'chars':>7}"
    print(header)
    print("-" * len(header))

    results = []
    for path in files:
        r = analyse(model, path)
        results.append(r)
        artefacts = sum(r["markers"].values()) + r["ellipsis"] + r["repeats"]
        row = (
            f"{r['name']:16} {r['audio_s']:6.0f}s {r['segments']:5} {r['windows']:5} "
            f"{min(r['confs']):6.3f} {sum(r['confs']) / len(r['confs']):6.3f} "
            f"{max(r['confs']):6.3f}"
        )
        row += "".join(f"{r['below'][t]:7}" for t in THRESHOLDS)
        row += f"{artefacts:6} {r['chars']:7}"
        print(row)

    # ── aggregate ────────────────────────────────────────────────────────
    all_confs = [c for r in results for c in r["confs"]]
    total_audio = sum(r["audio_s"] for r in results)
    total_elapsed = sum(r["elapsed"] for r in results)

    print(f"\n{len(all_confs)} segments across {total_audio / 60:.1f} min of audio")
    print(f"  transcribed in {total_elapsed:.0f}s  ({total_audio / total_elapsed:.0f}x realtime)")
    print(
        f"  confidence  min={min(all_confs):.3f}  "
        f"mean={sum(all_confs) / len(all_confs):.3f}  max={max(all_confs):.3f}"
    )
    for t in THRESHOLDS:
        n = sum(1 for c in all_confs if c < t)
        print(f"  below {t}: {n}/{len(all_confs)} ({n / len(all_confs):.1%})")

    print("\n  segments per decode window (confidence resolution):")
    for r in results:
        print(f"    {r['name']:16} {r['segments']:3} segments / {r['windows']:2} windows")

    found = {m: sum(r["markers"][m] for r in results) for m in MARKERS}
    found = {m: n for m, n in found.items() if n}
    print(f"\n  artefact markers found: {found or 'none'}")
    print(f"  ellipsis runs: {sum(r['ellipsis'] for r in results)}")
    print(f"  3x repeats:    {sum(r['repeats'] for r in results)}")
