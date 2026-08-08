"""
Unit tests for transcription functions in src/agents/transcription.py.
"""

import sys
import types
from unittest import mock

import pytest

from src.agents import transcription


@pytest.fixture(autouse=True)
def reset_whisper_singleton():
    """Clear the module singleton around every test in this file.

    Reset before so a test never inherits a model another test loaded, and
    after so this file leaves no WhisperModel behind for later modules.
    Deliberately not monkeypatch.setattr, which restores the prior value —
    here the prior value is exactly what we want gone.
    """
    transcription._model = None
    transcription._model_size = None
    yield
    transcription._model = None
    transcription._model_size = None


def test_model_is_loaded_once():
    assert transcription._get_whisper_model("tiny") is transcription._get_whisper_model("tiny")


def _fake_torch(cuda: bool, mps: bool):
    """Minimal stand-in exposing only what _detect_device touches."""
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
    )


@pytest.mark.parametrize(
    ("torch_module", "expected"),
    [
        pytest.param(None, ("cpu", "int8"), id="no-torch"),
        pytest.param(_fake_torch(cuda=True, mps=False), ("cuda", "float16"), id="cuda"),
        pytest.param(
            _fake_torch(cuda=False, mps=True), ("cpu", "int8"), id="mps-falls-back-to-cpu"
        ),
        pytest.param(_fake_torch(cuda=False, mps=False), ("cpu", "int8"), id="torch-but-no-gpu"),
    ],
)
def test_detect_device_returns_expected_tuple(torch_module, expected):
    with mock.patch.dict(sys.modules, {"torch": torch_module}):
        assert transcription._detect_device() == expected
