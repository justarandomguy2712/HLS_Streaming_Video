"""Upload object HLS bất biến bằng Boto3 và đọc lại để xác minh."""

from pathlib import Path
from threading import Event

from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import (ClientError, ConnectionError, HTTPClientError,
                                 IncompleteReadError, ReadTimeoutError, ResponseStreamingError)

from backend.binary.integrity import sha256_file
from backend.storage.r2_client import ObjectInfo, R2Client
from backend.storage.r2_downloader import IntegrityError, transfer_verified

MEDIA_HEADERS = {
    ".m3u8": ("application/vnd.apple.mpegurl", "public, max-age=60"),
    ".mp4": ("video/mp4", "public, max-age=31536000, immutable"),
    ".m4s": ("video/iso.segment", "public, max-age=31536000, immutable"),
    ".json": ("application/json", "public, max-age=60"),
}
TRANSFER_CONFIG = TransferConfig(use_threads=False, preferred_transfer_client="classic")


def is_retryable(error: Exception) -> bool:
    if isinstance(error, S3UploadFailedError):
        cause = error.__cause__ or error.__context__
        return isinstance(cause, Exception) and is_retryable(cause)
    if isinstance(error, ClientError):
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status in {408, 429} or status >= 500 or error.response["Error"].get("Code") in {
            "SlowDown", "Throttling", "RequestTimeout",
        }
    return isinstance(error, (ConnectionError, HTTPClientError, IncompleteReadError,
                              ReadTimeoutError, ResponseStreamingError))


def upload_verified(
    client: R2Client, key: str, path: Path, stop: Event,
    *, source_size: int | None = None, source_hash: str | None = None, progress=None,
) -> ObjectInfo:
    content_type, cache_control = MEDIA_HEADERS[path.suffix]
    expected = ObjectInfo(path.stat().st_size, sha256_file(path), content_type, cache_control)
    if source_size is not None and (expected.size != source_size or expected.sha256 != source_hash):
        raise IntegrityError(f"Tệp HLS cục bộ {path.name} khác metadata đã tạo.")

    for attempt in range(client.upload_max_retries + 1):
        if stop.is_set():
            raise InterruptedError("Worker đang dừng.")
        try:
            remote = client.head(key)
            if remote is None:
                client.s3.upload_file(
                    str(path), client.bucket, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "CacheControl": cache_control,
                        "Metadata": {"sha256": expected.sha256},
                    },
                    Config=TRANSFER_CONFIG,
                    **({"Callback": lambda size: progress(path.name, "bytes", size)} if progress else {}),
                )
                remote = client.head(key)
            if remote != expected:
                raise IntegrityError(f"Object {key} thiếu hoặc sai kích thước, Content-Type, cache hoặc SHA-256.")
            if progress:
                progress(path.name, "verifying", expected.size)
            transfer_verified(client, key, expected, stop=stop)
            if progress:
                progress(path.name, "verified", expected.size)
            return expected
        except Exception as exc:
            if progress:
                progress(path.name, "retry", 0)
            if attempt == client.upload_max_retries or not is_retryable(exc):
                raise
            if stop.wait(0.5 * 2**min(attempt, 6)):
                raise InterruptedError("Worker đang dừng.") from exc
