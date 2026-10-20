# Design: Rekap BOM Tanpa Filter 29 Sheet Target

**Date:** 2026-10-20
**File:** `material_planning_2.py` (`rekap_sumber_bom`, `run_bom_etl`,
`render_sheet_picker`) + `bom_rekap.py` (komentar/docstring)
**Keputusan user:** "kita tidak terikat pada 29 sheet target lagi"

## Problem

Pemilih sheet (`2026-10-20-pilih-sheet-bom-design.md`) membolehkan user
mencentang sheet apa pun, tapi `rekap_sumber_bom` **tetap menolak** sheet yang
namanya tidak cocok dengan `bom_rekap.DAFTAR_SHEET_TARGET`:

```
Rekap BOM gagal: Komponen (di luar daftar 29 sheet target)
```

Dua masalah sekaligus: user sudah memilih sheet secara sadar, lalu hasilnya
dibatalkan oleh daftar lama; dan kalau sheet yang dipilih **semuanya** non-target,
`reports` kosong → `run_bom_etl` berhenti dengan `st.error` (rekap gagal total).

## Requirements

1. **Semua sheet yang dipilih ikut rekap & cleaning.** Tidak ada filter nama.
2. **Daftar 29 nama tidak dihapus** — hanya kehilangan peran sebagai filter;
   tetap dipakai untuk *normalisasi nama* (sheet Excel terpotong batas 31
   karakter → nama penuh), supaya label `Nama Sheet Sumber` konsisten dengan
   hasil Colab.
3. **Guard lain tetap berlaku** (bukan soal nama sheet):
   - `bom_rekap.mendeteksi_output_rekap` — sheet agregat berkolom
     `Nama Sheet Sumber` (mis. tab `TOTAL BOM`) tetap dilewati agar baris tidak
     terhitung ganda;
   - sheet yang header-nya gagal terdeteksi / tidak menghasilkan baris tetap
     dilewati **per sheet** + dilaporkan (bukan gagal total selama minimal satu
     sheet berhasil).

## Design

### `rekap_sumber_bom(sources)` → `(dfs, reports, dilewati)`

Blok gate dihapus:

```python
# lama
target = bom_rekap.target_sheet(label)
if target is None:
    dilewati.append(f"{label} (di luar daftar 29 sheet target)")
    continue
df_src, report = bom_rekap.rekap_grid(src["grid"], target)

# baru
nama_sumber = bom_rekap.target_sheet(label) or label   # normalisasi, bukan filter
df_src, report = bom_rekap.rekap_grid(src["grid"], nama_sumber)
```

Pengembalian `target_terpakai` ikut dihapus (hanya dipakai blok audit di
`run_bom_etl` yang ikut dihapus) → signature 4 → 3 tuple.

### `run_bom_etl`

- Info audit **"Target terpenuhi: N/29 sheet target — belum ikut rekap: …"**
  dihapus — tidak ada lagi target yang harus dipenuhi. Diganti caption ringkas
  `"{N} sheet direkap dari {M} sumber dipilih."`
- Warning `"Sumber/sheet dilewati"` tetap (sekarang hanya berisi sheet gagal
  header / sheet agregat).

### `render_sheet_picker`

Caption `"… — N sheet di luar daftar 29 sheet target akan dilewati algoritma
rekap"` dihapus. Caption kini selalu
`"{N}/{M} sheet dipilih untuk rekap & cleaning."`

### `bom_rekap.py`

Tidak ada perubahan kode — hanya komentar `DAFTAR_SHEET_TARGET` dan docstring
`target_sheet()` / `nama_sheet_target()` yang ditulis ulang: daftar itu
referensi **penamaan**, bukan cakupan rekap.

## Edge cases

| Kasus | Penanganan |
|---|---|
| Sheet bernama apa pun (mis. `Komponen`, `Review Bogie`) | Direkap selama header-nya terdeteksi; label = nama sheet (dinormalkan hanya bila cocok salah satu dari 29 nama) |
| Sheet tanpa header BOM yang dikenali | Dilewati per sheet + masuk warning; kalau SEMUA gagal baru `st.error` |
| Sheet agregat berkolom `Nama Sheet Sumber` | Tetap dilewati (`mendeteksi_output_rekap`) |
| Nama sheet terpotong 31 karakter | Tetap dinormalkan ke nama penuh bila cocok prefix ≥ 20 karakter dengan salah satu dari 29 nama |
| Sheet target lama kini ada di file | Sama seperti sheet lain — labelnya persis nama target penuh, jadi keluaran tidak berubah |

## Verification

- `tests/test_etl.py::test_rekap_sumber_bom_tanpa_filter_target` — 4 sheet
  (2 target + 2 non-target) direkap, hanya sheet agregat yang dilewati,
  normalisasi nama tetap jalan; tidak ada lagi pesan "di luar daftar 29".
- `tests/test_etl.py::test_daftar_target_sheet` — helper tetap berfungsi,
  ditambah kasus `"Komponen"` → None (bukan ditolak, hanya tidak dinormalisasi).
- `tests/test_app.py::test_rekap_sheet_di_luar_29_target` — reproduksi error
  asli: sheet `Komponen` kini direkap & dibersihkan, tanpa `st.error`.
- Regresi penuh: `python tests/test_etl.py`, `python tests/test_app.py`.

## Out of scope

- Menghapus `DAFTAR_SHEET_TARGET` / `target_sheet()` dari `bom_rekap.py`.
- Perubahan pada algoritma deteksi header fuzzy (`rekap_grid`) atau cleaning.
- Pilihan sheet file SAP (alur SAP tetap memproses semua sheet).
