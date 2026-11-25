"""Pelacak pembicara: mencocokkan sidik suara dengan pembicara yang sudah dikenal.

Setiap potongan bicara diubah menjadi vektor (embedding). Vektor ini
dibandingkan (cosine similarity) dengan "sidik suara" rata-rata tiap pembicara.
- Mirip  -> pembicara lama, sidik suaranya diperbarui.
- Tidak mirip siapa pun dan potongannya cukup panjang -> pembicara BARU,
  dan pencatat ditanya sekali saja "Siapa ini?".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Speaker:
    id: int
    name: str | None = None
    # Jumlah vektor ternormalisasi; arahnya = sidik suara rata-rata.
    _sum: np.ndarray | None = field(default=None, repr=False)
    count: int = 0

    @property
    def label(self) -> str:
        return self.name or f"Pembicara {self.id}"

    @property
    def centroid(self) -> np.ndarray:
        return self._sum / np.linalg.norm(self._sum)

    def add(self, emb: np.ndarray, weight: float = 1.0) -> None:
        self._sum = emb * weight if self._sum is None else self._sum + emb * weight
        self.count += 1

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "label": self.label, "count": self.count}


@dataclass
class Match:
    speaker_id: int | None  # None = tidak bisa dipastikan (potongan terlalu pendek)
    score: float
    is_new: bool


class SpeakerTracker:
    def __init__(self, threshold: float = 0.5, min_new_speaker_sec: float = 1.5):
        self.threshold = threshold
        self.min_new_speaker_sec = min_new_speaker_sec
        self.speakers: dict[int, Speaker] = {}
        self._next_id = 1

    # ------------------------------------------------------------ pencocokan
    def best_match(self, emb: np.ndarray) -> tuple[int | None, float]:
        """Pembicara paling mirip (tanpa mengubah apa pun)."""
        emb = _normalize(emb)
        best_id, best_score = None, -1.0
        for spk in self.speakers.values():
            if spk._sum is None:  # nama dari daftar peserta, belum pernah bicara
                continue
            score = float(np.dot(emb, spk.centroid))
            if score > best_score:
                best_id, best_score = spk.id, score
        return best_id, best_score

    def identify(self, emb: np.ndarray, duration: float) -> Match:
        emb = _normalize(emb)
        best_id, best_score = self.best_match(emb)

        if best_id is not None and best_score >= self.threshold:
            # Potongan pendek tetap dicocokkan, tapi tidak dipakai untuk
            # memperbarui sidik suara karena embedding-nya kurang andal.
            if duration >= self.min_new_speaker_sec:
                self.speakers[best_id].add(emb, weight=min(duration, 10.0))
            return Match(best_id, best_score, is_new=False)

        if duration < self.min_new_speaker_sec:
            return Match(None, best_score, is_new=False)

        spk = Speaker(id=self._next_id)
        self._next_id += 1
        spk.add(emb, weight=min(duration, 10.0))
        self.speakers[spk.id] = spk
        return Match(spk.id, best_score, is_new=True)

    # --------------------------------------------------------- pelabelan
    def find_by_name(self, name: str) -> Speaker | None:
        key = name.strip().casefold()
        for spk in self.speakers.values():
            if spk.name and spk.name.casefold() == key:
                return spk
        return None

    def label(self, speaker_id: int, name: str) -> int:
        """Beri nama pembicara. Jika nama sudah dipakai pembicara lain,
        keduanya digabung (artinya mesin keliru menganggapnya orang baru).
        Mengembalikan id pembicara yang tersisa."""
        name = name.strip()
        spk = self.speakers[speaker_id]
        existing = self.find_by_name(name)
        if existing and existing.id != speaker_id:
            self.merge(speaker_id, existing.id)
            return existing.id
        spk.name = name
        return speaker_id

    def merge(self, from_id: int, into_id: int) -> None:
        src, dst = self.speakers.pop(from_id), self.speakers[into_id]
        if src._sum is not None:
            dst._sum = src._sum if dst._sum is None else dst._sum + src._sum
        dst.count += src.count

    def ensure_named(self, name: str) -> int:
        """Id pembicara bernama `name`; dibuat (tanpa sidik suara) jika belum ada."""
        existing = self.find_by_name(name)
        if existing:
            return existing.id
        spk = Speaker(id=self._next_id, name=name.strip())
        self._next_id += 1
        self.speakers[spk.id] = spk
        return spk.id

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.speakers.values()]


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v
