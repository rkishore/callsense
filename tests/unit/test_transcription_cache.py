"""
Unit tests for the SHA-256 transcription cache in src/agents/transcription.py.

Deliberately a separate file from test_transcription.py, whose autouse fixture
stubs the cache out — these tests need the real functions.
"""

from src.agents import transcription


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
