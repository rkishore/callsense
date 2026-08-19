"""
Stage 7 — report assembly.

Pure assembly: takes what the pipeline produced and returns a CallReport. No
I/O, no database, no PDF. Those are separate functions so this one can be
tested without either.
"""

import uuid
from datetime import datetime

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
