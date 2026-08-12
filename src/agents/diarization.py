"""
Speaker diarization — heuristic Agent/Customer labelling, no speaker model.

Two signals in priority order: content patterns first, then the timing gap
between segments. Both thresholds were measured across the ten reference calls
rather than assumed; see _spikes/README.md.

The priority order is not arbitrary. Content patterns match only ~11% of
segments, so they act as high-confidence *anchors* rather than labels; the
other ~89% inherits the current speaker until a gap flips it. And the gap alone
is unreliable — it fires on 3% of boundaries in a fast call and 38% in a slow
one, so it can correct a run but must never override an explicit match.

Kept separate from transcription.py so the heuristic can be swapped for a real
diarization model (pyannote) without touching the Whisper wrapper.
"""

import re
from typing import NamedTuple

from src.graph.state import SpeakerRole

# Chosen by measuring the ten reference calls, not by imagination — several
# obvious-sounding candidates ("I'm calling about", "I was charged", "can you
# verify") score zero hits across 787 segments. Two rules came out of that:
#
#   - Patterns must match what Whisper *outputs*, not what the agent said.
#     "assist you" scores 14 hits across 6 files; "how can I help" scores 1,
#     because Whisper mangles the help variants ("How can you help you today?").
#   - A phrase both speakers use is worthless however common it is. "my name
#     is" appears 13 times across 8 files and is excluded: agents introduce
#     themselves with it, and so do customers.
#
# \b boundaries rather than substring matching, so a short pattern cannot match
# inside a longer word. Compiled once at import; assign() runs per segment.
_AGENT_PATTERNS = (
    re.compile(r"\bthank you for calling\b", re.IGNORECASE),
    re.compile(r"\bassist you\b", re.IGNORECASE),
    re.compile(r"\bis there anything else\b", re.IGNORECASE),
    re.compile(r"\bi apologize\b", re.IGNORECASE),
    re.compile(r"\bmay i have\b", re.IGNORECASE),
    re.compile(r"\blet me check\b", re.IGNORECASE),
    re.compile(r"\bone moment\b", re.IGNORECASE),
)

_CUSTOMER_PATTERNS = (
    re.compile(r"\bi need\b", re.IGNORECASE),
    re.compile(r"\bi want\b", re.IGNORECASE),
    re.compile(r"\bmy account\b", re.IGNORECASE),
    re.compile(r"\bthank you so much\b", re.IGNORECASE),
)

GAP_THRESHOLD_SECONDS = 1.2


class Utterance(NamedTuple):
    """One transcript segment, as the diarizer sees it.

    Deliberately narrower than TranscriptionSegment: diarization needs only the
    text and the timings, and taking the full model would mean inventing a
    confidence the diarizer never computed.
    """

    text: str
    start: float
    end: float


def _matches(text: str, patterns: tuple[re.Pattern, ...]) -> bool:
    """Whether any pattern occurs anywhere in the text.

    search() rather than match(): match() anchors at position 0, so it would
    miss "that's exactly what I need" while still finding "Thank you for
    calling" — half the patterns appearing to work and half appearing broken.
    """
    return any(p.search(text) for p in patterns)


class SpeakerDiarizer:
    """Assigns Agent/Customer labels to a call's utterances."""

    def __init__(self, gap_threshold: float = GAP_THRESHOLD_SECONDS):
        self.gap_threshold = gap_threshold

    def _exceeds_threshold(self, prev_end: float, cur_start: float) -> bool:
        return (prev_end is not None) and ((cur_start - prev_end) > self.gap_threshold)

    def _flip(self, current):
        return SpeakerRole.CUSTOMER if current is SpeakerRole.AGENT else SpeakerRole.AGENT

    def assign(self, segments: list[Utterance]) -> list[SpeakerRole]:
        """Label each utterance, in order.

        Args:
            segments: The call's utterances, chronological. Timings are used
                for the gap signal, so order matters.

        Returns:
            One SpeakerRole per input segment, same length and order. The
            caller zips these onto its own TranscriptionSegments.

        A segment with no pattern match inherits the current speaker, which is
        the common case — content patterns match only ~11% of segments, so most
        of a transcript is labelled by propagation rather than by matching.
        The first segment defaults to Agent.

        Agent patterns are checked first, so a segment matching both resolves to
        Agent. Measured at 0 such segments in 787, so the rule almost never
        fires — but it is a decision, not an accident of branch order.

        A silence strictly longer than the threshold toggles the speaker. It is
        checked only after both pattern lists because it is the weaker signal:
        it fires on 3% of boundaries in a fast call and 38% in a slow one, so it
        can carry a run of unmatched segments but must never override an
        explicit match. Strictly greater, not >=, and a gap of 0.00 therefore
        never flips — Whisper's 30-second decode windows split mid-sentence at
        0.00, so zero carries no information.
        """
        current = SpeakerRole.AGENT
        results = []
        prev_end = None
        for s in segments:
            cur_start = s.start
            cur_end = s.end
            if _matches(s.text, _AGENT_PATTERNS):
                current = SpeakerRole.AGENT
            elif _matches(s.text, _CUSTOMER_PATTERNS):
                current = SpeakerRole.CUSTOMER
            elif self._exceeds_threshold(prev_end, cur_start):
                current = self._flip(current)

            prev_end = cur_end
            results.append(current)

        return results
