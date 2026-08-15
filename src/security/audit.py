"""
The compliance record — an append-only account of what the pipeline did.

Its value comes from being immutable. A log you can edit is not evidence of
anything, so this module deliberately offers one verb. There is no update path
and no delete path, and that absence *is* the guarantee: the schema does not
enforce it, since SQLite will accept an UPDATE from anyone who writes one.

Writes are separate from the pipeline's own error handling on purpose. A call
that fails still produces audit entries, because "this call was attempted and
went wrong" is exactly the thing a compliance reviewer needs to see.
"""

import uuid

from sqlalchemy.engine import Engine

from src.database.connection import session_scope
from src.database.models import AuditLogEntry
from src.graph.state import AuditAction


class AuditLogger:
    """Writes audit entries. Exposes `log()` and nothing else, by design."""

    def __init__(self, engine: Engine | None = None):
        """Store the engine as given, without resolving it.

        Passing None is normal: session_scope falls back to the process-wide
        engine at call time. Resolving here instead would open a database
        connection the moment an AuditLogger is constructed, whether or not
        anything is ever logged.

        Args:
            engine: Injectable so tests can point at a tmp_path database.
        """
        self.engine = engine

    def log(
        self,
        call_id: uuid.UUID,
        action: AuditAction,
        user: str = "system",
        details: dict | None = None,
    ) -> None:
        """Append one entry.

        Args:
            call_id: The call this concerns. Converted to str here — the
                pipeline carries UUID objects and the column is String(36), so
                this method owns that boundary rather than every caller.
            action: A member of AuditAction rather than a free string. Audit
                queries match on exact values, so a typo'd "complete" would
                write a row that every later query silently misses — the worst
                failure a log can have, because it looks like nothing happened.
            user: Defaults to "system"; the pipeline runs unattended. A real
                value belongs here once anything is triggered by a person.
            details: Any JSON-serialisable dict — matched injection patterns,
                an error message. Passed through untouched, because the JSON
                column handles serialisation in both directions.

        The commit belongs to session_scope. Committing inside the block would
        break the atomicity the context manager exists to provide.
        """
        with session_scope(self.engine) as session:
            entry = AuditLogEntry(call_id=str(call_id), action=action, user=user, details=details)
            session.add(entry)
