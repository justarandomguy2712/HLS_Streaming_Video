"""Phân mảnh HLS qua loopback, xác minh từng object và chốt playlist VOD."""

import json
import logging
import os
import re
import shutil
import subprocess
from collections import deque
from concurrent.futures import Executor, wait
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, perf_counter
from uuid import UUID, uuid4

from backend.processing.ffmpeg import (
    InsufficientDiskSpace, QualityError, require_disk_space, segment_to_hls, select_plan,
)
from backend.processing.ffprobe import probe_hls_codecs, probe_input
from backend.processing.hls_generator import segments_from_playlist
from backend.storage.r2_uploader import upload_verified

logger = logging.getLogger(__name__)
MEDIA_LINE = re.compile(r"seg_[0-9]{6,}\.m4s")


def stage_path(settings, identifier: UUID) -> Path:
    root = settings.processing_dir
    stage = root / f".{identifier}.stream"
    if root.resolve() != root or stage.resolve() != stage or stage.is_symlink() or stage.is_junction():
        raise OSError("Đường dẫn xử lý tạm không được chuyển hướng qua liên kết.")
    return stage


def write_receipt(path: Path, data: dict | str) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class SegmentSink:
    """Nhận PUT tuần tự. Giữ tối đa N object đang nhận hoặc chờ xác minh."""

    def __init__(self, settings, client, stage, prefix, executor: Executor):
        self.settings = settings
        self.client = client
        self.stage = stage
        self.prefix = prefix
        self.executor = executor
        self.cancel = Event()
        self.pending = deque()
        self.lock = Lock()
        self.bytes = 0
        self.peak = 0
        self.upload_seconds = 0.0
        self.error = None
        self.init = None
        self.codecs = None
        self.sequence = 0
        self.playlist_received = Event()
        self.token = f"/{uuid4()}/"
        self.limit = settings.max_temp_bytes // settings.processing_concurrency

    def check(self):
        if self.error is not None:
            raise self.error
        for _, result in self.pending.copy():
            if result.done():
                result.result()

    def reserve(self, byte_count):
        with self.lock:
            if self.bytes + byte_count > self.limit:
                raise InsufficientDiskSpace("Vượt ngân sách đĩa tạm HLS. Kiểm tra MAX_TEMP_STORAGE_GB.")
            require_disk_space(self.stage, self.settings.disk_reserve_bytes, byte_count)
            self.bytes += byte_count
            self.peak = max(self.peak, self.bytes)

    def remove(self, path, size):
        path.unlink()
        with self.lock:
            self.bytes -= size

    def upload(self, path, size):
        start = perf_counter()
        info = upload_verified(self.client, self.prefix + path.name, path, self.cancel)
        self.remove(path, size)
        with self.lock:
            self.upload_seconds += perf_counter() - start
        return {"filename": path.name, "size_bytes": info.size, "sha256": info.sha256}

    def collect(self, journal):
        filename, pending = self.pending[0]
        record = pending.result()
        self.pending.popleft()
        if filename == "init.mp4":
            self.init = record
        else:
            line = json.dumps(record) + "\n"
            self.reserve(len(line))
            journal.write(line)

    def receive(self, request, journal):
        if not request.path.startswith(self.token):
            request.send_error(404)
            return
        name = request.path.removeprefix(self.token)
        if name == "init.mp4" and self.codecs is None and not self.sequence:
            pass
        elif MEDIA_LINE.fullmatch(name) and name == f"seg_{self.sequence:06d}.m4s" and self.codecs:
            self.sequence += 1
        elif name != "playlist.m3u8" or not self.sequence or self.playlist_received.is_set():
            raise ValueError("FFmpeg gửi tên hoặc thứ tự object HLS không hợp lệ.")
        if self.playlist_received.is_set():
            raise ValueError("FFmpeg gửi thêm dữ liệu sau playlist VOD.")

        if request.headers.get("Transfer-Encoding", "").lower() != "chunked":
            raise ValueError("FFmpeg cần gửi HLS bằng HTTP chunked.")
        while len(self.pending) >= self.settings.max_pending_segments:
            self.collect(journal)
        path = self.stage / name
        size = 0
        with path.open("xb") as output:
            while True:
                self.cancel_check()
                line = request.rfile.readline(128)
                if not line.endswith(b"\r\n"):
                    raise EOFError("Thiếu header của chunk FFmpeg.")
                remaining = int(line, 16)
                if remaining < 0:
                    raise ValueError("Kích thước chunk FFmpeg không hợp lệ.")
                if not remaining:
                    if request.rfile.read(2) != b"\r\n":
                        raise EOFError("Thiếu dấu kết thúc request FFmpeg.")
                    break
                while remaining:
                    block = request.rfile.read(min(remaining, 64 * 1024))
                    if not block:
                        raise EOFError("FFmpeg chưa gửi đủ dữ liệu segment.")
                    while self.pending and self.bytes + len(block) > self.limit:
                        self.collect(journal)
                    self.reserve(len(block))
                    output.write(block)
                    remaining -= len(block)
                    size += len(block)
                if request.rfile.read(2) != b"\r\n":
                    raise EOFError("Thiếu dấu kết thúc chunk FFmpeg.")
        if not size:
            raise ValueError("FFmpeg gửi tệp HLS rỗng.")
        if name == "playlist.m3u8":
            self.playlist_received.set()
        else:
            if name == "init.mp4":
                self.codecs = probe_hls_codecs(self.settings.ffprobe_bin, path)
            self.pending.append((name, self.executor.submit(self.upload, path, size)))
        try:
            request.send_response(200)
            request.send_header("Content-Length", "0")
            request.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # FFmpeg có thể đóng socket ngay sau khi gửi hết body.
            pass

    def cancel_check(self):
        if self.cancel.is_set():
            raise InterruptedError("Đã dừng pipeline HLS.")

    def run(self, settings, source, media, plan, stop, journal):
        sink = self

        class Receiver(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(15)
                super().setup()

            def do_PUT(self):
                try:
                    sink.receive(self, journal)
                except Exception as exc:
                    sink.error = exc
                    try:
                        self.send_error(500)
                    except OSError:
                        pass

            def log_message(self, *_):
                pass

        with HTTPServer(("127.0.0.1", 0), Receiver) as server:
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
            thread.start()
            start = perf_counter()
            try:
                url = f"http://127.0.0.1:{server.server_port}{self.token}"
                segment_to_hls(settings, source, self.stage, media, stop,
                               plan=plan, sink_url=url, check=self.check)
                # FFmpeg có thể thoát trước khi server đọc và chốt request cuối.
                timeout = max(30, settings.r2.read_timeout * (settings.r2.upload_max_retries + 1) * 3)
                deadline = monotonic() + timeout
                while not self.playlist_received.wait(0.1):
                    if stop.is_set():
                        raise InterruptedError("Worker đang dừng.")
                    self.check()
                    if monotonic() >= deadline:
                        raise ValueError("FFmpeg chưa gửi playlist VOD.")
                self.check()
                ffmpeg_seconds = perf_counter() - start
            except BaseException:
                self.cancel.set()
                raise
            finally:
                server.shutdown()
                thread.join()
                if self.cancel.is_set():
                    self.wait_for_uploads()
        try:
            while self.pending:
                if stop.is_set():
                    raise InterruptedError("Worker đang dừng.")
                self.collect(journal)
        except BaseException:
            self.cancel.set()
            self.wait_for_uploads()
            raise
        return ffmpeg_seconds

    def wait_for_uploads(self):
        futures = [result for _, result in self.pending]
        for result in futures:
            result.cancel()
        # Bỏ qua kết quả ở đây vì ngoại lệ gốc đang được chuyển cho worker.
        # Executor sẽ lấy các future đã hủy và đánh dấu hoàn tất.
        wait(futures)


def write_manifest(stage, media, source_size, identifier, sink, plan, stop):
    metadata = {
        "video_id": str(identifier),
        "playlist": "playlist.m3u8",
        "init_segment": sink.init,
        "segment_target_seconds": float(sink.settings.segment_seconds),
        "codecs": sink.codecs,
        "encoder": plan.video,
        "audio_encoder": plan.audio,
        "processing_mode": sink.settings.processing_mode,
        "processing_note": plan.note,
        "source": {
            "size_bytes": source_size, "duration_seconds": media.duration_seconds,
            "bit_rate_bps": media.bit_rate_bps, "video": media.video, "audio": media.audio,
        },
    }
    duration = 0.0
    size = sink.init["size_bytes"]
    count = 0
    with (stage / "verified.jsonl").open(encoding="utf-8") as journal, (
        stage / "metadata.json"
    ).open("x", encoding="utf-8", newline="\n") as output:
        def write(text):
            sink.reserve(len(text.encode("utf-8")))
            output.write(text)

        write(json.dumps(metadata, ensure_ascii=False)[:-1] + ',\n"segments": [\n')
        for (_, filename, seconds), raw in zip(
            segments_from_playlist(stage / "playlist.m3u8"), journal, strict=True
        ):
            if stop.is_set():
                raise InterruptedError("Worker đang dừng.")
            item = json.loads(raw)
            if item["filename"] != filename:
                raise ValueError("Playlist tham chiếu segment chưa được xác minh.")
            if count:
                write(",\n")
            write(json.dumps({"sequence": count, "start_seconds": duration,
                              "duration_seconds": float(seconds), **item}))
            count += 1
            duration += float(seconds)
            size += item["size_bytes"]
        write("\n]}\n")
    return {
        "original_size": source_size, "duration": media.duration_seconds or duration,
        "segment_count": count, "processed_size": size,
        "codecs": sink.codecs, "init_key": sink.prefix + "init.mp4",
        "playlist_key": sink.prefix + "playlist.m3u8", "r2_prefix": sink.prefix,
    }


def cloud_publish(settings, storage, identifier, stop, executor, report):
    start = perf_counter()
    stage = stage_path(settings, identifier)
    receipt = stage / "receipt.json"
    playlist = stage / "playlist.m3u8"
    if receipt.is_file():
        # Metadata và mọi media đã được xác minh trước khi tạo receipt.
        result = json.loads(receipt.read_text(encoding="utf-8"))
        expected = f"videos/{identifier}/{UUID(json.loads((stage / 'attempt').read_text(encoding='utf-8')))}/"
        if result["playlist_key"] != expected + playlist.name or result["r2_prefix"] != expected:
            raise ValueError("Receipt không khớp prefix HLS.")
        upload_verified(storage.client, result["playlist_key"], playlist, stop)
        return result

    source = settings.uploads_dir / f"{identifier}.mp4"
    if source.resolve() != source or source.is_symlink():
        raise OSError("Bản sao MP4 của ứng dụng không được là liên kết.")
    source_size = source.stat().st_size
    media = probe_input(settings.ffprobe_bin, source)
    plan = select_plan(settings, source, media, stop)
    storage.client.check_bucket()

    while True:
        report(plan.note)
        marker = stage / "attempt"
        if marker.is_file():
            # Chỉ dọn attempt của chính job này và chưa có receipt để công bố.
            attempt = UUID(json.loads(marker.read_text(encoding="utf-8")))
            storage.discard_attempt(f"videos/{identifier}/{attempt}/", stop)
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        attempt = uuid4()
        write_receipt(marker, str(attempt))
        prefix = f"videos/{identifier}/{attempt}/"
        sink = SegmentSink(settings, storage.client, stage, prefix, executor)
        try:
            with (stage / "verified.jsonl").open("x", encoding="utf-8", newline="\n") as journal:
                ffmpeg_seconds = sink.run(settings, source, media, plan, stop, journal)
            break
        except subprocess.CalledProcessError as exc:
            fallback = plan.fallback(settings)
            if fallback is None:
                if settings.processing_mode == "original":
                    raise QualityError("ORIGINAL: FFmpeg không thể hoàn tất stream copy sang fMP4.") from exc
                raise
            logger.warning("FFmpeg %s thất bại, thử lại bằng libx264: %s", plan.video, exc.stderr)
            plan = fallback
        except BaseException:
            # MP4 tạm còn nguyên. Các segment dưới stage luôn có thể tái tạo.
            for path in stage.iterdir():
                if MEDIA_LINE.fullmatch(path.name) or path.name == "init.mp4":
                    path.unlink(missing_ok=True)
            raise

    result = write_manifest(stage, media, source_size, identifier, sink, plan, stop)
    metadata = stage / "metadata.json"
    result["processed_size"] += playlist.stat().st_size + metadata.stat().st_size
    upload_verified(storage.client, prefix + metadata.name, metadata, stop)
    write_receipt(receipt, result)
    upload_verified(storage.client, result["playlist_key"], playlist, stop)
    logger.info(
        "Phase07 id=%s encoder=%s segments=%d source_bytes=%d remote_bytes=%d "
        "peak_spool_bytes=%d ffmpeg_elapsed_s=%.3f upload_task_s=%.3f pipeline_s=%.3f",
        identifier, plan.video, result["segment_count"], source_size, result["processed_size"],
        sink.peak, ffmpeg_seconds, sink.upload_seconds, perf_counter() - start,
    )
    return result
