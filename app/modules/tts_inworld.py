"""
modules/tts_inworld.py — Inworld TTS-2 API клиент

Хийх зүйл:
  - Chunk текстийг Inworld TTS-2 API-д илгээх
  - WAV аудио байт + word-level timestamps хүлээн авах
  - Retry логик (tenacity)
  - Аудио хугацаа тооцоолох

Inworld TTS-2 REST API:
  POST https://api.inworld.ai/tts/v1/text:synthesize
  Headers: Authorization: Basic {base64(api_key:)}
  Body: {
    "text": "...",
    "voice": { "name": "...", "languageCode": "..." },
    "audioConfig": { "audioEncoding": "LINEAR16", "sampleRateHertz": 22050 },
    "enableWordTimeOffsets": true
  }

Response:
  {
    "audioContent": "<base64 WAV>",
    "wordTimeOffsets": [
      { "word": "Сайн", "startTime": "0.000s", "endTime": "0.420s" },
      ...
    ]
  }
"""

from __future__ import annotations

import base64
import io
import logging
import struct
import wave
from dataclasses import dataclass, field
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config import Config

logger = logging.getLogger("tts_inworld")

# Inworld TTS-2 API endpoint
_TTS_ENDPOINT = "https://api.inworld.ai/tts/v1/text:synthesize"
_REQUEST_TIMEOUT = 120  # секунд


# ── Үр дүнгийн dataclass ─────────────────────────────────────────────────────

@dataclass
class TTSResult:
    """Нэг TTS дуудлагын үр дүн."""
    audio_bytes:     bytes          = b""
    duration_sec:    float          = 0.0
    word_timestamps: list[dict]     = field(default_factory=list)
    # [{"word": str, "start_ms": int, "end_ms": int}, ...]
    sample_rate:     int            = 22050
    error:           Optional[str]  = None

    @property
    def success(self) -> bool:
        return self.error is None and len(self.audio_bytes) > 0


# ── Inworld TTS клиент ────────────────────────────────────────────────────────

class InworldTTSClient:
    """
    Inworld TTS-2 API-г ашиглан текстийг аудио болгоно.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })
        self._set_auth()

    def _set_auth(self) -> None:
        """API key-г Basic Auth хэлбэрт хөрвүүлж header-т тохируулна."""
        if not self.cfg.INWORLD_API_KEY:
            logger.warning("INWORLD_API_KEY тохируулаагүй.")
            return
        # Inworld: Basic base64("api_key:")
        token = base64.b64encode(
            f"{self.cfg.INWORLD_API_KEY}:".encode()
        ).decode()
        self._session.headers["Authorization"] = f"Basic {token}"

    # ── Нийтийн synthesize() ─────────────────────────────────────────────

    def synthesize(self, text: str) -> TTSResult:
        """
        Текстийг Inworld TTS-2-ээр аудио болгоно.

        Args:
            text: TTS-д илгээх текст (~1600 тэмдэгт, max 2000)

        Returns:
            TTSResult (аудио байт + word timestamps)
        """
        if not text.strip():
            return TTSResult(error="Хоосон текст.")

        if not self.cfg.INWORLD_API_KEY:
            return TTSResult(error="INWORLD_API_KEY тохируулаагүй.")

        char_count = len(text)
        if char_count > self.cfg.INWORLD_TTS_MAX_CHARS:
            logger.warning(
                f"Текст {char_count} тэмдэгт — хязгаараас ({self.cfg.INWORLD_TTS_MAX_CHARS}) хэтэрсэн. "
                f"Таслаж авна."
            )
            text = text[:self.cfg.INWORLD_TTS_MAX_CHARS]

        logger.info(f"TTS эхэлж байна: {char_count} тэмдэгт")

        try:
            return self._call_api(text)
        except Exception as exc:
            logger.error(f"TTS дуудлага бүтэлгүйтлээ: {exc}")
            return TTSResult(error=str(exc))

    # ── API дуудлага (retry-тэй) ──────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_api(self, text: str) -> TTSResult:
        """Inworld TTS API-г дуудна (retry логиктой)."""
        payload = self._build_payload(text)

        resp = self._session.post(
            _TTS_ENDPOINT,
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )

        if resp.status_code == 401:
            raise PermissionError("Inworld API Key буруу эсвэл хугацаа нь дууссан.")
        if resp.status_code == 429:
            raise requests.ConnectionError("Rate limit — дахин оролдоно.")
        if resp.status_code != 200:
            raise RuntimeError(
                f"Inworld TTS HTTP {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        return self._parse_response(data)

    def _build_payload(self, text: str) -> dict:
        """Inworld TTS API request payload үүсгэнэ."""
        return {
            "text": text,
            "voice": {
                "name":         self.cfg.INWORLD_VOICE_ID,
                "languageCode": self.cfg.INWORLD_LANGUAGE,
            },
            "audioConfig": {
                "audioEncoding":   "LINEAR16",   # WAV (PCM 16-bit)
                "sampleRateHertz": 22050,
            },
            "enableWordTimeOffsets": True,
        }

    def _parse_response(self, data: dict) -> TTSResult:
        """
        Inworld API хариуг TTSResult болгоно.

        Inworld response format:
        {
          "audioContent": "<base64>",
          "wordTimeOffsets": [
            {
              "word": "Сайн",
              "startTime": "0.000s",
              "endTime": "0.420s"
            },
            ...
          ]
        }
        """
        # ── Аудио байт ─────────────────────────────────────────────────────
        audio_b64 = data.get("audioContent", "")
        if not audio_b64:
            return TTSResult(error="Хариуд audioContent байхгүй.")

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as exc:
            return TTSResult(error=f"Аудио base64 decode алдаа: {exc}")

        # WAV байтаас хугацаа тооцоолох
        duration_sec = self._wav_duration(audio_bytes)
        sample_rate  = self._wav_sample_rate(audio_bytes)

        # ── Word timestamps ─────────────────────────────────────────────────
        raw_ts = data.get("wordTimeOffsets", [])
        timestamps = self._parse_timestamps(raw_ts, duration_sec)

        logger.info(
            f"TTS дууслаа: {duration_sec:.2f}с, "
            f"{len(timestamps)} үг, "
            f"{len(audio_bytes):,} байт"
        )

        return TTSResult(
            audio_bytes     = audio_bytes,
            duration_sec    = duration_sec,
            word_timestamps = timestamps,
            sample_rate     = sample_rate,
        )

    # ── Timestamp боловсруулалт ───────────────────────────────────────────

    @staticmethod
    def _parse_timestamps(raw: list[dict], total_dur: float) -> list[dict]:
        """
        Inworld timestamp-г стандарт форматад хөрвүүлнэ.

        Input:  {"word": "Сайн", "startTime": "0.123s", "endTime": "0.456s"}
        Output: {"word": "Сайн", "start_ms": 123, "end_ms": 456}
        """
        result = []

        for item in raw:
            word = item.get("word", "").strip()
            if not word:
                continue

            # "1.234s" → 1234 ms
            start_ms = _parse_time_str(item.get("startTime", "0s"))
            end_ms   = _parse_time_str(item.get("endTime", "0s"))

            # Сүүлийн үгийн end_ms нь аудио хугацаатай тэнцэнэ
            if end_ms == 0 and result:
                end_ms = int(total_dur * 1000)

            result.append({
                "word":     word,
                "start_ms": start_ms,
                "end_ms":   end_ms,
            })

        # Timestamp-ийн дараалал зөв эсэхийг шалгах
        result.sort(key=lambda x: x["start_ms"])

        # Давхцсан timestamp залруулах
        for i in range(1, len(result)):
            if result[i]["start_ms"] < result[i-1]["end_ms"]:
                result[i]["start_ms"] = result[i-1]["end_ms"]

        return result

    # ── WAV боловсруулалт ─────────────────────────────────────────────────

    @staticmethod
    def _wav_duration(wav_bytes: bytes) -> float:
        """WAV байтаас хугацаа (секунд) тооцоолно."""
        try:
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                frames      = wf.getnframes()
                sample_rate = wf.getframerate()
                return frames / sample_rate if sample_rate > 0 else 0.0
        except Exception as exc:
            logger.warning(f"WAV хугацаа тооцоолж чадсангүй: {exc}")
            # Rough estimate: LINEAR16 22050Hz → bytes/sample/rate
            # 16-bit = 2 bytes, mono = 1 channel
            header_size = 44  # стандарт WAV header
            data_bytes   = max(0, len(wav_bytes) - header_size)
            return data_bytes / (22050 * 2)

    @staticmethod
    def _wav_sample_rate(wav_bytes: bytes) -> int:
        """WAV байтаас sample rate авна."""
        try:
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                return wf.getframerate()
        except Exception:
            return 22050


# ── Туслах функц ─────────────────────────────────────────────────────────────

def _parse_time_str(time_str: str) -> int:
    """
    "1.234s" эсвэл "1234ms" хэлбэрийн мөрийг миллисекунд (int) болгоно.

    Examples:
        "0.420s"  → 420
        "1.234s"  → 1234
        "500ms"   → 500
        "1500"    → 1500  (ms гэж үзнэ)
    """
    time_str = time_str.strip().lower()

    if time_str.endswith("ms"):
        try:
            return int(float(time_str[:-2]))
        except ValueError:
            return 0

    if time_str.endswith("s"):
        try:
            return int(float(time_str[:-1]) * 1000)
        except ValueError:
            return 0

    # Тоо л байвал ms гэж үзнэ
    try:
        return int(float(time_str))
    except ValueError:
        return 0


# ── Mock клиент (API key байхгүй үед тест хийхэд) ───────────────────────────

class MockInworldTTSClient(InworldTTSClient):
    """
    Тест болон демонд зориулсан mock TTS клиент.
    Бодит API дуудахгүй — хоосон WAV + жишиг timestamp үүсгэнэ.
    """

    def synthesize(self, text: str) -> TTSResult:
        logger.info(f"[MOCK] TTS: {len(text)} тэмдэгт")

        words       = text.split()
        ms_per_word = 380   # дундаж үгийн хугацаа
        total_ms    = len(words) * ms_per_word

        timestamps = [
            {
                "word":     w,
                "start_ms": i * ms_per_word,
                "end_ms":   (i + 1) * ms_per_word,
            }
            for i, w in enumerate(words)
        ]

        # Хоосон WAV файл үүсгэх (silence)
        audio_bytes = _create_silent_wav(
            duration_ms=total_ms,
            sample_rate=22050,
        )

        return TTSResult(
            audio_bytes     = audio_bytes,
            duration_sec    = total_ms / 1000,
            word_timestamps = timestamps,
            sample_rate     = 22050,
        )


def _create_silent_wav(duration_ms: int, sample_rate: int = 22050) -> bytes:
    """Тест зориулалтын silent WAV файл үүсгэнэ."""
    n_frames  = int(sample_rate * duration_ms / 1000)
    pcm_data  = b'\x00\x00' * n_frames   # 16-bit silence

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)       # Mono
        wf.setsampwidth(2)       # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)

    return buf.getvalue()


# ── Factory функц ─────────────────────────────────────────────────────────────

def get_tts_client(config: Config) -> InworldTTSClient:
    """
    Config-т үндэслэн TTS клиент буцаана.
    API key байхгүй бол MockClient ашиглана.
    """
    if config.INWORLD_API_KEY:
        return InworldTTSClient(config)

    logger.warning(
        "INWORLD_API_KEY байхгүй — Mock TTS клиент ашиглана. "
        "Бодит аудио үүсгэхгүй."
    )
    return MockInworldTTSClient(config)
