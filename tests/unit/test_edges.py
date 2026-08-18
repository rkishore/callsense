import uuid

from src.graph.edges import route_after_intake, route_after_qa, route_after_transcription
from src.graph.state import (
    AudioProperties,
    ComplianceFlag,
    IntakeResult,
    PIIScanResult,
    PipelineState,
    QADimensionScore,
    QAScoreResult,
    SeverityLevel,
    TranscriptionResult,
    TranscriptionSegment,
)

CALL_ID = uuid.uuid4()


def _intake(validation_passed: bool) -> IntakeResult:
    return IntakeResult(
        call_id=CALL_ID,
        validation_passed=validation_passed,
        pii_scan=PIIScanResult(pii_detected=False, pii_count=0, pii_types=[]),
        audio_properties=AudioProperties(
            duration_seconds=60.0, size_bytes=1024000, sample_rate=16000, channels=2, format="mp3"
        ),
    )


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


def _qa_scores(flags: list[ComplianceFlag], **scores: int) -> QAScoreResult:
    return QAScoreResult(
        call_id=uuid.uuid4(),
        **{
            name: QADimensionScore(score=score, justification="Because.")
            for name, score in scores.items()
        },
        overall_score=3.0,
        compliance_flags=flags,
    )


def _compliance_flags(include_critical_severity: bool) -> list[ComplianceFlag]:
    """Two flags, the second of which is critical or not.

    Always returns more than one, and puts the varying flag *second*, so the
    critical case proves route_after_qa scans the whole list rather than reading
    flags[0].
    """
    compliance_flags = [
        ComplianceFlag(
            violation_description="high issue",
            severity=SeverityLevel.HIGH,
            transcript_timestamp=25.0,
        )
    ]
    if include_critical_severity:
        compliance_flags.append(
            ComplianceFlag(
                violation_description="critical issue",
                severity=SeverityLevel.CRITICAL,
                transcript_timestamp=35.0,
            )
        )
    else:
        compliance_flags.append(
            ComplianceFlag(
                violation_description="low issue",
                severity=SeverityLevel.LOW,
                transcript_timestamp=65.0,
            )
        )

    return compliance_flags


def test_validated_audio_routes_to_transcription():
    """The ordinary path: intake passed, so transcribe."""
    intake_result = _intake(True)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "transcribe"


def test_invalid_audio_routes_to_error():
    """The first of two places the pipeline can end early.

    Intake has already checked magic bytes, size and duration, so a failure
    means the upload was never analysable — there is nothing to degrade into.
    """
    intake_result = _intake(False)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "error"


def test_after_transcription_always_routes_to_summarize():
    """Unconditional, and tested anyway.

    One outcome today, but the router exists so a future condition — a
    low-confidence halt, say — has somewhere to live without rewiring the graph.
    A test here means adding that condition breaks something visible.
    """
    intake_result = _intake(True)
    state = PipelineState(intake=intake_result, transcription=_transcript())
    assert route_after_transcription(state) == "summarize"


def test_low_severity_flag_routes_to_report():
    """Severity below critical is scored, not escalated.

    The half of the pair that stops a router hardcoded to "supervisor_review"
    from passing. Deliberately includes a HIGH flag rather than only a LOW one:
    that proves *only* critical escalates, not merely that some severities do
    not.
    """
    qa_scores = _qa_scores(
        _compliance_flags(False),
        professionalism=5,
        empathy=5,
        problem_resolution=5,
        compliance=5,
        communication_clarity=5,
    )
    state = PipelineState(qa_scores=qa_scores)
    assert route_after_qa(state) == "report"


def test_critical_severity_flag_routes_to_supervisor():
    """One critical flag diverts the call to human review.

    The decision the graph exists for, and the sixth beat of the demo. Note the
    dimension scores are all 5s — a call scoring perfectly still goes to a
    supervisor, because severity is never averaged into the score and cannot be
    outweighed by good handling elsewhere.

    The other half of the pair: a router hardcoded to "report" passes the test
    above and fails this one. Neither proves the rule alone.
    """
    qa_scores = _qa_scores(
        _compliance_flags(True),
        professionalism=5,
        empathy=5,
        problem_resolution=5,
        compliance=5,
        communication_clarity=5,
    )
    state = PipelineState(qa_scores=qa_scores)
    assert route_after_qa(state) == "supervisor_review"
