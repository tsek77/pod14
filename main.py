"""
main.py — Түүхэн Подкаст Үүсгэгч
Gradio web app, RunPod дээр ажиллана.

Эхлүүлэх:  python main.py
RunPod дээр: GRADIO_SERVER_NAME=0.0.0.0 python main.py

Засагдсан алдаа (2025):
  - [FIX-4] create_output_tab(shared) → create_output_tab(config, shared)
"""

import os
import sys
import logging
import gradio as gr

from config import Config
from ui.settings_tab import create_settings_tab
from ui.pipeline_tab import create_pipeline_tab
from ui.output_tab   import create_output_tab

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ── Константууд ───────────────────────────────────────────────────────────────
APP_TITLE = "🎬 Түүхэн Подкаст Үүсгэгч"

CUSTOM_CSS = """
.gradio-container {
    max-width: 1280px !important;
    margin: 0 auto;
    font-family: 'Inter', sans-serif;
}
.app-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 12px;
    padding: 24px 32px;
    margin-bottom: 20px;
    border: 1px solid #e94560;
}
.app-header h1 { color: #e94560; margin: 0 0 8px 0; font-size: 1.8rem; }
.app-header p  { color: #a8b2d8; margin: 0; font-size: 0.95rem; }
.tab-nav { border-bottom: 2px solid #0f3460; }
.tab-nav button.selected {
    border-bottom: 2px solid #e94560 !important;
    color: #e94560 !important;
}
.status-ok   { color: #4ade80; font-weight: 600; }
.status-warn { color: #facc15; font-weight: 600; }
.status-err  { color: #f87171; font-weight: 600; }
#pipeline-log textarea {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    background: #0d0d1a;
    color: #7dd3fc;
    border: 1px solid #1e3a5f;
}
"""


def create_app() -> gr.Blocks:
    """
    Gradio Blocks app үүсгэж буцаана.
    .env байхгүй ч safe_mode-оор нормал нээгдэнэ.
    """
    try:
        config = Config()
        logger.info("Config амжилттай ачаалагдлаа.")
    except Exception as exc:
        logger.warning(f"Config алдаа: {exc} — default утгаар ажиллана.")
        config = Config(safe_mode=True)

    with gr.Blocks(
        title=APP_TITLE,
    ) as app:

        # ── Header ──────────────────────────────────────────────────────────
        with gr.Row(elem_classes="app-header"):
            gr.HTML(f"""
            <h1>{APP_TITLE}</h1>
            <p>
              Түүхэн сэдэвтэй скриптээс <strong>синематик подкаст видео</strong> үүсгэх систем&nbsp;|&nbsp;
              Скрипт → Inworld TTS-2 → Grok промпт → Flux.1 зураг → InfiniteTalk → 1080p Видео
            </p>
            """)

        # ── Shared state: pipeline outputs (дараах tab-д дамжуулна) ─────────
        shared = {
            "audio_path":    gr.State(None),
            "video_path":    gr.State(None),
            "subtitle_path": gr.State(None),
            "log_text":      gr.State(""),
            "chunks_info":   gr.State([]),
        }

        # ── Tabs ────────────────────────────────────────────────────────────
        with gr.Tabs(elem_classes="tab-nav"):

            with gr.Tab("⚙️  Тохиргоо & API", id="tab_settings"):
                create_settings_tab(config)

            with gr.Tab("📝  Скрипт & Pipeline", id="tab_pipeline"):
                create_pipeline_tab(config, shared)

            with gr.Tab("🎥  Гаралт & Татах", id="tab_output"):
                # [FIX-4] config параметр нэмсэн — өмнө зөвхөн (shared) байсан
                create_output_tab(config, shared)              # ← FIX-4

        # ── Footer ──────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="text-align:center;color:#4a5568;font-size:.75rem;padding:16px 0 8px">
          RunPod &middot; Inworld TTS-2 &middot; Grok xAI &middot;
          Flux.1 Klein &middot; InfiniteTalk &middot; FFmpeg
        </div>
        """)

    return app


def main():
    host  = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    port  = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    share = os.environ.get("GRADIO_SHARE", "false").lower() == "true"

    logger.info(f"Gradio app эхэлж байна →  http://{host}:{port}")

    app = create_app()
    app.queue(
        max_size=5,
        default_concurrency_limit=1,
    )
    app.launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        theme=gr.themes.Base(
            primary_hue=gr.themes.colors.red,
            secondary_hue=gr.themes.colors.blue,
            neutral_hue=gr.themes.colors.slate,
            font=[gr.themes.GoogleFont("Inter"), "sans-serif"],
        ),
        css=CUSTOM_CSS,
    )


if __name__ == "__main__":
    main()
