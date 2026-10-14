# Statistic Dashboard for INKA PPIC Division

## Google OAuth — Upload ke Drive

Upload file memakai **user OAuth** (bukan service account — service account tidak
punya kuota storage di Google Drive, lihat
[dokumen resmi Google](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)).

Scope: `https://www.googleapis.com/auth/drive`

### Login lokal (mesin development)

1. Jalankan dari terminal: `streamlit run app.py`
2. Klik **Upload to Google Drive** → browser terbuka → pilih akun
3. Token tersimpan di `token.json` (sudah ada di `.gitignore`, jangan di-commit)

### Deploy ke Streamlit Cloud

`run_local_server` tidak akan selesai di Streamlit Cloud (redirect `localhost`
mendarat di browser pengunjung, bukan container). Token harus dibuat sekali di
mesin lokal lalu ditaruh di secrets:

1. Ambil seluruh isi `token.json`
2. Streamlit Cloud → **Manage app → Settings → Secrets**, tambahkan:

   ```toml
   google_oauth_token = '''
   { ...tempel seluruh isi token.json di sini... }
   '''
   ```

3. Deploy ulang / restart app

Urutan pembacaan credential di `get_drive_service()`:
`token.json` (lokal) → `st.secrets["google_oauth_token"]` (deploy) → login interaktif.

### Penting: refresh token expired tiap 7 hari

Selama OAuth consent screen berstatus **Testing**, refresh token Google hanya
bertahan **7 hari**. Gejala: upload gagal dengan pesan "Refresh token ... ditolak/
kedaluwarsa". Solusi:

1. Di mesin lokal: hapus `token.json`, jalankan app dari terminal, klik Upload, login ulang
2. Salin isi `token.json` baru ke secret `google_oauth_token` di Streamlit Cloud
3. Restart app

Untuk menghilangkan siklus 7 hari ini, publish consent screen ke **Production**
(butuh domain sendiri + halaman homepage/privacy policy/ToS — lihat bagian
di bawah).

### Publish ke Production (opsional, long-term)

Wajib untuk app **External** yang dipublikasikan (tanpa ini tetap ada cap 100 user
dan warning screen):

1. Domain sendiri (domain `streamlit.app` tidak bisa dipakai — bukan milik kita)
2. Host 3 halaman di domain yang sama: **homepage** (deskripsi app, bukan cuma
   login, wajib memuat link privacy policy), **privacy policy**, **terms of service**
3. Verifikasi domain di [Google Search Console](https://search.google.com/search-console)
   (DNS TXT) memakai akun owner/editor project
4. Google Auth Platform → **Audience** → Publishing status → **In production**
5. **Branding page** → tambah **Authorized domain** dulu → baru isi Application
   homepage / Privacy policy / Terms of service → **Publish app**

Status hasil: *Published (unverified)* — user melihat warning screen yang bisa
di-click-through, cap 100 user total, tapi **refresh token tidak expired 7 hari**.
Verifikasi penuh (>100 user / hilangkan warning) untuk scope `drive` (restricted)
mensyaratkan video demo + justifikasi scope + security assessment tahunan berbayar —
pertimbangkan turunkan scope ke `drive.file` bila ingin diverifikasi.
