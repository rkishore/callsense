"""
Stage 1 — intake. The pipeline's gatekeeper.

Runs before any Whisper inference or LLM call, so a rejection here costs nothing
downstream. Note the ordering: WAV duration is read from the RIFF header *before*
the size gate, so an over-long recording reports its real problem rather than a
misleading "file too large".
"""

import re
import tempfile
import uuid

from src.graph.state import AudioInput, AudioProperties, IntakeResult, PIIScanResult
from src.utils.audio import (
    detect_audio_format,
    extract_audio_properties,
    validate_audio_file,
)

_EMPTY_PII = PIIScanResult(pii_detected=False, pii_types=[], pii_count=0)
_EMPTY_AUDIO_PROPS = AudioProperties(
    duration_seconds=0.0, size_bytes=0, sample_rate=0, channels=0, format=""
)


def _make_failed_result(call_id: uuid.UUID, error: str) -> IntakeResult:
    """Every failure path returns the same shape; only the message differs."""
    return IntakeResult(
        call_id=call_id,
        validation_passed=False,
        validation_error=error,
        pii_scan=_EMPTY_PII,
        audio_properties=_EMPTY_AUDIO_PROPS,
        temp_path=None,
    )


def scan_for_pii(caller_id: str | None, department: str | None) -> PIIScanResult:
    """Scan the caller_id and department for PII and return a PIIScanResult.

    Args:
        caller_id (str | None): The caller ID to scan for PII.
        department (str | None): The department to scan for PII.
    Returns:
        PIIScanResult: Whether PII was found, which types matched, and how many
            patterns matched. Note a single value can match more than one pattern
            (an SSN also satisfies the phone regex), so the count is of patterns,
            not of distinct values.
    """
    pii_detected = False
    pii_types = []
    pii_count = 0

    patterns = {
        "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
        "phone": r"\+?\d[\d -]{8,12}\d",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    }

    if caller_id:
        for pii_type, pattern in patterns.items():
            if re.search(pattern, caller_id):
                pii_detected = True
                pii_types.append(pii_type)
                pii_count += 1

    if department:
        for pii_type, pattern in patterns.items():
            if re.search(pattern, department):
                pii_detected = True
                pii_types.append(pii_type)
                pii_count += 1

    return PIIScanResult(pii_detected=pii_detected, pii_types=pii_types, pii_count=pii_count)


def run_intake(audio_input: AudioInput) -> IntakeResult:
    """
    Run the intake process for an incoming call.

    Args:
        audio_input (AudioInput): The audio input for the call.

    Returns:
        IntakeResult: Validation status, PII scan results, audio properties, and
            the temp file path — which is set only when validation passed.
    """

    # Assign a unique call ID for a new intake
    call_id = uuid.uuid4()

    # Validate the audio file
    validation_result = validate_audio_file(audio_input.audio_data, audio_input.filename)
    if not validation_result.is_valid:
        return _make_failed_result(call_id, validation_result.error)

    # extract_audio_properties returns a plain dict whose keys match AudioProperties
    audio_format = detect_audio_format(audio_input.audio_data[:12])
    audio_properties = AudioProperties(
        **extract_audio_properties(audio_input.audio_data, audio_format)
    )

    # Write valid audio to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_format}") as temp_file:
        temp_file.write(audio_input.audio_data)
        temp_path = temp_file.name

    # Scan for PII in caller_id and department
    pii_scan_result = scan_for_pii(audio_input.caller_id, audio_input.department)

    return IntakeResult(
        call_id=call_id,
        validation_passed=True,
        validation_error=None,
        pii_scan=pii_scan_result,
        audio_properties=audio_properties,
        temp_path=temp_path,
    )
