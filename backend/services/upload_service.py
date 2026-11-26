"""Tiếp nhận MP4 theo luồng và điều phối worker dựa trên SQLite."""

import errno
import hashlib
import logging
import msvcrt
import os
import shutil
import sqlite3
import subprocess
from collections.abc import AsyncIterable
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, perf_counter, time
from pathlib import Path
from threading import Event, Lock, Thread
from typing import BinaryIO
from uuid import UUID, uuid4

from fastapi.concurrency import run_in_threadpool

from backend.config import Settings
from backend.database import (
    backfill_metadata, claim_upload, finish_cleanup, finish_upload, incomplete_ready_videos,
    get_video, pending_cleanup, recover_processing, register_upload, set_processing_note,
    finish_online_sync, next_online_video,
)
from backend.processing.cloud_pipeline import cloud_publish, stage_path
from backend.processing.ffmpeg import InsufficientDiskSpace, QualityError, require_disk_space
from backend.processing.ffprobe import InvalidMP4
from backend.processing.hls_generator import generate_hls, get_video_metadata, read_manifest
from backend.storage.online_library import OnlineLibrary
from backend.storage.r2_storage import R2Storage, remove_local_hls

logger = logging.getLogger(__name__)
CLEAN_HLS = 1
CLEAN_MP4 = 2


def flush_source(file: BinaryIO) -> None:
    file.flush()
    os.fsync(file.fileno())


def local_size(directory: Path, manifest: dict) -> int:
    return (manifest["init_segment"]["size_bytes"]
            + sum(item["size_bytes"] for item in manifest["segments"])
            + (directory / "playlist.m3u8").stat().st_size
            + (directory / "metadata.json").stat().st_size)


class UploadTooLarge(ValueError):
    """Số byte vượt giới hạn cấu hình."""


class InvalidUpload(ValueError):
    """Request upload rỗng hoặc chưa nhận đủ số byte khai báo."""


class UploadService:
    def __init__(self, settings: Settings, storage: R2Storage | None = None) -> None:
        self.settings = settings
        self.storage = storage
        self.stop = Event()
        self.wakeup = Event()
        self.disk_write_lock = Lock()
        self.worker = Thread(target=self.run_worker, name="hls-worker")

    def start(self) -> None:
        self.worker.start()

    def close(self) -> None:
        self.stop.set()
        self.wakeup.set()
        self.worker.join()

    async def receive(
        self, chunks: AsyncIterable[bytes], filename: str, declared_size: int | None,
        *, owner: str | None = None, title: str | None = None,
    ) -> str:
        settings = self.settings
        if declared_size is not None:
            if declared_size <= 0:
                raise InvalidUpload("Request cần chứa dữ liệu MP4.")
            if declared_size > settings.max_upload_bytes:
                raise UploadTooLarge("Tệp vượt giới hạn dung lượng upload.")

        await run_in_threadpool(
            require_disk_space, settings.uploads_dir, settings.disk_reserve_bytes, declared_size or 0
        )
        video_id = str(uuid4())
        temporary = settings.uploads_dir / f"{video_id}.part"
        total = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as output:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise UploadTooLarge("Tệp vượt giới hạn dung lượng upload.")
                    await run_in_threadpool(self.write_chunk, output, chunk)
                    digest.update(chunk)
                await run_in_threadpool(flush_source, output)
            if not total or (declared_size is not None and declared_size != total):
                raise InvalidUpload("Request rỗng hoặc chưa đủ kích thước khai báo.")
            await run_in_threadpool(self.commit_upload, temporary, video_id, filename, total, digest.hexdigest(),
                                    owner=owner, title=title)
        except OSError as exc:
            if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in {39, 112}:
                raise InsufficientDiskSpace("Ổ đĩa không đủ dung lượng upload.") from exc
            raise
        finally:
            temporary.unlink(missing_ok=True)
        self.wakeup.set()
        return video_id

    def write_chunk(self, output: BinaryIO, chunk: bytes) -> None:
        size = self.settings.upload_chunk_bytes
        buffer = memoryview(chunk)
        for offset in range(0, len(buffer), size):
            block = buffer[offset:offset + size]
            with self.disk_write_lock:
                require_disk_space(self.settings.uploads_dir, self.settings.disk_reserve_bytes, len(block))
                output.write(block)
                output.flush()

    def commit_upload(
        self, temporary: Path, video_id: str, filename: str, total: int, source_sha256: str,
        companion_source: tuple[str, bool, bool, str] | None = None,
        *, owner: str | None = None, title: str | None = None,
    ) -> None:
        source = temporary.with_suffix(".mp4")
        temporary.rename(source)
        try:
            config = self.storage.config if self.storage else None
            register_upload(
                self.settings.database_path, video_id, filename,
                config.bucket_name if config else None,
                config.endpoint_url if config else None,
                original_size=total, source_sha256=source_sha256, companion_source=companion_source,
                owner=owner, title=title,
            )
        except Exception:
            source.unlink()
            raise

    def run_worker(self) -> None:
        # Khóa hệ điều hành tự nhả khi tiến trình Windows dừng đột ngột.
        # Chỉ chủ khóa mới được khôi phục hoặc xử lý hàng đợi của SQLite.
        lock_path = self.settings.database_path.with_suffix(".worker.lock")
        while not self.stop.is_set():
            try:
                with lock_path.open("a+b") as lock:
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        if exc.errno != errno.EACCES:
                            raise
                    else:
                        self.process_queue()
            except (OSError, sqlite3.Error):
                logger.exception("Worker chưa thể truy cập hàng đợi video.")
            self.stop.wait(1)

    def process_queue(self) -> None:
        path = self.settings.database_path
        recover_processing(path)
        self.backfill_library()
        config = self.storage.config if self.storage else None
        self.remove_abandoned_uploads()
        with (
            ThreadPoolExecutor(config.upload_concurrency if config else 1,
                               thread_name_prefix="r2-put") as uploads,
            ThreadPoolExecutor(self.settings.processing_concurrency, thread_name_prefix="hls-job") as workers,
            ThreadPoolExecutor(1, thread_name_prefix="online-library") as catalog,
        ):
            active = set()
            maintenance = 0.0
            online_job = None
            online_due = 0.0
            while not self.stop.is_set():
                if self.storage and self.settings.online_library_enabled and monotonic() >= online_due:
                    if online_job is None or online_job.done():
                        if online_job is not None:
                            online_job.result()
                        online_job = catalog.submit(self.sync_online_video)
                        online_due = monotonic() + 1.1
                if config and monotonic() >= maintenance:
                    for video_id, flags in pending_cleanup(path, config.bucket_name, config.endpoint_url):
                        if self.stop.is_set():
                            break
                        self.cleanup_local(video_id, flags)
                    maintenance = monotonic() + 30
                for future in tuple(active):
                    if future.done():
                        active.remove(future)
                        future.result()
                if len(active) < self.settings.processing_concurrency:
                    job = claim_upload(
                        path, config.bucket_name if config else None, config.endpoint_url if config else None
                    )
                    if job is not None:
                        video_id, should_publish = job
                        active.add(workers.submit(
                            self.process_job, video_id, self.storage if should_publish else None, uploads
                        ))
                        continue
                self.wakeup.wait(0.5)
                self.wakeup.clear()

    def sync_online_video(self) -> None:
        storage = self.storage
        if storage is None or self.stop.is_set():
            return
        path = self.settings.database_path
        config = storage.config
        row = next_online_video(path, config.bucket_name, config.endpoint_url)
        if row is None:
            return
        try:
            OnlineLibrary(storage.client).publish(row, self.stop)
        except InterruptedError:
            return
        except Exception:
            logger.exception("Chưa đồng bộ thư viện online cho video %s.", row["id"])
            finish_online_sync(path, row["id"], row["playlist_key"], published=False)
            return
        finish_online_sync(path, row["id"], row["playlist_key"], published=True)

    def remove_abandoned_uploads(self) -> None:
        root = self.settings.uploads_dir
        if root.resolve() != root:
            return
        for file in root.iterdir():
            if file.suffix not in {".part", ".mp4"} or file.is_symlink() or not file.is_file():
                continue
            try:
                if str(UUID(file.stem)) != file.stem or time() - file.stat().st_mtime < 86400:
                    continue
                if get_video(self.settings.database_path, file.stem) is None:
                    file.unlink()
            except (OSError, ValueError):
                continue

    def backfill_library(self) -> None:
        path = self.settings.database_path
        for row in incomplete_ready_videos(path):
            if self.stop.is_set():
                return
            try:
                identifier = UUID(row["id"])
                directory = self.settings.processing_dir / str(identifier)
                if (directory / "metadata.json").is_file():
                    manifest = read_manifest(directory)
                    processed_size = local_size(directory, manifest)
                elif self.storage and self.storage.matches(row):
                    prefix = row["r2_prefix"] or f"videos/{identifier}/"
                    manifest = self.storage.read_manifest(identifier, prefix)
                    processed_size = (manifest["init_segment"]["size_bytes"]
                                      + sum(item["size_bytes"] for item in manifest["segments"])
                                      + self.storage.client.head(prefix + "playlist.m3u8").size
                                      + self.storage.client.head(prefix + "metadata.json").size)
                else:
                    continue
                metrics = get_video_metadata(manifest, identifier)
                metrics["processed_size"] = processed_size
            except Exception:
                logger.exception("Chưa thể bổ sung metadata cho video cũ %s.", row["id"])
                continue
            backfill_metadata(path, row["id"], **metrics)

    def cleanup_local(self, video_id: str, flags: int) -> None:
        try:
            identifier = UUID(video_id)
            if str(identifier) != video_id:
                raise ValueError("Mã video không hợp lệ.")
            if flags & CLEAN_HLS:
                stage = stage_path(self.settings, identifier)
                if stage.exists():
                    shutil.rmtree(stage)
                root = self.settings.processing_dir
                if root.resolve() != root:
                    raise OSError("Thư mục xử lý không được là liên kết.")
                remove_local_hls(root / video_id)
            if flags & CLEAN_MP4:
                source = self.settings.uploads_dir / f"{video_id}.mp4"
                if source.resolve() != source or source.is_symlink():
                    raise OSError("Tệp upload không được là liên kết.")
                source.unlink(missing_ok=True)
            finish_cleanup(self.settings.database_path, video_id)
        except (OSError, ValueError):
            logger.exception("HLS đã xác minh trên R2, dọn tệp cục bộ còn chờ cho %s.", video_id)

    def process_job(self, video_id: str, storage: R2Storage | None, executor, progress=None) -> None:
        path = self.settings.database_path
        error = None

        def report(note: str) -> None:
            set_processing_note(path, video_id, note)

        try:
            identifier = UUID(video_id)
            if str(identifier) != video_id:
                raise ValueError("Mã video phải ở dạng UUID chuẩn.")
            directory = self.settings.processing_dir / video_id
            if storage and self.settings.stream_segments and not directory.exists():
                result = cloud_publish(self.settings, storage, identifier, self.stop, executor, report, progress)
                self.commit_publication(video_id, storage, result, streaming=True)
                return
            processing_started = perf_counter()
            codecs = generate_hls(self.settings, identifier, self.stop, report, progress)
            manifest = read_manifest(directory)
            metrics = get_video_metadata(manifest, identifier)
            metrics["processed_size"] = local_size(directory, manifest)
            processing_seconds = perf_counter() - processing_started
        except InterruptedError:
            # Khi khởi động lại, chủ khóa đưa job processing về uploaded.
            return
        except (InvalidMP4, InsufficientDiskSpace, QualityError) as exc:
            error = str(exc)
        except FileNotFoundError:
            logger.exception("Thiếu MP4 hoặc trình thực thi cho video %s.", video_id)
            error = "Thiếu tệp nguồn hoặc FFmpeg/FFprobe. Kiểm tra log backend."
        except subprocess.CalledProcessError as exc:
            logger.error("Lỗi công cụ media với video %s: %s", video_id, exc.stderr)
            error = "FFmpeg hoặc FFprobe xử lý thất bại. Kiểm tra log backend."
        except Exception:
            logger.exception("Không thể hoàn thành video %s.", video_id)
            error = "Không thể tạo hoặc xác minh bộ HLS. Kiểm tra log backend rồi dùng retry nếu phù hợp."

        if error:
            finish_upload(path, video_id, error=error)
            return
        if storage is None:
            finish_upload(
                path, video_id, playlist_key=f"{video_id}/playlist.m3u8", codecs=codecs, **metrics
            )
            return

        try:
            publishing_started = perf_counter()
            if progress:
                progress.update(phase="UPLOADING", ffmpeg_done=True, duration=metrics["duration"],
                                segments_created=metrics["segment_count"], total_bytes=metrics["processed_size"],
                                encoder=manifest.get("encoder"))
            key = storage.publish(identifier, directory, self.stop, manifest,
                                  progress=progress.upload if progress else None)
        except InterruptedError:
            return
        except Exception:
            logger.exception("Không thể xuất bản HLS lên R2 cho video %s.", video_id)
            finish_upload(path, video_id, error="Không thể lưu hoặc xác minh bộ HLS trên R2. Kiểm tra log backend.")
            return

        logger.info(
            "Phase07 batch id=%s peak_hls_bytes_at_publish=%d local_processing_s=%.3f upload_s=%.3f",
            video_id, metrics["processed_size"], processing_seconds, perf_counter() - publishing_started,
        )
        prefix = key.removesuffix("playlist.m3u8")
        self.commit_publication(video_id, storage, {
            **metrics, "playlist_key": key, "codecs": codecs, "r2_prefix": prefix,
            "init_key": prefix + "init.mp4",
        }, streaming=False)

    def commit_publication(self, video_id, storage, result, *, streaming):
        config = storage.config
        delete = self.settings.delete_after_verification and (
            streaming or self.settings.delete_local_after_upload
        )
        flags = CLEAN_HLS if delete else 0
        if self.settings.delete_after_verification and not self.settings.keep_original:
            flags |= CLEAN_MP4
        committed = finish_upload(
            self.settings.database_path, video_id,
            storage_bucket=config.bucket_name, storage_endpoint=config.endpoint_url,
            cleanup_pending=flags, **result,
        )
        if flags and committed and not self.settings.worker_endpoint:
            self.cleanup_local(video_id, flags)
