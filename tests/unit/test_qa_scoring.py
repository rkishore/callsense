"""
Unit tests for QA scoring in src/agents/qa_scoring.py.

The first test here is the one the milestone names in its deliverable: the
deterministic recomputation must be explicitly verified.
"""

import uuid
from unittest import mock

import pytest

from src.agents import qa_scoring
from src.graph.state import (
    QADimensionScore,
    QAScoreResult,
    ResolutionStatus,
    SummaryResult,
    TranscriptionResult,
    TranscriptionSegment,
)
from src.utils.config import Config

CALL_ID = uuid.uuid4()


def _config() -> Config:
    """Injected so the tests never read .env or need a real key."""
    return Config(llm_provider="openai", openai_api_key="sk-test", max_retries_per_node=3)


def _transcript() -> TranscriptionResult:
    return TranscriptionResult(
        call_id=CALL_ID,
        full_text="Thanks for calling.",
        segments=[
            TranscriptionSegment(
                text="Thanks for calling.", start_time=0.0, end_time=2.0, confidence=0.9
            )
        ],
        low_confidence_ratio=0.0,
        flagged_low_confidence=False,
    )


def _summary() -> SummaryResult:
    return SummaryResult(
        call_id=CALL_ID,
        call_purpose="Dispute a charge.",
        key_discussion_points=[],
        action_items=[],
        resolution_status=ResolutionStatus.RESOLVED,
        sentiment_trajectory="Concerned -> Reassured",
        entities=[],
    )


def _llm_response(overall_score: float, **scores: int) -> QAScoreResult:
    """A QAScoreResult as the LLM would return it.

    call_id is deliberately a fresh UUID rather than CALL_ID: the model invents
    one because the field is required, and the provider spike caught OpenAI
    returning 12345678-abcd-ef01-2345-6789abcdef01. Every test here therefore
    also checks that it gets overwritten.
    """
    return QAScoreResult(
        call_id=uuid.uuid4(),
        **{
            name: QADimensionScore(score=score, justification="Because.")
            for name, score in scores.items()
        },
        overall_score=overall_score,
        compliance_flags=[],
    )


def _run(llm_response: QAScoreResult) -> QAScoreResult:
    """Run the scorer against a canned LLM response.

    The mock chain mirrors the call: get_llm(...).with_structured_output(...)
    .invoke(...). Patched at src.agents.qa_scoring.get_llm — the name as
    imported into that module, not where it is defined.
    """
    with mock.patch("src.agents.qa_scoring.get_llm") as mock_get_llm:
        mock_get_llm.return_value.with_structured_output.return_value.invoke.return_value = (
            llm_response
        )
        return qa_scoring.run_qa_scoring(_transcript(), _summary(), _config())


def test_overall_score_is_recomputed_not_taken_from_the_llm():
    """The milestone's named deliverable.

    The LLM claims 3.0 while scoring every dimension 5/5. Python must return
    5.0 — the model's figure is discarded, not reconciled or averaged.

    Asserting it differs from the LLM's proposal matters as much as the value
    itself: a test that only checked `== 5.0` would pass on an implementation
    that trusted the model, in any case where the model happened to be right.
    """
    llm_said = _llm_response(
        overall_score=3.0,
        professionalism=5,
        empathy=5,
        problem_resolution=5,
        compliance=5,
        communication_clarity=5,
    )

    result = _run(llm_said)

    assert result.overall_score == 5.0
    assert result.overall_score != llm_said.overall_score


def test_each_dimension_carries_its_own_weight():
    """All-fives cannot catch a transposed weight; distinct scores can.

    Any set of weights summing to 1.0 turns five 5s into 5.0, so the test above
    would pass with professionalism at 30% and problem_resolution at 15%. Here
    every dimension has a different score, so swapping any two weights changes
    the answer.

        1x0.15 + 2x0.20 + 3x0.30 + 4x0.20 + 5x0.15 = 3.0
    """
    result = _run(
        _llm_response(
            overall_score=1.0,
            professionalism=1,
            empathy=2,
            problem_resolution=3,
            compliance=4,
            communication_clarity=5,
        )
    )

    assert result.overall_score == 3.0


def test_the_models_call_id_is_replaced_with_the_transcripts():
    """The model invents a call_id because the field is required.

    A valid UUID that is complete fiction passes validation silently, so
    nothing downstream would notice a report keyed to it.
    """
    llm_said = _llm_response(
        overall_score=3.0,
        professionalism=3,
        empathy=3,
        problem_resolution=3,
        compliance=3,
        communication_clarity=3,
    )

    result = _run(llm_said)

    assert result.call_id == CALL_ID
    assert result.call_id != llm_said.call_id


def test_qa_scoring_error_after_retries_are_exhausted():
    """Every attempt fails, so the caller is told rather than given a default.

    time.sleep is patched or this test costs 1 + 2 = 3 seconds of real backoff.
    The call count assertion pins that max_retries_per_node means attempts, not
    retries-after-the-first — an off-by-one here changes how long a failing
    call takes in production.
    """
    with (
        mock.patch("src.agents.qa_scoring.get_llm") as mock_get_llm,
        mock.patch("src.agents.qa_scoring.time.sleep") as mock_sleep,
    ):
        invoke = mock_get_llm.return_value.with_structured_output.return_value.invoke
        invoke.side_effect = RuntimeError("provider exploded")

        with pytest.raises(qa_scoring.QAScoringError, match="after 3"):
            qa_scoring.run_qa_scoring(_transcript(), _summary(), _config())

    assert invoke.call_count == 3
    # Slept between attempts, but not after the final failure — sleeping there
    # costs the caller time to be told something already known.
    assert mock_sleep.call_count == 2
