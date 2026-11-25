"""Sesi rekaman rapat: menerima audio, mendeteksi potongan bicara,
mengenali pembicara, dan menyimpan semuanya ke folder sesi."""

from __future__ import annotations

import io
import json
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sherpa_onnx

from .speakers import SpeakerTracker

SAMPLE_RATE = 16000
VAD_WINDOW = 512
CLIP_MAX_SEC = 6.0


class Models:
    """Model ONNX dimuat sekali dan dipakai bersama oleh semua sesi."""

    def __init__(self, models_dir: Path, num_threads: int = 2):
        self.vad_path = models_dir / "silero_vad.onnx"
        emb_path = models_dir / "speaker_embedding.onnx"
        for p in (self.vad_path, emb_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"Model tidak ditemukan: {p}\nJalankan dulu: python tools/unduh_model.py"
                )
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_path), num_threads=num_threads
        )
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def new_vad(self) -> sherpa_onnx.VoiceActivityDetector:
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(self.vad_path)
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = 0.4  # jeda antar-giliran bicara
        cfg.silero_vad.min_speech_duration = 0.3
        cfg.silero_vad.max_speech_duration = 12.0  # potong monolog panjang
        cfg.silero_vad.window_size = VAD_WINDOW
        cfg.sample_rate = SAMPLE_RATE
        return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        return np.array(self.extractor.compute(stream), dtype=np.float32)


class Session:
    def __init__(
        self,
        sid: str,
        root: Path,
        models: Models,
        title: str = "",
        participants: list[str] | None = None,
        threshold: float = 0.5,
    ):
        self.sid = sid
        self.dir = root / sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.models = models
        self.title = title.strip() or "Rapat"
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.participants = [p.strip() for p in (participants or []) if p.strip()]
        self.tracker = SpeakerTracker(threshold=threshold)
        self.vad = models.new_vad()
        self.segments: list[dict] = []
        self.clips: dict[int, np.ndarray] = {}
        self._unresolved: dict[int, np.ndarray] = {}  # seg_id -> embedding
        self.total_samples = 0
        self.recording = True
        self._pending = np.zeros(0, dtype=np.float32)
        self._wav = wave.open(str(self.dir / "audio.wav"), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

    # ------------------------------------------------------------ audio masuk
    def feed(self, samples: np.ndarray) -> list[dict]:
        """Terima potongan audio float32 16 kHz mono. Kembalikan event untuk UI."""
        if not self.recording or samples.size == 0:
            return []
        samples = np.clip(samples.astype(np.float32), -1.0, 1.0)
        self._wav.writeframes((samples * 32767).astype("<i2").tobytes())
        self.total_samples += samples.size

        buf = np.concatenate([self._pending, samples])
        n_full = (buf.size // VAD_WINDOW) * VAD_WINDOW
        for i in range(0, n_full, VAD_WINDOW):
            self.vad.accept_waveform(buf[i : i + VAD_WINDOW])
        self._pending = buf[n_full:]
        return self._drain()

    def _drain(self) -> list[dict]:
        events = []
        while not self.vad.empty():
            seg = self.vad.front
            samples = np.array(seg.samples, dtype=np.float32)
            start = seg.start
            self.vad.pop()
            events.extend(self._handle_speech(start, samples))
        return events

    def _handle_speech(self, start_sample: int, samples: np.ndarray) -> list[dict]:
        start = start_sample / SAMPLE_RATE
        duration = samples.size / SAMPLE_RATE
        emb = self.models.embed(samples)
        match = self.tracker.identify(emb, duration)
        seg = {
            "id": len(self.segments) + 1,
            "start": round(start, 2),
            "end": round(start + duration, 2),
            "speaker_id": match.speaker_id,
            "score": round(match.score, 3),
        }
        self.segments.append(seg)
        events = []
        if match.speaker_id is None:
            self._unresolved[seg["id"]] = emb
        if match.is_new:
            self.clips[match.speaker_id] = samples[: int(CLIP_MAX_SEC * SAMPLE_RATE)]
            events.append({"type": "new_speaker", "speaker_id": match.speaker_id,
                           "start": seg["start"], "end": seg["end"]})
        events.append({"type": "segment", "segment": self._seg_view(seg)})
        if match.is_new:
            events.extend(self._resolve_unknowns())
        return events

    def _resolve_unknowns(self) -> list[dict]:
        """Cocokkan ulang potongan pendek '?' setelah ada pembicara baru dikenal."""
        events = []
        for seg_id, emb in list(self._unresolved.items()):
            best_id, score = self.tracker.best_match(emb)
            if best_id is not None and score >= self.tracker.threshold:
                seg = self.segments[seg_id - 1]
                seg["speaker_id"], seg["score"] = best_id, round(score, 3)
                del self._unresolved[seg_id]
                events.append({"type": "segment", "segment": self._seg_view(seg)})
        return events

    def stop(self) -> list[dict]:
        if not self.recording:
            return []
        if self._pending.size:
            self.vad.accept_waveform(self._pending)
            self._pending = np.zeros(0, dtype=np.float32)
        self.vad.flush()
        events = self._drain()
        self.recording = False
        self._wav.close()
        self.save()
        return events

    # ----------------------------------------------------- tindakan pencatat
    def label(self, speaker_id: int, name: str) -> None:
        """Jawaban "Siapa ini?". Nama yang sudah ada = gabungkan dua pembicara."""
        if speaker_id not in self.tracker.speakers or not name.strip():
            return
        kept = self.tracker.label(speaker_id, name)
        if kept != speaker_id:
            for seg in self.segments:
                if seg["speaker_id"] == speaker_id:
                    seg["speaker_id"] = kept
            clip = self.clips.pop(speaker_id, None)
            if kept not in self.clips and clip is not None:
                self.clips[kept] = clip
        self.save()

    def reassign(self, segment_id: int, name: str) -> None:
        """Koreksi satu potongan bicara secara manual."""
        if not name.strip() or not (1 <= segment_id <= len(self.segments)):
            return
        self.segments[segment_id - 1]["speaker_id"] = self.tracker.ensure_named(name)
        self.save()

    # ------------------------------------------------------------- tampilan
    def _speaker_label(self, speaker_id: int | None) -> str:
        if speaker_id is None:
            return "?"
        spk = self.tracker.speakers.get(speaker_id)
        return spk.label if spk else "?"

    def _seg_view(self, seg: dict) -> dict:
        return {**seg, "speaker": self._speaker_label(seg["speaker_id"])}

    def state(self) -> dict:
        return {
            "type": "state",
            "sid": self.sid,
            "title": self.title,
            "recording": self.recording,
            "duration": round(self.total_samples / SAMPLE_RATE, 1),
            "participants": self.participants,
            "speakers": self.tracker.to_list(),
            "segments": [self._seg_view(s) for s in self.segments],
        }

    def clip_wav(self, speaker_id: int) -> bytes | None:
        clip = self.clips.get(speaker_id)
        if clip is None:
            return None
        bio = io.BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((clip * 32767).astype("<i2").tobytes())
        return bio.getvalue()

    def save(self) -> None:
        data = self.state()
        data.pop("type")
        data["started_at"] = self.started_at
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.dir / "session.json")
