"""Entrypoint. Startup wiring only: schema, Whisper warm-up, graph compile, launch.

The Whisper load happens here rather than per request: it costs 5-30 seconds, and
paying it inside the first upload makes a working system look broken.
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
    """Construct the UI without launching it, so tests need no server."""
    with gr.Blocks(title="callsense") as demo:
        gr.Markdown("# callsense\nCall centre intelligence — transcript, summary and QA scoring.")
        build_analyze_tab(graph, config)
    return demo


def main() -> None:
    config = load_config()

    engine = get_engine(config)
    init_db(engine)
    logger.info("Database ready at %s", config.db_path)

    logger.info("Loading Whisper model size=%s", config.whisper_model_size)
    _get_whisper_model(config.whisper_model_size)

    graph = compile_workflow(config, engine)

    # Loopback locally; containers and Spaces override it — see the Dockerfile.
    default_host = "0.0.0.0" if os.environ.get("SPACE_ID") else "127.0.0.1"  # noqa: S104
    server_name = os.environ.get("GRADIO_SERVER_NAME", default_host)
    logger.info("Starting Gradio on %s:7860", server_name)

    build_app(graph, config).launch(server_name=server_name, server_port=7860)


if __name__ == "__main__":
    main()
