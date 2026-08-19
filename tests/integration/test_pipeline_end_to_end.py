"""
End-to-end tests over the compiled graph.

Integration rather than unit: these invoke the whole pipeline and assert on
where it ended up, not on what any one node returned.
"""

import uuid
from unittest import mock

from src.database import connection
from src.graph.state import (
    AudioInput,
    CallStatus,
    ComplianceFlag,
    QADimensionScore,
    QAScoreResult,
    ResolutionStatus,
    SummaryResult,
)
from src.graph.workflow import compile_workflow
from src.utils.config import Config
from tests.conftest import make_segments_info, make_wav_bytes


def test_invalid_audio_returns_failed_status(db_engine):
    """Unanalysable audio is rejected at intake and routed straight to error.

    The two absence assertions are doing the real work. status == FAILED alone
    would also pass on a pipeline that ran every stage, spent ninety seconds and
    two LLM calls, and failed at the very end — which is the opposite of what
    route_after_intake is for.

    "transcription" not in result is the stronger of the two: transcription is
    the first thing that would have run had routing gone the wrong way, and the
    most expensive.

    Nothing is mocked, because nothing beyond intake is ever reached. That is
    also why this half was written before the happy path.

    b"not audio" fails the length gate — "File is too small to determine format"
    — rather than the magic-byte check. b"\x00" * 2000 would exercise the
    unsupported-format path instead; both are valid rejections.
    """
    audio_input = AudioInput(audio_data=b"not audio", filename="call.wav")
    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        confidence_threshold=0.6,
        low_confidence_halt_ratio=0.8,
    )

    graph = compile_workflow(config, db_engine)
    result = graph.invoke({"audio_input": audio_input})

    assert result["status"] == CallStatus.FAILED
    assert "transcription" not in result
    assert "report" not in result


def _summary() -> SummaryResult:
    return SummaryResult(
        call_id=uuid.uuid4(),
        call_purpose="Dispute a charge.",
        key_discussion_points=[],
        action_items=[],
        resolution_status=ResolutionStatus.RESOLVED,
        sentiment_trajectory="Concerned -> Reassured",
        entities=[],
    )


def _qa_scores(flags: list[ComplianceFlag], **scores: int) -> QAScoreResult:
    return QAScoreResult(
        call_id=uuid.uuid4(),
        **{
            name: QADimensionScore(score=score, justification="Because.")
            for name, score in scores.items()
        },
        overall_score=3.0,
        compliance_flags=flags,
    )


def test_valid_audio_returns_completed_status(tmp_path):
    """The M5 milestone: a clean call runs the whole pipeline to a report.

    Exactly three things are mocked — Whisper, and get_llm in each of the two
    agents that import it. **Everything between them is real**: intake writes a
    temp file, the SHA-256 cache hashes and queries it, the diarizer labels
    segments, the injection detector scans, the PII redactor rewrites full_text
    and every segment, the summary is threaded into QA scoring, the overall
    score is recomputed in Python, and the report is assembled. A wiring
    mistake in any of the eight nodes surfaces here.

    get_llm is patched in both src.agents.summarization and
    src.agents.qa_scoring. Each module imported the name independently, so
    patching src.utils.llm_factory.get_llm would miss both — and one shared
    patch would hand a SummaryResult to the QA scorer.

    The state is an output, not an input. Only audio_input goes in; seeding
    summary or qa_scores would achieve nothing, since the node runs regardless
    and overwrites whatever was there.

    db_path is set on the injected Config and the singleton seeded from it,
    because the cache calls session_scope() with no engine and falls back to the
    process-wide one. A tmp_path engine that the pipeline never sees would leave
    two databases in play — the initialised one nothing uses, and the one the
    cache actually queries.

    The fake transcript text must not trip the injection detector, or
    route_after_injection diverts to supervisor review and this reads as a
    wiring bug. make_segments_info()'s text matches none of the 22 patterns.

    The call_id assertion is the one nothing else covers. Both LLM results
    carried invented UUIDs and both were overwritten, so this proves the
    pipeline's own identifier threaded all seven stages — and it is what would
    have caught the discarded model_copy in run_summarization.
    """

    audio_data = make_wav_bytes(duration=2.0, sample_rate=16000)
    audio_input = AudioInput(audio_data=audio_data, filename="call.wav")

    config = Config(
        llm_provider="openai",
        openai_api_key="sk-test",
        confidence_threshold=0.6,
        low_confidence_halt_ratio=0.8,
        db_path=str(tmp_path / "pipeline.db"),
    )

    db_engine = connection.get_engine(config)
    connection.init_db(db_engine)

    with (
        mock.patch("src.agents.transcription._get_whisper_model") as mock_whisper,
        mock.patch("src.agents.summarization.get_llm") as mock_summary_llm,
        mock.patch("src.agents.qa_scoring.get_llm") as mock_qa_llm,
    ):
        mock_whisper.return_value.transcribe.return_value = make_segments_info()

        mock_summary_llm.return_value.with_structured_output.return_value.invoke.return_value = (
            _summary()
        )

        mock_qa_llm.return_value.with_structured_output.return_value.invoke.return_value = (
            _qa_scores(
                [],
                professionalism=5,
                empathy=5,
                problem_resolution=5,
                compliance=5,
                communication_clarity=5,
            )
        )

        graph = compile_workflow(config, db_engine)
        result = graph.invoke({"audio_input": audio_input})

    assert result["status"] == CallStatus.COMPLETED
    assert result["report"] is not None
    assert result["report"].call_id == result["intake"].call_id
    assert result["report"].call_id == result["intake"].call_id
