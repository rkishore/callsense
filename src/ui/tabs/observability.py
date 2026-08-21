import gradio as gr

from src.services.observability import AUDIT_COLUMNS, get_observability_dashboard


def build_observability_tab():
    with gr.Tab("Observability") as tab:
        metrics_md = gr.Markdown()
        langsmith_md = gr.Markdown()
        audit_df = gr.Dataframe(headers=AUDIT_COLUMNS, wrap=True)

        tab.select(get_observability_dashboard, outputs=[metrics_md, langsmith_md, audit_df])
