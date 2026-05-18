"""
utils/volume_manager.py — RunPod Volume файл менежмент

Үйлдэл:
  - Pipeline гаралтыг (видео, аудио, субтайтл, зургууд) Volume-д хуулна
  - Session-тус бүр timestamp-тай хавтас үүсгэнэ (давхцахгүй)
  - Volume дэх файлуудын жагсаалт, хэмжээ, метадата авна
  - Хуучин session-уудыг цэвэрлэх (keep_sessions тооноос хэтэрвэл)
  - Volume холбогдоогүй бол /tmp-д хадгалж анхааруулна

RunPod Volume бүтэц:
  /runpod-volume/podcast/
  ├── output/                    ← эцсийн гаралт (нийтийн)
  │   ├── 2025-01-15_12-34-56/  ← session хавтас
  │   │   ├── final_podcast.mp4
  │   │   ├── merged_audio.wav
  │   │   ├── subtitles.ass
  │   │   ├── images/
  │   │   │   ├── image_000.png
  │   │   │   └── ...
  │   │   └── session_meta.json
  │   └── latest -> 2025-01-15_12-34-56  (symlink)
  └── temp/                      ← боловсруулалтын түр файлууд

"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Config

logger = logging.getLogger("volume_manager")

# Хадгалах session-уудын тоо (хуучин автоматаар устана)
DEFAULT_KEEP_SESSIONS = 10


# ── Session метадата ──────────────────────────────────────────────────────────

@dataclass
class SessionMeta:
    """Нэг pipeline session-ийн метадата."""
    session_id:      str
    created_at:      str                 # ISO timestamp
    total_duration:  float               = 0.0
    chunk_count:     int                 = 0
    image_count:     int                 = 0
    video_path:      Optional[str]       = None
    audio_path:      Optional[str]       = None
    subtitle_path:   Optional[str]       = None
    image_paths:     list[str]           = field(default_factory=list)
    processing_time: float               = 0.0
    notes:           str                 = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── SaveResult ────────────────────────────────────────────────────────────────

@dataclass
class SaveResult:
    """Volume-д хадгалсны үр дүн."""
    success:       bool          = False
    session_dir:   Optional[str] = None
    video_path:    Optional[str] = None
    audio_path:    Optional[str] = None
    subtitle_path: Optional[str] = None
    image_paths:   list[str]     = field(default_factory=list)
    error:         Optional[str] = None
    fallback_mode: bool          = False   # Volume байхгүй → /tmp-д хадгаллаа

    def summary(self) -> str:
        if not self.success:
            return f"❌ Хадгалах алдаа: {self.error}"
        loc = "(fallback: /tmp)" if self.fallback_mode else ""
        return (
            f"✅ Session хадгалагдлаа {loc}\n"
            f"   📁 {self.session_dir}\n"
            f"   🎬 Видео: {self.video_path or '—'}\n"
            f"   🖼️  Зураг: {len(self.image_paths)}"
        )


# ── VolumeManager ─────────────────────────────────────────────────────────────

class VolumeManager:
    """
    RunPod Volume-тай холбоотой бүх үйлдлийг удирдана.

    Args:
        config: Config объект
    """

    def __init__(self, config: Config):
        self.cfg          = config
        self._volume_root = Path(config.RUNPOD_VOLUME_PATH)
        self._output_dir  = Path(config.OUTPUT_DIR)
        self._volume_ok   = self._check_volume()

    # ── Нийтийн save_outputs() ───────────────────────────────────────────

    def save_outputs(
        self,
        video_path:      Optional[str],
        subtitle_path:   Optional[str],
        audio_path:      Optional[str],
        chunks:          list,              # pipeline.ChunkResult жагсаалт
        processing_time: float = 0.0,
        total_duration:  float = 0.0,
        notes:           str   = "",
    ) -> SaveResult:
        """
        Pipeline гаралтыг Volume-д хуулж хадгална.

        Args:
            video_path:      MP4 эцсийн видео
            subtitle_path:   ASS субтайтл
            audio_path:      WAV аудио
            chunks:          ChunkResult жагсаалт (зурагтай)
            processing_time: Боловсруулалтын секунд
            total_duration:  Видеоны хугацаа (секунд)
            notes:           Нэмэлт тэмдэглэл

        Returns:
            SaveResult
        """
        session_id  = _make_session_id()
        target_root = self._output_dir if self._volume_ok else Path("/tmp/podcast_output")
        session_dir = target_root / session_id
        fallback    = not self._volume_ok

        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            images_dir = session_dir / "images"
            images_dir.mkdir(exist_ok=True)

            result = SaveResult(
                session_dir  = str(session_dir),
                fallback_mode = fallback,
            )

            # ── Файлуудыг хуулах ─────────────────────────────────────────
            if video_path and os.path.exists(video_path):
                dest = session_dir / "final_podcast.mp4"
                shutil.copy2(video_path, dest)
                result.video_path = str(dest)
                logger.info(f"Видео хуулагдлаа: {dest}")

            if audio_path and os.path.exists(audio_path):
                dest = session_dir / "merged_audio.wav"
                shutil.copy2(audio_path, dest)
                result.audio_path = str(dest)

            if subtitle_path and os.path.exists(subtitle_path):
                dest = session_dir / "subtitles.ass"
                shutil.copy2(subtitle_path, dest)
                result.subtitle_path = str(dest)

            # ── Зургууд ──────────────────────────────────────────────────
            image_paths: list[str] = []
            for chunk in chunks:
                img_src = getattr(chunk, "image_path", None)
                if img_src and os.path.exists(img_src):
                    idx  = getattr(chunk, "index", len(image_paths))
                    dest = images_dir / f"image_{idx:03d}.png"
                    shutil.copy2(img_src, dest)
                    image_paths.append(str(dest))

            result.image_paths = image_paths
            logger.info(f"{len(image_paths)} зураг хуулагдлаа.")

            # ── Промпт жагсаалт ──────────────────────────────────────────
            prompts_data = []
            for chunk in chunks:
                prompts_data.append({
                    "index":    getattr(chunk, "index", 0),
                    "prompt":   getattr(chunk, "image_prompt", ""),
                    "duration": getattr(chunk, "image_display_duration", 0),
                    "text":     getattr(chunk, "text", "")[:200],
                })
            prompts_path = session_dir / "prompts.json"
            prompts_path.write_text(
                json.dumps(prompts_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # ── Session метадата ──────────────────────────────────────────
            meta = SessionMeta(
                session_id      = session_id,
                created_at      = datetime.now().isoformat(),
                total_duration  = total_duration,
                chunk_count     = len(chunks),
                image_count     = len(image_paths),
                video_path      = result.video_path,
                audio_path      = result.audio_path,
                subtitle_path   = result.subtitle_path,
                image_paths     = image_paths,
                processing_time = processing_time,
                notes           = notes,
            )
            meta_path = session_dir / "session_meta.json"
            meta_path.write_text(
                json.dumps(meta.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # ── "latest" symlink шинэчлэх ────────────────────────────────
            self._update_latest_symlink(target_root, session_dir)

            # ── Хуучин session-уудыг цэвэрлэх ────────────────────────────
            self._cleanup_old_sessions(target_root)

            result.success = True
            logger.info(
                f"Volume хадгалалт дууслаа: {session_dir} "
                f"({'fallback' if fallback else 'volume'})"
            )
            return result

        except Exception as exc:
            logger.error(f"Volume хадгалах алдаа: {exc}")
            return SaveResult(
                success       = False,
                session_dir   = str(session_dir),
                fallback_mode = fallback,
                error         = str(exc),
            )

    # ── Session жагсаалт ──────────────────────────────────────────────────

    def list_sessions(self, limit: int = 20) -> list[SessionMeta]:
        """
        Output хавтас дахь session-уудыг хугацааны буурах дарааллаар буцаана.

        Returns:
            SessionMeta жагсаалт (хамгийн шинэ нь эхэнд)
        """
        root = self._output_dir if self._volume_ok else Path("/tmp/podcast_output")
        if not root.exists():
            return []

        sessions: list[SessionMeta] = []
        for d in sorted(root.iterdir(), reverse=True):
            if not d.is_dir() or d.name == "latest":
                continue
            meta_file = d / "session_meta.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    sessions.append(SessionMeta.from_dict(data))
                except Exception as exc:
                    logger.warning(f"Session meta унших алдаа ({d}): {exc}")
                    # Meta файлгүй session-г ID-гаар тодорхойлж нэмнэ
                    sessions.append(SessionMeta(
                        session_id = d.name,
                        created_at = datetime.fromtimestamp(
                            d.stat().st_mtime
                        ).isoformat(),
                    ))
            if len(sessions) >= limit:
                break

        return sessions

    def get_latest_session(self) -> Optional[SessionMeta]:
        """Хамгийн сүүлийн session-ийн метадатаг буцаана."""
        root   = self._output_dir if self._volume_ok else Path("/tmp/podcast_output")
        latest = root / "latest"

        if latest.is_symlink():
            target = latest.resolve()
            meta_f = target / "session_meta.json"
            if meta_f.exists():
                try:
                    return SessionMeta.from_dict(
                        json.loads(meta_f.read_text(encoding="utf-8"))
                    )
                except Exception:
                    pass

        sessions = self.list_sessions(limit=1)
        return sessions[0] if sessions else None

    # ── Хэмжээ тооцоолол ─────────────────────────────────────────────────

    def get_volume_stats(self) -> dict:
        """
        Volume-ийн нийт, ашигласан, чөлөөт зайг буцаана.

        Returns:
            {"total_gb": float, "used_gb": float, "free_gb": float,
             "session_count": int, "output_size_mb": float}
        """
        stats: dict = {
            "total_gb":      0.0,
            "used_gb":       0.0,
            "free_gb":       0.0,
            "session_count": 0,
            "output_size_mb": 0.0,
            "volume_ok":     self._volume_ok,
        }

        try:
            check_path = str(self._volume_root) if self._volume_ok else "/tmp"
            disk = shutil.disk_usage(check_path)
            stats["total_gb"] = disk.total / (1024 ** 3)
            stats["used_gb"]  = disk.used  / (1024 ** 3)
            stats["free_gb"]  = disk.free  / (1024 ** 3)
        except Exception as exc:
            logger.warning(f"Disk usage авахад алдаа: {exc}")

        try:
            root = self._output_dir if self._volume_ok else Path("/tmp/podcast_output")
            if root.exists():
                total_bytes = sum(
                    f.stat().st_size
                    for f in root.rglob("*")
                    if f.is_file()
                )
                stats["output_size_mb"] = total_bytes / (1024 ** 2)
                stats["session_count"]  = sum(
                    1 for d in root.iterdir()
                    if d.is_dir() and d.name != "latest"
                )
        except Exception as exc:
            logger.warning(f"Output хэмжээ тооцоолж чадсангүй: {exc}")

        return stats

    # ── Temp хавтас цэвэрлэх ─────────────────────────────────────────────

    def clear_temp(self) -> int:
        """
        /tmp/podcast_tmp хавтасны бүх файлыг устгана.

        Returns:
            Устгасан байтын тоо
        """
        tmp_dir = Path(self.cfg.TEMP_DIR)
        if not tmp_dir.exists():
            return 0

        total_bytes = 0
        try:
            for item in tmp_dir.iterdir():
                try:
                    if item.is_file():
                        total_bytes += item.stat().st_size
                        item.unlink()
                    elif item.is_dir():
                        total_bytes += sum(
                            f.stat().st_size for f in item.rglob("*") if f.is_file()
                        )
                        shutil.rmtree(item)
                except Exception as exc:
                    logger.warning(f"Temp файл устгах алдаа ({item}): {exc}")

            logger.info(
                f"Temp цэвэрлэлт: {total_bytes / 1024:.1f} KB чөлөөлөгдлөө"
            )
        except Exception as exc:
            logger.error(f"Temp цэвэрлэлт алдаа: {exc}")

        return total_bytes

    # ── Session устгах ────────────────────────────────────────────────────

    def delete_session(self, session_id: str) -> bool:
        """
        Тодорхой session-ийн хавтасыг устгана.

        Args:
            session_id: Session-ийн ID (хавтасны нэр)

        Returns:
            True → амжилттай, False → алдаа
        """
        root    = self._output_dir if self._volume_ok else Path("/tmp/podcast_output")
        target  = root / session_id

        if not target.exists():
            logger.warning(f"Session олдсонгүй: {session_id}")
            return False

        if not target.is_dir():
            logger.warning(f"Session хавтас биш: {session_id}")
            return False

        try:
            shutil.rmtree(target)
            logger.info(f"Session устгагдлаа: {session_id}")
            return True
        except Exception as exc:
            logger.error(f"Session устгах алдаа: {exc}")
            return False

    # ── Дотоод туслах ────────────────────────────────────────────────────

    def _check_volume(self) -> bool:
        """RunPod Volume холбогдсон эсэхийг шалгана."""
        try:
            self._volume_root.mkdir(parents=True, exist_ok=True)
            self._output_dir.mkdir(parents=True, exist_ok=True)
            # Бичих эрх шалгах
            test_file = self._volume_root / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            logger.info(f"RunPod Volume холбогдсон: {self._volume_root}")
            return True
        except Exception as exc:
            logger.warning(
                f"RunPod Volume хандах боломжгүй ({self._volume_root}): {exc}\n"
                f"Fallback: /tmp/podcast_output ашиглана."
            )
            return False

    @staticmethod
    def _update_latest_symlink(root: Path, session_dir: Path) -> None:
        """
        'latest' симлинкийг шинэ session хавтас руу чиглүүлнэ.
        Windows-д симлинк дэмжигдэхгүй бол файлаар хадгална.
        """
        latest = root / "latest"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink(missing_ok=True)
            latest.symlink_to(session_dir.name)
        except (OSError, NotImplementedError):
            # Windows fallback: latest.txt файлд session нэр бичих
            (root / "latest.txt").write_text(session_dir.name)

    def _cleanup_old_sessions(
        self,
        root: Path,
        keep: int = DEFAULT_KEEP_SESSIONS,
    ) -> None:
        """
        Session-уудыг хугацааны дарааллаар жагсааж хамгийн хуучин нь устгана.
        keep тоогоос хэтэрсэн session-уудыг устгана.
        """
        try:
            dirs = sorted(
                [
                    d for d in root.iterdir()
                    if d.is_dir() and d.name != "latest"
                ],
                key=lambda d: d.stat().st_mtime,
            )
            to_delete = dirs[: max(0, len(dirs) - keep)]
            for old in to_delete:
                try:
                    shutil.rmtree(old)
                    logger.info(f"Хуучин session устгагдлаа: {old.name}")
                except Exception as exc:
                    logger.warning(f"Session устгах алдаа ({old.name}): {exc}")
        except Exception as exc:
            logger.warning(f"Cleanup алдаа: {exc}")

    @property
    def is_available(self) -> bool:
        """Volume холбогдсон эсэхийг буцаана."""
        return self._volume_ok

    @property
    def effective_output_dir(self) -> str:
        """Үнэндээ ашиглаж буй output хавтасны замыг буцаана."""
        return str(self._output_dir if self._volume_ok else Path("/tmp/podcast_output"))


# ── Туслах функцүүд ───────────────────────────────────────────────────────────

def _make_session_id() -> str:
    """
    Timestamp-т суурилсан session ID үүсгэнэ.
    Формат: 2025-01-15_12-34-56
    """
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
