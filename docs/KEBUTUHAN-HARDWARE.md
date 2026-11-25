# Kebutuhan Hardware Tukang Catat

Tukang Catat bisa dipasang di komputer dengan spesifikasi yang berbeda-beda. Dokumen ini menjelaskan apa yang dibutuhkan, dan bagaimana aplikasi menyesuaikan diri dengan komputer yang tersedia.

## Ringkasan cepat

Jalankan skrip ini di komputer tujuan untuk mendapatkan rekomendasi otomatis:

```bash
python tools/cek_hardware.py
```

## Dua beban kerja yang berbeda

Tukang Catat sengaja memisahkan pekerjaannya menjadi dua bagian, karena kebutuhan hardware-nya sangat berbeda.

| Tahap | Pekerjaan | Beban |
|---|---|---|
| **Saat rapat** (real-time) | Merekam, mendeteksi suara, mengenali pembicara | **Ringan**: lancar di laptop biasa tanpa GPU |
| **Setelah rapat** | Transkripsi seluruh rekaman dengan Whisper | **Berat**: kecepatannya sangat bergantung pada CPU/GPU |

Artinya, **komputer yang lambat pun tetap bisa dipakai saat rapat**. Bedanya hanya pada berapa lama pengguna harus menunggu transkrip jadi setelah rapat selesai.

## Tingkat spesifikasi

### Minimum
- CPU: 4 core, x86-64 (Intel/AMD, 2015 ke atas) atau Apple Silicon
- RAM: 8 GB
- Disk kosong: 10 GB
- Model Whisper: `small`
- Perkiraan: ~15–30 menit transkripsi untuk 1 jam rapat. Akurasi cukup, tetapi lebih sering salah pada istilah teknis dan nama.

### Direkomendasikan
- CPU: 8 core atau lebih (mis. Intel Core i5/i7 gen 11+, AMD Ryzen 5/7 5000+, Apple M1 ke atas)
- RAM: 16 GB
- Disk kosong: 20 GB
- Model Whisper: `medium` atau `large-v3-turbo`
- Perkiraan: ~20–45 menit transkripsi untuk 1 jam rapat. Akurasi baik untuk bahasa Indonesia dan campuran Indonesia–Inggris.

### Optimal
- GPU NVIDIA dengan VRAM 6 GB atau lebih (mis. RTX 3060, RTX 4060) dan driver CUDA terbaru
- RAM: 16 GB
- Model Whisper: `large-v3`
- Perkiraan: ~3–6 menit transkripsi untuk 1 jam rapat. Akurasi terbaik.

> Semua perkiraan waktu bersifat kasar dan akan diperbarui setelah pengukuran nyata pada prototipe.

## Ukuran model Whisper

Model diunduh sekali saat pertama dipakai, lalu disimpan di komputer.

| Model | Ukuran unduhan | RAM terpakai (CPU, int8) | Akurasi bahasa Indonesia |
|---|---|---|---|
| `tiny` | ~75 MB | ~0,5 GB | Rendah |
| `base` | ~140 MB | ~0,7 GB | Rendah |
| `small` | ~470 MB | ~1 GB | Cukup |
| `medium` | ~1,5 GB | ~2–3 GB | Baik |
| `large-v3-turbo` | ~1,6 GB | ~2–3 GB | Sangat baik |
| `large-v3` | ~3 GB | ~4–5 GB | Terbaik |

## Catatan per sistem operasi

### Windows
- Windows 10 atau 11, 64-bit.
- GPU NVIDIA akan dipakai otomatis jika tersedia. GPU Intel/AMD terintegrasi **tidak** dipakai untuk transkripsi, jadi transkripsi berjalan di CPU.

### macOS
- Apple Silicon (M1/M2/M3/M4) sangat disarankan. Mac Intel tetap bisa dipakai tetapi lebih lambat.
- faster-whisper berjalan di CPU pada Mac dan tidak memakai GPU Apple. Untuk Mac, opsi mesin alternatif yang memakai GPU Apple (mis. whisper.cpp dengan Metal, atau mlx-whisper) akan dipertimbangkan.
- Browser akan meminta izin mikrofon saat pertama kali merekam.

### Linux
- Distribusi 64-bit modern (Ubuntu 22.04+ atau setara).
- GPU NVIDIA dengan CUDA didukung.

## Perangkat lunak pendukung
- **Python 3.10 atau lebih baru**
- **ffmpeg** untuk membaca dan mengonversi file audio
- **Browser modern**: Chrome, Edge, Firefox, atau Safari versi terbaru

## Mikrofon: sering lebih menentukan daripada komputer

Kualitas rekaman sangat memengaruhi akurasi transkripsi **dan** pengenalan pembicara.
- **Terbaik**: mikrofon konferensi (omnidirectional) di tengah meja.
- **Cukup**: mikrofon USB di dekat peserta.
- **Kurang**: mikrofon laptop bawaan di ujung ruangan, karena suara peserta yang jauh menjadi pelan dan pengenalan pembicara lebih sering keliru.
- Untuk rapat online (Zoom/Meet), rekam audio sistem atau gunakan file rekaman dari aplikasi rapatnya.

## Contoh: komputer pengembangan

| Komponen | Spesifikasi |
|---|---|
| CPU | Intel Core i5-12600H (12 core / 16 thread) |
| RAM | 24 GB |
| GPU | Intel Iris Xe (terintegrasi, tidak dipakai untuk transkripsi) |
| Hasil `cek_hardware.py` | **DIREKOMENDASIKAN**: `medium` / `large-v3-turbo` di CPU |
