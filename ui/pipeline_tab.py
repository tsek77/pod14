"""
ui/pipeline_tab.py — Скрипт оруулах, Pipeline ажиллуулах Gradio UI

Онцлог:
  - Скрипт текст оруулах (том textarea)
  - "Chunk харах" — TextChunker-ийн preview
  - "Pipeline эхлүүлэх" — бүрэн pipeline streaming дэвшил
  - Progress bar + log textarea (монospace, streaming update)
  - Алхам бүрийн статус badge (✅ / ⏳ / ❌)
  - Pipeline дуусвал shared state-г шинэчилнэ (output_tab-д дамжуулна)
  - API key байхгүй бол анхааруулга харуулна

UI бүтэц:
  ┌── 📝 Скрипт & Pipeline ─────────────────────────────────────┐
  │  ┌─────────────────────────────────────────────────────────┐ │
  │  │  Скрипт textarea (түүхэн сэдэвт нарийвчлан бичнэ...)  │ │
  │  └─────────────────────────────────────────────────────────┘ │
  │  Тэмдэгтийн тоо: 0                                          │
  │  [🔍 Chunk харах]  [▶️ Pipeline эхлүүлэх]  [⏹ Зогсоох]   │
  │  ┌── Chunk preview ────────────────────────────────────────┐ │
  │  │  📊 Нийт: N chunk                                      │ │
  │  └────────────────────────────────────────────────────────┘ │
  │  ══════════ Pipeline явц ═══════════════════════════════════ │
  │  [████████░░░░] 65%   Алхам 5/8 — Flux.1 зургууд үүсгэж…  │
  │  ┌── Log ──────────────────────────────────────────────────┐ │
  │  │  [12:34:01] Алхам 1/8 — Текст chunk болгох: ✅ ...     │ │
  │  └────────────────────────────────────────────────────────┘ │
  └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
from typing import Generator

import gradio as gr

from config import Config
from pipeline import PodcastPipeline, PipelineProgress

logger = logging.getLogger("pipeline_tab")

# Pipeline алхмуудын нэрс (UI-д харуулах)
_STEP_ICONS = ["📋", "🎙️", "🔗", "🤖", "🖼️", "👤", "🎬", "💾"]

_SAMPLE_SCRIPT = """\
Чингис хааны байлдан дагуулалт

XIII зуунд Монголын Их Эзэнт Гүрэн дэлхийн хамгийн том хуурай газрын эзэнт гүрэн болон өргөжсөн юм.

Тэмүжин 1162 онд мэндэлж, олон жилийн дайн байлдаанаар монгол овог аймгуудыг нэгтгэсний дараа 1206 онд Чингис хаан цол хүртэв. Тэрбээр хатуу сахилга бат, уян хатан тактик, харилцаа холбоог стратегийн давуу тал болгон ашиглаж байлдаж байлаа.

Монгол цэрэг Хятад, Дундад Ази, Орос, Перс зэрэг орнуудыг эзлэн авч, дэлхийн худалдааны замыг нэгтгэсэн нь Торгоны зам дагуу соёл иргэншил, шинжлэх ухааны нэгдлийг хурдасгав.
"""


def create_pipeline_tab(config: Config, shared: dict) -> None:
    """
    Pipeline tab-ийн UI-г үүсгэнэ.

    Args:
        config: Config объект
        shared: main.py-д тодорхойлсон gr.State dict
    """

    # ── API статус анхааруулга ────────────────────────────────────────────
    ready, missing = config.is_ready_for_pipeline()
    if not ready:
        gr.HTML(_warn_html(missing))

    # ── Скрипт оролт ─────────────────────────────────────────────────────
    with gr.Row():
        with gr.Column(scale=3):
            script_input = gr.Textbox(
                label       = "📜 Скрипт текст",
                placeholder = "Түүхэн сэдэвтэй подкаст скриптийг энд оруулна уу…",
                value       = _SAMPLE_SCRIPT,
                lines       = 14,
                max_lines   = 40,
                elem_id     = "script-input",
            )
            char_count_md = gr.Markdown(
                value = _char_count_md(_SAMPLE_SCRIPT),
                elem_id = "char-count",
            )

        with gr.Column(scale=1):
            gr.Markdown("### ⚙️ Тохиргоо")
            chunk_target_info = gr.Markdown(
                value = (
                    f"**Chunk хэмжээ:** {config.INWORLD_CHUNK_TARGET} тэмдэгт\n\n"
                    f"**TTS хязгаар:** {config.INWORLD_TTS_MAX_CHARS} тэмдэгт"
                )
            )
            gr.Markdown("---")
            gr.Markdown("### 🎬 Видео параметр")
            gr.Markdown(
                f"**Нягтрал:** {config.OUTPUT_VIDEO_WIDTH}×{config.OUTPUT_VIDEO_HEIGHT}\n\n"
                f"**FPS:** {config.OUTPUT_FPS}\n\n"
                f"**Bitrate:** {config.OUTPUT_VIDEO_BITRATE}"
            )

    # ── Товчнууд ─────────────────────────────────────────────────────────
    with gr.Row():
        chunk_preview_btn = gr.Button(
            "🔍  Chunk харах",
            variant  = "secondary",
            size     = "sm",
            scale    = 1,
        )
        run_btn = gr.Button(
            "▶️  Pipeline эхлүүлэх",
            variant  = "primary",
            size     = "lg",
            scale    = 3,
        )
        stop_btn = gr.Button(
            "⏹  Зогсоох",
            variant  = "secondary",
            size     = "lg",
            scale    = 1,
            interactive = False,
        )

    # ── Chunk preview ─────────────────────────────────────────────────────
    with gr.Accordion("🔍 Chunk preview", open=False) as chunk_accordion:
        chunk_preview_output = gr.Textbox(
            label     = "Chunk жагсаалт",
            lines     = 10,
            max_lines = 20,
            interactive = False,
            elem_id   = "chunk-preview",
        )

    # ── Pipeline явц ─────────────────────────────────────────────────────
    gr.Markdown("---")
    gr.Markdown("### Pipeline явц")

    with gr.Row():
        progress_bar = gr.Slider(
            minimum     = 0,
            maximum     = 100,
            value       = 0,
            label       = "Явц (%)",
            interactive = False,
            elem_id     = "pipeline-progress",
        )
        step_badge = gr.Markdown(
            value = "⏸ Хүлээж байна",
            elem_id = "step-badge",
        )

    # Алхмуудын иконо хэсэг
    steps_html = gr.HTML(value=_steps_html(current_step=0))

    # Log
    log_output = gr.Textbox(
        label       = "📋 Pipeline лог",
        lines       = 20,
        max_lines   = 40,
        interactive = False,
        elem_id     = "pipeline-log",
    )

    # ── Үр дүн (товч харуулалт) ───────────────────────────────────────────
    gr.Markdown("---")
    gr.Markdown("### 🎉 Үр дүн")
    with gr.Row():
        result_video = gr.Video(
            label   = "Эцсийн видео",
            visible = False,
        )
        result_audio = gr.Audio(
            label   = "Нийтлэл аудио",
            visible = False,
        )

    result_summary = gr.Markdown(
        value   = "",
        visible = False,
    )

    # ── Event handlers ────────────────────────────────────────────────────

    # Тэмдэгтийн тоо
    script_input.change(
        fn      = _char_count_md,
        inputs  = [script_input],
        outputs = [char_count_md],
    )

    # Chunk preview
    def _show_chunk_preview(text: str) -> tuple[str, dict]:
        if not text.strip():
            return "Скрипт хоосон байна.", gr.update(open=True)
        try:
            from modules.text_chunker import preview_chunks
            preview = preview_chunks(text, config)
            return preview, gr.update(open=True)
        except Exception as exc:
            logger.error(f"Chunk preview алдаа: {exc}")
            return f"❌ Алдаа: {exc}", gr.update(open=True)

    chunk_preview_btn.click(
        fn      = _show_chunk_preview,
        inputs  = [script_input],
        outputs = [chunk_preview_output, chunk_accordion],
    )

    # Pipeline ажиллуулах
    def _run_pipeline(
        script: str,
    ) -> Generator[tuple, None, None]:
        """
        Pipeline streaming generator.
        Алхам бүр дээр UI component-уудыг шинэчилнэ.
        """
        if not script.strip():
            yield (
                "⚠️ Скрипт хоосон байна.",  # log
                0,                           # progress bar
                "⚠️ Скрипт хоосон",         # step badge
                _steps_html(0),             # steps
                gr.update(visible=False),   # video
                gr.update(visible=False),   # audio
                gr.update(value="", visible=False),  # summary
                gr.update(interactive=True),  # run btn
                gr.update(interactive=False), # stop btn
            )
            return

        pipeline = PodcastPipeline(config)

        # Run товч идэвхгүй, Stop товч идэвхтэй болгох
        yield (
            "🚀 Pipeline эхэлж байна…",
            0,
            "🚀 Эхэлж байна…",
            _steps_html(0),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(value="", visible=False),
            gr.update(interactive=False),
            gr.update(interactive=True),
        )

        last_result = None

        for prog in pipeline.run(script):
            prog: PipelineProgress

            step_text = (
                f"{'❌' if prog.is_error else '⏳' if not prog.is_done else '✅'} "
                f"Алхам {prog.step}/{prog.total_steps} — {prog.step_name}"
            )

            yield (
                prog.log_line,
                prog.percent,
                step_text,
                _steps_html(prog.step, is_error=prog.is_error),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(value="", visible=False),
                gr.update(interactive=False),
                gr.update(interactive=True),
            )

            if prog.is_done:
                last_result = prog.result
                break

        # Дуусгавар
        if last_result and last_result.success:
            summary = _build_summary_md(last_result)
            yield (
                pipeline.get_log(),
                100,
                "✅ Амжилттай дууслаа!",
                _steps_html(8, is_done=True),
                gr.update(
                    value   = last_result.video_path,
                    visible = True,
                ) if last_result.video_path else gr.update(visible=False),
                gr.update(
                    value   = last_result.final_audio_path,
                    visible = True,
                ) if last_result.final_audio_path else gr.update(visible=False),
                gr.update(value=summary, visible=True),
                gr.update(interactive=True),
                gr.update(interactive=False),
            )
        else:
            err = last_result.error if last_result else "Тодорхойгүй алдаа"
            yield (
                pipeline.get_log(),
                0,
                f"❌ Алдаа: {err}",
                _steps_html(0, is_error=True),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(value=f"❌ **Алдаа:** {err}", visible=True),
                gr.update(interactive=True),
                gr.update(interactive=False),
            )

    run_btn.click(
        fn      = _run_pipeline,
        inputs  = [script_input],
        outputs = [
            log_output,
            progress_bar,
            step_badge,
            steps_html,
            result_video,
            result_audio,
            result_summary,
            run_btn,
            stop_btn,
        ],
        show_progress = "hidden",
    )


# ── Туслах функцүүд ───────────────────────────────────────────────────────────

def _char_count_md(text: str) -> str:
    n = len(text) if text else 0
    color = "#4ade80" if n < 8000 else "#facc15" if n < 20000 else "#f87171"
    return f'<span style="color:{color};font-size:.85rem">✏️ {n:,} тэмдэгт</span>'


def _warn_html(missing: list[str]) -> str:
    items = "".join(f"<li>{m}</li>" for m in missing)
    return (
        '<div style="background:#422006;border:1px solid #facc15;'
        'border-radius:8px;padding:12px 16px;margin-bottom:12px;">'
        '<strong style="color:#facc15">⚠️ Дараах тохиргоо дутуу байна:</strong>'
        f'<ul style="color:#fde68a;margin:8px 0 0 20px">{items}</ul>'
        '<p style="color:#fde68a;margin:8px 0 0;font-size:.85rem">'
        '⚙️ Тохиргоо tab дээр API key оруулна уу.</p></div>'
    )


def _steps_html(current_step: int, is_error: bool = False, is_done: bool = False) -> str:
    """
    Pipeline алхмуудын визуал харуулалт.
    current_step: 1–8 (0 = эхлээгүй)
    """
    step_names = [
        "Chunk",
        "TTS",
        "Аудио нэгтгэх",
        "Grok промпт",
        "Flux зураг",
        "Talking Head",
        "FFmpeg",
        "Volume",
    ]
    icons = _STEP_ICONS

    parts: list[str] = []
    for i, (name, icon) in enumerate(zip(step_names, icons)):
        step_num = i + 1
        if step_num < current_step:
            # Дуусгасан
            bg    = "#052e16"
            color = "#4ade80"
            bdr   = "#4ade80"
            status_icon = "✅"
        elif step_num == current_step:
            # Одоогийн
            bg    = "#422006" if is_error else "#1e3a5f"
            color = "#f87171" if is_error else "#7dd3fc"
            bdr   = "#f87171" if is_error else "#38bdf8"
            status_icon = "❌" if is_error else "⏳"
        else:
            # Хүлээж буй
            bg    = "#0d1117"
            color = "#4a5568"
            bdr   = "#21262d"
            status_icon = icon

        parts.append(
            f'<div style="'
            f'background:{bg};border:1px solid {bdr};'
            f'border-radius:6px;padding:4px 8px;margin:2px;'
            f'display:inline-block;min-width:90px;text-align:center;'
            f'font-size:.75rem;color:{color};">'
            f'{status_icon} {name}</div>'
        )

    return (
        '<div style="display:flex;flex-wrap:wrap;gap:4px;padding:8px 0;">'
        + "".join(parts)
        + "</div>"
    )


def _build_summary_md(result) -> str:
    """Pipeline үр дүнгийн хураангуй Markdown."""
    chunks_ok    = sum(1 for c in result.chunks if c.image_path)
    chunks_total = len(result.chunks)
    proc_min     = int(result.processing_time // 60)
    proc_sec     = int(result.processing_time % 60)
    audio_min    = int(result.total_duration // 60)
    audio_sec    = int(result.total_duration % 60)

    return (
        f"## 🎉 Pipeline амжилттай дууслаа!\n\n"
        f"| Үзүүлэлт | Утга |\n"
        f"|---|---|\n"
        f"| ⏱ Боловсруулалтын хугацаа | {proc_min}м {proc_sec}с |\n"
        f"| 🎵 Нийт аудио | {audio_min}м {audio_sec}с |\n"
        f"| 📦 Chunk | {chunks_total} |\n"
        f"| 🖼️ Зураг | {chunks_ok}/{chunks_total} |\n"
        f"| 📝 Субтайтл | {'✅' if result.subtitle_path else '—'} |\n"
        f"| 👤 Talking head | {'✅' if result.talking_head_path else '—'} |\n"
        f"| 🎬 Видео | `{result.video_path or '—'}` |\n"
    )
