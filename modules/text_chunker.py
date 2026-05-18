"""
modules/text_chunker.py — Текстийг ~1600 тэмдэгтийн chunk болгох

Таслах дэс дараалал (semantic хамгийн сайн хадгалах):
  1. Давхар мөр шилжилт (\n\n) — параграф хил
  2. Ганц мөр шилжилт (\n) — мөр хил
  3. Өгүүлбэр төгсгөл (. ! ? …) — өгүүлбэр
  4. Пауз тэмдэг (, ; : —) — хагас өгүүлбэр
  5. Үгийн хил (зай) — хамгийн сүүлийн арга

Онцлог:
  - Chunk нь target_chars-аас хэтрэхгүй (хатуу дээд хязгаар: max_chars)
  - Хамгийн сүүлийн таслах цэгийг хайж, chunk дотор утга бүрэн байна
  - Хоосон chunk үүсгэхгүй
  - Chunk мэдээллийг ChunkInfo dataclass-д хадгална
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

from config import Config

logger = logging.getLogger("text_chunker")


# ── Chunk мэдээлэл ────────────────────────────────────────────────────────────

@dataclass
class ChunkInfo:
    """Нэг chunk-ийн мэдээлэл."""
    index:       int
    text:        str
    char_count:  int
    word_count:  int
    start_char:  int        # Эх текст дэх эхлэх байрлал
    end_char:    int        # Эх текст дэх дуусах байрлал
    split_type:  str        # Хэрхэн тасалсан: "paragraph"|"newline"|"sentence"|"clause"|"word"


# ── Таслах цэгийн тодорхойлолт ────────────────────────────────────────────────

# (pattern, split_type_name, include_delimiter_in_left)
_SPLIT_RULES: list[tuple[re.Pattern, str, bool]] = [
    # 1. Параграф: нэг болон түүнээс олон хоосон мөр
    (re.compile(r'\n{2,}'),               "paragraph", False),

    # 2. Ганц мөр шилжилт
    (re.compile(r'\n'),                   "newline",   False),

    # 3. Өгүүлбэр: . ! ? … — хойно нь зай эсвэл мөр дуусах
    (re.compile(r'(?<=[.!?…])\s+'),       "sentence",  False),

    # 4. Дундах тэмдэг: , ; : — EM dash — хойно нь зай
    (re.compile(r'(?<=[,;:—–])\s+'),      "clause",    False),

    # 5. Үгийн хил: ямар ч зай
    (re.compile(r'\s+'),                  "word",      False),
]


# ── Үндсэн класс ──────────────────────────────────────────────────────────────

class TextChunker:
    """
    Текстийг Inworld TTS-д тохирох хэмжээний chunk болгоно.

    Args:
        config: Config объект (INWORLD_CHUNK_TARGET, INWORLD_TTS_MAX_CHARS)
    """

    def __init__(self, config: Config):
        self.target_chars = config.INWORLD_CHUNK_TARGET      # 1600
        self.max_chars    = config.INWORLD_TTS_MAX_CHARS - 50 # 1950 (buffer)

    # ── Нийтийн API ──────────────────────────────────────────────────────

    def chunk(self, text: str) -> list[ChunkInfo]:
        """
        Текстийг chunk болгоно.

        Args:
            text: Бүрэн скрипт текст

        Returns:
            ChunkInfo жагсаалт (хоосон байхгүй, дэс дараатай)
        """
        if not text or not text.strip():
            logger.warning("Хоосон текст оруулсан.")
            return []

        # Текстийг цэвэрлэх: олон хоосон зай → нэг, олон \n → хоёр
        cleaned = self._clean_text(text)

        # Chunk болгох
        raw_chunks = self._split_recursive(cleaned)

        # ChunkInfo объект үүсгэх
        result: list[ChunkInfo] = []
        cursor = 0

        for i, (chunk_text, split_type) in enumerate(raw_chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue

            # Эх текстэд байрлал тооцох
            pos = cleaned.find(chunk_text, cursor)
            if pos == -1:
                pos = cursor

            info = ChunkInfo(
                index      = len(result),
                text       = chunk_text,
                char_count = len(chunk_text),
                word_count = len(chunk_text.split()),
                start_char = pos,
                end_char   = pos + len(chunk_text),
                split_type = split_type,
            )
            result.append(info)
            cursor = pos + len(chunk_text)

        logger.info(
            f"Chunking дууслаа: {len(cleaned)} тэмдэгт → "
            f"{len(result)} chunk "
            f"(дундаж {sum(c.char_count for c in result)//max(len(result),1)} тэмдэгт)"
        )
        self._log_chunk_stats(result)
        return result

    # ── Recursive split ───────────────────────────────────────────────────

    def _split_recursive(
        self,
        text: str,
        rule_index: int = 0,
    ) -> list[tuple[str, str]]:
        """
        Текстийг дэс дараалан таслах.
        Хэрэв chunk target_chars-аас бага бол буцаана.
        Хэрэв хэтэрвэл дараагийн дүрмээр дахин тасална.

        Returns:
            [(chunk_text, split_type), ...]
        """
        # Бүх дүрмийг туршсан боловч хэт урт хэвээр → хатуу таслах
        if len(text) <= self.target_chars:
            split_type = _SPLIT_RULES[rule_index - 1][1] if rule_index > 0 else "none"
            return [(text, split_type)]

        if rule_index >= len(_SPLIT_RULES):
            # Аргагүй байдлаар target_chars-аар таслах (Монгол үгийн хилд)
            return self._force_split(text)

        pattern, split_type, _ = _SPLIT_RULES[rule_index]

        # Энэ дүрмээр хэрхэн тасарч болохыг олох
        segments = self._split_by_pattern(text, pattern)

        if len(segments) <= 1:
            # Энэ дүрэм хэрэгжихгүй → дараагийнх
            return self._split_recursive(text, rule_index + 1)

        # Сегментүүдийг target_chars дотор нэгтгэх (greedy merge)
        merged = self._greedy_merge(segments, split_type)

        # Нэгтгэгдсэн chunk тус бүрт хэт урт байвал дахин таслах
        result: list[tuple[str, str]] = []
        for chunk, st in merged:
            if len(chunk) > self.max_chars:
                # Дараагийн дүрмээр дахин таслах
                result.extend(self._split_recursive(chunk, rule_index + 1))
            else:
                result.append((chunk, st))

        return result

    def _split_by_pattern(self, text: str, pattern: re.Pattern) -> list[str]:
        """Pattern-ээр текстийг хуваана, хоосон хэсгийг хасна."""
        parts = pattern.split(text)
        return [p for p in parts if p.strip()]

    def _greedy_merge(
        self,
        segments: list[str],
        split_type: str,
    ) -> list[tuple[str, str]]:
        """
        Сегментүүдийг target_chars дотор байхаар нэгтгэнэ.
        Separator-г сегментүүдийн хооронд нэмнэ.
        """
        result: list[tuple[str, str]] = []
        current_parts: list[str] = []
        current_len = 0

        separator = "\n\n" if split_type == "paragraph" else \
                    "\n"   if split_type == "newline"   else " "

        for seg in segments:
            seg_len = len(seg)
            join_len = len(separator) if current_parts else 0

            if current_len + join_len + seg_len <= self.target_chars:
                # Нэмж болно
                current_parts.append(seg)
                current_len += join_len + seg_len
            else:
                # Одоогийн chunk хадгалаад шинийг эхлэх
                if current_parts:
                    result.append((separator.join(current_parts), split_type))
                current_parts = [seg]
                current_len   = seg_len

        if current_parts:
            result.append((separator.join(current_parts), split_type))

        return result

    def _force_split(self, text: str) -> list[tuple[str, str]]:
        """
        Аргагүй байдлаар target_chars-аар таслах.
        Монгол үгийн хил (зай) дээр таслахыг хичээнэ.
        """
        result: list[tuple[str, str]] = []
        start = 0

        while start < len(text):
            end = start + self.target_chars

            if end >= len(text):
                result.append((text[start:].strip(), "word"))
                break

            # Зайн хил хайх (буцах чиглэлд)
            split_at = end
            for i in range(end, max(start, end - 200), -1):
                if text[i] in " \t\n":
                    split_at = i
                    break

            chunk = text[start:split_at].strip()
            if chunk:
                result.append((chunk, "word"))
            start = split_at + 1

        return result

    # ── Туслах ────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_text(text: str) -> str:
        """
        Текстийг цэвэрлэх:
        - 3+ хоосон мөр → 2 болгох (параграф хил)
        - Тэмдэгтийн орон зайг нормалчлах
        - Unicode whitespace → ASCII зай
        """
        # Unicode whitespace (zero-width, non-break гэх мэт) → ASCII зай
        text = re.sub(r'[\u00a0\u200b\u200c\u200d\u2060\ufeff]', ' ', text)

        # 3+ хоосон мөр → 2
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Мөрийн эцсийн зай
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = re.sub(r'\n[ \t]+', '\n', text)

        # Олон зай → нэг
        text = re.sub(r'[ \t]{2,}', ' ', text)

        return text.strip()

    @staticmethod
    def _log_chunk_stats(chunks: list[ChunkInfo]) -> None:
        """Chunk статистик лог."""
        if not chunks:
            return
        chars = [c.char_count for c in chunks]
        types = {}
        for c in chunks:
            types[c.split_type] = types.get(c.split_type, 0) + 1

        logger.debug(
            f"Chunk статистик: "
            f"min={min(chars)}, max={max(chars)}, "
            f"avg={sum(chars)//len(chars)} тэмдэгт | "
            f"Таслалт: {types}"
        )


# ── Preview функц (UI-д харуулахад) ──────────────────────────────────────────

def preview_chunks(text: str, config: Config) -> str:
    """
    Chunk-уудын урьдчилан харах текст үүсгэнэ.
    Gradio Textbox-д харуулна.
    """
    chunker = TextChunker(config)
    chunks  = chunker.chunk(text)

    if not chunks:
        return "Текст хоосон байна."

    lines = [
        f"📊 Нийт: {len(chunks)} chunk | "
        f"{sum(c.char_count for c in chunks):,} тэмдэгт\n"
        + "─" * 60
    ]

    for c in chunks:
        badge = {
            "paragraph": "¶",
            "newline":   "↵",
            "sentence":  ".",
            "clause":    ",",
            "word":      "·",
            "none":      " ",
        }.get(c.split_type, "?")

        lines.append(
            f"\n[{badge}] Chunk {c.index+1:02d} — "
            f"{c.char_count} тэмдэгт, {c.word_count} үг\n"
            f"{c.text[:120]}{'…' if len(c.text) > 120 else ''}"
        )

    lines.append("\n" + "─" * 60)
    return "\n".join(lines)
