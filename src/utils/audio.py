"""
Utility functions for audio file format detection and validation.

This module provides functions to detect the format of audio files by inspecting their magic
bytes, and to validate audio files based on size, duration, and format constraints.
It supports WAV, MP3, FLAC, and M4A formats. The module raises AudioValidationError for corrupt
or unreadable files.
"""

import io
import wave
from dataclasses import dataclass

from mutagen import File, MutagenError

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_DURATION_SECONDS = 3600  # 1 hour
SUPPORTED_FORMATS = ("wav", "mp3", "flac", "m4a")


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    error: str | None


class AudioValidationError(Exception):
    """Custom exception for audio validation errors."""

    pass


def detect_audio_format(header: bytes) -> str | None:
    """
    Detect the audio format of a given file by inspecting the first 12 bytes only (magic bytes).

    It must identify formats by inspecting the first 12 bytes only (magic bytes):
    - RIFF + WAVE at bytes 0-11 → wav,
    - ID3 header or 0xFF+0xE0 sync bits → mp3,
    - fLaC at bytes 0-3 → flac,
    - ftyp at bytes 4-7 → m4a.

    Don't rely on file extensions.

    Args:
        header (bytes): The first 12 bytes of the audio file.

    Returns:
        str | None: The detected audio format ('wav', 'mp3', 'flac', 'm4a'),
            or None if the format is unsupported or the header is too short.

    """
    if len(header) < 12:
        return None

    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    elif header.startswith(b"ID3") or (header[0] & 0xFF == 0xFF and (header[1] & 0xE0) == 0xE0):
        return "mp3"
    elif header.startswith(b"fLaC"):
        return "flac"
    elif header[4:8] == b"ftyp":
        return "m4a"
    else:
        return None


def validate_audio_file(audio_data: bytes, file_path: str) -> ValidationResult:
    """
    Validate the audio file. Reject files that are empty, larger than 50MB, or in an
    unsupported format.

    Return a ValidationResult dataclass with is_valid and error fields.

    Args:
        audio_data (bytes): The audio file bytes.
        file_path (str): The path to the audio file.
    """
    if len(audio_data) == 0:
        return ValidationResult(is_valid=False, error=f"Empty file: {file_path}")

    if len(audio_data) < 12:
        return ValidationResult(
            is_valid=False,
            error=f"File is too small to determine format: {file_path}",
        )

    if len(audio_data) > MAX_FILE_SIZE_BYTES:
        return ValidationResult(
            is_valid=False,
            error=(
                f"File is too large (exceeds maximum size of "
                f"{MAX_FILE_SIZE_BYTES} bytes): {file_path}"
            ),
        )

    header = audio_data[:12]
    audio_format = detect_audio_format(header)
    if not audio_format:
        return ValidationResult(is_valid=False, error=f"Unsupported audio format: {file_path}")

    return ValidationResult(is_valid=True, error=None)


def extract_audio_properties(audio_data: bytes, audio_format: str) -> dict:
    """
    Extract properties of the audio file, such as format and size.

    Use the wave module's wave.open() for WAV files to read frame count, sample rate,
    and channel count.
    Use mutagen (MP3, FLAC, MP4 classes) for other formats.
    Raise AudioValidationError on corrupt or unreadable files.

    Args:
        audio_data (bytes): The audio file bytes.
        audio_format (str): The detected audio format.
    """
    with io.BytesIO(audio_data) as audio_file:
        if audio_format == "wav":
            try:
                with wave.open(audio_file, "rb") as wav_file:
                    frame_count = wav_file.getnframes()
                    sample_rate = wav_file.getframerate()
                    duration_seconds = frame_count / float(sample_rate)
                    return {
                        "format": audio_format,
                        "size_bytes": len(audio_data),
                        "duration_seconds": duration_seconds,
                        "sample_rate": sample_rate,
                        "channels": wav_file.getnchannels(),
                    }
            except wave.Error as e:
                raise AudioValidationError(f"Corrupt or unreadable WAV file: {e}") from e
            except EOFError as e:
                raise AudioValidationError("Unexpected EOF in WAV file: truncated data") from e

        elif audio_format in ("mp3", "flac", "m4a"):
            # Only the parse is guarded: a MutagenError here means the bytes are bad.
            # Everything after runs unguarded so a bug in our own code surfaces as a bug
            # rather than being reported to the user as corrupt audio.
            try:
                audio = File(audio_file)
            except (MutagenError, EOFError) as e:
                raise AudioValidationError(
                    f"Corrupt or unreadable {audio_format.upper()} file: {e}"
                ) from e

            if audio is None:
                raise AudioValidationError(
                    f"Unrecognised {audio_format.upper()} file: no audio stream found."
                )

            return {
                "format": audio_format,
                "size_bytes": len(audio_data),
                "duration_seconds": audio.info.length,
                "sample_rate": getattr(audio.info, "sample_rate", None),
                "channels": getattr(audio.info, "channels", None),
            }

        else:
            raise AudioValidationError(f"Unsupported audio format: {audio_format}")
