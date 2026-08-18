"""
SQLAlchemy models — the persistence schema.

Three tables, deliberately not one: they have different keys, different write
patterns and different lifecycles. `call_records` is written once per completed
call and read by the observability metrics; `audit_log` is append-only and
written several times per call; `transcription_cache` is content-addressed by
audio hash and has an N:1 relationship to calls, since the same recording
analysed twice should transcribe once.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Column widths, named rather than repeated. SQLite ignores declared lengths
# entirely, so an undersized column here passes every local test and truncates
# only once this reaches a database that enforces them.
UUID_LEN = 36  # str(uuid.UUID) — the pipeline's call_id and LangSmith trace ids
SHA256_HEX_LEN = 64  # a SHA-256 hex digest, not a UUID; the first draft said 36
FILENAME_LEN = 255  # conventional filesystem limit; Gradio temp names are long
ENUM_LEN = 50  # longest StrEnum value, with room
USER_LEN = 100  # audit_log actor — "system" today, a real account later


class Base(DeclarativeBase):
    """Registry for every table in the project.

    Each model subclassing this registers itself on `Base.metadata`, so
    `init_db()` creates the whole schema with one `create_all()` call rather
    than naming tables individually.
    """


class AuditLogEntry(Base):
    """One immutable record of something the pipeline did to a call.

    Append-only — but nothing here enforces that, and SQLite will happily accept
    an UPDATE. What enforces it is `AuditLogger` exposing `log()` and nothing
    else: no update path, no delete path. The guarantee lives in the API rather
    than the schema, which is worth knowing before trusting it.

    `call_id` is indexed and deliberately *not* unique. One call produces
    several entries over its life — started, completed, flagged — and "show me
    everything that happened to this call" is the query this table exists to
    answer. That is the opposite of `call_records`, where `call_id` is unique.

    `timestamp` takes a callable default. `default=datetime.now(UTC)` would
    evaluate once at import and stamp every row ever written with the moment the
    module was loaded — records that look plausible and are uniformly wrong.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String(UUID_LEN), index=True)

    action: Mapped[str] = mapped_column(String(ENUM_LEN))
    user: Mapped[str] = mapped_column(String(USER_LEN))

    timestamp: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    # JSON rather than Text: SQLAlchemy serialises on the way in and
    # deserialises on the way out, so callers hand over a dict and read back a
    # dict. Text would work too, but every reader would have to remember a
    # json.loads. The only reason to prefer Text is needing a canonical
    # byte-for-byte form (sort_keys) for hashing or signing, which we do not.
    details: Mapped[dict | None] = mapped_column(JSON, default=None)


class TranscriptionCache(Base):
    """One transcript per distinct piece of audio, keyed by content.

    The key is the audio's SHA-256, not a call_id — so two different calls that
    upload the same recording share one row and it transcribes once. That N:1
    relationship is the point: at the spec's 5,000 calls a day, re-running
    Whisper over a recording already transcribed is the difference between a
    system that scales and one that does not.

    audio_hash is unique here, which is the opposite of audit_log.call_id and
    for the opposite reason: there is exactly one transcript per distinct audio,
    where there are many audit entries per call.

    transcription_json is Text rather than JSON because the blob is a Pydantic
    model's own serialisation — the reader wants model_validate_json to parse
    it, not SQLAlchemy handing back a dict that would have to be re-serialised
    before Pydantic could validate it.

    64 characters because a SHA-256 hex digest is 64 characters. SQLite ignores
    declared lengths, so an undersized column here would pass every local test
    and truncate silently on Postgres.
    """

    __tablename__ = "transcription_cache"

    id: Mapped[int] = mapped_column(primary_key=True)

    audio_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LEN), unique=True, index=True)

    transcription_json: Mapped[str] = mapped_column(Text())

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class CallRecord(Base):
    """The durable record of one analysed call.

    call_id is unique here — one record per call — which is exactly the opposite
    of audit_log, where it is deliberately not unique because a single call
    produces several entries over its life. The two tables answer different
    questions: "what is the outcome of this call?" against "what happened to
    this call?".

    The four _json columns are Text rather than JSON for the same reason as
    transcription_json: they hold Pydantic serialisations, and the reader wants
    model_validate_json to parse the string rather than SQLAlchemy handing back
    a dict that would have to be re-serialised before Pydantic could validate
    it.

    transcript_text is stored redacted. It is written after stage 4, so what
    persists is what the LLM saw — never the raw transcript.

    trace_id is optional because LangSmith tracing may be off. A null here means
    untraced, not failed.
    """

    __tablename__ = "call_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String(UUID_LEN), unique=True, index=True)

    status: Mapped[str] = mapped_column(String(ENUM_LEN))
    audio_filename: Mapped[str] = mapped_column(String(FILENAME_LEN))

    transcript_text: Mapped[str] = mapped_column(Text())
    summary_json: Mapped[str] = mapped_column(Text())
    qa_scores_json: Mapped[str] = mapped_column(Text())
    report_json: Mapped[str] = mapped_column(Text())

    processed_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    trace_id: Mapped[str | None] = mapped_column(String(UUID_LEN), default=None)
