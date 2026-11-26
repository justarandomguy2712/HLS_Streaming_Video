"""So sánh pipeline HLS với cùng nguồn MP4, không truy cập cloud."""

import argparse
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter

from backend.config import load_settings
from backend.processing.ffmpeg import QualityError, segment_to_hls, select_plan
from backend.processing.ffprobe import probe_input
from backend.processing.system_metrics import SystemMetrics


def measure(settings, source, media, directory):
    stop = Event()
    try:
        plan = select_plan(settings, source, media, stop)
    except QualityError as exc:
        return {"mode": settings.processing_mode, "skipped": str(exc)}
    monitor = SystemMetrics(True, 0.5)
    samples = []

    def sample():
        while not stop.is_set():
            samples.append(monitor.snapshot())
            stop.wait(0.5)

    reporter = Thread(target=sample)
    reporter.start()
    start = perf_counter()
    try:
        segment_to_hls(settings, source, directory, media, stop, plan=plan)
        elapsed = perf_counter() - start
    finally:
        stop.set()
        reporter.join()
    # Playlist cục bộ có timeline và frame rate đầy đủ.
    probe = subprocess.run([
        settings.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json",
        str(directory / "playlist.m3u8"),
    ], check=True, capture_output=True, text=True)
    output = json.loads(probe.stdout)["streams"][0]
    compare = subprocess.run([
        settings.ffmpeg_bin, "-hide_banner", "-nostdin", "-nostats",
        "-i", str(source), "-i", str(directory / "playlist.m3u8"),
        "-lavfi", "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]ssim=shortest=1",
        "-an", "-f", "null", "-",
    ], check=True, capture_output=True, text=True)
    ssim = re.search(r"\bAll:([\d.]+)", compare.stderr)
    result = {
        "mode": settings.processing_mode, "encoder": plan.video, "cuda_decode": plan.hardware_decode,
        "seconds": round(elapsed, 3), "media_speed_x": round(media.duration_seconds / elapsed, 2),
        "hls_bytes": sum(path.stat().st_size for path in directory.iterdir()),
        "resolution": f"{output['width']}x{output['height']}", "fps": output["avg_frame_rate"],
        "samples": len(samples), "ssim": float(ssim[1]) if ssim else None,
    }
    for key in ("cpu_percent", "encode_percent", "decode_percent", "ram_used_bytes", "vram_used_mib"):
        values = [sample[key] for sample in samples if key in sample]
        if values:
            result[key] = {"mean": round(fmean(values), 2), "peak": round(max(values), 2)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve(strict=True)
    base = replace(load_settings(), ffmpeg_bin=args.ffmpeg, ffprobe_bin=args.ffprobe,
                   allow_cpu_fallback=False, video_encoder="auto", enable_nvenc=True, enable_hw_decode="auto")
    media = probe_input(base.ffprobe_bin, source)
    for mode in ("copy", "cpu", "gpu"):
        with TemporaryDirectory(prefix=f"{mode}-", dir=args.scratch_dir) as directory:
            settings = replace(base, processing_mode=mode, allow_stream_copy=mode == "copy")
            result = measure(settings, source, media, Path(directory).resolve())
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
