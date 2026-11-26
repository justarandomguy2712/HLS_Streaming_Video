"""Kiểm thử companion Windows với FFmpeg thật, S3 mô phỏng và lỗi publication có chủ đích."""

import argparse
import json
import logging
import shutil
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from unittest.mock import patch
from uuid import uuid4

import boto3
from moto.server import ThreadedMotoServer

from backend.binary.integrity import sha256_file
from backend.config import R2Settings, load_settings
from backend.database import get_video, initialize_database
from backend.local_processing import media_source, processing_lock, recycle_source, run_video, stage_source
from backend.services.upload_service import UploadService
from backend.storage.r2_downloader import transfer_verified
from backend.storage.r2_storage import R2Storage
from backend.worker_client import WorkerError
from tests.upload_smoke import generate_sample
from tests.online_library_smoke import export_worker_fixture


class PublicationFixture:
    def __init__(self) -> None:
        self.online = None
        self.reject = True
        self.payload = None

    def service(self, method, path, body=None):
        if method == "GET" and self.online is None:
            raise WorkerError(404, "")
        if method == "GET":
            return self.online
        assert path == "/api/local/videos"
        if self.reject:
            raise WorkerError(503, "D1 mô phỏng mất kết nối.")
        self.payload = body
        self.online = {"id": body["id"], "status": "READY", "playlist_key": body["playlist_key"],
                       "playback_checked_at": None}
        return self.online


def run(root, args, config):
    data = root / "data"
    settings = replace(load_settings(), uploads_dir=data / "uploads", processing_dir=data / "processing",
                       database_path=data / "state.sqlite3", r2=config, ffmpeg_bin=args.ffmpeg,
                       ffprobe_bin=args.ffprobe, worker_endpoint="http://127.0.0.1:9999",
                       keep_original=False, delete_after_verification=True, stream_segments=True,
                       disk_reserve_bytes=0)
    initialize_database(settings.database_path)
    for directory in (settings.uploads_dir, settings.processing_dir):
        directory.mkdir()
    source = root / "online-upload.mp4"
    generate_sample(args.ffmpeg, source, 9.2, "libx264", "aac")
    sha = sha256_file(source)
    fixture = PublicationFixture()
    storage = R2Storage(config)
    service = UploadService(settings, storage)
    try:
        with processing_lock(settings.database_path):
            identifier = stage_source(service, media_source(settings, str(source)), archive=False, recycle=True)
            try:
                run_video(service, fixture, identifier)
            except WorkerError as exc:
                assert exc.status == 503
            else:
                raise AssertionError("Không thử được lỗi publication.")
            row = get_video(settings.database_path, identifier)
            assert row["status"] == "ready"
            temporary = settings.uploads_dir / f"{identifier}.mp4"
            assert temporary.is_file() and sha256_file(temporary) == sha
            archive_key = row["r2_prefix"] + "source.mp4"
            assert storage.client.head(archive_key) is None
            for name in ("playlist.m3u8", "init.mp4", "seg_000000.m4s"):
                key = row["r2_prefix"] + name
                transfer_verified(storage.client, key, storage.client.head(key))

        # Tạo lại service như sau gián đoạn. HLS đã xác minh nên chỉ công bố và dọn.
        fixture.reject = False
        resumed = UploadService(settings, storage)
        with processing_lock(settings.database_path):
            run_video(resumed, fixture, identifier)
            assert not temporary.exists()
            assert not (settings.processing_dir / f".{identifier}.stream").exists()
            assert fixture.payload["segment_count"] == 3
            assert fixture.payload["archived_source_size"] is None
            assert source.is_file() and sha256_file(source) == sha
            with patch("backend.local_processing.recycle_windows") as recycle:
                try:
                    recycle_source(settings, fixture, identifier)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Chưa xác nhận phát/tua mà vẫn cho xóa.")
                recycle.assert_not_called()
                fixture.online["playback_checked_at"] = "2026-11-26T08:00:00.000Z"
                with patch("builtins.input", return_value="NO"):
                    recycle_source(settings, fixture, identifier)
                recycle.assert_not_called()
                with patch("builtins.input", return_value="RECYCLE " + identifier):
                    recycle_source(settings, fixture, identifier)
                recycle.assert_called_once_with(source)
            assert source.is_file()

            other = root / "online-after-restart.mp4"
            shutil.copyfile(source, other)
            archive_job = stage_source(resumed, media_source(settings, str(other)), archive=True, recycle=False)
            archive_fixture = PublicationFixture()
            archive_fixture.reject = False
            run_video(resumed, archive_fixture, archive_job)
            saved = get_video(settings.database_path, archive_job)
            original_key = saved["r2_prefix"] + "source.mp4"
            original_object = storage.client.head(original_key)
            assert original_object.sha256 == sha
            transfer_verified(storage.client, original_key, original_object)
            assert archive_fixture.payload["archived_source_size"] == other.stat().st_size
            assert other.is_file()
            if args.export:
                r2 = export_worker_fixture(storage, root)
                receipt = root / "receipt.json"
                receipt.write_text(json.dumps({
                    "id": identifier, "other_id": archive_job, "r2": str(r2),
                    "payloads": [fixture.payload, archive_fixture.payload],
                }), encoding="utf-8")
                print("Receipt:", receipt, flush=True)
    finally:
        storage.client.s3.close()
    print("PASS companion: copy, FFmpeg, verified R2, failed publication retention, resume cleanup, source guards (Recycle Bin mocked).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--export", action="store_true", help="Xuất fixture R2 + payload đã tạo để kiểm D1/Edge.")
    args = parser.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    emulator = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    emulator.start()
    try:
        host, port = emulator.get_host_and_port()
        config = R2Settings(f"http://{host}:{port}", "companion-test", uuid4().hex, uuid4().hex)
        with closing(boto3.client("s3", endpoint_url=config.endpoint_url, region_name="us-east-1",
                                  aws_access_key_id=config.access_key_id,
                                  aws_secret_access_key=config.secret_access_key)) as s3:
            s3.create_bucket(Bucket=config.bucket_name)
        if args.export:
            run(Path(mkdtemp(dir=args.scratch_dir, prefix="phase08-companion-")).resolve(), args, config)
        else:
            with TemporaryDirectory(dir=args.scratch_dir, prefix="phase08-companion-") as path:
                run(Path(path).resolve(), args, config)
    finally:
        emulator.stop()


if __name__ == "__main__":
    main()

