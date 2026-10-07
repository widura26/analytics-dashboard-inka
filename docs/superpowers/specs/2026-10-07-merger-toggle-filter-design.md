# Design: Toggle Filter Berbasis DataFrames yang Sudah Ada

**Date:** 2026-10-07
**File:** `material_planning_2.py` — section "Merger of BOM and SAP Data"

## Problem

Saat ini empat toggle (`PR Partial`, `Belum PR`, `Sudah PR`, `Stock`) memfilter
`merge_data` dengan kondisi boolean yang didefinisikan ulang di tempat itu
(lines 154–187). Definisi tersebut berbeda dari DataFrame yang sudah dihitung
sebelumnya (`partialPRData`, `belumPRData`, `sudahPRData`, `stockData`),
sehingga angka di tabel bisa tidak sinkron dengan angka di pie chart "Purchase
Requisition".

## Requirements

1. Toggle **PR Partial** → tampilkan `partialPRData`.
2. Toggle **Belum PR** → tampilkan `belumPRData`.
3. Toggle **Sudah PR** → tampilkan `sudahPRData`.
4. Toggle **Stock** → tampilkan `stockData`.
5. Beberapa toggle aktif sekaligus → union (gabungan) dari DataFrames terkait.
6. Semua toggle mati → tampilkan `merge_data` penuh (perilaku default saat ini).
7. Header `Merger of BOM and SAP Data ({len(df_terfilter)})` mencerminkan
   jumlah baris hasil filter.

## Approach (approved: A)

Bangun daftar `selected` berdasarkan toggle yang aktif, lalu `pd.concat` jika
tidak kosong:

```python
df_terfilter = merge_data.copy()
selected = []
if show_under_requested: selected.append(partialPRData)
if show_over_requested:  selected.append(belumPRData)
if show_same_requested:  selected.append(sudahPRData)
if show_stock_data:      selected.append(stockData)
if selected:
    df_terfilter = pd.concat(selected)
```

Blok `conditions` / `mask` lama (lines 159–187) dihapus dan diganti blok di
atas.

## Safety notes

- Keempat DataFrame saling leluasa (disjoint): `stockData` vs `otherData`
  dipisah oleh kondisi `Kode Material Stock` / `Stock Total`; tiga kategori PR
  adalah partisi dari `otherData`. Karena itu `pd.concat` tidak menghasilkan
  baris kembar, dan `drop_duplicates` tidak diperlukan.
- `pd.concat` mempertahankan index asli; `st.dataframe` tidak memerlukan
  index unik, jadi reset index tidak wajib.
- Kolom keempat DataFrame berasal dari `merge_data` → struktur kolom konsisten
  dengan tampilan sebelumnya.

## Out of scope

- Perubahan pada pie chart, definisi `partialPRData`/`belumPRData`/`sudahPRData`/
  `stockData`, atau sumber data.
- Perilaku toggle lain di luar section ini.

## Testing / Verification

- Script verifikasi kecil (tanpa Streamlit) yang meniru blok filter dengan
  fixture DataFrame: assert union beberapa toggle, assert default all-off =
  `merge_data` penuh, assert hasil per-toggle identik dengan DataFrame sumber.
- `python -m py_compile material_planning_2.py`.
