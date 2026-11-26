"""Container nhị phân HLSLAB độc lập dành cho tệp lab cục bộ."""

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from struct import Struct
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from backend.binary.integrity import copy_and_hash, files_equal, sha256_file

MAGIC = b"HLSLAB\x1a\x0a"
VERSION = 1
HEADER = Struct(">8sHHQ32s")


@contextmanager
def new_output(destination: Path) -> Iterator[BinaryIO]:
    if destination.exists():
        raise FileExistsError(f"Tệp đích đã tồn tại: {destination}")
    temporary = NamedTemporaryFile(
        prefix=".hlslab-", suffix=".tmp", dir=destination.parent, mode="w+b", delete=False
    )
    path = Path(temporary.name)
    try:
        with temporary:
            yield temporary
        # Trên Windows, rename tới một tệp đã tồn tại sẽ báo lỗi.
        path.rename(destination)
    finally:
        path.unlink(missing_ok=True)


def read_header(source: BinaryIO) -> tuple[int, bytes]:
    header = source.read(HEADER.size)
    if len(header) != HEADER.size:
        raise ValueError("Header HLSLAB bị cắt.")
    magic, version, flags, length, expected = HEADER.unpack(header)
    if magic != MAGIC:
        raise ValueError("Magic HLSLAB không hợp lệ.")
    if version != VERSION or flags != 0:
        raise ValueError("Phiên bản hoặc cờ HLSLAB không được hỗ trợ.")
    if os.fstat(source.fileno()).st_size != HEADER.size + length:
        raise ValueError("Kích thước tệp sai so với độ dài payload.")
    return length, expected


def pack(source: Path, destination: Path) -> dict:
    if destination.suffix.lower() != ".hlslab":
        raise ValueError("Tệp container lab dùng phần mở rộng .hlslab.")
    with source.open("rb") as payload, new_output(destination) as container:
        container.seek(HEADER.size)
        length, digest = copy_and_hash(payload, container)
        container.seek(0)
        container.write(HEADER.pack(MAGIC, VERSION, 0, length, digest))
    return {"payload_bytes": length, "sha256": digest.hex()}


def unpack(source: Path, destination: Path) -> dict:
    with source.open("rb") as container:
        length, expected = read_header(container)
        with new_output(destination) as payload:
            _, digest = copy_and_hash(container, payload, length=length)
            if container.read(1):
                raise ValueError("Container có dữ liệu dư sau payload.")
            if digest != expected:
                raise ValueError("SHA-256 của payload không khớp với header.")
    return {"payload_bytes": length, "sha256": digest.hex()}


def verify(source: Path, restored: Path) -> dict:
    return {
        "source_sha256": sha256_file(source),
        "restored_sha256": sha256_file(restored),
        "bytes_equal": files_equal(source, restored),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("pack", "unpack", "verify"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        operation = {"pack": pack, "unpack": unpack, "verify": verify}[args.operation]
        result = operation(args.source, args.destination)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result))
    if args.operation == "verify":
        return int(not result["bytes_equal"] or result["source_sha256"] != result["restored_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
