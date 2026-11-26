"""Kiểm thử Phase 07 trên Windows với nguồn tổng hợp và S3 mô phỏng hoặc R2."""

import argparse
import ctypes
import json
import logging
import os
import shutil
import socket
import sys
import struct
import subprocess
from contextlib import closing
from pathlib import Path
from tempfile import mkdtemp
from threading import Event
from time import monotonic, process_time, sleep
from unittest.mock import patch
from uuid import UUID, uuid4

import boto3
from botocore.exceptions import EndpointConnectionError
from moto.server import ThreadedMotoServer

from backend.config import R2Settings, load_settings
from backend.database import get_video
from backend.storage.r2_downloader import transfer_verified
from backend.storage.r2_storage import R2Storage
from tests.r2_smoke import app_server
from tests.upload_smoke import request, upload


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("page_faults", ctypes.c_ulong),
                ("peak_working_set", ctypes.c_size_t), ("other", ctypes.c_size_t * 7)]


def peak_process_bytes():
    counters = ProcessMemoryCounters()
    counters.size = ctypes.sizeof(counters)
    # GetCurrentProcess trả pseudo-handle -1. Truyền HANDLE đúng độ rộng Win64.
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(-1), ctypes.byref(counters), counters.size
    ):
        raise ctypes.WinError()
    return counters.peak_working_set


def sample(ffmpeg, path, duration, gop, *, large=False, audio=False):
    rate = 10 if duration < 600 else 2
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
        f"testsrc2=s=96x64:r={rate}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=330", "-c:a", "aac"]
    command += [
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "4",
        "-g", str(rate * gop), "-keyint_min", str(rate * gop), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ]
    subprocess.run(command, check=True)
    if large:
        # Box MP4 'free' hợp lệ để kiểm luồng HTTP nhiều GiB mà không tạo tải codec giả.
        box_bytes = 2304 * 1024**2
        block = bytes(8 * 1024**2)
        with path.open("ab") as file:
            file.write(struct.pack(">I4s", box_bytes, b"free"))
            remaining = box_bytes - 8
            while remaining:
                written = file.write(memoryview(block)[:remaining])
                remaining -= written


def completed(base, endpoint, timeout=1800):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        status = request(base, "GET", endpoint)[2]
        if status["status"] in {"ready", "failed"}:
            return status
        sleep(0.25)
    raise TimeoutError(f"Không thấy trạng thái cuối trong {timeout} giây.")


def assert_clean(data, identifier):
    targets = [data / "uploads" / f"{identifier}.mp4",
               data / "processing" / f".{identifier}.stream",
               data / "processing" / identifier]
    deadline = monotonic() + 15
    while any(path.exists() for path in targets) and monotonic() < deadline:
        sleep(0.05)
    assert not any(path.exists() for path in targets), targets


def run_batch(args, config, storage, root, cases, seconds, *, streaming=True, cpu_only=False):
    data = root / ("streaming" if streaming else "batch")
    records = []
    options = dict(
        STREAM_SEGMENTS_TO_R2=str(streaming).lower(), KEEP_ORIGINAL_VIDEO="false",
        DELETE_LOCAL_AFTER_VERIFICATION="true", R2_UPLOAD_CONCURRENCY="4",
        MAX_PENDING_SEGMENTS="8", HLS_SEGMENT_DURATION=str(seconds),
        HLS_DISK_RESERVE_BYTES="0", MAX_UPLOAD_BYTES=str(4 * 1024**3),
    )
    if cpu_only:
        options.update(ALLOW_STREAM_COPY="false", VIDEO_ENCODER="libx264")
    with app_server(data, args.ffmpeg, args.ffprobe, config, **options) as base:
        for label, duration, large in cases:
            source = root / f"{label}.mp4"
            if not source.exists():
                sample(args.ffmpeg, source, duration, seconds, large=large, audio=(label == "TC01"))
            original_size = source.stat().st_size
            start = monotonic()
            cpu_start = process_time()
            location = upload(base, source, source.name, chunked=True)
            accepted = monotonic()
            status = completed(base, location)
            ready = monotonic()
            cpu_seconds = process_time() - cpu_start
            python_peak = peak_process_bytes()
            assert status["status"] == "ready", status
            identifier = status["id"]
            detail = request(base, "GET", f"/api/videos/{identifier}")[2]
            assert detail["original_size"] == original_size
            assert abs(detail["duration"] - duration) <= 0.2
            assert detail["segment_count"] > 0 and detail["processed_size"] > 0
            prefix = detail["r2_prefix"]
            manifest = storage.read_manifest(UUID(identifier), prefix)
            assert len(manifest["segments"]) == detail["segment_count"]
            assert detail["init_key"] == prefix + "init.mp4"
            for name in ("init.mp4", manifest["segments"][-1]["filename"], "playlist.m3u8", "metadata.json"):
                key = prefix + name
                info = storage.client.head(key)
                assert info, key
                transfer_verified(storage.client, key, info, stop=Event())
            assert_clean(data, identifier)
            assert source.stat().st_size == original_size
            record = {
                "case": label, "mode": "streaming" if streaming else "batch", "id": identifier,
                "source_bytes": original_size, "duration": detail["duration"],
                "segments": detail["segment_count"], "remote_bytes": detail["processed_size"],
                "mean_segment_bytes": round(sum(part["size_bytes"] for part in manifest["segments"])
                                            / detail["segment_count"], 2),
                "encoder": manifest.get("encoder", "libx264"), "upload_ingress_seconds": round(accepted - start, 3),
                "processing_seconds": round(ready - accepted, 3), "pipeline_seconds": round(ready - start, 3),
                "playback_url": status["playback_url"], "remaining_app_media_bytes": 0,
                "python_process_cpu_s": round(cpu_seconds, 3), "python_peak_working_set": python_peak,
            }
            print(json.dumps(record), flush=True)
            with (root / "result.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(record) + "\n")
            records.append(record)
    with app_server(data, args.ffmpeg, args.ffprobe, config, **options) as base:
        for item in records:
            identifier = item["id"]
            assert_clean(data, identifier)
            code, _, playback = request(base, "GET", f"/api/videos/{identifier}/playback")
            assert code == 200 and playback["playback_url"] == item["playback_url"]
            code, _, detail = request(base, "GET", f"/api/videos/{identifier}")
            assert code == 200 and detail["playable"]
    return records


def run_failure_cases(args, config, storage, root):
    source = root / "TC01.mp4"
    data = root / "failures"
    options = dict(STREAM_SEGMENTS_TO_R2="true", KEEP_ORIGINAL_VIDEO="false",
                   R2_UPLOAD_MAX_RETRIES="0", MAX_PENDING_SEGMENTS="1")
    original = storage.client.__class__.open

    def disconnected(client, key):
        if key.endswith("seg_000001.m4s"):
            raise EndpointConnectionError(endpoint_url=config.endpoint_url)
        return original(client, key)

    with app_server(data, args.ffmpeg, args.ffprobe, config, **options) as base:
        with patch.object(storage.client.__class__, "open", disconnected):
            status = completed(base, upload(base, source, "R2-disconnected.mp4"))
        assert status["status"] == "failed" and not status["playback_url"]
        video_id = status["id"]
        assert (data / "uploads" / f"{video_id}.mp4").exists()
        listing = storage.client.s3.list_objects_v2(Bucket=config.bucket_name, Prefix=f"videos/{video_id}/")
        assert not any(obj["Key"].endswith("playlist.m3u8") for obj in listing.get("Contents", []))
        from backend.database import retry_upload
        assert retry_upload(data / "database" / "videos.sqlite3", video_id, config.bucket_name, config.endpoint_url)
        assert completed(base, f"/api/uploads/{video_id}")["status"] == "ready"
        assert_clean(data, video_id)
        objects = storage.client.s3.list_objects_v2(Bucket=config.bucket_name, Prefix=f"videos/{video_id}/")
        assert len({item["Key"].split("/")[2] for item in objects["Contents"]}) == 1
    with app_server(root / "disk", args.ffmpeg, args.ffprobe, config, **{
        **options, "HLS_DISK_RESERVE_BYTES": str(shutil.disk_usage(root).total),
    }) as base, source.open("rb") as handle:
        code, _, _ = request(base, "POST", "/api/uploads", handle, {
            "Content-Type": "video/mp4", "X-Filename": source.name, "Content-Length": str(source.stat().st_size),
        })
        assert code == 507
    with app_server(root / "encoder", args.ffprobe, args.ffprobe, config, **{
        **options, "ALLOW_STREAM_COPY": "false", "ENABLE_NVENC": "false",
    }) as base:
        status = completed(base, upload(base, source, "ffmpeg-failed.mp4"))
        assert status["status"] == "failed" and not status["playback_url"]
    print("PASS R2 interrupted and retried, FFmpeg non-zero, disk reserve HTTP 507", flush=True)


def run_crash_case(args, config, storage, root, after_playlist):
    data = root / ("crash-receipt" if after_playlist else "crash-segment")
    env = {
        **os.environ, "HLS_DATA_DIR": str(data), "HLS_SEGMENT_DURATION": "4",
        "HLS_FFMPEG_BIN": args.ffmpeg, "HLS_FFPROBE_BIN": args.ffprobe,
        "R2_ACCOUNT_ID": "", "R2_ENDPOINT_URL": config.endpoint_url,
        "R2_BUCKET_NAME": config.bucket_name, "R2_ACCESS_KEY_ID": config.access_key_id,
        "R2_SECRET_ACCESS_KEY": config.secret_access_key, "R2_PUBLIC_BASE_URL": "",
        "STORAGE_PROVIDER": "r2", "HLS_DISK_RESERVE_BYTES": "0",
        "STREAM_SEGMENTS_TO_R2": "true", "KEEP_ORIGINAL_VIDEO": "false",
        "DELETE_LOCAL_AFTER_VERIFICATION": "true",
    }
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    patch_code = """
from backend.services.upload_service import UploadService
def crash(*args, **kwargs):
    os._exit(71)
UploadService.commit_publication = crash
""" if after_playlist else """
from backend.processing import cloud_pipeline
verify = cloud_pipeline.upload_verified
def crash(client, key, *args, **kwargs):
    result = verify(client, key, *args, **kwargs)
    if key.endswith("seg_000001.m4s"):
        os._exit(72)
    return result
cloud_pipeline.upload_verified = crash
"""
    command = ("import os, uvicorn\n" + patch_code
               + f"\nuvicorn.run('backend.main:app', host='127.0.0.1', port={port}, log_level='critical')")
    base = f"http://127.0.0.1:{port}"
    child = subprocess.Popen([sys.executable, "-c", command], env=env)
    try:
        deadline = monotonic() + 10
        while True:
            try:
                if request(base, "GET", "/health")[0] == 200:
                    break
            except ConnectionRefusedError:
                pass
            if child.poll() is not None or monotonic() > deadline:
                raise RuntimeError("Backend kiểm thử crash chưa sẵn sàng.")
            sleep(0.1)
        endpoint = upload(base, root / "TC01.mp4", "crash-test.mp4")
        assert child.wait(timeout=40) == (71 if after_playlist else 72)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait()

    identifier = endpoint.rsplit("/", 1)[1]
    row = get_video(data / "database" / "videos.sqlite3", identifier)
    assert row["status"] == "processing" and row["playlist_key"] is None
    keys = [item["Key"] for item in storage.client.s3.list_objects_v2(
        Bucket=config.bucket_name, Prefix=f"videos/{identifier}/"
    ).get("Contents", [])]
    assert any(key.endswith("playlist.m3u8") for key in keys) == after_playlist
    with app_server(
        data, args.ffmpeg, args.ffprobe, config, STREAM_SEGMENTS_TO_R2="true",
        KEEP_ORIGINAL_VIDEO="false", DELETE_LOCAL_AFTER_VERIFICATION="true",
    ) as base:
        status = completed(base, endpoint)
        assert status["status"] == "ready", status
        assert (status["playlist_key"] in keys) == after_playlist
        assert_clean(data, identifier)
    print(f"PASS abrupt process exit and restart recovery (playlist already verified={after_playlist})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--real", action="store_true", help="Xuất bản nội dung tổng hợp lên bucket đang cấu hình.")
    parser.add_argument("--ladder", action="store_true", help="Kiểm thêm 10 phút đến hơn 3 giờ và 2,25 GiB.")
    parser.add_argument("--compare", action="store_true", help="Chạy baseline với TC02.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(dir=args.scratch_dir, prefix="phase07-")).resolve()
    print(f"Scratch: {root}", flush=True)
    emulator = None
    if args.real:
        config = load_settings().r2
        if config is None:
            parser.error("Thiếu cấu hình R2.")
    else:
        emulator = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
        emulator.start()
        host, port = emulator.get_host_and_port()
        config = R2Settings(f"http://{host}:{port}", "phase07-test", uuid4().hex, uuid4().hex)
        with closing(boto3.client("s3", endpoint_url=config.endpoint_url, region_name="us-east-1",
                          aws_access_key_id=config.access_key_id,
                          aws_secret_access_key=config.secret_access_key)) as client:
            client.create_bucket(Bucket=config.bucket_name)

    storage = R2Storage(config)
    try:
        run_batch(args, config, storage, root, [("TC01", 10, False), ("TC02", 60, False)], 4)
        if args.compare:
            run_batch(args, config, storage, root, [("TC02", 60, False)], 4,
                      streaming=False, cpu_only=True)
            run_batch(args, config, storage, root, [("TC02", 60, False)], 4, cpu_only=True)
        if args.ladder:
            run_batch(args, config, storage, root, [
                ("TC03", 600, False), ("TC04", 1800, False), ("TC05", 3600, False),
                ("TC06", 7200, False), ("TC07", 10800, False), ("TC08", 11100, False),
                ("TC09", 10, True),
            ], 60)
        if not args.real:
            run_failure_cases(args, config, storage, root)
            for after_playlist in (False, True):
                run_crash_case(args, config, storage, root, after_playlist)
    finally:
        storage.client.s3.close()
        if emulator:
            emulator.stop()


if __name__ == "__main__":
    main()
