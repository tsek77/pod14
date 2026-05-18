"""
modules/prompt_grok.py — Grok AI: ярианы утгаар зургийн промпт үүсгэх

Гол зорилт:
  - Подкастын скриптийг дүн шинжилгээ хийж семантик "сцен" болгон хуваана
  - Сцен бүрт нэг Flux.1 зургийн промпт + хугацааны хүрээ тооцоолно
  - Хугацааг word timestamp-аас тооцоолно → зурагт харагдах хугацаа = тухайн сцений ярианы уртаас шалтгаална
  - Ижил тооны тэмдэгт/үгээр хуваахгүй — утгын дагуу хуваана

Алгоритм:
  1. Бүх chunk-ийн текст + word timestamp-уудыг нэгтгэж Grok-д илгээнэ
  2. Grok нь текстийг семантик сцен болгон хуваана:
     - Нэг сцен = нэг гол санаа, дүр, тайлбар (≥1 өгүүлбэр)
     - Тухайн сцений эхлэл/төгсгөлийг word timestamp-ын word-ийн эхний болон сүүлийн
       миллисекундаар тооцоолно
  3. Гаралт: PromptSegment жагсаалт
     - prompt_en: Flux.1-д орох англи промпт
     - start_ms, end_ms: видеонд харагдах хугацаа
     - scene_text: Монгол ярианы хэсэг (debug)

Хязгаарлалт:
  - Нэг скриптэд хамгийн ихдээ MAX_PROMPTS зураг
  - Хамгийн богино сцен MIN_SCENE_DURATION_SEC секунд (хэт богино зургаас зайлсхийнэ)

Grok системийн промпт:
  - Монгол подкаст скрипт
  - Историк синематик зургийн промпт
  - Зөвхөн JSON гаралт
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from config import Config

logger = logging.getLogger("prompt_grok")

# ── Константууд ───────────────────────────────────────────────────────────────
MAX_PROMPTS           = 30      # Нэг видеонд хамгийн ихдээ
MIN_SCENE_DURATION_MS = 3000    # Хамгийн богино сцен (3 секунд)
MAX_SCENE_DURATION_MS = 30_000  # Хамгийн урт сцен (30 секунд)
MAX_INPUT_TOKENS      = 6000    # Grok-д илгээх текстийн дээд хязгаар (тэмдэгт)

# ── Grok системийн промпт ─────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
Та Монгол подкастын синематик зургийн промпт үүсгэгч туслагч юм.

Танд Монгол хэлний подкастын скрипт + word-level timestamp өгнө.
Word timestamp формат: {"word": "...", "start_ms": 1234, "end_ms": 1500}

ДААЛГАВАР:
1. Скриптийг семантик агуулгаар "сцен" болгон хуваа.
   - Нэг сцен = нэг гол санаа, тайлбар, үйл явдал, дүр зэрэг.
   - Сцен нь хамгийн ихдээ {max_prompts} байна.
   - Хуваахдаа тэмдэгт/үг тоогоор биш, УТГААР нь хуваа.
   - Нэг сцений ярианы хугацаа {min_sec}–{max_sec} секунд байхыг хичээ.

2. Сцен бүрт:
   a) Flux.1 Klein-д зориулсан АНГЛИ синематик зургийн промпт бичнэ.
      - Историк, реалист, синематик хэлбэртэй.
      - 16:9 харьцаа, IMAX кино зургийн чанартай.
      - Тухайн сценд яригдаж буй агуулга, орчин, дүрийг тусгана.
      - Орчин үеийн зүйл, текст, лого, усны тэмдэг байхгүй байна.
   b) start_ms: Тухайн сцений эхний үгийн start_ms
   c) end_ms: Тухайн сцений сүүлийн үгийн end_ms
   d) scene_summary: Сцений агуулгын товч Монгол тайлбар (1 өгүүлбэр)

ГАРАЛТЫН ФОРМАТ — зөвхөн JSON (код блок, тайлбар байхгүй):
[
  {
    "prompt_en": "...",
    "start_ms": 0,
    "end_ms": 5200,
    "scene_summary": "..."
  },
  ...
]
"""

_USER_TEMPLATE = """\
СКРИПТ:
{script_text}

WORD TIMESTAMPS (JSON):
{timestamps_json}

Дээрх скрипт, timestamp-д үндэслэн зургийн промпт жагсаалт үүсгэ.
Нийт {total_duration_sec:.1f} секунд аудио, {word_count} үг.
"""


# ── Үр дүнгийн dataclass ─────────────────────────────────────────────────────

@dataclass
class PromptSegment:
    """Нэг сцений зургийн промпт + хугацааны мэдээлэл."""
    index:         int
    prompt_en:     str          # Flux.1-д орох промпт
    start_ms:      int          # Видеонд харагдах эхлэх цаг
    end_ms:        int          # Видеонд харагдах дуусах цаг
    scene_summary: str          # Товч Монгол тайлбар
    image_path:    Optional[str] = None   # Үүссэний дараа image_runpod.py-аас бөглөнө

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000

    @property
    def full_prompt(self) -> str:
        """Config-ийн IMAGE_STYLE_SUFFIX дагалдуулсан бүрэн промпт."""
        return self.prompt_en  # Suffix-г image_runpod.py-д нэмнэ


@dataclass
class PromptGenResult:
    """prompt_grok.py-ийн бүх гаралт."""
    segments:      list[PromptSegment] = field(default_factory=list)
    total_ms:      int                 = 0
    error:         Optional[str]       = None

    @property
    def success(self) -> bool:
        return self.error is None and len(self.segments) > 0


# ── Grok клиент ───────────────────────────────────────────────────────────────

class GrokPromptGenerator:
    """
    Grok AI (xAI) ашиглан ярианы утгад суурилсан зургийн промпт үүсгэнэ.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._client: Optional[OpenAI] = None
        if config.XAI_API_KEY:
            self._client = OpenAI(
                api_key  = config.XAI_API_KEY,
                base_url = config.XAI_BASE_URL,
            )

    # ── Нийтийн API ──────────────────────────────────────────────────────

    def generate(
        self,
        chunks_text:       list[str],
        all_timestamps:    list[list[dict]],
        chunk_offsets_ms:  list[int],
    ) -> PromptGenResult:
        """
        Бүх chunk-ийн текст + timestamp-аас зургийн промпт үүсгэнэ.

        Args:
            chunks_text:      Chunk бүрийн текст ["текст1", "текст2", ...]
            all_timestamps:   Chunk бүрийн word timestamps
                              [[{"word":..,"start_ms":..,"end_ms":..}, ...], ...]
            chunk_offsets_ms: Chunk бүрийн абсолют эхлэх цаг [0, 8500, 15200, ...]

        Returns:
            PromptGenResult
        """
        if not self._client:
            return self._mock_generate(chunks_text, all_timestamps, chunk_offsets_ms)

        # ── Нэгтгэлт ─────────────────────────────────────────────────────
        merged_text, merged_ts = self._merge_chunks(
            chunks_text, all_timestamps, chunk_offsets_ms
        )

        if not merged_text.strip():
            return PromptGenResult(error="Хоосон текст.")

        total_ms = merged_ts[-1]["end_ms"] if merged_ts else 0

        logger.info(
            f"Grok промпт үүсгэж байна: {len(merged_text)} тэмдэгт, "
            f"{len(merged_ts)} үг, {total_ms/1000:.1f}с"
        )

        try:
            raw_json = self._call_grok(merged_text, merged_ts, total_ms)
            segments = self._parse_response(raw_json, merged_ts, total_ms)
            segments = self._validate_and_fix(segments, total_ms)

            logger.info(f"Grok: {len(segments)} промпт сцен үүслээ.")
            return PromptGenResult(segments=segments, total_ms=total_ms)

        except Exception as exc:
            logger.error(f"Grok промпт алдаа: {exc}")
            return PromptGenResult(error=str(exc))

    # ── Grok дуудлага ─────────────────────────────────────────────────────

    def _call_grok(
        self,
        text:     str,
        timestamps: list[dict],
        total_ms: int,
    ) -> str:
        """Grok API-г дуудаж raw JSON string буцаана."""

        # Timestamp-уудыг товчлох (хэт урт болохоос сэргийлэх)
        ts_compact = self._compact_timestamps(timestamps)
        ts_json    = json.dumps(ts_compact, ensure_ascii=False)

        # Текст хэт урт бол тайрах
        script_text = text[:MAX_INPUT_TOKENS] if len(text) > MAX_INPUT_TOKENS else text

        system = _SYSTEM_PROMPT.format(
            max_prompts = min(MAX_PROMPTS, max(3, len(ts_compact) // 20)),
            min_sec     = MIN_SCENE_DURATION_MS // 1000,
            max_sec     = MAX_SCENE_DURATION_MS // 1000,
        )

        user = _USER_TEMPLATE.format(
            script_text       = script_text,
            timestamps_json   = ts_json[:8000],    # JSON-г тайрах
            total_duration_sec= total_ms / 1000,
            word_count        = len(timestamps),
        )

        resp = self._client.chat.completions.create(
            model       = self.cfg.XAI_MODEL,
            temperature = self.cfg.XAI_PROMPT_TEMPERATURE,
            max_tokens  = 3000,
            messages    = [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )

        return resp.choices[0].message.content or ""

    # ── Хариу боловсруулах ────────────────────────────────────────────────

    def _parse_response(
        self,
        raw: str,
        all_timestamps: list[dict],
        total_ms: int,
    ) -> list[PromptSegment]:
        """
        Grok-ийн JSON хариуг PromptSegment жагсаалт болгоно.
        JSON parse алдаа гарвал regex-ээр сэргээхийг оролдоно.
        """
        # Markdown code block устгах
        cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip()
        cleaned = re.sub(r'```\s*$', '', cleaned).strip()

        # JSON эхлэх [ хайх
        start = cleaned.find('[')
        end   = cleaned.rfind(']') + 1
        if start == -1 or end == 0:
            raise ValueError(f"JSON олдсонгүй Grok хариуд:\n{raw[:300]}")

        json_str = cleaned[start:end]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            # Нийтлэг алдааг засах оролдлого
            json_str = self._fix_json(json_str)
            data = json.loads(json_str)

        segments = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue

            prompt = item.get("prompt_en", "").strip()
            if not prompt:
                continue

            start_ms = int(item.get("start_ms", 0))
            end_ms   = int(item.get("end_ms", total_ms))
            summary  = item.get("scene_summary", f"Сцен {i+1}")

            segments.append(PromptSegment(
                index         = i,
                prompt_en     = prompt,
                start_ms      = start_ms,
                end_ms        = end_ms,
                scene_summary = summary,
            ))

        return segments

    def _validate_and_fix(
        self,
        segments: list[PromptSegment],
        total_ms: int,
    ) -> list[PromptSegment]:
        """
        Сегментүүдийн хугацааг шалгаж, давхцсан эсвэл буруу утгыг засна.

        Дүрмүүд:
          - start_ms < end_ms байх ёстой
          - Дараалал зөв байх ёстой (start нь нэмэгдэх)
          - Хамгийн богино MIN_SCENE_DURATION_MS
          - Сүүлийн сегментийн end_ms = total_ms
          - Давхцсан хугацаа → өмнөх сегментийн end дагах
        """
        if not segments:
            return segments

        valid = []
        prev_end = 0

        for seg in segments:
            # Буруу дараалал засах
            if seg.start_ms < prev_end:
                seg.start_ms = prev_end

            # Хэт богино → сунгах
            if seg.end_ms - seg.start_ms < MIN_SCENE_DURATION_MS:
                seg.end_ms = seg.start_ms + MIN_SCENE_DURATION_MS

            # Нийт хугацааг хэтрэхгүй байх
            seg.end_ms = min(seg.end_ms, total_ms)

            if seg.start_ms >= seg.end_ms:
                logger.debug(f"Сегмент {seg.index} алгасав (start≥end).")
                continue

            valid.append(seg)
            prev_end = seg.end_ms

        # Index дахин дугаарлах
        for i, seg in enumerate(valid):
            seg.index = i

        # Сүүлийн сегментийн end_ms нь total_ms-тэй тэнцэх
        if valid:
            valid[-1].end_ms = total_ms

        return valid

    # ── Chunk нэгтгэлт ────────────────────────────────────────────────────

    @staticmethod
    def _merge_chunks(
        chunks_text:      list[str],
        all_timestamps:   list[list[dict]],
        chunk_offsets_ms: list[int],
    ) -> tuple[str, list[dict]]:
        """
        Chunk-уудыг нэг том текст + нэгтгэсэн timestamp жагсаалт болгоно.
        Timestamp-ийн хугацааг offset-ээр шилжүүлнэ.
        """
        merged_text = "\n\n".join(chunks_text)
        merged_ts: list[dict] = []

        for chunk_idx, (ts_list, offset) in enumerate(
            zip(all_timestamps, chunk_offsets_ms)
        ):
            for w in ts_list:
                merged_ts.append({
                    "word":     w["word"],
                    "start_ms": w["start_ms"] + offset,
                    "end_ms":   w["end_ms"]   + offset,
                })

        # Дараалал зөв байх
        merged_ts.sort(key=lambda x: x["start_ms"])
        return merged_text, merged_ts

    @staticmethod
    def _compact_timestamps(timestamps: list[dict]) -> list[dict]:
        """
        Token хэрэглээ багасгахын тулд timestamp-уудыг товчолно.
        Зөвхөн үг + эхлэх цаг → [{"w": "Монгол", "s": 1234}, ...]
        """
        return [
            {"w": t["word"], "s": t["start_ms"], "e": t["end_ms"]}
            for t in timestamps
        ]

    @staticmethod
    def _fix_json(json_str: str) -> str:
        """Нийтлэг JSON алдааг автоматаар засах."""
        # Trailing comma before ] or }
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        # Single quotes → double
        json_str = json_str.replace("'", '"')
        # Unescaped newlines in strings
        json_str = re.sub(r'(?<!\\)\n(?!["\]\}])', ' ', json_str)
        return json_str

    # ── Mock (API key байхгүй үед) ───────────────────────────────────────

    def _mock_generate(
        self,
        chunks_text:      list[str],
        all_timestamps:   list[list[dict]],
        chunk_offsets_ms: list[int],
    ) -> PromptGenResult:
        """
        Grok API key байхгүй үед жишиг промптууд буцаана.
        Текстийг хугацааны хувьд тэнцүү хуваана (mock зориулалт).
        """
        logger.warning("XAI_API_KEY байхгүй — Mock промпт ашиглана.")

        merged_text, merged_ts = self._merge_chunks(
            chunks_text, all_timestamps, chunk_offsets_ms
        )

        if not merged_ts:
            return PromptGenResult(error="Timestamp хоосон.")

        total_ms = merged_ts[-1]["end_ms"]

        # Текстийг өгүүлбэрээр хуваах (mock сцен)
        sentences = re.split(r'(?<=[.!?])\s+', merged_text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            sentences = [merged_text[:200]]

        # Хугацааг тэнцүү хуваах (mock-д л хэрэглэнэ)
        n        = min(len(sentences), MAX_PROMPTS)
        dur_each = total_ms // max(n, 1)

        mock_prompts = [
            "ancient mongolian warriors on horseback, vast steppe landscape, golden sunrise, epic wide shot, cinematic historical photography, 8K ultra-detailed, dramatic lighting",
            "medieval fortress walls with torches at night, stone architecture, misty atmosphere, cinematic historical scene",
            "traditional mongolian ger camp surrounded by mountains, nomadic life, warm firelight, documentary photography style",
            "great khan court ceremony, silk robes, candlelight, historical epic, cinematic composition",
            "battle scene from bird's eye view, dust and motion, medieval warfare, dramatic cinematic lighting",
        ]

        segments = []
        for i in range(n):
            seg = PromptSegment(
                index         = i,
                prompt_en     = mock_prompts[i % len(mock_prompts)],
                start_ms      = i * dur_each,
                end_ms        = (i + 1) * dur_each if i < n - 1 else total_ms,
                scene_summary = sentences[i][:80] if i < len(sentences) else f"Сцен {i+1}",
            )
            segments.append(seg)

        return PromptGenResult(segments=segments, total_ms=total_ms)


# ── Factory ───────────────────────────────────────────────────────────────────

def get_prompt_generator(config: Config) -> GrokPromptGenerator:
    """Config-т үндэслэн промпт генератор буцаана."""
    if not config.XAI_API_KEY:
        logger.warning("XAI_API_KEY байхгүй — Mock промпт генератор ашиглана.")
    return GrokPromptGenerator(config)
