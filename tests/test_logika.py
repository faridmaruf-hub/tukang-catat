"""Uji logika inti tanpa model (cepat):  python -m unittest discover tests"""

import unittest

import numpy as np

from tukang_catat.speakers import SpeakerTracker
from tukang_catat.transcribe import group_turns, pack_windows


def voice(seed, noise=0.0, rng=np.random.default_rng(42)):
    base = np.random.default_rng(seed).normal(size=256)
    return base + rng.normal(scale=noise, size=256)


class TestSpeakerTracker(unittest.TestCase):
    def test_tidak_bertanya_dua_kali_untuk_suara_yang_sama(self):
        t = SpeakerTracker(threshold=0.6)
        a1 = t.identify(voice(1), 3.0)
        b1 = t.identify(voice(2), 3.0)
        a2 = t.identify(voice(1, noise=0.3), 3.0)
        self.assertTrue(a1.is_new and b1.is_new)
        self.assertFalse(a2.is_new)
        self.assertEqual(a2.speaker_id, a1.speaker_id)

    def test_potongan_pendek_tidak_membuat_pembicara_baru(self):
        t = SpeakerTracker(threshold=0.6)
        m = t.identify(voice(1), 0.8)
        self.assertIsNone(m.speaker_id)
        self.assertEqual(t.speakers, {})

    def test_nama_sama_menggabungkan_pembicara(self):
        t = SpeakerTracker(threshold=0.99)
        a = t.identify(voice(1), 3.0).speaker_id
        b = t.identify(voice(1, noise=0.5), 3.0).speaker_id  # terdeteksi "baru"
        self.assertNotEqual(a, b)
        t.label(a, "Bu Rina")
        kept = t.label(b, "bu rina")  # tidak peka huruf besar/kecil
        self.assertEqual(kept, a)
        self.assertEqual(list(t.speakers), [a])

    def test_peserta_tanpa_suara_diabaikan_saat_mencocokkan(self):
        t = SpeakerTracker(threshold=0.6)
        t.ensure_named("Pak Ahmad")
        m = t.identify(voice(1), 3.0)
        self.assertTrue(m.is_new)


    def test_rebuild_sidik_suara_setelah_koreksi(self):
        t = SpeakerTracker(threshold=0.6)
        a = t.identify(voice(1), 3.0).speaker_id
        b = t.new_speaker("Bu Rina")
        # notulis memindahkan potongan bersuara "2" ke Bu Rina
        t.rebuild(b, [(voice(2), 3.0)])
        best, _ = t.best_match(voice(2, noise=0.3))
        self.assertEqual(best, b)
        t.rebuild(a, [])
        self.assertIsNone(t.speakers[a]._sum)  # tanpa potongan = tanpa sidik suara


class TestGroupTurns(unittest.TestCase):
    def test_gabung_potongan_berurutan_orang_sama(self):
        segs = [
            {"speaker": "A", "start": 0.0, "end": 3.0},
            {"speaker": "A", "start": 3.5, "end": 6.0},
            {"speaker": "B", "start": 6.5, "end": 8.0},
            {"speaker": "A", "start": 12.0, "end": 14.0},
        ]
        turns = group_turns(segs)
        self.assertEqual([(t["speaker"], t["start"], t["end"]) for t in turns],
                         [("A", 0.0, 6.0), ("B", 6.5, 8.0), ("A", 12.0, 14.0)])

    def test_pengemasan_jendela_per_bahasa(self):
        sr = 16000
        turns = [{"language": l} for l in ["id", "id", "en", "id"]]
        clips = [np.ones(5 * sr, dtype=np.float32) for _ in turns]
        wins = pack_windows(turns, clips)
        self.assertEqual(len(wins), 3)  # id+id | en | id
        self.assertEqual([w["language"] for w in wins], ["id", "en", "id"])
        # kata di detik ke-1 milik giliran 0, di detik ke-7 milik giliran 1
        self.assertEqual(wins[0]["locate"](1.0), 0)
        self.assertEqual(wins[0]["locate"](7.0), 1)

    def test_giliran_panjang_dipecah(self):
        segs = [{"speaker": "A", "start": i * 10.0, "end": i * 10.0 + 9.5} for i in range(5)]
        self.assertTrue(all(t["end"] - t["start"] <= 28.0 for t in group_turns(segs)))


if __name__ == "__main__":
    unittest.main()
