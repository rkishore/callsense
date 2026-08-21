"""Shared pytest fixtures and helpers.

Test scaffolding only — no production logic lives here.
"""

from __future__ import annotations

import io
import math
import struct
import types
import uuid
import wave

import pytest
from sqlalchemy import create_engine

from src.agents.intake import _EMPTY_AUDIO_PROPS, _EMPTY_PII
from src.database import connection
from src.graph.state import (
    QA_DIMENSIONS,
    ComplianceFlag,
    IntakeResult,
    QADimensionScore,
    QAScoreResult,
    ResolutionStatus,
    SummaryResult,
    TranscriptionResult,
    TranscriptionSegment,
)
from src.utils.config import Config


def make_wav_bytes(
    duration: float = 5.0,
    sample_rate: int = 16000,
    channels: int = 1,
    freq: float = 440.0,
) -> bytes:
    """Generate a valid in-memory WAV file.

    Used throughout the unit tests so no real audio file is ever needed. The milestone
    self-checks assume this helper exists, e.g.::

        detect_audio_format(make_wav_bytes()) == "wav"
        extract_audio_properties(make_wav_bytes(5.0, 16000), "wav")  # duration ~5.0

    A sine tone is written rather than silence so the bytes are not trivially
    compressible and the file looks like real audio to anything inspecting it.

    Args:
        duration: Length in seconds. Values above 3600 are useful for exercising the
            MAX_DURATION_SECONDS gate without producing a large file (16 kHz mono for
            one hour is ~115 MB, so prefer a low sample_rate when testing long audio).
        sample_rate: Frames per second.
        channels: 1 for mono, 2 for stereo.
        freq: Tone frequency in Hz.

    Returns:
        Complete WAV file bytes, including the 44-byte RIFF/WAVE header.
    """
    n_frames = int(duration * sample_rate)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)  # 16-bit PCM
        wav.setframerate(sample_rate)

        frames = bytearray()
        for i in range(n_frames):
            value = int(32767.0 * 0.3 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            frames.extend(struct.pack("<h", value) * channels)
        wav.writeframes(bytes(frames))

    return buf.getvalue()


def make_silent_wav_bytes(
    duration: float = 5.0,
    sample_rate: int = 8000,
    channels: int = 1,
) -> bytes:
    """Generate a silent WAV cheaply.

    Much faster than :func:`make_wav_bytes` for long durations because no per-frame
    math runs — use this when exercising the duration gate (e.g. 3601 seconds).
    """
    n_frames = int(duration * sample_rate)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * n_frames * channels)

    return buf.getvalue()


def make_mp3_bytes(payload_size: int = 1024) -> bytes:
    """Generate bytes whose first 12 bytes look like an MP3 frame sync.

    Not a playable MP3 — enough to exercise magic-byte detection only.
    """
    return b"\xff\xfb\x90\x00" + b"\x00" * payload_size


def make_id3_mp3_bytes(payload_size: int = 1024) -> bytes:
    """Bytes carrying an ID3v2 header, the other MP3 detection path."""
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * payload_size


def make_flac_bytes(payload_size: int = 1024) -> bytes:
    """Bytes carrying the fLaC magic at offset 0."""
    return b"fLaC" + b"\x00" * payload_size


def make_m4a_bytes(payload_size: int = 1024) -> bytes:
    """Bytes carrying the ftyp box at offset 4."""
    return b"\x00\x00\x00\x20ftypM4A " + b"\x00" * payload_size


def make_ogg_bytes(payload_size: int = 1024) -> bytes:
    """An unsupported format — should be rejected by validation."""
    return b"OggS\x00\x02" + b"\x00" * payload_size


@pytest.fixture
def wav_bytes() -> bytes:
    """A short, valid mono WAV."""
    return make_wav_bytes(duration=2.0, sample_rate=16000)


@pytest.fixture
def oversized_bytes() -> bytes:
    """One byte over the 50 MB limit."""
    return b"\x00" * (52_428_800 + 1)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path):
    """Give every test a complete, disposable environment.

    Several code paths call load_config() internally rather than taking an
    injected Config — transcription_node calls run_transcription(state["intake"])
    with no config, for instance, which is correct in production where .env
    exists. On a machine without one, eight tests failed with ConfigError, and a
    clean clone is exactly what a reviewer runs.

    They passed here only because the developer's own .env happened to be
    present, which also meant the suite behaved differently depending on which
    provider that .env named. Setting the values makes the suite hermetic in
    both directions.

    DB_PATH points at tmp_path for the same reason. Anything falling back to the
    process-wide engine then writes to a database thrown away after the test —
    the integration suite previously put 21 rows of its own failures into the
    real data/calls.db that way.

    monkeypatch restores the prior environment afterwards, so this leaks nothing
    into the shell that ran pytest.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test-fallback.db"))


@pytest.fixture(autouse=True)
def reset_engine_singleton():
    """Clear the engine singleton around every test in the suite.

    autouse and defined in conftest, so this runs for every test everywhere —
    not only the database ones. That is deliberate: a test that points the
    singleton at a tmp_path database must not leave it there, and the cost to
    tests that never touch a database is two attribute assignments.

    Same shape and same reason as reset_whisper_singleton in
    test_transcription.py, which stays file-local because loading a Whisper
    model is expensive enough to be worth confining.
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


def fake_segment(text, start, end, avg_logprob=-0.27, no_speech_prob=0.32):
    return types.SimpleNamespace(
        text=text,
        start=start,
        end=end,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
    )


def fake_info():
    return types.SimpleNamespace(duration=120, duration_after_vad=120)


def make_segments_info():
    fake_segments = [
        fake_segment("Hello world!", 0.0, 3.5),
        fake_segment("I love you!", 3.6, 4.0),
        fake_segment("Do you like me?", 4.1, 4.5),
    ]

    return (fake_segments, fake_info())


# ── Model builders ───────────────────────────────────────────────────────────
# Plain functions, imported — not fixtures. They take arguments, so they cannot
# be fixtures, and the same distinction applies as with make_wav_bytes.
#
# Every parameter has a working default so a test overrides only what it is
# actually testing. make_qa_scores() bare is a valid all-3s result, which is what
# lets a test that does not care about scores ignore them entirely.


def make_config(**overrides) -> Config:
    """A Config that needs no .env and no real key."""
    defaults = {
        "llm_provider": "openai",
        "openai_api_key": "sk-test",
        "confidence_threshold": 0.6,
        "low_confidence_halt_ratio": 0.8,
    }
    return Config(**{**defaults, **overrides})


def make_transcript(
    call_id: uuid.UUID | None = None,
    full_text: str = "Thanks for calling.",
    segments: list[TranscriptionSegment] | None = None,
) -> TranscriptionResult:
    """A minimal TranscriptionResult, one segment unless told otherwise."""
    if segments is None:
        segments = [
            TranscriptionSegment(text=full_text, start_time=0.0, end_time=2.0, confidence=0.9)
        ]
    return TranscriptionResult(
        call_id=call_id or uuid.uuid4(),
        full_text=full_text,
        segments=segments,
        low_confidence_ratio=0.0,
        flagged_low_confidence=False,
    )


def make_summary(call_id: uuid.UUID | None = None, **overrides) -> SummaryResult:
    """A minimal SummaryResult. Pass any field to override it."""
    defaults = {
        "call_purpose": "Dispute a charge.",
        "key_discussion_points": [],
        "action_items": [],
        "resolution_status": ResolutionStatus.RESOLVED,
        "sentiment_trajectory": "Concerned -> Reassured",
        "entities": [],
    }
    return SummaryResult(call_id=call_id or uuid.uuid4(), **{**defaults, **overrides})


def make_qa_scores(
    flags: list[ComplianceFlag] | tuple = (),
    overall_score: float = 3.0,
    call_id: uuid.UUID | None = None,
    justification: str = "Because.",
    **scores: int,
) -> QAScoreResult:
    """A QAScoreResult with every dimension at 3 unless named in **scores.

    Dimension names come from QA_DIMENSIONS rather than being hardcoded, so a
    dimension added there cannot leave this builder constructing an invalid
    model.

    call_id defaults to a *fresh* UUID, which is deliberate: this stands in for
    what an LLM returns, and the model always invents one. Tests asserting the
    pipeline overwrites it rely on that difference.
    """
    dimensions = dict.fromkeys(QA_DIMENSIONS, 3)
    dimensions.update(scores)
    return QAScoreResult(
        call_id=call_id or uuid.uuid4(),
        **{
            name: QADimensionScore(score=score, justification=justification)
            for name, score in dimensions.items()
        },
        overall_score=overall_score,
        compliance_flags=list(flags),
    )


def make_intake(
    validation_passed: bool = True,
    call_id: uuid.UUID | None = None,
    temp_path: str | None = "/tmp/does-not-matter.wav",
) -> IntakeResult:
    """A minimal IntakeResult with empty PII and audio-property records."""
    return IntakeResult(
        call_id=call_id or uuid.uuid4(),
        validation_passed=validation_passed,
        pii_scan=_EMPTY_PII,
        audio_properties=_EMPTY_AUDIO_PROPS,
        temp_path=temp_path,
    )
