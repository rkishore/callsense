"""
Engine lifecycle and the session context manager.

The engine is a process-wide singleton for the same reason the Whisper model is:
it owns a connection pool, so building one per request leaks connections until
SQLite refuses to open more.

Sessions are the opposite — short-lived, one per unit of work, never shared.
`session_scope()` exists so no call site has to remember the commit / rollback /
close sequence, and so the one that forgets `rollback` cannot leave a poisoned
session behind.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.database.models import Base
from src.utils.config import Config, load_config

_engine: Engine | None = None


def get_engine(config: Config | None = None) -> Engine:
    """Return the process-wide engine, creating it on first call.

    Args:
        config: Injectable so tests need not depend on the environment. Read
            only when the engine does not yet exist — on a cache hit the config
            is never touched.

    Returns:
        The shared Engine. Same instance every call.
    """
    global _engine

    if _engine is None:
        if config is None:
            config = load_config()
        _engine = create_engine(f"sqlite:///{config.db_path}")

    return _engine


def init_db(engine: Engine) -> None:
    """Create any table that does not exist yet.

    Safe to call on every startup: create_all() skips tables already present, so
    this is idempotent rather than destructive.

    Takes the engine as an argument rather than reaching for the singleton, so
    tests can build a schema in a tmp_path database without touching it.
    """
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine | None = None):
    """Yield a session, committing on success and rolling back on failure.

    Args:
        engine: Defaults to the process-wide engine. Tests pass a tmp_path one.

    Yields:
        A Session, valid only inside the `with` block.

    @contextmanager turns this generator into something usable with `with`:
    everything before the yield is setup, everything after is teardown, and the
    caller's block runs *at* the yield. If that block raises, the exception is
    thrown back into this function at the yield point — which is why the try has
    to wrap it rather than merely follow it.

    The re-raise after rollback is not optional. Swallowing it would leave the
    caller believing a write succeeded, which for the audit log means a missing
    record nobody knows is missing.
    """
    if engine is None:
        engine = get_engine()

    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
