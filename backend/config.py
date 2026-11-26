"""Cấu hình backend từ biến môi trường và tệp .env."""

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

MAX_VIDEO_PAGE_SIZE = 200
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class R2Settings:
    endpoint_url: str
    bucket_name: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    public_base_url: str = ""
    upload_concurrency: int = 4
    upload_max_retries: int = 5
    connect_timeout: int = 5
    read_timeout: int = 30


@dataclass(frozen=True, slots=True)
class Settings:
    uploads_dir: Path
    processing_dir: Path
    database_path: Path
    ffmpeg_bin: str
    ffprobe_bin: str
    max_upload_bytes: int
    segment_seconds: Decimal
    disk_reserve_bytes: int
    r2: R2Settings | None = None
    delete_local_after_upload: bool = True
    stream_segments: bool = True
    online_library_enabled: bool = False
    worker_endpoint: str = ""
    processing_service_token: str = field(default="", repr=False)
    enable_legacy_lab_http: bool = False
    delete_source_file_after_success: bool = False
    keep_original: bool = False
    delete_after_verification: bool = True
    upload_chunk_bytes: int = 8 * 1024**2
    processing_concurrency: int = 1
    max_pending_segments: int = 32
    max_temp_bytes: int = 40 * 1024**3
    video_encoder: str = "auto"
    enable_nvenc: bool = True
    enable_hw_decode: str = "auto"
    allow_cpu_fallback: bool = True
    nvenc_preset: str = ""
    enable_gpu_monitoring: bool = True
    progress_update_interval_ms: int = 1000
    browser_admin_origin: str = ""
    allow_stream_copy: bool = True
    processing_mode: str = "auto"
    max_copy_segment_seconds: int = 0
    preserve_resolution: bool = True
    preserve_frame_rate: bool = True
    allow_downscaling: bool = False
    video_list_page_size: int = 20
    player_forward_seconds: int = 60
    player_back_seconds: int = 30


def read_integer(name: str, default: str, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
        if value >= minimum:
            return value
    except ValueError:
        pass
    raise ValueError(f"{name} phải là số nguyên từ {minimum} trở lên.")


def read_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in choices:
        raise ValueError(f"{name} cần là một trong: {', '.join(sorted(choices))}.")
    return value


def read_bool(name: str, default: str) -> bool:
    return read_choice(name, default, {"true", "false"}) == "true"


def read_url(value: str, name: str) -> str:
    url = urlsplit(value)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or (url.scheme != "https" and not (local and url.scheme == "http"))):
        raise ValueError(f"{name} cần là URL HTTPS hoặc HTTP loopback cho kiểm thử cục bộ.")
    return value.rstrip("/")


def load_r2_settings() -> R2Settings | None:
    account, key, secret, bucket, endpoint, public = (
        os.environ.get(name, "").strip()
        for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                     "R2_BUCKET_NAME", "R2_ENDPOINT_URL", "R2_PUBLIC_BASE_URL")
    )
    if not any((account, key, secret, bucket, endpoint, public)):
        return None
    if not (key and secret and bucket and (account or endpoint)):
        raise ValueError("Cấu hình R2 cần bucket, Access Key ID, Secret Access Key và account ID hoặc endpoint.")
    if not endpoint:
        if len(account) != 32 or any(char not in "0123456789abcdefABCDEF" for char in account):
            raise ValueError("R2_ACCOUNT_ID cần là 32 ký tự hex.")
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return R2Settings(
        endpoint_url=read_url(endpoint, "R2_ENDPOINT_URL"),
        bucket_name=bucket,
        access_key_id=key,
        secret_access_key=secret,
        public_base_url=read_url(public, "R2_PUBLIC_BASE_URL") if public else "",
        upload_concurrency=read_integer("R2_UPLOAD_CONCURRENCY", "4", 1),
        upload_max_retries=read_integer("R2_UPLOAD_MAX_RETRIES", "5", 0),
        connect_timeout=read_integer("R2_CONNECT_TIMEOUT_SECONDS", "5", 1),
        read_timeout=read_integer("R2_READ_TIMEOUT_SECONDS", "30", 1),
    )


def load_settings() -> Settings:
    segment_env = os.environ.get("HLS_SEGMENT_DURATION") or os.environ.get("HLS_SEGMENT_SECONDS")
    load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")

    data_dir = (PROJECT_ROOT / os.environ.get("HLS_DATA_DIR", "data")).resolve()
    try:
        segment_seconds = Decimal(
            segment_env or os.environ.get("HLS_SEGMENT_DURATION")
            or os.environ.get("HLS_SEGMENT_SECONDS") or "4"
        )
        if not segment_seconds.is_finite() or segment_seconds < Decimal("0.001"):
            raise InvalidOperation
        # FFmpeg dùng số micro giây có dấu, tính với độ chính xác hữu hạn.
        if segment_seconds >= Decimal("9223372036854"):
            raise InvalidOperation
        segment_seconds = segment_seconds.quantize(Decimal("0.000001"))
    except InvalidOperation as exc:
        raise ValueError("HLS_SEGMENT_DURATION phải là thời lượng FFmpeg hợp lệ, tối thiểu 0,001 giây.") from exc

    provider = read_choice("STORAGE_PROVIDER", "auto", {"auto", "r2", "local"})
    read_choice("LOCAL_STORAGE_MODE", "temporary", {"temporary"})
    r2 = None if provider == "local" else load_r2_settings()
    if provider == "r2" and r2 is None:
        raise ValueError("STORAGE_PROVIDER=r2 cần cấu hình kết nối R2.")
    default_reserve = read_integer("MIN_FREE_DISK_GB", "10", 0) * 1024**3
    legacy_limit = os.environ.get("HLS_MAX_UPLOAD_BYTES", str(50 * 1024**3))
    local_limit = read_integer("MAX_TEMP_STORAGE_GB", "40", 1) * 1024**3

    page_size = read_integer("VIDEO_LIST_PAGE_SIZE", "20", 1)
    if page_size > MAX_VIDEO_PAGE_SIZE:
        raise ValueError(f"VIDEO_LIST_PAGE_SIZE tối đa {MAX_VIDEO_PAGE_SIZE} mỗi trang.")

    endpoint = os.environ.get("WORKER_ENDPOINT", "").strip()
    if endpoint:
        endpoint = read_url(endpoint, "WORKER_ENDPOINT")
        if urlsplit(endpoint).path:
            raise ValueError("WORKER_ENDPOINT chỉ nhận origin của Worker.")
    service_token = os.environ.get("PROCESSING_SERVICE_TOKEN", "").strip()
    if service_token and (len(service_token) != 64 or any(c not in "0123456789abcdef" for c in service_token)):
        raise ValueError("PROCESSING_SERVICE_TOKEN cần 64 ký tự hex thường.")
    if read_bool("REMOTE_PROCESSING_ENABLED", "false"):
        raise ValueError("Companion chỉ hỗ trợ xử lý trực tiếp tại Windows.")
    mode = read_choice("VIDEO_PROCESSING_MODE", "auto", {"original", "copy", "auto", "gpu", "cpu", "high_quality"})
    admin_origin = os.environ.get("BROWSER_ADMIN_ORIGIN", "").strip().rstrip("/")
    if admin_origin:
        origin = urlsplit(read_url(admin_origin, "BROWSER_ADMIN_ORIGIN"))
        if origin.scheme != "https" or origin.path:
            raise ValueError("BROWSER_ADMIN_ORIGIN cần đúng origin HTTPS của website Admin.")

    return Settings(
        uploads_dir=data_dir / "uploads",
        processing_dir=data_dir / "processing",
        database_path=data_dir / "database" / "videos.sqlite3",
        ffmpeg_bin=os.environ.get("HLS_FFMPEG_BIN", "ffmpeg"),
        ffprobe_bin=os.environ.get("HLS_FFPROBE_BIN", "ffprobe"),
        max_upload_bytes=read_integer("MAX_UPLOAD_BYTES", legacy_limit, 1),
        segment_seconds=segment_seconds,
        disk_reserve_bytes=read_integer("HLS_DISK_RESERVE_BYTES", str(default_reserve), 0),
        r2=r2,
        delete_local_after_upload=read_bool("DELETE_LOCAL_AFTER_UPLOAD", "true"),
        stream_segments=read_bool("STREAM_SEGMENTS_TO_R2", "true"),
        online_library_enabled=read_bool("ONLINE_LIBRARY_ENABLED", "false"),
        worker_endpoint=endpoint, processing_service_token=service_token,
        enable_legacy_lab_http=read_bool("ENABLE_LEGACY_LAB_HTTP", "false"),
        delete_source_file_after_success=read_bool("DELETE_SOURCE_FILE_AFTER_SUCCESS", "false"),
        keep_original=read_bool("KEEP_ORIGINAL_VIDEO", "false"),
        delete_after_verification=read_bool("DELETE_LOCAL_AFTER_VERIFICATION", "true"),
        upload_chunk_bytes=read_integer("UPLOAD_CHUNK_SIZE_MB", "8", 1) * 1024**2,
        processing_concurrency=read_integer("VIDEO_PROCESSING_CONCURRENCY", "1", 1),
        max_pending_segments=read_integer("MAX_PENDING_SEGMENTS", "32", 1),
        max_temp_bytes=local_limit,
        video_encoder=read_choice("VIDEO_ENCODER", "auto", {"auto", "libx264", "h264_nvenc"}),
        enable_nvenc=read_bool("ENABLE_NVENC", "true"),
        enable_hw_decode=read_choice("ENABLE_HW_DECODE", "auto", {"auto", "true", "false"}),
        allow_cpu_fallback=read_bool("ALLOW_CPU_FALLBACK", "true"),
        nvenc_preset=read_choice("NVENC_PRESET", "p6" if mode == "high_quality" else "p5",
                                 {f"p{value}" for value in range(1, 8)}),
        enable_gpu_monitoring=read_bool("ENABLE_GPU_MONITORING", "true"),
        progress_update_interval_ms=read_integer("PROGRESS_UPDATE_INTERVAL_MS", "1000", 100),
        browser_admin_origin=admin_origin,
        allow_stream_copy=read_bool("ALLOW_STREAM_COPY", "true"),
        max_copy_segment_seconds=read_integer("MAX_COPY_SEGMENT_SECONDS", "0", 0),
        processing_mode=mode,
        preserve_resolution=read_bool("PRESERVE_RESOLUTION", "true"),
        preserve_frame_rate=read_bool("PRESERVE_FRAME_RATE", "true"),
        allow_downscaling=read_bool("ALLOW_DOWNSCALING", "false"),
        video_list_page_size=page_size,
        player_forward_seconds=read_integer("PLAYER_FORWARD_BUFFER_SECONDS", "60", 1),
        player_back_seconds=read_integer("PLAYER_BACK_BUFFER_SECONDS", "30", 0),
    )
