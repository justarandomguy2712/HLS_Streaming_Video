"""Đếm tiến độ HLS theo từng lần xử lý, an toàn khi upload song song."""

from math import isfinite
from threading import Lock
from time import monotonic


class JobProgress:
    def __init__(self, mode: str, previous=None):
        self.lock = Lock()
        self.started = monotonic()
        self.data = {
            "phase": "ANALYZING", "mode": mode, "media_seconds": 0,
            "segments_created": 0, "segments_uploaded": 0, "verified_bytes": 0,
            "ffmpeg_done": False, "verification": "pending", "cleanup": "pending",
        }
        self.active = {}
        self.upload_started = None
        self.upload_finished = None
        self.new_bytes = 0
        if previous and previous.get("verification") == "verified":
            self.started -= previous["elapsed_seconds"]
            self.data.update({key: previous.get(key) for key in ("encoder", "hardware_decode", "bytes_per_second")})

    def update(self, **fields):
        with self.lock:
            self.data.update(fields)

    def begin(self, plan, duration):
        with self.lock:
            self.active.clear()
            self.upload_started = self.upload_finished = None
            self.new_bytes = 0
            self.data.update(
                phase="PROCESSING", encoder=plan.video, hardware_decode=plan.hardware_decode,
                note=plan.note, duration=duration, media_seconds=0, speed=None,
                segments_created=0, segments_uploaded=0, verified_bytes=0,
                total_bytes=None, ffmpeg_done=False, verification="pending",
            )

    def ffmpeg(self, seconds, speed):
        with self.lock:
            self.data["media_seconds"] = max(self.data["media_seconds"], seconds)
            self.data["speed"] = speed if speed is not None and isfinite(speed) and speed > 0 else None

    def upload(self, name, event, value):
        with self.lock:
            sent, pending, verifying = self.active.get(name, (0, 0, False))
            if event == "bytes":
                if self.upload_started is None:
                    self.upload_started = monotonic()
                sent = max(0, sent + value)
                self.active[name] = sent, sent, False
            elif event == "verifying":
                self.active[name] = sent, value, True
            elif event == "verified":
                self.active.pop(name, None)
                self.new_bytes += sent
                self.data["verified_bytes"] += value
                self.data["segments_uploaded"] += int(name.endswith(".m4s"))
            else:
                self.active.pop(name, None)

    def media_ready(self, result):
        with self.lock:
            self.upload_finished = monotonic()
            self.active.clear()
            self.data.update(
                phase="PUBLISHING", ffmpeg_done=True, verification="verified",
                duration=result["duration"],
                total_bytes=result["processed_size"], verified_bytes=result["processed_size"],
                segments_created=result["segment_count"], segments_uploaded=result["segment_count"],
            )

    def snapshot(self):
        with self.lock:
            now = monotonic()
            transferred = self.data["verified_bytes"] + sum(item[1] for item in self.active.values())
            uploaded = self.data["verified_bytes"] + sum(item[1] for item in self.active.values() if item[2])
            rate = self.data.get("bytes_per_second")
            if self.upload_started is not None:
                elapsed = max(0.001, (self.upload_finished or now) - self.upload_started)
                rate = (self.new_bytes + sum(item[0] for item in self.active.values())) / elapsed
            data = dict(self.data)
            if data["verification"] == "pending" and any(item[2] for item in self.active.values()):
                data["verification"] = "running"
            if data["phase"] == "UPLOADING" and self.active and all(item[2] for item in self.active.values()):
                data["phase"] = "VERIFYING"
            return dict(data, elapsed_seconds=now - self.started, transfer_bytes=transferred,
                        uploaded_bytes=uploaded, bytes_per_second=rate)
