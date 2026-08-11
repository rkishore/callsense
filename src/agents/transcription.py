"""
Stage 2 — transcription. Local faster-whisper inference plus artifact cleaning
and per-segment confidence.

The model is a process-wide singleton because loading costs 5-30s and the spec
names per-request loading as a top mistake.

Not here yet: speaker diarization, and the SHA-256 cache (which needs the
database layer from M5). Prompt-injection detection and PII redaction are
separate stages and live in src/security/.
"""

import re

from faster_whisper import WhisperModel

from src.graph.state import IntakeResult, TranscriptionResult, TranscriptionSegment
from src.utils.config import Config, get_logger, load_config

logger = get_logger(__name__)

_model: WhisperModel | None = None
_model_size: str | None = None

# Literal strings, not patterns — several contain regex metacharacters
# ("[" opens a character class, "(" opens a group), so they are escaped before
# use. Unescaped, "[BLANK_AUDIO]" deletes every B/A/N/K/_/U/D/I/O in the
# transcript rather than the tag itself.
MARKERS = (
    "[BLANK_AUDIO]",
    "[BLANK _AUDIO]",
    "thanks for watching",
    "thank you for watching",
    "(music)",
    "(applause)",
    "♪",
)

# Runs of four or more dots — three is ordinary punctuation, longer runs are
# degenerate generation.
_ELLIPSIS_RUN = re.compile(r"\.{4,}")

# A repeated phrase, e.g. "thank you thank you thank you" -> "thank you".
# The group captures 1-5 words and the backreference requires the same words
# again immediately after, so it catches phrase-level repetition as well as a
# single stuttered word. The {0,4} bound keeps backtracking cheap.
_REPEATED_PHRASE = re.compile(r"\b(\w+(?:\s+\w+){0,4})(?:\s+\1\b)+", flags=re.IGNORECASE)

# Collapse any whitespace left behind once markers are removed.
_EXTRA_SPACE = re.compile(r"\s{2,}")


def _detect_device() -> tuple[str, str]:
    """
    Detect local inference hardware: CPU, GPU

    Note: on Macs, don't use the local GPU and use the CPU instead
    """
    try:
        import torch
    except ImportError:
        return ("cpu", "int8")
    else:
        if torch.cuda.is_available():
            return ("cuda", "float16")

        # Use the CPU even if MAC GPU is present
        if torch.backends.mps.is_available():
            return ("cpu", "int8")

    return ("cpu", "int8")


def _get_whisper_model(model_size: str) -> WhisperModel:
    """
    Load the Whisper model once and reuse it for the process lifetime.

    Loading costs 5-30s, so app.py warms this at startup and every request
    reuses the instance. Rebuilt only when a different size is requested.
    """
    global _model, _model_size

    if _model is None or _model_size != model_size:
        device, compute_type = _detect_device()
        logger.info(
            "Loading Whisper model size=%s device=%s compute_type=%s",
            model_size,
            device,
            compute_type,
        )
        _model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _model_size = model_size

    return _model


def _clean_transcript_text(full_text: str) -> str:
    """Strip the artifacts Whisper produces on silence, music and dead air.

    Whisper is a generative model trained on web audio, so on ambiguous input it
    emits plausible-looking text rather than nothing: YouTube sign-offs,
    non-speech labels, and degenerate repetition.

    Args:
        full_text: Raw joined transcript text.

    Returns:
        The same text with artifacts removed and whitespace tidied.
    """
    for marker in MARKERS:
        # re.escape treats the marker as literal characters rather than a pattern
        full_text = re.sub(re.escape(marker), "", full_text, flags=re.IGNORECASE)

    full_text = _ELLIPSIS_RUN.sub("...", full_text)
    full_text = _REPEATED_PHRASE.sub(r"\1", full_text)
    full_text = _EXTRA_SPACE.sub(" ", full_text)

    return full_text.strip()


def run_transcription(
    intake_result: IntakeResult, config: Config | None = None
) -> TranscriptionResult:
    """Stage 2 — turn the intake's audio file into a typed transcript.

    Runs after intake has validated the audio and written it to a temp file.
    Artifacts are cleaned per segment rather than on the joined text, so
    `full_text` and `segments` can never disagree — both are consumed
    downstream, `full_text` by PII redaction and `segments` by the LLM prompt
    and the UI.

    Args:
        intake_result: Stage-1 output. Supplies `temp_path` and the `call_id`
            that every later stage carries.
        config: Injectable so tests need not depend on the environment.

    Returns:
        TranscriptionResult with the same call_id as the intake.

    Not yet implemented: the SHA-256 cache (needs the database layer, M5) and
    speaker diarization, so every segment's speaker is None.
    """
    if config is None:
        config = load_config()

    model = _get_whisper_model(config.whisper_model_size)

    segments, info = model.transcribe(
        str(intake_result.temp_path),
        beam_size=1,
        language="en",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    segments = list(segments)  # transcription runs here as model.transcribe returns a generator

    # Clean each segment, then derive full_text from the cleaned texts. Doing it
    # the other way round leaves segments holding raw text while full_text is
    # clean, and both are read downstream.
    cleaned_texts = [_clean_transcript_text(s.text) for s in segments]

    # Cleaning strips the leading space Whisper puts on every segment, so join
    # with " ". Empty segments are kept (a silent stretch may matter later) but
    # skipped here, or they would inject double spaces.
    full_text = " ".join(text for text in cleaned_texts if text)

    confs = [
        max(0.0, min(1.0, 1 + s.avg_logprob)) * 0.7 + (1 - s.no_speech_prob) * 0.3 for s in segments
    ]

    transcription_segments = [
        TranscriptionSegment(
            text=cleaned_texts[idx],
            start_time=float(s.start),
            end_time=float(s.end),
            confidence=confs[idx],
            speaker=None,  # set by the diarizer, not yet built
        )
        for idx, s in enumerate(segments)
    ]

    num_segments_below_threshold = sum(1 for c in confs if c < config.confidence_threshold)
    low_confidence_ratio = num_segments_below_threshold / len(confs) if confs else 0.0
    flagged_low_confidence = low_confidence_ratio > config.low_confidence_halt_ratio

    result = TranscriptionResult(
        call_id=intake_result.call_id,
        full_text=full_text,
        segments=transcription_segments,
        low_confidence_ratio=low_confidence_ratio,
        flagged_low_confidence=flagged_low_confidence,
    )

    return result
