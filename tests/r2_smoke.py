"""Kiểm thử Phase 04 bằng S3 mô phỏng chỉ trên loopback Windows."""

import argparse
import hashlib
import logging
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import DEFAULT, patch
from urllib.request import urlopen
from uuid import UUID, uuid4

import boto3
import uvicorn
from botocore.exceptions import EndpointConnectionError
from moto.server import ThreadedMotoServer

from backend.binary.integrity import sha256_file
from backend.config import R2Settings
from backend.storage.r2_downloader import IntegrityError, download_object
from backend.storage.r2_storage import R2Storage, media_key
from backend.storage.r2_uploader import MEDIA_HEADERS, upload_verified
from tests.upload_smoke import await_status, generate_sample, request, upload


@contextmanager
def app_server(data: Path, ffmpeg: str, ffprobe: str, config: R2Settings, **overrides):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {
        **os.environ, "HLS_DATA_DIR": str(data), "HLS_SEGMENT_DURATION": "4",
        "HLS_FFMPEG_BIN": ffmpeg, "HLS_FFPROBE_BIN": ffprobe,
        "R2_ACCOUNT_ID": "", "R2_ENDPOINT_URL": config.endpoint_url,
        "R2_BUCKET_NAME": config.bucket_name, "R2_ACCESS_KEY_ID": config.access_key_id,
        "R2_SECRET_ACCESS_KEY": config.secret_access_key, "R2_PUBLIC_BASE_URL": config.public_base_url,
        "DELETE_LOCAL_AFTER_UPLOAD": "true", "PYTHONDONTWRITEBYTECODE": "1",
        "STREAM_SEGMENTS_TO_R2": "false", "KEEP_ORIGINAL_VIDEO": "true",
        "STORAGE_PROVIDER": "auto", "MIN_FREE_DISK_GB": "0",
        "ENABLE_LEGACY_LAB_HTTP": "true", "WORKER_ENDPOINT": "",
        **overrides,
    }
    with patch.dict(os.environ, env):
        server = uvicorn.Server(
            uvicorn.Config("backend.main:app", host="127.0.0.1", port=port,
                           access_log=False, log_level="critical")
        )
        thread = Thread(target=server.run, name="phase04-http-test", daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{port}"
            for _ in range(80):
                if not thread.is_alive():
                    raise RuntimeError("Uvicorn dừng trước khi sẵn sàng.")
                try:
                    if request(base_url, "GET", "/health")[0] == 200:
                        break
                except ConnectionRefusedError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Uvicorn chưa sẵn sàng.")
            yield base_url
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            if thread.is_alive():
                raise TimeoutError("Uvicorn chưa dừng sau kiểm thử.")


def test_transfer(storage, directory):
    client = storage.client
    client.check_bucket()
    path = directory / "metadata.json"
    path.write_bytes(b"a" * (9 * 1024 * 1024))
    key = "smoke/multipart.json"
    assert client.head(key) is None
    original = client.s3.upload_file
    failures = [EndpointConnectionError(endpoint_url=storage.config.endpoint_url), DEFAULT]
    with patch.object(client.s3, "upload_file", wraps=original, side_effect=failures) as transfer:
        upload_verified(client, key, path, Event())
        assert transfer.call_count == 2
    raw = client.s3.head_object(Bucket=client.bucket, Key=key)
    assert "-2" in raw["ETag"], raw["ETag"]
    assert client.head(key).size == path.stat().st_size
    target = directory / "download.json"
    download_object(client, key, target)
    assert sha256_file(target) == sha256_file(path)

    content_type, cache_control = MEDIA_HEADERS[".json"]
    stale_key = "smoke/tampered.json"
    client.s3.put_object(
        Bucket=client.bucket, Key=stale_key, Body=b"x",
        ContentType=content_type, CacheControl=cache_control,
        Metadata={"sha256": hashlib.sha256(b"y").hexdigest()},
    )
    rejected = directory / "bad.json"
    try:
        download_object(client, stale_key, rejected)
    except IntegrityError:
        assert not rejected.exists()
        assert not list(directory.glob(".r2-*.tmp"))
    else:
        raise AssertionError("Nội dung sai SHA-256 đã được chấp nhận.")
    print("PASS HEAD, upload retry, multipart 9 MiB, verified download and corrupt body rejection")


def test_application(storage, config, directory, ffmpeg, ffprobe):
    sample = directory / "source.mp4"
    generate_sample(ffmpeg, sample, 9.2, "libx264", "aac")
    data = directory / "data"
    with app_server(data, ffmpeg, ffprobe, config, ALLOW_STREAM_COPY="false") as origin:
        location = upload(origin, sample, sample.name)
        video, _ = await_status(origin, location)
        assert video["status"] == "ready" and video["storage"] == "r2", video
        identifier = UUID(video["id"])
        assert video["playlist_key"] == media_key(identifier, "playlist.m3u8")
        staging = data / "processing" / str(identifier)
        deadline = time.monotonic() + 5
        while staging.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not staging.exists()
        source_copy = data / "uploads" / f"{identifier}.mp4"
        assert source_copy.is_file()
        # Bỏ bản MP4 tổng hợp trong sandbox để xác nhận playback chỉ đọc S3.
        source_copy.unlink()

    expected = {
        "init.mp4", "playlist.m3u8", "metadata.json",
        *(f"seg_{index:06d}.m4s" for index in range(3)),
    }
    with app_server(data, ffmpeg, ffprobe, config) as origin:
        video, _ = await_status(origin, location)
        assert video["status"] == "ready" and video["playback_url"] == f"{location}/media/playlist.m3u8"
        for name in expected:
            remote = storage.client.head(media_key(identifier, name))
            with urlopen(f"{origin}{location}/media/{name}", timeout=10) as response:
                content_type, _ = MEDIA_HEADERS[Path(name).suffix]
                assert response.headers.get_content_type() == remote.content_type == content_type
                assert int(response.headers["Content-Length"]) == remote.size
                assert hashlib.file_digest(response, "sha256").hexdigest() == remote.sha256
        subprocess.run(
            [ffmpeg, "-v", "error", "-xerror", "-i", origin + video["playback_url"], "-f", "null", "-"],
            check=True,
        )
        assert request(origin, "GET", f"{location}/media/seg_999999.m4s")[0] == 404
    print("PASS 6 published objects, local HLS deleted, remote HTTP playback decoded after app restart")

    public = replace(config, public_base_url="https://media.example.com")
    with app_server(data, ffmpeg, ffprobe, public) as origin:
        video, _ = await_status(origin, location)
        assert video["playback_url"] == f"https://media.example.com/{video['playlist_key']}"

    unavailable = replace(config, bucket_name="absent-test-bucket")
    with app_server(data, ffmpeg, ffprobe, unavailable) as origin:
        location = upload(origin, sample, "failed.mp4")
        failed, _ = await_status(origin, location)
        assert failed["status"] == "failed" and failed["playback_url"] is None
        retained = data / "processing" / failed["id"]
        assert (retained / "playlist.m3u8").is_file()
        assert (retained / "metadata.json").is_file()
        assert request(origin, "GET", f"{location}/media/playlist.m3u8")[0] == 404
    print("PASS CDN URL selection and bucket failure retains staging")


def test_legacy_publication(config, directory, ffmpeg, ffprobe):
    data = directory / "legacy-data"
    sample = directory / "source.mp4"
    disabled = {name: "" for name in ("R2_ENDPOINT_URL", "R2_BUCKET_NAME",
                                     "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")}
    with app_server(data, ffmpeg, ffprobe, config, **disabled) as origin:
        location = upload(origin, sample, "legacy.mp4")
        local, _ = await_status(origin, location)
        assert local["status"] == "ready" and local["storage"] == "local" and not local["playback_url"]
    local_hls = data / "processing" / local["id"]
    assert (local_hls / "playlist.m3u8").is_file()

    env = {
        **os.environ, "HLS_DATA_DIR": str(data), "R2_ACCOUNT_ID": "",
        "R2_ENDPOINT_URL": config.endpoint_url, "R2_BUCKET_NAME": config.bucket_name,
        "R2_ACCESS_KEY_ID": config.access_key_id, "R2_SECRET_ACCESS_KEY": config.secret_access_key,
        "R2_PUBLIC_BASE_URL": "",
    }
    with app_server(data, ffmpeg, ffprobe, config, DELETE_LOCAL_AFTER_UPLOAD="false") as origin:
        for command in [("check-bucket",), ("queue", local["id"])]:
            subprocess.run(
                [sys.executable, "-m", "backend.storage.r2_storage", *command],
                env=env, check=True,
            )
        published, _ = await_status(origin, location)
        assert published["status"] == "ready" and published["storage"] == "r2", published
    assert (local_hls / "playlist.m3u8").is_file()
    print("PASS local compatibility, CLI queue for existing video and disabled local cleanup")


def run(args):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("backend.services.upload_service").setLevel(logging.CRITICAL)
    emulator = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    emulator.start()
    try:
        host, port = emulator.get_host_and_port()
        config = R2Settings(f"http://{host}:{port}", "hls-smoke", uuid4().hex, uuid4().hex)
        bootstrap = boto3.client(
            "s3", endpoint_url=config.endpoint_url, region_name="us-east-1",
            aws_access_key_id=config.access_key_id, aws_secret_access_key=config.secret_access_key,
        )
        try:
            bootstrap.create_bucket(Bucket=config.bucket_name)
        finally:
            bootstrap.close()
        storage = R2Storage(config)
        try:
            with TemporaryDirectory(dir=args.scratch_dir, prefix="phase04-") as scratch:
                directory = Path(scratch).resolve()
                test_transfer(storage, directory)
                test_application(storage, config, directory, args.ffmpeg, args.ffprobe)
                test_legacy_publication(config, directory, args.ffmpeg, args.ffprobe)
        finally:
            storage.client.s3.close()
    finally:
        emulator.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--scratch-dir", type=Path)
    run(parser.parse_args())
