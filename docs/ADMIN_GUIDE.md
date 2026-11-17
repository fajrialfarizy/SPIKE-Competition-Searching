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
4. Tunggu hingga ringkasan eksekusi muncul dengan status `Success`. Ini akan menginisialisasi `global_checkpoint`.

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

### 8. Cara Menangani Postingan Berstatus `Failed`
- Postingan yang gagal diproses AI akan memiliki `Processing Status = Failed` di System Log beserta alasannya di `Error Notes`.
- Admin tidak perlu mengedit apa pun; pada eksekusi Routine berikutnya, sistem akan secara otomatis mencoba memproses ulang semua post yang berstatus `Failed` atau `Pending`.

### 9. Cara Membaca `Execution Log`
Sheet **Execution Log** mencatat riwayat setiap eksekusi:
- `Execution ID`: Kode unik per run (format `EXE-YYYYMMDD-HHMMSS-<notebook>`).
- `Execution Status`:
  - `Success`: Semua sumber selesai tanpa kendala.
  - `Partial`: Sebagian sumber gagal, batas kapasitas 100 post baru tercapai, ada sumber yang belum tuntas (tercatat di `pending_ranges`), atau proses dihentikan karena error permanen (checkpoint lama dipertahankan). Sumber yang tercatat di `pending_ranges` akan dijalankan lebih dulu pada eksekusi berikutnya.
  - `Failed`: Eksekusi gagal total.
- `Latest Successful Posting Timestamp`: Waktu postingan terbaru yang berhasil diambil.

### 10. Recovery Bila Runtime Colab Terputus
Jika koneksi internet atau runtime Google Colab terputus di tengah jalan:
1. Tidak ada data yang hilang: seluruh postingan mentah sudah tersimpan di System Log sebelum AI dipanggil.
2. Sambungkan kembali runtime Colab.
3. Jalankan ulang notebook yang sama dari awal.
4. Sistem akan mendeteksi postingan yang masih `Pending` atau `Failed` dan melanjutkannya secara aman.

