"""
Transcription node in langgraph with heuristics-based speaker diarization,
SHA-256 caching, prompt injection detector and PII redactor.
"""

from faster_whisper import WhisperModel

from src.utils.config import get_logger

logger = get_logger(__name__)

_model: WhisperModel | None = None
_model_size: str | None = None


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

    # Check the cache
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
