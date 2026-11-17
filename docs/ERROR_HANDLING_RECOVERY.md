# PANDUAN PENANGANAN ERROR & RECOVERY
## Instagram Competition Monitoring & Curation System

Dokumen ini mendokumentasikan mekanisme penanganan kesalahan (error handling), ketahanan data (data integrity), dan prosedur pemulihan (recovery) sesuai PRD Bagian 12.

---

## 1. Penanganan Error Apify Scraping (PRD 12.1)

| Kondisi Error | Dampak | Penanganan Sistem |
|---|---|---|
| Timeout / Actor Gagal | Satu sumber tidak menghasilkan data | Sumber yang gagal dicatat ke `pending_ranges` di Run State. Sumber lain yang berhasil tetap disimpan. Eksekusi ditandai `Partial`. Checkpoint tidak bergeser. |
| Hasil Kosong | Tidak ada postingan baru ditemukan | Dicatat normal, proses berlanjut ke sumber berikutnya. |
| Kuota Bulanan Apify Habis | Request ditolak Apify API | Proses scraping berhenti aman. Data yang sudah terkumpul diproses ke System Log. Admin diberi pesan jelas untuk menunggu reset kuota. |

---

## 2. Penanganan Error Gemini API (PRD 12.2)

| Kondisi Error | Penanganan Sistem |
|---|---|
| Rate Limit (HTTP 429) | Sistem melakukan Exponential Backoff dengan jeda bertingkat: $6 \times 2^{(attempt-1)} + \text{jitter}$ (maksimal 3 kali retry). |
| Invalid JSON / Format Error | Dicoba ulang hingga 3 kali. Jika tetap gagal, `Processing Status` diset ke `Failed`, alasan dicatat di `Error Notes`, dan sisa postingan tetap diproses. Postingan ini akan dicoba ulang pada eksekusi berikutnya. |
| API Key Tidak Valid | Sistem mendeteksi error autentikasi permanen (`API_KEY_INVALID`) dan **segera menghentikan proses tanpa retry sia-sia**. Sisa postingan tetap berstatus `Pending`. |

---

## 3. Penanganan Error Google Sheets API (PRD 12.3)

| Kondisi Error | Penanganan Sistem |
|---|---|
| Batas Kuota Tulis (60 req/min) | Menggunakan Batch Writing (menyatukan penulisan multi-baris dalam 1 request HTTP) sehingga pemakaian kuota tulis sangat rendah (< 5 req/min). |
| Overwrite / Formula Damage | Seluruh penulisan kompetisi menggunakan pemisahan kolom eksplisit (A:C dan E:O). **Kolom D tidak pernah ditulisi oleh Python**, menjaga `ARRAYFORMULA` tetap utuh. |
| Adanya Gap Baris Kosong Admin | Sistem memindai seluruh kolom (A–O selain D) untuk mendeteksi baris terisi paling bawah. Baris baru selalu ditulis **setelah baris terbawah**, tidak pernah mengisi celah. |

---

## 4. State Machine & Recovery Runtime Terputus (PRD 12.4 & D2, D10)

Alur penulisan yang aman dari putus koneksi:
```
[Scraping Raw Post] 
       ↓
[Simpan ke System Log (Status: Pending)]  <-- RAW DATA SELAMAT
       ↓
[Panggilan Gemini Extractor]
       ↓
[Tulis ke Competition Report (Jika Baru)]
       ↓
[Update System Log (Status: Success)]
```

Jika runtime terputus di tengah jalan:
- **Terputus sebelum AI**: Postingan sudah ada di System Log dengan status `Pending`. Jalankan ulang notebook, postingan akan langsung diproses tanpa scraping ulang.
- **Terputus antara Competition Report dan System Log**: Record lomba sudah tertulis di Competition Report, namun System Log masih `Pending`. Saat notebook dijalankan ulang, modul matcher 4-atribut akan menemukan lomba yang sudah tertulis tersebut dan **tidak membuat duplikat**, melainkan langsung memperbarui status System Log menjadi `Success`.

