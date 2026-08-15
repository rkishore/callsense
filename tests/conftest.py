"""Shared pytest fixtures and helpers.

Test scaffolding only — no production logic lives here.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest
from sqlalchemy import create_engine

from src.database import connection


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
