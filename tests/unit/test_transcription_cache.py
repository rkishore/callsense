"""
Unit tests for the SHA-256 transcription cache in src/agents/transcription.py.

Deliberately a separate file from test_transcription.py, whose autouse fixture
stubs the cache out — these tests need the real functions.
"""

from unittest import mock

from src.agents import transcription
from src.database import connection
from src.utils.config import Config
from tests.conftest import make_intake, make_segments_info


def test_compute_audio_hash_is_content_addressed(tmp_path):
    """Identical bytes hash the same however they are named.

    This is the premise the cache rests on. A re-uploaded recording always
    arrives as a fresh temp file with a different name, so a digest that took
    the filename into account would never hit — the cache would look correct
    and never fire.

    The length assertion pins the String(64) column. A SHA-256 hex digest is 64
    characters; SQLite ignores declared lengths, so an undersized column would
    pass every local test and truncate on Postgres.
    """
    a = tmp_path / "a.mp3"
    a.write_bytes(b"same content")

    b = tmp_path / "b.mp3"
    b.write_bytes(b"same content")

    c = tmp_path / "c.mp3"
    c.write_bytes(b"different content")

    digest_a = transcription._compute_audio_hash(a)
    digest_b = transcription._compute_audio_hash(b)
    digest_c = transcription._compute_audio_hash(c)

    assert digest_a == digest_b
    assert digest_a != digest_c
    assert len(digest_a) == len(digest_b) == len(digest_c) == 64
    # Stable across calls — the chunked read must leave no state behind.
    assert digest_a == transcription._compute_audio_hash(a)


def test_second_call_with_same_audio_returns_cached_result(tmp_path):
    """Milestone 3's own self-check, and the reason the cache exists.

    Two separate IntakeResults share one audio path deliberately — that models
    two different calls uploading the same recording, which is the whole premise
    of content-addressing. Reusing one intake and mutating its call_id would
    test the same code path while describing something that never happens.

    Each assertion does a distinct job:

      transcribe called once   the milestone's self-check, and the only thing
                               that distinguishes a cache hit from work that
                               merely happened to be fast
      call_ids differ          the substitution actually ran; without this a
                               stored call_id could be returned unnoticed
      call_id is the second    it is the *current* call's id, not some other one
      full_text matches        the cached transcript is what came back, not an
                               empty result that skipped transcription

    Only Whisper is mocked. The file, the SHA-256, SQLite and the Pydantic
    round trip are all real, so this exercises the whole path end to end.
    """
    audio = tmp_path / "call.mp3"
    audio.write_bytes(b"test content")

    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        db_path=str(tmp_path / "fallback.db"),
    )
    connection.init_db(connection.get_engine(config))

    (fake_segments, fake_info) = make_segments_info()
    intake_result1 = make_intake(temp_path=audio)
    intake_result2 = make_intake(temp_path=audio)

    with mock.patch("src.agents.transcription._get_whisper_model") as mock_get_model:
        mock_get_model.return_value.transcribe.return_value = (fake_segments, fake_info)
        result1 = transcription.run_transcription(intake_result1)
        result2 = transcription.run_transcription(intake_result2)
        # The point of this test: the function used the model once.
        mock_get_model.return_value.transcribe.assert_called_once()

        assert result2.call_id != result1.call_id  # substitution actually happened
        assert result2.call_id == intake_result2.call_id
        assert result2.full_text == result1.full_text  # same transcript came back
