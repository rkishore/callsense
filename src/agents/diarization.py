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

from typing import NamedTuple

from src.graph.state import SpeakerRole


class Utterance(NamedTuple):
    """One transcript segment, as the diarizer sees it.

    Deliberately narrower than TranscriptionSegment: diarization needs only the
    text and the timings, and taking the full model would mean inventing a
    confidence the diarizer never computed.
    """

    text: str
    start: float
    end: float


class SpeakerDiarizer:
    """Assigns Agent/Customer labels to a call's utterances."""

    def assign(self, segments: list[Utterance]) -> list[SpeakerRole]:
        """Label each utterance, in order.

        Args:
            segments: The call's utterances, chronological. Timings are used
                for the gap signal, so order matters.

        Returns:
            One SpeakerRole per input segment, same length and order. The
            caller zips these onto its own TranscriptionSegments.

        Not yet implemented: content patterns and gap switching, so every
        segment currently comes back as Agent.
        """
        return [SpeakerRole.AGENT for _ in segments]
