"""Tạo mẫu lab, phân tích HLS Phase 02 và xác minh đóng gói/khôi phục."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.binary.integrity import CHUNK_BYTES, files_equal, sha256_file
from backend.binary.lab_container import HEADER, pack, unpack, verify
from backend.binary.signature_detector import inspect_file


def make_jpeg_cases(directory: Path, ffmpeg: str, sample_segment: Path) -> list[dict]:
    valid = directory / "valid.jpg"
    subprocess.run(
        [ffmpeg, "-nostdin", "-n", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=s=96x64", "-frames:v", "1", "-c:v", "mjpeg",
         "-q:v", "3", "-threads", "1", str(valid)],
        check=True,
    )
    subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(valid), "-f", "null", "-"],
        check=True,
    )
    tail = directory / "trailing-text.jpg"
    shutil.copyfile(valid, tail)
    with tail.open("ab") as file:
        file.write(b"LAB_SAMPLE\n")
    truncated = directory / "missing-eoi.jpg"
    shutil.copyfile(valid, truncated)
    with truncated.open("r+b") as file:
        file.seek(-2, 2)
        assert file.read(2) == b"\xff\xd9"
        file.truncate(file.tell() - 2)
    (directory / "soi-only.jpg").write_bytes(b"\xff\xd8")
    renamed = directory / "segment-renamed.jpg"
    shutil.copyfile(sample_segment, renamed)

    cases = [
        (valid, "jpeg"), (tail, "jpeg_with_trailer"), (truncated, "malformed_or_unsupported"),
        (directory / "soi-only.jpg", "malformed_or_unsupported"), (renamed, "fmp4_media"),
    ]
    results = []
    for sample, expected in cases:
        result = inspect_file(sample, "image/jpeg")
        assert result["structure"] == expected, result
        results.append(result)
        print(f"{sample.name}: {result['signature']} -> {expected}")
    assert results[0]["jpeg"]["width"] == 96 and results[0]["jpeg"]["height"] == 64
    assert results[1]["jpeg"]["trailing_bytes"] == len(b"LAB_SAMPLE\n")
    assert files_equal(sample_segment, renamed)
    return results


def roundtrip(source: Path, output_dir: Path, expected_hash: str | None = None) -> dict:
    container = output_dir / f"{source.name}.hlslab"
    restored = output_dir / f"restored-{source.name}"
    packed = pack(source, container)
    inspection = inspect_file(container, "application/octet-stream")
    assert inspection["structure"] == "hlslab_header_ok"
    assert inspection["payload_offset"] == HEADER.size
    recovered = unpack(container, restored)
    comparison = verify(source, restored)
    assert packed == recovered
    assert comparison["bytes_equal"]
    assert packed["sha256"] == comparison["source_sha256"] == comparison["restored_sha256"]
    if expected_hash is not None:
        assert packed["sha256"] == expected_hash
    return {
        "source": source.name, "container": container.name, "restored": restored.name,
        "payload_bytes": packed["payload_bytes"], **comparison,
    }


def check_rejected_containers(directory: Path, container: Path) -> list[str]:
    checks = {
        "bad_magic": (0, b"!"),
        "unsupported_version": (9, b"\x02"),
        "nonzero_flags": (11, b"\x01"),
        "payload_tampered": (HEADER.size, b"\xff"),
        "trailing_byte": (container.stat().st_size, b"!"),
        "truncated_payload": (container.stat().st_size - 1, None),
    }
    restored = directory / "rejected.m4s"
    for name, (offset, replacement) in checks.items():
        sample = directory / f"{name}.hlslab"
        shutil.copyfile(container, sample)
        with sample.open("r+b") as file:
            file.seek(offset)
            if replacement is None:
                file.truncate()
            else:
                if offset == HEADER.size:
                    replacement = bytes([file.read(1)[0] ^ 0xFF])
                    file.seek(offset)
                file.write(replacement)
        try:
            unpack(sample, restored)
        except ValueError:
            assert not restored.exists()
            assert not list(directory.glob(".hlslab-*.tmp"))
        else:
            raise AssertionError(f"Container cần bị từ chối: {name}")
    return list(checks)


def run(hls_dir: Path, output_dir: Path, ffmpeg: str) -> None:
    hls_metadata = json.loads((hls_dir / "metadata.json").read_text(encoding="utf-8"))
    items = [hls_metadata["init_segment"], *hls_metadata["segments"]]
    output_dir.mkdir(parents=True, exist_ok=False)
    cases = make_jpeg_cases(output_dir, ffmpeg, hls_dir / items[1]["filename"])

    inspections = []
    comparisons = []
    for index, item in enumerate(items):
        source = hls_dir / item["filename"]
        expected_type = "fmp4_init" if index == 0 else "fmp4_media"
        expected_mime = "video/mp4" if index == 0 else "video/iso.segment"
        inspection = inspect_file(source, expected_mime)
        assert inspection["structure"] == expected_type, inspection
        assert inspection["bytes"] == item["size_bytes"]
        inspections.append(inspection)
        comparison = roundtrip(source, output_dir, item["sha256"])
        comparisons.append(comparison)
        print(f"{source.name}: {comparison['payload_bytes']} bytes, "
              f"SHA-256 {comparison['source_sha256']}, bytes_equal=True")

    with TemporaryDirectory(dir=output_dir) as temporary:
        scratch = Path(temporary)
        rejected = check_rejected_containers(scratch, output_dir / comparisons[1]["container"])
        large = scratch / "chunked.bin"
        with large.open("wb") as file:
            for fill in (0x00, 0x80, 0xFF):
                file.write(bytes([fill]) * CHUNK_BYTES)
            file.write(b"seventeen-bytes!!")
        large_result = roundtrip(large, scratch)
        empty = scratch / "empty.bin"
        empty.touch()
        empty_result = roundtrip(empty, scratch)
        existing = scratch / "restored-chunked.bin"
        try:
            unpack(scratch / "chunked.bin.hlslab", existing)
        except FileExistsError:
            assert sha256_file(existing) == large_result["source_sha256"]
        else:
            raise AssertionError("Unpack cần giữ tệp đích đã tồn tại.")

    result = {
        "hls_video_id": hls_metadata["video_id"], "jpeg_cases": cases,
        "hls_structure": inspections, "hls_roundtrips": comparisons,
        "rejected_container_cases": rejected,
        "additional_roundtrips": [large_result, empty_result],
        "existing_destination_preserved": True,
    }
    (output_dir / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"PASS: {len(rejected)} invalid containers, {large_result['payload_bytes']} byte streaming case, "
          "empty payload and existing output")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hls-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    run(args.hls_dir, args.output_dir, args.ffmpeg)
