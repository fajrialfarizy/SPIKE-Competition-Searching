# PANDUAN ADMINISTRATOR (ADMIN GUIDE)
## Instagram Competition Monitoring & Curation System

Dokumen ini merupakan panduan operasional lengkap bagi admin non-teknis sesuai dengan 10 butir kebutuhan PRD Bagian 13.

---

### 1. Cara Mengisi & Mengubah Konfigurasi (`Config`)
Sheet **Config** menyimpan parameter operasional sistem. Admin dapat mengubah nilai di kolom `Value` tanpa menyentuh kode:
- `max_new_posts_per_run`: Jumlah maksimal post baru per eksekusi (default: 100).
- `max_results_per_input`: Jumlah postingan per keyword dalam 1 panggilan actor (default: 20), dan juga langkah eskalasi `maxPostsPerKeyword` pada jendela satu hari. Untuk Source Accounts nilainya hanya menjadi `resultsLimit` pertama bila `account_start_limit_*` tidak diisi; eskalasi akun tidak lagi memakai kelipatan nilai ini (lihat catatan biaya di bawah).
- `max_results_ceiling_per_source`: Batas atas pagination per sumber (default: 200). Jika sebuah sumber masih penuh sampai batas ini, nilai ceiling tersebut dicatat di alasan entri `pending_ranges` (mis. `ceiling=300`), entri tetap terdaftar di Run State, dan eksekusi menjadi `Partial`. Sumber itu **tidak** di-scrape ulang pada eksekusi berikutnya sampai nilai ini dinaikkan (lihat catatan biaya di bawah).
- `gemini_delay_seconds`: Jeda sequential antar-panggilan Gemini (default: 6 detik).
- `gemini_model`: Model AI yang digunakan (default: `gemini-3.5-flash-lite`).
- `max_failed_run_attempts`: Berapa kali satu post boleh gagal ekstraksi sebelum tidak diulang otomatis (default: 3). Lihat bagian 8.
- `account_start_limit_initial`: `resultsLimit` **pertama** untuk Source Accounts pada Initial Scraping (default: 20). Nilai di atas `max_results_ceiling_per_source` otomatis dibatasi ke ceiling tersebut.
- `account_start_limit_routine`: Sama seperti di atas, khusus Routine Scraping (default: 20).

> **Catatan biaya Apify (Fix 8).** Pengukuran menunjukkan biaya per item yang dikembalikan **naik** seiring kedalaman halaman (\$0,00162/item pada 20 hasil, \$0,00243/item pada 100 hasil), dan setiap langkah eskalasi mengulang scraping dari awal — jadi 20/40/60/... adalah jalur paling mahal. Karena itu pagination akun sekarang:
> 1. Menjalankan `account_start_limit_*` lebih dulu. Bila hasil mentahnya **penuh** (jumlah item == `resultsLimit`), permintaan kedua **langsung** memakai `max_results_ceiling_per_source`. Maksimal **2 actor run per akun** per eksekusi, bukan 20/40/60/....
> 2. Bila sumber masih penuh sampai ceiling, entri `pending_ranges`-nya menyimpan nilai ceiling yang tercapai (mis. `ceiling=300`). Pada eksekusi berikutnya sumber itu **tidak di-scrape ulang** (tidak memakan kredit) selama `max_results_ceiling_per_source` belum dinaikkan; entri tetap ada di `pending_ranges` dan `Execution Log` menulis `ceiling reached for <account>, raise max_results_ceiling_per_source in Config`. Entri seperti ini juga **tidak** menahan `global_checkpoint`.
> 3. Untuk mengambil sisanya, naikkan `max_results_ceiling_per_source` di atas nilai yang tercatat. Sumber tersebut otomatis dijalankan **pertama** (dengan tanggal `from` aslinya) dan entrinya dihapus begitu selesai.
>
> Nilai Config yang direkomendasikan berdasarkan pengukuran tersebut:
>
> | Key | Value |
> |---|---|
> | `account_start_limit_initial` | `200` |
> | `account_start_limit_routine` | `40` |
> | `max_results_ceiling_per_source` | `300` |
>
> Dengan nilai ini Initial Scraping memakai paling banyak 2 run per akun (200 lalu 300) dan Routine tetap murah (40, lalu 300 hanya bila 40 pertama sudah penuh). Eksperimen biaya dapat diulang di sel opt-in `notebooks/test_apify_actors_phase0.ipynb` (bagian 4).

### 1b. Formula, Locale Spreadsheet, dan Format Tanggal
- Formula `Competition Report!D2` (Grade Lomba) dan sel bantu pola alias `Grade Rules!G2:G3` ditulis oleh notebook **00** mengikuti **locale spreadsheet**: pada locale Indonesia (`id_ID`) pemisah argumennya `;`, pada locale AS/Inggris `,`. Kode tetap memakai sintaks kanonik (koma); penerjemahan hanya terjadi saat menulis. **Locale spreadsheet cukup dibaca, tidak diubah dan tidak perlu diubah.**
- Setiap formula dibaca kembali setelah ditulis. Bila sel menampilkan `#ERROR!`, `#NAME?`, `#N/A`, atau `#REF!`, sel dicoba sekali lagi dengan pemisah lainnya. Bila tetap gagal, notebook **berhenti dengan pesan error** yang menyebut nama sel, nilai error, locale, dan pemisah yang dicoba (bukan peringatan yang dibiarkan berlalu), dan `D2` **tidak** dipasang selama sel bantu masih error.
- **Regenerasi sel bantu:** kosongkan `Grade Rules`!`G2:G3` (kosongkan juga `F2:F3` bila labelnya ikut rusak), lalu jalankan ulang sel **2. Jalankan Inisialisasi Template** di `notebooks/00_build_sps_template.ipynb`. Pola akan ditulis dengan pemisah yang benar untuk locale tersebut.
- **`D2` tidak bergantung pada named range.** Formula `D2` merujuk sel bantu secara langsung (`'Grade Rules'!$G$2` dan `'Grade Rules'!$G$3`), jadi tidak ada named range yang perlu dibuat dan `D2` tidak bisa kosong karena `addNamedRange` ditolak. `D2` dipasang bila kosong atau bila masih berisi formula hasil sistem (termasuk versi lama yang memakai named range); formula buatan admin tidak pernah ditimpa.
- **Tabel status di akhir sel 00.** Setiap kali sel 00 selesai, tampil ringkasan: locale spreadsheet, pemisah formula, status sel bantu `G2`/`G3` (ok/error), dan status `D2` (`terpasang`, `disegarkan`, atau `tidak diubah` beserta alasannya). Bila `G2:G3` masih error, sel 00 berhenti dengan pesan error dan `D2` **tidak** dipasang.
- **Grade janggal (mis. BINUS/BINUS menjadi Professional):** penyebabnya perbandingan boolean dengan angka (`REGEXMATCH(...)>0`) pada formula lama, di mana `FALSE>0` bernilai TRUE sehingga setiap baris dianggap Top Organizer. Sudah diperbaiki; jalankan ulang sel 00 agar `D2` versi lama diganti.
- Formula `D2` buatan admin **tidak pernah ditimpa**. Formula hasil sistem hanya ditulis ulang bila isinya benar-benar berbeda (termasuk bila pemisahnya masih salah), jadi menjalankan ulang sel 00 aman (idempoten). Mengubah tabel `Grade Rules` (nilai Grade/Skala/Mode/Priority) tetap membutuhkan menjalankan ulang sel tersebut karena urutan cabang dihasilkan dari Priority.
- **Tanggal yang diketik admin** di `Competition Report` (kolom `Tanggal Mulai Registrasi` / `Deadline Pendaftaran`): gunakan `YYYY-MM-DD` (paling aman) atau `dd/mm/yyyy` (mis. `01/12/2026` berarti 1 Desember 2026). Sistem menormalkan kedua format sebelum membandingkan dengan hasil AI, sehingga baris duplikat tidak dibuat; format yang tidak bisa dibaca dianggap berbeda dan tidak pernah menggagalkan run.
- **Zona waktu spreadsheet hanya diperiksa.** Bila bukan `Asia/Jakarta`, sel 00 menampilkan peringatan dalam bahasa Indonesia tetapi **tidak mengubahnya**; tanggal/jam yang ditulis sistem tetap memakai zona waktu dari sheet `Config`.

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

### 3b. Cara Membangun / Memeriksa Template Spreadsheet (`00_build_sps_template`)
Jika spreadsheet baru dibuat atau ingin memastikan ke-11 sheet dan formula terpasang dengan benar:
1. Buka notebook `notebooks/00_build_sps_template.ipynb` di Google Colab.
2. Jalankan sel autentikasi dan masukkan ID Spreadsheet Anda.
3. Jalankan sel inisialisasi template: sistem akan memeriksa dan membuat sheet yang belum ada, mengatur format teks Post ID, dropdown Status Pemeriksaan, dan proteksi kolom.
4. Sheet yang sudah memiliki data tidak akan dihapus atau ditimpa (*append-safe*).

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
- Checkpoint **tidak** maju bila pada run itu tidak ada satu pun sumber yang selesai di-scrape: semua sumber error, atau satu-satunya sumber mentok `max_results_ceiling_per_source`, atau tertunda kuota 100 post. Sumber tersebut tercatat di `pending_ranges` dan dijalankan pertama pada run berikutnya.
- Bila penyebabnya ceiling, run berikutnya **tidak** mengulang scrape sumber itu selama `max_results_ceiling_per_source` belum dinaikkan (hemat kredit), dan sumber yang di-skip tersebut tidak lagi menahan checkpoint. Naikkan nilainya agar sumber itu dijalankan pertama dan entrinya dibersihkan.
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

