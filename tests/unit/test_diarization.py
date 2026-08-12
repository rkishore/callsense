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
AGENT_GREETING = "Thank you for calling Nissan. My name is Warren. Can I have your name?"


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
        pytest.param(AGENT_GREETING, id="thank-you-for-calling"),
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


# ── Group 3: gap propagation ─────────────────────────────────────────────────
# All text here stays pattern-free, or content anchoring fires and the gap is
# never consulted. Gaps are built by the helpers below rather than by hand:
# a gap is start[i] - end[i-1], and writing those numbers directly is easy to
# get backwards, which yields a test that passes and verifies nothing.


def _with_gaps(*gaps):
    """Pattern-free segments separated by exactly the given gaps.

    Segments are zero-duration and placed at the cumulative sum of the gaps, so
    each gap is a subtraction of two nearby values rather than of a large offset.
    That matters: the diarizer's boundary row uses a gap of exactly 1.2, and
    5.0 + 1.2 - 5.0 is 1.2000000000000002 — fractionally *above* the threshold,
    which flips the speaker and makes a correct implementation look broken.

    Float arithmetic still cannot be exact for arbitrary values, so keep gaps
    here exactly representable — integers, halves, quarters — or reason about
    the subtraction before adding a row.
    """
    segments = [_seg(start=0.0, end=0.0)]
    position = 0.0
    for gap in gaps:
        position += gap
        segments.append(_seg(start=position, end=position))
    return segments


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        pytest.param(2.0, SpeakerRole.CUSTOMER, id="above-threshold-flips"),
        pytest.param(1.2, SpeakerRole.AGENT, id="exactly-at-threshold-does-not-flip"),
        pytest.param(0.3, SpeakerRole.AGENT, id="small-gap-keeps-speaker"),
        pytest.param(0.0, SpeakerRole.AGENT, id="zero-gap-does-not-flip"),
    ],
)
def test_gap_returns_expected_role(gap, expected):
    """A gap flips the speaker only when it is strictly above the threshold.

    The 1.2 row is the > versus >= boundary — the likeliest single bug here.
    The 0.0 row encodes a spike finding: Whisper's 30-second decode windows
    split mid-sentence at 0.00, and a 0.00 gap also occurred on a genuine
    speaker change, so zero must stay inert in both directions.
    """
    diarizer = SpeakerDiarizer()
    assert diarizer.assign(_with_gaps(gap)) == [SpeakerRole.AGENT, expected]


def test_consecutive_gaps_alternate_speakers():
    """The gap toggles the speaker rather than setting it to Customer.

    An implementation that assigns Customer on a gap passes every row above —
    one flip looks identical either way. It breaks only on the second flip,
    where a toggle returns to Agent and an assignment stays stuck.
    """
    diarizer = SpeakerDiarizer()
    expected = [SpeakerRole.AGENT, SpeakerRole.CUSTOMER, SpeakerRole.AGENT]
    assert diarizer.assign(_with_gaps(2.0, 3.0)) == expected


# ── Group 4: regressions from the spike ──────────────────────────────────────
# Both tests below look redundant at a glance — one asserts Customer stays
# Customer, the other that Agent stays Agent. Each is guarding a specific
# mistake the measurements caught, and neither is obvious without that context.


def test_my_name_is_does_not_force_agent():
    """ "my name is" must never become an agent pattern.

    It is the most tempting phrase in the corpus — 13 hits across 8 files, and
    it appears in the opening line of half the calls. It is also used by both
    speakers: "Thank you for calling Nissan. My name is Warren" and "Yeah, my
    name is John Smith". High frequency, zero discrimination.

    The customer seed is load-bearing. A lone "my name is" line matches no
    customer pattern and so falls through to the Agent default — asserting
    Agent-ness on it would pass whether or not the pattern existed. Seeding
    Customer first means the label can only move if something forces it.
    """
    diarizer = SpeakerDiarizer()
    result = diarizer.assign([_seg(CUSTOMER_SEED), _seg(text="Yeah, my name is John Smith.")])
    assert result == [SpeakerRole.CUSTOMER, SpeakerRole.CUSTOMER]


def test_content_pattern_beats_gap():
    """The only test pinning the priority order as a decision.

    Every other test in this file passes against a gap-first implementation,
    because no other case puts the two signals in conflict. Here they disagree:
    the current speaker is Agent, the gap of 2.0s says "flip to Customer", and
    the agent greeting says "stay Agent". Content has to win.

    The conflict is what makes it work. Seeding Customer and then supplying a
    gap plus an agent line would have both signals agreeing on Agent, and the
    test would pass whichever order the branches were written in.

    Why content ranks first: it matches ~11% of segments but is reliable when
    it does, whereas the gap fires on 3% of boundaries in a fast call and 38%
    in a slow one. The gap carries a run of unmatched segments; it does not get
    to overrule evidence.
    """
    segments = [_seg(start=0.0, end=0.0), _seg(AGENT_GREETING, start=2.0, end=3.0)]
    diarizer = SpeakerDiarizer()
    result = diarizer.assign(segments)
    assert result == [SpeakerRole.AGENT, SpeakerRole.AGENT]


def test_realistic_exchange():
    """One scripted call, exercising every mechanism together.

    The closest thing in this suite to documentation of the algorithm — each
    segment is here for a different reason:

      # text            gap  label     mechanism
      0 agent greeting   -   Agent     content anchor at position 0
      1 customer line    0.5 Customer  content flips it
      2 pattern-free     0.5 Customer  propagation — the ~89% case
      3 pattern-free     3.0 Agent     gap flip
      4 agent closer     0.5 Agent     content re-anchors, agreeing with the run
      5 customer thanks  0.5 Customer  content anchor at the close

    Segment 4 is the only one where both signals point the same way, which is
    what most of a real transcript looks like. Timings are halves and integers
    so the gap subtractions stay exact; see _with_gaps for why that matters.
    """
    segments = [
        _seg(AGENT_GREETING, start=0.0, end=2.0),
        _seg(CUSTOMER_SEED, start=2.5, end=4.0),
        _seg(start=4.5, end=5.0),
        _seg(start=8.0, end=9.0),
        _seg("Is there anything else I can help you with?", start=9.5, end=10.5),
        _seg("Oh, thank you. That's exactly what I need…", start=11.0, end=12.0),
    ]
    diarizer = SpeakerDiarizer()
    result = diarizer.assign(segments)
    assert result == [
        SpeakerRole.AGENT,
        SpeakerRole.CUSTOMER,
        SpeakerRole.CUSTOMER,
        SpeakerRole.AGENT,
        SpeakerRole.AGENT,
        SpeakerRole.CUSTOMER,
    ]
