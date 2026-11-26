"""Sao chép, băm và đối chiếu byte với bộ nhớ giới hạn."""

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK_BYTES = 1024 * 1024


def copy_and_hash(
    source: BinaryIO, destination: BinaryIO, *, length: int | None = None
) -> tuple[int, bytes]:
    digest = hashlib.sha256()
    copied = 0
    while length is None or copied < length:
        count = CHUNK_BYTES if length is None else min(CHUNK_BYTES, length - copied)
        block = source.read(count)
        if not block:
            if length is not None:
                raise ValueError("Payload kết thúc trước số byte đã khai báo.")
            break
        destination.write(block)
        digest.update(block)
        copied += len(block)
    return copied, digest.digest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def files_equal(first: Path, second: Path) -> bool:
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            block = left.read(CHUNK_BYTES)
            if block != right.read(CHUNK_BYTES):
                return False
            if not block:
                return True
