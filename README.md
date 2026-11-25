# Tukang Catat

Aplikasi pencatat notula rapat yang cerdas, gratis, dan berjalan **offline di komputer sendiri**.

Tukang Catat merekam rapat, mengenali **siapa yang sedang berbicara** dengan bantuan pencatat notula (manusia), lalu menghasilkan **transkrip berlabel pembicara**. Transkrip ini kemudian diolah menjadi notula rapat yang rapi menggunakan Claude dan *skill* khusus "Penulis Notula".

> Status: **tahap perancangan / prototipe awal**.

---

## Cara kerja

```
┌───────────────────────── SAAT RAPAT (real-time) ──────────────────────────┐
│  [Record] → deteksi suara → kenali sidik suara pembicara                  │
│     ├─ suara sudah dikenal  → dilabeli otomatis                           │
│     └─ suara baru           → pencatat ditanya: "Siapa ini?"              │
│                               (tidak ditanya dua kali untuk orang sama)   │
└───────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌───────────────────────── SETELAH RAPAT ───────────────────────────────────┐
│  Whisper mentranskripsi seluruh rekaman (akurasi tinggi)                  │
│  → digabung dengan garis waktu pembicara                                  │
│  → review & koreksi label oleh pencatat                                   │
│  → ekspor transkrip berlabel (.md / .json)                                │
└───────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌───────────────────────── PENULISAN NOTULA (terpisah) ─────────────────────┐
│  Transkrip → Claude + skill "Penulis Notula" → notula rapat final         │
│  (agenda, ringkasan, keputusan, action item, peserta)                     │
└───────────────────────────────────────────────────────────────────────────┘
```

### Prinsip desain
- **Gratis**: semua komponen open source. Penulisan notula memakai langganan Claude yang sudah ada, bukan API berbayar.
- **Privat**: audio dan transkrip tidak pernah meninggalkan komputer pengguna.
- **Manusia tetap memegang kendali**: mesin membedakan suara, manusia memberi nama. Kombinasi ini lebih andal daripada diarization yang sepenuhnya otomatis.
- **Lintas platform**: berbentuk aplikasi web lokal (server kecil di komputer pengguna + tampilan di browser), jadi bisa jalan di Windows, macOS, dan Linux.

## Komponen yang direncanakan

| Fungsi | Teknologi |
|---|---|
| Deteksi ada/tidaknya suara | Silero VAD |
| Sidik suara pembicara (speaker embedding) | SpeechBrain ECAPA / pyannote |
| Speech-to-text | faster-whisper (model Whisper) |
| Server lokal | Python (FastAPI) |
| Tampilan | Web (dibuka di browser, `http://localhost`) |
| Penulisan notula | Claude + custom skill "Penulis Notula" |

## Kebutuhan hardware

Aplikasi ini bisa dipasang di komputer dengan spesifikasi berbeda-beda. Ringkasnya:

| Tingkat | Spesifikasi | Model Whisper |
|---|---|---|
| Minimum | CPU 4 core, RAM 8 GB | `small` |
| Direkomendasikan | CPU 8+ core, RAM 16 GB | `medium` / `large-v3-turbo` |
| Optimal | GPU NVIDIA dengan VRAM 6 GB+ | `large-v3` |

Penjelasan lengkap, termasuk catatan untuk Mac, ada di **[docs/KEBUTUHAN-HARDWARE.md](docs/KEBUTUHAN-HARDWARE.md)**.

Untuk mengecek apakah sebuah komputer siap, jalankan (tidak perlu instalasi apa pun selain Python):

```bash
python tools/cek_hardware.py
```

## Struktur repo

```
tukang-catat/
├── README.md
├── docs/
│   └── KEBUTUHAN-HARDWARE.md   # catatan spesifikasi komputer
└── tools/
    └── cek_hardware.py         # skrip pengecek kesiapan hardware
```
