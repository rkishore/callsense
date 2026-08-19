import uuid

from src.graph.edges import route_after_intake, route_after_qa, route_after_transcription
from src.graph.state import (
    ComplianceFlag,
    PipelineState,
    SeverityLevel,
)
from tests.conftest import make_intake, make_qa_scores, make_transcript

CALL_ID = uuid.uuid4()


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
    intake_result = make_intake(validation_passed=True)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "transcribe"


def test_invalid_audio_routes_to_error():
    """The first of two places the pipeline can end early.

    Intake has already checked magic bytes, size and duration, so a failure
    means the upload was never analysable — there is nothing to degrade into.
    """
    intake_result = make_intake(validation_passed=False)
    state = PipelineState(intake=intake_result)
    assert route_after_intake(state) == "error"


def test_after_transcription_always_routes_to_summarize():
    """Unconditional, and tested anyway.

    One outcome today, but the router exists so a future condition — a
    low-confidence halt, say — has somewhere to live without rewiring the graph.
    A test here means adding that condition breaks something visible.
    """
    intake_result = make_intake(validation_passed=True)
    state = PipelineState(intake=intake_result, transcription=make_transcript())
    assert route_after_transcription(state) == "summarize"


def test_low_severity_flag_routes_to_report():
    """Severity below critical is scored, not escalated.

    The half of the pair that stops a router hardcoded to "supervisor_review"
    from passing. Deliberately includes a HIGH flag rather than only a LOW one:
    that proves *only* critical escalates, not merely that some severities do
    not.
    """
    qa_scores = make_qa_scores(
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
    qa_scores = make_qa_scores(
        _compliance_flags(True),
        professionalism=5,
        empathy=5,
        problem_resolution=5,
        compliance=5,
        communication_clarity=5,
    )
    state = PipelineState(qa_scores=qa_scores)
    assert route_after_qa(state) == "supervisor_review"
