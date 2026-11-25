"""Jalankan Tukang Catat:  python run.py

Membuka server lokal di http://127.0.0.1:8765 dan membuka browser otomatis.
"""

import argparse
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Tukang Catat")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    args = ap.parse_args()

    models = ROOT / "models"
    if not (models / "silero_vad.onnx").exists() or not (models / "speaker_embedding.onnx").exists():
        print("Model belum ada, mengunduh dulu...")
        subprocess.run([sys.executable, str(ROOT / "tools" / "unduh_model.py")], check=True)

    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"\nTukang Catat berjalan di {url}  (tekan Ctrl+C untuk berhenti)\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("tukang_catat.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
