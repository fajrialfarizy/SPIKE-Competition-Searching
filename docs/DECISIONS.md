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

---

### D17 & D18 (Round 2): Keputusan yang Disetujui Pemilik Proyek

> Catatan penomoran: tabel D16 di atas juga memakai nomor D17/D18 untuk item teknis fase 0.
> Dua keputusan di bawah adalah keputusan pemilik proyek pada ronde perbaikan ini dan ditulis
> dengan akhiran "(Round 2)" agar tidak tertukar.

#### D17 (Round 2): Batas percobaan ekstraksi per post

| Aspek | Keputusan |
|---|---|
| Masalah | Satu post yang selalu gagal (mis. Gemini selalu mengembalikan JSON tidak valid) diulang di setiap run, sehingga setiap run `Partial` dan Routine tidak pernah mengejar ketertinggalan. |
| Batas | Post boleh gagal paling banyak `max_failed_run_attempts` run (Config, default 3). Setelah itu post menjadi **dead**: tetap `Failed`, tidak diproses otomatis lagi, tidak memakai kuota 100 post, tidak dihitung `failed_posts`, dan tidak memaksa `Partial`. |
| Penyimpanan counter | Tanpa kolom/sheet baru. Counter disimpan di akhir `Error Notes` System Log sebagai marker machine-readable `[attempts=N]` (`compmon/retry_policy.py`). |
| Yang dihitung | Hanya kegagalan Gemini/ekstraksi (error API setelah retry, JSON tidak valid, kegagalan validasi/skema). `GeminiPermanentError` (API key salah, kuota harian) **tidak** memakai attempt dan post tetap `Pending`. Error baca/tulis Google Sheets **tidak** memakai attempt: error tersebut di-retry oleh `_execute_with_retry` atau berhenti sebagai error fatal run. |
| Status run | Run di mana post mencapai kegagalan ke-N tetap `Partial`. Hanya run berikutnya yang bisa `Success` lagi. |
| Pelaporan | Execution Log `Errors / Notes` menyebut jumlah dead post, mis. `2 posts permanently failed (attempts>=3), see System Log`. |
| Pemulihan admin | Admin dapat memaksa percobaan ulang dengan mengubah `Processing Status` menjadi `Pending` dan menghapus marker `[attempts=N]` di `Error Notes` (lihat `docs/ADMIN_GUIDE.md`). |

#### D18 (Round 2): Checkpoint = cakupan scraping, bukan keberhasilan ekstraksi

**Keputusan ini secara eksplisit menimpa aturan lama "checkpoint hanya maju bila status eksekusi Success" (PRD 6.2 / 6.13, non-negotiable rule 6 pada brief pertama), dengan persetujuan pemilik proyek.**

| Aspek | Keputusan |
|---|---|
| Alasan | Apify dibayar per hasil. Run Routine mingguan yang hanya `Partial` karena satu post gagal Gemini membuat checkpoint tertahan, sehingga run berikutnya mengulang seluruh window yang makin panjang dan membayar Apify lagi untuk post yang sudah tersimpan. |
| Prinsip | Checkpoint menggambarkan **cakupan scraping**. Post mentah sudah ditulis ke System Log SEBELUM pemrosesan AI (rule 5), dan percobaan ulang ekstraksi membaca System Log (`Pending`, atau `Failed` dengan attempt < batas), jadi tidak perlu scraping ulang. |
| Syarat maju | Checkpoint maju hanya bila SEMUA terpenuhi: (a) setiap sumber yang dicoba pada run ini selesai di-scrape atau tercatat di `pending_ranges` dengan `from` sendiri; (b) merge `pending_ranges` tidak menjatuhkan entri (`dropped_entries == 0`); (c) setiap post baru benar-benar tersimpan di System Log; (d) bukan outage: bila `total_sources > 0` maka `completed_sources >= 1` — literal, minimal satu sumber harus SELESAI di-scrape pada run ini. Konsekuensi yang disengaja: run yang satu-satunya sumbernya tertunda kuota 100 post, atau mentok `max_results_ceiling_per_source`, TIDAK memajukan checkpoint walaupun post-nya tersimpan; sumber itu tercatat di `pending_ranges` dengan `from` aslinya dan dijalankan pertama pada run berikutnya. |
| Tidak lagi memblokir | Kegagalan ekstraksi Gemini, dead post (D17), halt `GeminiPermanentError`, dan post yang belum diproses karena cap pemrosesan (tetap `Pending`). |
| Nilai baru | Waktu MULAI run (UTC ISO), bukan waktu posting terbaru. Post yang terbit selama run berjalan dicakup run berikutnya; Post-ID dedup membuatnya gratis. |
| Status eksekusi | Definisi `Success`/`Partial`/`Failed` tidak berubah, hanya untuk pelaporan. `Partial` tidak lagi berarti checkpoint tetap. |
| Sumber belum selesai | Tetap di `pending_ranges` dengan `from` miliknya (cutoff yang dipakai run itu, bukan "hari ini"). Run berikutnya mengambil sumber tersebut lebih dulu, bukan seluruh window. |
| Frequency | `keyword_run_counter` naik setiap kali checkpoint maju, bukan hanya saat `Success`. |
| D13 | Tidak berubah: Routine tetap menolak berjalan bila checkpoint kosong. |

