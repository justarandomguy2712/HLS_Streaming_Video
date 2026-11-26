"""Xuất bản bộ HLS lên R2 và các lệnh kiểm tra từ PowerShell."""

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from contextlib import closing
from pathlib import Path
from threading import Event
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from backend.config import R2Settings, load_settings
from backend.database import initialize_database, queue_publication, retry_upload
from backend.processing.hls_generator import read_manifest, segments_from_playlist
from backend.storage.r2_client import R2Client
from backend.storage.r2_downloader import download_object
from backend.storage.r2_uploader import upload_verified

HLS_NAMES = {"init.mp4", "playlist.m3u8", "metadata.json"}


def is_media_name(name: str) -> bool:
    return name in HLS_NAMES or re.fullmatch(r"seg_[0-9]{6,}\.m4s", name) is not None


def media_key(video_id: UUID, name: str) -> str:
    if not is_media_name(name):
        raise ValueError("Tên tệp HLS không hợp lệ.")
    return f"videos/{video_id}/{name}"


class R2Storage:
    def __init__(self, config: R2Settings) -> None:
        self.config = config
        self.client = R2Client(config)

    def matches(self, video: dict) -> bool:
        return (video["storage_bucket"] == self.config.bucket_name
                and video["storage_endpoint"] == self.config.endpoint_url)

    def playlist_url(self, video_id: UUID, key: str | None = None) -> str:
        if self.config.public_base_url:
            return f"{self.config.public_base_url}/{key or media_key(video_id, 'playlist.m3u8')}"
        return f"/api/uploads/{video_id}/media/playlist.m3u8"

    def discard_attempt(self, prefix: str, stop: Event) -> None:
        if re.fullmatch(r"videos/[0-9a-f-]{36}/[0-9a-f-]{36}/", prefix) is None:
            raise ValueError("Prefix tạm không hợp lệ.")
        if self.client.head(prefix + "playlist.m3u8") is not None:
            raise ValueError("Attempt có playlist đã công bố. Cần đối chiếu receipt.")
        s3 = self.client.s3
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=self.client.bucket, Prefix=prefix):
            if stop.is_set():
                raise InterruptedError("Worker đang dừng.")
            objects = [
                {"Key": item["Key"]} for item in page.get("Contents", [])
                if is_media_name(item["Key"].removeprefix(prefix)) and item["Key"].startswith(prefix)
            ]
            if objects:
                response = s3.delete_objects(Bucket=self.client.bucket, Delete={"Objects": objects, "Quiet": True})
                if response.get("Errors"):
                    raise OSError("R2 chưa dọn được object của lần xử lý cũ.")

    def read_manifest(self, video_id: UUID, prefix: str | None = None) -> dict:
        response = self.client.open(prefix + "metadata.json" if prefix else media_key(video_id, "metadata.json"))
        with closing(response["Body"]) as body:
            contents = body.read()
        if hashlib.sha256(contents).hexdigest() != response.get("Metadata", {}).get("sha256"):
            raise ValueError("SHA-256 của metadata trên R2 không khớp.")
        return json.loads(contents)

    def publish(self, video_id: UUID, directory: Path, stop: Event, manifest: dict | None = None) -> str:
        metadata = directory / "metadata.json"
        playlist = directory / "playlist.m3u8"
        if manifest is None:
            manifest = read_manifest(directory)
        if manifest["video_id"] != str(video_id) or manifest["playlist"] != playlist.name:
            raise ValueError("Metadata HLS không khớp UUID hoặc playlist.")

        self.client.check_bucket()
        init = manifest["init_segment"]
        if init["filename"] != "init.mp4":
            raise ValueError("Metadata HLS thiếu init.mp4.")
        self.upload_segment(video_id, directory, init, stop)
        for (index, name, duration), item in zip(
            segments_from_playlist(playlist), manifest["segments"], strict=True
        ):
            if (item["sequence"] != index or item["filename"] != name
                    or item["duration_seconds"] != float(duration)):
                raise ValueError("Metadata và playlist HLS không khớp.")
            self.upload_segment(video_id, directory, item, stop)

        # Playlist là object cuối để mọi URI tương đối đã có dữ liệu xác minh được.
        for path in (metadata, playlist):
            upload_verified(self.client, media_key(video_id, path.name), path, stop)
        return media_key(video_id, playlist.name)

    def upload_segment(self, video_id: UUID, directory: Path, item: dict, stop: Event) -> None:
        name = item["filename"]
        upload_verified(
            self.client, media_key(video_id, name), directory / name, stop,
            source_size=item["size_bytes"], source_hash=item["sha256"],
        )


def remove_local_hls(directory: Path) -> None:
    if directory.is_symlink() or directory.is_junction():
        raise OSError("Thư mục HLS không được là liên kết.")
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if is_media_name(path.name):
            path.unlink(missing_ok=True)
    if not any(directory.iterdir()):
        directory.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-bucket")
    head = commands.add_parser("head")
    head.add_argument("key")
    download = commands.add_parser("download")
    download.add_argument("key")
    download.add_argument("destination", type=Path)
    queue = commands.add_parser("queue")
    queue.add_argument("video_id", type=UUID)
    retry = commands.add_parser("retry")
    retry.add_argument("video_id", type=UUID)
    args = parser.parse_args()
    try:
        settings = load_settings()
        if settings.r2 is None:
            raise ValueError("Cần cấu hình R2 trong môi trường hoặc .env.")
        if args.command == "retry":
            initialize_database(settings.database_path)
            if not (settings.uploads_dir / f"{args.video_id}.mp4").is_file():
                raise ValueError("Thiếu MP4 tạm của tác vụ. Hãy upload lại từ nguồn.")
            if not retry_upload(settings.database_path, str(args.video_id),
                                settings.r2.bucket_name, settings.r2.endpoint_url):
                raise ValueError("Chỉ thử lại job failed gắn với bucket và endpoint hiện tại.")
            result = {"id": str(args.video_id), "status": "uploaded"}
        elif args.command == "queue":
            initialize_database(settings.database_path)
            directory = settings.processing_dir / str(args.video_id)
            if not (directory / "metadata.json").is_file():
                raise ValueError("Cần có bộ HLS cục bộ trước khi đưa vào hàng đợi R2.")
            if not queue_publication(
                settings.database_path, str(args.video_id),
                settings.r2.bucket_name, settings.r2.endpoint_url,
            ):
                raise ValueError("Video chưa sẵn sàng để đưa vào hàng đợi R2.")
            result = {"id": str(args.video_id), "status": "uploaded"}
        else:
            client = R2Client(settings.r2)
            if args.command == "check-bucket":
                client.check_bucket()
                result = {"bucket": client.bucket, "status": "ok"}
            elif args.command == "head":
                info = client.head(args.key)
                if info is None:
                    raise FileNotFoundError(args.key)
                result = asdict(info)
            else:
                result = asdict(download_object(client, args.key, args.destination))
        print(json.dumps(result))
    except (BotoCoreError, ClientError, OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
