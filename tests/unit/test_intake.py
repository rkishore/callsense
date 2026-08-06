"""
Unit tests for the intake step in src/agents/intake.py

PII in caller_id sets pii_scan.pii_detected=True,
two run_intake calls generate different UUIDs,
Pydantic ValidationError for out-of-range confidence and score fields.

"""

from pathlib import Path

import pytest

from src.agents.intake import run_intake, scan_for_pii
from src.graph.state import AudioInput
from tests.conftest import make_ogg_bytes, make_silent_wav_bytes, make_wav_bytes


@pytest.fixture
def intake():
    """Call run_intake and remove any temp file it created."""
    created = []

    def _run(**kwargs):
        result = run_intake(AudioInput(**kwargs))
        if result.temp_path:
            created.append(Path(result.temp_path))
        return result

    yield _run
    for path in created:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("audio_data", "filename", "expected_passed", "expected_error_substring"),  # parameter names
    [
        pytest.param(make_wav_bytes(5.0), "valid.wav", True, None, id="valid WAV"),
        pytest.param(b"", "empty.wav", False, "Empty file", id="empty file"),
        pytest.param(
            make_ogg_bytes(), "invalid.ogg", False, "Unsupported", id="unsupported format"
        ),
        pytest.param(
            make_silent_wav_bytes(3601.0, 8000), "long.wav", False, "duration", id="over-60-minutes"
        ),
    ],
)
def test_intake_accepts_and_rejects(
    intake, audio_data, filename, expected_passed, expected_error_substring
):
    intake_result = intake(audio_data=audio_data, filename=filename)
    assert intake_result.validation_passed == expected_passed
    if not intake_result.validation_passed:
        assert expected_error_substring in intake_result.validation_error


def test_two_calls_get_different_call_ids(intake):
    """call_id is generated per invocation, not per file."""
    audio = make_wav_bytes(1.0)
    first = intake(audio_data=audio, filename="call.wav")
    second = intake(audio_data=audio, filename="call.wav")

    assert first.call_id != second.call_id


def test_pii_in_caller_id_is_detected(intake):
    """Metadata is scanned before it is stored, separately from the transcript."""
    result = intake(
        audio_data=make_wav_bytes(1.0),
        filename="call.wav",
        caller_id="SSN: 123-45-6789",
    )

    assert result.validation_passed is True
    assert result.pii_scan.pii_detected is True
    assert "ssn" in result.pii_scan.pii_types


def test_clean_metadata_reports_no_pii(intake):
    result = intake(
        audio_data=make_wav_bytes(1.0),
        filename="call.wav",
        caller_id="agent-42",
        department="billing",
    )

    assert result.pii_scan.pii_detected is False
    assert result.pii_scan.pii_types == []


def test_temp_file_is_written_with_audio_bytes(intake):
    """The path must point at a real file holding the audio.

    Asserting existence alone is not enough — an earlier version built a path
    string and never wrote to it, which every other assertion happily passed.
    """
    audio = make_wav_bytes(2.0)
    result = intake(audio_data=audio, filename="call.wav")

    temp_path = Path(result.temp_path)
    assert temp_path.exists()
    assert temp_path.read_bytes() == audio
    assert temp_path.suffix == ".wav"


def test_failed_intake_writes_no_temp_file(intake):
    """temp_path is set exactly when validation passed."""
    result = intake(audio_data=b"", filename="empty.wav")

    assert result.validation_passed is False
    assert result.temp_path is None


@pytest.mark.parametrize(
    ("value", "expected_types"),
    [
        pytest.param("jane@example.com", ["email"], id="email"),
        pytest.param("123-45-6789", ["phone", "ssn"], id="ssn-also-matches-phone"),
        pytest.param("+1 555 123 4567", ["phone"], id="phone"),
        pytest.param("billing", [], id="clean"),
    ],
)
def test_scan_for_pii_patterns(value, expected_types):
    """SSN and phone deliberately overlap — an SSN satisfies the phone regex too."""
    result = scan_for_pii(caller_id=value, department=None)

    assert result.pii_types == expected_types
    assert result.pii_detected is bool(expected_types)
