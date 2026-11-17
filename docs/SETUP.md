# SETUP & DEPLOYMENT GUIDE
## Instagram Competition Monitoring & Curation System

Dokumen ini menjelaskan langkah-langkah persiapan dan pemasangan sistem dari awal (zero to running) bagi admin atau pengguna baru.

---

## 1. Prasyarat Akun & Layanan

1. **Akun Google**: Untuk mengakses Google Drive, Google Sheets, dan Google Colab.
2. **Akun Apify**: Daftar di [apify.com](https://apify.com). Akun gratis mendapatkan kuota platform compute $5/bulan.
3. **Akun Google AI Studio**: Dapatkan API Key gratis di [aistudio.google.com](https://aistudio.google.com).

---

## 2. Pembuatan Template Google Sheets

1. Buat satu file Google Spreadsheet baru di Google Drive Anda.
2. Salin ID Spreadsheet dari URL browser Anda (bagian antara `/d/` dan `/edit`).
3. Jalankan skrip `templates/build_template.py` untuk menginisialisasi ke-11 sheet secara otomatis:
   ```bash
   python -c "import gspread; from templates.build_template import populate_template; gc = gspread.oauth(); populate_template(gc, 'ID_SPREADSHEET_ANDA')"
   ```
4. Verifikasi bahwa 11 tab sheet telah terbuat dengan benar:
   - `Competition Report`
   - `System Log`
   - `Execution Log`
   - `Config`
   - `Run State`
   - `Source Accounts`
   - `Master Top University`
   - `Master Top Organizer`
   - `Grade Rules`
   - `Eligibility Rules`
   - `Search Keywords`

---

## 3. Konfigurasi Colab Secrets

Buka notebook di Google Colab dan konfigurasikan Secrets (panel kiri ikon kunci):
1. `SPREADSHEET_ID`: ID spreadsheet yang telah dibuat di langkah 2.
2. `APIFY_API_TOKEN`: Token API dari Apify Console.
3. `GEMINI_API_KEY`: API Key dari Google AI Studio.

Pastikan opsi **Notebook access** diaktifkan untuk ketiga secret tersebut.

---

## 4. Menjalankan Sistem

1. **Inisialisasi**: Buka `notebooks/01_initial_scraping.ipynb` dan jalankan semua sel untuk pengumpulan data awal.
2. **Operasional Rutin**: Jalankan `notebooks/02_routine_scraping.ipynb` secara mingguan (rekomendasi: setiap Jumat pukul 12.00 WIB).
3. **Evaluasi Keyword**: Jalankan `notebooks/03_keyword_evaluation.ipynb` setiap 4 minggu untuk meninjau relevansi kata kunci.

