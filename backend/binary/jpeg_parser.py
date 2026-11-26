"""Quan sát marker của JPEG Huffman tuần tự và progressive."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

SOI = b"\xff\xd8"
SOF_CODES = {0xC0, 0xC1, 0xC2}
OTHER_SOF = {0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
RESTART_CODES = range(0xD0, 0xD8)
MARKER_NAMES = {
    0xC0: "SOF0", 0xC1: "SOF1", 0xC2: "SOF2", 0xC4: "DHT",
    0xD8: "SOI", 0xD9: "EOI", 0xDA: "SOS", 0xDB: "DQT",
    0xDD: "DRI", 0xFE: "COM",
}
SCAN_TOKEN = re.compile(rb"(?<!\xff)\xff+([^\xff])")


@dataclass(frozen=True, slots=True)
class Marker:
    name: str
    offset: int
    code: str
    payload_offset: int | None = None
    payload_bytes: int = 0


@dataclass(frozen=True, slots=True)
class JPEGStructure:
    width: int
    height: int
    scan_count: int
    markers: list[Marker]
    trailing_bytes: int


def next_marker(file: BinaryIO) -> tuple[int, int]:
    if file.read(1) != b"\xff":
        raise ValueError("Cần byte FF ở đầu marker JPEG.")
    code = file.read(1)
    while code == b"\xff":
        code = file.read(1)
    if not code or code == b"\x00":
        raise ValueError("Marker JPEG không đầy đủ hoặc không hợp lệ.")
    return file.tell() - 2, code[0]


def scan_to_marker(file: BinaryIO) -> tuple[int, int]:
    pending_ff = False
    while block := file.read(64 * 1024):
        base = file.tell() - len(block)
        start = 0
        if pending_ff:
            index = len(block) - len(block.lstrip(b"\xff"))
            if index == len(block):
                continue
            code = block[index]
            if code != 0 and code not in RESTART_CODES:
                file.seek(base + index + 1)
                return base + index - 1, code
            start = index + 1

        for token in SCAN_TOKEN.finditer(block, start):
            code = token[1][0]
            if code != 0 and code not in RESTART_CODES:
                file.seek(base + token.end())
                return base + token.end() - 2, code
        pending_ff = start < len(block) and block.endswith(b"\xff")
    raise ValueError("Dữ liệu scan kết thúc trước marker tiếp theo hoặc EOI.")


def marker_details(code: int, offset: int, payload: int | None = None, size: int = 0) -> Marker:
    name = MARKER_NAMES.get(code, f"FF{code:02X}")
    if 0xE0 <= code <= 0xEF:
        name = f"APP{code - 0xE0}"
    return Marker(name, offset, f"FF {code:02X}", payload, size)


def parse_jpeg(path: Path) -> JPEGStructure:
    with path.open("rb") as file:
        size = file.seek(0, 2)
        file.seek(0)
        if file.read(2) != SOI:
            raise ValueError("Thiếu SOI FF D8 tại offset 0.")
        markers = [marker_details(0xD8, 0)]
        width = height = components = scans = 0
        read_marker = next_marker
        while True:
            offset, code = read_marker(file)
            read_marker = next_marker
            if code == 0xD9:
                if not components or not scans:
                    raise ValueError("EOI xuất hiện trước frame hoặc scan JPEG.")
                markers.append(marker_details(code, offset))
                return JPEGStructure(width, height, scans, markers, size - file.tell())
            if code < 0xC0 or code in RESTART_CODES or code == 0xD8:
                raise ValueError("Marker không hợp lệ ở vị trí hiện tại.")
            if code in OTHER_SOF:
                raise ValueError("Lab hỗ trợ các frame Huffman SOF0, SOF1 và SOF2.")

            field = file.read(2)
            segment_length = int.from_bytes(field, "big")
            payload = file.tell()
            if len(field) != 2 or segment_length < 2 or payload + segment_length - 2 > size:
                raise ValueError(f"Độ dài marker tại offset {offset} vượt biên tệp.")
            length = segment_length - 2
            if code in SOF_CODES:
                if components or length < 6:
                    raise ValueError("Frame JPEG trùng hoặc thiếu trường kích thước.")
                frame = file.read(6)
                height = int.from_bytes(frame[1:3], "big")
                width = int.from_bytes(frame[3:5], "big")
                components = frame[5]
                precisions = (8,) if code == 0xC0 else (8, 12)
                if (frame[0] not in precisions or not width or not height
                        or not components or length != 6 + 3 * components):
                    raise ValueError("Thông tin frame JPEG không hợp lệ.")
            elif code == 0xDA:
                if not components or length < 1:
                    raise ValueError("JPEG cần frame hợp lệ trước SOS.")
                scan_components = file.read(1)[0]
                if not 1 <= scan_components <= components or length != 4 + 2 * scan_components:
                    raise ValueError("Thông tin scan JPEG không hợp lệ.")
                scans += 1
                read_marker = scan_to_marker
            markers.append(marker_details(code, offset, payload, length))
            file.seek(payload + length)
