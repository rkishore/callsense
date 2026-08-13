import time
from pathlib import Path

import gradio as gr


def show_status():
    return gr.update(
        visible=True,
        value="⏳ Processing… roughly 30s for a 5-minute call. Please do not refresh.",
    )


def do_work(audio_path):
    time.sleep(2)  # stands in for the seven-stage pipeline
    return f"done: {audio_path}"


def hide_status():
    return gr.update(visible=False)


def inspect(value):
    lines = [f"type: {type(value)}"]
    if isinstance(value, tuple):
        sr, arr = value
        lines += [f"sample_rate: {sr}", f"ndarray shape: {arr.shape}", f"dtype: {arr.dtype}"]
    elif isinstance(value, str):
        data = Path(value).read_bytes()
        lines += [f"path: {value}", f"bytes: {len(data)}", f"first 12: {data[:12]!r}"]
    return "\n".join(lines)


with gr.Blocks() as demo:
    with gr.Tab("T1"):
        audio = gr.Audio(type="numpy", label="numpy")
        report = gr.Textbox(label="what arrived", lines=12)
        audio.change(inspect, inputs=audio, outputs=report)
    with gr.Tab("T2"):
        audio2 = gr.Audio(type="filepath", sources=["upload", "microphone"], label="filepath")
        report2 = gr.Textbox(label="what arrived", lines=12)
        audio2.change(inspect, inputs=audio2, outputs=report2)

    status = gr.Markdown(visible=False)
    result = gr.Textbox(label="result")
    btn = gr.Button("Analyze Call", variant="primary")

    btn.click(show_status, outputs=status).then(do_work, inputs=audio2, outputs=result).then(
        hide_status, outputs=status
    )

demo.launch()
