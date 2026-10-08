# Multi-Select Sheet Sumber Rekap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mengganti dropdown single-sheet di sidebar dengan multiselect "daftar rekap", memindahkan pemilih sheet mapper ke area utama, dan memfilter Generate Rekap berdasarkan sheet yang dicentang.

**Architecture:** Satu state baru `st.session_state.recap_sheets: list[str]` (session-only) yang menjadi sumber keputusan: sidebar mengisi lewat `st.multiselect(key="recap_sheets")`, area utama memilih satu sheet dari daftar itu untuk dimapping, dan Generate memfilter `sheet_mappings` berdasarkan daftar itu. Mapping yang di-uncheck tetap tersimpan di `sheet_mappings.json` (tidak ikut rekap, tidak hilang).

**Tech Stack:** Python 3.10, Streamlit, gspread. Spec: `docs/superpowers/specs/2026-10-08-multiselect-sheet-source-design.md`.

## Global Constraints

- `recap_engine.py` TIDAK diubah. Perubahan hanya di `sheet_mapping.py`.
- Format file `sheet_mappings.json` TIDAK berubah.
- Daftar centang `recap_sheets` session-only: TIDAK ada file persisten baru, TIDAK ada param `default` pada widget yang key-nya sudah ada di session_state (melempar `StreamlitAPIException`).
- Key widget mapping per-sheet dipertahankan persis: `key=f"mapping_{selected_sheet}_{field}"`.
- Repo ini TIDAK punya test framework (tanpa pytest). Gerbang otomatis per task = `python -m py_compile sheet_mapping.py`; verifikasi perilaku = checklist manual di browser (lihat spec, bagian Verification).
- Semua pesan UI dalam Bahasa Indonesia, konsisten dengan teks yang ada sekarang.

---

### Task 1: Multiselect sidebar + pemilih sheet di area utama

**Files:**
- Modify: `sheet_mapping.py:481-501` (sidebar `selectbox` → `multiselect`, lalu logika pemilihan sheet pindah ke area utama)

**Interfaces:**
- Consumes: `st.session_state.sheet_mappings` (dict, sudah ada), `st.session_state.selected_sheet` (sudah ada), `sheet_options: list[str]` (dihitung di sidebar, baris 475).
- Produces: `st.session_state.recap_sheets: list[str]` — daftar sheet tercentang; menjadi satu-satunya input untuk filter Generate (Task 2) dan grup status (Task 3).

- [x] **Step 1: Ganti `selectbox` sidebar dengan `multiselect`**

Di `render()`, blok sidebar. HAPUS blok lama ini (sheet_mapping.py:481-491):

```python
        current = st.session_state.selected_sheet
        if current not in sheet_options:
            current = sheet_options[0]

        selected_sheet = st.selectbox(
            "Pilih Sheet",
            sheet_options,
            index=sheet_options.index(current),
        )

        st.session_state.selected_sheet = selected_sheet
```

GANTI dengan inisialisasi state + multiselect (indentasi 8 spasi, di dalam `with st.sidebar:`):

```python
        if "recap_sheets" not in st.session_state:
            st.session_state.recap_sheets = [
                n for n in sheet_options if n in st.session_state.sheet_mappings
            ]

        st.multiselect(
            "Pilih Sheet Sumber Rekap",
            sheet_options,
            key="recap_sheets",
        )
```

Catatan: tanpa param `default` — state diisi manual di baris sebelum widget dipanggil.

- [x] **Step 2: Tambah pemilih sheet di area utama sebelum data dimuat**

Di `render()`, blok `# LOAD SHEET`. SEBELUM baris `try: data = get_sheet_data(...)` (sheet_mapping.py:497) dan setelah blok sidebar berakhir, sisipkan:

```python
    # ------------------------------------------------------------
    # PICK SHEET TO MAP (dari daftar centang)
    # ------------------------------------------------------------

    valid_sheets = [
        s for s in st.session_state.recap_sheets if s in sheet_options
    ]

    current = st.session_state.selected_sheet
    if current not in valid_sheets:
        current = valid_sheets[0] if valid_sheets else None

    if current is None:
        st.info("Centang sheet di sidebar terlebih dahulu.")
        st.stop()

    selected_sheet = st.selectbox(
        "Sheet yang sedang dimapping",
        valid_sheets,
        index=valid_sheets.index(current),
    )

    st.session_state.selected_sheet = selected_sheet
```

Indentasi 4 spasi (level fungsi `render()`). Blok `# LOAD SHEET` di bawahnya tidak diubah — `get_sheet_data(spreadsheet_name, selected_sheet)` tetap memakai `selected_sheet` yang baru di-set.

- [x] **Step 3: Cek kompilasi**

Run: `python -m py_compile sheet_mapping.py`
Expected: tanpa output, exit code 0.

- [x] **Step 4: Verifikasi manual di aplikasi**

Run: `.venv/Scripts/streamlit run app.py` lalu buka `http://localhost:8501`.

Checklist:
1. Sidebar menampilkan "Pilih Sheet Sumber Rekap" (multiselect), bukan "Pilih Sheet" (selectbox).
2. Default tercentang = sheet yang punya mapping tersimpan (cek isi `sheet_mappings.json`).
3. Area utama menampilkan selectbox "Sheet yang sedang dimapping" berisi hanya sheet tercentang.
4. Uncheck semua sheet → area utama menampilkan "Centang sheet di sidebar terlebih dahulu." lalu berhenti, tanpa traceback.
5. Centang 2 sheet (mis. "De-Scoping" dan "Fastening Mekanik") → keduanya muncul di selectbox area utama; berpindah sheet memuat struktur header masing-masing.
6. Uncheck sheet yang sedang terbuka → selectbox pindah ke sheet valid pertama tanpa error.
7. Simpan mapping di sheet 1, pindah ke sheet 2, kembali ke sheet 1 → pilihan kolom sheet 1 tidak berubah (key per-sheet).

- [x] **Step 5: Commit**

```bash
git add sheet_mapping.py
git commit -m "feat: multiselect sheet source + main-area mapper selector"
```

---

### Task 2: Filter Generate Rekap berdasarkan daftar centang

**Files:**
- Modify: `sheet_mapping.py:784-809` (section "4. Generate Rekap")

**Interfaces:**
- Consumes: `st.session_state.recap_sheets: list[str]` (Task 1), `st.session_state.sheet_mappings: dict[str, dict]`.
- Produces: variabel lokal `active: dict[str, dict]` (mapping tercentang) yang dikirim ke `run_recap(spreadsheet, active)` — `run_recap` sendiri tidak diubah (recap_engine.py:74).

- [x] **Step 1: Hitung `active` dan ganti caption/infos**

HAPUS blok lama (sheet_mapping.py:784-796):

```python
    jumlah_mapping = len(st.session_state.sheet_mappings)

    if jumlah_mapping == 0:
        st.info(
            "Belum ada mapping yang tersimpan. Simpan mapping minimal satu "
            "sheet di section 3 terlebih dahulu."
        )
    else:
        st.caption(
            f"{jumlah_mapping} sheet dengan mapping tersimpan akan direkap."
        )

    st.caption(f"Hasil ditulis ke tab: '{REKAP_TAB_NAME}'.")
```

GANTI dengan:

```python
    recap_sheets = st.session_state.recap_sheets
    active = {
        k: v
        for k, v in st.session_state.sheet_mappings.items()
        if k in recap_sheets
    }
    belum_dimapping = [
        s for s in recap_sheets if s not in st.session_state.sheet_mappings
    ]

    if not recap_sheets:
        st.info("Belum ada sheet yang dicentang di sidebar.")
    elif not active:
        st.info(
            f"{len(recap_sheets)} sheet dicentang, tetapi belum ada yang "
            "dimapping. Simpan mapping di section 3 terlebih dahulu."
        )
    else:
        st.caption(
            f"{len(recap_sheets)} sheet dicentang, {len(active)} sudah "
            f"dimapping, {len(belum_dimapping)} belum."
        )

    st.caption(f"Hasil ditulis ke tab: '{REKAP_TAB_NAME}'.")
```

- [x] **Step 2: Ganti tombol Generate dan pemanggilan `run_recap`**

HAPUS (sheet_mapping.py:798-809, baris di dalam `if st.button(...)` ikut sampai pemanggilan `run_recap`):

```python
    if st.button(
        "🚀 Generate Rekap",
        type="primary",
        disabled=jumlah_mapping == 0,
    ):
        with st.spinner(f"Merekap {jumlah_mapping} sheet..."):
            try:
                spreadsheet = open_spreadsheet(spreadsheet_name)
                df, report = run_recap(
                    spreadsheet,
                    st.session_state.sheet_mappings,
                )
```

GANTI dengan:

```python
    if st.button(
        "🚀 Generate Rekap",
        type="primary",
        disabled=len(active) == 0,
    ):
        with st.spinner(f"Merekap {len(active)} sheet..."):
            try:
                spreadsheet = open_spreadsheet(spreadsheet_name)
                df, report = run_recap(spreadsheet, active)
```

Sisa blok (`if df.empty: ...`, sukses, warning per report, `except`) TIDAK diubah apa pun — struktur bersarangnya identik (`button → spinner → try`), jadi tidak ada perubahan indentasi.

- [x] **Step 3: Cek kompilasi**

Run: `python -m py_compile sheet_mapping.py`
Expected: tanpa output, exit code 0. (Kalau gagal dengan `NameError`/`IndentationError` di sekitar `jumlah_mapping`, sisa referensi lama belum terganti — cari dengan `grep -n "jumlah_mapping" sheet_mapping.py`, hasilnya harus nol.)

Run: `grep -c "jumlah_mapping" sheet_mapping.py`
Expected: `0`

- [x] **Step 4: Verifikasi manual di aplikasi**

Masih dengan app berjalan:

1. Centang 2 sheet, mapping keduanya tersimpan → caption "2 sheet dicentang, 2 sudah dimapping, 0 belum."; tombol Generate aktif.
2. Uncheck sheet 2 → caption "1 sheet dicentang, 1 sudah dimapping, 0 belum."
3. Generate → sukses menyebut 1 sheet; tab `REKAP_BOM_TOTAL (Berdasarkan Kode Material) 3` hanya berisi baris dengan `Nama Sheet Sumber` = sheet 1.
4. Buka `sheet_mappings.json` → kedua sheet mappingnya MASIH ADA (uncheck tidak menghapus).
5. Centang sheet yang belum dimapping → caption "…, 1 belum."; Generate tetap jalan untuk yang sudah dimapping.
6. Uncheck semua → tombol Generate disabled, info "Belum ada sheet yang dicentang di sidebar."

- [x] **Step 5: Commit**

```bash
git add sheet_mapping.py
git commit -m "feat: filter Generate Rekap by checked sheets"
```

---

### Task 3: Expander status mapping di sidebar (dua grup)

**Files:**
- Modify: `sheet_mapping.py:834-843` (sidebar expander "📌 Mapping yang sudah disimpan")

**Interfaces:**
- Consumes: `st.session_state.recap_sheets` (Task 1), `st.session_state.sheet_mappings`.
- Produces: tampilan sidebar saja; tidak ada state baru.

- [x] **Step 1: Ganti isi expander**

HAPUS (sheet_mapping.py:834-843):

```python
    with st.sidebar.expander("📌 Mapping yang sudah disimpan"):
        if not st.session_state.sheet_mappings:
            st.caption("Belum ada mapping yang disimpan.")
        else:
            for name, config in st.session_state.sheet_mappings.items():
                st.write(
                    f"**{name}** — "
                    f"Row {config['header_start_row'] + 1}, "
                    f"{config['header_row_count']} header row"
                )
```

GANTI dengan:

```python
    with st.sidebar.expander("📌 Status mapping"):
        mappings = st.session_state.sheet_mappings
        recap_sheets = st.session_state.recap_sheets

        if not recap_sheets and not mappings:
            st.caption("Belum ada sheet dicentang maupun mapping tersimpan.")
        else:
            if recap_sheets:
                st.markdown("**Ikut rekap**")
                for name in recap_sheets:
                    if name in mappings:
                        config = mappings[name]
                        st.write(
                            f"✓ **{name}** — "
                            f"Row {config['header_start_row'] + 1}, "
                            f"{config['header_row_count']} header row"
                        )
                    else:
                        st.write(f"⚠ **{name}** — belum dimapping")

            hidden = [n for n in mappings if n not in recap_sheets]
            if hidden:
                st.markdown("**Tersimpan, tidak ikut rekap**")
                for name in hidden:
                    config = mappings[name]
                    st.write(
                        f"{name} — "
                        f"Row {config['header_start_row'] + 1}, "
                        f"{config['header_row_count']} header row"
                    )
```

- [x] **Step 2: Cek kompilasi**

Run: `python -m py_compile sheet_mapping.py`
Expected: tanpa output, exit code 0.

- [x] **Step 3: Verifikasi manual di aplikasi**

1. Expander berjudul "📌 Status mapping".
2. Sheet tercentang + sudah dimapping → `✓ **nama** — Row N, M header row` di grup "Ikut rekap".
3. Sheet tercentang belum dimapping → `⚠ **nama** — belum dimapping`.
4. Sheet punya mapping tapi di-uncheck → muncul di grup "Tersimpan, tidak ikut rekap".
5. Uncheck semua dan hapus semua mapping (tombol Reset per sheet) → caption "Belum ada sheet dicentang maupun mapping tersimpan."

- [x] **Step 4: Commit**

```bash
git add sheet_mapping.py
git commit -m "feat: status mapping sidebar with recap/hidden groups"
```

---

### Task 4: Verifikasi akhir end-to-end

**Files:**
- Test: tidak ada file baru — checklist manual terhadap spec.

**Interfaces:**
- Consumes: hasil Task 1-3 di `sheet_mapping.py`.

- [ ] **Step 1: Jalankan app bersih**

Run: `streamlit run app.py` (session browser baru / reload penuh).

- [ ] **Step 2: Jalankan checklist spec**

Lakukan 7 langkah di spec `docs/superpowers/specs/2026-10-08-multiselect-sheet-source-design.md`, bagian **Verification**:

1. Centang "De-Scoping" + "Fastening Mekanik" → keduanya muncul di pemilih area utama.
2. Map sheet 1 → Simpan → pindah ke sheet 2 → map → Simpan. Form sheet 1 tidak terganggu.
3. Generate → report menampilkan kedua sheet; kolom `Nama Sheet Sumber` benar.
4. Uncheck sheet 2 → Generate hanya memuat sheet 1; `sheet_mappings.json` masih berisi keduanya.
5. Refresh halaman → default centang = sheet yang punya mapping.
6. Uncheck sheet yang sedang terbuka → area utama pindah tanpa error.
7. Centang sheet yang belum dimapping → status "belum dimapping"; Generate tetap jalan untuk sheet yang sudah dimapping.

Expected: 7/7 lolos, tidak ada traceback di terminal Streamlit.

- [ ] **Step 3: Cek area lain tidak rusak**

Buka halaman lain di app (`app.py` multipage: material planning, dll) dan pastikan tidak ada error baru — perubahan terisolasi di `sheet_mapping.py`, tapi `app.py:2` mengimport modul ini.

- [ ] **Step 4: Final commit (kalau ada perbaikan dari checklist)**

```bash
git status --short
git add sheet_mapping.py
git commit -m "fix: perbaikan dari verifikasi akhir multi-sheet"
```

Kalau tidak ada perbaikan, lewati commit ini.
