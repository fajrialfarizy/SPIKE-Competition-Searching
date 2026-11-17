# Instagram Competition Monitoring & Curation System

Sistem semi-otomatis berbasis Python, Apify, Gemini AI, dan Google Sheets untuk memonitor, menyaring, dan mengkurasi informasi kompetisi mahasiswa (Business Plan, Business Model Canvas, Business Case, Essay, dan Karya Tulis Ilmiah).

---

## 🌟 Fitur Utama

1. **Scraping Terjadwal & Selektif**: Mengambil postingan Instagram dari 4 akun tetap dan pencarian kata kunci bebas via Apify.
2. **Ekstraksi AI Tanpa Halusinasi**: Menggunakan Google Gemini API (`gemini-3.5-flash-lite` / `gemini-2.5-flash-lite`) dengan structured output JSON schema untuk mengekstrak informasi lomba dari caption saja.
3. **Integritas Data Append-Safe**: Tidak pernah menimpa atau menghapus data manual admin di Google Sheets. Memisahkan penulisan untuk melindungi formula `ARRAYFORMULA` di kolom Grade Lomba.
4. **Deduplikasi 2 Tingkat**: Deduplikasi tingkat postingan (Post ID/shortcode) dan pencocokan tingkat kompetisi (4 atribut: Nama Lomba, Penyelenggara, Deadline, Jenis Lomba).
5. **Evaluasi & Discovery Keyword**: Menghitung Relevance Rate kumulatif dan menemukan kata kunci kandidat baru tanpa AI.

---

## 📁 Struktur Repositori

```
compmon/                      # Paket Python inti
  __init__.py
  config.py                  # Validasi konfigurasi & parsing sheet Config
  sheets_repo.py             # Satu-satunya layer I/O ke Google Sheets
  apify_client.py            # Client actor Apify (akun & keyword)
  validator.py               # Validasi URL, shortcode, timestamp, caption
  dedup.py                   # Deduplikasi & merge atribusi sumber
  gemini_extractor.py        # Ekstraksi metadata via Gemini API
  eligibility.py             # Hierarki aturan eligibility & deadline
  matcher.py                 # Pencocokan 4-atribut kompetisi (D2, D3, D5)
  grading_support.py         # Oracle Python untuk verifikasi formula Grade
  keyword_eval.py            # Perhitungan metrik evaluasi & n-gram discovery
  exec_log.py                # Pencatatan riwayat eksekusi (EXE-...)
  checkpoint.py              # Pengelolaan global_checkpoint & pending_ranges
  quota.py                   # Safety tracker kuota & budget post per run
  models.py                  # Dataclass & Enum
  normalization.py           # Pembersihan teks & kamus singkatan
  pipeline.py                # Orkestrator alur kerja eksekusi
notebooks/                   # Notebook Google Colab tipis
  01_initial_scraping.ipynb
  02_routine_scraping.ipynb
  03_keyword_evaluation.ipynb
templates/                   # Skrip pembuat template Google Sheets
  build_template.py
tests/                       # Suite pengujian komprehensif
  fakes.py                   # Fake gspread 6.1.4 in-memory (strict RAW)
  test_compmon.py
  test_pipeline.py           # pagination, cap, pending_ranges, ID, Gemini
  test_sheets_safety.py
  test_template_builder.py
  test_scenarios.py
templates/test_grade_parity.py  # parity formula Sheets vs oracle (live: needs TEST sheet)
docs/                        # Dokumentasi lengkap
  PHASE0_FINDINGS.md
  DECISIONS.md
  CREDENTIALS.md
  ADMIN_GUIDE.md
  ERROR_HANDLING_RECOVERY.md
  SETUP.md
  PRD_TRACEABILITY.md
  TEST_REPORT.md
requirements.txt             # Dependensi proyek
```

---

## 🚀 Panduan Ringkas Penggunaan

1. **Persiapan Kredensial**: Buka [docs/CREDENTIALS.md](docs/CREDENTIALS.md) untuk mengatur Secrets di Colab (`SPREADSHEET_ID`, `APIFY_API_TOKEN`, `GEMINI_API_KEY`).
2. **Inisialisasi Template**: Jalankan `templates/build_template.py` untuk membuat 11 sheet Google Sheets.
3. **Initial Scraping**: Buka dan jalankan `notebooks/01_initial_scraping.ipynb`.
4. **Routine Scraping**: Jalankan `notebooks/02_routine_scraping.ipynb` setiap minggu.
5. **Keyword Evaluation**: Jalankan `notebooks/03_keyword_evaluation.ipynb` setiap 4 minggu.
6. **Panduan Admin**: Baca [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) untuk detail tata kelola harian admin.

---

## 🧪 Menjalankan Pengujian

Jalankan seluruh test case unit dan skenario integrasi dengan perintah:
```bash
python -m unittest discover tests -v
python -m unittest templates.test_grade_parity -v   # parity formula (live part needs a TEST spreadsheet)
```
Semua test di `tests/` berjalan tanpa dependensi jaringan eksternal menggunakan mock dan fake in-memory sheets yang meniru gspread 6.1.4.

