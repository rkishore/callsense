"""
Entrypoint. Thin by design — everything here is startup wiring.

Three things happen once, at boot, that would otherwise happen per request:
the database schema is created, the Whisper model is loaded, and the graph is
compiled. The Whisper load is the one that matters for the demo: it costs
5-30 seconds, and paying it inside the first upload makes a working system look
broken.
"""

import os

import gradio as gr

from src.agents.transcription import _get_whisper_model
from src.database.connection import get_engine, init_db
from src.graph.workflow import compile_workflow
from src.ui.tabs.analyze import build_analyze_tab
from src.utils.config import Config, get_logger, load_config

logger = get_logger(__name__)


def build_app(graph, config: Config) -> gr.Blocks:
    """Construct the UI without launching it.

    Separate from the launch so tests can build the interface without starting a
    server, and so the Observability tab has an obvious place to go.
    """
    with gr.Blocks(title="callsense") as demo:
        gr.Markdown("# callsense\nCall centre intelligence — transcript, summary and QA scoring.")
        build_analyze_tab(graph, config)

    return demo


def main() -> None:
    config = load_config()

    engine = get_engine(config)
    init_db(engine)
    logger.info("Database ready at %s", config.db_path)

    # Warm the singleton now rather than inside the first request.
    logger.info("Loading Whisper model size=%s", config.whisper_model_size)
    _get_whisper_model(config.whisper_model_size)

    graph = compile_workflow(config, engine)

    # HuggingFace Spaces sets SPACE_ID and requires binding to all interfaces;
    # locally, loopback only.
    on_spaces = bool(os.environ.get("SPACE_ID"))
    server_name = "0.0.0.0" if on_spaces else "127.0.0.1"  # noqa: S104
    logger.info("Starting Gradio on %s:7860 (spaces=%s)", server_name, on_spaces)

    build_app(graph, config).launch(server_name=server_name, server_port=7860)


if __name__ == "__main__":
    main()
