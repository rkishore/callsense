"""
Unit tests for audio utility functions in src/utils/audio.py.
"""

import pytest

from src.utils.audio import (
    AudioValidationError,
    _wav_rejection_reason,
    detect_audio_format,
    extract_audio_properties,
    validate_audio_file,
)
from tests.conftest import (
    make_flac_bytes,
    make_id3_mp3_bytes,
    make_m4a_bytes,
    make_mp3_bytes,
    make_ogg_bytes,
    make_silent_wav_bytes,
    make_wav_bytes,
)


@pytest.mark.parametrize(
    ("payload", "expected"),  # parameter names
    [
        pytest.param(make_wav_bytes(), "wav", id="wav"),
        pytest.param(make_mp3_bytes(), "mp3", id="mp3"),
        pytest.param(make_id3_mp3_bytes(), "mp3", id="id3-mp3"),
        pytest.param(make_flac_bytes(), "flac", id="flac"),
        pytest.param(make_m4a_bytes(), "m4a", id="m4a"),
        pytest.param(make_ogg_bytes(), None, id="ogg"),  # OGG is unsupported
        pytest.param(b"", None, id="empty"),  # Empty header
        pytest.param(b"\xff", None, id="single-sync-byte"),
        pytest.param(b"RIFF", None, id="truncated-riff"),
    ],
)
def test_detect_audio_format(payload, expected):
    assert detect_audio_format(payload) == expected


def test_rejects_empty_file():
    result = validate_audio_file(b"", "empty.wav")
    assert not result.is_valid
    assert "Empty file" in result.error


def test_extracts_audio_properties_wav():
    properties = extract_audio_properties(make_wav_bytes(5.0, 16000), "wav")
    assert properties["format"] == "wav"
    assert 4.9 <= properties["duration_seconds"] <= 5.1
    assert properties["sample_rate"] == 16000
    assert properties["channels"] == 1


def test_rejects_oversized_file(oversized_bytes):
    result = validate_audio_file(oversized_bytes, "large.wav")
    assert not result.is_valid
    assert "exceeds maximum" in result.error


@pytest.mark.parametrize(
    ("payload", "audio_format"),
    [
        # Each payload carries a valid magic-byte header but no decodable stream,
        # so the format is recognised and only the decode fails.
        pytest.param(make_flac_bytes(), "flac", id="flac-header-without-stream"),
        pytest.param(make_mp3_bytes(), "mp3", id="mp3-sync-without-frames"),
        pytest.param(make_m4a_bytes(), "m4a", id="m4a-ftyp-without-atoms"),
        pytest.param(make_wav_bytes()[:20], "wav", id="wav-truncated"),
    ],
)
def test_extract_audio_properties_raises_on_unreadable_input(payload, audio_format):
    with pytest.raises(AudioValidationError):
        extract_audio_properties(payload, audio_format)


def test_extract_audio_properties_rejects_unknown_format():
    """Dispatch is on the format argument, so the payload is never inspected."""
    with pytest.raises(AudioValidationError, match="Unsupported audio format"):
        extract_audio_properties(b"", "ogg")


@pytest.mark.parametrize(
    ("payload", "expected_substring"),
    [
        pytest.param(make_silent_wav_bytes(3601.0, 8000), "duration", id="over-60-minutes"),
        pytest.param(make_wav_bytes()[:20], "truncated", id="truncated-header"),
        pytest.param(make_wav_bytes(2.0), None, id="acceptable-wav"),
    ],
)
def test_wav_rejection_reason(payload, expected_substring):
    """Exercised directly, not only through validate_audio_file.

    All three outcomes come from the same wave.open() call: an over-length file
    reads fine and fails the duration comparison, a truncated one raises
    EOFError, and a malformed one raises wave.Error.
    """
    reason = _wav_rejection_reason(payload)

    if expected_substring is None:
        assert reason is None
    else:
        assert reason is not None
        assert expected_substring in reason


def test_validate_prefers_duration_over_size():
    """A 3601s WAV breaks both limits; the more specific message must win."""
    payload = make_silent_wav_bytes(3601.0, 8000)
    assert len(payload) > 52_428_800  # genuinely oversized too

    error = validate_audio_file(payload, "long.wav").error
    assert "duration" in error
    assert "exceeds maximum" not in error


def test_validate_prefers_size_over_format(oversized_bytes):
    """50MB of non-audio breaks both limits; size is the more specific message."""
    error = validate_audio_file(oversized_bytes, "big.wav").error
    assert "exceeds maximum" in error
    assert "Unsupported" not in error
