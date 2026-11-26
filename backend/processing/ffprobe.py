"""Đọc metadata của MP4 và init segment bằng FFprobe."""

import json
from contextlib import closing
from math import isfinite
from tempfile import TemporaryFile
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


class InvalidMP4(ValueError):
    """Tệp không chứa video MP4 mà quy trình có thể đọc."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_seconds: float | None
    bit_rate_bps: int | None
    video: dict
    audio: dict | None


def probe(bin_path: str, path: Path, entries: str, *, show_data: bool = False) -> dict:
    command = [
        bin_path, "-v", "error", "-protocol_whitelist", "file",
        "-f", "mov", "-enable_drefs", "0", "-i", str(path),
        "-show_entries", entries, "-of", "json",
    ]
    if show_data:
        command.append("-show_data")
    result = subprocess.run(command, capture_output=True, check=True)
    return json.loads(result.stdout)


def probe_input(bin_path: str, path: Path) -> MediaInfo:
    try:
        data = probe(
            bin_path,
            path,
            "format=duration,bit_rate,format_name:format_tags=major_brand:"
            "stream=index,codec_type,codec_name,profile,pix_fmt,width,height,channels,"
            "sample_rate,bit_rate,nal_length_size,level,r_frame_rate,avg_frame_rate,time_base,"
            "sample_aspect_ratio,display_aspect_ratio,start_time,color_range,color_space,color_transfer,"
            "color_primaries:stream_disposition=attached_pic:stream_side_data=rotation",
        )
    except subprocess.CalledProcessError as exc:
        raise InvalidMP4("FFprobe không đọc được tệp MP4.") from exc

    container = data.get("format", {})
    brand = container.get("tags", {}).get("major_brand", "").strip().lower()
    if "mp4" not in container.get("format_name", "").split(",") or not brand:
        raise InvalidMP4("Tệp đầu vào cần có định dạng MP4.")
    if brand == "qt" or brand.startswith(("3gp", "3g2")):
        raise InvalidMP4("Tệp đầu vào là QuickTime hoặc 3GP. Cần cung cấp MP4.")

    streams = data.get("streams", [])
    video = next(
        (track for track in streams if track["codec_type"] == "video"
         and not track.get("disposition", {}).get("attached_pic")),
        None,
    )
    if not video or not video.get("width") or not video.get("height"):
        raise InvalidMP4("MP4 cần có luồng video với độ phân giải hợp lệ.")
    audio = next((track for track in streams if track["codec_type"] == "audio"), None)
    raw_duration = container.get("duration", "N/A")
    duration = float(raw_duration) if raw_duration != "N/A" else math.nan
    bit_rate = container.get("bit_rate", "")
    return MediaInfo(
        duration_seconds=duration if math.isfinite(duration) and duration >= 0 else None,
        bit_rate_bps=int(bit_rate) if bit_rate.isdecimal() else None,
        video=video,
        audio=audio,
    )


def probe_hls_codecs(bin_path: str, path: Path) -> str:
    data = probe(bin_path, path, "stream=codec_name,codec_tag_string,extradata", show_data=True)
    streams = data.get("streams", [])
    video = next(track for track in streams if track["codec_name"] == "h264")
    # FFprobe hiển thị AVCDecoderConfigurationRecord dưới dạng hexdump.
    # Bỏ byte version 01 để lấy profile, compatibility flags và level.
    first_line = video["extradata"].strip().splitlines()[0]
    avcc_prefix = bytes.fromhex(first_line.split(":", 1)[1].strip()[:9])
    if len(avcc_prefix) != 4 or avcc_prefix[0] != 1:
        raise ValueError("Không đọc được cấu hình AVC từ init segment.")
    codecs = [f'{video["codec_tag_string"]}.{avcc_prefix[1:].hex()}']
    if any(track["codec_name"] == "aac" for track in streams):
        codecs.append("mp4a.40.2")  # Pipeline chỉ sao chép hoặc tạo AAC-LC.
    return ",".join(codecs)


def has_idr(file, position: int, size: int, length_size: int) -> bool:
    if position < 0 or size <= length_size:
        return False
    file.seek(position)
    end = position + size
    while file.tell() + length_size < end:
        size = int.from_bytes(file.read(length_size), "big")
        position = file.tell()
        if not size or position + size > end:
            return False
        header = file.read(1)
        if header and header[0] & 31 == 5:
            return True
        file.seek(position + size)
    return False


def copy_safe(bin_path, source, media, seconds, max_segment, stop) -> bool:
    """Kiểm tra packet và IDR theo luồng trước khi bỏ qua chuyển mã."""
    track = media.video
    length_size = int(track.get("nal_length_size") or 0)
    if (track.get("codec_name") != "h264" or track.get("pix_fmt") != "yuv420p"
            or length_size not in {1, 2, 4} or track.get("profile") not in {
                "Baseline", "Constrained Baseline", "Main", "High"
            }):
        return False
    # Một timeline có khoảng trống hoặc thời điểm bắt đầu bất thường cần encode.
    command = [
        bin_path, "-v", "error", "-protocol_whitelist", "file",
        "-f", "mov", "-enable_drefs", "0", "-i", str(source),
        "-select_streams", str(track["index"]), "-show_packets",
        "-show_entries", "packet=pts_time,dts_time,pos,size,flags", "-of", "compact=p=0",
    ]
    first = previous_dts = previous_key = None
    target = float(seconds)
    boundary = target
    last_cut = 0.0
    with source.open("rb") as file, TemporaryFile() as errors, subprocess.Popen(
        command, stdout=subprocess.PIPE, text=True, stderr=errors,
    ) as child, closing(child.stdout) as output:
        try:
            for line in output:
                if stop.is_set():
                    raise InterruptedError("Worker đang dừng.")
                if not line.strip():
                    continue
                fields = dict(field.split("=", 1) for field in line.strip().split("|") if "=" in field)
                try:
                    pts, dts = float(fields["pts_time"]), float(fields["dts_time"])
                except (KeyError, ValueError):
                    return False
                if not isfinite(pts) or not isfinite(dts) or (
                    previous_dts is not None and dts <= previous_dts
                ):
                    return False
                previous_dts = dts
                key = fields.get("flags", "").startswith("K")
                if first is None:
                    if not key or abs(pts) > 0.1:
                        return False
                    first = pts
                if key:
                    if not has_idr(file, int(fields["pos"]), int(fields["size"]), length_size):
                        return False
                    relative = pts - first
                    if previous_key is not None and relative <= previous_key:
                        return False
                    previous_key = relative
                    if relative >= boundary - 0.000001:
                        if relative - last_cut > max_segment:
                            return False
                        last_cut = relative
                        boundary += target
                if pts - first - last_cut > max_segment + 1:
                    return False
            if child.wait() != 0 or first is None or media.duration_seconds is None:
                return False
            return media.duration_seconds - first - last_cut <= max_segment
        except (KeyError, ValueError, OverflowError):
            return False
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait()
