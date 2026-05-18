"""
modules/talking_head.py — InfiniteTalk ярьдаг толгой видео үүсгэгч

Үйлдэл:
  - RunPod Pod дээр ажиллаж буй InfiniteTalk API-г дуудна
  - Нэгтгэсэн аудио (WAV) + аватар зурагаас ярьдаг толгой видео үүсгэнэ
  - Үр дүн нь MP4 видео — pipeline-д overlay хийхэд ашиглана
  - Retry + timeout логик (InfiniteTalk удаан байж болно)

InfiniteTalk API (RunPod Pod дэх):
  POST {INFINITETALK_ENDPOINT}/generate
  Body (multipart/form-data):
    audio:  WAV файл
    image:  Аватар зураг (PNG/JPG)
    config: JSON string { "width": 512, "height": 512, ... }

  Response:
    { "status": "ok", "video_url": "http://..../output.mp4" }
  эсвэл
    { "status": "processing", "job_id": "abc123" }

  Poll:
  GET {INFINITETALK_ENDPOINT}/status/{job_id}
    { "status": "done"|"processing"|"error", "video_url": "..." }

Онцлог:
  - Async polling (5 секунд хоорондтой, max 20 удаа)
  - Volume path-аас аватар зураг унших
  - Endpoint байхгүй бол хоосон үр дүн буцаана (pipeline зогсохгүй)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
    before_sleep_log,
)

from config import Config

logger = logging.getLogger("talking_head")

_POLL_INTERVAL_SEC = 5
_POLL_MAX_ATTEMPTS = 60   # 5 * 60 = 5 минут


# ── Үр дүн ───────────────────────────────────────────────────────────────────

@dataclass
class TalkingHeadResult:
    """InfiniteTalk дуудлагын үр дүн."""
    video_path:  Optional[str] = None
    duration_sec: float        = 0.0
    error:       Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.video_path)


# ── InfiniteTalk клиент ───────────────────────────────────────────────────────

class InfiniteTalkClient:
    """
    RunPod Pod дэх InfiniteTalk API-г ашиглан ярьдаг толгой видео үүсгэнэ.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg     = config
        self._session = requests.Session()
        if config.RUNPOD_POD_API_KEY:
            self._session.headers["Authorization"] = f"Bearer {config.RUNPOD_POD_API_KEY}"

    # ── Нийтийн generate() ───────────────────────────────────────────────

    def generate(
        self,
        audio_path: str,
        output_path: str,
    ) -> TalkingHeadResult:
        """
        Аудиоос ярьдаг толгой видео үүсгэнэ.

        Args:
            audio_path:  Нэгтгэсэн WAV файлын зам
            output_path: Гаралтын MP4 файлын зам

        Returns:
            TalkingHeadResult
        """
        if not self.cfg.INFINITETALK_ENDPOINT:
            logger.warning("INFINITETALK_ENDPOINT тохируулаагүй — алгасна.")
            return TalkingHeadResult(error="InfiniteTalk endpoint тохируулаагүй.")

        if not os.path.exists(audio_path):
            return TalkingHeadResult(error=f"Аудио файл олдсонгүй: {audio_path}")

        avatar_path = self._resolve_avatar()
        if not avatar_path:
            return TalkingHeadResult(
                error="Аватар зураг олдсонгүй. INFINITETALK_AVATAR_IMAGE тохируулна уу."
            )

        logger.info(f"InfiniteTalk эхэлж байна: {audio_path}")

        try:
            job_id_or_url = self._submit_job(audio_path, avatar_path)
        except Exception as exc:
            logger.error(f"InfiniteTalk submit алдаа: {exc}")
            return TalkingHeadResult(error=str(exc))

        # Шууд URL ирвэл татна
        if job_id_or_url.startswith("http"):
            video_url = job_id_or_url
        else:
            # Poll хийнэ
            video_url = self._poll_job(job_id_or_url)
            if not video_url:
                return TalkingHeadResult(error="InfiniteTalk timeout — видео хүлээгдсэнгүй.")

        # Видео татаж хадгалах
        try:
            video_path = self._download_video(video_url, output_path)
            logger.info(f"InfiniteTalk видео хадгалагдлаа: {video_path}")
            return TalkingHeadResult(video_path=video_path)
        except Exception as exc:
            return TalkingHeadResult(error=f"Видео татах алдаа: {exc}")

    # ── Ажил илгээх ──────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _submit_job(self, audio_path: str, avatar_path: str) -> str:
        """
        InfiniteTalk API-д ажил илгээнэ.

        Returns:
            job_id (polling хийх) эсвэл шууд video_url
        """
        endpoint = self.cfg.INFINITETALK_ENDPOINT.rstrip("/")

        # Видео хэмжээ тооцоолох (баруун доод булан overlay → жижиг)
        scale      = self.cfg.TALKING_HEAD_SCALE      # 0.22
        vid_w      = self.cfg.OUTPUT_VIDEO_WIDTH       # 1920
        vid_h      = self.cfg.OUTPUT_VIDEO_HEIGHT      # 1080
        head_w     = int(vid_w  * scale)               # ~422
        head_h     = int(vid_h  * scale)               # ~238
        # 2-ийн кратад оруулах (FFmpeg FFT шаардлага)
        head_w     = head_w - (head_w % 2)
        head_h     = head_h - (head_h % 2)

        config_json = {
            "width":       head_w,
            "height":      head_h,
            "fps":         self.cfg.OUTPUT_FPS,
            "enhancer":    "gfpgan",   # нүүрийг сайжруулах
            "background":  "transparent",
        }

        with open(audio_path,  "rb") as af, \
             open(avatar_path, "rb") as if_:

            files = {
                "audio":  ("audio.wav",   af,  "audio/wav"),
                "image":  ("avatar.png",  if_, "image/png"),
            }
            data = {"config": __import__("json").dumps(config_json)}

            resp = self._session.post(
                f"{endpoint}/generate",
                files=files,
                data=data,
                timeout=60,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"InfiniteTalk HTTP {resp.status_code}: {resp.text[:300]}"
            )

        body = resp.json()
        status = body.get("status", "")

        if status == "ok" and body.get("video_url"):
            return body["video_url"]           # Шууд URL
        if body.get("job_id"):
            return body["job_id"]              # Poll хийх
        raise RuntimeError(f"InfiniteTalk хариу буруу: {body}")

    # ── Poll ─────────────────────────────────────────────────────────────

    def _poll_job(self, job_id: str) -> Optional[str]:
        """
        Ажлын дуусахыг хүлээж poll хийнэ.

        Returns:
            video_url эсвэл None (timeout/error)
        """
        endpoint = self.cfg.INFINITETALK_ENDPOINT.rstrip("/")
        url      = f"{endpoint}/status/{job_id}"

        for attempt in range(_POLL_MAX_ATTEMPTS):
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 200:
                    body   = resp.json()
                    status = body.get("status", "")
                    if status == "done" and body.get("video_url"):
                        logger.info(
                            f"InfiniteTalk дууслаа "
                            f"({(attempt+1) * _POLL_INTERVAL_SEC}с хүлээсэн)"
                        )
                        return body["video_url"]
                    if status == "error":
                        logger.error(f"InfiniteTalk алдаа: {body}")
                        return None
                    logger.debug(
                        f"InfiniteTalk polling [{attempt+1}/{_POLL_MAX_ATTEMPTS}]: {status}"
                    )
            except Exception as exc:
                logger.warning(f"Polling алдаа: {exc}")

            time.sleep(_POLL_INTERVAL_SEC)

        logger.error("InfiniteTalk timeout — дуусаагүй.")
        return None

    # ── Видео татах ───────────────────────────────────────────────────────

    def _download_video(self, url: str, output_path: str) -> str:
        """URL-ээс видео татаж output_path-д хадгална."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        resp = self._session.get(url, stream=True, timeout=120)
        resp.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        return output_path

    # ── Аватар зураг ─────────────────────────────────────────────────────

    def _resolve_avatar(self) -> Optional[str]:
        """
        Аватар зургийн замыг шийдэнэ.
        Config дэх INFINITETALK_AVATAR_IMAGE path-г шалгана.
        """
        path = self.cfg.INFINITETALK_AVATAR_IMAGE
        if path and os.path.exists(path):
            return path

        # Volume доторх default avatar хайх
        volume_candidates = [
            os.path.join(self.cfg.RUNPOD_VOLUME_PATH, "avatar.png"),
            os.path.join(self.cfg.RUNPOD_VOLUME_PATH, "avatar.jpg"),
            os.path.join(self.cfg.RUNPOD_VOLUME_PATH, "avatar", "default.png"),
        ]
        for cand in volume_candidates:
            if os.path.exists(cand):
                logger.info(f"Аватар зураг олдлоо: {cand}")
                return cand

        logger.warning("Аватар зураг олдсонгүй.")
        return None


# ── Mock клиент ───────────────────────────────────────────────────────────────

class MockInfiniteTalkClient(InfiniteTalkClient):
    """
    Тест зориулалтын mock — бодит API дуудахгүй.
    Хоосон видео (black frame) үүсгэнэ.
    """

    def generate(
        self,
        audio_path: str,
        output_path: str,
    ) -> TalkingHeadResult:
        logger.info("[MOCK] InfiniteTalk: хоосон talking head видео үүсгэж байна…")

        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            # FFmpeg-ээр хоосон хар видео үүсгэх
            import subprocess
            w = int(self.cfg.OUTPUT_VIDEO_WIDTH  * self.cfg.TALKING_HEAD_SCALE)
            h = int(self.cfg.OUTPUT_VIDEO_HEIGHT * self.cfg.TALKING_HEAD_SCALE)
            w = w - (w % 2)
            h = h - (h % 2)

            # Аудионы уртыг авах
            duration = self._get_audio_duration(audio_path)

            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={w}x{h}:r={self.cfg.OUTPUT_FPS}",
                "-i", audio_path,
                "-t", str(duration),
                "-c:v", "libx264",
                "-c:a", "aac",
                "-shortest",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"[MOCK] Talking head видео үүслээ: {output_path}")
            return TalkingHeadResult(video_path=output_path, duration_sec=duration)

        except Exception as exc:
            logger.error(f"[MOCK] Talking head алдаа: {exc}")
            return TalkingHeadResult(error=str(exc))

    @staticmethod
    def _get_audio_duration(audio_path: str) -> float:
        """WAV файлын хугацааг авна."""
        try:
            import wave
            with wave.open(audio_path) as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 30.0


# ── Factory ───────────────────────────────────────────────────────────────────

def get_talking_head_client(config: Config) -> InfiniteTalkClient:
    """
    Config-т үндэслэн клиент буцаана.
    Endpoint байхгүй бол MockClient ашиглана.
    """
    if config.INFINITETALK_ENDPOINT:
        return InfiniteTalkClient(config)

    logger.warning(
        "INFINITETALK_ENDPOINT байхгүй — Mock client ашиглана."
    )
    return MockInfiniteTalkClient(config)
