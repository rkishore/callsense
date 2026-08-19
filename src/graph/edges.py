"""
Conditional routing — the decisions that make this a graph rather than a
sequence of function calls.

Each function reads the state and returns a **string**. That string is looked up
in the mapping passed to add_conditional_edges, so the values here and the keys
there must agree exactly: a typo fails at wiring time with a graph-construction
error rather than at the routing call, which is a long way from the cause.

PipelineState is a TypedDict, so it is a plain dict at runtime — state["qa_scores"],
never state.qa_scores. The latter raises AttributeError.
"""

from src.graph.state import PipelineState, SeverityLevel


def route_after_intake(state: PipelineState) -> str:
    """Transcribe validated audio; send everything else to the error node.

    The first of two places the pipeline can end early. Intake has already
    checked magic bytes, size and duration, so a failure here means the upload
    was never analysable — there is nothing to degrade gracefully into.
    """
    return "transcribe" if state["intake"].validation_passed else "error"


def route_after_transcription(state: PipelineState) -> str:
    """Always continue. Unconditional on purpose.

    Not dead weight, despite having one outcome. It keeps every stage boundary
    the same shape, and gives a future condition — a low-confidence halt, say,
    when flagged_low_confidence is set — somewhere to live without rewiring the
    graph.
    """
    return "summarize"


def route_after_qa(state: PipelineState) -> str:
    """One critical compliance flag diverts the whole call to human review.

    This is the decision the graph exists for, and the sixth beat of the demo.
    A call scoring well overall still goes to a supervisor if any single flag is
    critical — severity is not averaged into the score and cannot be outweighed
    by good handling elsewhere.

    any() rather than a count: one is enough, and stopping at the first match
    means a call with twelve flags routes as fast as a call with one.

    Compared against SeverityLevel.CRITICAL rather than the string "critical".
    They are equal — it is a StrEnum — but the enum cannot be silently typo'd.
    """
    return (
        "supervisor_review"
        if any(
            flag.severity == SeverityLevel.CRITICAL for flag in state["qa_scores"].compliance_flags
        )
        else "report"
    )


def route_after_injection(state: PipelineState) -> str:
    """A detected injection stops the pipeline before any LLM sees the text.

    Without this the guard runs and stops nothing: injection_check_node would
    set the status, and a plain edge would carry the attacker-controlled
    transcript straight on through redaction into both LLM calls.

    Routes to supervisor review rather than to the error node, which is a
    deliberate deviation — the milestone's wiring says "pii redact | error"
    while the same milestone has the injection node set
    status="flagged_for_review", and error_node would immediately overwrite that
    with FAILED. The two mean different things: "error" is we could not analyse
    this call, "flagged_for_review" is we analysed it and stopped on purpose.
    Conflating them would count every successfully blocked attack as a pipeline
    failure on the observability dashboard.
    """
    if state["injection_scan"].injection_detected:
        return "supervisor_review"
    return "redact_pii"
