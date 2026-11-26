"""Companion Windows chạy theo lệnh, dùng pipeline Phase 07 mà không mở HTTP."""

import argparse
import ctypes
import hashlib
import logging
import msvcrt
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from getpass import getpass
from pathlib import Path
from uuid import UUID, uuid4

from backend.binary.integrity import sha256_file
from backend.config import Settings, load_settings
from backend.database import (
    claim_companion_video, finish_online_sync, get_companion_source, get_video,
    initialize_database, ready_r2_ids, recover_processing, retry_upload,
)
from backend.processing.ffmpeg import require_disk_space
from backend.services.upload_service import UploadService, flush_source
from backend.storage.r2_storage import R2Storage
from backend.storage.r2_uploader import upload_verified
from backend.worker_client import WorkerClient, WorkerError

logger = logging.getLogger(__name__)

@contextmanager
def processing_lock(database: Path):
    with database.with_suffix(".worker.lock").open("a+b") as lock:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Ứng dụng Windows đang xử lý tệp ở tiến trình khác.") from None
        yield


def media_source(settings: Settings, supplied: str) -> Path:
    path = Path(os.path.abspath(Path(supplied).expanduser()))
    if (path.suffix.lower() != ".mp4" or path.resolve(strict=True) != path
            or not path.is_file() or path.is_relative_to(settings.uploads_dir.parent)
            or path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("Chọn đúng tệp MP4 thường bên ngoài thư mục dữ liệu ứng dụng.")
    return path


def identity(info: os.stat_result) -> str:
    return f"{info.st_dev}:{info.st_ino}"


def stage_source(service: UploadService, source: Path, *, archive: bool, recycle: bool) -> str:
    settings = service.settings
    video_id = str(uuid4())
    temporary = settings.uploads_dir / f"{video_id}.part"
    digest = hashlib.sha256()
    count = 0
    try:
        with source.open("rb") as original:
            initial = os.fstat(original.fileno())
            if initial.st_size <= 0 or initial.st_size > settings.max_upload_bytes:
                raise ValueError("Kích thước MP4 nằm ngoài giới hạn upload.")
            require_disk_space(settings.uploads_dir, settings.disk_reserve_bytes, initial.st_size)
            with temporary.open("xb") as output:
                while block := original.read(settings.upload_chunk_bytes):
                    count += len(block)
                    if count > initial.st_size:
                        raise ValueError("Tệp nguồn thay đổi trong lúc đọc.")
                    digest.update(block)
                    service.write_chunk(output, block)
                flush_source(output)
            end = os.fstat(original.fileno())
            if count != initial.st_size or end.st_mtime_ns != initial.st_mtime_ns:
                raise ValueError("Tệp nguồn thay đổi trong lúc đọc.")
        service.commit_upload(temporary, video_id, source.name, count, digest.hexdigest(),
                              (str(source), archive, recycle, identity(initial)))
    finally:
        temporary.unlink(missing_ok=True)
    return video_id


def d1_state(client: WorkerClient, video_id: str) -> dict | None:
    try:
        return client.service("GET", f"/api/local/videos/{video_id}")
    except WorkerError as exc:
        if exc.status == 404:
            return None
        raise


def publish(service: UploadService, client: WorkerClient, row: dict, source: dict | None, on_cleanup=None) -> None:
    storage = service.storage
    video_id = row["id"]
    key = row["playlist_key"]
    prefix = row["r2_prefix"] or key.removesuffix("playlist.m3u8")
    online = d1_state(client, video_id)
    if online:
        if online["status"] != "READY" or online["playlist_key"] != key:
            raise ValueError("Video đã bị xóa hoặc khóa R2 của D1 không khớp.")
    else:
        manifest = storage.read_manifest(UUID(video_id), prefix)
        video = manifest["source"]["video"]
        fps = video.get("avg_frame_rate") or video.get("r_frame_rate")
        resolution = f"{video['width']}x{video['height']}" if video.get("width") and video.get("height") else None
        archived = None
        if source and source["archive_requested"]:
            local = service.settings.uploads_dir / f"{video_id}.mp4"
            if local.resolve() != local or not local.is_file():
                raise ValueError("Thiếu bản MP4 tạm đã chọn lưu thêm. Chưa dọn dữ liệu retry.")
            upload_verified(storage.client, prefix + "source.mp4", local, service.stop,
                            source_size=row["original_size"], source_hash=row["source_sha256"])
            archived = row["original_size"]
        payload = {name: row[name] for name in (
            "id", "title", "original_filename", "original_size", "processed_size",
            "duration", "segment_count", "playlist_key", "codecs", "created_at",
        )}
        payload.update(resolution=resolution, frame_rate=fps if fps and fps != "0/0" else None,
                       archived_source_size=archived)
        result = client.service("POST", "/api/local/videos", payload)
        if result.get("id") != video_id or result.get("status") != "READY":
            raise ValueError("D1 chưa xác nhận video READY.")
    finish_online_sync(service.settings.database_path, video_id, key, published=True)
    if row["cleanup_pending"]:
        if on_cleanup:
            on_cleanup()
        service.cleanup_local(video_id, row["cleanup_pending"])


def run_video(service: UploadService, client: WorkerClient, video_id: str, progress=None) -> None:
    settings, storage = service.settings, service.storage
    announce = logger.info if progress else partial(print, flush=True)
    row = get_video(settings.database_path, video_id)
    if not row or not storage.matches(row):
        raise ValueError("Không tìm thấy video của bucket đã cấu hình.")
    config = storage.config
    if row["status"] == "failed":
        retry_upload(settings.database_path, video_id, config.bucket_name, config.endpoint_url)
    if row["status"] != "ready":
        if not claim_companion_video(settings.database_path, video_id, config.bucket_name, config.endpoint_url):
            raise ValueError("Không thể nhận tác vụ này.")
        announce(f"{video_id} · FFmpeg và xác minh R2")
        with ThreadPoolExecutor(config.upload_concurrency) as uploads:
            service.process_job(video_id, storage, uploads, progress)
        row = get_video(settings.database_path, video_id)
        if row["status"] != "ready":
            raise ValueError(row["error"] or "Tác vụ chưa hoàn tất. Dùng resume để tiếp tục.")
    source = get_companion_source(settings.database_path, video_id)
    if progress:
        progress.media_ready(row)
    try:
        publish(service, client, row, source,
                (lambda: progress.update(phase="CLEANING_UP", cleanup="running")) if progress else None)
    except (WorkerError, OSError, ValueError):
        announce(f"{video_id} · chưa hoàn tất công bố. Giữ dữ liệu tạm; thử lại.")
        raise
    updated = get_video(settings.database_path, video_id)
    if progress:
        cleanup = "failed" if updated["cleanup_pending"] else ("done" if row["cleanup_pending"] else "none")
        progress.update(phase="READY", cleanup=cleanup)
    announce(f"{video_id} · D1 READY" + (" · dọn tệp đang chờ" if updated["cleanup_pending"] else ""))
    if source and source["recycle_requested"]:
        print("Phát và tua trên website, xác nhận kiểm tra video, rồi chạy recycle-source nếu muốn xóa MP4 nguồn.")


def recycle_windows(path: Path) -> None:
    if not path.drive or path.drive.startswith("\\\\") or ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3:
        raise ValueError("Recycle Bin chỉ được thử với ổ đĩa cục bộ cố định.")
    script = (
        "$ErrorActionPreference='Stop'; Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        "$env:HLS_COMPANION_RECYCLE_PATH,"
        "[Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,"
        "[Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin,"
        "[Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException)"
    )
    environment = {**os.environ, "HLS_COMPANION_RECYCLE_PATH": str(path)}
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=environment, check=True)


def recycle_source(settings: Settings, client: WorkerClient, video_id: str) -> None:
    row = get_video(settings.database_path, video_id)
    chosen = get_companion_source(settings.database_path, video_id)
    if not row or row["status"] != "ready" or not chosen or not chosen["recycle_requested"]:
        raise ValueError("Tác vụ chưa bật tùy chọn xóa nguồn hoặc chưa hoàn tất.")
    online = d1_state(client, video_id)
    if (not online or online["status"] != "READY" or online["playlist_key"] != row["playlist_key"]
            or not online["playback_checked_at"]):
        raise ValueError("Cần trạng thái D1 READY và xác nhận đã phát, tua video trên website.")
    source = media_source(settings, chosen["source_path"])
    if identity(source.stat()) != chosen["source_identity"] or sha256_file(source) != row["source_sha256"]:
        raise ValueError("Tệp nguồn đã đổi hoặc không phải chính tệp đã chọn.")
    print(f"MP4 nguồn: {source}")
    print("Bộ HLS không đảm bảo khôi phục MP4 nguồn giống từng byte.")
    if input(f"Gõ RECYCLE {video_id} để xác nhận: ").strip() != f"RECYCLE {video_id}":
        print("Đã giữ tệp nguồn.")
        return
    if media_source(settings, str(source)) != source or identity(source.stat()) != chosen["source_identity"]:
        raise ValueError("Tệp nguồn đã đổi sau khi kiểm tra.")
    if sha256_file(source) != row["source_sha256"]:
        raise ValueError("Nội dung tệp nguồn đã đổi sau khi kiểm tra.")
    confirmed = d1_state(client, video_id)
    if (not confirmed or confirmed["status"] != "READY" or confirmed["playlist_key"] != row["playlist_key"]
            or not confirmed["playback_checked_at"]):
        raise ValueError("Trạng thái trực tuyến đã thay đổi.")
    recycle_windows(source)
    print("Đã gửi tệp nguồn vào Recycle Bin.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="Tên tài khoản Admin D1.")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("source", nargs="?")
    add.add_argument("--archive-source", action="store_true", help="Lưu thêm MP4 nguồn lên R2 sau khi HLS đã xác minh.")
    add.add_argument("--request-recycle", action="store_true", help="Cho phép xác nhận xóa nguồn ở bước riêng sau phát/tua.")
    commands.add_parser("resume").add_argument("id", type=UUID)
    commands.add_parser("import-ready")
    commands.add_parser("recycle-source").add_argument("id", type=UUID)
    args = parser.parse_args()

    try:
        settings = load_settings()
        if not settings.r2 or not settings.worker_endpoint or not settings.processing_service_token:
            raise ValueError("Cần cấu hình R2, WORKER_ENDPOINT và PROCESSING_SERVICE_TOKEN.")
        client = WorkerClient(settings.worker_endpoint, settings.processing_service_token)
        username = args.username or input("Tên Admin: ").strip()
        client.require_admin(username, getpass("Mật khẩu Admin: "))
        initialize_database(settings.database_path)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        settings.processing_dir.mkdir(parents=True, exist_ok=True)
        storage = R2Storage(settings.r2)
        service = UploadService(settings, storage)
        try:
            with processing_lock(settings.database_path):
                recover_processing(settings.database_path)
                service.remove_abandoned_uploads()
                if args.command == "recycle-source":
                    recycle_source(settings, client, str(args.id))
                elif args.command == "import-ready":
                    for video_id in ready_r2_ids(settings.database_path, settings.r2.bucket_name, settings.r2.endpoint_url):
                        state = d1_state(client, video_id)
                        if not state or state["status"] == "READY":
                            run_video(service, client, video_id)
                else:
                    if args.command == "add":
                        supplied = args.source or input("Đường dẫn MP4: ").strip().strip('"')
                        source = media_source(settings, supplied)
                        if args.archive_source:
                            print(f"Lưu thêm MP4 trên R2: {source.stat().st_size:,} byte, ngoài HLS.")
                        video_id = stage_source(service, source, archive=args.archive_source,
                                                recycle=args.request_recycle or settings.delete_source_file_after_success)
                        print(f"Video ID: {video_id}", flush=True)
                    else:
                        video_id = str(args.id)
                    run_video(service, client, video_id)
        finally:
            storage.client.s3.close()
    except KeyboardInterrupt:
        parser.exit(130, "Đã dừng. Có thể tiếp tục tác vụ bằng resume.\n")
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

