# Design: Pilih Sheet File BOM untuk Rekap & Cleaning

**Date:** 2026-10-20
**File:** `material_planning_2.py` — blok "Upload Bill Of Material" di `render_pipeline()`
**Keputusan user:** multiselect (centang banyak), default **semua sheet** tercentang

> **Update (hari yang sama):** filter 29 sheet target **dilepas** — semua sheet
> yang dicentang ikut rekap. Lihat spec
> `2026-10-20-lepas-filter-29-sheet-target.md`. Pernyataan di bawah yang
> menyinggung filter itu sudah disesuaikan.

## Problem

Begitu file BOM (.xlsx/.xlsm/.xls/.xlsb) diunggah, `prepare_upload_state` membaca
**seluruh** sheet menjadi grid (`etl_bom_sources`) dan tombol **Rekap & Cleaning**
memproses daftar itu apa adanya — user tidak bisa memilih sheet mana yang ikut.

Padahal file BOM berisi banyak sheet yang bukan data BOM mentah (cover, tab
`TOTAL BOM` hasil rekap, sheet review, dll). Kalaupun ada mekanisme penyaring,
dia bekerja **diam-diam di belakang layar** (waktu itu: hanya 29 sheet di
`bom_rekap.DAFTAR_SHEET_TARGET` yang direkap, sisanya dilewati + dilaporkan),
sehingga user tidak punya kendali dan harus menebak-nebak isi warning
"Sumber/sheet dilewati". Kendali penuh kini ada di pemilih sheet (dan sejak
update di atas, daftar 29 itu bukan filter lagi).

## Requirements (keputusan user)

1. **Multiselect = daftar proses.** Sheet tercentang = sheet yang dikirim ke
   `run_bom_etl` (rekap sekaligus cleaning ikut terfilter otomatis karena
   cleaning berjalan di atas hasil rekap).
2. **Default semua sheet tercentang** — perilaku lama tetap berlaku tanpa
   klik tambahan; user tinggal mengurangi pilihan.
3. **Uncheck menyembunyikan, tidak menghapus.** Grid sheet tetap ada di
   `etl_bom_sources`; file baru membaca ulang semuanya.
4. **Tanpa file baru / tanpa perubahan format.** Pilihan hanya di
   `st.session_state` (session-only).

## Design

### State

`st.session_state.etl_bom_sheets: list[str]` — nama sheet tercentang.

Dibersihkan `prepare_upload_state` (kunci `etl_{prefix}_sheets` ikut di
`clear_all`) saat file upload berganti atau dihapus, sehingga **file baru selalu
berawal dengan semua sheet tercentang**. Kunci ikut terhapus `reset_etl`
(pola `etl_*`).

### UI (`render_sheet_picker(prefix)`)

Dipanggil setelah `render_upload_status("bom")`:

```python
bom_sources = render_sheet_picker("bom")   # list sumber terpilih
```

- **File CSV / Excel 1 sheet** → tidak ada widget; seluruh sumber dipakai
  (memang cuma satu).
- **Excel multi-sheet** → `st.multiselect("Sheet yang ikut rekap & cleaning",
  labels, key="etl_bom_sheets")`, tanpa param `default` (state diisi manual
  sebelum widget dipanggil — passing `default` pada key yang sudah ada di
  session_state melempar `StreamlitAPIException`).
- **Semua sheet terpilih ikut rekap** — tidak ada filter nama sheet (update di
  atas); satu-satunya penyaring yang tersisa adalah guard header/agregat di
  `rekap_sumber_bom`.
- Nama sheet lama yang sudah tidak ada di file aktif dibersihkan; bila habis
  semua, kembali ke default semua sheet.

### Tombol Rekap & Cleaning

```python
disabled=not bom_file_ready or not bom_sources
...
if bom_clicked and bom_sources:
    run_bom_etl(bom_sources, spreadsheet_name, share_email)
```

Tanpa centang → tombol nonaktif + info "Belum ada sheet yang dicentang…" dan
caption status "Centang minimal satu sheet di atas." Hasil rekap sebelumnya
**tidak** dihapus oleh mengubah centang (hanya berganti setelah tombol ditekan).

### Ringkasan hasil rekap

Audit cakupan 29 sheet target sudah **dihapus** bersama filternya. Yang tampil
setelah tahap rekap: caption `"{N} sheet direkap dari {M} sumber dipilih."` +
warning "Sumber/sheet dilewati" untuk sheet yang gagal terbaca (header tidak
terdeteksi / sheet agregat).

## Edge cases

| Kasus | Penanganan |
|---|---|
| File CSV (bukan Excel) | Tidak ada pemilih; satu sumber dipakai penuh — perilaku lama persis |
| Excel berisi 1 sheet | Tidak ada pemilih (tidak ada yang bisa dipilih) |
| Uncheck semua sheet | Tombol nonaktif; hasil rekap/cleaning lama tetap utuh sampai tombol ditekan lagi |
| Sheet di-rename/dihapus di file baru | Pilihan lama tersaring sendiri; file baru = default semua sheet |
| Reset ETL | `etl_bom_sheets` ikut terhapus (pola `etl_*`), lalu file dibaca ulang oleh `sync_upload` |
| Sheet nama apa pun (termasuk di luar 29 nama target) | Direkap selama header-nya terdeteksi; label = nama sheet, dinormalkan ke nama penuh hanya bila cocok salah satu dari 29 nama |
| Mengubah centang setelah hasil muncul | Hasil lama tetap tampil sampai tombol ditekan; tidak ada proses diam-diam |

## Verification (otomatis — repo punya suite test)

- `tests/test_etl.py::test_pilih_sheet_reset_state` — pilihan bertahan saat file
  sama, di-reset saat file berganti/dihapus.
- `tests/test_app.py::test_pilih_sheet_rekap_bom` — multiselect tampil +
  default semua sheet; rekap 2 sheet → ubah ke 1 sheet → hasil hanya sheet itu;
  tanpa centang → tombol nonaktif (AppTest menolak klik) dan hasil lama utuh.
- `tests/test_app.py::test_rekap_sheet_di_luar_29_target` — sheet non-target
  (`Komponen`) tetap direkap (spec `2026-10-20-lepas-filter-29-sheet-target`).
- Regresi: `python tests/test_etl.py`, `python tests/test_app.py`,
  `python tests/test_bom_firestore.py`, `python tests/test_drive_upload.py`.

## Out of scope

- Pilihan sheet untuk file **SAP** (alur SAP tetap memproses semua sheet).
- Penghapusan `bom_rekap.DAFTAR_SHEET_TARGET` (kini dipakai untuk normalisasi
  nama, bukan filter) — lihat spec `2026-10-20-lepas-filter-29-sheet-target`.
- Persistensi pilihan ke file (session-only).
