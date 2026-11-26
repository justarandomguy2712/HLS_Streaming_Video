"""Khởi tạo và kiểm tra cơ sở dữ liệu danh mục video."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY NOT NULL,
                original_filename TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                status TEXT NOT NULL DEFAULT 'uploaded'
                    CHECK (status IN ('uploaded', 'processing', 'ready', 'failed')),
                playlist_key TEXT,
                codecs TEXT,
                CHECK (status <> 'ready' OR (playlist_key IS NOT NULL AND codecs IS NOT NULL))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS companion_sources (
                video_id TEXT PRIMARY KEY, source_path TEXT NOT NULL,
                archive_requested INTEGER NOT NULL, recycle_requested INTEGER NOT NULL, source_identity TEXT
            )
            """
        )
        companion_fields = {column[1] for column in connection.execute("PRAGMA table_info(companion_sources)")}
        if "source_identity" not in companion_fields:
            connection.execute("ALTER TABLE companion_sources ADD COLUMN source_identity TEXT")
        columns = connection.execute("PRAGMA table_info(videos)")
        existing = {column[1] for column in columns}
        for name, definition in {
            "title": "TEXT",
            "original_size": "INTEGER",
            "duration": "REAL",
            "segment_count": "INTEGER",
            "r2_prefix": "TEXT",
            "processed_size": "INTEGER",
            "init_key": "TEXT",
            "error": "TEXT",
            "processing_note": "TEXT",
            "source_sha256": "TEXT",
            "storage_bucket": "TEXT",
            "storage_endpoint": "TEXT",
            "cleanup_pending": "INTEGER NOT NULL DEFAULT 0",
            "online_synced": "INTEGER NOT NULL DEFAULT 0",
            "online_retry_after": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
        connection.execute(
            """
            UPDATE videos SET title = CASE
                WHEN lower(substr(original_filename, -4)) = '.mp4'
                    AND length(trim(substr(original_filename, 1, length(original_filename) - 4))) > 0
                THEN trim(substr(original_filename, 1, length(original_filename) - 4))
                ELSE original_filename END
            WHERE title IS NULL
            """
        )
        connection.execute(
            """
            UPDATE videos SET r2_prefix = substr(playlist_key, 1, length(playlist_key) - length('playlist.m3u8'))
            WHERE status = 'ready' AND storage_bucket IS NOT NULL AND r2_prefix IS NULL
                AND playlist_key = 'videos/' || id || '/playlist.m3u8'
            """
        )
        connection.execute(
            "UPDATE videos SET init_key = r2_prefix || 'init.mp4' "
            "WHERE status = 'ready' AND r2_prefix IS NOT NULL AND init_key IS NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS videos_status_created ON videos(status, created_at, id)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS videos_created ON videos(created_at DESC, id DESC)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS videos_online_todo ON videos(storage_bucket, storage_endpoint, "
            "online_retry_after, created_at, id) WHERE status = 'ready' AND online_synced = 0"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS videos_pending_cleanup ON videos(storage_bucket, storage_endpoint, id) "
            "WHERE status = 'ready' AND cleanup_pending <> 0"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS videos_incomplete_metadata ON videos(id) WHERE status = 'ready' "
            "AND (original_size IS NULL OR duration IS NULL OR segment_count IS NULL OR processed_size IS NULL)"
        )


def check_database(path: Path) -> None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("SELECT id FROM videos LIMIT 1").fetchone()


def register_upload(
    path: Path, video_id: str, filename: str,
    storage_bucket: str | None = None, storage_endpoint: str | None = None,
    *, original_size: int | None = None, source_sha256: str | None = None,
    companion_source: tuple[str, bool, bool, str] | None = None,
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO videos (
                id, title, original_filename, original_size, storage_bucket, storage_endpoint, source_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, Path(filename).stem.strip() or filename, filename, original_size,
             storage_bucket, storage_endpoint, source_sha256),
        )
        if companion_source is not None:
            connection.execute(
                "INSERT INTO companion_sources VALUES(?, ?, ?, ?, ?)", (video_id, *companion_source),
            )


def get_video(path: Path, video_id: str) -> dict | None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return dict(row) if row is not None else None


def list_videos(
    path: Path, limit: int, offset: int = 0, *, before: tuple[str, str] | None = None,
) -> list[dict]:
    columns = (
        "id, title, original_filename, original_size, duration, segment_count, processed_size, "
        "init_key, playlist_key, r2_prefix, status, created_at, storage_bucket, storage_endpoint, codecs"
    )
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if before is not None:
            rows = connection.execute(
                f"SELECT {columns} FROM videos WHERE (created_at, id) < (?, ?) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*before, limit),
            )
        else:
            rows = connection.execute(
                f"SELECT {columns} FROM videos ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [dict(row) for row in rows]


def incomplete_ready_videos(path: Path) -> Iterator[dict]:
    after = ""
    while True:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, storage_bucket, storage_endpoint, r2_prefix FROM videos
                WHERE id > ? AND status = 'ready'
                    AND (original_size IS NULL OR duration IS NULL OR segment_count IS NULL OR processed_size IS NULL)
                ORDER BY id LIMIT 50
                """, (after,),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield dict(row)
        after = rows[-1]["id"]


def backfill_metadata(
    path: Path, video_id: str, *, original_size: int, duration: float, segment_count: int,
    processed_size: int | None = None,
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            UPDATE videos SET original_size = COALESCE(original_size, ?),
                duration = COALESCE(duration, ?), segment_count = COALESCE(segment_count, ?),
                processed_size = COALESCE(processed_size, ?)
            WHERE id = ? AND status = 'ready'
            """,
            (original_size, duration, segment_count, processed_size, video_id),
        )


def recover_processing(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE videos SET status = 'uploaded' WHERE status = 'processing'")


def claim_upload(
    path: Path, bucket: str | None = None, endpoint: str | None = None,
) -> tuple[str, bool] | None:
    with closing(sqlite3.connect(path)) as connection, connection:
        row = connection.execute(
            """
            UPDATE videos SET status = 'processing', error = NULL
            WHERE id = (
                SELECT id FROM videos WHERE status = 'uploaded'
                    AND (storage_bucket IS NULL OR (storage_bucket = ? AND storage_endpoint = ?))
                ORDER BY created_at, id LIMIT 1
            )
            RETURNING id, storage_bucket
            """,
            (bucket, endpoint),
        ).fetchone()
        return (row[0], row[1] is not None) if row is not None else None


def set_processing_note(path: Path, video_id: str, note: str) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE videos SET processing_note = ? WHERE id = ? AND status = 'processing'",
            (note, video_id),
        )


def finish_upload(
    path: Path,
    video_id: str,
    *,
    playlist_key: str | None = None,
    codecs: str | None = None,
    error: str | None = None,
    storage_bucket: str | None = None,
    storage_endpoint: str | None = None,
    cleanup_pending: int = 0,
    original_size: int | None = None,
    duration: float | None = None,
    segment_count: int | None = None,
    r2_prefix: str | None = None,
    processed_size: int | None = None,
    init_key: str | None = None,
) -> bool:
    with closing(sqlite3.connect(path)) as connection, connection:
        result = connection.execute(
            """
            UPDATE videos SET status = ?, playlist_key = ?, codecs = ?, error = ?,
                storage_bucket = COALESCE(?, storage_bucket),
                storage_endpoint = COALESCE(?, storage_endpoint), cleanup_pending = ?,
                original_size = COALESCE(original_size, ?), duration = COALESCE(?, duration),
                segment_count = COALESCE(?, segment_count), r2_prefix = COALESCE(?, r2_prefix),
                processed_size = COALESCE(?, processed_size), init_key = COALESCE(?, init_key)
            WHERE id = ? AND status = 'processing'
            """,
            (
                "failed" if error else "ready", playlist_key, codecs, error,
                storage_bucket, storage_endpoint, cleanup_pending,
                original_size, duration, segment_count, r2_prefix, processed_size, init_key, video_id,
            ),
        )
        return result.rowcount == 1


def queue_publication(path: Path, video_id: str, bucket: str, endpoint: str) -> bool:
    with closing(sqlite3.connect(path)) as connection, connection:
        result = connection.execute(
            """
            UPDATE videos SET status = 'uploaded', error = NULL, storage_bucket = ?, storage_endpoint = ?
            WHERE id = ? AND status IN ('ready', 'failed') AND (
                storage_bucket IS NULL OR
                (status = 'failed' AND storage_bucket = ? AND storage_endpoint = ?)
            )
            """,
            (bucket, endpoint, video_id, bucket, endpoint),
        )
        return result.rowcount == 1


def pending_cleanup(path: Path, bucket: str, endpoint: str) -> Iterator[tuple[str, int]]:
    after = ""
    while True:
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute(
                """
                SELECT id, cleanup_pending FROM videos WHERE id > ? AND status = 'ready' AND cleanup_pending <> 0
                    AND storage_bucket = ? AND storage_endpoint = ?
                    AND original_size IS NOT NULL AND duration IS NOT NULL AND segment_count IS NOT NULL
                ORDER BY id LIMIT 50
                """,
                (after, bucket, endpoint),
            ).fetchall()
        if not rows:
            return
        yield from rows
        after = rows[-1][0]


def finish_cleanup(path: Path, video_id: str) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE videos SET cleanup_pending = 0 WHERE id = ?", (video_id,))


def retry_upload(path: Path, video_id: str, bucket: str, endpoint: str) -> bool:
    with closing(sqlite3.connect(path)) as connection, connection:
        result = connection.execute(
            "UPDATE videos SET status = 'uploaded', error = NULL "
            "WHERE id = ? AND status = 'failed' AND storage_bucket = ? AND storage_endpoint = ?",
            (video_id, bucket, endpoint),
        )
        return result.rowcount == 1


def next_online_video(path: Path, bucket: str, endpoint: str) -> dict | None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, title, original_filename, playlist_key, codecs, duration, segment_count, created_at
            FROM videos WHERE status = 'ready' AND online_synced = 0 AND storage_bucket = ?
                AND storage_endpoint = ? AND online_retry_after <= unixepoch()
            ORDER BY online_retry_after, created_at, id LIMIT 1
            """,
            (bucket, endpoint),
        ).fetchone()
        return dict(row) if row else None


def finish_online_sync(path: Path, video_id: str, playlist_key: str, *, published: bool) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            UPDATE videos SET online_synced = ?, online_retry_after = unixepoch() + ?
            WHERE id = ? AND status = 'ready' AND playlist_key = ?
            """,
            (int(published), 0 if published else 30, video_id, playlist_key),
        )


def get_companion_source(path: Path, video_id: str) -> dict | None:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute("SELECT * FROM companion_sources WHERE video_id=?", (video_id,)).fetchone()
        return dict(source) if source else None


def claim_companion_video(path: Path, video_id: str, bucket: str, endpoint: str) -> bool:
    with closing(sqlite3.connect(path)) as connection, connection:
        result = connection.execute(
            "UPDATE videos SET status='processing',error=NULL WHERE id=? AND status='uploaded' "
            "AND storage_bucket=? AND storage_endpoint=?", (video_id, bucket, endpoint),
        )
        return result.rowcount == 1


def ready_r2_ids(path: Path, bucket: str, endpoint: str) -> Iterator[str]:
    after = ""
    while True:
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute(
                "SELECT id FROM videos WHERE status='ready' AND storage_bucket=? AND storage_endpoint=? "
                "AND id>? ORDER BY id LIMIT 50", (bucket, endpoint, after),
            ).fetchall()
        if not rows:
            return
        for (identifier,) in rows:
            yield identifier
        after = rows[-1][0]
