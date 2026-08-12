"""
Unit tests for diarization functions in src/agents/diarization.py.
"""

from src.agents.diarization import SpeakerDiarizer, Utterance
from src.graph.state import SpeakerRole


def _seg(text="hello", start=0.0, end=1.0):
    """Build one Utterance, defaulting everything a test does not care about.

    The default text is deliberately pattern-free — "hello" matches nothing in
    _AGENT_PATTERNS or _CUSTOMER_PATTERNS. Several tests below assert the
    *fallback* behaviour and would silently start passing for the wrong reason
    if this default were made more realistic.

    Timings default to a single second at the origin. Only the gap tests care,
    and they pass start/end explicitly so that timing appears in a test exactly
    when it is the thing under test.
    """
    return Utterance(text=text, start=start, end=end)


# ── Group 1: structural ──────────────────────────────────────────────────────
# These pin the shape of the output — length, type, default, empty input — and
# deliberately say nothing about whether the labels are correct. A diarizer that
# labels everything Agent passes all four. Group 2 is what breaks it.


def test_empty_segments_returns_empty():
    """VAD can filter a near-silent file down to no segments at all."""
    diarizer = SpeakerDiarizer()
    assert diarizer.assign([]) == []


def test_output_length_matches_input():
    """One label per segment.

    Five rather than two, so a loop that drops the first or last element is
    unambiguous — the likely bug, since gaps are computed over
    range(1, len(segments)) and that off-by-one can leak into the label loop.
    """
    segments = [_seg() for _ in range(5)]
    diarizer = SpeakerDiarizer()
    assert len(diarizer.assign(segments)) == len(segments)


def test_every_label_is_agent_or_customer():
    """Never None, never a third value.

    Uses pattern-free text: the fallback path is where a stray None would come
    from, since every other path assigns a role explicitly.
    """
    segments = [_seg() for _ in range(5)]
    diarizer = SpeakerDiarizer()
    labels = diarizer.assign(segments)
    assert all(label in (SpeakerRole.AGENT, SpeakerRole.CUSTOMER) for label in labels)


def test_single_segment_is_agent():
    """The documented default, and the seed every propagation test builds on.

    Group 3 reads as "first segment is Agent, then something does or does not
    flip it" — if this default were Customer, every expectation there inverts.
    """
    segments = [_seg()]
    diarizer = SpeakerDiarizer()
    result = diarizer.assign(segments)
    assert result == [SpeakerRole.AGENT]
