"""Kiểm thử upload và đồng bộ danh mục bằng FFmpeg, SQLite và S3 mô phỏng."""

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import mkdtemp
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import UUID, uuid4

import boto3
from moto.server import ThreadedMotoServer

from backend.config import R2Settings
from backend.storage.online_library import OnlineLibrary, PAGE_SIZE, ROOT_KEY
from backend.storage.r2_storage import R2Storage
from tests.cloud_first_smoke import assert_clean, completed
from tests.r2_smoke import app_server
from tests.upload_smoke import generate_sample, request, upload


def fixture(number):
    identifier = str(UUID(int=number))
    return {
        "id": identifier, "title": f"Mẫu metadata {number}", "original_filename": "fixture.mp4",
        "playlist_key": f"videos/{identifier}/playlist.m3u8", "codecs": "avc1.64001e",
        "created_at": "2026-01-01T00:00:00.000Z", "duration": 1, "segment_count": 1,
    }


def verify_index(catalog):
    # Mẫu metadata chỉ dùng trong S3 mô phỏng, không tạo media cho các bản ghi này.
    for number in range(1, PAGE_SIZE + 1):
        catalog.publish(fixture(number), Event())

    # Bắt buộc hai publisher đọc cùng một ETag, chạy CAS trên S3 mô phỏng thật.
    from threading import Barrier, Lock

    barrier = Barrier(2, timeout=8)
    guard = Lock()
    reads = 0
    base_read = catalog.read

    def race_read(key):
        nonlocal reads
        outcome = base_read(key)
        if key == ROOT_KEY:
            with guard:
                reads += 1
                synchronize = reads <= 2
            if synchronize:
                barrier.wait()
        return outcome

    with patch.object(catalog, "read", side_effect=race_read), ThreadPoolExecutor(2) as workers:
        jobs = [workers.submit(catalog.publish, fixture(number), Event())
                for number in (PAGE_SIZE + 1, PAGE_SIZE + 2)]
        for future in jobs:
            future.result()
    assert reads >= 3, "Chưa kiểm thử được lần đọc lại sau tranh chấp ETag."
    catalog.publish(fixture(1), Event())
    current, _ = catalog.read(ROOT_KEY)
    archived = catalog.read_page(current["next"])
    assert len(current["items"]) == 2
    assert len(archived["items"]) == PAGE_SIZE
    assert len({row["id"] for row in current["items"] + archived["items"]}) == PAGE_SIZE + 2
    print("PASS rollover, conditional-write contention and duplicate retry across archived pages.", flush=True)


def wait_online(origin, path):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        status = request(origin, "GET", path)[2]
        if status["online_status"] == "published":
            return status
        sleep(0.2)
    raise AssertionError("Worker Windows không nối được video vào danh mục R2.")


def export_worker_fixture(storage, root):
    # Copy từng object theo luồng để import sang binding R2 cục bộ của Miniflare.
    destination = root / "r2"
    paginator = storage.client.s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage.client.bucket):
        for item in page.get("Contents", []):
            key = item["Key"]
            target = destination / key
            target.parent.mkdir(parents=True, exist_ok=True)
            response = storage.client.open(key)
            with closing(response["Body"]) as source, target.open("wb") as output:
                for block in source.iter_chunks(chunk_size=64 * 1024):
                    output.write(block)
    return destination


def run(args, storage, root):
    catalog = OnlineLibrary(storage.client)
    verify_index(catalog)
    source = root / "source.mp4"
    generate_sample(args.ffmpeg, source, 9.2, "libx264", "aac")
    data = root / "admin"
    options = {
        "ONLINE_LIBRARY_ENABLED": "true", "STREAM_SEGMENTS_TO_R2": "true",
        "KEEP_ORIGINAL_VIDEO": "false", "DELETE_LOCAL_AFTER_VERIFICATION": "true",
        "HLS_SEGMENT_DURATION": "4", "HLS_DISK_RESERVE_BYTES": "0",
    }
    old_root, _ = catalog.read(ROOT_KEY)
    with app_server(data, args.ffmpeg, args.ffprobe, storage.config, **options) as origin:
        first_path = upload(origin, source, "online-upload.mp4", chunked=True)
        first = wait_online(origin, first_path)
        assert first["status"] == "ready"
        assert_clean(data, first["id"])
    # Bản ghi thứ hai tạo ở lần chạy chưa bật online, sau đó tự cập nhật khi bật lại.
    disabled = {**options, "ONLINE_LIBRARY_ENABLED": "false"}
    with app_server(data, args.ffmpeg, args.ffprobe, storage.config, **disabled) as origin:
        second_path = upload(origin, source, "online-after-restart.mp4")
        second = completed(origin, second_path)
        assert second["status"] == "ready" and second["online_status"] == "disabled"
        assert_clean(data, second["id"])
    with app_server(data, args.ffmpeg, args.ffprobe, storage.config, **options) as origin:
        assert wait_online(origin, second_path)["id"] == second["id"]
        assert wait_online(origin, first_path)["id"] == first["id"]
    page, _ = catalog.read(ROOT_KEY)
    items = page["items"]
    assert [item["id"] for item in items[:2]] == [second["id"], first["id"]]
    assert items[2:] == old_root["items"] and page["next"] == old_root["next"]
    export = export_worker_fixture(storage, root)
    (root / "receipt.json").write_text(
        json.dumps({"id": first["id"], "other_id": second["id"], "r2": str(export)}), encoding="utf-8"
    )
    print("PASS 2 FFmpeg uploads synced, app restart/backfill, SHA verification and no admin media copies.")
    print("Receipt:", root / "receipt.json", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--scratch-dir", type=Path, required=True)
    args = parser.parse_args()
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(dir=args.scratch_dir, prefix="phase08-")).resolve()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    emulator = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    emulator.start()
    try:
        host, port = emulator.get_host_and_port()
        config = R2Settings(f"http://{host}:{port}", "phase08-test", uuid4().hex, uuid4().hex)
        with closing(boto3.client(
            "s3", endpoint_url=config.endpoint_url, region_name="us-east-1",
            aws_access_key_id=config.access_key_id, aws_secret_access_key=config.secret_access_key,
        )) as client:
            client.create_bucket(Bucket=config.bucket_name)
        storage = R2Storage(config)
        try:
            run(args, storage, root)
        finally:
            storage.client.s3.close()
    finally:
        emulator.stop()


if __name__ == "__main__":
    main()

