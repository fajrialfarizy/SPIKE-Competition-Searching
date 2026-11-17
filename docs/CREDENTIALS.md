# CREDENTIALS & SECRETS SETUP
## Instagram Competition Monitoring & Curation System

Sistem ini didesain aman dan tidak pernah menanamkan API key, token, atau kredensial rahasia di dalam kode sumber, repositori, atau log.

Semua kredensial dibaca langsung melalui **Google Colab Secrets** (`google.colab.userdata`).

---

## 1. Daftar Nama Secret yang Digunakan

| Nama Secret di Colab | Deskripsi & Tujuan | Sumber Kredensial |
|---|---|---|
| `SPREADSHEET_ID` | ID unik dari file Google Spreadsheet database | URL Google Sheets (antara `/d/` dan `/edit`) |
| `APIFY_API_TOKEN` | Token API Apify untuk menjalankan actor scraping | [Apify Console Settings](https://console.apify.com/account/integrations) |
| `GEMINI_API_KEY` | API Key Google Gemini untuk ekstraksi caption | [Google AI Studio](https://aistudio.google.com/app/apikey) |

---

## 2. Cara Mengatur Secrets di Google Colab

1. Buka notebook di Google Colab.
2. Pada panel sebelah kiri, klik ikon **Kunci** (Secrets).
3. Klik tombol **Add new secret**.
4. Masukkan nama `SPREADSHEET_ID` pada kolom *Name*, lalu tempelkan ID spreadsheet pada kolom *Value*. Aktifkan toggle **Notebook access**.
5. Ulangi langkah di atas untuk:
   - `APIFY_API_TOKEN`
   - `GEMINI_API_KEY`

---

## 3. Otorisasi Akun Google (Google Sheets)

Notebook menggunakan autentikasi resmi Google Colab:
```python
from google.colab import auth
auth.authenticate_user()
```
Ketika sel ini dijalankan, Google Colab akan memunculkan pop-up izin akun Google. Admin cukup menyetujui izin tersebut untuk memberikan hak akses baca/tulis ke spreadsheet.

