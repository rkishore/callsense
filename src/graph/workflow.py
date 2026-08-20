"""
The pipeline's nodes — one per stage, plus three terminals.

Every node takes the state and returns a **partial dict** of the keys it wrote.
LangGraph merges that into the state; nothing here mutates the dict it was
handed. Because each node writes disjoint keys, there are no reducers and no
merge semantics to reason about.

Two things about PipelineState, which is a TypedDict and therefore a plain dict
at runtime with no validation whatsoever:

- Access is state["key"], never state.key — the latter raises AttributeError.
- A misspelled key in a returned dict is accepted silently. The value lands
  where nothing reads it and the pipeline runs to completion having quietly
  skipped a stage.

Reading a key that no earlier node set raises KeyError rather than returning
None, since total=False means optional-and-absent rather than defaulted.
"""

from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine

from src.agents.intake import run_intake
from src.agents.qa_scoring import run_qa_scoring
from src.agents.report import compile_report, persist_report
from src.agents.summarization import run_summarization
from src.agents.transcription import run_transcription
from src.graph.edges import (
    route_after_injection,
    route_after_intake,
    route_after_qa,
    route_after_transcription,
)
from src.graph.state import AuditAction, CallStatus, PipelineState, SeverityLevel
from src.security.audit import AuditLogger
from src.security.injection_detector import detect_injection
from src.security.pii_redactor import detect_and_redact_pii
from src.utils.config import Config, get_logger

logger = get_logger(__name__)


def intake_step(state: PipelineState) -> dict:
    """Stage 1 — validate the upload before anything expensive runs."""
    audio_input = state["audio_input"]
    result = run_intake(audio_input)
    audio_properties = result.audio_properties
    pii_scan_result = result.pii_scan
    audit_details = {
        "filename": audio_input.filename,
        "format": audio_properties.format,
        "duration_seconds": audio_properties.duration_seconds,
        "size_bytes": audio_properties.size_bytes,
        "metadata_pii_types": pii_scan_result.pii_types,
        "metadata_pii_count": pii_scan_result.pii_count,
    }
    AuditLogger().log(result.call_id, AuditAction.STARTED, details=audit_details)

    return {"intake": result}


def transcription_node(state: PipelineState) -> dict:
    """Stage 2 — Whisper, diarization and the SHA-256 cache.

    The only node that may return without doing its work: an identical
    recording seen before comes straight from the cache.
    """
    result = run_transcription(state["intake"])
    return {"transcription": result}


def injection_check_node(state: PipelineState) -> dict:
    """Stage 3 — a fail-closed input guard, before any LLM sees the text.

    Scans full_text rather than the formatted transcript. The 22 patterns were
    measured against raw Whisper output, and the formatted version prefixes
    every line with "Speaker:" — which the conversation_inject pattern
    (human|assistant|user|ai followed by a colon) would match if the diarizer
    ever emitted a label like "User". Scanning full_text removes the coupling.

    status is returned only on a match; setting it unconditionally would flag
    every call in the system.
    """
    result = detect_injection(state["transcription"].full_text)
    retval = {"injection_scan": result}
    if result.injection_detected:
        retval["status"] = CallStatus.FLAGGED_FOR_REVIEW
        AuditLogger().log(
            state["transcription"].call_id,
            AuditAction.FLAGGED,
            details={
                "filename": state["audio_input"].filename,
                "injection_detected": result.injection_detected,
                "patterns_matched": result.patterns_matched,
            },
        )

    return retval


def pii_redaction_node(state: PipelineState) -> dict:
    """Stage 4 — redact PII from the full text *and* every segment.

    Returns the redacted transcript under the same "transcription" key,
    replacing the original. That is deliberate: downstream nodes read
    state["transcription"] and there is no longer a raw version to reach for,
    so sending unredacted text to a third-party LLM becomes unrepresentable
    rather than merely discouraged.

    The scan counts come from full_text alone. Summing the per-segment counts as
    well would report the same card number twice.
    """
    transcription = state["transcription"]
    scan = detect_and_redact_pii(state["transcription"].full_text)

    redacted_segments = [
        seg.model_copy(update={"text": detect_and_redact_pii(seg.text).redacted_text})
        for seg in transcription.segments
    ]

    redacted = transcription.model_copy(
        update={"full_text": scan.redacted_text, "segments": redacted_segments}
    )

    return {"pii_redactor_scan": scan, "transcription": redacted}


def summarize_and_qa_node(state: PipelineState) -> dict:
    """Stages 5 and 6 — both LLM calls, sequential by design.

    QA scoring receives the summary as context: a reviewer who knows the call
    was about a disputed charge judges the handling differently from one seeing
    only the transcript. That dependency is why these are one node rather than
    two parallel ones.

    The transcript read here is the redacted one, courtesy of stage 4.
    """
    summary_result = run_summarization(state["transcription"])
    qa_score_result = run_qa_scoring(state["transcription"], summary_result)
    return {"summary": summary_result, "qa_scores": qa_score_result}


def error_node(state: PipelineState) -> dict:
    """Terminal: the call could not be analysed.

    Reached from route_after_intake when validation failed.
    """
    intake = state.get("intake")
    if intake is None:
        logger.error("error_node reached with no intake in state")
        return {"status": CallStatus.FAILED}

    AuditLogger().log(
        state["intake"].call_id,
        AuditAction.FAILED,
        details={"filename": state["audio_input"].filename, "error": intake.validation_error},
    )
    return {"status": CallStatus.FAILED}


def supervisor_review_node(state: PipelineState) -> dict:
    """Terminal: a human needs to look at this call.

    Reached when any compliance flag is critical, however well the call scored
    overall.
    """
    if "qa_scores" not in state:
        return {"status": CallStatus.FLAGGED_FOR_REVIEW}

    compliance_flags = state["qa_scores"].compliance_flags
    critical_severity_flags = [
        flag for flag in compliance_flags if flag.severity == SeverityLevel.CRITICAL
    ]
    if critical_severity_flags:
        AuditLogger().log(
            state["transcription"].call_id,
            AuditAction.FLAGGED,
            details={
                "filename": state["audio_input"].filename,
                "critical_compliance_flag_count": len(critical_severity_flags),
            },
        )

    return {"status": CallStatus.FLAGGED_FOR_REVIEW}


def report_node(state: PipelineState) -> dict:
    """Stage 7 — assemble the report and mark the call complete."""
    result = compile_report(
        state["transcription"].call_id,
        state["audio_input"].filename,
        state["transcription"],
        state["summary"],
        state["qa_scores"],
        CallStatus.COMPLETED,
        processed_at=datetime.now(UTC),
    )
    persist_report(result)

    AuditLogger().log(
        state["transcription"].call_id,
        AuditAction.COMPLETED,
        details={
            "filename": state["audio_input"].filename,
            "pii_detected": state["pii_redactor_scan"].pii_detected,
            "pii_types": state["pii_redactor_scan"].pii_types,
            "pii_count": state["pii_redactor_scan"].pii_count,
            "overall_score": state["qa_scores"].overall_score,
            "compliance_flags_count": len(state["qa_scores"].compliance_flags),
        },
    )

    return {"report": result, "status": CallStatus.COMPLETED}


def compile_workflow(config: Config, db_engine: Engine) -> CompiledStateGraph:
    """Build and compile the pipeline graph.

    Args:
        config: The active configuration.
        db_engine: Passed through for report persistence.

    Returns:
        A compiled graph, ready for invoke(). One call per audio file.

    A function rather than module-level code so the graph is not built at import
    time, and so tests can compile a fresh one per test.

    **No checkpointer.** This is a stateless batch pipeline — one invoke() per
    call, no conversation, nothing to carry between runs. A MemorySaver here
    would also make invoke() require a config carrying a thread_id, and raise
    without one.

    Four branch points, all reached through edges.py so the routing rules stay
    testable without constructing a graph:

        intake       -> transcribe | error
        transcribe   -> injection check          (unconditional today)
        injection    -> pii redact | supervisor
        summarize+qa -> report | supervisor

    pii redact -> summarize_and_qa is a plain edge: there is no decision to make,
    and redaction cannot fail in a way the pipeline can route around.

    Every terminal edges to END. A path that reached no terminal would hang the
    graph rather than fail it, which is the failure mode worth checking after
    any rewiring.
    """

    workflow = StateGraph(PipelineState)

    # ── Add nodes ────────────────────────────────────────
    workflow.add_node("intake_step", intake_step)
    workflow.add_node("transcribe_step", transcription_node)
    workflow.add_node("injection_check_step", injection_check_node)
    workflow.add_node("pii_redact_step", pii_redaction_node)
    workflow.add_node("summarize_and_qa_step", summarize_and_qa_node)
    workflow.add_node("error_step", error_node)
    workflow.add_node("supervisor_step", supervisor_review_node)
    workflow.add_node("report_step", report_node)

    # ── Add edges ────────────────────────────────────────
    workflow.add_edge(START, "intake_step")

    # ── Add conditional edges ────────────────────────────────────────
    workflow.add_conditional_edges(
        "intake_step", route_after_intake, {"transcribe": "transcribe_step", "error": "error_step"}
    )

    workflow.add_conditional_edges(
        "transcribe_step", route_after_transcription, {"summarize": "injection_check_step"}
    )

    workflow.add_conditional_edges(
        "injection_check_step",
        route_after_injection,
        {"redact_pii": "pii_redact_step", "supervisor_review": "supervisor_step"},
    )

    workflow.add_edge("pii_redact_step", "summarize_and_qa_step")

    workflow.add_conditional_edges(
        "summarize_and_qa_step",
        route_after_qa,
        {"supervisor_review": "supervisor_step", "report": "report_step"},
    )

    workflow.add_edge("report_step", END)
    workflow.add_edge("error_step", END)
    workflow.add_edge("supervisor_step", END)

    graph = workflow.compile()

    logger.info("Graph compiled")

    return graph
