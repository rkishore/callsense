"""
Configuration file for the CallSense project.
This module is imported by everything else, so it must have zero
dependencies on other project modules.
"""

import logging
import os
import sys
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


# ── Logger ───────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """Create a module-level logger with a readable format."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"
            )
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


logger = get_logger("config")

# ── Load .env ──────────────────────
load_dotenv(find_dotenv(usecwd=True))


class ConfigError(Exception):
    pass


# ── Defaults and allowed values ──────────────────────────────
# Single source of truth: referenced by the dataclass fields, by load_config(),
# and by the error messages below, so the three can never drift apart.
VALID_PROVIDERS = ("openai", "gemini", "groq")
VALID_WHISPER_SIZES = ("tiny", "base", "small", "medium", "large-v3")

DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_WHISPER_MODEL_SIZE = "tiny"
DEFAULT_DB_PATH = "data/calls.db"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_LOW_CONFIDENCE_HALT_RATIO = 0.8

MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 30.0, 600.0
MIN_RETRIES, MAX_RETRIES = 0, 10


@dataclass(frozen=True, kw_only=True)
class Config:
    # Required environment variables
    llm_provider: str
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    google_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    groq_api_key: str = ""
    groq_model: str = DEFAULT_GROQ_MODEL

    llm_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries_per_node: int = DEFAULT_MAX_RETRIES
    whisper_model_size: str = DEFAULT_WHISPER_MODEL_SIZE
    db_path: str = DEFAULT_DB_PATH

    # Optional environment variables can be added here as needed
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    low_confidence_halt_ratio: float = DEFAULT_LOW_CONFIDENCE_HALT_RATIO
    db_encryption_key: str = ""

    def __post_init__(self):
        if not self.llm_provider or self.llm_provider not in VALID_PROVIDERS:
            raise ConfigError(
                f"LLM_PROVIDER is missing or invalid. Copy .env.example → .env "
                f"and add {' | '.join(VALID_PROVIDERS)}."
            )
        else:
            if self.llm_provider == "openai" and (
                not self.openai_api_key or self.openai_api_key.startswith("your")
            ):
                raise ConfigError(
                    "OPENAI_API_KEY is missing or invalid. "
                    "Copy .env.example → .env and add your key."
                )
            elif self.llm_provider == "gemini" and (
                not self.google_api_key or self.google_api_key.startswith("your")
            ):
                raise ConfigError(
                    "GOOGLE_API_KEY is missing or invalid. "
                    "Copy .env.example → .env and add your key."
                )
            elif self.llm_provider == "groq" and (
                not self.groq_api_key or self.groq_api_key.startswith("your")
            ):
                raise ConfigError(
                    "GROQ_API_KEY is missing or invalid. Copy .env.example → .env and add your key."
                )

        if not MIN_TIMEOUT_SECONDS <= self.llm_timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ConfigError(
                f"LLM_TIMEOUT_SECONDS must be between {MIN_TIMEOUT_SECONDS} "
                f"and {MAX_TIMEOUT_SECONDS} seconds."
            )

        if not MIN_RETRIES <= self.max_retries_per_node <= MAX_RETRIES:
            raise ConfigError(
                f"MAX_RETRIES_PER_NODE must be between {MIN_RETRIES} and {MAX_RETRIES}."
            )

        if not self.whisper_model_size or self.whisper_model_size not in VALID_WHISPER_SIZES:
            raise ConfigError(
                f"WHISPER_MODEL_SIZE is missing or invalid. Copy .env.example → .env "
                f"and add {' | '.join(VALID_WHISPER_SIZES)}."
            )

        parent = os.path.dirname(self.db_path) or "."
        if not self.db_path or not os.path.isdir(parent):
            raise ConfigError(
                "DB_PATH is missing or invalid. Copy .env.example → .env "
                "and add a valid path to your database file."
            )

        if self.confidence_threshold < 0.0 or self.confidence_threshold > 1.0:
            raise ConfigError("CONFIDENCE_THRESHOLD must be between 0.0 and 1.0.")

        if self.low_confidence_halt_ratio < 0.0 or self.low_confidence_halt_ratio > 1.0:
            raise ConfigError("LOW_CONFIDENCE_HALT_RATIO must be between 0.0 and 1.0.")


def _env_float(name: str, default: float) -> float:
    """Get a float from the environment, or return a default."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{name} must be a number. Got: {value!r}. Check .env file.") from None


def _env_int(name: str, default: int) -> int:
    """Get an int from the environment, or return a default."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer. Got: {value!r}. Check .env file.") from None


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        llm_provider=os.environ.get("LLM_PROVIDER", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        google_api_key=os.environ.get("GOOGLE_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        max_retries_per_node=_env_int("MAX_RETRIES_PER_NODE", DEFAULT_MAX_RETRIES),
        whisper_model_size=os.environ.get("WHISPER_MODEL_SIZE", DEFAULT_WHISPER_MODEL_SIZE),
        db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH),
        confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD),
        low_confidence_halt_ratio=_env_float(
            "LOW_CONFIDENCE_HALT_RATIO", DEFAULT_LOW_CONFIDENCE_HALT_RATIO
        ),
        db_encryption_key=os.environ.get("DB_ENCRYPTION_KEY", ""),
    )
