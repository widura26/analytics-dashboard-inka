# Design: Pilih Banyak Sheet Sumber Rekap (Multiselect + Mapper Per Sheet)

**Date:** 2026-10-08
**File:** `sheet_mapping.py` — sidebar "Pilih Sheet" + section "Generate Rekap"
**Approved approach:** B (state centang session-only, tanpa file baru)

## Problem

Dropdown "Pilih Sheet" di sidebar memakai `st.selectbox` (sheet_mapping.py:485),
sehingga hanya satu sheet bisa dipilih pada satu waktu. Alur pemakaian untuk
beberapa sheet (mis. "De-Scoping" dan "Fastening Mekanik") harus berurutan:
pilih → map → simpan → pilih lagi → map → simpan, tanpa indikasi mana yang
sudah/sedang diproses.

Hasil akhir rekap sebenarnya sudah multi-sheet (`run_recap` me-loop semua
entri di `sheet_mappings`, recap_engine.py:82) — yang kurang hanya cara
memilih dan memetakan banyak sheet sekaligus.

## Requirements (keputusan user)

1. **Multiselect = daftar rekap.** Sheet tercentang = sheet yang ikut
   Generate. Centang adalah satu-satunya sumber keputusan keluaran.
2. **Uncheck menyembunyikan, tidak menghapus.** Mapping tetap tersimpan di
   `sheet_mappings.json`; centang ulang → langsung dipakai lagi.
3. **Navigasi per sheet di area utama.** Pemilih sheet berada di area utama
   (bukan sidebar), hanya menampilkan sheet hasil centang; memilih sheet
   memuat mapping tersimpannya masing-masing.
4. **Approach B:** daftar centang hanya di `st.session_state` (tanpa file
   persisten baru). Setelah refresh, default centang = sheet yang punya
   mapping tersimpan.

## Design

### State

`st.session_state.recap_sheets: list[str]` — daftar sheet yang dicentang,
session-only.

Diinisialisasi di `render()` **setelah** `sheet_options` diketahui (bukan di
`initialize_state()`, karena daftar sheet baru tersedia setelah sidebar):

```python
if "recap_sheets" not in st.session_state:
    st.session_state.recap_sheets = [
        n for n in sheet_options if n in st.session_state.sheet_mappings
    ]
```

### Sidebar

`st.selectbox("Pilih Sheet")` (baris 485) diganti:

```python
st.multiselect(
    "Pilih Sheet Sumber Rekap", sheet_options, key="recap_sheets"
)
```

Tanpa param `default` — state sudah diisi manual di atas; passing `default`
pada key yang sudah ada di session_state melempar `StreamlitAPIException`.

### Area utama (mapper)

Memuat **satu sheet** pada satu waktu. Sheet valid =
`recap_sheets ∩ sheet_options`:

```python
valid = [s for s in st.session_state.recap_sheets if s in sheet_options]
current = st.session_state.selected_sheet
if current not in valid:
    current = valid[0] if valid else None
if current is None:
    st.info("Centang sheet di sidebar terlebih dahulu.")
    st.stop()
```

Form per-sheet tidak saling tabrakan karena key widget sudah per-sheet:
`key=f"mapping_{selected_sheet}_{field}"` (baris 664). Header config,
auto-suggestion, manual mapping, tombol Simpan/Reset, dan preview tetap
persepsi sheet yang sedang dibuka.

### Generate Rekap (baris 781–828)

Filter mapping sebelum memanggil `run_recap`:

```python
active = {
    k: v
    for k, v in st.session_state.sheet_mappings.items()
    if k in st.session_state.recap_sheets
}
jumlah_mapping = len(active)
df, report = run_recap(spreadsheet, active)
```

- Caption tiga angka: "N sheet dicentang, M sudah dimapping, K belum."
- Tombol disabled saat `jumlah_mapping == 0`. Pesan dibedakan: tidak ada
  yang dicentang vs. tercentang tapi belum dimapping.
- `recap_engine.py` tidak diubah.

### Sidebar expander "Mapping yang sudah disimpan" (baris 834)

Dibagi dua grup:

- **Ikut rekap** (tercentang): `✓ sudah dimapping` / `⚠ belum dimapping`
- **Tersimpan, tidak ikut rekap** (ter-uncheck): ditampilkan agar jelas
  mapping masih ada, bukan hilang.

## Edge cases

| Kasus | Penanganan |
|---|---|
| Sheet tercentang sedang dibuka lalu di-uncheck | Fallback ke sheet valid pertama; kalau kosong → `st.info` + stop |
| Tidak ada sheet dicentang | Area utama st.info + stop; section Generate tidak dirender; multiselect + expander "Status mapping" di sidebar tetap tampil |
| Sheet di-rename/dihapus di Google Sheets | Setelah refresh, nama tersaring dari daftar centang dan muncul di grup "Tersimpan, tidak ikut rekap" (mapping aman); selama cache belum di-refresh, jalur error lama yang menangani (atau report "hilang" di run_recap) tetap berlaku |
| Tombol "Reset Mapping" | Mapping dihapus, centang tetap → status jadi "belum dimapping" |
| Tombol "🔄 Refresh Spreadsheet" | `recap_sheets` tetap ada (widget key), tidak hilang |
| Sheet punya mapping tapi di-uncheck lalu halaman di-refresh | Tercentang kembali — konsekuensi disengaja approach B (state session-only) |

## Verification (manual — repo tanpa test framework)

1. Centang "De-Scoping" + "Fastening Mekanik" → keduanya muncul di pemilih
   area utama.
2. Map sheet 1 → Simpan → pindah ke sheet 2 → map → Simpan. Form sheet 1
   tidak terganggu.
3. Generate → report menampilkan kedua sheet; kolom `Nama Sheet Sumber`
   benar.
4. Uncheck sheet 2 → Generate hanya memuat sheet 1;
   `sheet_mappings.json` masih berisi keduanya.
5. Refresh halaman → default centang = sheet yang punya mapping.
6. Uncheck sheet yang sedang terbuka → area utama pindah tanpa error.
7. Centang sheet yang belum dimapping → status "belum dimapping"; Generate
   tetap jalan untuk sheet yang sudah dimapping.

## Out of scope

- Perubahan pada `recap_engine.py` atau format `sheet_mappings.json`.
- Clone/copy mapping antar sheet.
- Persistensi daftar centang ke file (dipilih approach B, session-only).
- Perubahan pada halaman lain (`material_planning_2.py`, dll).
