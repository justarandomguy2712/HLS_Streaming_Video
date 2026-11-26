"""Smoke test thư viện với video tổng hợp, SQLite và R2 thật."""

import argparse
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from threading import Event
from urllib.parse import quote
from uuid import UUID

from backend.config import load_settings
from backend.storage.r2_downloader import transfer_verified
from backend.storage.r2_storage import R2Storage
from backend.storage.r2_uploader import MEDIA_HEADERS
from tests.upload_smoke import await_status, read_chunks, request


def make_video(command: str, path: Path, pattern: str, duration: float) -> None:
    subprocess.run(
        [command, "-nostdin", "-n", "-v", "error", "-f", "lavfi", "-i", pattern,
         "-f", "lavfi", "-i", "sine=frequency=510:sample_rate=48000", "-t", str(duration),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-force_key_frames", "expr:gte(t,n_forced*4)", "-sc_threshold", "0", "-c:a", "aac", str(path)],
        check=True,
    )


def upload_video(base_url: str, source: Path, *, chunked: bool) -> dict:
    headers = {"Content-Type": "video/mp4", "X-Filename": quote(source.name)}
    size = source.stat().st_size
    if not chunked:
        headers["Content-Length"] = str(size)
    with source.open("rb") as file:
        code, response_headers, accepted = request(
            base_url, "POST", "/api/videos/upload", read_chunks(file) if chunked else file,
            headers, chunked=chunked,
        )
    assert code == 202 and accepted["status"] == "uploaded", (code, accepted)
    video_id = str(UUID(accepted["id"]))
    location = response_headers["Location"]
    assert location == f"/api/videos/{video_id}/status"
    return {"id": video_id, "filename": source.name, "size": size}


def library_records(base_url: str, ids: set[str]) -> dict:
    offset = 0
    found = {}
    while offset is not None and ids - found.keys():
        code, _, result = request(base_url, "GET", f"/api/videos?limit=1&offset={offset}")
        assert code == 200, result
        for item in result["items"]:
            if item["id"] in ids:
                found[item["id"]] = item
        offset = result["next_offset"]
    assert found.keys() == ids
    return found


def verify(base_url: str, tests: list[dict], *, expect_cleanup: bool, published: bool) -> None:
    settings = load_settings()
    if settings.r2 is None:
        raise ValueError("Cần cấu hình bucket R2 kiểm thử.")
    storage = R2Storage(settings.r2)
    try:
        records = library_records(base_url, {item["id"] for item in tests})
        for item in tests:
            identifier = UUID(item["id"])
            endpoint = f"/api/videos/{identifier}"
            code, _, status = request(base_url, "GET", f"{endpoint}/status")
            assert code == 200 and status["status"] == "ready" and status["storage"] == "r2", status
            code, _, detail = request(base_url, "GET", endpoint)
            assert code == 200 and detail == records[item["id"]], detail
            assert detail["title"] == Path(item["filename"]).stem and detail["playable"]
            assert detail["original_filename"] == item["filename"]
            assert detail["original_size"] == item["size"]
            assert abs(detail["duration"] - item["duration"]) <= 0.15
            assert detail["segment_count"] == item["segments"]
            assert detail["r2_prefix"].startswith(f"videos/{identifier}/")
            assert detail["init_key"] == detail["r2_prefix"] + "init.mp4"
            assert detail["playlist_key"] == f"{detail['r2_prefix']}playlist.m3u8"
            code, playback_headers, playback = request(base_url, "GET", f"{endpoint}/playback")
            assert code == 200 and playback["playlist_key"] == detail["playlist_key"]
            assert playback["playback_url"] == storage.playlist_url(identifier, detail["playlist_key"])
            assert playback_headers["Cache-Control"] == "no-store"
            if expect_cleanup:
                directory = settings.processing_dir / str(identifier)
                stage = settings.processing_dir / f".{identifier}.stream"
                source_copy = settings.uploads_dir / f"{identifier}.mp4"
                deadline = time.monotonic() + 10
                while (directory.exists() or stage.exists() or source_copy.exists()) and time.monotonic() < deadline:
                    time.sleep(0.10)
                assert not any(item.exists() for item in (directory, stage, source_copy))
            with closing(sqlite3.connect(f"{settings.database_path.as_uri()}?mode=ro", uri=True)) as database:
                row = database.execute(
                    """SELECT title, original_size, duration, segment_count, r2_prefix, playlist_key
                       FROM videos WHERE id = ?""", (str(identifier),),
                ).fetchone()
                assert row == tuple(detail[key] for key in (
                    "title", "original_size", "duration", "segment_count", "r2_prefix", "playlist_key",
                )), row
            if published:
                manifest = storage.read_manifest(identifier, detail["r2_prefix"])
                objects = [manifest["init_segment"], *manifest["segments"],
                           {"filename": "playlist.m3u8"}, {"filename": "metadata.json"}]
                for entry in objects:
                    filename = entry["filename"]
                    key = detail["r2_prefix"] + filename
                    remote = storage.client.head(key)
                    assert remote and remote.content_type == MEDIA_HEADERS[Path(filename).suffix][0], key
                    if "sha256" in entry:
                        assert remote.sha256 == entry["sha256"] and remote.size == entry["size_bytes"], key
                    transfer_verified(storage.client, key, remote, stop=Event())
            print(f"PASS {identifier}: {detail['original_size']} bytes, {detail['duration']}s, "
                  f"{detail['segment_count']} segments, SQLite/R2 ready")
    finally:
        storage.client.s3.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("publish", "verify"))
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expect-cleanup", action="store_true")
    args = parser.parse_args()
    if args.stage == "verify":
        verify(args.base_url, json.loads(args.receipt.read_text(encoding="utf-8")),
               expect_cleanup=args.expect_cleanup, published=False)
        return

    ffmpeg = load_settings().ffmpeg_bin
    uploaded = []
    for name, pattern, duration, segments, chunked in (
        ("Phase 06 - Ngan.mp4", "testsrc2=size=160x90:rate=20", 2.4, 1, False),
        ("Phase 06 - Dai.mp4", "testsrc2=size=640x360:rate=30", 29.2, 8, True),
    ):
        path = args.receipt.parent / name
        make_video(ffmpeg, path, pattern, duration)
        item = upload_video(args.base_url, path, chunked=chunked)
        uploaded.append({**item, "duration": duration, "segments": segments})
        args.receipt.write_text(json.dumps(uploaded, indent=2), encoding="utf-8")
        print(f"Accepted {item['id']}: {item['size']} bytes; chunked={chunked}")
    for item in uploaded:
        status, _ = await_status(args.base_url, f"/api/videos/{item['id']}/status")
        assert status["status"] == "ready", status
    verify(args.base_url, uploaded, expect_cleanup=args.expect_cleanup, published=True)


if __name__ == "__main__":
    main()
