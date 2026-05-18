"""
modules/image_runpod.py — RunPod Serverless → Flux.1 Klein зураг үүсгэгч

Үйлдэл:
  - PromptSegment жагсаалт хүлээн авна
  - RunPod Serverless /runsync endpoint-д Flux.1 Klein зургийн хүсэлт илгээнэ
  - Base64 PNG буцаж ирнэ → файлд хадгална
  - Параллел генерац: asyncio + aiohttp (хурд нэмэгдэнэ)
  - Retry логик (tenacity)
  - PromptSegment.image_path-г бөглөнө

RunPod Serverless API:
  POST https://api.runpod.io/v2/{endpoint_id}/runsync
  Headers: Authorization: Bearer {api_key}
  Body: {
    "input": {
      "prompt": "...",
      "negative_prompt": "...",
      "width": 1920,
      "height": 1080,
      "num_inference_steps": 20,
      "guidance_scale": 7.5,
      "num_images": 1
    }
  }

Response:
  {
    "id": "...",
    "status": "COMPLETED",
    "output": {
      "images": ["<base64_png>"],
      ...
    }
  }

Зарим handler-ууд output.images биш output[0] эсвэл output.image гэж буцааж болно.
Бүх нийтлэг форматыг _extract_image() функц дэмжинэ.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

import aiohttp
import aiofiles
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config import Config
from modules.prompt_grok import PromptSegment

logger = logging.getLogger("image_runpod")

# ── Константууд ───────────────────────────────────────────────────────────────
_RUNPOD_BASE        = "https://api.runpod.io/v2"
_RUNSYNC_PATH       = "/runsync"
_STATUS_PATH        = "/status/{job_id}"
_REQUEST_TIMEOUT    = aiohttp.ClientTimeout(total=320)  # RunPod timeout + buffer
_MAX_PARALLEL       = 3    # Нэгэн зэрэг илгээх хамгийн их зургийн тоо
_POLL_INTERVAL_SEC  = 3    # /status polling хугацааны зай
_POLL_MAX_ATTEMPTS  = 100  # 300 секунд

# ── Үр дүнгийн dataclass ─────────────────────────────────────────────────────

@dataclass
class ImageResult:
    """Нэг зургийн үр дүн."""
    segment_index: int
    image_path:    Optional[str]  = None
    error:         Optional[str]  = None
    latency_sec:   float          = 0.0

    @property
    def success(self) -> bool:
        return self.error is None and self.image_path is not None


@dataclass
class ImageBatchResult:
    """Бүх зургийн үр дүн."""
    results:       list[ImageResult] = field(default_factory=list)
    success_count: int               = 0
    fail_count:    int               = 0
    total_sec:     float             = 0.0

    @property
    def all_ok(self) -> bool:
        return self.fail_count == 0


# ── RunPod зураг үүсгэгч ─────────────────────────────────────────────────────

class RunPodImageGenerator:
    """
    RunPod Serverless Flux.1 Klein зураг үүсгэгч.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg = config

    # ── Нийтийн API ──────────────────────────────────────────────────────

    def generate_all(
        self,
        segments:    list[PromptSegment],
        output_dir:  str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> ImageBatchResult:
        """
        Бүх сегментийн зургийг үүсгэнэ (sync wrapper).

        Args:
            segments:    PromptSegment жагсаалт
            output_dir:  Зураг хадгалах хавтас
            progress_cb: (current, total, message) → None

        Returns:
            ImageBatchResult
        """
        if not segments:
            return ImageBatchResult()

        if not self.cfg.RUNPOD_API_KEY or not self.cfg.RUNPOD_FLUX_ENDPOINT_ID:
            logger.warning("RunPod API key эсвэл Flux endpoint ID байхгүй — Mock зураг.")
            return self._mock_generate_all(segments, output_dir, progress_cb)

        # asyncio event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self._async_generate_all(segments, output_dir, progress_cb)
        )

    # ── Async дотоод логик ────────────────────────────────────────────────

    async def _async_generate_all(
        self,
        segments:    list[PromptSegment],
        output_dir:  str,
        progress_cb: Optional[Callable[[int, int, str], None]],
    ) -> ImageBatchResult:
        """Бүх зургийг параллелаар үүсгэнэ."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        total     = len(segments)
        batch_res = ImageBatchResult(total_sec=time.time())
        done      = 0
        sem       = asyncio.Semaphore(_MAX_PARALLEL)

        connector = aiohttp.TCPConnector(limit=_MAX_PARALLEL + 2)

        async with aiohttp.ClientSession(
            connector = connector,
            timeout   = _REQUEST_TIMEOUT,
            headers   = {
                "Authorization": f"Bearer {self.cfg.RUNPOD_API_KEY}",
                "Content-Type":  "application/json",
            },
        ) as session:

            async def _run_one(seg: PromptSegment) -> ImageResult:
                nonlocal done
                async with sem:
                    result = await self._generate_one(seg, output_dir, session)
                    done += 1
                    if progress_cb:
                        status = "✅" if result.success else "❌"
                        progress_cb(
                            done, total,
                            f"{status} Зураг {seg.index+1}/{total}: "
                            f"{seg.scene_summary[:40]}…"
                        )
                    return result

            tasks   = [_run_one(seg) for seg in segments]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                res = ImageResult(segment_index=i, error=str(r))
            else:
                res = r

            batch_res.results.append(res)

            if res.success:
                batch_res.success_count += 1
                # PromptSegment-д image_path бичих
                segments[res.segment_index].image_path = res.image_path
            else:
                batch_res.fail_count += 1
                logger.error(
                    f"Зураг {res.segment_index} бүтэлгүйтлээ: {res.error}"
                )

        batch_res.total_sec = time.time() - batch_res.total_sec
        logger.info(
            f"Зургийн генерац дууслаа: "
            f"{batch_res.success_count}/{total} амжилттай, "
            f"{batch_res.total_sec:.1f}с"
        )
        return batch_res

    async def _generate_one(
        self,
        seg:       PromptSegment,
        output_dir: str,
        session:   aiohttp.ClientSession,
    ) -> ImageResult:
        """Нэг сегментийн зургийг үүсгэнэ."""
        t0 = time.time()

        try:
            image_bytes = await self._call_runsync(seg, session)
        except Exception as exc:
            return ImageResult(
                segment_index = seg.index,
                error         = str(exc),
                latency_sec   = time.time() - t0,
            )

        # Файлд хадгалах
        filename   = f"scene_{seg.index:04d}.png"
        image_path = str(Path(output_dir) / filename)

        try:
            async with aiofiles.open(image_path, "wb") as f:
                await f.write(image_bytes)
        except Exception as exc:
            return ImageResult(
                segment_index = seg.index,
                error         = f"Файл хадгалах алдаа: {exc}",
                latency_sec   = time.time() - t0,
            )

        logger.info(
            f"Зураг {seg.index}: {image_path} "
            f"({len(image_bytes)//1024}KB, {time.time()-t0:.1f}с)"
        )
        return ImageResult(
            segment_index = seg.index,
            image_path    = image_path,
            latency_sec   = time.time() - t0,
        )

    async def _call_runsync(
        self,
        seg:     PromptSegment,
        session: aiohttp.ClientSession,
    ) -> bytes:
        """
        RunPod /runsync endpoint-г дуудна.
        COMPLETED биш бол /status endpoint-ийг polling хийнэ.
        """
        endpoint_id = self.cfg.RUNPOD_FLUX_ENDPOINT_ID
        url = f"{_RUNPOD_BASE}/{endpoint_id}{_RUNSYNC_PATH}"

        payload = self._build_payload(seg)

        async with session.post(url, json=payload) as resp:
            if resp.status == 401:
                raise PermissionError("RunPod API Key буруу.")
            if resp.status == 429:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=429,
                    message="Rate limit"
                )
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"RunPod HTTP {resp.status}: {text[:300]}")

            data = await resp.json()

        # COMPLETED эсэхийг шалгах
        status = data.get("status", "")
        if status == "COMPLETED":
            return self._extract_image(data)

        if status == "FAILED":
            raise RuntimeError(f"RunPod job FAILED: {data.get('error', '')}")

        # IN_QUEUE / IN_PROGRESS → polling
        job_id = data.get("id", "")
        if not job_id:
            raise RuntimeError("RunPod: job ID байхгүй.")

        return await self._poll_status(job_id, session, endpoint_id)

    async def _poll_status(
        self,
        job_id:      str,
        session:     aiohttp.ClientSession,
        endpoint_id: str,
    ) -> bytes:
        """RunPod job дуустал /status endpoint-г polling хийнэ."""
        url = f"{_RUNPOD_BASE}/{endpoint_id}/status/{job_id}"

        for attempt in range(_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL_SEC)

            async with session.get(url) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()

            status = data.get("status", "")
            if status == "COMPLETED":
                return self._extract_image(data)
            if status == "FAILED":
                raise RuntimeError(f"RunPod job FAILED: {data.get('error', '')}")

            logger.debug(f"Job {job_id}: {status} ({attempt+1}/{_POLL_MAX_ATTEMPTS})")

        raise TimeoutError(f"RunPod job {job_id} timeout ({_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SEC}с).")

    def _build_payload(self, seg: PromptSegment) -> dict:
        """RunPod Serverless input payload үүсгэнэ."""
        full_prompt = (
            f"{seg.prompt_en}, {self.cfg.IMAGE_STYLE_SUFFIX}"
        )
        return {
            "input": {
                "prompt":               full_prompt,
                "negative_prompt":      self.cfg.IMAGE_NEGATIVE_PROMPT,
                "width":                self.cfg.RUNPOD_FLUX_WIDTH,
                "height":               self.cfg.RUNPOD_FLUX_HEIGHT,
                "num_inference_steps":  self.cfg.RUNPOD_FLUX_STEPS,
                "guidance_scale":       self.cfg.RUNPOD_FLUX_GUIDANCE,
                "num_images":           1,
            }
        }

    @staticmethod
    def _extract_image(data: dict) -> bytes:
        """
        RunPod handler-уудын олон төрлийн output форматаас зургийн байт авна.

        Дэмжих форматууд:
          output.images[0]   — base64 PNG (нийтлэг)
          output.image       — base64 PNG
          output[0]          — base64 PNG (зарим handler)
          output.image_url   — URL (татаж авах шаардлагатай — энд дэмжихгүй)
        """
        output = data.get("output", {})

        # 1. output.images жагсаалт
        if isinstance(output, dict):
            images = output.get("images") or output.get("image_base64")
            if images:
                if isinstance(images, list):
                    b64 = images[0]
                else:
                    b64 = images
                return RunPodImageGenerator._decode_b64(b64)

            # output.image string
            image_val = output.get("image") or output.get("img")
            if isinstance(image_val, str):
                return RunPodImageGenerator._decode_b64(image_val)

        # 2. output нь жагсаалт
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return RunPodImageGenerator._decode_b64(first)
            if isinstance(first, dict):
                for key in ("image", "images", "b64_json", "base64"):
                    if key in first:
                        val = first[key]
                        if isinstance(val, list):
                            val = val[0]
                        return RunPodImageGenerator._decode_b64(val)

        # 3. output нь шууд string (base64)
        if isinstance(output, str) and len(output) > 100:
            return RunPodImageGenerator._decode_b64(output)

        raise RuntimeError(
            f"RunPod output-аас зураг авч чадсангүй. "
            f"Output keys: {list(output.keys()) if isinstance(output, dict) else type(output)}"
        )

    @staticmethod
    def _decode_b64(data: str) -> bytes:
        """Data URL эсвэл цэвэр base64 string-ийг bytes болгоно."""
        if "," in data:
            data = data.split(",", 1)[1]
        # Padding засах
        data = data.strip()
        pad  = len(data) % 4
        if pad:
            data += "=" * (4 - pad)
        return base64.b64decode(data)

    # ── Mock (API key байхгүй үед) ───────────────────────────────────────

    def _mock_generate_all(
        self,
        segments:    list[PromptSegment],
        output_dir:  str,
        progress_cb: Optional[Callable[[int, int, str], None]],
    ) -> ImageBatchResult:
        """
        Mock: Бодит зураг үүсгэхгүй — хоосон PNG placeholder хадгална.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        batch = ImageBatchResult(total_sec=time.time())

        for i, seg in enumerate(segments):
            filename   = f"scene_{seg.index:04d}.png"
            image_path = str(Path(output_dir) / filename)

            # 1920×1080 хар PNG (1-фрэйм placeholder)
            png_bytes = _create_placeholder_png(
                width  = self.cfg.RUNPOD_FLUX_WIDTH,
                height = self.cfg.RUNPOD_FLUX_HEIGHT,
                text   = f"Scene {seg.index+1}: {seg.scene_summary[:50]}",
            )

            Path(image_path).write_bytes(png_bytes)
            seg.image_path = image_path

            res = ImageResult(segment_index=seg.index, image_path=image_path)
            batch.results.append(res)
            batch.success_count += 1

            if progress_cb:
                progress_cb(i + 1, len(segments), f"[MOCK] Зураг {i+1}/{len(segments)}")

        batch.total_sec = time.time() - batch.total_sec
        logger.info(f"[MOCK] {len(segments)} placeholder зураг үүслээ.")
        return batch


# ── Placeholder PNG үүсгэгч ───────────────────────────────────────────────────

def _create_placeholder_png(
    width: int = 1920,
    height: int = 1080,
    text: str = "",
) -> bytes:
    """
    Mock зориулалтын хоосон PNG байт үүсгэнэ.
    PIL ашиглана — PIL байхгүй бол minimal PNG header.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        img  = Image.new("RGB", (width, height), color=(15, 15, 30))
        draw = ImageDraw.Draw(img)

        # Текст байрлуулах
        if text:
            try:
                font = ImageFont.load_default(size=40)
            except Exception:
                font = ImageFont.load_default()

            draw.text(
                (width // 2, height // 2),
                text[:80],
                fill=(80, 80, 120),
                font=font,
                anchor="mm",
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    except ImportError:
        # PIL байхгүй бол minimal 1×1 PNG
        return (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
            b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
            b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def get_image_generator(config: Config) -> RunPodImageGenerator:
    """Config-т үндэслэн зураг үүсгэгч буцаана."""
    if not config.RUNPOD_API_KEY:
        logger.warning("RUNPOD_API_KEY байхгүй — Mock зураг үүсгэгч ашиглана.")
    return RunPodImageGenerator(config)
