"""Unit tests for the pipeline data contracts in src/graph/state.py."""

import pytest
from pydantic import ValidationError

from src.graph.state import AudioInput, QADimensionScore, TranscriptionSegment


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
