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


def _compliance_flags(include_critical_severity: bool) -> ComplianceFlag:
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
                violation_description="high issue",
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
    intake_result = _intake(True)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "transcribe"


def test_invalid_audio_routes_to_error():
    intake_result = _intake(False)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "error"


def test_after_transcription_always_routes_to_summarize():
    intake_result = _intake(True)
    state = PipelineState(intake=intake_result, transcription=_transcript())
    assert route_after_transcription(state) == "summarize"


def test_low_severity_flag_routes_to_report():
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
