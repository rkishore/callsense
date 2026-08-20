"""
Stage 7 — report assembly.

Pure assembly: takes what the pipeline produced and returns a CallReport. No
I/O, no database, no PDF. Those are separate functions so this one can be
tested without either.
"""

import uuid
from datetime import datetime

from sqlalchemy.engine import Engine

from src.database.connection import session_scope
from src.database.models import CallRecord
from src.graph.state import (
    CallReport,
    CallStatus,
    QAScoreResult,
    SummaryResult,
    TranscriptionResult,
)


def compile_report(
    call_id: uuid.UUID,
    audio_filename: str,
    transcription_result: TranscriptionResult,
    summary: SummaryResult,
    qa_scores: QAScoreResult,
    status: CallStatus,
    processed_at: datetime,
    trace_id: str | None = None,
) -> CallReport:
    """Assemble the final report from the pipeline's outputs.

    Args:
        call_id: The transcription's, not the LLM's. Both summarization and QA
            scoring overwrite the model's invented UUID, so by here every stage
            agrees on one identifier.
        audio_filename: From the original AudioInput.
        transcription_result: **Redacted** by the time it reaches here — stage 4
            replaced the transcription in state with a copy whose full_text and
            every segment have been through the PII redactor. That is what makes
            CallRecord.transcript_text safe to persist.
        summary: Stage-5 output.
        qa_scores: Stage-6 output, with overall_score recomputed by Python.
        status: CallStatus.COMPLETED on the ordinary path. The caller decides,
            since a report can also be compiled for a call routed to supervisor
            review.
        processed_at: When the analysis finished. Passed in rather than defaulted
            so the report and the database row cannot disagree.
        trace_id: LangSmith run id, or None when tracing is off. None means
            untraced, not failed.

    Returns:
        A validated CallReport.
    """
    return CallReport(
        call_id=call_id,
        audio_filename=audio_filename,
        transcription_result=transcription_result,
        summary=summary,
        qa_scores=qa_scores,
        status=status,
        processed_at=processed_at,
        trace_id=trace_id,
    )


def persist_report(report: CallReport, engine: Engine | None = None) -> None:
    """Write one CallRecord for a finished call.

    Args:
        report: The assembled report.
        engine: Defaults to the process-wide engine. Nodes receive only the
            state, so report_node has no engine to pass — the same fallback the
            SHA-256 cache relies on, and app.py seeds the singleton at startup.

    **transcript_text is redacted by construction.** It comes from
    report.transcription_result, which stage 4 replaced in state with a copy
    whose full_text and every segment have been through the PII redactor. Taking
    it from anywhere else would persist card numbers to disk while
    CallRecord's docstring claims otherwise.

    Keyword arguments throughout, and not only because SQLAlchemy models reject
    positional ones: nine same-typed values in a row means a transposition would
    silently store the QA JSON in the summary column.

    processed_at comes from the report rather than the column default, so the
    report and the row cannot disagree by however long persistence took.

    call_id is unique on this table, so re-persisting a call raises
    IntegrityError. Not reachable today — the SHA-256 cache returns early for a
    repeated upload — but it would surface if the cache were cleared and the
    same file re-analysed.
    """
    with session_scope(engine) as session:
        session.add(
            CallRecord(
                call_id=str(report.call_id),
                status=report.status,
                audio_filename=report.audio_filename,
                transcript_text=report.transcription_result.full_text,
                summary_json=report.summary.model_dump_json(),
                qa_scores_json=report.qa_scores.model_dump_json(),
                report_json=report.model_dump_json(),
                processed_at=report.processed_at,
                trace_id=report.trace_id,
            )
        )


def generate_report_json(report: CallReport) -> str:
    return report.model_dump_json(indent=2)
