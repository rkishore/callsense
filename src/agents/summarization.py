"""
Stage 5 — summarization. The first of two LLM calls, and the one whose output
feeds the second.

QA scoring runs after this and receives the summary as context, which is why
the two are sequential rather than parallel: a scorer that knows the call was
about a disputed charge judges the agent's handling differently from one seeing
only the transcript.
"""

import time

from src.graph.state import (
    SummaryResult,
    TranscriptionResult,
)
from src.utils.config import Config, get_logger, load_config
from src.utils.formatters import format_transcript_segments
from src.utils.llm_factory import get_llm

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are analysing a transcript of a customer service call.

Ground everything in the transcript. Do not infer facts that were not stated,
and do not add context from general knowledge about the company or industry.
If something is unclear from the transcript, say so rather than guessing.

call_purpose: why the customer called, in one sentence, from their point of view.

key_discussion_points: what was actually discussed. Substance, not pleasantries.

action_items: only commitments someone actually made. Set owner and deadline
only if they were stated aloud — leave them empty otherwise rather than
inferring who would probably do it. An empty list is correct for a call where
nothing was promised.

resolution_status: "resolved" if the customer's issue was settled during the
call, "escalated" if it was passed to another person or team, "unresolved"
otherwise.

sentiment_trajectory: how the customer's mood moved, as "Start -> End", e.g.
"Frustrated -> Satisfied". Use one word at each end.

entities: people, organisations, products and account identifiers named in the
call. Not every noun.

Timestamps in the transcript are [MM:SS - MM:SS] ranges marking when each line
was spoken."""


class SummarizationError(Exception):
    """Raised when the LLM call fails on every attempt.

    Deliberately not a dataclass — a frozen one would generate __init__(self)
    and make SummarizationError("message") a TypeError.
    """


def run_summarization(
    transcript_result: TranscriptionResult,
    config: Config | None = None,
) -> SummaryResult:
    """Summarise a transcript into a validated SummaryResult.

    Args:
        transcript_result: Stage-2 output. Supplies the segments and the
            call_id that every later stage carries.
        config: Injectable so tests need not depend on the environment.

    Returns:
        SummaryResult carrying the transcript's call_id, not the model's.

    Raises:
        SummarizationError: after max_retries_per_node failed attempts.

    The client and the formatted transcript are built once, above the loop.
    Neither fails transiently — one constructs an object, the other formats a
    string — so retrying them would mean formatting a 60-minute transcript three
    times, and would sleep twice before reporting a config error that could
    never have succeeded. The try wraps only the network call.

    Broad `except Exception` on purpose: three providers raise three different
    exception hierarchies, and the spec says catch all.

    call_id is overwritten because the model has to invent one — it is a
    required field on the schema. The provider spike found OpenAI returning
    12345678-abcd-ef01-2345-6789abcdef01: a valid UUID, so validation passes,
    and complete fiction. Gemini invented a plausible random one, which is worse
    because it looks real.

    The prompt carries what the schema cannot. Field(description=...) text is
    already sent as part of the JSON schema, so the system prompt covers
    grounding, per-field judgement calls, and the instruction that an empty
    action_items list is correct — without which a model handed a list field
    invents entries to fill it.
    """
    if config is None:
        config = load_config()

    llm = get_llm(config=config)
    formatted_transcripts = format_transcript_segments(transcript_result.segments)

    for attempt in range(config.max_retries_per_node):
        try:
            structured = llm.with_structured_output(SummaryResult)
            result = structured.invoke(
                [("system", SYSTEM_PROMPT), ("human", formatted_transcripts)]
            )
            return result.model_copy(update={"call_id": transcript_result.call_id})
        except Exception as e:
            if attempt == config.max_retries_per_node - 1:
                error_str = f"Summarization failed after {config.max_retries_per_node} attempts"
                raise SummarizationError(error_str) from e
            logger.warning(
                "Summarization attempt %d/%d failed: %s: %s",
                attempt + 1,
                config.max_retries_per_node,
                type(e).__name__,
                str(e)[:200],
            )
            time.sleep(min(2**attempt, 10))
