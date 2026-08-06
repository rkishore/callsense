"""Unit tests for the pipeline data contracts in src/graph/state.py."""

import uuid

import pytest
from pydantic import ValidationError

from src.graph.state import (
    AudioInput,
    QADimensionScore,
    QAScoreResult,
    TranscriptionResult,
    TranscriptionSegment,
)


@pytest.mark.parametrize(
    ("model", "valid_kwargs"),
    [
        # One contract we populate ourselves, one the LLM populates — extra="forbid"
        # guards different mistakes in each: our typo'd keyword vs provider drift.
        pytest.param(
            AudioInput,
            {"audio_data": b"\x00" * 16, "filename": "call.wav"},
            id="written-by-us",
        ),
        pytest.param(
            QADimensionScore,
            {"score": 3, "justification": "at 02:15 the agent verified identity"},
            id="written-by-the-llm",
        ),
    ],
)
def test_unknown_fields_are_rejected(model, valid_kwargs):
    """Without extra='forbid', Pydantic silently discards unknown keys.

    That bit twice during development: AudioInput.audio_data and
    QADimensionScore.justification were both accepted and dropped, so the
    constructor appeared to work while the data vanished.
    """
    model(**valid_kwargs)  # the valid form still constructs

    with pytest.raises(ValidationError, match="extra_forbidden"):
        model(**valid_kwargs, unexpected_field=1)


@pytest.mark.parametrize(
    ("model", "invalid_kwargs"),
    [
        pytest.param(
            QADimensionScore,
            {"score": 0, "justification": "the agent was very helpful"},
            id="raise-validation-error-on-score-out-of-range",
        ),
        pytest.param(
            TranscriptionSegment,
            {"text": "Hello, world!", "start_time": 0.0, "end_time": 5.0, "confidence": 1.5},
            id="raise-validation-error-on-confidence-out-of-range",
        ),
    ],
)
def test_validation_errors(model, invalid_kwargs):
    """Test that validation errors are raised for invalid data."""
    with pytest.raises(ValidationError, match="less_than_equal|greater_than_equal"):
        model(**invalid_kwargs)


@pytest.mark.parametrize(
    "bad_overall_score",
    [
        pytest.param(0.5, id="below-range"),
        pytest.param(5.5, id="above-range"),
    ],
)
def test_overall_score_out_of_range_is_rejected(bad_overall_score):
    """The third of M2's declared constraints: overall_score must be 1.0-5.0.

    Written out in full rather than via a builder — the five dimension objects
    are noise here, but there is only one other call site so far. Worth
    extracting once M4 needs a valid QAScoreResult for the recompute test.
    """
    with pytest.raises(ValidationError, match="less_than_equal|greater_than_equal"):
        QAScoreResult(
            call_id=uuid.uuid4(),
            professionalism=QADimensionScore(score=3, justification="at 02:15"),
            empathy=QADimensionScore(score=3, justification="at 02:15"),
            problem_resolution=QADimensionScore(score=3, justification="at 02:15"),
            compliance=QADimensionScore(score=3, justification="at 02:15"),
            communication_clarity=QADimensionScore(score=3, justification="at 02:15"),
            overall_score=bad_overall_score,
            compliance_flags=[],
        )


@pytest.mark.parametrize(
    "bad_confidence_ratio",
    [
        pytest.param(-0.1, id="below-range"),
        pytest.param(1.1, id="above-range"),
    ],
)
def test_low_confidence_ratio_out_of_range_is_rejected(bad_confidence_ratio):
    """Adding to M2's constraints: confidence_ratio must be 0.0-1.0."""
    with pytest.raises(ValidationError, match="less_than_equal|greater_than_equal"):
        TranscriptionResult(
            call_id=uuid.uuid4(),
            full_text="at 03:15 and on a train",
            segments=[
                TranscriptionSegment(
                    text="Hello, world!", start_time=0.0, end_time=5.0, confidence=0.5
                ),
            ],
            low_confidence_ratio=bad_confidence_ratio,
        )
