"""
Unit tests for auditlogger functions in src/security/audit.py.
"""

import uuid

from sqlalchemy import select

from src.database.connection import session_scope
from src.database.models import AuditLogEntry
from src.graph.state import AuditAction
from src.security.audit import AuditLogger


def test_log_writes_one_row(db_engine):
    """One call to log() appends exactly one entry.

    The query below is the shape every later test here reuses:

        select(AuditLogEntry)                       what to fetch
            .where(AuditLogEntry.call_id == ...)    the filter
        session.scalars(...)                        rows as model instances
            .all() / .one()                         list, or exactly one

    Not session.get(), which looks up by *primary key* — that is `id`, an
    autoincrementing integer. call_id is deliberately non-unique, so it can
    never be a get() key.

    scalars() rather than execute(): execute() yields Row tuples and you would
    write row[0] everywhere. Assertions happen inside the session_scope block
    because commit() expires instances and close() detaches them.
    """
    call_id = uuid.uuid4()
    AuditLogger(db_engine).log(call_id, AuditAction.STARTED)

    with session_scope(db_engine) as session:
        entries = session.scalars(
            select(AuditLogEntry).where(AuditLogEntry.call_id == str(call_id))
        ).all()

        assert len(entries) == 1
        assert entries[0].action == AuditAction.STARTED
        assert entries[0].user == "system"


def test_call_id_is_stored_as_a_string(db_engine):
    """The type conversion log() owns.

    The pipeline carries uuid.UUID objects; the column is String(36). Doing the
    str() here rather than at every call site means no caller has to know, but
    it also means nothing else checks it — drop the str() and SQLAlchemy may
    coerce the UUID quietly rather than raising.

    Both assertions earn their place: isinstance proves it is a string, the
    equality proves it is the *right* string. A conversion that produced
    "UUID('...')" would satisfy the first and fail the second.
    """
    call_id = uuid.uuid4()
    AuditLogger(db_engine).log(call_id, AuditAction.STARTED)

    with session_scope(db_engine) as session:
        entries = session.scalars(
            select(AuditLogEntry).where(AuditLogEntry.call_id == str(call_id))
        ).all()

        assert len(entries) == 1
        assert isinstance(entries[0].call_id, str)
        assert entries[0].call_id == str(call_id)


def test_details_round_trip(db_engine):
    """A dict goes in and a dict comes back, never a JSON string.

    Not redundant with test_details_round_trips_as_a_dict in test_connection.py,
    which proves the *column* serialises. This proves log() passes the dict
    through **untouched**: add a json.dumps inside log() and the connection test
    still passes, while this one fails on a JSON string of a JSON string.

    The nested list is deliberate — a double encode is obvious in
    '{"patterns": ["ignore_previous"]}' and easy to miss in '{"k": 1}'.
    """
    details = {"patterns": ["ignore_previous", "prompt_leak"], "count": 2}
    call_id = uuid.uuid4()
    AuditLogger(db_engine).log(call_id, AuditAction.STARTED, details=details)

    with session_scope(db_engine) as session:
        entries = session.scalars(
            select(AuditLogEntry).where(AuditLogEntry.call_id == str(call_id))
        ).all()
        assert len(entries) == 1
        assert isinstance(entries[0].details, dict)
        assert entries[0].details == details


def test_multiple_entries_share_one_call_id(db_engine):
    """Several entries per call — what the non-unique index exists for.

    Every real call writes started, then completed or failed, and possibly
    flagged. If call_id were unique the second write would raise, so this pins
    the schema decision that distinguishes audit_log from call_records.
    """
    call_id = uuid.uuid4()
    auditlogger = AuditLogger(db_engine)
    auditlogger.log(call_id, AuditAction.STARTED)
    auditlogger.log(call_id, AuditAction.COMPLETED)

    with session_scope(db_engine) as session:
        entries = session.scalars(
            select(AuditLogEntry)
            .where(AuditLogEntry.call_id == str(call_id))
            .order_by(AuditLogEntry.id)
        ).all()

        assert len(entries) == 2
        assert [e.action for e in entries] == [AuditAction.STARTED, AuditAction.COMPLETED]
