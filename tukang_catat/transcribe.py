"""Transkripsi setelah rapat: Whisper + giliran bicara -> transkrip berlabel.

Karena saat rapat kita sudah tahu KAPAN dan SIAPA yang bicara:
1. Bahasa tiap giliran dideteksi dengan model `tiny` (murah), sehingga rapat
   campuran Indonesia-Inggris tidak "diterjemahkan" ke satu bahasa.
2. Giliran berurutan yang berbahasa sama DIKEMAS ke satu jendela <= 28 detik,
   dipisah jeda hening buatan. Whisper selalu membayar biaya jendela 30 detik,
   jadi pengemasan ini jauh lebih cepat daripada satu panggilan per giliran.
3. Kata hasil transkripsi dikembalikan ke giliran asalnya berdasarkan posisi
   waktunya di dalam jendela; jeda hening membuat batas giliran jelas.

Bisa juga dijalankan langsung dari terminal:
    python -m tukang_catat.transcribe recordings/<sesi> --model medium
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
OUTPUT_MD = "transkrip.md"
OUTPUT_JSON = "transkrip.json"
MERGE_GAP_SEC = 1.5   # potongan berurutan dari orang yang sama digabung
MAX_TURN_SEC = 28.0   # sedikit di bawah jendela 30 detik Whisper
PAD_SEC = 0.15        # sedikit audio di kiri-kanan tiap giliran
SEP_SEC = 0.8         # jeda hening buatan antar-giliran di dalam satu jendela
LANG_MODEL = "tiny"


def fmt_time(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def load_wav(path: Path) -> np.ndarray:
    """Baca WAV 16 kHz mono hasil rekaman sebagai float32. Dengan membacanya
    sendiri, kita tidak bergantung pada pembaca audio bawaan faster-whisper (PyAV)."""
    with wave.open(str(path)) as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("audio.wav harus 16 kHz, mono, 16-bit")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def pick_device() -> tuple[str, str]:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # noqa: BLE001
        pass
    return "cpu", "int8"


def group_turns(segments: list[dict]) -> list[dict]:
    """Gabungkan potongan VAD berurutan dari pembicara yang sama menjadi giliran."""
    turns: list[dict] = []
    for s in sorted(segments, key=lambda s: s["start"]):
        last = turns[-1] if turns else None
        if (last and last["speaker"] == s["speaker"]
                and s["start"] - last["end"] <= MERGE_GAP_SEC
                and s["end"] - last["start"] <= MAX_TURN_SEC):
            last["end"] = s["end"]
        else:
            turns.append({"speaker": s["speaker"], "start": s["start"], "end": s["end"]})
    return turns


def _clip(audio: np.ndarray, turn: dict) -> np.ndarray:
    a = max(0, int((turn["start"] - PAD_SEC) * SAMPLE_RATE))
    b = min(audio.size, int((turn["end"] + PAD_SEC) * SAMPLE_RATE))
    return audio[a:b]


def pack_windows(turns: list[dict], clips: list[np.ndarray],
                 max_sec: float = MAX_TURN_SEC) -> list[dict]:
    """Kemas giliran berurutan yang berbahasa sama ke jendela <= max_sec.

    Setiap jendela berisi audio gabungan dan fungsi `locate(detik)` yang
    mengembalikan indeks giliran pemilik detik tersebut di dalam jendela."""
    sep = np.zeros(int(SEP_SEC * SAMPLE_RATE), dtype=np.float32)
    groups: list[list[int]] = []
    for i, (t, c) in enumerate(zip(turns, clips)):
        if groups:
            g = groups[-1]
            used = sum(clips[j].size for j in g) + sep.size * len(g)
            if (turns[g[-1]]["language"] == t["language"]
                    and (used + c.size) / SAMPLE_RATE <= max_sec):
                g.append(i)
                continue
        groups.append([i])

    windows = []
    for g in groups:
        parts, spans, pos = [], [], 0
        for k, j in enumerate(g):
            if k:
                parts.append(sep)
                pos += sep.size
            parts.append(clips[j])
            spans.append((pos / SAMPLE_RATE, (pos + clips[j].size) / SAMPLE_RATE, j))
            pos += clips[j].size

        def locate(sec: float, spans=spans) -> int:
            # giliran yang memuat detik ini, atau yang terdekat (di dalam jeda)
            return min(spans, key=lambda s: 0 if s[0] <= sec <= s[1]
                       else min(abs(sec - s[0]), abs(sec - s[1])))[2]

        windows.append({"audio": np.concatenate(parts), "language": turns[g[0]]["language"],
                        "locate": locate})
    return windows


def render_markdown(session: dict, turns: list[dict], model: str, duration: float) -> str:
    speakers = sorted({t["speaker"] for t in turns if t["speaker"] != "?"})
    lines = [
        f"# Transkrip: {session.get('title', 'Rapat')}",
        "",
        f"- Tanggal: {session.get('started_at', '-').replace('T', ' ')}",
        f"- Durasi: {fmt_time(duration)}",
        f"- Pembicara: {', '.join(speakers) or '-'}",
    ]
    if session.get("participants"):
        lines.append(f"- Daftar peserta: {', '.join(session['participants'])}")
    lines += [
        f"- Dibuat oleh: Tukang Catat (Whisper `{model}`)",
        "- Catatan: label `?` berarti pembicara tidak dapat dipastikan.",
        "",
        "---",
        "",
    ]
    # Giliran berurutan dari orang yang sama ditampilkan sebagai satu paragraf.
    prev = None
    for t in turns:
        if prev and prev["speaker"] == t["speaker"]:
            lines[-2] += " " + t["text"]
        else:
            lines.append(f"**[{fmt_time(t['start'])}] {t['speaker']}:** {t['text']}")
            lines.append("")
        prev = t
    return "\n".join(lines)


def transcribe_session(
    session_dir: Path,
    model_size: str = "large-v3-turbo",
    language: str | None = "id",
    progress: Callable[[float, str], None] = lambda p, msg: None,
) -> Path:
    from faster_whisper import WhisperModel

    session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    audio = load_wav(session_dir / "audio.wav")
    duration = audio.size / SAMPLE_RATE
    turns = group_turns(session.get("segments", []))
    total = sum(t["end"] - t["start"] for t in turns) or 1.0

    device, compute_type = pick_device()
    clips = [_clip(audio, t) for t in turns]

    # 1. Bahasa per giliran
    if language:
        for t in turns:
            t["language"] = language
    else:
        progress(0.0, "Mendeteksi bahasa tiap giliran...")
        detector = WhisperModel(LANG_MODEL, device=device, compute_type=compute_type)
        for i, (t, c) in enumerate(zip(turns, clips), 1):
            t["language"] = detector.detect_language(c)[0]
            progress(0.1 * i / len(turns), f"Mendeteksi bahasa {i}/{len(turns)}")
        del detector

    # 2-3. Kemas, transkripsi, kembalikan kata ke gilirannya
    progress(0.1, f"Memuat model {model_size} ({device}). Pertama kali: model diunduh dulu...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    windows = pack_windows(turns, clips)
    for t in turns:
        t["text"] = ""
    for w_i, win in enumerate(windows, 1):
        segs, _ = model.transcribe(
            win["audio"],
            language=win["language"],
            beam_size=5,
            word_timestamps=True,
            condition_on_previous_text=False,
            vad_filter=False,  # sudah dipotong oleh VAD saat rapat
        )
        words = [w for s in segs for w in (s.words or [])]
        for w in words:
            idx = win["locate"]((w.start + w.end) / 2)
            turns[idx]["text"] += w.word
        progress(0.1 + 0.89 * w_i / len(windows),
                 f"Mentranskripsi bagian {w_i}/{len(windows)}")

    for t in turns:
        t["text"] = t["text"].strip()
    turns = [t for t in turns if t["text"]]
    md = render_markdown(session, turns, model_size, duration)
    out = session_dir / OUTPUT_MD
    out.write_text(md, encoding="utf-8")
    (session_dir / OUTPUT_JSON).write_text(
        json.dumps({"title": session.get("title"), "started_at": session.get("started_at"),
                    "model": model_size, "turns": turns}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress(1.0, "Selesai")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Transkripsi sesi Tukang Catat")
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--language", default="auto", help="kode bahasa, atau 'auto'")
    args = ap.parse_args()
    lang = None if args.language == "auto" else args.language
    out = transcribe_session(args.session_dir, args.model, lang,
                             progress=lambda p, msg: print(f"[{p:5.0%}] {msg}"))
    print(f"Transkrip tersimpan: {out}")


if __name__ == "__main__":
    main()
