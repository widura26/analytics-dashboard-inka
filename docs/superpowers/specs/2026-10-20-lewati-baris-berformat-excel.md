# Design: Lewati Baris Excel yang Ditandai (Hidden/Collapse, Dicoret, Blok Hitam)

**Date:** 2026-10-20
**File:** `excel_convert.py` (`_baca_tandai_baris`, `_pindai_tandai_sheet`,
`_warna_gelap`, `read_excel_sheets`) + `bom_rekap.py` (`rekap_grid(abaikan=...)`)
+ `material_planning_2.py` (`prepare_upload_state`, `rekap_sumber_bom`,
`pesan_baris_tandai`, `render_rekap_report`)
**Keputusan user:** tandai di Excel = tidak usah direkap → lewati total +
laporkan, langsung aktif tanpa toggle, BOM saja dulu.

## Problem

Di file BOM, tim menandai komponen yang sudah dibatalkan lewat tiga cara:
baris **di-collapse/disembunyikan**, **diblok hitam**, dan **dicoret**
(strikethrough). Karena itu komponennya "tidak ditemukan" saat dicari di Excel —
tapi pandas hanya membaca **nilai sel**, tidak membaca format. Akibatnya baris
itu tetap terbaca, ikut terekap, dan muncul sebagai kode material yang
seolah-olah tidak ada di file sumber.

## Requirements

1. **Baris ditandai tidak ikut rekap & cleaning BOM**, dan jumlahnya
   dilaporkan per sheet (`N baris ditandai di Excel tidak ikut rekap …`)
   supaya bisa diaudit, bukan hilang diam-diam.
2. **Selalu aktif**, tanpa toggle di UI.
3. **BOM saja dulu** — file SAP tidak berubah (masih membaca semua baris).
4. **Tidak boleh mengubah perilaku lama**: file tanpa penanda (dan CSV) harus
   menghasilkan rekap identik, baris header tetap dipakai untuk deteksi.

## Design

### `excel_convert.py` — pemindaian format (khusus `.xlsx`/`.xlsm`)

`read_excel_sheets` tetap membaca nilai lewat pandas (aman regresi), lalu
membuka workbook sekali lagi dengan openpyxl **mode normal** — mode read-only
yang dipakai pandas tidak membawa `row_dimensions`, jadi baris tersembunyi
tidak terlihat di sana. Hasilnya ditambahkan sebagai kunci `tandai` pada tiap
sheet:

```python
{"label": ..., "grid": ..., "rows": ..., "cols": ...,
 "tandai": {baris_0_based: ["tersembunyi/collapse" | "dicoret" | "blok gelap", ...]}}
```

Penanda yang dikenali:

| Penanda | Sumber data | Alasan |
|---|---|---|
| Tersembunyi/collapse | `ws.row_dimensions[r].hidden` | Group collapse / row hide |
| Dicoret | `cell.font.strike` | Strikethrough |
| Blok gelap | `cell.fill.patternType == "solid"` + semua kanal RGB ≤ 63 (termasuk `theme` 1 = dk1/Hitam bawaan Excel) | Blok hitam pekat |

Ambang `≤ 63` (#3F) disengaja longgar untuk hitam pekat tetapi menolak warna
gelap biasa (mis. navy `#1F3864`, kanal B=100). Scan kolom dibatasi 64 kolom
supaya bekas format di kolom jauh (XFD) tidak memperlambat.

`.xls` (xlrd) dan `.xlsb` (pyxlsb) tidak punya API format → `tandai` kosong
(perilaku lama). Gagal membuka workbook kedua juga tidak membatalkan
pembacaan nilai (dialihkan ke `{}`).

### `bom_rekap.rekap_grid(data, source_label, abaikan=None)`

Parameter baru `abaikan = {baris_0_based: [alasan, ...]}`. Diterapkan **setelah**
deteksi header, hanya untuk baris DATA (indeks ≥ `data_start_idx`), sehingga
baris header tetap dipakai deteksi walau ikut ditandai. Report bertambah dua
kunci:

- `rows_skipped_marked` — jumlah baris ditandai yang dilewati;
- `mark_summary` — `{alasan: jumlah}`, dihitung sekali per baris (alasan
  pertama) supaya totalnya selalu = `rows_skipped_marked`.

Default `abaikan=None` → perilaku lama persis (CSV tidak punya format sel).

### `material_planning_2.py`

- `prepare_upload_state`: sumber Excel kini ikut membawa `"tandai"`.
- `rekap_sumber_bom`: meneruskan `abaikan=src.get("tandai")` — sumber lama
  tanpa kunci `tandai` tetap aman.
- `pesan_baris_tandai(reports)`: merangkum jadi
  `"N baris ditandai di Excel tidak ikut rekap [alasan…]. Per sheet: …"`,
  ditampilkan via `st.info` pada tampilan rinci (1 sumber) dan tabel ringkas
  (banyak sumber); tabel juga mendapat kolom `Ditandai`, dan total "Baris
  dilewati" kini ikut menghitung baris ber-tanda.

## Edge cases

| Kasus | Penanganan |
|---|---|
| Baris header ikut ditandai | Tidak dihitung: header tetap dipakai deteksi, pemotongan hanya berlaku baris data |
| Indeks di luar jangkauan grid (mis. baris kosong ekor) | Tidak cocok dengan baris mana pun → diabaikan aman |
| Satu baris ditandai lebih dari satu cara | Dihitung sekali, memakai alasan pertama |
| Sel ber-fill gelap-biasa (navy, merah marun) | Tidak ditandai (kanal > 63) |
| Kolom jauh berbekas format | Tidak dipindai (batas 64 kolom) |
| File `.xls` / `.xlsb` / CSV | `tandai` kosong → semua baris direkap seperti dulu |
| Baris normal pada sheet dengan baris ditandai | Tetap direkap; hanya baris bertanda yang keluar |

## Verification

- `tests/test_etl.py::test_baca_tandai_baris` — workbook berisi baris normal,
  dicoret, blok hitam, dan hidden terdeteksi tepat (indeks 0-based selaras
  grid); file tanpa penanda → `tandai == {}`.
- `tests/test_etl.py::test_rekap_grid_lewati_baris_tandai` — baris bertanda
  keluar dari rekap + terhitung di report; tanpa `abaikan` semua baris direkap;
  indeks header/luar jangkauan aman; `rekap_sumber_bom` meneruskan `tandai`.
- `tests/test_app.py::test_lewati_baris_berformat` — E2E AppTest: kode material
  ber-tanda tidak muncul di `etl_bom_rekap` maupun `etl_bom_clean`, info
  `"1 baris ditandai di Excel tidak ikut rekap [1 dicoret]. Per sheet: Interior (1)"`.
- Regresi penuh: `python tests/test_etl.py`, `python tests/test_app.py`,
  `python tests/test_bom_firestore.py`, `python tests/test_drive_upload.py`.

## Out of scope

- Toggle on/off di UI (user memilih langsung aktif).
- Penerapan aturan yang sama untuk file SAP (alur SAP tetap membaca semua
  baris) — kandidat tindak lanjut.
- Deteksi penanda selain ketiganya (mis. font berwarna terang, komentar sel).
- Membaca format `.xls`/`.xlsb`.
