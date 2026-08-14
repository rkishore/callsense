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
    """Two different PII types in one string, both replaced.

    The exact-output assertion is doing the real work. Measured: a left-to-right
    implementation produces

        'My card is [REDACTED_CREDIT_CARD] and you can email me [REDACTED_EMAIL]com.'

    which passes every membership and absence check below — both placeholders
    are present and neither original value survives, because the mis-aligned cut
    destroyed them. Only comparing the whole string catches the eaten "at " and
    the orphaned "com.".
    """
    result = detect_and_redact_pii(DUAL_PATTERN_PAYLOAD)
    assert isinstance(result, PIIRedactionResult)
    assert result.pii_detected is True
    assert result.pii_count == 2
    assert set(result.pii_types) == {"CREDIT_CARD", "EMAIL"}
    assert (
        result.redacted_text
        == "My card is [REDACTED_CREDIT_CARD] and you can email me at [REDACTED_EMAIL]."
    )
    assert "4111-1111-1111-1111" not in result.redacted_text
    assert "john.smith@example.com" not in result.redacted_text


def test_overlapping_matches_are_deduped():
    result = detect_and_redact_pii(OVERLAPPING_PAYLOAD)
    assert result.pii_detected is True
    assert result.pii_count == 1
    assert result.pii_types == ["CREDIT_CARD"]
    assert "4111111111111111" not in result.redacted_text


def test_second_match_is_not_left_partially_intact():
    """The regression test for left-to-right replacement.

    Two SSNs in one string, both 11 characters, both replaced by a 14-character
    placeholder. Each replacement therefore grows the string by 3 and slides
    everything after it 3 to the right.

        SSN 123-45-6789 and 987-65-4321 done
            ^--(4,15)--^    ^--(20,31)-^

    Right-to-left: editing (20, 31) first cannot move anything below index 20,
    so (4, 15) is still accurate when its turn comes.

        'SSN [REDACTED_SSN] and [REDACTED_SSN] done'

    Left-to-right: editing (4, 15) first pushes the second SSN from 20 to 23,
    but the stored offset still says 20. The cut lands three characters early,
    swallows " and 987-65-4" and leaves "321" behind.

        'SSN [REDACTED_SSN] a[REDACTED_SSN]321 done'

    That output has two placeholders and a plausible shape, so a test asserting
    only that "[REDACTED_SSN]" appears would pass while three digits of a real
    SSN sit in text bound for a third-party LLM. Asserting the *absence* of the
    original values is what catches it — and the trailing fragment is why
    checking for the whole value is not enough on its own.
    """
    result = detect_and_redact_pii("SSN 123-45-6789 and 987-65-4321 done")

    assert result.pii_count == 2
    assert result.redacted_text == "SSN [REDACTED_SSN] and [REDACTED_SSN] done"
    # Each of these fails on the left-to-right implementation, and the fragment
    # check is the one that fails for the interesting reason.
    assert "987-65-4321" not in result.redacted_text
    assert "321" not in result.redacted_text
