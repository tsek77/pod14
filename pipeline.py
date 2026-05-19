"""
pipeline.py — Бүх алхамыг дараалан удирдах Pipeline Orchestrator

Алхамууд:
  1. Текстийг chunk болгох (~1600 тэмдэгт, параграф/өгүүлбэрээр)
  2. Chunk бүрт Inworld TTS-2 → аудио + word timestamps
  3. Аудионуудыг нэгтгэх + нийт timestamps бэлдэх
  4. ASS субтайтл үүсгэх
  5. Chunk бүрт Grok → зургийн промпт (утга + хугацаагаар)
  6. RunPod Serverless Flux.1 → зургууд (параллел)
  7. InfiniteTalk → ярьдаг толгой видео
  8. FFmpeg → эцсийн 1080p видео

Засагдсан алдаанууд (2025):
  - [FIX-1] ASSSubtitleGenerator → ASSBuilder (зөв класс нэр)
  - [FIX-2] subtitler.generate() → add_chunk() + save() (зөв API)
  - [FIX-3] prompter.generate_prompts() → prompter.generate() (зөв сигнатур)
  - [FIX-4] imager.generate() → imager.generate_all() (зөв метод)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional

from config import Config

logger = logging.getLogger("pipeline")


# ── Үр дүнгийн dataclass-ууд ─────────────────────────────────────────────────

@dataclass
class ChunkResult:
    """Нэг chunk-ийн боловсруулалтын бүрэн үр дүн."""
    index:           int
    text:            str
    char_count:      int
    audio_path:      Optional[str]   = None
    audio_duration:  float           = 0.0   # секунд
    audio_offset_ms: int             = 0     # нийт аудио дахь эхлэх цаг (ms)
    word_timestamps: list[dict]      = field(default_factory=list)
    # raw timestamps (offset тооцоогүй) — [{word, start_ms, end_ms}]
    image_prompt:    Optional[str]   = None
    image_path:      Optional[str]   = None
    image_display_duration: float    = 0.0
    error:           Optional[str]   = None


@dataclass
class PipelineResult:
    """Pipeline-ийн бүрэн гаралт."""
    success:           bool           = False
    error:             Optional[str]  = None
    chunks:            list[ChunkResult] = field(default_factory=list)
    final_audio_path:  Optional[str]  = None
    subtitle_path:     Optional[str]  = None
    video_path:        Optional[str]  = None
    talking_head_path: Optional[str]  = None
    total_duration:    float          = 0.0
    processing_time:   float          = 0.0


@dataclass
class PipelineProgress:
    """Градио UI-д шилжүүлэх явцын мэдээлэл."""
    step:        int    = 0
    total_steps: int    = 8
    step_name:   str    = ""
    message:     str    = ""
    percent:     float  = 0.0
    log_line:    str    = ""
    is_error:    bool   = False
    is_done:     bool   = False
    result:      Optional[PipelineResult] = None


# ── Pipeline ──────────────────────────────────────────────────────────────────

class PodcastPipeline:
    """
    Түүхэн подкаст үүсгэх бүрэн pipeline.

    Хэрэглээ (Gradio event дотор):
        pipeline = PodcastPipeline(config)
        for progress in pipeline.run(script_text):
            yield progress.log_line, progress.percent
    """

    STEPS = [
        "Текст chunk болгох",             # 1
        "Inworld TTS аудио үүсгэх",       # 2
        "Аудио нэгтгэж субтайтл бэлдэх",  # 3
        "Grok зургийн промпт үүсгэх",     # 4
        "Flux.1 зургууд үүсгэх",          # 5
        "InfiniteTalk толгой видео",       # 6
        "Видео эвлүүлэх (FFmpeg)",         # 7
        "Файлуудыг Volume-д хадгалах",    # 8
    ]

    def __init__(self, config: Config):
        self.cfg = config
        self._log_lines: list[str] = []

        # Lazy import — API key байхгүй ч import амжилттай байна
        # [FIX-1] ASSSubtitleGenerator → ASSBuilder
        from modules.text_chunker   import TextChunker
        from modules.tts_inworld    import get_tts_client
        from modules.subtitle_ass   import ASSBuilder          # ← FIX-1
        from modules.prompt_grok    import GrokPromptGenerator
        from modules.image_runpod   import RunPodImageGenerator
        from modules.talking_head   import get_talking_head_client
        from modules.video_composer import VideoComposer
        from utils.volume_manager   import VolumeManager

        self.chunker  = TextChunker(config)
        self.tts      = get_tts_client(config)
        self.subtitler = ASSBuilder(config)                    # ← FIX-1
        self.prompter = GrokPromptGenerator(config)
        self.imager   = RunPodImageGenerator(config)
        self.talker   = get_talking_head_client(config)
        self.composer = VideoComposer(config)
        self.volume   = VolumeManager(config)

    # ── Нийтийн run() ────────────────────────────────────────────────────
    def run(self, script_text: str) -> Generator[PipelineProgress, None, None]:
        """
        Бүрэн pipeline-г ажиллуулна.
        Градио UI streaming-д зориулж PipelineProgress yield хийнэ.
        """
        start_time = time.time()
        result     = PipelineResult()

        try:
            tmp = Path(self.cfg.TEMP_DIR)
            tmp.mkdir(parents=True, exist_ok=True)

            # ── Алхам 1: Chunk ────────────────────────────────────────────
            yield self._prog(1, "Текст боловсруулж chunk болгож байна…")
            chunks = self.chunker.chunk(script_text)
            result.chunks = [
                ChunkResult(index=i, text=c.text, char_count=len(c.text))
                for i, c in enumerate(chunks)
            ]
            yield self._prog(1, f"✅ {len(chunks)} chunk үүслээ "
                                f"(нийт {len(script_text):,} тэмдэгт)")

            # ── Алхам 2: TTS ──────────────────────────────────────────────
            yield self._prog(2, f"Inworld TTS-2: {len(chunks)} chunk аудио болгож байна…")

            # FIX-3 бэлтгэл: raw timestamps (offset тооцоогүй) жагсаалт хадгалах
            raw_timestamps_per_chunk: list[list[dict]] = []
            chunk_offsets_ms: list[int] = []
            time_offset_ms = 0   # ms

            for cr in result.chunks:
                yield self._prog(
                    2,
                    f"  TTS [{cr.index+1}/{len(result.chunks)}]: "
                    f"{cr.char_count} тэмдэгт…"
                )
                tts_res = self.tts.synthesize(cr.text)

                if tts_res.error:
                    cr.error = f"TTS алдаа: {tts_res.error}"
                    yield self._prog(2, f"  ⚠️ {cr.error}", is_error=True)
                    raw_timestamps_per_chunk.append([])
                    chunk_offsets_ms.append(time_offset_ms)
                    continue

                # Аудио файл хадгалах
                audio_path = tmp / f"chunk_{cr.index:03d}.wav"
                audio_path.write_bytes(tts_res.audio_bytes)
                cr.audio_path          = str(audio_path)
                cr.audio_duration      = tts_res.duration_sec
                cr.image_display_duration = tts_res.duration_sec
                cr.audio_offset_ms     = time_offset_ms
                cr.word_timestamps     = tts_res.word_timestamps  # raw (без offset)

                # Chunk-ийн raw timestamps + offset жагсаалтад хадгалах (FIX-3-д хэрэглэнэ)
                raw_timestamps_per_chunk.append(tts_res.word_timestamps)
                chunk_offsets_ms.append(time_offset_ms)

                time_offset_ms += int(tts_res.duration_sec * 1000)
                yield self._prog(
                    2,
                    f"  ✅ Chunk {cr.index+1}: {tts_res.duration_sec:.1f}с аудио"
                )

            result.total_duration = time_offset_ms / 1000

            # ── Алхам 3: Аудио нэгтгэх + субтайтл ───────────────────────
            yield self._prog(3, "Аудио файлуудыг нэгтгэж байна…")
            audio_paths = [cr.audio_path for cr in result.chunks if cr.audio_path]

            if not audio_paths:
                raise RuntimeError("Аудио файл үүсгэгдсэнгүй — TTS алдаа шалгана уу.")

            merged_audio = tmp / "merged_audio.wav"
            self._merge_wav_files(audio_paths, str(merged_audio))
            result.final_audio_path = str(merged_audio)

            # [FIX-2] subtitler.generate() → add_chunk() + save()
            yield self._prog(3, "ASS субтайтл файл үүсгэж байна…")
            subtitle_path = tmp / "subtitles.ass"
            self.subtitler.reset()

            for cr in result.chunks:
                if cr.word_timestamps:
                    self.subtitler.add_chunk(       # ← FIX-2
                        chunk_index     = cr.index,
                        word_timestamps = cr.word_timestamps,
                        time_offset_ms  = cr.audio_offset_ms,
                        duration_ms     = int(cr.audio_duration * 1000),
                    )

            self.subtitler.save(str(subtitle_path)) # ← FIX-2
            result.subtitle_path = str(subtitle_path)

            total_words = sum(len(cr.word_timestamps) for cr in result.chunks)
            yield self._prog(
                3,
                f"✅ Аудио: {result.total_duration:.1f}с | "
                f"Субтайтл: {total_words} үг | "
                f"{self.subtitler.line_count} мөр"
            )

            # ── Алхам 4: Grok промпт ─────────────────────────────────────
            yield self._prog(4, f"Grok: {len(result.chunks)} chunk-аас семантик промпт үүсгэж байна…")

            # [FIX-3] generate_prompts() → generate() зөв сигнатураар
            prompt_result = self.prompter.generate(          # ← FIX-3
                chunks_text      = [cr.text for cr in result.chunks],
                all_timestamps   = raw_timestamps_per_chunk,
                chunk_offsets_ms = chunk_offsets_ms,
            )

            if prompt_result.error:
                yield self._prog(4, f"⚠️ Grok алдаа: {prompt_result.error}", is_error=True)
                # Fallback: chunk тус бүрт хоосон промпт
                from modules.prompt_grok import PromptSegment
                prompt_result.segments = [
                    PromptSegment(
                        index         = cr.index,
                        prompt_en     = "historical documentary scene, cinematic photography",
                        start_ms      = cr.audio_offset_ms,
                        end_ms        = cr.audio_offset_ms + int(cr.audio_duration * 1000),
                        scene_summary = cr.text[:60],
                    )
                    for cr in result.chunks
                ]

            segments = prompt_result.segments
            for seg in segments:
                yield self._prog(
                    4,
                    f"  ✅ Сцен {seg.index+1} ({seg.duration_sec:.1f}с): "
                    f"{seg.scene_summary[:60]}…" if len(seg.scene_summary) > 60
                    else seg.scene_summary
                )

            # ── Алхам 5: Зургууд ─────────────────────────────────────────
            yield self._prog(5, f"Flux.1: {len(segments)} зураг үүсгэж байна…")
            img_dir = tmp / "images"

            def _img_progress(done: int, total: int, msg: str) -> None:
                logger.info(f"  Зураг [{done}/{total}] {msg}")

            # [FIX-4] imager.generate() → imager.generate_all()
            batch = self.imager.generate_all(                # ← FIX-4
                segments    = segments,
                output_dir  = str(img_dir),
                progress_cb = _img_progress,
            )

            for img_res in batch.results:
                status = "✅" if img_res.success else "❌"
                yield self._prog(
                    5,
                    f"  {status} Зураг {img_res.segment_index+1}: "
                    f"{'хадгалагдлаа' if img_res.success else img_res.error}"
                )

            yield self._prog(
                5,
                f"✅ Нийт {batch.success_count}/{len(segments)} зураг амжилттай "
                f"({batch.total_sec:.1f}с)"
            )

            # ── Алхам 6: Talking head ─────────────────────────────────────
            yield self._prog(6, "InfiniteTalk: ярьдаг толгой видео үүсгэж байна…")
            th_result = self.talker.generate(
                audio_path  = result.final_audio_path,
                output_path = str(tmp / "talking_head.mp4"),
            )
            if th_result.error:
                yield self._prog(6, f"⚠️ InfiniteTalk: {th_result.error}", is_error=True)
                result.talking_head_path = None
            else:
                result.talking_head_path = th_result.video_path
                yield self._prog(6, "✅ Ярьдаг толгой видео бэлэн")

            # ── Алхам 7: Видео эвлүүлэх ──────────────────────────────────
            yield self._prog(7, "FFmpeg: эцсийн видео эвлүүлж байна…")
            output_video = tmp / "final_podcast.mp4"

            # PromptSegment.start_ms/end_ms-г ашиглан зургийн хугацааг тооцоолох
            # Энэ нь шаардлагын гол хэсэг: зургийн харагдах хугацаа нь тухайн
            # сценд хамаарах ярианы урт дээр тулгуурлана.
            image_timeline = [
                {
                    "path":     seg.image_path,
                    "duration": seg.duration_sec,   # start_ms → end_ms хооронд
                    "index":    seg.index,
                }
                for seg in segments
                if seg.image_path
            ]

            if not image_timeline:
                raise RuntimeError(
                    "Жодна зураг амжилттай үүсгэгдсэнгүй — видео үүсгэх боломжгүй."
                )

            self.composer.compose(
                image_timeline    = image_timeline,
                audio_path        = result.final_audio_path,
                subtitle_path     = result.subtitle_path,
                talking_head_path = result.talking_head_path,
                output_path       = str(output_video),
            )
            result.video_path = str(output_video)
            yield self._prog(7, f"✅ Видео эвлүүлэгдлээ: {output_video}")

            # ── Алхам 8: Volume-д хадгалах ───────────────────────────────
            yield self._prog(8, "Файлуудыг RunPod Volume-д хадгалж байна…")

            # ChunkResult-уудад image_path дүүргэх (gallery харуулахад хэрэгтэй)
            # Segment → chunk mapping (хамгийн ойрын chunk-г хайна)
            for seg in segments:
                if seg.image_path:
                    # Segment-ийн start_ms-т хамгийн ойрхон chunk-г ол
                    best_cr = min(
                        result.chunks,
                        key=lambda c: abs(c.audio_offset_ms - seg.start_ms),
                    )
                    if best_cr.image_path is None:
                        best_cr.image_path             = seg.image_path
                        best_cr.image_prompt           = seg.prompt_en
                        best_cr.image_display_duration = seg.duration_sec

            saved = self.volume.save_outputs(
                video_path      = result.video_path,
                subtitle_path   = result.subtitle_path,
                audio_path      = result.final_audio_path,
                chunks          = result.chunks,
                processing_time = time.time() - start_time,
                total_duration  = result.total_duration,
            )
            yield self._prog(8, f"✅ Volume-д хадгалагдлаа: {saved.summary()}")

            # ── Дуусгавар ─────────────────────────────────────────────────
            result.success         = True
            result.processing_time = time.time() - start_time

            summary = (
                f"🎉 Pipeline амжилттай дууслаа!\n"
                f"   ⏱  Боловсруулалт: {result.processing_time:.0f}с\n"
                f"   🎵 Нийт аудио: {result.total_duration:.1f}с\n"
                f"   🖼  Зураг: {batch.success_count}/{len(segments)}\n"
                f"   📝 Субтайтл: {self.subtitler.line_count} мөр\n"
                f"   🎬 Видео: {result.video_path}"
            )
            yield PipelineProgress(
                step=8, total_steps=8,
                step_name="Дуусгавар",
                message=summary,
                percent=100.0,
                log_line=summary,
                is_done=True,
                result=result,
            )

        except Exception as exc:
            logger.exception("Pipeline алдаа")
            result.error           = str(exc)
            result.processing_time = time.time() - start_time
            yield PipelineProgress(
                step=0, total_steps=8,
                step_name="Алдаа",
                message=f"❌ Pipeline алдаа: {exc}",
                percent=0.0,
                log_line=f"❌ АЛДАА: {exc}",
                is_error=True,
                is_done=True,
                result=result,
            )

    # ── Туслах методууд ───────────────────────────────────────────────────
    def _prog(
        self,
        step: int,
        message: str,
        is_error: bool = False,
    ) -> PipelineProgress:
        """PipelineProgress объект үүсгэж лог-д нэмнэ."""
        step_name = self.STEPS[step - 1] if 1 <= step <= len(self.STEPS) else ""
        percent   = round((step - 1) / len(self.STEPS) * 100, 1)
        ts        = time.strftime("%H:%M:%S")
        log_line  = f"[{ts}] Алхам {step}/{len(self.STEPS)} — {step_name}: {message}"

        self._log_lines.append(log_line)
        level = logger.error if is_error else logger.info
        level(log_line)

        return PipelineProgress(
            step=step,
            total_steps=len(self.STEPS),
            step_name=step_name,
            message=message,
            percent=percent,
            log_line="\n".join(self._log_lines[-120:]),
            is_error=is_error,
            is_done=False,
        )

    @staticmethod
    def _merge_wav_files(paths: list[str], output: str) -> None:
        """WAV файлуудыг нэгтгэх. pydub → ffmpeg concat fallback."""
        try:
            from pydub import AudioSegment  # type: ignore
            combined = AudioSegment.empty()
            for p in paths:
                combined += AudioSegment.from_wav(p)
            combined.export(output, format="wav")
            return
        except ImportError:
            pass

        import subprocess
        list_file = output + ".list.txt"
        with open(list_file, "w") as f:
            for p in paths:
                f.write(f"file '{p}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", output],
            check=True,
            capture_output=True,
        )
        os.unlink(list_file)

    def get_log(self) -> str:
        """Бүрэн лог текстийг буцаана."""
        return "\n".join(self._log_lines)
