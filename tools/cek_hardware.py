"""Cek kesiapan hardware untuk Tukang Catat.

Jalankan:  python tools/cek_hardware.py
Hanya memakai library bawaan Python, jadi bisa dijalankan sebelum instalasi apa pun.
"""

import os
import platform
import shutil
import subprocess
import sys

GB = 1024 ** 3


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def ram_gb():
    system = platform.system()
    if system == "Windows":
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MemStatus()
        stat.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / GB
    if system == "Darwin":
        val = run(["sysctl", "-n", "hw.memsize"])
        return int(val) / GB if val.isdigit() else 0.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024 / GB
    except OSError:
        pass
    return 0.0


def cpu_name():
    system = platform.system()
    if system == "Windows":
        name = run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor).Name"])
        return name or platform.processor()
    if system == "Darwin":
        return run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def nvidia_gpu():
    """Kembalikan (nama, vram_gb) GPU NVIDIA pertama, atau None."""
    if not shutil.which("nvidia-smi"):
        return None
    out = run(["nvidia-smi", "--query-gpu=name,memory.total",
               "--format=csv,noheader,nounits"])
    if not out:
        return None
    name, mem = [p.strip() for p in out.splitlines()[0].split(",")]
    return name, int(mem) / 1024


def recommend(cores, ram, gpu, apple_silicon):
    if gpu and gpu[1] >= 6:
        return "OPTIMAL", "large-v3 (GPU/CUDA)", "~3-6 menit per 1 jam rapat"
    if gpu and gpu[1] >= 4:
        return "OPTIMAL", "large-v3-turbo (GPU/CUDA)", "~5-10 menit per 1 jam rapat"
    if (apple_silicon or cores >= 8) and ram >= 16:
        return ("DIREKOMENDASIKAN", "large-v3-turbo (CPU); small jika ingin cepat",
                "~30 menit (turbo) / ~13 menit (small) per 1 jam rapat")
    if cores >= 4 and ram >= 8:
        return "MINIMUM", "small (CPU)", "~15-30 menit per 1 jam rapat"
    return "KURANG", "base atau tiny (CPU, akurasi rendah)", "bisa lebih lama dari durasi rapat"


def main():
    system = platform.system()
    machine = platform.machine().lower()
    apple_silicon = system == "Darwin" and machine in ("arm64", "aarch64")
    cores = os.cpu_count() or 0
    ram = ram_gb()
    disk_free = shutil.disk_usage(os.path.abspath(os.sep)).free / GB
    gpu = nvidia_gpu()
    ffmpeg = shutil.which("ffmpeg") is not None
    py = sys.version_info

    print("=" * 56)
    print(" Tukang Catat - Cek Kesiapan Hardware")
    print("=" * 56)
    print(f" Sistem operasi : {system} {platform.release()} ({machine})")
    print(f" CPU            : {cpu_name()}")
    print(f" Thread CPU     : {cores}")
    print(f" RAM            : {ram:.1f} GB")
    print(f" Disk kosong    : {disk_free:.1f} GB")
    print(f" GPU NVIDIA     : {f'{gpu[0]} ({gpu[1]:.1f} GB VRAM)' if gpu else 'tidak ada'}")
    if apple_silicon:
        print(" Apple Silicon  : ya")
    print(f" Python         : {py.major}.{py.minor}.{py.micro}")
    print(f" ffmpeg         : {'ada' if ffmpeg else 'TIDAK ADA'}")
    print("-" * 56)

    level, model, speed = recommend(cores, ram, gpu, apple_silicon)
    print(f" Tingkat        : {level}")
    print(f" Model Whisper  : {model}")
    print(f" Perkiraan      : {speed} (perkiraan kasar)")
    print("-" * 56)

    warnings = []
    if ram < 8:
        warnings.append("RAM di bawah 8 GB: aplikasi mungkin lambat/tidak stabil.")
    if disk_free < 10:
        warnings.append("Disk kosong di bawah 10 GB: model & rekaman butuh ruang.")
    if not ffmpeg:
        warnings.append("ffmpeg belum terpasang (dibutuhkan untuk membaca file audio).")
    if py < (3, 10):
        warnings.append("Python minimal 3.10.")
    if warnings:
        print(" Perhatian:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print(" Tidak ada masalah ditemukan.")
    print("=" * 56)


if __name__ == "__main__":
    main()
