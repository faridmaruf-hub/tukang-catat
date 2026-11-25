"""Unduh model ONNX yang dibutuhkan Tukang Catat ke folder models/.

Jalankan:  python tools/unduh_model.py
Model Whisper TIDAK diunduh di sini; faster-whisper mengunduhnya otomatis
saat transkripsi pertama kali dijalankan.
"""

import sys
import urllib.request
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MODELS = {
    # Deteksi ada/tidaknya suara (Voice Activity Detection)
    "silero_vad.onnx": f"{BASE}/asr-models/silero_vad.onnx",
    # Sidik suara pembicara (speaker embedding), dilatih pada VoxCeleb
    # (ribuan pembicara multibahasa), tidak bergantung pada bahasa yang diucapkan.
    "speaker_embedding.onnx": (
        f"{BASE}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
    ),
}

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def download(name, url):
    target = MODELS_DIR / name
    if target.exists() and target.stat().st_size > 0:
        print(f"  [ada]    {name}")
        return
    print(f"  [unduh]  {name} ...", end="", flush=True)
    tmp = target.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(target)
    print(f" selesai ({target.stat().st_size / 1e6:.1f} MB)")


def main():
    MODELS_DIR.mkdir(exist_ok=True)
    print(f"Folder model: {MODELS_DIR}")
    try:
        for name, url in MODELS.items():
            download(name, url)
    except Exception as e:  # noqa: BLE001
        print(f"\nGagal mengunduh: {e}")
        sys.exit(1)
    print("Semua model siap.")


if __name__ == "__main__":
    main()
