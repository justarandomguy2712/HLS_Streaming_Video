"""Chạy FFmpeg trên đĩa và giám sát tiến trình phân mảnh."""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
import subprocess
from pathlib import Path
from tempfile import TemporaryFile
from threading import Event, Thread

from backend.config import Settings
from backend.processing.ffprobe import MediaInfo, copy_safe


class InsufficientDiskSpace(OSError):
    """Dung lượng đĩa không đáp ứng lượng byte cần ghi và mức dự trữ."""


def require_disk_space(directory: Path, reserve_bytes: int, extra_bytes: int = 0) -> None:
    if shutil.disk_usage(directory).free < reserve_bytes + extra_bytes:
        raise InsufficientDiskSpace("Ổ đĩa không đủ dung lượng cho tác vụ video.")


@lru_cache(maxsize=8)
def nvenc_available(binary: str) -> bool:
    try:
        test = subprocess.run([
            binary, "-nostdin", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=black:s=1280x720:r=30", "-frames:v", "8",
            "-c:v", "h264_nvenc", "-preset", "p5", "-f", "null", "-",
        ], capture_output=True, timeout=15)
        return test.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class QualityError(ValueError):
    """Không thể giữ các thuộc tính media theo chính sách đã chọn."""


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    video: str
    audio: str | None
    reasons: tuple[str, ...]
    high_quality: bool
    hardware_decode: bool = False

    @property
    def note(self) -> str:
        if not self.reasons:
            return "Stream copy video và audio hiện có."
        return " ".join(self.reasons)

    def fallback(self, settings: Settings) -> "ProcessingPlan | None":
        if self.video == "libx264" or settings.processing_mode in {"original", "copy"}:
            return None
        if self.hardware_decode:
            reason = "Thử lại NVENC với giải mã CPU."
            return replace(self, hardware_decode=False, reasons=(*self.reasons, reason))
        prefer_nvenc = (settings.processing_mode != "cpu" and settings.enable_nvenc
                        and settings.video_encoder != "libx264")
        if self.video == "copy" and prefer_nvenc and nvenc_available(settings.ffmpeg_bin):
            return replace(self, video="h264_nvenc",
                           reasons=(*self.reasons, "FFmpeg không hoàn tất stream copy. Thử NVIDIA NVENC."))
        if not settings.allow_cpu_fallback and (
            self.video == "h264_nvenc" or prefer_nvenc or settings.processing_mode == "gpu"
        ):
            return None
        reason = ("NVENC không hoàn tất tác vụ. Thử lại bằng libx264." if self.video == "h264_nvenc"
                  else "FFmpeg không hoàn tất stream copy video sang fMP4. Chuyển mã bằng libx264.")
        return replace(self, video="libx264", reasons=(*self.reasons, reason))


def select_plan(settings: Settings, source: Path, media: MediaInfo, stop: Event) -> ProcessingPlan:
    mode = settings.processing_mode
    high_quality = mode == "high_quality"
    audio = media.audio
    reasons: list[str] = []
    video = "copy"
    if high_quality or mode == "cpu":
        reasons.append(f"{mode.upper()} yêu cầu chuyển mã video.")
    elif not settings.allow_stream_copy:
        reasons.append("ALLOW_STREAM_COPY đang tắt.")
    elif media.video.get("codec_name") != "h264":
        reasons.append(f'Codec video {media.video.get("codec_name") or "không rõ"} cần chuyển mã sang H.264.')
    elif not copy_safe(
        settings.ffprobe_bin, source, media, settings.segment_seconds,
        settings.max_copy_segment_seconds or max(30, 2 * float(settings.segment_seconds)), stop,
    ):
        reasons.append("Profile, pixel format, timestamp hoặc IDR của video chưa phù hợp với fMP4 MSE và độ dài segment đã cấu hình.")
    if reasons:
        video = "libx264"

    audio_codec = None
    if audio is not None:
        if high_quality or not settings.allow_stream_copy:
            audio_codec = "aac"
        elif audio.get("codec_name") == "aac" and audio.get("profile") == "LC":
            audio_codec = "copy"
        else:
            audio_codec = "aac"
            reasons.append(f'Audio {audio.get("codec_name") or "không rõ"} cần chuyển mã sang AAC-LC.')

    if mode in {"original", "copy"} and reasons:
        raise QualityError(f"{mode.upper()} chỉ cho phép stream copy. " + " ".join(reasons))
    use_nvenc = mode != "cpu" and settings.enable_nvenc and settings.video_encoder != "libx264"
    if video != "copy" and use_nvenc:
        if nvenc_available(settings.ffmpeg_bin):
            video = "h264_nvenc"
        elif not settings.allow_cpu_fallback:
            raise QualityError("NVIDIA NVENC không khả dụng. Kiểm tra FFmpeg và driver.")
        else:
            reasons.append("NVENC không khả dụng. Dùng libx264.")
    elif video != "copy" and mode == "gpu":
        if not settings.allow_cpu_fallback:
            raise QualityError("NVENC đang tắt và GPU không cho phép CPU fallback.")
        reasons.append("NVENC đang tắt. Dùng libx264.")
    hardware_decode = (
        video == "h264_nvenc" and settings.enable_hw_decode != "false"
        and media.video.get("codec_name") in {"h264", "hevc", "av1", "vp9", "mpeg2video"}
        and media.video.get("pix_fmt") in {"yuv420p", "yuvj420p", "nv12"}
    )
    return ProcessingPlan(video, audio_codec, tuple(reasons), high_quality, hardware_decode)


def read_progress(pipe, notify: Callable[[float, float | None], None]) -> None:
    fields = {}
    for line in pipe:
        name, _, value = line.strip().partition("=")
        fields[name] = value
        if name == "progress":
            try:
                seconds = max(0, int(fields.get("out_time_us", ""))) / 1_000_000
            except ValueError:
                fields.clear()
                continue
            try:
                speed = float(fields.get("speed", "").removesuffix("x"))
            except ValueError:
                speed = None
            notify(seconds, speed)
            fields.clear()


def dimension_filter(settings: Settings, video: dict) -> list[str]:
    width, height = video["width"], video["height"]
    if not (width % 2 or height % 2):
        return []
    if settings.preserve_resolution:
        raise QualityError(
            f"Không thể mã hóa {width}×{height} sang H.264 yuv420p với kích thước lẻ "
            "khi PRESERVE_RESOLUTION=true. Cần kích thước chẵn hoặc cho phép hiệu chỉnh kích thước."
        )
    if settings.allow_downscaling and min(width, height) > 1:
        return ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    # Padding tối đa 1 px mỗi chiều. Giữ display aspect ratio bằng SAR.
    sar = video.get("sample_aspect_ratio", "1:1")
    try:
        original_sar = Fraction(sar.replace(":", "/"))
        if original_sar <= 0:
            original_sar = Fraction(1)
    except (ValueError, ZeroDivisionError):
        original_sar = Fraction(1)
    padded_sar = original_sar * Fraction(width * (height + height % 2), height * (width + width % 2))
    return ["-vf", f"pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar={padded_sar.numerator}/{padded_sar.denominator}:max=65535"]


def segment_to_hls(
    settings: Settings, source: Path, output_dir: Path, media: MediaInfo, stop: Event,
    *, plan: ProcessingPlan, sink_url: str | None = None, check=None,
    progress: Callable[[float, float | None], None] | None = None,
) -> None:
    seconds = format(settings.segment_seconds, "f")
    command = [
        settings.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
        *(["-progress", "pipe:1", "-stats_period", str(settings.progress_update_interval_ms / 1000)]
          if progress else []),
        "-protocol_whitelist", "file", "-f", "mov", "-enable_drefs", "0",
        *(["-hwaccel", "cuda"] if plan.hardware_decode else []),
        "-noautorotate", "-i", str(source), "-map", f'0:{media.video["index"]}',
        "-c:v", plan.video,
    ]
    if plan.video != "copy":
        command.extend([
            "-filter_threads", "2", "-threads:v", "4", "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{seconds})",
            *dimension_filter(settings, media.video),
        ])
        if settings.preserve_frame_rate:
            command.extend(["-fps_mode:v", "passthrough", "-enc_time_base:v", "demux"])
        quality = "17" if plan.high_quality else "19"
        if plan.video == "h264_nvenc":
            command.extend(["-preset", settings.nvenc_preset or ("p6" if plan.high_quality else "p5"),
                            "-rc", "vbr", "-cq", quality, "-b:v", "0",
                            "-forced-idr", "1", "-no-scenecut", "1", "-flags", "+cgop"])
        else:
            command.extend(["-preset", "slow" if plan.high_quality else "medium", "-crf", quality,
                            "-x264-params", "open-gop=0", "-sc_threshold", "0"])
    if media.audio is not None:
        command.extend(["-map", f'0:{media.audio["index"]}', "-c:a", plan.audio])
        if plan.audio == "aac":
            command.extend(["-profile:a", "aac_low", "-q:a", "2" if plan.high_quality else "1.8"])

    command.extend([
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-f", "hls", "-hls_playlist_type", "vod", "-hls_list_size", "0",
        "-hls_time", seconds, "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", (sink_url or "") + "seg_%06d.m4s",
    ])
    if sink_url:
        command.extend(["-hls_flags", "independent_segments", "-method", "PUT", sink_url + "playlist.m3u8"])
    else:
        command.extend(["-hls_flags", "independent_segments+temp_file", "playlist.m3u8"])
    environment = os.environ.copy()
    if sink_url:
        environment["no_proxy"] = "127.0.0.1"
    with TemporaryFile(dir=output_dir) as log, subprocess.Popen(
        command, cwd=output_dir, stdout=subprocess.PIPE if progress else subprocess.DEVNULL,
        stderr=log, env=environment, text=True,
    ) as child:
        reader = None
        if progress:
            reader = Thread(target=read_progress, args=(child.stdout, progress), name="ffmpeg-progress")
            reader.start()
        try:
            while child.poll() is None:
                if stop.wait(0.1 if check else 0.5):
                    raise InterruptedError("Worker đang dừng.")
                if check:
                    check()
                require_disk_space(output_dir, settings.disk_reserve_bytes)
            if check:
                check()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait()
            if reader:
                reader.join()

        if child.returncode:
            log.seek(max(0, log.seek(0, 2) - 8192))
            details = log.read().decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(child.returncode, command, stderr=details)
