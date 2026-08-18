"""
Formatting — Pydantic models in, text out.

Two audiences, and it matters which is which:

- **Model input.** format_transcript_segments renders a transcript for an LLM
  prompt. Summarization and QA scoring both consume it, which is why it lives
  here rather than in either agent.
- **User output.** format_summary and format_qa render Markdown for the Gradio
  Analyze tab, where the two sit side by side in a gr.Row — hence ### headings
  rather than #, since two panels of H1 overwhelm the transcript below them.

Do not unify them. They share secs_to_mmss and nothing else: one is read by a
model that will imitate its format, the other by a person.

Display only, deliberately. Nothing here computes, defaults or re-derives a
value — an empty call_purpose is a prompt problem, and a formatter that quietly
filled the gap would make a broken pipeline look healthy in a demo.
"""

from src.graph.state import (
    QA_DIMENSIONS,
    ActionItem,
    ComplianceFlag,
    QADimensionScore,
    QAScoreResult,
    SeverityLevel,
    SummaryResult,
    TranscriptionSegment,
)

NOT_IDENTIFIED = "_None identified._"

# No flags is good news, so it gets its own wording rather than the neutral one.
NO_COMPLIANCE_ISSUES = "_No compliance issues identified._"

# Severity carries more at a glance than the word does. A scorecard where one
# critical flag is visually distinct from three low ones reads faster than four
# identical lines — which matters, because routing to supervisor review keys off
# exactly that distinction.
SEVERITY_ICONS = {
    SeverityLevel.LOW: "\u2139\ufe0f",
    SeverityLevel.MEDIUM: "\u26a0\ufe0f",
    SeverityLevel.HIGH: "\U0001f536",
    SeverityLevel.CRITICAL: "\U0001f534",
}


def secs_to_mmss(seconds: float) -> str:
    """Convert float seconds to a zero-padded "MM:SS" string.

    Truncates rather than rounds: 59.9 is still second 59, and rounding would
    produce a timestamp for a moment that has not happened yet.

    Minutes deliberately exceed 59 rather than rolling into hours — a
    3600-second call is legal, so "60:00" is a valid timestamp here. It looks
    like an overflow bug at a glance and is not.

    Negative input clamps to zero. It should not occur, but Python floors
    toward negative infinity, so -75 would otherwise render as "-2:00" — further
    from zero than the input.
    """
    seconds = max(0.0, seconds)
    m = int(seconds // 60)
    s = int(seconds % 60)

    return f"{m:02d}:{s:02d}"


def _format_transcript_segment(segment: TranscriptionSegment) -> str:
    """One transcript line for an LLM prompt: "[MM:SS - MM:SS] Speaker: text".

    Prompt format, not display format. The QA prompt asks the model to cite
    timestamps in its justifications, and it will imitate whatever it sees here
    — so this format and the citation format asked for in that prompt have to
    agree.

    The range is start-to-end position. An earlier version passed
    (end_time - start_time), giving each segment's *duration*: a line at 147.3s
    rendered as "[00:04]", a plausible-looking early-call timestamp that was
    wrong by two and a half minutes. Every citation built on it would have been
    wrong and none would have looked it.

    speaker falls back to "Unknown" — it is Optional on the model, and "None:"
    in a prompt invites the model to treat it as a participant.
    """
    timestamp = f"[{secs_to_mmss(segment.start_time)}-{secs_to_mmss(segment.end_time)}]"
    speaker = "Unknown" if segment.speaker is None else segment.speaker
    result = f"{timestamp} {speaker.title()}: {segment.text}"
    return result


def format_transcript_segments(segments: list[TranscriptionSegment]) -> str:
    """The whole transcript as prompt text, one segment per line."""
    return "\n".join(_format_transcript_segment(segment) for segment in segments)


def _format_action_item(item: ActionItem) -> str:
    """One action item, with owner and deadline only when the LLM supplied them.

    Both are optional on the model, and interpolating the object directly would
    put "deadline=None" on screen — a Pydantic repr is not a display format.
    """
    parts = [item.description]
    if item.owner:
        parts.append(f"owner: {item.owner}")
    if item.deadline:
        parts.append(f"due: {item.deadline}")

    return " — ".join(parts)


def _section(heading: str, *lines: str) -> str:
    """One heading plus its body, as a single Markdown block.

    Lines inside a section join with a single newline, not a blank one.
    A blank line between list items makes Markdown render a *loose* list —
    every item in its own paragraph — which doubles the vertical space in a
    side-by-side panel. Sections themselves do need the blank line.
    """
    return "\n".join([heading, *lines])


def format_summary(d: SummaryResult) -> str:
    """Render a SummaryResult as Markdown for the Analyze tab.

    Sections are omitted rather than left empty where the model legitimately
    returns nothing: a short call may have no action items, and a heading with
    nothing under it reads as a failure rather than as an accurate summary.

    call_id is not displayed. It is a UUID the user has no use for, and the
    provider spike showed a stale one can survive if the post-call overwrite is
    ever missed — better not to put it on screen at all.
    """
    sections = [
        _section("### Call Purpose", d.call_purpose or NOT_IDENTIFIED),
        _section(
            "### Key Discussion Points",
            *(
                [f"{i}. {p}" for i, p in enumerate(d.key_discussion_points, start=1)]
                or [NOT_IDENTIFIED]
            ),
        ),
        _section(
            "### Action Items",
            *(
                [f"{i}. {_format_action_item(a)}" for i, a in enumerate(d.action_items, start=1)]
                or [NOT_IDENTIFIED]
            ),
        ),
        # A StrEnum interpolates as its value ("escalated"); title-case for display.
        _section("### Resolution Status", d.resolution_status.title()),
        _section("### Sentiment Trajectory", d.sentiment_trajectory or NOT_IDENTIFIED),
        _section(
            "### Entities",
            *([f"- **{e.name}** ({e.type})" for e in d.entities] or [NOT_IDENTIFIED]),
        ),
    ]

    return "\n\n".join(sections)


def _format_dimensional_score(q: QADimensionScore) -> str:
    parts = [f"Score: {q.score}"]
    parts.append(f"Justification: {q.justification}")
    return "\n".join(parts)


def _format_compliance_flags(flags: list[ComplianceFlag]) -> str:
    """Numbered flags, each led by its severity icon and an MM:SS timestamp.

    The timestamp is the point. A reviewer reading "a violation occurred" has to
    listen to the whole call; one reading "02:27" can scrub straight to it, which
    is why the milestone asks for MM:SS citations rather than prose.
    """
    if not flags:
        return NO_COMPLIANCE_ISSUES

    return "\n".join(
        f"{i}. {SEVERITY_ICONS[f.severity]} **{secs_to_mmss(f.transcript_timestamp)}** — "
        f"{f.violation_description}"
        for i, f in enumerate(flags, start=1)
    )


def format_qa(q: QAScoreResult) -> str:
    """Render a QAScoreResult as Markdown for the Analyze tab.

    Sits beside format_summary in a gr.Row, so heading levels match: ### for
    sections, #### for the individual dimensions nested under one of them.

    overall_score here is the value Python recomputed from the weighted
    dimensions, not the one the LLM proposed — that is discarded before this is
    ever called.
    """
    sections = [
        _section("### Overall Score", str(q.overall_score)),
        _section("### Individual dimensional scores"),
        *(
            _section(
                f"#### {spec.label} ({spec.weight:.0%})",
                _format_dimensional_score(getattr(q, name)),
            )
            for name, spec in QA_DIMENSIONS.items()
        ),
        _section("### Compliance Flags", _format_compliance_flags(q.compliance_flags)),
    ]

    return "\n\n".join(sections)
