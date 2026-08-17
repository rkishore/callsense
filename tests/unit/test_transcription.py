"""
Unit tests for transcription functions in src/agents/transcription.py.
"""

import sys
import types
import uuid
from unittest import mock

import pytest

from src.agents import transcription
from src.agents.intake import _EMPTY_AUDIO_PROPS, _EMPTY_PII
from src.graph.state import IntakeResult, SpeakerRole
from src.utils.config import Config


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


@pytest.fixture(autouse=True)
def stub_the_cache():
    """Take the SHA-256 cache out of play for every test in this file.

    These tests are about transcription, not caching, and without this each one
    would need a real audio file on disk for _compute_audio_hash to read and a
    database for _check_cache to query. Neither has anything to do with what
    they assert.

    _compute_audio_hash is stubbed as well as the two lookups, so no file is
    touched at all — patching only _check_cache would still leave the hash
    reading intake_result.temp_path.

    The cache's own behaviour is covered in test_transcription_cache.py, which
    does not inherit this fixture.
    """
    with (
        mock.patch("src.agents.transcription._compute_audio_hash", return_value="stub-hash"),
        mock.patch("src.agents.transcription._check_cache", return_value=None),
        mock.patch("src.agents.transcription._save_cache"),
    ):
        yield


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


def _fake_segment(text, start, end, avg_logprob=-0.27, no_speech_prob=0.32):
    return types.SimpleNamespace(
        text=text,
        start=start,
        end=end,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
    )


def _high_conf_segment(text=" high", start=0.0, end=1.0):
    # avg_logprob=0.0 -> clamp(1 + 0.0) = 1.0 -> 1.0*0.7 + 1.0*0.3 = 1.00
    return _fake_segment(text=text, start=start, end=end, avg_logprob=0.0, no_speech_prob=0.0)


def _low_conf_segment(text=" low", start=0.0, end=1.0):
    # avg_logprob=-1.0 -> clamp(1 + -1.0) = 0.0 -> 0.0*0.7 + 1.0*0.3 = 0.30
    return _fake_segment(text=text, start=start, end=end, avg_logprob=-1.0, no_speech_prob=0.0)


def _fake_info():
    return types.SimpleNamespace(duration=120, duration_after_vad=120)


def _make_segments_info():
    fake_segments = [
        _fake_segment("Hello world!", 0.0, 3.5),
        _fake_segment("I love you!", 3.6, 4.0),
        _fake_segment("Do you like me?", 4.1, 4.5),
    ]

    fake_info = _fake_info()

    return (fake_segments, fake_info)


def _make_intake_result():
    intake_result = IntakeResult(
        call_id=uuid.uuid4(),
        validation_passed=True,
        pii_scan=_EMPTY_PII,
        audio_properties=_EMPTY_AUDIO_PROPS,
        temp_path="/tmp/does-not-matter.wav",
    )

    return intake_result


def test_transcription_threads_the_call_id():
    (fake_segments, fake_info) = _make_segments_info()
    intake_result = _make_intake_result()

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, fake_info)
        result = transcription.run_transcription(intake_result)
        assert result.call_id == intake_result.call_id


def test_transcription_uses_the_whisper_model():
    (fake_segments, fake_info) = _make_segments_info()
    intake_result = _make_intake_result()

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, fake_info)
        transcription.run_transcription(intake_result)
        # The point of this test: the function actually used the model, rather
        # than returning a plausible-looking object built from nothing.
        mock_get_model.return_value.transcribe.assert_called_once()


def test_transcription_preserves_segment_text():
    (fake_segments, fake_info) = _make_segments_info()
    intake_result = _make_intake_result()

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, fake_info)
        result = transcription.run_transcription(intake_result)

    assert len(result.segments) == len(fake_segments)
    assert result.segments[0].text == "Hello world!"
    # The invariant the per-segment cleaning exists to guarantee: full_text is
    # derived from the segments, so the two can never disagree.
    assert result.full_text == " ".join(s.text for s in result.segments if s.text)


@pytest.mark.parametrize(
    ("n_high", "n_low", "expected_ratio", "expected_flagged"),
    [
        # Config below sets threshold 0.6 and halt ratio 0.8. High segments
        # score 1.00, low segments score 0.30, so the split is unambiguous.
        pytest.param(3, 1, 0.25, False, id="mixed"),
        pytest.param(4, 0, 0.0, False, id="all-high"),
        pytest.param(0, 4, 1.0, True, id="all-low"),
        # VAD can filter a silent file down to nothing — must not divide by zero
        pytest.param(0, 0, 0.0, False, id="no-segments"),
    ],
)
def test_low_confidence_ratio_and_flag(n_high, n_low, expected_ratio, expected_flagged):
    """Injecting a config keeps this independent of whatever .env says."""
    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        confidence_threshold=0.6,
        low_confidence_halt_ratio=0.8,
    )

    fake_segments = [_high_conf_segment() for _ in range(n_high)]
    fake_segments += [_low_conf_segment() for _ in range(n_low)]
    intake_result = _make_intake_result()

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, _fake_info())
        result = transcription.run_transcription(intake_result, config)

    assert result.low_confidence_ratio == pytest.approx(expected_ratio)
    assert result.flagged_low_confidence is expected_flagged


def test_transcription_assigns_speakers_from_cleaned_text():
    """The diarizer is called, and it is called with cleaned text.

    The [BLANK_AUDIO] marker in segment 1 is load-bearing, not incidental
    realism. "thank you so much" cannot match while the marker sits in the
    middle of it, so the raw text scores no customer pattern; cleaning strips
    the marker and collapses the double space, and then it matches:

        raw text     -> [Agent, Agent, Agent]
        cleaned text -> [Agent, Customer, Customer]

    So the label sequence alone proves cleaning happens before diarization —
    no need to inspect the diarizer's arguments. Delete the marker and this
    test still passes, but it stops checking the thing it exists for.

    Asserting the whole sequence rather than "speaker is not None" also catches
    a reversed or off-by-one zip, which is the failure mode of this wiring.
    Config is injected only to keep the test off whatever .env says.
    """
    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        confidence_threshold=0.6,
        low_confidence_halt_ratio=0.8,
    )

    fake_segments = [
        _fake_segment("Thank you for calling Acme.", 0.0, 2.0),
        _fake_segment("Thank you [BLANK_AUDIO] so much for your help.", 2.5, 4.0),
        _fake_segment("Okay.", 4.5, 5.0),
    ]

    intake_result = _make_intake_result()

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, _fake_info())
        result = transcription.run_transcription(intake_result, config)

    speaker_roles = [s.speaker for s in result.segments]
    assert speaker_roles == [SpeakerRole.AGENT, SpeakerRole.CUSTOMER, SpeakerRole.CUSTOMER]
    assert len(result.segments) == 3
