"""
ui/output_tab.py — Үр дүн харах, татах Gradio UI

Онцлог:
  - Pipeline дуусвал видео, аудио, субтайтл харуулна
  - Генерэйтэд зургуудын галерей (chunk бүрийн зураг + хугацаа)
  - ASS субтайтл контентыг текст хэлбэрт харуулах
  - Бүх файлыг татах товчнууд
  - Volume-д хадгалагдсан файлуудын жагсаалт
  - Pipeline лог татах
  - "Дахин үүсгэх" — pipeline tab руу шилжих

UI бүтэц:
  ┌── 🎥 Гаралт & Татах ────────────────────────────────────────┐
  │  ┌─ 🎬 Эцсийн видео ──────────────────────────────────────┐ │
  │  │  [Video player]                 [⬇ Видео татах]        │ │
  │  │  Хугацаа: 4м 32с | Хэмжээ: 145.3 MB                   │ │
  │  └────────────────────────────────────────────────────────┘ │
  │  ┌─ 🎵 Аудио + 📝 Субтайтл ────────────────────────────── ┐ │
  │  │  [Audio player]  [⬇ WAV]  [⬇ ASS субтайтл]            │ │
  │  └────────────────────────────────────────────────────────┘ │
  │  ┌─ 🖼️ Генерэйтэд зургууд ─────────────────────────────── ┐ │
  │  │  [Img 1: 12.3с] [Img 2: 8.7с] [Img 3: 15.1с] ...      │ │
  │  └────────────────────────────────────────────────────────┘ │
  │  ┌─ 💾 Volume файлууд ──────────────────────────────────── ┐ │
  │  │  /runpod-volume/podcast/output/                         │ │
  │  │  📄 final_podcast.mp4  145.3 MB  2025-01-15 12:34      │ │
  │  └────────────────────────────────────────────────────────┘ │
  │  [🔄 Шинэчлэх]                                              │
  └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import gradio as gr

from config import Config

logger = logging.getLogger("output_tab")


def create_output_tab(config: Config, shared: dict) -> None:
    """
    Гаралтын tab-ийн бүрэн UI-г үүсгэнэ.

    Args:
        config: Config объект
        shared: pipeline_tab.py-аас дамжих gr.State dict
                {
                  "video_path":    gr.State(None),
                  "audio_path":    gr.State(None),
                  "subtitle_path": gr.State(None),
                  "log_text":      gr.State(""),
                  "chunks_info":   gr.State([]),
                }
    """

    # ── Хоосон хавтасны анхааруулга ─────────────────────────────────────
    gr.HTML(_info_html(
        "ℹ️ Pipeline дуусвал энд видео, аудио, субтайтл болон зургууд харагдана.",
        color="blue",
    ))

    # ── 1. Эцсийн видео ──────────────────────────────────────────────────
    with gr.Accordion("🎬 Эцсийн видео", open=True):
        with gr.Row():
            with gr.Column(scale=3):
                output_video = gr.Video(
                    label     = "Подкаст видео (1080p)",
                    height    = 400,
                    elem_id   = "output-video",
                )
            with gr.Column(scale=1):
                video_info_md = gr.Markdown(
                    value = "_Видео бэлэн болоогүй_",
                    elem_id = "video-info",
                )
                download_video_btn = gr.DownloadButton(
                    label    = "⬇️  Видео татах (MP4)",
                    variant  = "primary",
                    size     = "sm",
                    visible  = False,
                    elem_id  = "dl-video",
                )

    # ── 2. Аудио + Субтайтл ──────────────────────────────────────────────
    with gr.Accordion("🎵 Аудио & 📝 Субтайтл", open=False):
        with gr.Row():
            with gr.Column():
                output_audio = gr.Audio(
                    label    = "Нэгтгэсэн аудио (WAV)",
                    type     = "filepath",
                    elem_id  = "output-audio",
                )
                download_audio_btn = gr.DownloadButton(
                    label   = "⬇️  WAV татах",
                    variant = "secondary",
                    size    = "sm",
                    visible = False,
                )

            with gr.Column():
                output_subtitle = gr.Textbox(
                    label       = "ASS Субтайтл (preview)",
                    lines       = 10,
                    max_lines   = 20,
                    interactive = False,
                    elem_id     = "output-subtitle",
                    placeholder = "Субтайтл файл бэлэн болоогүй…",
                )
                download_sub_btn = gr.DownloadButton(
                    label   = "⬇️  ASS субтайтл татах",
                    variant = "secondary",
                    size    = "sm",
                    visible = False,
                )

    # ── 3. Зургуудын галерей ──────────────────────────────────────────────
    with gr.Accordion("🖼️ Генерэйтэд зургууд", open=False):
        image_gallery = gr.Gallery(
            label        = "Flux.1 зургууд (хугацааны дарааллаар)",
            columns      = 4,
            rows         = 2,
            object_fit   = "cover",
            height       = 280,
            elem_id      = "image-gallery",
        )
        gallery_info_md = gr.Markdown(
            value = "_Зургууд бэлэн болоогүй_",
        )

    # ── 4. Pipeline лог ───────────────────────────────────────────────────
    with gr.Accordion("📋 Pipeline лог", open=False):
        log_display = gr.Textbox(
            label        = "Бүрэн лог",
            lines        = 15,
            max_lines    = 50,
            interactive  = False,
            elem_id      = "output-log",
            show_copy_button = True,
        )
        download_log_btn = gr.DownloadButton(
            label   = "⬇️  Лог татах (.txt)",
            variant = "secondary",
            size    = "sm",
            visible = False,
        )

    # ── 5. Volume файлуудын жагсаалт ─────────────────────────────────────
    with gr.Accordion("💾 RunPod Volume файлууд", open=False):
        with gr.Row():
            volume_refresh_btn = gr.Button(
                "🔄  Шинэчлэх",
                variant = "secondary",
                size    = "sm",
                scale   = 0,
            )
            volume_path_display = gr.Markdown(
                value = f"`{config.RUNPOD_VOLUME_PATH}`",
            )
        volume_files_md = gr.Markdown(
            value = _list_volume_files(config),
            elem_id = "volume-files",
        )

    # ── Хуудасны доод товчнууд ────────────────────────────────────────────
    gr.Markdown("---")
    with gr.Row():
        refresh_all_btn = gr.Button(
            "🔄  Бүгдийг шинэчлэх",
            variant = "secondary",
            size    = "sm",
        )
        clear_btn = gr.Button(
            "🗑️  Цэвэрлэх",
            variant = "stop",
            size    = "sm",
        )

    # ── Shared state-ийн trigger ──────────────────────────────────────────
    # pipeline_tab.py нь shared dict дэх State-уудыг шинэчилнэ.
    # Энд тэдгээрийн change event-г сонсоно.

    video_state    = shared.get("video_path")
    audio_state    = shared.get("audio_path")
    sub_state      = shared.get("subtitle_path")
    log_state      = shared.get("log_text")
    chunks_state   = shared.get("chunks_info")

    # ── Event: shared state өөрчлөгдөхөд UI шинэчлэх ────────────────────

    def _on_video_ready(video_path: Optional[str]) -> tuple:
        """Видео бэлэн болоход UI шинэчлэнэ."""
        if not video_path or not os.path.exists(video_path):
            return (
                None,
                "_Видео бэлэн болоогүй_",
                gr.update(visible=False, value=None),
            )
        info = _file_info_md(video_path, "🎬 Видео")
        return (
            video_path,
            info,
            gr.update(visible=True, value=video_path),
        )

    def _on_audio_ready(audio_path: Optional[str]) -> tuple:
        """Аудио бэлэн болоход UI шинэчлэнэ."""
        if not audio_path or not os.path.exists(audio_path):
            return None, gr.update(visible=False, value=None)
        return audio_path, gr.update(visible=True, value=audio_path)

    def _on_subtitle_ready(sub_path: Optional[str]) -> tuple:
        """Субтайтл бэлэн болоход UI шинэчлэнэ."""
        if not sub_path or not os.path.exists(sub_path):
            return "", gr.update(visible=False, value=None)
        try:
            content = Path(sub_path).read_text(encoding="utf-8")
            preview = content[:3000] + ("\n…(тайрагдлаа)" if len(content) > 3000 else "")
        except Exception:
            preview = f"Субтайтл унших алдаа: {sub_path}"
        return preview, gr.update(visible=True, value=sub_path)

    def _on_log_ready(log_text: str) -> tuple:
        """Лог бэлэн болоход UI шинэчлэнэ."""
        if not log_text:
            return "", gr.update(visible=False, value=None)

        # Лог файлыг /tmp-д хадгалж download link үүсгэнэ
        log_file = "/tmp/podcast_pipeline.log"
        try:
            Path(log_file).write_text(log_text, encoding="utf-8")
            dl = gr.update(visible=True, value=log_file)
        except Exception:
            dl = gr.update(visible=False)
        return log_text, dl

    def _on_chunks_ready(chunks_info: list) -> tuple:
        """
        Chunk мэдээллээс зургуудыг галерейд харуулна.
        chunks_info: [{"index": int, "image_path": str, "duration": float, "prompt": str}, ...]
        """
        if not chunks_info:
            return [], "_Зургууд бэлэн болоогүй_"

        gallery_items = []
        captions = []
        for item in chunks_info:
            path = item.get("image_path")
            if path and os.path.exists(path):
                gallery_items.append(path)
                dur  = item.get("duration", 0)
                idx  = item.get("index", 0)
                captions.append(f"#{idx+1} | {dur:.1f}с")

        if not gallery_items:
            return [], "_Зургийн файл олдсонгүй_"

        info = f"**{len(gallery_items)} зураг** галерейд харагдаж байна."
        return list(zip(gallery_items, captions)), info

    # State change → UI update
    if video_state:
        video_state.change(
            fn      = _on_video_ready,
            inputs  = [video_state],
            outputs = [output_video, video_info_md, download_video_btn],
        )

    if audio_state:
        audio_state.change(
            fn      = _on_audio_ready,
            inputs  = [audio_state],
            outputs = [output_audio, download_audio_btn],
        )

    if sub_state:
        sub_state.change(
            fn      = _on_subtitle_ready,
            inputs  = [sub_state],
            outputs = [output_subtitle, download_sub_btn],
        )

    if log_state:
        log_state.change(
            fn      = _on_log_ready,
            inputs  = [log_state],
            outputs = [log_display, download_log_btn],
        )

    if chunks_state:
        chunks_state.change(
            fn      = _on_chunks_ready,
            inputs  = [chunks_state],
            outputs = [image_gallery, gallery_info_md],
        )

    # ── Шинэчлэх товч ────────────────────────────────────────────────────

    def _refresh_volume() -> str:
        return _list_volume_files(config)

    volume_refresh_btn.click(
        fn      = _refresh_volume,
        inputs  = [],
        outputs = [volume_files_md],
    )

    def _refresh_all(
        video_path: Optional[str],
        audio_path: Optional[str],
        sub_path:   Optional[str],
        log_text:   str,
        chunks_info: list,
    ) -> tuple:
        v_out    = _on_video_ready(video_path)
        a_out    = _on_audio_ready(audio_path)
        s_out    = _on_subtitle_ready(sub_path)
        l_out    = _on_log_ready(log_text)
        g_out    = _on_chunks_ready(chunks_info)
        vol_md   = _list_volume_files(config)

        return (
            v_out[0], v_out[1], v_out[2],    # video, info, dl_video
            a_out[0], a_out[1],               # audio, dl_audio
            s_out[0], s_out[1],               # subtitle text, dl_sub
            l_out[0], l_out[1],               # log, dl_log
            g_out[0], g_out[1],               # gallery, gallery_info
            vol_md,
        )

    refresh_inputs = [
        video_state or gr.State(None),
        audio_state or gr.State(None),
        sub_state   or gr.State(None),
        log_state   or gr.State(""),
        chunks_state or gr.State([]),
    ]

    refresh_all_btn.click(
        fn      = _refresh_all,
        inputs  = refresh_inputs,
        outputs = [
            output_video, video_info_md, download_video_btn,
            output_audio, download_audio_btn,
            output_subtitle, download_sub_btn,
            log_display, download_log_btn,
            image_gallery, gallery_info_md,
            volume_files_md,
        ],
    )

    # ── Цэвэрлэх ─────────────────────────────────────────────────────────

    def _clear_outputs() -> tuple:
        return (
            None,                                       # video
            "_Видео бэлэн болоогүй_",                  # video info
            gr.update(visible=False),                   # dl video
            None,                                       # audio
            gr.update(visible=False),                   # dl audio
            "",                                         # subtitle
            gr.update(visible=False),                   # dl sub
            "",                                         # log
            gr.update(visible=False),                   # dl log
            [],                                         # gallery
            "_Зургууд бэлэн болоогүй_",                # gallery info
        )

    clear_btn.click(
        fn      = _clear_outputs,
        inputs  = [],
        outputs = [
            output_video, video_info_md, download_video_btn,
            output_audio, download_audio_btn,
            output_subtitle, download_sub_btn,
            log_display, download_log_btn,
            image_gallery, gallery_info_md,
        ],
    )


# ── Туслах функцүүд ───────────────────────────────────────────────────────────

def _file_info_md(path: str, label: str = "Файл") -> str:
    """Файлын хэмжээ, хугацаа мэдээллийг Markdown хэлбэрт харуулна."""
    try:
        stat   = os.stat(path)
        size_b = stat.st_size
        size_s = _human_size(size_b)

        # Хугацаа (видеонд)
        duration_s = ""
        if path.endswith(".mp4"):
            dur = _get_video_duration(path)
            if dur > 0:
                m = int(dur // 60)
                s = int(dur % 60)
                duration_s = f"⏱ **{m}м {s}с**  "

        from datetime import datetime
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

        return (
            f"**{label}**\n\n"
            f"{duration_s}💾 **{size_s}**\n\n"
            f"📅 {mtime}\n\n"
            f"`{path}`"
        )
    except Exception as exc:
        return f"_{label}: {exc}_"


def _human_size(n: int) -> str:
    """Байтыг хүний уншиж болох хэлбэрт хөрвүүлнэ."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _get_video_duration(path: str) -> float:
    """FFprobe-оор видеоны хугацааг авна."""
    try:
        import subprocess, json
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(r.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


def _list_volume_files(config: Config) -> str:
    """
    RunPod Volume дотор output хавтасны файлуудыг жагсаана.
    Хавтас байхгүй бол анхааруулга харуулна.
    """
    output_dir = config.OUTPUT_DIR
    if not os.path.exists(output_dir):
        return (
            f"⚠️ Volume хавтас олдсонгүй: `{output_dir}`\n\n"
            "RunPod Volume холбогдсон эсэхийг шалгана уу."
        )

    try:
        entries = sorted(
            Path(output_dir).iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except PermissionError:
        return f"❌ Хандах эрхгүй: `{output_dir}`"

    if not entries:
        return f"📂 Хавтас хоосон: `{output_dir}`"

    # Файлуудыг хүснэгтэд харуулна
    rows = ["| Файл | Хэмжээ | Огноо |", "|---|---|---|"]
    for entry in entries[:30]:   # Хамгийн ихдээ 30
        if entry.is_file():
            stat    = entry.stat()
            size    = _human_size(stat.st_size)
            from datetime import datetime
            mtime   = datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M")
            icon    = _file_icon(entry.suffix)
            rows.append(f"| {icon} `{entry.name}` | {size} | {mtime} |")

    return (
        f"📂 **{output_dir}**\n\n"
        + "\n".join(rows)
    )


def _file_icon(suffix: str) -> str:
    """Файлын өргөтгөлд тохирох emoji буцаана."""
    icons = {
        ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬",
        ".wav": "🎵", ".mp3": "🎵", ".aac": "🎵",
        ".ass": "📝", ".srt": "📝", ".vtt": "📝",
        ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️",
        ".txt": "📄", ".log": "📄", ".json": "📋",
    }
    return icons.get(suffix.lower(), "📁")


def _info_html(message: str, color: str = "blue") -> str:
    """Мэдэгдлийн HTML блок үүсгэнэ."""
    palettes = {
        "blue":   ("#1e3a5f", "#38bdf8", "#7dd3fc"),
        "green":  ("#052e16", "#4ade80", "#bbf7d0"),
        "yellow": ("#422006", "#facc15", "#fde68a"),
        "red":    ("#450a0a", "#f87171", "#fecaca"),
    }
    bg, border, text = palettes.get(color, palettes["blue"])
    return (
        f'<div style="background:{bg};border:1px solid {border};'
        f'border-radius:8px;padding:10px 16px;margin-bottom:10px;">'
        f'<span style="color:{text};font-size:.9rem">{message}</span>'
        f'</div>'
    )
