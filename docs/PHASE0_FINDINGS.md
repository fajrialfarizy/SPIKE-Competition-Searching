# PHASE 0 FINDINGS & TECHNICAL VERIFICATION REPORT
## Instagram Competition Monitoring & Curation System

Dokumen ini memuat hasil verifikasi teknis, batas kuota aktual, karakteristik actor Apify, spesifikasi Gemini API, dan batasan Google Sheets.

> **Catatan Status Verifikasi**: Parameter skema diambil langsung dari dokumentasi API & skema input/output resmi Apify Store. Untuk verifikasi langsung terhadap token akun pengguna di Apify, notebook uji terisolasi telah disediakan di `notebooks/test_apify_actors_phase0.ipynb`.

---

## 1. Verifikasi Actor Apify & Analisis Kuota

### 1.1 Actor Akun Tetap: `apify/instagram-scraper`
- **Status Input/Output**: [DOKUMENTASI RESMI APIFY STORE]
- **Parameter Input Terverifikasi**:
  - `directUrls`: array URL profil (mis. `["https://www.instagram.com/lombabisnis/"]`)
  - `resultsType`: `"posts"` (bukan reels/details)
  - `resultsLimit`: integer (mis. 20)
  - `onlyPostsNewerThan`: string tanggal (`YYYY-MM-DD` atau ISO UTC).
- **Struktur Field Output**:
  - `id`: string ID numerik Instagram (media ID)
  - `shortCode`: string alphanumeric shortcode (e.g. `DdG4RIxIPyf`)
  - `url`: URL lengkap Instagram (`https://www.instagram.com/p/.../`)
  - `caption`: string teks lengkap caption
  - `timestamp`: string ISO 8601 UTC
  - `ownerUsername`: string username akun
- **Karakteristik Tanggal & Pinned Post**:
  - Tidak memiliki parameter batas atas tanggal (`until`). Paginasi bertahap Initial Scraping dilakukan dengan pembatasan `resultsLimit` dan deduplikasi Post ID.
  - Pinned post tetap dikembalikan meskipun lebih tua dari `onlyPostsNewerThan`. **Python wajib memfilter ulang**: `post.posted_at >= cutoff_date`.

### 1.1b Biaya Pagination Akun (BELUM TERVERIFIKASI)
- Pagination akun saat ini meminta 20, lalu 40, lalu 60 hasil, dan setiap langkah adalah satu actor run berbayar. Akun dengan 45 post kira-kira ditagih untuk ~120 hasil.
- Hipotesis: satu permintaan besar (`resultsLimit` besar) bisa lebih murah karena actor umumnya hanya mengembalikan post yang ada di dalam window tanggal. **Ini belum diukur.**
- Untuk mengukurnya tersedia sel opt-in di `notebooks/test_apify_actors_phase0.ipynb` bagian 4: satu akun, cutoff `onlyPostsNewerThan` 60 hari yang sama, dua run (`resultsLimit` 20 vs `MAX_TEST_RESULTS`), mencetak jumlah item mentah, item di dalam window, item lebih tua (pinned), `usageTotalUsd` + breakdown bila tersedia, dan biaya per item.
- Status: `Not tested (needs real services)`. Tidak ada angka biaya yang diklaim di dokumen ini sampai pemilik proyek menempelkan output nyata.

### 1.2 Actor Keyword: `seemuapps/instagram-keyword-posts-scraper`
- **Status Input/Output**: [DOKUMENTASI RESMI APIFY STORE]
- **Parameter Input Terverifikasi**:
  - `keywords`: array string frasa pencarian (mis. `["lomba business plan"]`)
  - `maxPostsPerKeyword`: integer
  - `postType`: `"all"`
  - `dateFrom`: string tanggal (`YYYY-MM-DD`)
  - `dateTo`: string tanggal (`YYYY-MM-DD`)
  - `pageId`: cursor string (`NEXT_PAGE_ID` untuk kelanjutan)
- **Struktur Field Output**:
  - `postId`: string ID numerik
  - `shortcode`: string alphanumeric shortcode
  - `url`: string URL
  - `caption`: string caption
  - `takenAt`: string timestamp ISO 8601 UTC
  - `ownerUsername`: string username pemilik
  - `keyword`: string keyword terkait
- **Karakteristik & Urutan**:
  - Hasil diurutkan oleh Instagram berdasarkan **relevansi, bukan tanggal**. Filter rentang tanggal ketat diverifikasi di Python.
- **Analisis Dampak Kuota Free Tier (50 Post/Run & 3 Run/Hari)**:
  - Batasan paket gratis actor: Maksimal **50 post per run** dan **3 run per hari**.
  - **Hitungan Beban**:
    - 10 Keyword Core $\times$ 20 hasil = 200 post.
    - 7 Keyword Discovery $\times$ 20 hasil = 140 post.
    - Total = 340 post jika dijalankan penuh sekaligus.
  - **Dampak & Solusi**:
    1. Initial Scraping keyword tidak dapat diselesaikan dalam 1 hari di paket free tier.
    2. Sistem membatasi scraping keyword bertahap sesuai prioritas (Core sebelum Discovery).
    3. Jika kuota harian tercapai atau batas 100 post tercapai, sisa keyword yang belum diproses dicatat ke `pending_ranges` di Run State dengan status `Partial`.
    4. Admin dapat melanjutkan kembali keesokan harinya secara mulus tanpa kehilangan state.
    5. Actor dapat menerima beberapa keyword sekaligus dalam 1 run (`keywords: [...]`), namun untuk kepatuhan batas 50 post/run, jumlah keyword per run dibatasi maksimal 2 keyword per panggilan jika memakai limit 20 post.

---

## 2. Verifikasi Gemini API

- **Model ID**: `gemini-3.5-flash-lite` (ditetapkan di sheet `Config`, tidak di-hardcode).
- **Aturan Non-Fallback**: Tidak ada pergantian provider atau model otomatis tanpa izin/pengubahan di sheet `Config`.
- **SDK**: Menggunakan SDK resmi modern `google-genai` (Package dipin: `google-genai==1.2.0`).
- **Structured Output**: Menggunakan `response_mime_type="application/json"` dan `response_schema`.
- **Penanganan Error AI**:
  - Jika JSON corrupt/rusak: Retry maksimal 3 kali dengan backoff+jitter. Jika tetap gagal, status post di System Log menjadi **Failed** (BUKAN Ineligible, BUKAN Success).
  - API Key salah: Berhenti seketika tanpa retry sia-sia.
  - Rate limit (429): Menjalankan exponential backoff dan jitter.
- **Proteksi Privasi**: Hanya caption dan konteks tanggal (`posted_at` dan tanggal hari ini WIB) yang dikirim ke Gemini.

---

## 3. Verifikasi Google Sheets API & Google Colab

- **Library & Versi**: `gspread==6.1.4` (dipin di `requirements.txt`).
- **Metode Update**: Menggunakan sintaks keyword-argument eksplisit (`ws.update(range_name=..., values=..., value_input_option='RAW')`) untuk kompatibilitas penuh gspread v6+.
- **Formula Kolom D**:
  - Menggunakan satu `ARRAYFORMULA` dinamis yang membaca langsung sheet **Grade Rules**, **Master Top University**, dan **Master Top Organizer** (tidak ada aturan yang di-hardcode di dalam string formula).
  - Kolom D dilewati pada penulisan Python (`append_competition_records` hanya menulis A:C dan E:O).
- **Proteksi Overwrite & Gap Baris**:
  - Selalu mendeteksi baris terisi terbawah (`bottom_most_filled_row`).
  - Data admin yang memiliki baris kosong di atasnya tidak akan tertimpa.
