"""
ui/settings_tab.py — API key, загвар тохиргооны Gradio UI

Онцлог:
  - CONFIG_GROUPS-аас автоматаар форм үүсгэнэ
  - API key-г password input-оор нуух
  - "Хадгалах" → .env файлд бичнэ
  - "Шалгах" → API status badge харуулна
  - Апп нэг ч API key байхгүй ч нормал нээгдэнэ
  - Хадгалсан утга дараагийн нэвтрэлтэд дахин ачаалагдана

UI бүтэц:
  ┌── ⚙️ Тохиргоо & API ─────────────────────────────────────────┐
  │  [🔄 API статус шалгах]                                      │
  │  ┌─ 🎙️ Inworld TTS-2 ──────────────────────────────────┐    │
  │  │  API Key [password]   Дуу хоолой [text]              │    │
  │  │  Хэл [text]          Chunk хэмжээ [number]           │    │
  │  └────────────────────────────────────────────────────────┘    │
  │  ┌─ 🤖 xAI Grok ──────────────────────────────────────┐    │
  │  │  API Key [password]   Загвар [text]   Темп [number] │    │
  │  └────────────────────────────────────────────────────────┘    │
  │  ... (RunPod, Pod, Output groups)                             │
  │  [💾 Бүгдийг хадгалах]                                        │
  │  Status: ✅ Хадгалагдлаа / ❌ Алдаа                          │
  └────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Any

import gradio as gr

from config import Config, CONFIG_GROUPS

logger = logging.getLogger("settings_tab")


def create_settings_tab(config: Config) -> None:
    """
    Тохиргооны tab-ийн бүрэн UI-г үүсгэнэ.

    Args:
        config: Config объект (унших + хадгалахад хэрэглэнэ)
    """

    # ── API Status харуулах ────────────────────────────────────────────────
    with gr.Row():
        status_html = gr.HTML(
            value=_render_status_html(config),
            elem_id="api-status-panel",
        )
        with gr.Column(scale=0, min_width=180):
            check_btn = gr.Button(
                "🔄 Статус шалгах",
                variant="secondary",
                size="sm",
            )

    # ── Мэдэгдэл ──────────────────────────────────────────────────────────
    save_status = gr.Markdown(
        value="",
        elem_id="save-status-msg",
    )

    # ── Config groups → форм ──────────────────────────────────────────────
    # Бүх input component-г dict-д хадгална
    inputs: dict[str, gr.components.Component] = {}

    for group in CONFIG_GROUPS:
        with gr.Accordion(
            label=group["title"],
            open=(group["id"] == "inworld"),   # Эхний accordion нээлттэй
        ):
            # 2 баганатай grid
            fields = group["fields"]
            for i in range(0, len(fields), 2):
                with gr.Row():
                    for fld in fields[i : i + 2]:
                        key         = fld["key"]
                        label       = fld["label"]
                        ftype       = fld["type"]
                        placeholder = fld.get("placeholder", "")
                        current_val = _get_display_value(config, key, ftype)

                        if ftype == "password":
                            comp = gr.Textbox(
                                label       = label,
                                value       = current_val,
                                placeholder = placeholder,
                                type        = "password",
                                interactive = True,
                                info        = f"Env: {key}",
                            )
                        elif ftype == "number":
                            comp = gr.Number(
                                label       = label,
                                value       = float(current_val) if current_val else None,
                                interactive = True,
                                info        = f"Env: {key}",
                            )
                        else:
                            comp = gr.Textbox(
                                label       = label,
                                value       = current_val,
                                placeholder = placeholder,
                                interactive = True,
                                info        = f"Env: {key}",
                            )

                        inputs[key] = comp

    # ── Хадгалах товч ────────────────────────────────────────────────────
    gr.Markdown("---")
    with gr.Row():
        save_btn = gr.Button(
            "💾  Бүгдийг хадгалах",
            variant="primary",
            size="lg",
            scale=2,
        )
        reset_btn = gr.Button(
            "↺  Дахин ачаалах",
            variant="secondary",
            size="lg",
            scale=1,
        )

    # ── Нэмэлт тохиргоо: Image style ─────────────────────────────────────
    with gr.Accordion("🎨 Зургийн стиль (Flux prompt)", open=False):
        style_suffix = gr.Textbox(
            label       = "Стилийн нэмэлт текст",
            value       = config.IMAGE_STYLE_SUFFIX,
            placeholder = "cinematic historical photography...",
            lines       = 3,
            interactive = True,
            info        = "Env: IMAGE_STYLE_SUFFIX",
        )
        neg_prompt = gr.Textbox(
            label       = "Negative Prompt",
            value       = config.IMAGE_NEGATIVE_PROMPT,
            placeholder = "cartoon, anime, modern...",
            lines       = 2,
            interactive = True,
            info        = "Env: IMAGE_NEGATIVE_PROMPT",
        )
        inputs["IMAGE_STYLE_SUFFIX"]    = style_suffix
        inputs["IMAGE_NEGATIVE_PROMPT"] = neg_prompt

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_save(*values) -> tuple[str, str]:
        """Бүх оролтыг .env-д хадгалж статус буцаана."""
        keys = list(inputs.keys())
        updates: dict[str, str] = {}

        for key, val in zip(keys, values):
            if val is None:
                updates[key] = ""
            else:
                updates[key] = str(val).strip()

        result = config.save_to_env(updates)

        if result["status"] == "ok":
            new_status = _render_status_html(config)
            return (
                f"✅ **{result['message']}**",
                new_status,
            )
        else:
            return (
                f"❌ **Алдаа:** {result['message']}",
                _render_status_html(config),
            )

    def _on_check() -> str:
        """API статусыг шалгаж HTML буцаана."""
        return _render_status_html(config)

    def _on_reset() -> tuple:
        """Утгуудыг .env-ээс дахин ачаалж, form-г шинэчилнэ."""
        config._load_from_env()
        vals = tuple(
            _get_display_value(config, key, _find_field_type(key))
            for key in inputs.keys()
        )
        return vals + (
            "🔄 Дахин ачаалагдлаа.",
            _render_status_html(config),
        )

    # Save
    input_list = list(inputs.values())
    save_btn.click(
        fn       = _on_save,
        inputs   = input_list,
        outputs  = [save_status, status_html],
    )

    # Check
    check_btn.click(
        fn      = _on_check,
        inputs  = [],
        outputs = [status_html],
    )

    # Reset
    reset_btn.click(
        fn      = _on_reset,
        inputs  = [],
        outputs = input_list + [save_status, status_html],
    )


# ── Туслах функцүүд ───────────────────────────────────────────────────────────

def _render_status_html(config: Config) -> str:
    """
    API статусыг HTML badge хэлбэрт хөрвүүлнэ.
    """
    status = config.get_api_status()

    badges: list[str] = []
    for _key, info in status.items():
        ok    = info["ok"]
        label = info["label"]
        hint  = info.get("hint", "")

        color = "#4ade80" if ok else "#facc15"
        bg    = "#052e16" if ok else "#422006"

        badge = (
            f'<span style="'
            f'background:{bg};color:{color};'
            f'border:1px solid {color};'
            f'border-radius:6px;padding:4px 12px;'
            f'font-size:.8rem;margin:3px;display:inline-block;'
            f'" title="{hint}">'
            f'{label}'
            f'</span>'
        )
        badges.append(badge)

    all_ok = all(info["ok"] for info in status.values())
    overall = (
        '<div style="color:#4ade80;font-weight:700;margin-bottom:8px">✅ Pipeline бэлэн</div>'
        if all_ok else
        '<div style="color:#facc15;font-weight:700;margin-bottom:8px">⚠️ Зарим тохиргоо дутуу — Pipeline ажиллахгүй байж болно</div>'
    )

    return (
        '<div style="background:#0d1117;border:1px solid #21262d;'
        'border-radius:8px;padding:12px 16px;">'
        + overall
        + "".join(badges)
        + "</div>"
    )


def _get_display_value(config: Config, key: str, ftype: str) -> Any:
    """Config-ээс утга авч UI-д тохирох хэлбэрт хөрвүүлнэ."""
    val = getattr(config, key, None)
    if val is None:
        return ""
    if ftype == "password":
        return ""   # Аюулгүй байдлын үүднээс password-г харуулахгүй
    return val


def _find_field_type(key: str) -> str:
    """Key-ийн field type-г CONFIG_GROUPS-аас олно."""
    for group in CONFIG_GROUPS:
        for fld in group["fields"]:
            if fld["key"] == key:
                return fld["type"]
    # IMAGE_STYLE_SUFFIX, IMAGE_NEGATIVE_PROMPT
    if key in ("IMAGE_STYLE_SUFFIX", "IMAGE_NEGATIVE_PROMPT"):
        return "text"
    return "text"
