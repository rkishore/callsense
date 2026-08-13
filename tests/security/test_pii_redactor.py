"""
Unit tests for pii redactor functions in src/security/pii_redactor.py.
"""

from src.graph.state import PIIRedactionResult
from src.security.pii_redactor import detect_and_redact_pii

CLEAN_TRANSCRIPT = "Thank you for calling Nissan. My name is Warren. Can I have your name?"
SINGLE_PATTERN_PAYLOAD = "Sure, my social security number is 123-45-6789."
DUAL_PATTERN_PAYLOAD = (
    "My card is 4111-1111-1111-1111 and you can email me at john.smith@example.com."
)
OVERLAPPING_PAYLOAD = "The number is 4111111111111111, got it?"


def test_clean_transcript_is_not_flagged():
    """
    Nothing in an ordinary service call should trip the detector.

    Weak on its own — a detector that always returns False passes it. Its job
    is to pin the return *contract*: a model, not a tuple or a bare dict.
    """
    result = detect_and_redact_pii(CLEAN_TRANSCRIPT)
    assert isinstance(result, PIIRedactionResult)
    assert result.pii_detected is False
    assert result.pii_count == 0
    assert result.pii_types == []
    assert result.redacted_text == CLEAN_TRANSCRIPT


def test_on_one_payload():
    result = detect_and_redact_pii(SINGLE_PATTERN_PAYLOAD)
    assert isinstance(result, PIIRedactionResult)
    assert result.pii_detected is True
    assert result.pii_count == 1
    assert result.pii_types == ["SSN"]
    assert "[REDACTED_SSN]" in result.redacted_text
    assert "123-45-6789" not in result.redacted_text


def test_two_matches_on_one_payload():
    result = detect_and_redact_pii(DUAL_PATTERN_PAYLOAD)
    assert isinstance(result, PIIRedactionResult)
    assert result.pii_detected is True
    assert result.pii_count == 2
    assert set(result.pii_types) == {"CREDIT_CARD", "EMAIL"}
    assert "[REDACTED_CREDIT_CARD]" in result.redacted_text
    assert "[REDACTED_EMAIL]" in result.redacted_text
    assert "4111-1111-1111-1111" not in result.redacted_text
    assert "john.smith@example.com" not in result.redacted_text


def test_overlapping_matches_are_deduped():
    result = detect_and_redact_pii(OVERLAPPING_PAYLOAD)
    assert result.pii_detected is True
    assert result.pii_count == 1
    assert result.pii_types == ["CREDIT_CARD"]
    assert "4111111111111111" not in result.redacted_text
