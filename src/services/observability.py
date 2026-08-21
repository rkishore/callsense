"""
Metrics for the Observability tab.

One query pass, one session. The whole dashboard is assembled inside a single
session_scope rather than a call per metric — seven separate scopes would mean
seven connections and, worse, seven snapshots of a database that is being
written to while the page renders. A dashboard whose numbers disagree with each
other is harder to trust than one that is slightly stale.
"""

import json
import os

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from src.database.connection import session_scope
from src.database.models import AuditLogEntry, CallRecord
from src.graph.state import AuditAction
from src.utils.config import get_logger

logger = get_logger(__name__)

RECENT_AUDIT_LIMIT = 20

AUDIT_COLUMNS = ["Timestamp", "Call ID", "Action", "Details"]

NO_CALLS_YET = "_No calls analysed yet._"


def _mean_overall_score(qa_json_blobs: list[str]) -> float | None:
    """Average overall_score across completed calls.

    Computed in Python rather than SQL because qa_scores_json is a Text column
    holding a Pydantic serialisation — there is nothing for AVG() to reach
    inside. At demo volume that is a few hundred rows; at real volume the right
    fix is an overall_score column on call_records, which is a schema change and
    not one to make with the deadline close.

    A blob that no longer parses is skipped rather than fatal. The dashboard
    reporting a slightly wrong average is better than the dashboard failing to
    render, and a burst of these means the QA schema changed.
    """
    scores = []
    for blob in qa_json_blobs:
        try:
            scores.append(json.loads(blob)["overall_score"])
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Skipping an unparseable qa_scores_json row")

    return sum(scores) / len(scores) if scores else None


def _count_compliance_flags(qa_json_blobs: list[str]) -> int:
    """Total compliance flags raised across all calls, for the same reason."""
    total = 0
    for blob in qa_json_blobs:
        try:
            total += len(json.loads(blob).get("compliance_flags", []))
        except (json.JSONDecodeError, TypeError):
            continue

    return total


def _format_metrics(
    total: int,
    by_action: dict[str, int],
    mean_score: float | None,
    flag_count: int,
    audit_events: int,
) -> str:
    """Render the metrics block as Markdown."""
    if total == 0:
        return f"### Pipeline\n\n{NO_CALLS_YET}"

    completed = by_action.get(AuditAction.COMPLETED, 0)
    # Success rate counts completed calls only. A call flagged for review was
    # analysed successfully and deliberately withheld — it is not a failure, and
    # counting it as one would make a working security guard look like an
    # outage.
    success_rate = completed / total

    return "\n".join(
        [
            "### Pipeline",
            f"- **Calls analysed:** {total}",
            f"- **Completed:** {completed}",
            f"- **Flagged for review:** {by_action.get(AuditAction.FLAGGED, 0)}",
            f"- **Failed:** {by_action.get(AuditAction.FAILED, 0)}",
            f"- **Success rate:** {success_rate:.0%}",
            "",
            "### Quality",
            f"- **Average QA score:** {mean_score:.2f} / 5.00"
            if mean_score is not None
            else "- **Average QA score:** _n/a_",
            f"- **Compliance flags raised:** {flag_count}",
            "",
            "### Audit",
            f"- **Events recorded:** {audit_events}",
        ]
    )


def _format_langsmith_status() -> str:
    """Whether tracing is on, and enough detail to fix it if it is not.

    Reads LANGSMITH_* first and falls back to the older LANGCHAIN_* prefix.
    Current langsmith releases honour both, and .env.example standardises on
    LANGSMITH_* while the course documentation uses LANGCHAIN_*. Checking only
    one meant the dashboard reported tracing disabled while it was running.
    """
    enabled = (
        os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2") or ""
    ).lower() == "true"
    project = (
        os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "default"
    )

    if enabled:
        return f"### LangSmith\n\n🟢 Tracing enabled — project `{project}`."

    return (
        "### LangSmith\n\n⚪ Tracing disabled. "
        "Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to enable."
    )


def _format_audit_rows(entries: list[AuditLogEntry]) -> list[list[str]]:
    """Audit entries as plain rows for gr.Dataframe.

    Converted to strings inside the session that loaded them. Reading an
    attribute after the scope closes raises DetachedInstanceError, and a
    Dataframe holding ORM objects would try exactly that when Gradio renders it.
    """
    rows = []
    for entry in entries:
        timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if entry.timestamp else ""
        details = json.dumps(entry.details) if entry.details else ""
        rows.append([timestamp, entry.call_id[:8], str(entry.action), details])

    return rows


def get_observability_dashboard(engine: Engine | None = None) -> tuple[str, str, list[list[str]]]:
    """Everything the Observability tab renders, from one session.

    Args:
        engine: Defaults to the process-wide engine.

    Returns:
        (metrics_markdown, langsmith_markdown, audit_rows) — the third ready for
        gr.Dataframe with AUDIT_COLUMNS as its headers.
    """
    with session_scope(engine) as session:
        # Outcome counts come from audit_log, not call_records. persist_report
        # runs only in report_node, so call_records contains *completed* calls
        # and nothing else — counting failures there would report zero of them
        # and a success rate of completed-over-completed, which is always 100%.
        # audit_log sees every call, because every terminal writes an event.
        action_counts = dict(
            session.execute(
                select(AuditLogEntry.action, func.count()).group_by(AuditLogEntry.action)
            ).all()
        )
        # One STARTED per call, written by intake_step before any routing.
        total_calls = action_counts.get(AuditAction.STARTED, 0)

        # Scores still come from call_records: only a completed call has any.
        qa_blobs = list(session.scalars(select(CallRecord.qa_scores_json)).all())
        audit_events = session.scalar(select(func.count()).select_from(AuditLogEntry)) or 0

        recent = list(
            session.scalars(
                select(AuditLogEntry).order_by(AuditLogEntry.id.desc()).limit(RECENT_AUDIT_LIMIT)
            ).all()
        )
        audit_rows = _format_audit_rows(recent)

    metrics_md = _format_metrics(
        total_calls,
        action_counts,
        _mean_overall_score(qa_blobs),
        _count_compliance_flags(qa_blobs),
        audit_events,
    )

    return metrics_md, _format_langsmith_status(), audit_rows
