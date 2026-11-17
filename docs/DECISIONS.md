# DECISIONS LOG
## Instagram Competition Monitoring & Curation System

Dokumen ini mencatat semua keputusan teknis dan desain yang disetujui atau diputuskan selama pengembangan proyek.

---

### D1–D15: Keputusan dari Brief Kerja (Sudah Disetujui)

| ID | Topik | Keputusan |
|---|---|---|
| D1 | Sheet Tambahan | Tambah sheet `Config`, `Run State`, `Grade Rules`, dan `Eligibility Rules`. Tiga sheet operasional (`Competition Report`, `System Log`, `Execution Log`) tidak berubah. |
| D2 | Deadline Berbeda | Buat baris baru dengan Competition ID baru. Catat "mirip ID xxx, deadline beda" di Error Notes System Log. Tidak menggabung dan tidak mengubah baris lama. |
| D3 | Atribut Kosong saat Matching | Kosong sama kosong dianggap cocok. Setiap penggabungan akibat atribut kosong dicatat di Error Notes System Log. |
| D4 | Lomba Sudah Tutup | Deadline < hari ini (WIB) = Eligibility Ineligible, Processing Status Success, hanya masuk System Log. |
| D5 | Format Competition ID | `CMP-000001`, berurutan. Angka terbesar di kolom Competition ID + 1. |
| D6 | Kolom Frequency | Bilangan bulat N: keyword dipakai setiap N kali eksekusi Routine yang Success. |
| D7 | Keyword Baru | Python (tanpa AI) menghitung n-gram frasa sering muncul. Disimpan dengan Active Status = `Kandidat`, tidak pernah aktif otomatis. |
| D8 | Evaluasi Keyword | Kumulatif sejak awal, bukan jendela 4 minggu. |
| D9 | Actor Keyword | `seemuapps/instagram-keyword-posts-scraper`. Dilarang cookie Instagram. |
| D10 | Actor Akun | `apify/instagram-scraper` dengan `directUrls`, `resultsType: posts`, `resultsLimit`, `onlyPostsNewerThan`. |
| D11 | Model Gemini | `gemini-3.5-flash-lite` (fallback/baseline `gemini-2.5-flash-lite`). Model ID di Config. |
| D12 | Tahun pada Deadline | Simpulkan tahun dari tanggal posting jika caption tidak mencantumkan tahun. Konteks tanggal posting dan hari ini (WIB) dikirim ke Gemini. |
| D13 | Routine Tanpa Checkpoint | Jika `global_checkpoint` kosong, Routine berhenti dan menyuruh admin jalankan Initial Scraping dulu. |
| D14 | `pending_ranges` | Disimpan di Run State sebagai JSON array objek `{source_type, source, from, to, reason}`. |
| D15 | Enum Search Keywords | Active Status: `Aktif`, `Nonaktif`, `Kandidat`. Evaluation Status: `Menunggu Evaluasi`, `Sudah Dievaluasi`. Category: `Core`, `Discovery`. Priority: `High`, `Medium`, `Low`. |

---

### D16: Keputusan Teknis Fase 0 (Desain Normalisasi & Autentikasi)

| ID | Topik | Keputusan & Alasan |
|---|---|---|
| D16 | Primary Post ID Normalization | Menggunakan **Instagram Shortcode** sebagai `Post ID` canonical internal jika terdapat inkonsistensi representasi ID numerik antara actor akun (`id` 64-bit int string) dan keyword scraper (`postId`). Shortcode dijamin unik per postingan Instagram, diekstrak dari URL atau field `shortCode`/`shortcode`, dan stabil lintas actor. |
| D17 | Autentikasi Google Sheets di Colab | Menggunakan `google.colab.auth.authenticate_user()` dipadukan dengan `gspread` (dan `google-api-python-client` jika diperlukan untuk batch format/protected range). Pilihan ini paling ramah admin tanpa mengharuskan admin membuat GCP Service Account JSON key yang rumit. Alternatif Service Account tetap didukung jika ada kredensial service account di Colab Secrets. |
| D18 | Gemini Python SDK | Menggunakan SDK resmi modern Google GenAI (`google-genai`) dengan Pydantic class `response_schema` dan `response_mime_type="application/json"` untuk ekstraksi structured output JSON anti-halusinasi. |
| D19 | Penanganan Truncation Caption | Google Sheets memiliki batas sel 50.000 karakter, sedangkan Instagram caption maksimal 2.200 karakter. Jika ada caption anomali > 45.000 karakter, potong pada karakter ke-45.000 dan tambahkan penanda di Error Notes. |

