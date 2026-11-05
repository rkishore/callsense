"""
Unit tests for evals/public/fetch.py: how a Harper Valley call becomes the
text scan_text sees.
"""

import json
import zipfile

from evals.public import fetch
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


def test_aixblock_sample_drops_duplicates_and_empty_calls_and_caps_each_domain(
    tmp_path, monkeypatch
):
    # Two zips of one domain, the second repeating a call from the first, as
    # AIxBlock's re-uploads do. An empty call and a macOS sidecar are skipped.
    monkeypatch.setattr(fetch, "AIXBLOCK_ZIPS", {"a.zip": "auto", "b.zip": "auto"})
    monkeypatch.setattr(fetch, "AIXBLOCK_PER_DOMAIN", 2)
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        for i, text in enumerate(["one", "two", "three", ""]):
            zf.writestr(f"a/call_555123456{i}.json", json.dumps({"text": text}))
        zf.writestr("__MACOSX/a/._call_5551234560.json", "not json")
    with zipfile.ZipFile(tmp_path / "b.zip", "w") as zf:
        zf.writestr("b/copy.json", json.dumps({"text": "one"}))

    sample, sizes = fetch.aixblock_calls(tmp_path)

    assert sizes == {"auto": 3}
    assert len(sample) == 2
    # Ids are hashes: the phone-like file names never leave data/.
    assert all("555" not in call["id"] and len(call["id"]) == 16 for call in sample)
    assert sample == sorted(sample, key=lambda c: c["id"])
