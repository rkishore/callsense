"""
Unit tests for evals/public/fetch.py: how a Harper Valley call becomes the
text scan_text sees.
"""

import json

from evals.public.fetch import harper_valley_calls


def segment(start_ms: int, text: str, role: str) -> dict:
    return {"start_ms": start_ms, "transcript": text, "speaker_role": role, "human_transcript": "x"}


def test_a_call_is_its_machine_transcript_in_time_order_without_labels(tmp_path):
    # Stored per channel, so file order is not time order. An empty segment
    # adds no double space.
    (tmp_path / "data" / "transcript").mkdir(parents=True)
    (tmp_path / "data" / "metadata").mkdir(parents=True)
    segments = [
        segment(3000, "i lost my card", "caller"),
        segment(0, "harper valley bank", "agent"),
        segment(5000, " ", "agent"),
        segment(6000, "i can help", "agent"),
    ]
    (tmp_path / "data" / "transcript" / "a.json").write_text(json.dumps(segments))
    meta = {"sid": "a", "tasks": [{"task_type": "replace card"}]}
    (tmp_path / "data" / "metadata" / "a.json").write_text(json.dumps(meta))

    assert harper_valley_calls(tmp_path) == [
        {
            "sid": "a",
            "tasks": ["replace card"],
            "text": "harper valley bank i lost my card i can help",
        }
    ]
