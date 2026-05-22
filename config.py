"""
config.py — .env файл унших/хадгалах, бүх тохиргоо

Онцлог:
  - .env байхгүй ч апп нормал нээгдэнэ (safe_mode)
  - UI-ээс оруулсан утгыг /workspace/.env-д шууд бичнэ
  - Бүх API key, загвар, параметр CONFIG_GROUPS-аар UI-д харагдана
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional
from dotenv import dotenv_values, set_key, unset_key

logger = logging.getLogger("config")

_DEFAULT_ENV_PATH = Path(os.environ.get("DOTENV_PATH", "/workspace/.env"))
ENV_PATH = _DEFAULT_ENV_PATH


@dataclass
class Config:
    # ── Inworld TTS-2 ─────────────────────────────────────────────────────
    INWORLD_API_KEY:       Optional[str] = None
    INWORLD_VOICE_ID:      str           = "Dennis"
    INWORLD_LANGUAGE:      str           = "en-US"
    INWORLD_DELIVERY_MODE: str           = "CREATIVE"
    INWORLD_TTS_MAX_CHARS: int           = 2000
    INWORLD_CHUNK_TARGET:  int           = 1600

    # ── xAI Grok ──────────────────────────────────────────────────────────
    XAI_API_KEY:             Optional[str] = None
    XAI_MODEL:               str           = "grok-3-latest"
    XAI_BASE_URL:            str           = "https://api.x.ai/v1"
    XAI_PROMPT_TEMPERATURE:  float         = 0.8

    # ── RunPod Serverless (Flux.1) ─────────────────────────────────────────
    RUNPOD_API_KEY:          Optional[str] = None
    RUNPOD_FLUX_ENDPOINT_ID: str           = ""
    RUNPOD_FLUX_MODEL:       str           = "flux-1-klein"
    RUNPOD_FLUX_WIDTH:       int           = 1920
    RUNPOD_FLUX_HEIGHT:      int           = 1080
    RUNPOD_FLUX_STEPS:       int           = 20
    RUNPOD_FLUX_GUIDANCE:    float         = 7.5
    RUNPOD_FLUX_TIMEOUT:     int           = 300

    # ── RunPod Pod (InfiniteTalk) ──────────────────────────────────────────
    RUNPOD_POD_ID:            str           = ""
    RUNPOD_POD_API_KEY:       Optional[str] = None
    RUNPOD_VOLUME_PATH:       str           = "/workspace"
    INFINITETALK_ENDPOINT:    str           = ""
    INFINITETALK_AVATAR_IMAGE:str           = ""
    TALKING_HEAD_SCALE:       float         = 0.22
    TALKING_HEAD_MARGIN:      int           = 20

    # ── Видео гаралт ──────────────────────────────────────────────────────
    OUTPUT_VIDEO_WIDTH:   int = 1920
    OUTPUT_VIDEO_HEIGHT:  int = 1080
    OUTPUT_FPS:           int = 30
    OUTPUT_VIDEO_BITRATE: str = "8M"
    OUTPUT_AUDIO_BITRATE: str = "192k"
    OUTPUT_DIR:           str = "/workspace/output"
    TEMP_DIR:             str = "/tmp/podcast_tmp"

    # ── Зургийн стиль ─────────────────────────────────────────────────────
    IMAGE_STYLE_SUFFIX: str = (
        "cinematic historical photography, 8K ultra-detailed, "
        "dramatic lighting, epic wide shot, film grain, "
        "aspect ratio 16:9, award-winning cinematography"
    )
    IMAGE_NEGATIVE_PROMPT: str = (
        "cartoon, anime, illustration, modern, text, watermark, "
        "blurry, low quality, distorted, nsfw"
    )

    _safe_mode: bool = field(default=False, repr=False)
    _env_path:  Path = field(default=ENV_PATH, repr=False)

    def __init__(self, safe_mode: bool = False, env_path: Path = ENV_PATH):
        object.__setattr__(self, "_safe_mode", safe_mode)
        object.__setattr__(self, "_env_path", Path(env_path))
        for f in fields(self.__class__):
            if not f.name.startswith("_"):
                object.__setattr__(self, f.name, f.default)
        self._load_from_env()

    def _load_from_env(self) -> None:
        env_vals: dict = {}
        if self._env_path.exists():
            try:
                env_vals = dotenv_values(str(self._env_path))
                logger.info(f".env ачаалагдлаа: {self._env_path}")
            except Exception as exc:
                if not self._safe_mode:
                    raise
                logger.warning(f".env унших алдаа: {exc}")
        else:
            logger.info(f".env олдсонгүй ({self._env_path}) — default утгаар.")

        for f in fields(self.__class__):
            if f.name.startswith("_"):
                continue
            raw = os.environ.get(f.name) or env_vals.get(f.name)
            if raw is None:
                continue
            try:
                object.__setattr__(self, f.name, self._cast(raw, f.type))
            except (ValueError, TypeError) as exc:
                logger.warning(f"'{f.name}' cast алдаа: {exc}")

    @staticmethod
    def _cast(value: str, type_hint: str):
        t = str(type_hint).lower()
        if "int" in t:   return int(value)
        if "float" in t: return float(value)
        if "bool" in t:  return value.lower() in ("true", "1", "yes")
        return value

    def save_to_env(self, updates: dict[str, str]) -> dict:
        try:
            self._env_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._env_path.exists():
                self._env_path.touch()
                logger.info(f"Шинэ .env үүсгэлээ: {self._env_path}")

            changed: list[str] = []
            for key, value in updates.items():
                if not key or key.startswith("_"):
                    continue
                if value == "" or value is None:
                    try:
                        unset_key(str(self._env_path), key)
                        object.__setattr__(self, key, None)
                        changed.append(f"✗{key}")
                    except Exception:
                        pass
                    continue
                set_key(str(self._env_path), key, str(value))
                changed.append(key)
                for f in fields(self.__class__):
                    if f.name == key:
                        try:
                            object.__setattr__(self, key, self._cast(str(value), f.type))
                        except Exception:
                            object.__setattr__(self, key, value)
                        break

            logger.info(f".env хадгалагдлаа: {', '.join(changed)}")
            return {"status": "ok", "message": f"{len(changed)} тохиргоо хадгалагдлаа"}

        except PermissionError as exc:
            return {"status": "error", "message": f"Эрхгүй: {exc}"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def get_api_status(self) -> dict[str, dict]:
        def _chk(val, name, hint=""):
            ok = bool(val and str(val).strip())
            return {"ok": ok,
                    "label": f"✅ {name}" if ok else f"⚠️ {name} тохируулаагүй",
                    "hint": hint}
        return {
            "inworld":  _chk(self.INWORLD_API_KEY,  "Inworld API Key",
                              "https://studio.inworld.ai → API Keys"),
            "xai":      _chk(self.XAI_API_KEY,       "xAI Grok API Key",
                              "https://console.x.ai → API Keys"),
            "runpod":   _chk(self.RUNPOD_API_KEY,    "RunPod API Key",
                              "https://runpod.io/console/user/settings"),
            "flux_ep":  _chk(self.RUNPOD_FLUX_ENDPOINT_ID, "Flux Endpoint ID",
                              "RunPod → Serverless → Endpoints"),
            "infinitetalk": _chk(self.INFINITETALK_ENDPOINT, "InfiniteTalk URL",
                                  "Pod дэх InfiniteTalk API URL"),
        }

    def is_ready_for_pipeline(self) -> tuple[bool, list[str]]:
        status  = self.get_api_status()
        missing = [v["label"] for v in status.values() if not v["ok"]]
        return len(missing) == 0, missing

    def key_is_set(self, key: str) -> bool:
        """API key тохируулагдсан эсэхийг шалгана."""
        val = getattr(self, key, None)
        return bool(val and str(val).strip())


# ── CONFIG_GROUPS — settings_tab.py UI автоматаар үүсгэнэ ────────────────────
CONFIG_GROUPS: list[dict] = [
    {
        "id":    "inworld",
        "title": "🎙️ Inworld TTS-2",
        "fields": [
            {"key": "INWORLD_API_KEY",
             "label": "API Key",
             "type": "password",
             "placeholder": "inworld_...",
             "info": "studio.inworld.ai → API Keys"},
            {"key": "INWORLD_VOICE_ID",
             "label": "Дуу хоолой ID",
             "type": "text",
             "placeholder": "Dennis",
             "info": "Dennis, Oliver, Sarah гэх мэт"},
            {"key": "INWORLD_LANGUAGE",
             "label": "Хэл",
             "type": "text",
             "placeholder": "en-US",
             "info": "en-US | mn-MN | 100+ хэл дэмждэг"},
            {"key": "INWORLD_DELIVERY_MODE",
             "label": "Delivery Mode",
             "type": "dropdown",
             "choices": ["STABLE", "BALANCED", "CREATIVE"],
             "placeholder": "CREATIVE",
             "info": "STABLE=тогтвортой | BALANCED=дунд | CREATIVE=илэрхийлэлтэй"},
            {"key": "INWORLD_CHUNK_TARGET",
             "label": "Chunk хэмжээ (тэмдэгт)",
             "type": "number",
             "placeholder": "1600",
             "info": "Inworld хязгаар 2000 — 1600 хэрэглэхийг зөвлөнө"},
        ],
    },
    {
        "id":    "xai",
        "title": "🤖 xAI Grok (промпт үүсгэгч)",
        "fields": [
            {"key": "XAI_API_KEY",
             "label": "API Key",
             "type": "password",
             "placeholder": "xai-...",
             "info": "console.x.ai → API Keys"},
            {"key": "XAI_MODEL",
             "label": "Загвар",
             "type": "dropdown",
             "choices": ["grok-3-latest", "grok-3-mini-latest", "grok-2-latest"],
             "placeholder": "grok-3-latest",
             "info": "Зургийн промпт үүсгэхэд хэрэглэнэ"},
            {"key": "XAI_PROMPT_TEMPERATURE",
             "label": "Temperature",
             "type": "slider",
             "min": 0.0, "max": 1.0, "step": 0.05,
             "placeholder": "0.8",
             "info": "Өндөр = бүтээлч | Бага = тогтвортой"},
        ],
    },
    {
        "id":    "runpod_serverless",
        "title": "☁️ RunPod Serverless (Flux.1 зураг)",
        "fields": [
            {"key": "RUNPOD_API_KEY",
             "label": "RunPod API Key",
             "type": "password",
             "placeholder": "rp_...",
             "info": "runpod.io → Settings → API Keys"},
            {"key": "RUNPOD_FLUX_ENDPOINT_ID",
             "label": "Flux Endpoint ID",
             "type": "text",
             "placeholder": "abc123xyz",
             "info": "RunPod → Serverless → Endpoints → ID хуулна"},
            {"key": "RUNPOD_FLUX_MODEL",
             "label": "Flux загвар",
             "type": "dropdown",
             "choices": ["flux-1-klein", "flux-1-dev", "flux-1-schnell"],
             "placeholder": "flux-1-klein",
             "info": "Serverless worker дэмжих загварыг сонгоно"},
            {"key": "RUNPOD_FLUX_STEPS",
             "label": "Inference алхам",
             "type": "slider",
             "min": 4, "max": 50, "step": 1,
             "placeholder": "20",
             "info": "Өндөр = чанартай, удаан"},
            {"key": "RUNPOD_FLUX_GUIDANCE",
             "label": "Guidance Scale",
             "type": "slider",
             "min": 1.0, "max": 15.0, "step": 0.5,
             "placeholder": "7.5",
             "info": "Prompt-д хэр нарийн дагахыг тохируулна"},
            {"key": "RUNPOD_FLUX_WIDTH",
             "label": "Зургийн өргөн (px)",
             "type": "number",
             "placeholder": "1920",
             "info": "16:9 → 1920×1080"},
            {"key": "RUNPOD_FLUX_HEIGHT",
             "label": "Зургийн өндөр (px)",
             "type": "number",
             "placeholder": "1080",
             "info": "16:9 → 1920×1080"},
        ],
    },
    {
        "id":    "runpod_pod",
        "title": "🖥️ RunPod Pod (InfiniteTalk + Рэндэр)",
        "fields": [
            {"key": "INFINITETALK_ENDPOINT",
             "label": "InfiniteTalk URL",
             "type": "text",
             "placeholder": "http://pod-ip:8080",
             "info": "Pod дэх InfiniteTalk API хаяг"},
            {"key": "INFINITETALK_AVATAR_IMAGE",
             "label": "Аватар зургийн зам",
             "type": "text",
             "placeholder": "/workspace/avatar.png",
             "info": "Talking head-д хэрэглэх нүүрний зураг"},
            {"key": "RUNPOD_POD_ID",
             "label": "Pod ID",
             "type": "text",
             "placeholder": "abc123xyz",
             "info": "RunPod → Pods → Pod ID"},
            {"key": "RUNPOD_POD_API_KEY",
             "label": "Pod API Key",
             "type": "password",
             "placeholder": "rp_...",
             "info": "Pod-тэй холбоход хэрэглэх API key"},
            {"key": "RUNPOD_VOLUME_PATH",
             "label": "Volume хавтас",
             "type": "text",
             "placeholder": "/workspace",
             "info": "Network Volume mount path"},
            {"key": "TALKING_HEAD_SCALE",
             "label": "Talking Head хэмжээ",
             "type": "slider",
             "min": 0.1, "max": 0.5, "step": 0.01,
             "placeholder": "0.22",
             "info": "Видео хэмжээний харьцаа (0.22 = 22%)"},
        ],
    },
    {
        "id":    "output",
        "title": "🎬 Видео гаралт",
        "fields": [
            {"key": "OUTPUT_FPS",
             "label": "FPS",
             "type": "dropdown",
             "choices": ["24", "25", "30", "60"],
             "placeholder": "30",
             "info": "Frames per second"},
            {"key": "OUTPUT_VIDEO_BITRATE",
             "label": "Видео bitrate",
             "type": "dropdown",
             "choices": ["4M", "8M", "12M", "16M"],
             "placeholder": "8M",
             "info": "Өндөр = чанартай, том файл"},
            {"key": "OUTPUT_VIDEO_WIDTH",
             "label": "Видео өргөн",
             "type": "number",
             "placeholder": "1920",
             "info": "1920 (1080p) | 1280 (720p)"},
            {"key": "OUTPUT_VIDEO_HEIGHT",
             "label": "Видео өндөр",
             "type": "number",
             "placeholder": "1080",
             "info": "1080 (1080p) | 720 (720p)"},
            {"key": "OUTPUT_DIR",
             "label": "Гаралтын хавтас",
             "type": "text",
             "placeholder": "/workspace/output",
             "info": "Эцсийн видео хадгалагдах зам"},
            {"key": "TEMP_DIR",
             "label": "Түр хавтас",
             "type": "text",
             "placeholder": "/tmp/podcast_tmp",
             "info": "Завсрын файл хадгалах газар"},
        ],
    },
]
