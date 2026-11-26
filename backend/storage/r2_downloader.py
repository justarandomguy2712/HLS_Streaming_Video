"""Đọc object R2 theo khối và xác minh SHA-256 của nội dung."""

import hashlib
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from typing import BinaryIO

from backend.binary.integrity import CHUNK_BYTES
from backend.storage.r2_client import ObjectInfo, R2Client


class IntegrityError(ValueError):
    """Object khác dữ liệu đã ghi nhận."""


def transfer_verified(
    client: R2Client, key: str, expected: ObjectInfo,
    output: BinaryIO | None = None, stop: Event | None = None,
) -> None:
    response = client.open(key)
    with closing(response["Body"]) as body:
        if ObjectInfo.from_response(response) != expected or not expected.sha256:
            raise IntegrityError(f"Header của object {key} sai hoặc thiếu SHA-256.")
        digest = hashlib.sha256()
        total = 0
        for chunk in body.iter_chunks(chunk_size=CHUNK_BYTES):
            if stop is not None and stop.is_set():
                raise InterruptedError("Worker đang dừng.")
            digest.update(chunk)
            total += len(chunk)
            if output is not None:
                output.write(chunk)
        if total != expected.size or digest.hexdigest() != expected.sha256:
            raise IntegrityError(f"Nội dung object {key} không khớp kích thước hoặc SHA-256.")


def download_object(client: R2Client, key: str, destination: Path) -> ObjectInfo:
    if destination.exists():
        raise FileExistsError(f"Tệp đích đã tồn tại: {destination}")
    expected = client.head(key)
    if expected is None:
        raise FileNotFoundError(key)
    temporary = NamedTemporaryFile(dir=destination.parent, prefix=".r2-", suffix=".tmp", delete=False)
    path = Path(temporary.name)
    try:
        with temporary:
            transfer_verified(client, key, expected, output=temporary)
        path.rename(destination)
    finally:
        path.unlink(missing_ok=True)
    return expected
