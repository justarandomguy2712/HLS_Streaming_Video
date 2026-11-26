"""Tạo bộ tệp HLS trên đĩa, kiểm tra playlist và tính SHA-256."""

import hashlib
import json
import math
import shutil
import subprocess
from collections.abc import Callable
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import NotRequired, TypedDict
from uuid import UUID

from backend.config import Settings
from backend.processing.ffmpeg import QualityError, require_disk_space, segment_to_hls, select_plan
from backend.processing.ffprobe import probe_hls_codecs, probe_input


class VideoMetadata(TypedDict):
    original_size: int
    duration: float
    segment_count: int
    processed_size: NotRequired[int]


def read_manifest(directory: Path) -> dict:
    with (directory / "metadata.json").open(encoding="utf-8") as file:
        return json.load(file)


def get_video_metadata(manifest: dict, video_id: UUID) -> VideoMetadata:
    if manifest["video_id"] != str(video_id) or manifest["playlist"] != "playlist.m3u8":
        raise ValueError("Metadata HLS không khớp UUID hoặc playlist.")
    source = manifest["source"]
    segments = manifest["segments"]
    if not segments:
        raise ValueError("Metadata HLS không có phân đoạn.")
    tail = segments[-1]
    duration = source["duration_seconds"] or (tail["start_seconds"] + tail["duration_seconds"])
    original_size = source["size_bytes"]
    if original_size <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Metadata HLS chứa kích thước hoặc thời lượng không hợp lệ.")
    return {"original_size": original_size, "duration": duration, "segment_count": len(segments)}


def file_metadata(path: Path) -> dict:
    with path.open("rb") as file:
        sha256 = hashlib.file_digest(file, "sha256").hexdigest()
    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"Tệp HLS rỗng: {path.name}")
    return {"filename": path.name, "size_bytes": size, "sha256": sha256}


def segments_from_playlist(playlist: Path) -> Iterator[tuple[int, str, Decimal]]:
    sequence = 0
    duration = None
    has_init = False
    has_end = False
    with playlist.open(encoding="utf-8") as file:
        if file.readline().strip() != "#EXTM3U":
            raise ValueError("Playlist HLS thiếu header.")
        for raw_line in file:
            line = raw_line.strip()
            if line == '#EXT-X-MAP:URI="init.mp4"':
                has_init = True
            elif line == "#EXT-X-ENDLIST":
                has_end = True
            elif line.startswith("#EXTINF:"):
                duration = Decimal(line.removeprefix("#EXTINF:").split(",", 1)[0])
            elif line and not line.startswith("#"):
                if (line != f"seg_{sequence:06d}.m4s" or duration is None
                        or not duration.is_finite() or duration <= 0 or not has_init or has_end):
                    raise ValueError("Phân đoạn hoặc thời lượng trong playlist không hợp lệ.")
                yield sequence, line, duration
                sequence += 1
                duration = None
    if not sequence or not has_end or duration is not None:
        raise ValueError("Playlist HLS VOD chưa hoàn chỉnh.")


def write_metadata(directory: Path, metadata: dict, stop: Event) -> None:
    with (directory / "metadata.json").open("w", encoding="utf-8") as file:
        # Ghi từng phần để bộ nhớ không tăng theo số segment.
        header = json.dumps(metadata, ensure_ascii=False)
        file.write(header[:-1] + ',\n"segments": [\n')
        separator = ""
        start = Decimal(0)
        for sequence, name, duration in segments_from_playlist(directory / "playlist.m3u8"):
            if stop.is_set():
                raise InterruptedError("Worker đang dừng.")
            item = {
                "sequence": sequence,
                "start_seconds": float(start),
                "duration_seconds": float(duration),
                **file_metadata(directory / name),
            }
            file.write(separator)
            json.dump(item, file, ensure_ascii=False)
            separator = ",\n"
            start += duration
        file.write("\n]}\n")


def generate_hls(settings: Settings, video_id: UUID, stop: Event, report: Callable[[str], None]) -> str:
    source = settings.uploads_dir / f"{video_id}.mp4"
    target = settings.processing_dir / str(video_id)
    staging = settings.processing_dir / f".{video_id}.tmp"
    if target.is_dir():
        # Có thể đã đổi tên thư mục xong trước khi SQLite kịp ghi ready.
        return probe_hls_codecs(settings.ffprobe_bin, target / "init.mp4")
    if staging.is_dir():
        shutil.rmtree(staging)

    size = source.stat().st_size
    require_disk_space(settings.processing_dir, settings.disk_reserve_bytes, size)
    media = probe_input(settings.ffprobe_bin, source)
    if stop.is_set():
        raise InterruptedError("Worker đang dừng.")
    plan = select_plan(settings, source, media, stop)
    report(plan.note)
    staging.mkdir()
    try:
        try:
            segment_to_hls(settings, source, staging, media, stop, plan=plan)
        except subprocess.CalledProcessError as exc:
            fallback = plan.fallback(settings)
            if fallback is None:
                if settings.processing_mode == "original":
                    raise QualityError("ORIGINAL: FFmpeg không thể hoàn tất stream copy sang fMP4.") from exc
                raise
            plan = fallback
            report(plan.note)
            shutil.rmtree(staging)
            staging.mkdir()
            segment_to_hls(settings, source, staging, media, stop, plan=plan)
        codecs = probe_hls_codecs(settings.ffprobe_bin, staging / "init.mp4")
        write_metadata(
            staging,
            {
                "video_id": str(video_id),
                "playlist": "playlist.m3u8",
                "init_segment": file_metadata(staging / "init.mp4"),
                "segment_target_seconds": float(settings.segment_seconds),
                "codecs": codecs,
                "encoder": plan.video,
                "audio_encoder": plan.audio,
                "processing_mode": settings.processing_mode,
                "processing_note": plan.note,
                "source": {
                    "size_bytes": size,
                    "duration_seconds": media.duration_seconds,
                    "bit_rate_bps": media.bit_rate_bps,
                    "video": media.video,
                    "audio": media.audio,
                },
            },
            stop,
        )
        require_disk_space(staging, settings.disk_reserve_bytes)
        staging.rename(target)
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)
    return codecs
