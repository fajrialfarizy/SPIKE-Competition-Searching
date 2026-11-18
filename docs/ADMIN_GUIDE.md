# PANDUAN ADMINISTRATOR (ADMIN GUIDE)
## Instagram Competition Monitoring & Curation System

Dokumen ini merupakan panduan operasional lengkap bagi admin non-teknis sesuai dengan 10 butir kebutuhan PRD Bagian 13.

---

### 1. Cara Mengisi & Mengubah Konfigurasi (`Config`)
Sheet **Config** menyimpan parameter operasional sistem. Admin dapat mengubah nilai di kolom `Value` tanpa menyentuh kode:
- `max_new_posts_per_run`: Jumlah maksimal post baru per eksekusi (default: 100).
- `max_results_per_input`: Jumlah postingan per akun/keyword dalam 1 panggilan actor (default: 20). Nilai ini juga menjadi langkah pagination.
- `max_results_ceiling_per_source`: Batas atas pagination per sumber (default: 200). Jika sebuah sumber masih menghasilkan data sampai batas ini, sumber tersebut dicatat di `pending_ranges` (Run State) dan eksekusi menjadi `Partial`.
- `gemini_delay_seconds`: Jeda sequential antar-panggilan Gemini (default: 6 detik).
- `gemini_model`: Model AI yang digunakan (default: `gemini-3.5-flash-lite`).
- `max_failed_run_attempts`: Berapa kali satu post boleh gagal ekstraksi sebelum tidak diulang otomatis (default: 3). Lihat bagian 8.
- `account_start_limit_initial`: `resultsLimit` **pertama** untuk Source Accounts pada Initial Scraping (default: 20). Nilai di atas `max_results_ceiling_per_source` otomatis dibatasi ke ceiling tersebut.
- `account_start_limit_routine`: Sama seperti di atas, khusus Routine Scraping (default: 20).

> Catatan biaya Apify (belum terverifikasi): pagination akun menaikkan `resultsLimit` per langkah (20, 40, 60, ...), dan setiap langkah adalah satu actor run berbayar. Eksperimen biaya tersedia di sel opt-in `notebooks/test_apify_actors_phase0.ipynb` (bagian 4). **Setelah** pengukuran nyata menunjukkan permintaan besar tidak lebih mahal, admin dapat menaikkan `account_start_limit_initial` (mis. 100) tanpa mengubah kode; langkah eskalasi dan ceiling tetap sama.

### 2. Cara Mengisi & Mengelola `Source Accounts`
Buka sheet **Source Accounts**:
- Masukkan username Instagram pada kolom A (tanpa tanda `@`).
- Akun yang terdaftar akan otomatis di-scrape saat Initial dan Routine Scraping.
- Menghapus akun dari sheet ini **tidak akan menghapus riwayat postingan** yang sudah tercatat di System Log.

### 3. Cara Menambah & Mengubah Keyword (`Search Keywords`)
Buka sheet **Search Keywords**:
- Tambah baris baru dengan mengisi kolom `Keyword`, `Category` (`Core` atau `Discovery`), `Priority` (`High`/`Medium`/`Low`), `Active Status` (`Aktif`), dan `Frequency` (angka bulat $N$).
- Keyword tidak boleh mengandung tanda koma (`,`).
- Kolom `Frequency` = $N$ berarti keyword akan dijalankan setiap $N$ kali eksekusi Routine yang berstatus `Success`.

### 4. Cara Menjalankan Initial Scraping
1. Buka notebook `notebooks/01_initial_scraping.ipynb` di Google Colab.
2. Pastikan Secrets (`SPREADSHEET_ID`, `APIFY_API_TOKEN`, `GEMINI_API_KEY`) sudah terisi di Colab.
3. Jalankan sel secara berurutan. Admin dapat mengisi form jika hanya ingin menguji akun/keyword tertentu.
4. Tunggu hingga ringkasan eksekusi muncul. `global_checkpoint` diinisialisasi begitu cakupan scraping run ini lengkap (lihat bagian 9b): minimal satu sumber harus selesai di-scrape, sementara sumber lain boleh tercatat di `pending_ranges`. Status `Partial` karena kegagalan ekstraksi Gemini tidak menahan checkpoint.

### 5. Cara Menjalankan Routine Scraping
1. Buka notebook `notebooks/02_routine_scraping.ipynb` di Google Colab.
2. Jalankan sel dari atas ke bawah.
3. Sistem akan membaca `global_checkpoint`, mengambil postingan terbaru dari akun dan keyword yang jatuh tempo, menyimpannya ke System Log, dan memproses lomba baru ke Competition Report.

### 6. Cara Menjalankan Keyword Evaluation
1. Dijalankan setiap 4 minggu sekali melalui `notebooks/03_keyword_evaluation.ipynb`.
2. Notebook akan mengevaluasi seluruh post berstatus `Success` di System Log terhadap kurasi admin di Competition Report.
3. Menghitung `Relevance Rate` kumulatif dan menampilkan rekomendasi kandidat frasa baru.

### 7. Cara Mengisi Competition Report Secara Manual
Admin dapat langsung mengetikkan kompetisi baru di baris bawah **Competition Report**:
- Kolom yang wajib diisi: `Nama Lomba`, `Penyelenggara`, `Skala`, `Pelaksanaan Final`, `Deadline Pendaftaran`.
- **JANGAN mengisi atau mengedit Kolom D (Grade Lomba)** karena terisi otomatis oleh formula sistem.
- Jika Competition ID belum terisi, sistem akan memberikan nomor urut baru secara aman pada run berikutnya.

### 8. Cara Menangani Postingan Berstatus `Failed` (D17)
- Postingan yang gagal diproses AI akan memiliki `Processing Status = Failed` di System Log beserta alasannya di `Error Notes`, diakhiri marker jumlah percobaan, mis. `Extraction Error: ... [attempts=2]`.
- Sistem mencoba ulang post `Failed` secara otomatis pada run berikutnya, tetapi **paling banyak `max_failed_run_attempts` run** (default 3).
- Setelah batas itu, post menjadi **dead**: tetap `Failed`, tidak diproses otomatis lagi, tidak memakai kuota 100 post, tidak dihitung sebagai `Failed Posts`, dan tidak lagi membuat run menjadi `Partial`. Execution Log akan menulis mis. `2 posts permanently failed (attempts>=3), see System Log`.
- Penyebab permanen seperti API key salah atau kuota harian Gemini **tidak** menambah attempt; post tetap `Pending` dan akan diproses pada run berikutnya.
- **Memaksa coba ulang (manual):** di System Log, ubah `Processing Status` post tersebut menjadi `Pending` dan kosongkan `Error Notes` (atau hapus bagian `[attempts=N]`). Post itu akan diproses lagi pada run berikutnya.
- Error Google Sheets tidak pernah menghitung attempt: error tersebut di-retry otomatis, atau bila tetap gagal, run berhenti fatal dan status/attempt post tidak berubah.

### 9. Cara Membaca `Execution Log`
Sheet **Execution Log** mencatat riwayat setiap eksekusi:
- `Execution ID`: Kode unik per run (format `EXE-YYYYMMDD-HHMMSS-<notebook>`).
- `Execution Status`:
  - `Success`: Semua sumber selesai tanpa kendala.
  - `Partial`: Sebagian sumber gagal, batas kapasitas 100 post baru tercapai, ada sumber yang belum tuntas (tercatat di `pending_ranges`), ada post yang gagal diproses, atau proses dihentikan karena error permanen. Sumber yang tercatat di `pending_ranges` akan dijalankan lebih dulu pada eksekusi berikutnya.
  - `Failed`: Eksekusi gagal total.
- `Errors / Notes`: alasan ringkas, termasuk jumlah sumber yang tertunda dan jumlah post yang permanen gagal.
- `Latest Successful Posting Timestamp`: Waktu postingan terbaru yang berhasil diambil.

### 9b. Arti `global_checkpoint` (D18)
- `global_checkpoint` menunjukkan **sampai kapan scraping sudah tercakup**, bukan "semua post berhasil diekstrak".
- Checkpoint maju bila seluruh sumber run ini selesai di-scrape atau tercatat di `pending_ranges` (dengan tanggal `from` masing-masing), tidak ada entri `pending_ranges` yang terbuang, semua post baru sudah tersimpan di System Log, dan run bukan outage total.
- Kegagalan ekstraksi Gemini, post yang menjadi dead, dan halt karena error permanen **tidak** menahan checkpoint: post tersebut diambil ulang dari System Log tanpa scraping ulang (hemat kredit Apify).
- Checkpoint **tidak** maju bila pada run itu tidak ada satu pun sumber yang selesai di-scrape: semua sumber error, atau satu-satunya sumber mentok `max_results_ceiling_per_source`, atau tertunda kuota 100 post. Sumber tersebut tercatat di `pending_ranges` dan dijalankan pertama pada run berikutnya; bila penyebabnya ceiling, naikkan `max_results_ceiling_per_source` agar suatu run bisa menyelesaikan minimal satu sumber.
- Nilai checkpoint adalah waktu MULAI run. Post yang terbit saat run berjalan akan diambil run berikutnya; Post-ID dedup membuat post yang sudah dikenal tidak memakan kuota.
- `keyword_run_counter` (untuk Frequency) naik setiap kali checkpoint maju, termasuk pada run `Partial` yang setidaknya satu sumbernya selesai.
- D13 tetap berlaku: Routine menolak berjalan bila checkpoint masih kosong.

### 10. Recovery Bila Runtime Colab Terputus
Jika koneksi internet atau runtime Google Colab terputus di tengah jalan:
1. Tidak ada data yang hilang: seluruh postingan mentah sudah tersimpan di System Log sebelum AI dipanggil.
2. Sambungkan kembali runtime Colab.
3. Jalankan ulang notebook yang sama dari awal.
4. Sistem akan mendeteksi postingan yang masih `Pending` atau `Failed` (yang belum mencapai batas attempt) dan melanjutkannya secara aman.

### 11. Catatan Kuota Google Sheets
Sistem menghemat kuota Google Sheets (sekitar 60 baca dan 60 tulis per menit): handle worksheet di-cache, setiap post hanya memakai beberapa panggilan API, dan error `429` / `RESOURCE_EXHAUSTED` / `Quota exceeded` ditunggu dengan jeda 15 detik, 30 detik, lalu 60 detik sebelum dicoba lagi. Admin tidak perlu melakukan apa pun; cukup biarkan notebook berjalan.

