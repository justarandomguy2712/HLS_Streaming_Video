"""Cập nhật danh mục R2 bằng ghi có điều kiện và các trang bất biến."""

import hashlib
import json
import re
from contextlib import closing
from threading import Event
from uuid import UUID

from botocore.exceptions import ClientError

from backend.storage.r2_client import R2Client, is_missing_object
from backend.storage.r2_uploader import is_retryable

ROOT_KEY = "library.json"
PAGE_SIZE = 50
PAGE_ID = re.compile(r"[0-9a-f]{64}")
ATTEMPT_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
CODECS = re.compile(r"avc1\.[0-9a-f]{6}(?:,mp4a\.40\.2)?", re.I)


def encode(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


def record_from_row(row: dict) -> dict:
    identifier = str(UUID(row["id"]))
    key = row["playlist_key"]
    if (not key or not re.fullmatch(rf"videos/{identifier}/(?:{ATTEMPT_ID}/)?playlist\.m3u8", key)
            or not CODECS.fullmatch(row["codecs"] or "")):
        raise ValueError("Bản ghi HLS đã xuất bản không hợp lệ cho thư viện online.")
    return {
        "id": identifier,
        "title": row["title"] or row["original_filename"],
        "created_at": row["created_at"],
        "duration": row["duration"],
        "segment_count": row["segment_count"],
        "codecs": row["codecs"],
        "playlist_key": key,
    }


def conflicting(error: Exception) -> bool:
    return isinstance(error, ClientError) and error.response["ResponseMetadata"].get("HTTPStatusCode") in {409, 412}


class OnlineLibrary:
    def __init__(self, client: R2Client) -> None:
        self.client = client

    def read(self, key: str) -> tuple[dict | None, str | None]:
        try:
            response = self.client.open(key)
        except ClientError as exc:
            if is_missing_object(exc):
                return None, None
            raise
        with closing(response["Body"]) as body:
            return json.load(body), response["ETag"]

    def put(self, key: str, body: bytes, etag: str | None = None) -> None:
        condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        self.client.s3.put_object(
            Bucket=self.client.bucket, Key=key, Body=body, ContentType="application/json",
            CacheControl="private, no-store", **condition,
        )

    def immutable_put(self, key: str, data: dict) -> bool:
        try:
            self.put(key, encode(data))
            return True
        except ClientError as exc:
            if exc.response["ResponseMetadata"].get("HTTPStatusCode") != 412:
                raise
            existing, _ = self.read(key)
            if existing != data:
                raise ValueError("Khóa metadata bất biến trên R2 có nội dung khác.") from exc
            return False

    def read_page(self, cursor: str) -> dict:
        if not PAGE_ID.fullmatch(cursor):
            raise ValueError("Cursor thư viện R2 không hợp lệ.")
        page, _ = self.read(f"catalog/pages/{cursor}.json")
        if page is None or hashlib.sha256(encode(page)).hexdigest() != cursor:
            raise ValueError("Trang thư viện R2 thiếu hoặc không khớp checksum.")
        return page

    def contains(self, page: dict, record: dict, stop: Event) -> bool:
        while True:
            if stop.is_set():
                raise InterruptedError
            if any(item["id"] == record["id"] for item in page["items"]):
                return True
            if page["next"] is None:
                return False
            page = self.read_page(page["next"])

    def publish(self, row: dict, stop: Event) -> None:
        record = record_from_row(row)
        for attempt in range(self.client.upload_max_retries + 1):
            if stop.is_set():
                raise InterruptedError
            try:
                created = self.immutable_put(f"catalog/videos/{record['id']}.json", record)
                page, etag = self.read(ROOT_KEY)
                if page is None:
                    page = {"version": 1, "items": [], "next": None}
                if (page["version"] != 1 or not isinstance(page["items"], list)
                        or len(page["items"]) > PAGE_SIZE):
                    raise ValueError("Cấu trúc library.json không được hỗ trợ.")
                # Chỉ cần đọc các trang cũ khi thử lại cùng UUID hoặc có tranh chấp CAS.
                if not created and self.contains(page, record, stop):
                    return
                if len(page["items"]) == PAGE_SIZE:
                    cursor = hashlib.sha256(encode(page)).hexdigest()
                    self.immutable_put(f"catalog/pages/{cursor}.json", page)
                    page = {"version": 1, "items": [], "next": cursor}
                page["items"].insert(0, record)
                self.put(ROOT_KEY, encode(page), etag)
                return
            except Exception as exc:
                if attempt == self.client.upload_max_retries or not (conflicting(exc) or is_retryable(exc)):
                    raise
                if stop.wait(min(0.5 * 2**attempt, 10)):
                    raise InterruptedError from exc
