"""
Unit tests for audio utility functions in src/utils/audio.py.
"""

import pytest

from src.utils.audio import (
    AudioValidationError,
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
        # so detection succeeds and only the parse fails.
        pytest.param(make_flac_bytes(), "flac", id="flac-header-without-stream"),
        pytest.param(make_mp3_bytes(), "mp3", id="mp3-sync-without-frames"),
        pytest.param(make_m4a_bytes(), "m4a", id="m4a-ftyp-without-atoms"),
        pytest.param(make_ogg_bytes(16), "ogg", id="format-we-do-not-support"),
    ],
)
def test_extract_audio_properties_raises_on_unreadable_input(payload, audio_format):
    with pytest.raises(AudioValidationError):
        extract_audio_properties(payload, audio_format)
