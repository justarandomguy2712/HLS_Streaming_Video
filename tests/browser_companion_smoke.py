"""Kiểm thử Edge → companion loopback → FFmpeg → S3 và danh mục mô phỏng."""

import argparse
import json
import logging
import os
import subprocess
from contextlib import ExitStack, closing, contextmanager
from decimal import Decimal
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep, time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
import uvicorn
from moto.server import ThreadedMotoServer

from backend.browser_companion import PORT, create_app
from backend.config import PROJECT_ROOT, R2Settings, load_settings
from backend.database import get_video
from tests.upload_smoke import generate_sample

LOCAL = f"http://127.0.0.1:{PORT}"
ADMIN, USER, OTHER, SERVICE = (digit * 64 for digit in "1234")


@contextmanager
def http_server(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@contextmanager
def companion_server(settings):
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=PORT,
                                          proxy_headers=False, log_level="warning"))
    thread = Thread(target=server.run)
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started:
            if not thread.is_alive() or monotonic() >= deadline:
                raise RuntimeError("Không khởi động được companion kiểm thử.")
            sleep(0.05)
        yield
    finally:
        server.should_exit = True
        thread.join()


def api_status(path="/api/companion/jobs", *, method="GET", headers=None):
    try:
        with urlopen(Request(LOCAL + path, method=method, headers=headers or {}), timeout=10) as response:
            return response.status, response.headers, json.load(response) if method != "OPTIONS" else None
    except HTTPError as error:
        with error:
            return error.code, error.headers, None


def run(root, args, r2, s3):
    published = {}
    stalled_once = set()
    worker_origin = ""

    class Website(SimpleHTTPRequestHandler):
        def __init__(self, *values):
            super().__init__(*values, directory=str(PROJECT_ROOT / "frontend"))

        def do_GET(self):
            if self.path == "/js/config.js":
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.end_headers()
                self.wfile.write(f"export const WORKER_ENDPOINT = {json.dumps(worker_origin)};".encode())
            else:
                super().do_GET()

        def log_message(self, *_):
            pass

    class Worker(BaseHTTPRequestHandler):
        def reply(self, status, data=None):
            self.send_response(status)
            origin = self.headers.get("Origin")
            if origin and origin in {website, LOCAL}:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if data is not None:
                try:
                    self.wfile.write(json.dumps(data).encode())
                except ConnectionError:
                    pass

        def do_OPTIONS(self):
            self.reply(204)

        def do_GET(self):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if self.path == "/api/auth/me":
                if token in {ADMIN, USER, OTHER}:
                    self.reply(200, {"id": token[0], "role": "USER" if token == USER else "ADMIN"})
                else:
                    self.reply(401)
            elif self.path.startswith("/api/local/videos/") and token == SERVICE:
                item = published.get(self.path.rsplit("/", 1)[-1])
                self.reply(200 if item else 404, item)
            else:
                self.reply(200, {"items": list(published.values()) if self.path.endswith("/videos") else []})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/auth/login":
                self.reply(200, {"token": ADMIN, "expires_at": int(time()) + 3600,
                                 "user": {"username": data["username"], "role": "ADMIN"}})
                return
            if self.path != "/api/local/videos" or self.headers.get("Authorization") != f"Bearer {SERVICE}":
                return self.reply(403)
            identifier = data["id"]
            # Lỗi tạm thời ở lần đầu để kiểm tra upload xong vẫn chưa READY.
            if identifier not in stalled_once:
                stalled_once.add(identifier)
                sleep(1.3)
                return self.reply(503, {"detail": "Lỗi danh mục mô phỏng"})
            assert s3.head_object(Bucket=r2.bucket_name, Key=data["playlist_key"])["ContentLength"] > 0
            published[identifier] = {**data, "status": "READY"}
            self.reply(200, published[identifier])

        def log_message(self, *_):
            pass

    with http_server(Website) as website, http_server(Worker) as worker_origin:
        source = args.source.resolve(strict=True) if args.source else root / "fixture.mp4"
        if not args.source:
            generate_sample(args.ffmpeg, source, 12, "libx264", "aac")
        settings = replace(load_settings(), r2=r2, worker_endpoint=worker_origin,
                           browser_admin_origin=website, processing_service_token=SERVICE,
                           uploads_dir=root / "uploads", processing_dir=root / "processing",
                           database_path=root / "catalog.sqlite3", disk_reserve_bytes=0,
                           ffmpeg_bin=args.ffmpeg, ffprobe_bin=args.ffprobe, keep_original=False,
                           delete_after_verification=True, stream_segments=not args.batch, processing_mode=args.mode,
                           allow_stream_copy=not args.no_copy, video_encoder="auto", enable_nvenc=True,
                           enable_hw_decode="auto", allow_cpu_fallback=True,
                           upload_chunk_bytes=1024 * 1024, progress_update_interval_ms=250,
                           segment_seconds=args.segment_seconds,
                           max_upload_bytes=source.stat().st_size + 1024)
        admin_headers = {"Authorization": f"Bearer {ADMIN}"}
        with companion_server(settings):
            assert api_status()[0] == 401
            assert api_status(headers={"Authorization": f"Bearer {USER}"})[0] == 403
            assert api_status(headers={**admin_headers, "Origin": "https://other.invalid"})[0] == 403
            assert api_status(headers={**admin_headers, "Host": "attacker.invalid"})[0] == 403
            status, headers, _ = api_status(method="OPTIONS", headers={
                "Origin": website, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-filename,x-video-title",
            })
            assert status == 200 and headers["Access-Control-Allow-Origin"] == website
            subprocess.run(["node", "tests/browser_companion_smoke.mjs", website, worker_origin, str(source)],
                           check=True, env=os.environ.copy())
            identifier, = published
            row = get_video(settings.database_path, identifier)
            assert row["online_synced"] and row["cleanup_pending"] == 0
            assert row["original_size"] == source.stat().st_size
            assert not list(settings.uploads_dir.iterdir()) and not list(settings.processing_dir.iterdir())
            assert api_status(headers={"Authorization": f"Bearer {OTHER}"})[2]["jobs"] == []
            print(f"PASS upload {row['original_size']} bytes, {row['duration']:.2f}s, "
                  f"{row['segment_count']} segments, cleanup, admin scoping")
        with companion_server(settings):
            job, = api_status(headers=admin_headers)[2]["jobs"]
            assert job["phase"] == "READY" and job["verification"] == "verified" and job["cleanup"] == "done"
            if args.encoder:
                assert job["encoder"] == args.encoder, job
            print(f"PASS trạng thái READY, xác minh và dọn tệp sau khởi động lại companion, encoder={job['encoder']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--segment-seconds", type=Decimal, default=Decimal(4))
    parser.add_argument("--mode", choices=["auto", "copy", "cpu", "gpu"], default="auto")
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--encoder")
    parser.add_argument("--scratch-dir", type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    emulator = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    emulator.start()
    try:
        host, port = emulator.get_host_and_port()
        r2 = R2Settings(f"http://{host}:{port}", "browser-test", "local-test-key", "local-test-secret")
        with ExitStack() as stack:
            s3 = stack.enter_context(closing(boto3.client(
                "s3", endpoint_url=r2.endpoint_url, region_name="us-east-1",
                aws_access_key_id=r2.access_key_id, aws_secret_access_key=r2.secret_access_key,
            )))
            directory = stack.enter_context(TemporaryDirectory(prefix="browser-", dir=args.scratch_dir))
            s3.create_bucket(Bucket=r2.bucket_name)
            run(Path(directory).resolve(), args, r2, s3)
    finally:
        emulator.stop()


if __name__ == "__main__":
    main()
