"""
Stage 4 — PII detection and redaction over the transcribed text.

Runs after injection detection and before any LLM call, because the LLM is a
third party: once a credit card number is in a prompt it has left the building,
and no downstream handling can undo that. Redaction is therefore the last thing
that happens while the data is still ours.

Applies to the full transcript and, at the caller, to every individual segment —
both are read downstream, and a segment still holding an SSN would leak it
through the UI transcript even if `full_text` were clean.
"""

import re

from src.graph.state import PIIRedactionResult
from src.utils.config import get_logger

logger = get_logger(__name__)

# The [-.\s]? separator class is what gives one pattern several format
# variants: 123-45-6789, 123 45 6789, 123.45.6789 and 123456789 all match the
# SSN entry. Validated against the reference corpus, where three of the ten
# calls contain a spoken phone number — one hyphenated, two space-separated.
#
# CREDIT_CARD ends on \d rather than \b: "(?:\d[ -]?){13,19}" lets the final
# repetition swallow a trailing space, so the redaction would eat the gap and
# produce "[REDACTED_CREDIT_CARD]and".
PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "SSN"),
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "CREDIT_CARD"),
    (re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
    (re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "PHONE"),
]


def detect_and_redact_pii(text: str) -> PIIRedactionResult:
    """Replace every PII match with a labelled placeholder.

    Args:
        text: Transcript text, already checked for prompt injection.

    Returns:
        PIIRedactionResult. `redacted_text` is always populated — equal to the
        input when nothing matched — so callers can assign it unconditionally
        rather than writing `result.redacted_text or original`, which is one
        forgotten `or` away from blanking a transcript.

    The four steps below are order-critical, and getting any of them wrong
    corrupts output silently rather than raising:

    1. Collect every match across every pattern **from the original text**.
       Redacting between patterns would leave later patterns scanning a string
       of a different length, describing a document that no longer exists.
    2. Sort by start ascending, longest first on a tie. Ascending rather than
       the spec's descending because step 3's "keep the earlier start" rule then
       falls out of the ordering; step 4 takes reversed() instead. Longest-first
       matters because a 16-digit run matches CREDIT_CARD and its tail matches
       PHONE — and over-redacting is the safe direction, so the tie breaks
       towards the longer span.
    3. Drop overlaps. Right-to-left replacement protects against offset drift
       between *disjoint* matches; it does nothing for two matches claiming the
       same characters, which corrupt in either direction.
    4. Replace right-to-left. Each edit only changes text to the right of its
       start, so every offset still to be used — all of them to the left — stays
       valid. Left-to-right invalidates every offset behind the first edit, and
       because "[REDACTED_CREDIT_CARD]" is longer than the number it replaces,
       later spans land early and can leave a fragment of the next value intact.
    """
    matches = []
    for pattern, pii_type in PII_PATTERNS:
        for m in pattern.finditer(text):
            matches.append((m.start(), m.end(), pii_type))

    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

    logger.debug(matches)

    # dedupe
    kept = []
    last_end = -1
    for start, end, pii_type in matches:
        if start >= last_end:
            kept.append((start, end, pii_type))
            last_end = end

    redacted_text = text
    for start, end, pii_type in reversed(kept):
        redacted_text = redacted_text[:start] + f"[REDACTED_{pii_type}]" + redacted_text[end:]

    return PIIRedactionResult(
        pii_detected=bool(kept),
        pii_types=sorted({pii_type for _, _, pii_type in kept}),
        pii_count=len(kept),
        redacted_text=redacted_text,
    )
