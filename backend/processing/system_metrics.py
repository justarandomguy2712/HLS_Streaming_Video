"""Đọc số liệu hệ thống Windows và GPU NVIDIA nếu thiết bị hỗ trợ."""

import ctypes
import csv
import shutil
import subprocess
from ctypes import wintypes
from threading import Lock
from time import monotonic


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in (
            "total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended",
        )
    ]


def windows_times():
    times = [wintypes.FILETIME() for _ in range(3)]
    if ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(value) for value in times)):
        return tuple((value.dwHighDateTime << 32) | value.dwLowDateTime for value in times)
    return None


def numeric(value):
    try:
        return int(value.strip())
    except ValueError:
        return None


class SystemMetrics:
    def __init__(self, gpu_enabled, interval_seconds):
        self.smi = shutil.which("nvidia-smi") if gpu_enabled else None
        self.interval = interval_seconds
        self.lock = Lock()
        self.last = 0
        self.last_cpu = windows_times()
        self.values = {}

    def snapshot(self):
        with self.lock:
            if monotonic() - self.last < self.interval:
                return self.values
            result = {}
            cpu = windows_times()
            if cpu and self.last_cpu:
                idle, kernel, user = (current - previous for current, previous in zip(cpu, self.last_cpu))
                if kernel + user > 0:
                    result["cpu_percent"] = 100 * (kernel + user - idle) / (kernel + user)
            self.last_cpu = cpu
            ram = MemoryStatus()
            ram.length = ctypes.sizeof(ram)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ram)):
                result.update(ram_used_bytes=ram.total - ram.available, ram_total_bytes=ram.total)
            if self.smi:
                result.update(self.gpu())
            self.values = result
            self.last = monotonic()
            return result

    def gpu(self):
        try:
            response = subprocess.run([
                self.smi, "--id=0",
                "--query-gpu=name,utilization.encoder,utilization.decoder,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ], capture_output=True, encoding="utf-8", errors="replace", timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            name, encode, decode, used, total = next(csv.reader(response.stdout.splitlines()))
        except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
            return {}
        values = {"gpu_name": name.strip(), "encode_percent": numeric(encode), "decode_percent": numeric(decode),
                  "vram_used_mib": numeric(used), "vram_total_mib": numeric(total)}
        return {key: value for key, value in values.items() if value is not None}
