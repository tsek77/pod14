"""
ui/settings_tab.py — Бүх тохиргоог UI-ээс .env-д хадгалах

Онцлог:
  - CONFIG_GROUPS-аас автоматаар форм үүсгэнэ
  - password | text | number | dropdown | slider төрлүүдийг дэмжнэ
  - Password field: тохируулагдсан бол "●●●● тохируулагдсан" харуулна
  - Хадгалах → /workspace/.env файлд бичнэ
  - Дахин ачаалах → .env-ээс уншиж form-г шинэчилнэ
  - Статус badge → API key бүрийн байдлыг харуулна
"""

from __future__ import annotations

import logging
from typing import Any

import gradio as gr

from config import Config, CONFIG_GROUPS

logger = logging.getLogger("settings_tab")


def create_settings_tab(config: Config) -> None:
    """
    ⚙️ Тохиргоо tab-ийн бүрэн UI үүсгэнэ.
    """

    # ── Толгой: статус + шалгах товч ─────────────────────────────────────
    gr.HTML("""
    <div style="background:#0d1117;border:1px solid #21262d;border-radius:10px;
                padding:14px 18px;margin-bottom:12px;">
      <div style="color:#7dd3fc;font-size:.85rem;margin-bottom:6px;">
        ℹ️ Тохиргоо <b>/workspace/.env</b> файлд хадгалагдана.
        Pod дахин эхэлсэн ч утгууд хадгалагдсан хэвээр байна.
      </div>
    </div>
    """)

    with gr.Row():
        status_html = gr.HTML(
            value=_render_status_html(config),
            elem_id="api-status-panel",
        )
        with gr.Column(scale=0, min_width=160):
            check_btn = gr.Button("🔄 Статус", variant="secondary", size="sm")

    save_status = gr.Markdown(value="", elem_id="save-status-msg")

    # ── Form: CONFIG_GROUPS-аас автоматаар үүсгэнэ ───────────────────────
    inputs: dict[str, gr.components.Component] = {}

    for group in CONFIG_GROUPS:
        is_first = group["id"] == "inworld"
        with gr.Accordion(label=group["title"], open=is_first):
            flds = group["fields"]

            # 2 баганаар харуулна
            for i in range(0, len(flds), 2):
                with gr.Row():
                    for fld in flds[i : i + 2]:
                        comp = _build_component(config, fld)
                        inputs[fld["key"]] = comp

    # ── Зургийн стиль (тусдаа accordion) ─────────────────────────────────
    with gr.Accordion("🎨 Зургийн стиль (Flux prompt нэмэлт)", open=False):
        gr.HTML("""<div style="color:#94a3b8;font-size:.8rem;margin-bottom:8px;">
        Зураг бүрийн prompt-д автоматаар нэмэгдэх текст.
        </div>""")
        style_suffix = gr.Textbox(
            label="Style Suffix",
            value=config.IMAGE_STYLE_SUFFIX,
            placeholder="cinematic historical photography...",
            lines=3,
            interactive=True,
            info="Env: IMAGE_STYLE_SUFFIX",
        )
        neg_prompt = gr.Textbox(
            label="Negative Prompt",
            value=config.IMAGE_NEGATIVE_PROMPT,
            placeholder="cartoon, anime, modern...",
            lines=2,
            interactive=True,
            info="Env: IMAGE_NEGATIVE_PROMPT",
        )
        inputs["IMAGE_STYLE_SUFFIX"]    = style_suffix
        inputs["IMAGE_NEGATIVE_PROMPT"] = neg_prompt

    # ── Товчнууд ─────────────────────────────────────────────────────────
    gr.Markdown("---")
    with gr.Row():
        save_btn  = gr.Button("💾  Бүгдийг хадгалах",  variant="primary",   size="lg", scale=3)
        reset_btn = gr.Button("↺  Дахин ачаалах",      variant="secondary", size="lg", scale=1)

    # ── .env файлын агуулгыг харах (debug) ───────────────────────────────
    with gr.Accordion("🔍 .env файл харах", open=False):
        env_preview = gr.Textbox(
            label=f"Одоогийн {config._env_path}",
            value=_read_env_masked(config),
            lines=15,
            interactive=False,
            info="API key-үүд маскалагдсан байна",
        )
        refresh_env_btn = gr.Button("🔄 Шинэчлэх", size="sm", variant="secondary")

    # ── Event handlers ────────────────────────────────────────────────────
    input_list = list(inputs.values())

    def _on_save(*values):
        keys = list(inputs.keys())
        updates = {}
        for key, val in zip(keys, values):
            updates[key] = "" if val is None else str(val).strip()

        result = config.save_to_env(updates)
        new_status = _render_status_html(config)
        new_env    = _read_env_masked(config)

        if result["status"] == "ok":
            return (f"✅ **{result['message']}**", new_status, new_env)
        else:
            return (f"❌ **Алдаа:** {result['message']}", new_status, new_env)

    def _on_check():
        return _render_status_html(config)

    def _on_reset():
        config._load_from_env()
        vals = []
        for key in inputs.keys():
            ftype = _find_field_type(key)
            vals.append(_get_display_value(config, key, ftype))
        return tuple(vals) + ("🔄 Дахин ачаалагдлаа.", _render_status_html(config), _read_env_masked(config))

    def _on_refresh_env():
        return _read_env_masked(config)

    save_btn.click(
        fn=_on_save,
        inputs=input_list,
        outputs=[save_status, status_html, env_preview],
    )
    check_btn.click(fn=_on_check, inputs=[], outputs=[status_html])
    reset_btn.click(
        fn=_on_reset,
        inputs=[],
        outputs=input_list + [save_status, status_html, env_preview],
    )
    refresh_env_btn.click(fn=_on_refresh_env, inputs=[], outputs=[env_preview])


# ── Component builder ─────────────────────────────────────────────────────────

def _build_component(config: Config, fld: dict) -> gr.components.Component:
    """
    Field тодорхойлолтоос Gradio component үүсгэнэ.
    Дэмжих төрлүүд: password | text | number | dropdown | slider
    """
    key         = fld["key"]
    label       = fld["label"]
    ftype       = fld["type"]
    placeholder = fld.get("placeholder", "")
    info        = fld.get("info", f"Env: {key}")
    cur         = _get_display_value(config, key, ftype)

    if ftype == "password":
        # Тохируулагдсан бол placeholder-т мэдэгдэл харуулна
        is_set = config.key_is_set(key)
        ph = "●●●●●●●● (тохируулагдсан — шинэ утга оруулахад дарж бичнэ)" if is_set else placeholder
        return gr.Textbox(
            label=label, value="", placeholder=ph,
            type="password", interactive=True, info=info,
        )

    if ftype == "dropdown":
        choices = fld.get("choices", [])
        # str болгох (number dropdown-д хэрэгтэй)
        str_choices = [str(c) for c in choices]
        cur_str = str(cur) if cur else (str_choices[0] if str_choices else "")
        if cur_str not in str_choices and str_choices:
            cur_str = str_choices[0]
        return gr.Dropdown(
            label=label, choices=str_choices, value=cur_str,
            interactive=True, info=info,
        )

    if ftype == "slider":
        mn   = fld.get("min", 0)
        mx   = fld.get("max", 100)
        step = fld.get("step", 1)
        try:
            val = float(cur) if cur != "" else float(placeholder)
        except (ValueError, TypeError):
            val = mn
        return gr.Slider(
            label=label, minimum=mn, maximum=mx, step=step,
            value=val, interactive=True, info=info,
        )

    if ftype == "number":
        try:
            val = float(cur) if cur != "" else None
        except (ValueError, TypeError):
            val = None
        return gr.Number(label=label, value=val, interactive=True, info=info)

    # text (default)
    return gr.Textbox(
        label=label, value=str(cur), placeholder=placeholder,
        interactive=True, info=info,
    )


# ── Туслах функцүүд ───────────────────────────────────────────────────────────

def _render_status_html(config: Config) -> str:
    status  = config.get_api_status()
    all_ok  = all(v["ok"] for v in status.values())

    overall = (
        '<div style="color:#4ade80;font-weight:700;margin-bottom:8px">✅ Pipeline бэлэн</div>'
        if all_ok else
        '<div style="color:#facc15;font-weight:700;margin-bottom:8px">'
        '⚠️ Зарим тохиргоо дутуу</div>'
    )

    badges = []
    for info in status.values():
        ok    = info["ok"]
        color = "#4ade80" if ok else "#facc15"
        bg    = "#052e16" if ok else "#422006"
        hint  = info.get("hint", "")
        badges.append(
            f'<span title="{hint}" style="background:{bg};color:{color};'
            f'border:1px solid {color};border-radius:6px;'
            f'padding:4px 10px;font-size:.78rem;margin:2px;display:inline-block;">'
            f'{info["label"]}</span>'
        )

    return (
        '<div style="background:#0d1117;border:1px solid #21262d;'
        'border-radius:8px;padding:12px 16px;">'
        + overall + "".join(badges) + "</div>"
    )


def _read_env_masked(config: Config) -> str:
    """
    .env файлын агуулгыг унших — API key-үүдийг маскална.
    """
    try:
        if not config._env_path.exists():
            return f"⚠️ {config._env_path} файл олдсонгүй."
        lines = config._env_path.read_text(encoding="utf-8").splitlines()
        masked = []
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if "KEY" in k.upper() and v.strip():
                    v = v[:4] + "●●●●" + v[-2:] if len(v) > 6 else "●●●●"
                    masked.append(f"{k}={v}")
                else:
                    masked.append(line)
            else:
                masked.append(line)
        return "\n".join(masked)
    except Exception as exc:
        return f"❌ Унших алдаа: {exc}"


def _get_display_value(config: Config, key: str, ftype: str) -> Any:
    val = getattr(config, key, None)
    if val is None:
        return ""
    if ftype == "password":
        return ""   # password-г UI-д харуулахгүй
    return val


def _find_field_type(key: str) -> str:
    for group in CONFIG_GROUPS:
        for fld in group["fields"]:
            if fld["key"] == key:
                return fld["type"]
    return "text"
