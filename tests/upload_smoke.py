"""Kiểm thử upload và đầu ra HLS qua một Uvicorn đang chạy."""

import argparse
import hashlib
import http.client
import json
import struct
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

CHUNK_BYTES = 128 * 1024


def request(base_url, method, path, body=None, headers=None, *, chunked=False):
    url = urlsplit(base_url)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
    try:
        connection.request(method, path, body, headers or {}, encode_chunked=chunked)
        with connection.getresponse() as response:
            return response.status, response.headers, json.load(response)
    finally:
        connection.close()


def generate_sample(ffmpeg, path, duration, video_codec, audio_codec=None, *, segment_seconds=4):
    command = [
        ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=20",
    ]
    if audio_codec:
        command.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"])
        command.extend(["-c:a", audio_codec])
    command.extend(["-t", str(duration), "-c:v", video_codec, "-pix_fmt", "yuv420p"])
    if video_codec == "libx264":
        command.extend(["-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})", "-sc_threshold", "0"])
    command.append(str(path))
    subprocess.run(command, check=True)


def pad_mp4(path, padding_bytes):
    """Thêm box free hợp lệ để kiểm thử luồng HTTP nhiều MiB."""
    with path.open("ab") as file:
        file.write(struct.pack(">I4s", padding_bytes + 8, b"free"))
        for offset in range(0, padding_bytes, CHUNK_BYTES):
            file.write(bytes(min(CHUNK_BYTES, padding_bytes - offset)))


def read_chunks(file):
    while chunk := file.read(CHUNK_BYTES):
        yield chunk


def await_status(base_url, location):
    seen = {"uploaded"}
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, _, video = request(base_url, "GET", location)
        assert status == 200, video
        seen.add(video["status"])
        if video["status"] in {"ready", "failed"}:
            return video, seen
        time.sleep(0.1)
    raise TimeoutError(f"Video chưa hoàn thành: {location}")


def upload(base_url, path, filename, *, chunked=False):
    headers = {"Content-Type": "video/mp4", "X-Filename": quote(filename)}
    with path.open("rb") as file:
        if not chunked:
            headers["Content-Length"] = str(path.stat().st_size)
        body = read_chunks(file) if chunked else file
        status, response_headers, accepted = request(
            base_url, "POST", "/api/uploads", body, headers, chunked=chunked
        )
    assert status == 202 and accepted["status"] == "uploaded", (status, accepted)
    location = response_headers["Location"]
    assert location == f'/api/uploads/{UUID(accepted["id"])}'
    return location


def verify_hls(args, video, source, source_video, source_audio, expected_durations):
    assert video["status"] == "ready", video
    output = args.data_dir / "processing" / video["id"]
    manifest = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert video["playlist_key"] == f'{video["id"]}/playlist.m3u8'
    assert manifest["codecs"] == video["codecs"] and video["codecs"].startswith("avc1.")
    assert manifest["segment_target_seconds"] == args.segment_seconds
    info = manifest["source"]
    assert info["size_bytes"] == source.stat().st_size
    assert info["video"]["codec_name"] == source_video
    assert (info["video"]["width"], info["video"]["height"]) == (160, 90)
    assert info["duration_seconds"] > 0 and info["bit_rate_bps"] > 0
    assert (info["audio"]["codec_name"] if info["audio"] else None) == source_audio
    playlist = output / "playlist.m3u8"
    lines = playlist.read_text(encoding="utf-8").splitlines()
    assert '#EXT-X-MAP:URI="init.mp4"' in lines and "#EXT-X-ENDLIST" in lines
    assert "#EXT-X-INDEPENDENT-SEGMENTS" in lines
    segments = manifest["segments"]
    assert [segment["filename"] for segment in segments] == [
        line for line in lines if line and not line.startswith("#")
    ]
    durations = [round(segment["duration_seconds"], 2) for segment in segments]
    assert durations == expected_durations, durations
    elapsed = 0
    for sequence, segment in enumerate(segments):
        assert segment["sequence"] == sequence
        assert segment["filename"] == f"seg_{sequence:06d}.m4s"
        assert abs(segment["start_seconds"] - elapsed) < 1e-6
        elapsed += segment["duration_seconds"]
    for item in [manifest["init_segment"], *segments]:
        media = output / item["filename"]
        assert media.stat().st_size == item["size_bytes"] > 0
        with media.open("rb") as file:
            assert hashlib.file_digest(file, "sha256").hexdigest() == item["sha256"]
    probe = subprocess.run(
        [args.ffprobe, "-v", "error", "-show_entries", "stream=codec_name",
         "-of", "json", playlist.name],
        cwd=output, check=True, stdout=subprocess.PIPE,
    )
    expected = ["h264", "aac"] if source_audio else ["h264"]
    assert [stream["codec_name"] for stream in json.loads(probe.stdout)["streams"]] == expected
    subprocess.run(
        [args.ffmpeg, "-v", "error", "-xerror", "-i", playlist.name, "-f", "null", "-"],
        cwd=output, check=True,
    )


def run(args):
    result, _, health = request(args.base_url, "GET", "/health")
    assert result == 200 and health["database"] == "ok"
    cases = [
        (9.2, "libx264", "h264", "aac", False),
        (5.25, "mpeg4", "mpeg4", None, True),
        (2.3, "mpeg4", "mpeg4", "alac", False),
    ]
    with TemporaryDirectory(dir=args.scratch_dir) as folder:
        for index, (duration, encoder, source_video, audio, chunked) in enumerate(cases):
            sample = Path(folder) / f"sample-{index}.mp4"
            generate_sample(args.ffmpeg, sample, duration, encoder, audio, segment_seconds=args.segment_seconds)
            if chunked:
                pad_mp4(sample, 16 * 1024 * 1024)
            filename = f"Mẫu {index}.mp4"
            location = upload(args.base_url, sample, filename, chunked=chunked)
            video, seen = await_status(args.base_url, location)
            assert video["original_filename"] == filename
            segment_count = int(duration / args.segment_seconds)
            tail = round(duration - segment_count * args.segment_seconds, 2)
            expected = [args.segment_seconds] * segment_count + ([tail] if tail else [])
            verify_hls(args, video, sample, source_video, audio, expected)
            print(f"PASS {sample.stat().st_size} bytes, {sorted(seen)}, {expected}s, source audio={audio}")

        invalid = Path(folder) / "invalid.mp4"
        invalid.write_bytes(b"not an MP4")
        video, _ = await_status(args.base_url, upload(args.base_url, invalid, invalid.name))
        assert video["status"] == "failed" and video["error"], video
        assert not (args.data_dir / "processing" / video["id"]).exists()
        print("PASS invalid MP4 -> failed without HLS output")

    assert request(args.base_url, "POST", "/api/uploads", b"plain")[0] == 415
    assert request(args.base_url, "POST", "/api/uploads", b"", {"Content-Type": "video/mp4"})[0] == 400
    assert request(args.base_url, "GET", f"/api/uploads/{uuid4()}")[0] == 404
    print("PASS unsupported content type (415), empty upload (400), unknown ID (404)")

    if args.upload_limit:
        headers = {"Content-Type": "video/mp4"}
        declared = {**headers, "Content-Length": str(args.upload_limit + 1)}
        assert request(args.base_url, "POST", "/api/uploads", b"", declared)[0] == 413
        oversized = (
            bytes(min(CHUNK_BYTES, args.upload_limit + 1 - offset))
            for offset in range(0, args.upload_limit + 1, CHUNK_BYTES)
        )
        assert request(
            args.base_url, "POST", "/api/uploads", oversized, headers, chunked=True
        )[0] == 413
        assert not list((args.data_dir / "uploads").glob("*.part"))
        print("PASS oversized requests with Content-Length or chunked transfer (413)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--scratch-dir", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--segment-seconds", type=float, choices=[4, 2.5], default=4)
    parser.add_argument("--upload-limit", type=int)
    run(parser.parse_args())
