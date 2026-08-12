"""
Unit tests for diarization functions in src/agents/diarization.py.
"""

import pytest

from src.agents.diarization import SpeakerDiarizer, Utterance
from src.graph.state import SpeakerRole

# These module-level constants exist to move the diarizer off its default
# Both are a SEED in one test and a case in another.
CUSTOMER_SEED = "And I want you to cancel my account completely."
AGENT_SEED = "How may I assist you today?"


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


# ── Group 2: content anchors ─────────────────────────────────────────────────
# The lines below are real Whisper output from the ten reference calls, not
# idealised text. Deliberately not parametrized over _AGENT_PATTERNS itself:
# looping over the module's own list only asserts that each pattern matches
# itself, which cannot fail and cannot catch a missing pattern.


@pytest.mark.parametrize(
    ("agent_line",),
    [
        pytest.param(
            "Thank you for calling Nissan. My name is Warren. Can I have your name?",
            id="thank-you-for-calling",
        ),
        pytest.param(
            "Thank you for calling vital care health solutions. My name is Terry. "
            "How may I assist you today?",
            id="assist-you",
        ),
    ],
)
def test_agent_patterns_label_agent(agent_line):
    """An agent phrase anchors the label, rather than it landing there by default.

    The customer seed is what gives this test teeth. Asserting [AGENT] on a lone
    agent line would also pass with every pattern deleted, since Agent is the
    fallback — you cannot observe a value being set when it is also what you get
    for free. Seeding the opposite role first makes the match observable.
    """
    segments = [_seg(CUSTOMER_SEED), _seg(agent_line)]
    diarizer = SpeakerDiarizer()
    labels = diarizer.assign(segments)
    assert labels == [SpeakerRole.CUSTOMER, SpeakerRole.AGENT]


@pytest.mark.parametrize(
    ("customer_line",),
    [
        pytest.param(CUSTOMER_SEED, id="cancel-my-account"),
        pytest.param(
            "I've started my account in Postpaid and decided to switch over pre-pay.",
            id="switch-over",
        ),
        pytest.param(
            "Oh, thank you. That's exactly what I need. Thank you so much, Candace.",
            id="what-i-need",
        ),
    ],
)
def test_customer_patterns_label_customer(customer_line):
    """The first test in the suite an all-Agent implementation cannot pass.

    Also pins that content beats the default: the customer line sits at index 1
    behind an agent seed, but a lone customer line at index 0 would be Customer
    too — a match overrides the first-segment default rather than deferring to
    it. That is why _seg()'s default text must stay pattern-free, or this test
    and test_single_segment_is_agent would contradict each other.
    """
    segments = [_seg(AGENT_SEED), _seg(customer_line)]
    diarizer = SpeakerDiarizer()
    labels = diarizer.assign(segments)
    assert labels == [SpeakerRole.AGENT, SpeakerRole.CUSTOMER]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param(
            "THANK YOU FOR CALLING NISSAN. CAN I HAVE YOUR NAME?",
            [SpeakerRole.CUSTOMER, SpeakerRole.AGENT],
            id="agent-line-upper",
        ),
        pytest.param(
            "AND I WANT YOU TO CANCEL MY ACCOUNT COMPLETELY.",
            [SpeakerRole.AGENT, SpeakerRole.CUSTOMER],
            id="customer-line-upper",
        ),
    ],
)
def test_pattern_matching_is_case_insensitive(line, expected):
    """Pins re.IGNORECASE so it cannot be dropped during a tidy-up.

    Whisper normally emits sentence case, so nothing else in this suite would
    notice its removal — every other test would keep passing.

    The seed is the opposite role to the line under test, so a match is what
    sets the second label rather than it inheriting the seed.
    """
    seed = CUSTOMER_SEED if expected[1] is SpeakerRole.AGENT else AGENT_SEED
    diarizer = SpeakerDiarizer()
    assert diarizer.assign([_seg(seed), _seg(line)]) == expected
