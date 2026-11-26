"""Đối chiếu chữ ký quan sát được, cấu trúc, phần mở rộng và MIME."""

import argparse
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from backend.binary.jpeg_parser import SOI, parse_jpeg
from backend.binary.lab_container import HEADER, MAGIC, read_header

EXTENSION_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp4": "video/mp4",
    ".m4s": "video/iso.segment", ".hlslab": "application/octet-stream",
}
BMFF_FIRST_TYPES = {b"ftyp", b"styp", b"sidx", b"moof", b"free", b"skip", b"emsg"}
BOX_CONTAINERS = {"moov", "mvex", "trak", "moof", "traf"}


@dataclass(frozen=True, slots=True)
class Box:
    kind: str
    offset: int
    size: int
    header_bytes: int
    children: list["Box"]


def iter_boxes(file: BinaryIO, start: int, end: int, *, depth: int = 0) -> Iterator[Box]:
    while start < end:
        if end - start < 8:
            raise ValueError(f"Box thiếu header tại offset {start}.")
        file.seek(start)
        header = file.read(8)
        box_size = int.from_bytes(header[:4], "big")
        kind = header[4:8].decode("ascii", errors="replace")
        header_bytes = 8
        if box_size == 1:
            header_bytes += 8
            if end - start < header_bytes:
                raise ValueError(f"Box thiếu kích thước 64-bit tại offset {start}.")
            box_size = int.from_bytes(file.read(8), "big")
        elif box_size == 0:
            box_size = end - start
        if kind == "uuid":
            header_bytes += 16
        box_end = start + box_size
        if box_size < header_bytes or box_end > end:
            raise ValueError(f"Box {kind} vượt biên tại offset {start}.")

        children = []
        if depth < 2 and kind in BOX_CONTAINERS:
            children = list(iter_boxes(file, start + header_bytes, box_end, depth=depth + 1))
        yield Box(kind, start, box_size, header_bytes, children)
        start = box_end


def classify_boxes(boxes: list[Box]) -> tuple[str, str | None]:
    if boxes and boxes[0].kind == "ftyp":
        if any(
            box.kind == "moov" and any(child.kind == "mvex" for child in box.children)
            for box in boxes
        ):
            return "fmp4_init", "video/mp4"
        return "isobmff", None

    expecting_data = False
    found_fragment = False
    for box in boxes:
        if box.kind == "moof":
            if expecting_data or not any(child.kind == "traf" for child in box.children):
                return "isobmff", None
            expecting_data = True
        elif box.kind == "mdat" and expecting_data:
            expecting_data = False
            found_fragment = True
    if found_fragment and not expecting_data:
        return "fmp4_media", "video/iso.segment"
    return "isobmff", None


def inspect_file(path: Path, declared_mime: str | None = None) -> dict:
    extension = path.suffix.lower()
    with path.open("rb") as file:
        size = file.seek(0, 2)
        file.seek(0)
        prefix = file.read(16)
        result = {
            "filename": path.name,
            "bytes": size,
            "prefix_hex": prefix.hex(" ").upper(),
            "extension": extension,
            "mime_by_extension": EXTENSION_MIME.get(extension),
            "declared_mime": declared_mime,
            "signature": None,
            "structure": "unknown",
            "mime_by_structure": None,
        }
        try:
            if prefix.startswith(SOI):
                result["signature"] = "JPEG SOI @0"
                jpeg = parse_jpeg(path)
                structure = "jpeg_with_trailer" if jpeg.trailing_bytes else "jpeg"
                result.update(structure=structure, mime_by_structure="image/jpeg", jpeg=asdict(jpeg))
            elif prefix.startswith(MAGIC):
                result["signature"] = "HLSLAB magic @0"
                file.seek(0)
                length, digest = read_header(file)
                result.update(
                    structure="hlslab_header_ok", mime_by_structure="application/octet-stream",
                    payload_offset=HEADER.size, payload_bytes=length, header_sha256=digest.hex(),
                )
            elif prefix[4:8] in BMFF_FIRST_TYPES:
                result["signature"] = f"ISO BMFF {prefix[4:8].decode('ascii')} @4"
                boxes = list(iter_boxes(file, 0, size))
                structure, mime = classify_boxes(boxes)
                result.update(structure=structure, mime_by_structure=mime, boxes=[asdict(box) for box in boxes])
        except ValueError as exc:
            result.update(structure="malformed_or_unsupported", error=str(exc))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", metavar="FILE", type=Path, nargs="+")
    parser.add_argument("--declared-mime", help="Nhãn MIME cung cấp để đối chiếu trong lab.")
    args = parser.parse_args()
    try:
        results = [inspect_file(path, args.declared_mime) for path in args.files]
    except OSError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(results, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
