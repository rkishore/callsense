"""
The seam between the Gradio UI and the LangGraph pipeline.

Everything above this module is presentation; everything below it is the
pipeline. process_call() is the only function the UI calls.
"""

from pathlib import Path
from typing import NamedTuple

from langgraph.graph.state import CompiledStateGraph

from src.graph.state import AudioInput, CallStatus
from src.utils.config import Config
from src.utils.formatters import format_qa, format_summary, format_transcript_for_display

REJECTED = "This file could not be analysed."

BLOCKED = """### Analysis stopped

A prompt injection attempt was detected in this transcript, so it was never sent
to the language model.

**Patterns matched:** {patterns}"""


class PipelineResult(NamedTuple):
    status: CallStatus
    transcript: str
    summary: str
    qa: str


def process_call(
    audio_path: Path,
    graph: CompiledStateGraph,
    config: Config,
    caller_id: str | None = None,
    department: str | None = None,
) -> PipelineResult:
    """Run one audio file through the pipeline and render it for the UI.

    The seam between Gradio and the graph: everything above it is presentation,
    everything below is the pipeline.

    Args:
        audio_path: What gr.Audio(type="filepath") handed the callback. The
            Gradio spike confirmed this is the original file, untouched — so
            read_bytes() gives exactly the bytes AudioInput wants, and the
            magic-byte validator still sees the real format.
        graph: Compiled once at startup by app.py, not per call.
        config: Supplies confidence_threshold for the [LOW CONF] markers.
        caller_id: Optional metadata, scanned for PII at intake.
        department: Optional metadata, scanned for PII at intake.

    Returns:
        PipelineResult — three rendered strings and the final status.

    **Branches on key presence, not on status.** CallStatus.FLAGGED_FOR_REVIEW
    is set by two different paths: an injection block, which stops before
    summarization and leaves no summary or qa_scores in state, and a critical
    compliance flag, which reaches supervisor review with both present. The
    status cannot distinguish them; the keys can.

    The transcript rendered here is the redacted one — stage 4 replaced the
    transcription in state — so the UI shows [REDACTED_CREDIT_CARD] rather than
    a card number.
    """
    audio_input = AudioInput(
        audio_data=Path(audio_path).read_bytes(),
        filename=Path(audio_path).name,
        caller_id=caller_id,
        department=department,
    )

    result = graph.invoke({"audio_input": audio_input})
    status = result["status"]

    # Rejected at intake: nothing was transcribed, so the validator's message is
    # the only thing worth showing.
    if "transcription" not in result:
        return PipelineResult(
            transcript=result["intake"].validation_error or REJECTED,
            summary="",
            qa="",
            status=status,
        )

    transcript = format_transcript_for_display(
        result["transcription"].segments, config.confidence_threshold
    )

    # Blocked before any LLM saw the text. There is a transcript but no analysis,
    # and naming the matched patterns is the point — it shows the guard working
    # rather than the pipeline merely failing.
    if "qa_scores" not in result:
        matched = ", ".join(result["injection_scan"].patterns_matched)
        return PipelineResult(
            transcript=transcript,
            summary=BLOCKED.format(patterns=matched),
            qa="",
            status=status,
        )

    return PipelineResult(
        transcript=transcript,
        summary=format_summary(result["summary"]),
        qa=format_qa(result["qa_scores"]),
        status=status,
    )
