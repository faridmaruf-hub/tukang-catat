"""Sesi rekaman rapat: menerima audio, mendeteksi potongan bicara,
mengenali pembicara, menerima koreksi notulis, dan menyimpan semuanya."""

from __future__ import annotations

import copy
import io
import json
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import sherpa_onnx

from .speakers import SpeakerTracker

SAMPLE_RATE = 16000
VAD_WINDOW = 512
CLIP_MAX_SEC = 6.0
RING_SEC = 60.0          # audio terakhir yang disimpan di memori untuk koreksi
LOOKBACK_SEC = 20.0      # seberapa jauh "mundur" saat notulis mengoreksi
WIN_SEC = 1.5            # jendela kecil untuk mencari titik pergantian
HOP_SEC = 0.5
SPLIT_GAIN = 0.04        # minimal peningkatan kemiripan agar dianggap ada pergantian
SNAP_SEC = 1.0           # tempelkan titik pergantian ke awal potongan terdekat
LIVE_EVERY_SEC = 1.0     # seberapa sering lampu "sedang bicara" diperbarui
STICKY_SLACK = 0.10      # channel aktif tetap dipakai walau skor sedikit di bawah ambang
STICKY_MARGIN = 0.04     # ...selama orang lain tidak lebih cocok dari selisih ini
REFINE_SEC = 2.0         # setelah klik, dengarkan suara baru selama ini sebelum menganalisis


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
        v = np.array(self.extractor.compute(stream), dtype=np.float32)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v


class AudioRing:
    """Menyimpan audio beberapa puluh detik terakhir, dialamatkan dengan detik absolut."""

    def __init__(self, seconds: float):
        self.max = int(seconds * SAMPLE_RATE)
        self.chunks: deque[np.ndarray] = deque()
        self.size = 0
        self.end = 0  # posisi sampel absolut setelah sampel terakhir

    def append(self, x: np.ndarray) -> None:
        self.chunks.append(x)
        self.size += x.size
        self.end += x.size
        while self.size - self.chunks[0].size >= self.max:
            self.size -= self.chunks.popleft().size

    @property
    def start_sec(self) -> float:
        return (self.end - self.size) / SAMPLE_RATE

    def get(self, t0: float, t1: float) -> np.ndarray:
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        data = np.concatenate(self.chunks)
        base = self.end - self.size
        a = max(0, int(t0 * SAMPLE_RATE) - base)
        b = max(a, min(data.size, int(t1 * SAMPLE_RATE) - base))
        return data[a:b]


@dataclass
class PendingOverride:
    """Koreksi untuk kalimat yang SEDANG diucapkan saat tombol diklik
    (potongannya baru selesai setelah orangnya berhenti bicara)."""
    speaker_id: int
    change_t: float
    click_t: float


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
        self.participants: list[str] = []
        self.tracker = SpeakerTracker(threshold=threshold)
        for p in participants or []:
            self.add_participant(p, save=False)
        self.vad = models.new_vad()
        self.ring = AudioRing(RING_SEC)
        self.segments: list[dict] = []           # urut berdasarkan waktu mulai
        self._embs: dict[int, tuple[np.ndarray, float]] = {}  # seg_id -> (emb, bobot)
        self._unresolved: set[int] = set()       # seg_id potongan pendek berlabel '?'
        self._next_seg = 1
        self.clips: dict[int, np.ndarray] = {}
        self.total_samples = 0
        self.recording = True
        self._pending_vad = np.zeros(0, dtype=np.float32)
        self._override: PendingOverride | None = None
        self._undo: dict | None = None
        self._live = (False, None)
        self._live_at = 0
        self._active: int | None = None   # channel yang sedang "terbuka"
        self._active_manual = False       # dibuka oleh klik notulis?
        self._last_click_t = -1e9
        self._deferred: dict | None = None  # klik yang menunggu analisis
        self._wav = wave.open(str(self.dir / "audio.wav"), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

    @property
    def now(self) -> float:
        return self.total_samples / SAMPLE_RATE

    # ------------------------------------------------------------ audio masuk
    def feed(self, samples: np.ndarray) -> list[dict]:
        """Terima potongan audio float32 16 kHz mono. Kembalikan event untuk UI."""
        if not self.recording or samples.size == 0:
            return []
        samples = np.clip(samples.astype(np.float32), -1.0, 1.0)
        self._wav.writeframes((samples * 32767).astype("<i2").tobytes())
        self.ring.append(samples)
        self.total_samples += samples.size

        buf = np.concatenate([self._pending_vad, samples])
        n_full = (buf.size // VAD_WINDOW) * VAD_WINDOW
        for i in range(0, n_full, VAD_WINDOW):
            self.vad.accept_waveform(buf[i : i + VAD_WINDOW])
        self._pending_vad = buf[n_full:]
        events = self._drain()
        if self._deferred and self.now >= self._deferred["due"]:
            events.extend(self._run_deferred())
        events.extend(self._live_update())
        return events

    def _drain(self) -> list[dict]:
        events = []
        while not self.vad.empty():
            seg = self.vad.front
            samples = np.array(seg.samples, dtype=np.float32)
            start = seg.start
            self.vad.pop()
            events.extend(self._handle_speech(start / SAMPLE_RATE, samples))
        return events

    def _live_update(self) -> list[dict]:
        """Lampu 'sedang bicara': tebakan cepat dari 1,5 detik audio terakhir."""
        speaking = self.vad.is_speech_detected()
        spk = self._live[1]
        if not speaking:
            spk = None
        elif self.total_samples - self._live_at >= LIVE_EVERY_SEC * SAMPLE_RATE:
            self._live_at = self.total_samples
            if self._override:
                spk = self._override.speaker_id
            else:
                emb = self.models.embed(self.ring.get(self.now - WIN_SEC, self.now))
                best, score = self.tracker.best_match(emb)
                spk = best if best is not None and score >= self.tracker.threshold - 0.05 else None
        if (speaking, spk) == self._live:
            return []
        self._live = (speaking, spk)
        return [{"type": "live", "speaking": speaking, "speaker_id": spk}]

    # -------------------------------------------------------- potongan bicara
    def _new_seg(self, start: float, end: float, speaker_id: int | None, score: float,
                 emb: np.ndarray, manual: bool = False) -> dict:
        seg = {"id": self._next_seg, "start": round(start, 2), "end": round(end, 2),
               "speaker_id": speaker_id, "score": round(score, 3), "manual": manual}
        self._next_seg += 1
        dur = end - start
        weight = min(dur, 10.0) if dur >= self.tracker.min_new_speaker_sec else 0.0
        self._embs[seg["id"]] = (emb, weight)
        self.segments.append(seg)
        self.segments.sort(key=lambda s: s["start"])
        return seg

    def _handle_speech(self, start: float, samples: np.ndarray) -> list[dict]:
        end = start + samples.size / SAMPLE_RATE
        ov = self._override
        if ov and start < ov.click_t:
            # Kalimat yang sedang berlangsung saat tombol diklik: ikuti notulis.
            self._override = None
            return self._forced_speech(start, end, samples, ov)

        emb = self.models.embed(samples)
        sticky = self._sticky(emb)
        if sticky is not None:
            seg = self._new_seg(start, end, sticky[0], sticky[1], emb)
            self._rebuild_all()
            return [{"type": "segment", "segment": self._seg_view(seg)}]
        match = self.tracker.identify(emb, end - start)
        if match.speaker_id is not None:
            self._active = match.speaker_id
        seg = self._new_seg(start, end, match.speaker_id, match.score, emb)
        events = []
        if match.speaker_id is None:
            self._unresolved.add(seg["id"])
        if match.is_new:
            self.clips[match.speaker_id] = samples[: int(CLIP_MAX_SEC * SAMPLE_RATE)]
            events.append({"type": "new_speaker", "speaker_id": match.speaker_id,
                           "start": seg["start"], "end": seg["end"]})
        events.append({"type": "segment", "segment": self._seg_view(seg)})
        if match.is_new:
            events.extend(self._resolve_unknowns())
        return events

    def _sticky(self, emb: np.ndarray) -> tuple[int, float] | None:
        """Prinsip mixer: channel yang terakhir ditekan notulis tetap "terbuka".
        Potongan berikutnya tetap milik orang itu, kecuali suara lain JELAS lebih
        cocok. Ini mencegah mesin berganti sendiri pada audio yang sulit (TV,
        speaker), tapi tetap membiarkannya mendeteksi orang baru di ruangan."""
        spk = self.tracker.speakers.get(self._active) if self._active_manual else None
        if spk is None or spk._sum is None:
            return None
        s_active = float(np.dot(emb, spk.centroid))
        best, s_best = self.tracker.best_match(emb)
        if (s_active >= self.tracker.threshold - STICKY_SLACK
                and s_best - s_active < STICKY_MARGIN):
            return spk.id, s_active
        self._active_manual = False  # suara lain jelas berbeda: channel ditutup
        return None

    def _forced_speech(self, start: float, end: float, samples: np.ndarray,
                       ov: PendingOverride) -> list[dict]:
        cut = ov.change_t if start + 0.3 < ov.change_t < end - 0.3 else start
        if cut > start:
            head = samples[: int((cut - start) * SAMPLE_RATE)]
            emb = self.models.embed(head)
            prev = self._speaker_before(start)
            best, score = self.tracker.best_match(emb)
            owner = best if best is not None and score >= self.tracker.threshold else prev
            self._new_seg(start, cut, owner, score, emb)
        tail = samples[int((cut - start) * SAMPLE_RATE):]
        self._new_seg(cut, end, ov.speaker_id, 1.0, self.models.embed(tail), manual=True)
        self._rebuild_all()
        return [self.state()]

    def _speaker_before(self, t: float) -> int | None:
        prev = [s for s in self.segments if s["end"] <= t + 0.05 and s["speaker_id"] is not None]
        return prev[-1]["speaker_id"] if prev else None

    def _resolve_unknowns(self) -> list[dict]:
        """Cocokkan ulang potongan pendek '?' setelah ada pembicara baru dikenal."""
        events = []
        for seg in self.segments:
            if seg["id"] not in self._unresolved:
                continue
            best_id, score = self.tracker.best_match(self._embs[seg["id"]][0])
            if best_id is not None and score >= self.tracker.threshold:
                seg["speaker_id"], seg["score"] = best_id, round(score, 3)
                self._unresolved.discard(seg["id"])
                events.append({"type": "segment", "segment": self._seg_view(seg)})
        return events

    # ----------------------------------------- koreksi notulis + mundur sedikit
    def override(self, target: str | int | None) -> list[dict]:
        """Notulis menekan tombol peserta: "yang bicara SEKARANG adalah orang ini".

        target: id pembicara, nama peserta, atau None untuk "orang baru".
        Channel langsung menyala, tetapi analisis "mundur sedikit" ditunda
        REFINE_SEC detik: aplikasi mendengar suara barunya sedikit lebih lama
        dulu, supaya titik pergantian bisa ditemukan dengan lebih yakin."""
        if not self.recording:
            return []
        events = self._run_deferred()  # klik sebelumnya yang belum diproses
        if isinstance(target, int) and target not in self.tracker.speakers:
            return events
        if isinstance(target, str):
            self._remember_participant(target)
            target = self.tracker.ensure_named(target)
        self._deferred = {"target": target, "click_t": self.now,
                          "due": self.now + REFINE_SEC}
        if target is not None:
            self._active, self._active_manual = target, True
            if self._live[0]:
                self._live = (True, target)
        events.append({"type": "override_pending", "speaker_id": target,
                       "click_t": round(self.now, 2)})
        events.append({"type": "live", "speaking": self._live[0],
                       "speaker_id": target if self._live[0] else None})
        return events

    def _run_deferred(self) -> list[dict]:
        d, self._deferred = self._deferred, None
        if not d:
            return []
        return self._execute_override(d["target"], d["click_t"])

    def _execute_override(self, target: int | None, click_t: float) -> list[dict]:
        self._save_undo()
        is_new = target is None
        tid = self.tracker.new_speaker() if is_new else target
        now = self.now
        change_t = self.find_change_point(click_t, now)
        moved_sec, moved_n = self._move_since(change_t, tid)
        if self.vad.is_speech_detected():
            # Kalimat yang masih berlangsung: potongannya baru jadi nanti.
            self._override = PendingOverride(tid, change_t, now)
            moved_sec += max(0.0, now - max(change_t, self._last_seg_end()))
        self._rebuild_all(keep={tid})
        self._active, self._active_manual, self._last_click_t = tid, True, click_t
        if self._live[0]:
            self._live = (True, tid)

        events = []
        if is_new:
            self.clips[tid] = self.ring.get(change_t, min(now, change_t + CLIP_MAX_SEC))
            events.append({"type": "new_speaker", "speaker_id": tid,
                           "start": round(change_t, 2), "end": round(now, 2)})
        events.append(self.state())
        events.append({"type": "override", "speaker_id": tid,
                       "label": self.tracker.speakers[tid].label,
                       "from": round(change_t, 2), "moved_sec": round(moved_sec, 1),
                       "segments": moved_n})
        self.save()
        return events

    def find_change_point(self, click_t: float, until_t: float | None = None) -> float:
        """Cari kapan suara 'yang sekarang' mulai: maksimal LOOKBACK_SEC sebelum
        klik, tidak melewati klik sebelumnya (saat itu notulis sudah memastikan
        siapa), dan tidak sesudah klik. Audio sesudah klik (sampai `until_t`)
        ikut dipakai sebagai contoh suara "yang sekarang"."""
        until_t = max(until_t or click_t, click_t)
        t_lo = max(click_t - LOOKBACK_SEC, self.ring.start_sec, self._last_click_t + 0.5)
        if until_t - t_lo < WIN_SEC * 2:
            return self._snap(self._recent_speech_start(t_lo, click_t), t_lo)
        audio = self.ring.get(t_lo, until_t)
        win, hop = int(WIN_SEC * SAMPLE_RATE), int(HOP_SEC * SAMPLE_RATE)
        starts = list(range(0, max(0, audio.size - win) + 1, hop)) if audio.size >= win else []
        if starts:
            rms = np.array([np.sqrt(np.mean(audio[s:s + win] ** 2)) for s in starts])
            loud = max(0.003, 0.2 * float(np.percentile(rms, 90)))
            starts = [s for s, r in zip(starts, rms) if r >= loud]
        if len(starts) < 3:
            return self._snap(self._recent_speech_start(t_lo, click_t), t_lo)

        E = np.stack([self.models.embed(audio[s:s + win]) for s in starts])
        # Normalisasi jalur: kurangi rata-rata rentang ini. Bagian yang sama untuk
        # semua suara (speaker TV, ruangan, mikrofon) hilang; yang tersisa adalah
        # perbedaan antar-orang. Ini sangat membantu untuk audio lewat speaker.
        E = E - E.mean(axis=0)
        E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-6)
        n = len(starts)

        def cohesion(a: int, b: int) -> float:
            c = E[a:b].sum(axis=0)
            c /= np.linalg.norm(c)
            return float((E[a:b] @ c).sum())

        base = cohesion(0, n)
        best_k, best_gain = 0, 0.0
        for k in range(1, n - 1):  # minimal 2 jendela untuk suara "sekarang"
            if t_lo + starts[k] / SAMPLE_RATE > click_t:
                break  # pergantian pasti terjadi SEBELUM klik
            gain = cohesion(0, k) + cohesion(k, n) - base
            if gain > best_gain:
                best_k, best_gain = k, gain
        if best_gain / n < SPLIT_GAIN:
            # Tidak ada pergantian yang jelas: jangan mundur jauh, ambil awal
            # kalimat yang sedang/terakhir diucapkan sebelum klik.
            return self._snap(self._recent_speech_start(t_lo, click_t), t_lo)
        # Jendela pertama yang "suara baru" biasanya sudah separuh terisi suara baru.
        change = t_lo + starts[best_k] / SAMPLE_RATE + (WIN_SEC - HOP_SEC) / 2
        return self._snap(min(change, click_t), t_lo)

    def _recent_speech_start(self, t_lo: float, click_t: float) -> float:
        """Awal kalimat yang sedang/terakhir diucapkan SEBELUM klik."""
        if self.vad.is_speech_detected():
            cur = self.vad.current_segment.start / SAMPLE_RATE
            if cur <= click_t:
                return max(t_lo, cur)
        return max(t_lo, self._last_seg_start(t_lo, click_t))

    def _snap(self, t: float, t_lo: float) -> float:
        """Tempelkan ke awal potongan bicara terdekat; jeda adalah batas alami."""
        t = max(t, t_lo)
        for s in self.segments:
            if s["end"] <= t:
                continue
            if s["start"] >= t:  # t jatuh di jeda: mulai di potongan berikutnya
                return s["start"] if s["start"] - t <= SNAP_SEC * 3 else t
            if t - s["start"] <= SNAP_SEC:
                return s["start"]
            if s["end"] - t <= SNAP_SEC * 0.5:
                return s["end"]
            return t
        return t

    def _last_seg_start(self, t_lo: float, t_hi: float) -> float:
        inside = [s for s in self.segments if s["end"] > t_lo and s["start"] < t_hi]
        return inside[-1]["start"] if inside else t_hi

    def _last_seg_end(self) -> float:
        return self.segments[-1]["end"] if self.segments else 0.0

    def _move_since(self, t: float, tid: int) -> tuple[float, int]:
        moved_sec, moved_n = 0.0, 0
        for seg in list(self.segments):
            if seg["end"] <= t + 0.05:
                continue
            if seg["start"] < t - 0.05:  # potongan terbelah oleh titik pergantian
                seg = self._split(seg, t)
            seg["speaker_id"], seg["manual"], seg["score"] = tid, True, 1.0
            self._unresolved.discard(seg["id"])
            moved_sec += seg["end"] - seg["start"]
            moved_n += 1
        return moved_sec, moved_n

    def _split(self, seg: dict, t: float) -> dict:
        head = self.ring.get(seg["start"], t)
        tail = self.ring.get(t, seg["end"])
        old_emb = self._embs[seg["id"]][0]
        e_head = self.models.embed(head) if head.size >= SAMPLE_RATE // 2 else old_emb
        e_tail = self.models.embed(tail) if tail.size >= SAMPLE_RATE // 2 else old_emb
        old_end = seg["end"]
        seg["end"] = round(t, 2)
        dur = seg["end"] - seg["start"]
        self._embs[seg["id"]] = (e_head, min(dur, 10.0) if dur >= 1.5 else 0.0)
        return self._new_seg(t, old_end, seg["speaker_id"], seg["score"], e_tail)

    def _rebuild_all(self, keep: set[int] = frozenset()) -> None:
        """Sidik suara dibangun ulang dari potongan miliknya: aplikasi 'belajar'
        dari setiap koreksi. Pembicara tanpa nama & tanpa potongan dihapus."""
        owned: dict[int, list] = {sid: [] for sid in self.tracker.speakers}
        for seg in self.segments:
            if seg["speaker_id"] in owned:
                owned[seg["speaker_id"]].append(self._embs[seg["id"]])
        for sid, items in owned.items():
            spk = self.tracker.speakers[sid]
            has_segments = any(s["speaker_id"] == sid for s in self.segments)
            if not spk.name and not has_segments and sid not in keep:
                self.tracker.remove(sid)
                self.clips.pop(sid, None)
            else:
                self.tracker.rebuild(sid, items)

    # ---------------------------------------------------------------- batal
    def _save_undo(self) -> None:
        self._undo = {
            "segments": copy.deepcopy(self.segments),
            "embs": dict(self._embs),
            "speakers": copy.deepcopy(self.tracker.speakers),
            "next_id": self.tracker._next_id,
            "unresolved": set(self._unresolved),
            "clips": dict(self.clips),
        }

    def undo(self) -> list[dict]:
        if not self._undo:
            return []
        u, self._undo = self._undo, None
        self.segments, self._embs = u["segments"], u["embs"]
        self.tracker.speakers, self.tracker._next_id = u["speakers"], u["next_id"]
        self._unresolved, self.clips = u["unresolved"], u["clips"]
        self._override = None
        self._active_manual = False
        self.save()
        return [self.state()]

    # ---------------------------------------------------------------- stop
    def stop(self) -> list[dict]:
        if not self.recording:
            return []
        if self._pending_vad.size:
            self.vad.accept_waveform(self._pending_vad)
            self._pending_vad = np.zeros(0, dtype=np.float32)
        events = self._run_deferred()
        self.vad.flush()
        events += self._drain()
        self.recording = False
        self._override = None
        self._wav.close()
        self.save()
        return events

    # ----------------------------------------------------- tindakan pencatat
    def add_participant(self, name: str, save: bool = True) -> None:
        """Peserta baru (juga di tengah rapat): muncul sebagai channel di konsol."""
        name = name.strip()
        if not name:
            return
        self._remember_participant(name)
        self.tracker.ensure_named(name)
        if save:
            self.save()

    def _remember_participant(self, name: str) -> None:
        name = name.strip()
        if name and name.casefold() not in {p.casefold() for p in self.participants}:
            self.participants.append(name)

    def label(self, speaker_id: int, name: str) -> None:
        """Jawaban "Siapa ini?". Nama yang sudah ada = gabungkan dua pembicara."""
        if speaker_id not in self.tracker.speakers or not name.strip():
            return
        self._remember_participant(name)
        kept = self.tracker.label(speaker_id, name)
        if kept != speaker_id:
            for seg in self.segments:
                if seg["speaker_id"] == speaker_id:
                    seg["speaker_id"] = kept
            clip = self.clips.pop(speaker_id, None)
            if kept not in self.clips and clip is not None:
                self.clips[kept] = clip
            if self._override and self._override.speaker_id == speaker_id:
                self._override.speaker_id = kept
            self._rebuild_all()
        self.save()

    def reassign(self, segment_id: int, name: str) -> None:
        """Koreksi satu potongan bicara secara manual dari garis waktu."""
        seg = next((s for s in self.segments if s["id"] == segment_id), None)
        if seg is None or not name.strip():
            return
        self._save_undo()
        self._remember_participant(name)
        seg["speaker_id"] = self.tracker.ensure_named(name)
        seg["manual"] = True
        self._unresolved.discard(segment_id)
        self._rebuild_all()
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
        talk: dict[int, float] = {}
        for s in self.segments:
            if s["speaker_id"] is not None:
                talk[s["speaker_id"]] = talk.get(s["speaker_id"], 0.0) + s["end"] - s["start"]
        speakers = []
        for spk in self.tracker.speakers.values():
            d = spk.to_dict()
            d["talk_sec"] = round(talk.get(spk.id, 0.0), 1)
            d["has_voice"] = spk._sum is not None
            speakers.append(d)
        return {
            "type": "state",
            "sid": self.sid,
            "title": self.title,
            "recording": self.recording,
            "duration": round(self.now, 1),
            "participants": self.participants,
            "speakers": speakers,
            "segments": [self._seg_view(s) for s in self.segments],
            "can_undo": self._undo is not None,
        }

    def clip_wav(self, speaker_id: int) -> bytes | None:
        clip = self.clips.get(speaker_id)
        if clip is None or clip.size == 0:
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
        data.pop("can_undo")
        data["started_at"] = self.started_at
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.dir / "session.json")
