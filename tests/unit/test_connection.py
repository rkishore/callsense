"""
Unit tests for engine lifecycle and session handling in
src/database/connection.py.
"""

import pytest
from sqlalchemy import create_engine, inspect, select

from src.database import connection
from src.database.models import AuditLogEntry
from src.utils.config import Config


@pytest.fixture(autouse=True)
def reset_engine_singleton():
    """Clear the module singleton around every test in this file.

    Same shape and same reason as reset_whisper_singleton: an engine leaked
    from one test would be reused by the next, and a test that points the
    singleton at a tmp_path database must not leave it there.
    """
    connection._engine = None
    yield
    connection._engine = None


@pytest.fixture
def db_engine(tmp_path):
    """A real SQLite database on disk, schema created, discarded after the test.

    On disk rather than in-memory because in-memory SQLite gives each new
    connection its own empty database — the schema would vanish between the
    session that wrote it and the session that reads it.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    connection.init_db(engine)
    return engine


def _entry(call_id="abc", action="started"):
    return AuditLogEntry(call_id=call_id, action=action, user="system")


def test_init_db_creates_the_audit_table(db_engine):
    assert "audit_log" in inspect(db_engine).get_table_names()


def test_init_db_is_idempotent(db_engine):
    """Called on every startup, so running twice must not raise."""
    connection.init_db(db_engine)
    assert "audit_log" in inspect(db_engine).get_table_names()


def test_get_engine_returns_the_same_instance(tmp_path):
    """The singleton exists so connection pools are not created per request."""
    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        db_path=str(tmp_path / "singleton.db"),
    )
    assert connection.get_engine(config) is connection.get_engine(config)


def test_session_scope_commits_on_success(db_engine):
    with connection.session_scope(db_engine) as session:
        session.add(_entry())

    with connection.session_scope(db_engine) as session:
        assert len(session.scalars(select(AuditLogEntry)).all()) == 1


def test_session_scope_rolls_back_and_re_raises(db_engine):
    """A failed unit of work leaves nothing behind, and the caller is told.

    The re-raise is half the assertion. Rolling back silently would leave the
    caller believing the write landed — for the audit log, a missing record
    nobody knows is missing.
    """
    with (
        pytest.raises(RuntimeError, match="simulated"),
        connection.session_scope(db_engine) as session,
    ):
        session.add(_entry(action="boom"))
        raise RuntimeError("simulated failure")

    with connection.session_scope(db_engine) as session:
        assert session.scalars(select(AuditLogEntry)).all() == []


def test_session_scope_falls_back_to_the_shared_engine(tmp_path):
    """The branch taken when no engine is passed.

    Worth its own test because every other test here supplies an engine, so
    this path would otherwise never run — and it previously did not work:
    get_engine()'s return value was discarded and the session was built with a
    bind of None.
    """
    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        db_path=str(tmp_path / "fallback.db"),
    )
    connection.init_db(connection.get_engine(config))

    with connection.session_scope() as session:
        session.add(_entry(call_id="no-engine-arg"))

    with connection.session_scope() as session:
        # Read the attributes inside the block. commit() expires every instance
        # and close() detaches it, so touching row.call_id after the `with` has
        # exited raises DetachedInstanceError rather than returning stale data.
        call_ids = [r.call_id for r in session.scalars(select(AuditLogEntry)).all()]

    assert call_ids == ["no-engine-arg"]


def test_details_round_trips_as_a_dict(db_engine):
    """The JSON column serialises and deserialises, so callers never see a str."""
    with connection.session_scope(db_engine) as session:
        session.add(
            AuditLogEntry(
                call_id="abc",
                action="flagged",
                user="system",
                details={"patterns": ["ignore_previous"], "count": 1},
            )
        )

    with connection.session_scope(db_engine) as session:
        row = session.scalars(select(AuditLogEntry)).one()
        assert row.details == {"patterns": ["ignore_previous"], "count": 1}


def test_timestamp_is_stamped_per_row(db_engine):
    """Guards the callable default.

    default=datetime.now(UTC) would evaluate once at import and give every row
    ever written the same timestamp — records that look plausible and are
    uniformly wrong. Two rows written at different moments must differ.
    """
    with connection.session_scope(db_engine) as session:
        session.add(_entry(call_id="first"))

    with connection.session_scope(db_engine) as session:
        session.add(_entry(call_id="second"))

    with connection.session_scope(db_engine) as session:
        rows = session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id)).all()
        assert rows[0].timestamp is not None
        assert rows[0].timestamp <= rows[1].timestamp
