"""
modules/subtitle_ass.py — Word-level timestamps → ASS subtitle файл

Үйлдэл:
  - TTSResult.word_timestamps жагсаалтыг ASS форматад хөрвүүлнэ
  - Karaoke хэлбэрийн үг-тус-бүр тодруулга (\\k tag)
  - Chunk хоорондын хугацааны offset зөв тооцоолно
  - 1920×1080 видеонд тохирох фонт, байрлал

ASS subtitle format:
  [Script Info] — мета, видео хэмжээ
  [V4+ Styles]  — фонт, өнгө, байрлал
  [Events]      — Dialogue мөр тус бүр

Karaoke tag:
  {\\k<cs>}үг  — cs = сентисекунд (1cs = 10ms)
  Тухайн үгийг тодруулах хугацааг тооцоолно.

Хэрэглээ:
  builder = ASSBuilder(config)
  builder.add_chunk(chunk_index=0, tts_result=result, time_offset_ms=0)
  builder.add_chunk(chunk_index=1, tts_result=result2, time_offset_ms=8500)
  path = builder.save("/tmp/podcast.ass")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import Config

logger = logging.getLogger("subtitle_ass")

# ── ASS цаг хэлбэрлэлт ───────────────────────────────────────────────────────

def _ms_to_ass_time(ms: int) -> str:
    """
    Миллисекундыг ASS цагийн форматад хөрвүүлнэ.
    ASS: H:MM:SS.cs  (cs = centisecond = 10ms)

    Examples:
        0       → "0:00:00.00"
        1500    → "0:00:01.50"
        61234   → "0:01:01.23"
        3661000 → "1:01:01.00"
    """
    total_cs = ms // 10
    cs       = total_cs % 100
    total_s  = total_cs // 100
    s        = total_s % 60
    total_m  = total_s // 60
    m        = total_m % 60
    h        = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ms_to_centisec(ms: int) -> int:
    """Миллисекундыг центисекунд болгоно (ASS \\k tag-д)."""
    return max(1, ms // 10)


# ── Нэг мөрийн мэдээлэл ──────────────────────────────────────────────────────

@dataclass
class _SubLine:
    """ASS Dialogue мөрийн дотоод бүтэц."""
    start_ms:   int
    end_ms:     int
    karaoke:    str   # {\\k..}үг {\\k..}үг ... хэлбэрийн текст
    plain_text: str   # Цэвэр текст (debug-д)


# ── ASSBuilder ────────────────────────────────────────────────────────────────

class ASSBuilder:
    """
    ASS subtitle файл үүсгэгч.

    Хэрэглэх дэс дараалал:
        1. builder = ASSBuilder(config)
        2. for each chunk:
               builder.add_chunk(i, tts_result, offset_ms)
        3. path = builder.save(output_path)

    Args:
        config: Config объект (видео хэмжээ, фонт параметр)
    """

    # ── Дизайн константууд ────────────────────────────────────────────────
    FONT_NAME        = "Arial"
    FONT_SIZE        = 52
    MARGIN_V         = 60      # Доороос пиксел зай
    MARGIN_H         = 80      # Зүүн/баруун пиксел зай
    OUTLINE          = 3       # Текстийн хар тойм (пиксел)
    SHADOW           = 1
    # Өнгө: ASS BBGGRR hex (0x болон 00 alpha)
    COLOR_WHITE      = "&H00FFFFFF"   # Энгийн үгийн өнгө
    COLOR_HIGHLIGHT  = "&H0000FFFF"   # Тодруулсан үгийн өнгө (шар)
    COLOR_OUTLINE    = "&H00000000"   # Хар тойм
    # Нэг мөрт оруулах үгийн дээд тоо
    WORDS_PER_LINE   = 12

    def __init__(self, config: Config):
        self.cfg        = config
        self._lines:    list[_SubLine] = []
        self._video_w   = config.OUTPUT_VIDEO_WIDTH   # 1920
        self._video_h   = config.OUTPUT_VIDEO_HEIGHT  # 1080

    # ── Chunk нэмэх ──────────────────────────────────────────────────────

    def add_chunk(
        self,
        chunk_index: int,
        word_timestamps: list[dict],
        time_offset_ms:  int,
        duration_ms:     int,
    ) -> None:
        """
        Нэг TTS chunk-ийн word timestamp-уудыг субтайтл мөр болгон нэмнэ.

        Args:
            chunk_index:     Chunk-ийн дугаар (log-д)
            word_timestamps: TTSResult.word_timestamps
                             [{"word": str, "start_ms": int, "end_ms": int}]
            time_offset_ms:  Энэ chunk-ийн аудио дахь абсолют эхлэх цаг (ms)
            duration_ms:     Chunk-ийн нийт хугацаа (ms) — сүүлийн мөрийн end
        """
        if not word_timestamps:
            logger.warning(f"Chunk {chunk_index}: word_timestamps хоосон.")
            return

        # Хугацааг offset-ээр шилжүүлэх
        shifted = [
            {
                "word":     w["word"],
                "start_ms": w["start_ms"] + time_offset_ms,
                "end_ms":   w["end_ms"]   + time_offset_ms,
            }
            for w in word_timestamps
        ]

        # Үгүүдийг WORDS_PER_LINE хэмжээтэй бүлэгт хуваах
        groups = self._group_words(shifted)

        for grp in groups:
            line = self._build_line(grp)
            self._lines.append(line)

        logger.debug(
            f"Chunk {chunk_index}: {len(shifted)} үг → "
            f"{len(groups)} субтайтл мөр (offset={time_offset_ms}ms)"
        )

    # ── Хадгалах ─────────────────────────────────────────────────────────

    def save(self, output_path: str) -> str:
        """
        ASS файлыг хадгална.

        Args:
            output_path: Гаралтын файлын зам

        Returns:
            Файлын зам (абсолют)
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content = self._render_ass()
        path.write_text(content, encoding="utf-8")

        logger.info(
            f"ASS subtitle хадгалагдлаа: {path} "
            f"({len(self._lines)} мөр)"
        )
        return str(path.resolve())

    def get_content(self) -> str:
        """ASS файлын агуулгыг string буцаана (хадгалахгүй)."""
        return self._render_ass()

    # ── Дотоод логик ─────────────────────────────────────────────────────

    def _group_words(self, words: list[dict]) -> list[list[dict]]:
        """
        Үгүүдийг WORDS_PER_LINE-д оруулан бүлэглэнэ.
        Нэг бүлэг нь нэг Dialogue мөр болно.
        """
        groups: list[list[dict]] = []
        current: list[dict] = []

        for w in words:
            current.append(w)
            if len(current) >= self.WORDS_PER_LINE:
                groups.append(current)
                current = []

        if current:
            groups.append(current)

        return groups

    def _build_line(self, words: list[dict]) -> _SubLine:
        """
        Нэг бүлэг үгийг _SubLine болгоно.
        - start_ms: бүлгийн эхний үгийн эхлэл
        - end_ms: бүлгийн сүүлийн үгийн төгсгөл
        - karaoke: {\\k<cs>}үг хэлбэрийн текст

        Karaoke логик:
          {\\k<cs>} гэдэг нь тухайн үг гараад ирэхийг хүлээх хугацаа (cs).
          ASS каракэ: \\k нь тухайн бичгийг тодруулах хугацааг заана.
          Тэгвэл каракэ тэг нь:
            {\\k<prev_duration_cs>}{\\K<cur_duration_cs>}үг
          Бид {\\K} ашиглана — filled highlight (sweep effect биш).
        """
        line_start = words[0]["start_ms"]
        line_end   = words[-1]["end_ms"]

        parts: list[str] = []

        # Эхний үгийн өмнөх хугацааны "lead-in" (мөр эхлэлээс анхны үг хүртэл)
        lead_in_cs = _ms_to_centisec(words[0]["start_ms"] - line_start)

        for i, w in enumerate(words):
            word_dur_ms = w["end_ms"] - w["start_ms"]
            word_dur_cs = _ms_to_centisec(word_dur_ms)

            if i == 0 and lead_in_cs > 0:
                # Анхны үгийн өмнөх хугацаа — тодруулаагүй (\\k)
                parts.append(f"{{\\k{lead_in_cs}}}")

            parts.append(f"{{\\K{word_dur_cs}}}{w['word']}")

            # Үгийн дараа зай нэмэх (сүүлийн үгээс бусад)
            if i < len(words) - 1:
                gap_ms = words[i + 1]["start_ms"] - w["end_ms"]
                if gap_ms > 0:
                    gap_cs = _ms_to_centisec(gap_ms)
                    parts.append(f"{{\\k{gap_cs}}} ")
                else:
                    parts.append(" ")

        karaoke_text = "".join(parts)
        plain_text   = " ".join(w["word"] for w in words)

        return _SubLine(
            start_ms   = line_start,
            end_ms     = line_end,
            karaoke    = karaoke_text,
            plain_text = plain_text,
        )

    # ── ASS рэндэр ───────────────────────────────────────────────────────

    def _render_ass(self) -> str:
        """Бүрэн ASS файлын агуулгыг бүтээнэ."""
        sections = [
            self._render_script_info(),
            self._render_styles(),
            self._render_events(),
        ]
        return "\n".join(sections)

    def _render_script_info(self) -> str:
        return (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {self._video_w}\n"
            f"PlayResY: {self._video_h}\n"
            "ScaledBorderAndShadow: yes\n"
            "Collisions: Normal\n"
            "WrapStyle: 0\n"
            "YCbCr Matrix: TV.709\n"
        )

    def _render_styles(self) -> str:
        """
        V4+ Styles:
        - Default: ердийн субтайтл
        - Karaoke: тодруулга өнгөтэй (\\K тэгт хариулна)
        """
        # Format order (зайгүй таслалаар):
        # Name, Fontname, Fontsize,
        # PrimaryColour, SecondaryColour, OutlineColour, BackColour,
        # Bold, Italic, Underline, StrikeOut,
        # ScaleX, ScaleY, Spacing, Angle,
        # BorderStyle, Outline, Shadow,
        # Alignment, MarginL, MarginR, MarginV,
        # Encoding

        style_fmt = (
            "Format: Name, Fontname, Fontsize, "
            "PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        )

        # Alignment 2 = bottom center
        style_default = (
            f"Style: Default,{self.FONT_NAME},{self.FONT_SIZE},"
            f"{self.COLOR_WHITE},{self.COLOR_HIGHLIGHT},"
            f"{self.COLOR_OUTLINE},&H80000000,"
            f"-1,0,0,0,"
            f"100,100,0,0,"
            f"1,{self.OUTLINE},{self.SHADOW},"
            f"2,{self.MARGIN_H},{self.MARGIN_H},{self.MARGIN_V},1"
        )

        return (
            "[V4+ Styles]\n"
            f"{style_fmt}\n"
            f"{style_default}\n"
        )

    def _render_events(self) -> str:
        """Dialogue мөрүүдийг рэндэрлэнэ."""
        lines = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for sub in self._lines:
            start = _ms_to_ass_time(sub.start_ms)
            end   = _ms_to_ass_time(sub.end_ms)
            # Dialogue: Layer, Start, End, Style, Name, ML, MR, MV, Effect, Text
            lines.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{sub.karaoke}"
            )

        return "\n".join(lines) + "\n"

    # ── Reset ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Мөрүүдийг цэвэрлэнэ (шинэ видеонд бэлдэх)."""
        self._lines.clear()

    @property
    def line_count(self) -> int:
        return len(self._lines)
