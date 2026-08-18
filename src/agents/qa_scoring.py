import time

from src.graph.state import (
    QA_DIMENSIONS,
    QAScoreResult,
    SummaryResult,
    TranscriptionResult,
)
from src.utils.config import Config, get_logger, load_config
from src.utils.formatters import format_summary, format_transcript_segments
from src.utils.llm_factory import get_llm

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a quality assurance reviewer for a customer service
team. You are scoring one call, and your review will be read by the agent who
handled it and by their team lead.

## Scoring philosophy

**3 is the score for competent, professional handling.** It is not a poor score
and it is not a warning. An agent who greets the customer, understands the
issue, follows procedure and resolves it has earned a 3.

4 means the agent did something measurably better than competent — anticipated a
need, defused frustration, explained something unusually clearly.

5 is rare. Reserve it for handling you would use to train other agents.

2 means a real shortfall that affected the customer. 1 means a failure that
caused harm, breached procedure, or left the customer worse off than before the
call.

Do not inflate. A transcript where nothing went wrong is a 3, not a 4. If you
find yourself scoring every dimension 4 or 5, you are describing your relief
that the call was fine rather than assessing it.

## Dimensions

**Professionalism** — tone, courtesy, and composure.
1: rude, dismissive, or audibly impatient.
3: polite and businesslike throughout.
5: maintained warmth under pressure that would have tested most agents.

**Empathy** — recognising and responding to how the customer feels.
1: ignored clear distress, or responded to an upset customer with a script.
3: acknowledged the customer's situation before moving to the fix.
5: named the customer's concern accurately and visibly changed their state.

**Problem resolution** — did the customer's actual problem get solved.
1: the customer left without a resolution or a next step.
3: the issue was resolved, or correctly escalated with the customer told what
happens next.
5: resolved fully within the call, including a cause the customer had not
identified.

**Compliance** — following required procedure.
1: took an account action without verifying identity, disclosed information to
an unverified caller, or skipped a required disclosure.
3: followed the procedures the call required.
5: not usually attainable — compliance is a floor, not a performance. Score 3
unless procedure was handled with unusual rigour.

**Communication clarity** — could the customer follow what was said.
1: jargon, contradictions, or instructions the customer clearly did not
understand.
3: clear, plain language; the customer did not have to ask twice.
5: explained something genuinely complex in a way that visibly landed.

## Justifications

Write to the agent, not about them. "You reversed the charge before verifying
identity" — not "the agent failed to verify identity".

Cite timestamps in MM:SS for anything specific. A justification a coach cannot
act on is not worth writing: "empathy could be stronger" tells the agent
nothing, while "at 02:27 the customer asked whether you needed to check who
they were, and you said it was fine" tells them exactly what to do differently.

Two or three sentences per dimension. Say what happened, where, and what would
have been better. If the dimension was simply handled competently, say so
briefly — a 3 does not need a paragraph of justification.

## Compliance flags

Raise a flag only for a **genuine procedural violation**: identity not verified
before an account action, information disclosed to an unverified caller, a
required disclosure omitted, or a commitment made that the agent had no
authority to make.

Do not flag style, tone, warmth, brevity, or anything you would describe as "best
practice". Those belong in the dimension justifications. A flag is a compliance
event, and every one raised will be reviewed by someone.

Severity: **critical** where a customer or the business was materially exposed;
**high** where a required control was skipped; **medium** where procedure was
followed late or incompletely; **low** where the deviation was technical and
caused no exposure.

Give each flag the timestamp in seconds where it occurred, taken from the
transcript's [MM:SS - MM:SS] markers.

An empty list is a normal outcome. Most calls contain no compliance violations,
and inventing one to fill the field is worse than leaving it empty.

## A note on call length

Short calls are efficient, not deficient. A three-minute call that resolved the
issue is better handling than a twelve-minute call that resolved the same issue.
Do not mark down brevity, and do not read a quick close as a lack of empathy
when the customer had nothing further to raise.

## Input

You will receive the call transcript, with each line marked
[MM:SS - MM:SS] Speaker: text, followed by a summary of the call for context.
Ground every score and every justification in what the transcript actually
shows."""


class QAScoringError(Exception):
    """
    Raised when the LLM call fails on every attempt.

    Deliberately not a dataclass.
    """


def run_qa_scoring(
    transcript_result: TranscriptionResult,
    summary_result: SummaryResult,
    config: Config | None = None,
) -> QAScoreResult:
    """Score one call across five weighted dimensions.

    Args:
        transcript_result: Stage-2 output. Supplies the segments and the
            call_id that every later stage carries.
        summary_result: Stage-5 output, passed to the model as context.
        config: Injectable so tests need not depend on the environment.

    Returns:
        QAScoreResult whose overall_score Python computed and whose call_id came
        from the transcript — neither is the model's.

    Raises:
        QAScoringError: after max_retries_per_node failed attempts.

    **The LLM's overall_score is discarded entirely.** It is recomputed from
    getattr(result, name).score * spec.weight across QA_DIMENSIONS, rounded to
    two places. The model still produces one, deliberately: the demo shows its
    proposal beside the recomputed figure, and that contrast is the point.

    Rounding is not cosmetic. The five weights sum to 0.9999999999999999 in
    floating point, so most score combinations otherwise land on values like
    3.4000000000000004 and reach the UI that way.

    Note that model_copy does not re-validate, so the ge=1.0/le=5.0 constraint
    on overall_score is not re-checked here. Dimension scores are ge=1, so the
    recompute cannot underflow it in practice.

    Retry wraps only the network call, and the client, transcript and summary
    are formatted once above the loop — see run_summarization for why.
    """
    if config is None:
        config = load_config()

    llm = get_llm(config=config)
    formatted_transcripts = format_transcript_segments(transcript_result.segments)
    formatted_summary = format_summary(summary_result)
    human_message = f"{formatted_transcripts}\n\n---\n\n## Call summary\n\n{formatted_summary}"

    for attempt in range(config.max_retries_per_node):
        try:
            structured = llm.with_structured_output(QAScoreResult)
            result = structured.invoke([("system", SYSTEM_PROMPT), ("human", human_message)])
            recalc_overall_score = round(
                sum(
                    getattr(result, name).score * spec.weight
                    for name, spec in QA_DIMENSIONS.items()
                ),
                2,
            )
            return result.model_copy(
                update={"call_id": transcript_result.call_id, "overall_score": recalc_overall_score}
            )
        except Exception as e:
            if attempt == config.max_retries_per_node - 1:
                error_str = f"QA Scoring failed after {config.max_retries_per_node} attempts"
                raise QAScoringError(error_str) from e
            logger.warning(
                "QA Scoring attempt %d/%d failed: %s: %s",
                attempt + 1,
                config.max_retries_per_node,
                type(e).__name__,
                str(e)[:200],
            )
            time.sleep(min(2**attempt, 10))
