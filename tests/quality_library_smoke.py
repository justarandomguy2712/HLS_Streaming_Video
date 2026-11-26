"""Kiểm chứng chất lượng, toàn vẹn và phân trang Phase 07 trên Windows."""

import argparse
import hashlib
import json
import logging
import sqlite3
import subprocess
from contextlib import closing, contextmanager, nullcontext
from fractions import Fraction
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import UUID, uuid4

import boto3
from moto.server import ThreadedMotoServer

from backend.config import R2Settings, load_settings
from backend.database import initialize_database
from backend.storage.r2_downloader import download_object
from backend.storage.r2_storage import R2Storage
from tests.cloud_first_smoke import assert_clean, completed, sample
from tests.r2_smoke import app_server
from tests.upload_smoke import request, upload


def make_fixture(ffmpeg, source, dimensions, rate, *, video="libx264", audio=None):
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
        f"testsrc2=s={dimensions}:r={rate}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440", "-c:a", audio, "-b:a", "192k"]
    command += ["-t", "1.5", "-c:v", video, "-g", str(rate), "-threads:v", "4", "-pix_fmt", "yuv420p"]
    if dimensions == "1920x1080":
        command += ["-vf", "setsar=4/3"]
    if video == "libx264":
        command += ["-preset", "ultrafast", "-crf", "10", "-keyint_min", str(rate), "-sc_threshold", "0"]
    else:
        command += ["-q:v", "3"]
    subprocess.run([*command, str(source)], check=True)


def probe(ffprobe, path):
    return json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries",
        "format=bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
        "sample_aspect_ratio,display_aspect_ratio",
        "-of", "json", str(path),
    ]))


def packet_hashes(ffprobe, path, selector="v:0"):
    contents = subprocess.check_output([
        ffprobe, "-v", "error", "-select_streams", selector, "-show_packets",
        "-show_entries", "packet=data_hash", "-show_data_hash", "sha256", "-of", "json", str(path),
    ])
    return [packet["data_hash"] for packet in json.loads(contents)["packets"]]


def options(mode, seconds=1):
    return {
        "VIDEO_PROCESSING_MODE": mode, "STREAM_SEGMENTS_TO_R2": "true",
        "KEEP_ORIGINAL_VIDEO": "false", "DELETE_LOCAL_AFTER_VERIFICATION": "true",
        "HLS_SEGMENT_DURATION": str(seconds), "HLS_DISK_RESERVE_BYTES": "0",
        "VIDEO_LIST_PAGE_SIZE": "20", "ALLOW_STREAM_COPY": "true",
        "PRESERVE_RESOLUTION": "true", "PRESERVE_FRAME_RATE": "true", "ALLOW_DOWNSCALING": "false",
        "VIDEO_ENCODER": "auto", "ENABLE_NVENC": "true",
    }


def verify_case(args, root, config, storage, label, source, mode, *, copy_video, audio_encoder, seconds=1):
    data = root / mode
    env = options(mode, seconds)
    with source.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    with app_server(data, args.ffmpeg, args.ffprobe, config, **env) as origin:
        status_path = upload(origin, source, source.name, chunked=True)
        status = completed(origin, status_path)
        assert status["status"] == "ready", status
        assert status["source_sha256"] == sha
        identifier = status["id"]
        assert_clean(data, identifier)
        detail = request(origin, "GET", f"/api/videos/{identifier}")[2]
        prefix = detail["r2_prefix"]
        manifest = storage.read_manifest(UUID(identifier), prefix)
        assert manifest["processing_mode"] == mode
        assert (manifest["encoder"] == "copy") == copy_video
        assert manifest["audio_encoder"] == audio_encoder
        assert manifest["processing_note"] == status["processing_note"]

        out = root / label
        out.mkdir()
        for name in ["playlist.m3u8", "init.mp4", *(row["filename"] for row in manifest["segments"])]:
            download_object(storage.client, prefix + name, out / name)
        original = probe(args.ffprobe, source)
        output = probe(args.ffprobe, out / "playlist.m3u8")
        src_video = next(stream for stream in original["streams"] if stream["codec_type"] == "video")
        dst_video = next(stream for stream in output["streams"] if stream["codec_type"] == "video")
        assert dst_video["codec_name"] == "h264"
        for field in ("width", "height", "sample_aspect_ratio", "display_aspect_ratio"):
            assert dst_video[field] == src_video[field], (field, src_video, dst_video)
        src_fps = Fraction(src_video["avg_frame_rate"])
        dst_fps = Fraction(dst_video["avg_frame_rate"])
        assert abs(src_fps - dst_fps) <= Fraction(1, 100), (src_video, dst_video)
        assert [stream["codec_name"] for stream in output["streams"] if stream["codec_type"] == "audio"] == (
            ["aac"] if audio_encoder else []
        )
        if copy_video:
            assert packet_hashes(args.ffprobe, source) == packet_hashes(args.ffprobe, out / "playlist.m3u8")
        if audio_encoder == "copy":
            # MOV edit lists may represent AAC priming differently. Compare the encoded audio payloads
            # that occur on the playback timeline, allowing an initial priming packet to be omitted.
            before = packet_hashes(args.ffprobe, source, "a:0")
            after = packet_hashes(args.ffprobe, out / "playlist.m3u8", "a:0")
            assert before == after or before[1:] == after
        with source.open("rb") as handle:
            assert hashlib.file_digest(handle, "sha256").hexdigest() == sha

    with app_server(data, args.ffmpeg, args.ffprobe, config, **env) as origin:
        code, _, playback = request(origin, "GET", f"/api/videos/{identifier}/playback")
        assert code == 200 and playback["playback_url"] == status["playback_url"]
        assert_clean(data, identifier)
        if not config.public_base_url:
            with urlopen(origin + playback["playback_url"], timeout=10) as response:
                assert response.status == 200 and b"#EXT-X-ENDLIST" in response.read()
    record = {
        "case": label, "id": identifier, "resolution": f'{dst_video["width"]}x{dst_video["height"]}',
        "fps": str(dst_fps), "encoder": manifest["encoder"], "audio": audio_encoder, "mode": mode,
        "source_bitrate": int(original["format"].get("bit_rate", 0)),
        "duration": detail["duration"], "segments": detail["segment_count"], "url": status["playback_url"],
    }
    print(json.dumps(record), flush=True)
    with (root / "result.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def check_catalog(args, root, config):
    data = root / "synthetic-catalog"
    database = data / "database" / "videos.sqlite3"
    initialize_database(database)
    count = 10025
    timestamp = "2025-01-01T12:00:00.000Z"
    identifiers = [str(UUID(int=index)) for index in range(1, count + 1)]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            "INSERT INTO videos (id, original_filename, title, created_at, status, original_size) "
            "VALUES (?, 'Synthetic.mp4', 'Synthetic', ?, 'failed', 120)",
            [(identifier, timestamp) for identifier in identifiers],
        )
        query = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM videos WHERE (created_at, id) < (?, ?) "
            "ORDER BY created_at DESC, id DESC LIMIT 21", (timestamp, identifiers[-1]),
        ).fetchall()
        assert "videos_created" in str(query) and "TEMP B-TREE" not in str(query), query

    env = options("auto")
    with app_server(data, args.ffmpeg, args.ffprobe, config, **env) as origin:
        first_time = perf_counter()
        first = request(origin, "GET", "/api/videos")[2]
        first_ms = (perf_counter() - first_time) * 1000
        assert len(first["items"]) == 20 and first["next_offset"] == 20 and first["next_cursor"]
        old_page = request(origin, "GET", "/api/videos?offset=20")[2]
        assert old_page["items"][0]["id"] == identifiers[-21]
        assert request(origin, "GET", "/api/videos?limit=201")[0] == 422
        assert request(origin, "GET", "/api/videos?cursor=invalid")[0] == 400

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO videos (id, original_filename, created_at, status) VALUES (?, ?, ?, 'failed')",
            (str(uuid4()), "newer.mp4", "2025-01-02T12:00:00.000Z"),
        )

    collect = [item["id"] for item in first["items"]]
    cursor = first["next_cursor"]
    start = perf_counter()
    with app_server(data, args.ffmpeg, args.ffprobe, config, **env) as origin:
        # Khôi phục cursor từ trước restart, dù có insert mới ở đầu thư viện.
        while cursor:
            params = urlencode({"cursor": cursor, "limit": 200})
            code, _, page = request(origin, "GET", "/api/videos?" + params)
            assert code == 200 and len(page["items"]) <= 200
            collect.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
    assert collect == identifiers[::-1], (len(collect), len(set(collect)))
    print(f"PASS {count} synthetic metadata rows; first 20 {first_ms:.1f} ms; cursor walk {perf_counter() - start:.2f} s")


@contextmanager
def local_s3():
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--long", action="store_true", help="Thêm một video tổng hợp 3 giờ.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(dir=args.scratch_dir, prefix="quality07-")).resolve()
    print(f"Scratch: {root}", flush=True)
    if args.real:
        config = load_settings().r2
        if config is None:
            parser.error("Thiếu cấu hình R2.")
        emulator = nullcontext()
    else:
        emulator = local_s3()

    with emulator as server:
        if not args.real:
            host, port = server.get_host_and_port()
            config = R2Settings(f"http://{host}:{port}", "quality-test", uuid4().hex, uuid4().hex)
            with closing(boto3.client("s3", endpoint_url=config.endpoint_url, region_name="us-east-1",
                                      aws_access_key_id=config.access_key_id,
                                      aws_secret_access_key=config.secret_access_key)) as client:
                client.create_bucket(Bucket=config.bucket_name)
        storage = R2Storage(config)
        try:
            inputs = []
            for label, dimensions, fps, video, audio in [
                ("1080p24", "1920x1080", 24, "libx264", "aac"),
                ("1440p30", "2560x1440", 30, "libx264", "ac3"),
                ("4k60", "3840x2160", 60, "libx264", None),
                ("1080p60-mpeg4", "1920x1080", 60, "mpeg4", "aac"),
            ]:
                source = root / f"{label}.mp4"
                make_fixture(args.ffmpeg, source, dimensions, fps, video=video, audio=audio)
                inputs.append(source)
            verify_case(args, root, config, storage, "1080-original", inputs[0], "original",
                        copy_video=True, audio_encoder="copy")
            verify_case(args, root, config, storage, "1440-audio-transcode", inputs[1], "auto",
                        copy_video=True, audio_encoder="aac")
            verify_case(args, root, config, storage, "4k60-copy", inputs[2], "auto",
                        copy_video=True, audio_encoder=None)
            verify_case(args, root, config, storage, "1080p60-transcode", inputs[3], "auto",
                        copy_video=False, audio_encoder="copy")
            verify_case(args, root, config, storage, "1080-high-quality", inputs[0], "high_quality",
                        copy_video=False, audio_encoder="aac")
            with app_server(root / "original", args.ffmpeg, args.ffprobe, config, **options("original")) as origin:
                failed = completed(origin, upload(origin, inputs[1], inputs[1].name))
                assert failed["status"] == "failed" and "ORIGINAL" in failed["error"] and "Audio" in failed["error"]
                assert not failed["playlist_key"]
                print("PASS ORIGINAL rejected an incompatible audio stream without publishing", flush=True)
            source = root / "sparse-idr.mp4"
            sample(args.ffmpeg, source, 10, 9)
            verify_case(args, root, config, storage, "sparse-idr-copy", source, "original",
                        copy_video=True, audio_encoder=None, seconds=4)
            if args.long:
                source = root / "3h.mp4"
                sample(args.ffmpeg, source, 10800, 60)
                verify_case(args, root, config, storage, "3h-copy", source, "original",
                            copy_video=True, audio_encoder=None, seconds=60)
            check_catalog(args, root, config)
        finally:
            storage.client.s3.close()


if __name__ == "__main__":
    main()
