"""
modules/video_composer.py — FFmpeg: зураг + talking head + аудио + субтайтл → 1080p MP4

Архитектур:
  ┌─────────────────────────────────────────────────────────────┐
  │  image_timeline (зураг + хугацаа)                          │
  │  [img0: 12.3с] [img1: 8.7с] [img2: 15.1с] ...             │
  │         ↓ ffmpeg concat + scale                            │
  │  background_video (1920×1080, 30fps)                       │
  │         ↓ overlay (баруун доод булан)                      │
  │  talking_head_video (жижиг, ~422×238)                      │
  │         ↓ audio                                            │
  │  merged_audio.wav                                          │
  │         ↓ subtitle (ASS)                                   │
  │  subtitles.ass (karaoke, bottom center)                    │
  │         ↓                                                  │
  │  final_podcast.mp4 (1920×1080, H.264, AAC, 30fps)         │
  └─────────────────────────────────────────────────────────────┘

Talking head байрлал:
  - Видеоны баруун доод булан
  - TALKING_HEAD_SCALE * видео хэмжээ (жишээ: 22% → ~422×238)
  - TALKING_HEAD_MARGIN пиксел зайтай хүрээнээс

FFmpeg pipeline (нэг дамжуулалт):
  1. Зургуудыг concat filter-ээр нэгтгэж background болгох
  2. Talking head-г overlay хийх (баруун доод)
  3. ASS субтайтлыг ass filter-ээр шатхах
  4. Аудио нэмэх
  5. H.264 + AAC кодлох
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from config import Config

logger = logging.getLogger("video_composer")


class VideoComposer:
    """
    FFmpeg ашиглан эцсийн подкаст видео үүсгэнэ.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._check_ffmpeg()

    # ── Нийтийн compose() ────────────────────────────────────────────────

    def compose(
        self,
        image_timeline: list[dict],
        audio_path:     str,
        subtitle_path:  Optional[str],
        talking_head_path: Optional[str],
        output_path:    str,
    ) -> str:
        """
        Бүрэн видео эвлүүлнэ.

        Args:
            image_timeline: [{"path": str, "duration": float, "index": int}, ...]
                            duration = тухайн зурагт хамаарах аудионы хугацаа (секунд)
            audio_path:     Нэгтгэсэн WAV аудионы зам
            subtitle_path:  ASS субтайтл файлын зам (None бол субтайтлгүй)
            talking_head_path: InfiniteTalk MP4 видеоны зам (None бол overlay байхгүй)
            output_path:    Гаралтын MP4 файлын зам

        Returns:
            output_path (абсолют)
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if not image_timeline:
            raise ValueError("image_timeline хоосон — зураг байхгүй байна.")
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Аудио файл олдсонгүй: {audio_path}")

        logger.info(
            f"Видео эвлүүлэлт эхэлж байна: "
            f"{len(image_timeline)} зураг, "
            f"talking_head={'тийм' if talking_head_path else 'үгүй'}, "
            f"subtitle={'тийм' if subtitle_path else 'үгүй'}"
        )

        # Зургуудын нийт хугацаа
        total_dur = sum(item["duration"] for item in image_timeline)
        logger.info(f"Нийт видео хугацаа: {total_dur:.1f}с")

        # Concat list файл үүсгэх (ffmpeg concat demuxer)
        concat_file = self._write_concat_file(image_timeline)

        try:
            cmd = self._build_ffmpeg_cmd(
                concat_file       = concat_file,
                audio_path        = audio_path,
                subtitle_path     = subtitle_path,
                talking_head_path = talking_head_path,
                output_path       = output_path,
                total_dur         = total_dur,
            )

            logger.debug("FFmpeg команд: " + " ".join(cmd))
            self._run_ffmpeg(cmd)

            logger.info(f"Видео амжилттай үүслээ: {output_path}")
            return str(Path(output_path).resolve())

        finally:
            # Түр файл устгах
            try:
                os.unlink(concat_file)
            except Exception:
                pass

    # ── FFmpeg командын үүсгэлт ──────────────────────────────────────────

    def _build_ffmpeg_cmd(
        self,
        concat_file:       str,
        audio_path:        str,
        subtitle_path:     Optional[str],
        talking_head_path: Optional[str],
        output_path:       str,
        total_dur:         float,
    ) -> list[str]:
        """
        FFmpeg командыг бүтээнэ.

        Filter graph тайлбар:
          [0:v]     → background зургуудын concat видео
          scale     → 1920×1080
          [1:v]     → talking head видео (байгаа бол)
          overlay   → баруун доод булан
          subtitles → ASS текст шатгах
          [a:0]     → аудио
        """
        W = self.cfg.OUTPUT_VIDEO_WIDTH    # 1920
        H = self.cfg.OUTPUT_VIDEO_HEIGHT   # 1080
        fps    = self.cfg.OUTPUT_FPS       # 30
        vbr    = self.cfg.OUTPUT_VIDEO_BITRATE   # "8M"
        abr    = self.cfg.OUTPUT_AUDIO_BITRATE   # "192k"

        # Talking head overlay тооцоолол
        scale  = self.cfg.TALKING_HEAD_SCALE    # 0.22
        margin = self.cfg.TALKING_HEAD_MARGIN   # 20
        th_w   = int(W * scale)
        th_h   = int(H * scale)
        th_w   = th_w - (th_w % 2)
        th_h   = th_h - (th_h % 2)
        th_x   = W - th_w - margin
        th_y   = H - th_h - margin

        # ── Input тодорхойлолт ────────────────────────────────────────────
        cmd: list[str] = ["ffmpeg", "-y"]

        # Input 0: зургуудын concat (loop=0 → нэг удаа тоглуулна)
        cmd += [
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
        ]
        # Input 1: аудио
        cmd += ["-i", audio_path]

        # Input 2: talking head (байгаа бол)
        has_talking_head = (
            talking_head_path
            and os.path.exists(talking_head_path)
        )
        if has_talking_head:
            cmd += ["-i", talking_head_path]

        # ── Filter graph ──────────────────────────────────────────────────
        # Зургуудыг scale → pad → fps тохируулах
        bg_filter = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"setsar=1,"
            f"fps={fps}[bg]"
        )

        filters: list[str] = [bg_filter]
        last_v = "[bg]"

        if has_talking_head:
            # Talking head-г scale + overlay
            th_filter = (
                f"[2:v]scale={th_w}:{th_h}[th];"
                f"{last_v}[th]overlay={th_x}:{th_y}:shortest=1[ov]"
            )
            filters.append(th_filter)
            last_v = "[ov]"

        if subtitle_path and os.path.exists(subtitle_path):
            # ASS субтайтл шатгах
            # Замд ерөнхий таслалын тэмдэгт байвал escape хийх
            safe_sub = subtitle_path.replace("\\", "/").replace(":", "\\:")
            sub_filter = f"{last_v}ass='{safe_sub}'[sv]"
            filters.append(sub_filter)
            last_v = "[sv]"

        filter_graph = ";".join(filters)
        cmd += ["-filter_complex", filter_graph]
        cmd += ["-map", last_v, "-map", "1:a"]

        # ── Кодлох ───────────────────────────────────────────────────────
        cmd += [
            # Видео
            "-c:v",        "libx264",
            "-preset",     "fast",
            "-crf",        "18",
            "-b:v",        vbr,
            "-maxrate",    vbr,
            "-bufsize",    "16M",
            "-profile:v",  "high",
            "-pix_fmt",    "yuv420p",
            "-r",          str(fps),
            # Аудио
            "-c:a",        "aac",
            "-b:a",        abr,
            "-ar",         "44100",
            # Хугацааны хязгаар (нийт аудионы урттай тэнцэнэ)
            "-t",          f"{total_dur:.3f}",
            # Moov atom — streaming-д ашиглахад
            "-movflags",   "+faststart",
            output_path,
        ]

        return cmd

    # ── Concat файл ───────────────────────────────────────────────────────

    def _write_concat_file(self, image_timeline: list[dict]) -> str:
        """
        FFmpeg concat demuxer-т зориулсан жагсаалт файл үүсгэнэ.

        Формат:
            file '/path/to/image.png'
            duration 12.300
            file '/path/to/image2.png'
            duration 8.700
            ...
        """
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            prefix="podcast_concat_",
        )

        for item in image_timeline:
            path = item["path"]
            dur  = float(item["duration"])

            if not path or not os.path.exists(path):
                logger.warning(f"Зураг олдсонгүй, алгасна: {path}")
                continue

            # Зам дэх нэг хашилтаас escape хийх
            safe_path = str(path).replace("'", r"'\''")
            tmp.write(f"file '{safe_path}'\n")
            tmp.write(f"duration {dur:.6f}\n")

        # FFmpeg concat demuxer-ийн шаардлага:
        # Сүүлийн файлыг дахин нэмэх (duration алдагдахгүй)
        if image_timeline:
            last = image_timeline[-1]
            if last["path"] and os.path.exists(last["path"]):
                safe_path = str(last["path"]).replace("'", r"'\''")
                tmp.write(f"file '{safe_path}'\n")

        tmp.close()
        return tmp.name

    # ── FFmpeg ажиллуулах ─────────────────────────────────────────────────

    @staticmethod
    def _run_ffmpeg(cmd: list[str]) -> None:
        """FFmpeg командыг ажиллуулж алдаа шалгана."""
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            # FFmpeg stderr-ийн сүүлийн 40 мөрийг харуулна
            stderr_tail = "\n".join(result.stderr.splitlines()[-40:])
            raise RuntimeError(
                f"FFmpeg алдааны код {result.returncode}:\n{stderr_tail}"
            )

        # Анхааруулгыг лог-д бичих
        for line in result.stderr.splitlines():
            if "warning" in line.lower() or "error" in line.lower():
                logger.debug(f"ffmpeg: {line}")

    # ── FFmpeg шалгах ─────────────────────────────────────────────────────

    @staticmethod
    def _check_ffmpeg() -> None:
        """FFmpeg системд суулгасан эсэхийг шалгана."""
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                check=True,
            )
            ver_line = r.stdout.decode().splitlines()[0] if r.stdout else "?"
            logger.info(f"FFmpeg олдлоо: {ver_line}")
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.warning(
                "FFmpeg олдсонгүй! "
                "Суулгахын тулд: apt-get install -y ffmpeg"
            )

    # ── Preview thumbnail ─────────────────────────────────────────────────

    def extract_thumbnail(self, video_path: str, output_path: str, time_sec: float = 5.0) -> Optional[str]:
        """
        Видеоноос preview зураг авна.

        Args:
            video_path:  MP4 файлын зам
            output_path: PNG гаралтын зам
            time_sec:    Хэдэн секундын кадрыг авах

        Returns:
            output_path эсвэл None (алдаа гарвал)
        """
        try:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(time_sec),
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "2",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            return output_path
        except Exception as exc:
            logger.warning(f"Thumbnail гарахгүй байна: {exc}")
            return None

    def get_video_info(self, video_path: str) -> dict:
        """
        FFprobe-оор видеоны мэдээлэл авна.

        Returns:
            {"duration": float, "width": int, "height": int, "fps": float}
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            import json
            data    = json.loads(result.stdout)
            streams = data.get("streams", [])
            v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})

            fps_str = v_stream.get("r_frame_rate", "30/1")
            num, den = (int(x) for x in fps_str.split("/")) if "/" in fps_str else (30, 1)

            return {
                "duration": float(v_stream.get("duration", 0)),
                "width":    int(v_stream.get("width", 0)),
                "height":   int(v_stream.get("height", 0)),
                "fps":      num / den if den else 30.0,
            }
        except Exception as exc:
            logger.warning(f"Видео мэдээлэл авахад алдаа: {exc}")
            return {"duration": 0, "width": 0, "height": 0, "fps": 30.0}
