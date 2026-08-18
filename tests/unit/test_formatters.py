"""
Unit tests for display formatting in src/utils/formatters.py.

Deliberately small. These are display functions with no logic, so the tests
cover only what has already gone wrong once: timestamp edge cases, the weights
that silently corrupt every score if they drift, and the empty-collection paths
where a Pydantic repr or a None leaked onto the screen.
"""

import uuid

import pytest

from src.graph.state import (
    QA_DIMENSIONS,
    ActionItem,
    ComplianceFlag,
    Entity,
    QADimensionScore,
    QAScoreResult,
    ResolutionStatus,
    SeverityLevel,
    SummaryResult,
)
from src.utils.formatters import format_qa, format_summary, secs_to_mmss


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        pytest.param(0, "00:00", id="zero"),
        pytest.param(59.9, "00:59", id="truncates-rather-than-rounds"),
        pytest.param(60, "01:00", id="minute-boundary"),
        # A 3600-second call is legal, so minutes exceed 59 rather than rolling
        # into hours. Looks like an overflow bug and is not.
        pytest.param(3600, "60:00", id="hour-does-not-roll-over"),
        # Python floors toward negative infinity, so without the clamp this
        # would render "-2:00" — further from zero than the input.
        pytest.param(-75, "00:00", id="negative-clamps-to-zero"),
    ],
)
def test_secs_to_mmss(seconds, expected):
    assert secs_to_mmss(seconds) == expected


def test_dimension_weights_sum_to_one():
    """The highest-value assertion in this file.

    A weight typed as 0.15 twice and 0.25 once produces plausible-looking scores
    that are quietly on the wrong scale, forever, with nothing else to catch it.

    approx rather than ==: the five weights sum to 0.9999999999999999 in
    floating point, so the obvious assertion fails.
    """
    assert sum(spec.weight for spec in QA_DIMENSIONS.values()) == pytest.approx(1.0)


def _sparse_summary() -> SummaryResult:
    return SummaryResult(
        call_id=uuid.uuid4(),
        call_purpose="Dispute a charge.",
        key_discussion_points=[],
        action_items=[ActionItem(description="Reverse the charge", owner=None, deadline=None)],
        resolution_status=ResolutionStatus.ESCALATED,
        sentiment_trajectory="Concerned -> Reassured",
        entities=[Entity(name="Metro Bank", type="organization")],
    )


def _qa(flags: list[ComplianceFlag]) -> QAScoreResult:
    dim = QADimensionScore(score=3, justification="Adequate.")
    return QAScoreResult(
        call_id=uuid.uuid4(),
        professionalism=dim,
        empathy=dim,
        problem_resolution=dim,
        compliance=dim,
        communication_clarity=dim,
        overall_score=3.0,
        compliance_flags=flags,
    )


def test_summary_renders_optional_fields_without_leaking_reprs():
    """The bug this exists for: interpolating a model put "deadline=None" on screen.

    An action item with neither owner nor deadline must render as its
    description alone, and an empty list must produce a message rather than a
    bare heading.
    """
    out = format_summary(_sparse_summary())

    assert "Reverse the charge" in out
    # A Pydantic repr would put "description=... owner=None deadline=None" on
    # screen. Checking for the field-name markers is precise; checking for the
    # bare word "None" is not, since "_None identified._" is a legitimate value.
    assert "description=" not in out
    assert "owner=" not in out
    assert "deadline=" not in out
    assert "_None identified._" in out  # empty key_discussion_points
    assert "Escalated" in out  # StrEnum title-cased for display


def test_qa_renders_flags_and_the_empty_case():
    """Covers both compliance-flag paths, and the MM:SS conversion.

    147.3 seconds must reach the screen as 02:27 — a reviewer can scrub to that;
    they cannot scrub to 147.3.
    """
    flagged = format_qa(
        _qa(
            [
                ComplianceFlag(
                    violation_description="Action taken without verification.",
                    severity=SeverityLevel.CRITICAL,
                    transcript_timestamp=147.3,
                )
            ]
        )
    )
    assert "02:27" in flagged
    assert "147.3" not in flagged
    assert "🔴" in flagged

    empty = format_qa(_qa([]))
    assert "_No compliance issues identified._" in empty

    # Every dimension label and weight reaches the output, so a dimension added
    # to QA_DIMENSIONS cannot be silently missing from the scorecard.
    for spec in QA_DIMENSIONS.values():
        assert spec.label in flagged
        assert f"{spec.weight:.0%}" in flagged
