import gradio as gr
from langgraph.graph.state import CompiledStateGraph

from src.services.pipeline import process_call
from src.utils.config import Config


def show_status():
    return gr.update(
        visible=True,
        value="⏳ Processing… roughly 30s for a 5-minute call. Please do not refresh.",
    ), gr.update(interactive=False)


def hide_status():
    return gr.update(visible=False), gr.update(interactive=True)


def build_analyze_tab(graph: CompiledStateGraph, config: Config):
    with gr.Tab("Analyze Call"):
        audio = gr.Audio(type="filepath", sources=["upload", "microphone"], label="Call recording")
        caller_id = gr.Textbox(label="Caller ID (optional)")
        department = gr.Textbox(label="Department (optional)")

        btn = gr.Button("Analyze Call", variant="primary")
        status = gr.Markdown(visible=False)
        transcript = gr.Textbox(lines=15, show_copy_button=True, label="Transcript")

        with gr.Row():
            summary_md = gr.Markdown(label="Summary")
            qa_md = gr.Markdown(label="QA Scorecard")

        json_file = gr.File(label="Report in JSON")

        def analyze(audio_path, caller, dept):
            if not audio_path:
                raise gr.Error("Please upload a recording first.")
            try:
                result = process_call(audio_path, graph, config, caller or None, dept or None)
            except Exception as exc:
                raise gr.Error(f"Could not analyze this recording: {exc}") from exc
            json_path = str(result.json_path) if result.json_path else None
            return result.transcript, result.summary, result.qa, json_path

        btn.click(show_status, outputs=[status, btn]).then(
            analyze,
            inputs=[audio, caller_id, department],
            outputs=[transcript, summary_md, qa_md, json_file],
        ).then(hide_status, outputs=[status, btn])
