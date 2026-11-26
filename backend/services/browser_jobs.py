"""Hàng đợi upload từ trình duyệt, chạy bằng pipeline companion có sẵn."""

import json
import logging
import sqlite3
from contextlib import closing
from threading import Lock, Thread

from backend.local_processing import run_video
from backend.services.job_progress import JobProgress

logger = logging.getLogger(__name__)


class BrowserJobs:
    def __init__(self, service, client):
        self.service = service
        self.client = client
        self.path = service.settings.database_path
        self.active = None
        self.lock = Lock()
        self.worker = Thread(target=self.run, name="browser-companion")

    def start(self):
        self.worker.start()

    def close(self):
        self.service.stop.set()
        self.service.wakeup.set()
        self.worker.join()

    def run(self):
        while not self.service.stop.is_set():
            try:
                with closing(sqlite3.connect(self.path)) as db:
                    storage = self.service.storage.config
                    row = db.execute(
                        "SELECT video_id, snapshot FROM browser_jobs JOIN videos ON id=video_id "
                        "WHERE state='queued' AND storage_bucket=? AND storage_endpoint=? "
                        "ORDER BY created_at, video_id LIMIT 1",
                        (storage.bucket_name, storage.endpoint_url),
                    ).fetchone()
                if row:
                    self.process(row[0], json.loads(row[1]) if row[1] else None)
                    continue
            except sqlite3.Error:
                logger.exception("Không thể truy cập hàng đợi trình duyệt.")
            self.service.wakeup.wait(0.5)
            self.service.wakeup.clear()

    def process(self, video_id, previous):
        progress = JobProgress(self.service.settings.processing_mode, previous)
        with self.lock:
            self.active = video_id, progress
        try:
            try:
                run_video(self.service, self.client, video_id, progress)
                state = "ready"
            except Exception:
                if self.service.stop.is_set():
                    return
                logger.exception("Tác vụ trình duyệt chưa hoàn tất cho %s.", video_id)
                state = "failed"
                previous = progress.snapshot()
                progress.update(
                    phase="FAILED",
                    verification="verified" if previous["verification"] == "verified" else "incomplete",
                    error="Tác vụ chưa hoàn tất. Xem log companion và thử lại.",
                )
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("UPDATE browser_jobs SET state=?, snapshot=? WHERE video_id=?",
                           (state, json.dumps(progress.snapshot()), video_id))
        finally:
            with self.lock:
                self.active = None

    def list(self, owner):
        storage = self.service.storage.config
        with self.lock:
            active = self.active
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, title, created_at, original_size, state, snapshot, error "
                "FROM browser_jobs JOIN videos ON id=video_id "
                "WHERE owner=? AND storage_bucket=? AND storage_endpoint=? "
                "ORDER BY (id=?) DESC, (state='queued') DESC, created_at DESC, id DESC LIMIT 30",
                (owner, storage.bucket_name, storage.endpoint_url, active[0] if active else ""),
            ).fetchall()
        result = []
        for row in rows:
            progress = {"phase": "QUEUED"}
            if active and active[0] == row["id"]:
                progress = active[1].snapshot()
            elif row["snapshot"] and row["state"] != "queued":
                progress = json.loads(row["snapshot"])
            result.append({
                "id": row["id"], "title": row["title"], "created_at": row["created_at"],
                "original_size": row["original_size"], **progress,
                "error": row["error"] or progress.get("error"),
            })
        return result

    def retry(self, owner, video_id):
        storage = self.service.storage.config
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute(
                "UPDATE browser_jobs SET state='queued' WHERE owner=? AND video_id=? "
                "AND EXISTS(SELECT 1 FROM videos WHERE id=video_id AND storage_bucket=? AND storage_endpoint=? "
                "AND (browser_jobs.state='failed' OR (browser_jobs.state='ready' AND cleanup_pending<>0)))",
                (owner, video_id, storage.bucket_name, storage.endpoint_url),
            )
        if result.rowcount:
            self.service.wakeup.set()
        return bool(result.rowcount)
