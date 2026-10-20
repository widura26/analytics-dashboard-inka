"""Unit test logika ETL: rekap BOM, cleaning, merge, utilitas.

Jalankan:  python tests/test_etl.py   (dari root proyek)
"""
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gspread
import pandas as pd

import bom_rekap
import material_planning_2 as mp2
from etl_fixtures import (
    BOM_BYTES, BOM_HEADER, BOM_ROWS, SAP_HEADER, SAP_ROWS, FakeUpload,
    to_csv,
)
from csv_utils import grid_to_dataframe
from excel_convert import is_excel_file, read_excel_sheets
from material_planning_2 import (
    BOM_REQUIRED_COLUMNS,
    SAP_REQUIRED_COLUMNS,
    TAB_BOM_CLEAN,
    TAB_BOM_REKAP,
    TAB_MERGE,
    TAB_SAP_CLEAN,
    aggregate_bom,
    build_merge,
    clean_bom,
    clean_sap,
    frame_from_sheet,
    normalize_columns,
    prepare_for_sheets,
    prepare_upload_state,
    read_csv_flexible,
    rekap_sumber_bom,
)


def test_tab_names():
    assert (TAB_BOM_REKAP, TAB_BOM_CLEAN, TAB_SAP_CLEAN, TAB_MERGE) == (
        "TOTAL BOM", "BOM_CLEANED", "SAP_CLEANED", "MERGE_BOM_SAP"
    )
    print("[OK] nama tab:", TAB_BOM_REKAP, TAB_BOM_CLEAN, TAB_SAP_CLEAN,
          TAB_MERGE)


def test_encoding_delimiter():
    # latin-1 + pemisah ';' (gaya export SAP/Excel)
    rows = [["Plän", "1.234"], ["Ünit", "500"]]
    raw = to_csv(["Nama", "Qty"], rows, sep=";", encoding="latin-1")
    df = read_csv_flexible(raw)
    assert df.columns.tolist() == ["Nama", "Qty"], df.columns.tolist()
    assert len(df) == 2
    assert df.iloc[0, 0] == "Plän", df.iloc[0, 0]
    print("[OK] read_csv_flexible: latin-1 + ';'")


def test_normalize_columns_sap():
    """REGRESI: header SAP dengan spasi ekor / NBSP / huruf kecil tetap
    dikenali sebagai kolom wajib (bug: 'Ordered' ada di file tapi ditolak)."""
    messy_header = [
        "Kode Material ",
        "WBS Elem",
        "WBS\xa0Description",   # non-breaking space
        "Mat. Description",
        "Spesifikasi",
        "PR Status",
        "Qty Requested",
        "ordered",               # beda huruf
    ]
    df = pd.DataFrame(
        [["1000123", "A-1.10", "WP-1", "BAUT", "M8", "OPEN", "10", "5"]],
        columns=messy_header,
    )

    clean, stats = clean_sap(df)

    assert "Ordered" in clean.columns, clean.columns.tolist()
    assert "WBS Description" in clean.columns, clean.columns.tolist()
    assert "Kode Material" in clean.columns, clean.columns.tolist()
    assert stats["rows_out"] == 1, stats
    assert int(clean["Ordered"].iloc[0]) == 5
    assert int(clean["Qty Requested"].iloc[0]) == 10
    print("[OK] normalisasi header SAP: spasi ekor + NBSP + huruf kecil")


def test_normalize_columns_direct():
    raw = pd.DataFrame(columns=[" Ordered", "Qty\tRequested "])
    out = normalize_columns(raw, ["Ordered", "Qty Requested"])
    assert out.columns.tolist() == ["Ordered", "Qty Requested"], \
        out.columns.tolist()
    print("[OK] normalize_columns: spasi ekor/tab/NBSP dibersihkan")


def test_sap_missing_column_hint():
    """Kolom yang benar-benar hilang -> error + petunjuk kolom terbaca."""
    df = pd.DataFrame(
        [["1000123", "A-1.10", "WP-1", "BAUT", "M8", "OPEN", "10"]],
        columns=SAP_REQUIRED_COLUMNS[:7],
    )

    try:
        clean_sap(df)
    except ValueError as exc:
        msg = str(exc)
        assert "Ordered" in msg, msg
        assert "Kolom yang terbaca" in msg, msg
        assert "'Qty Requested'" in msg, msg  # repr menampilkan kutip
        print("[OK] error kolom hilang menyertakan petunjuk kolom terbaca")
    else:
        raise AssertionError("seharusnya gagal")


def test_rekap_parse_number():
    assert bom_rekap.parse_number("12,5") == 12.5
    # titik = desimal (algoritma rekap yang diadopsi; keputusan user)
    assert bom_rekap.parse_number("1.250") == 1.25
    assert bom_rekap.parse_number("123 pcs") == 123.0
    assert bom_rekap.parse_number("-") == 0.0
    assert bom_rekap.parse_number("") == 0.0
    assert bom_rekap.parse_number("#N/A") == 0.0
    print("[OK] parse_number rekap")


def test_rekap_without_header():
    grid = [["kolom aneh"], ["tanpa", "header bom"]]
    try:
        bom_rekap.rekap_grid(grid, "x.csv")
    except ValueError as exc:
        assert "Header BOM tidak ditemukan" in str(exc), exc
        print("[OK] tanpa header -> error jelas")
    else:
        raise AssertionError("seharusnya gagal")


def test_rekap_requires_kode():
    """BOM tanpa Kode Material ditolak (itu kunci merge dengan SAP)."""
    grid = [["Material / Komponen"], ["BAUT M8"]]
    try:
        bom_rekap.rekap_grid(grid, "x.csv")
    except ValueError as exc:
        assert "Kode Material" in str(exc), exc
        print("[OK] tanpa Kode Material -> ditolak")
    else:
        raise AssertionError("seharusnya gagal")


def test_clean_bom_missing_columns():
    """clean_bom tetap menjaga kolom wajib bila dipanggil langsung."""
    try:
        clean_bom(pd.DataFrame({"Kode Material": ["1000123"]}))
    except ValueError as exc:
        assert "tidak memiliki kolom wajib" in str(exc), exc
        assert "Kolom yang terbaca" in str(exc), exc
        print("[OK] clean_bom menolak frame tanpa kolom wajib (+ petunjuk)")
    else:
        raise AssertionError("seharusnya gagal")


def test_rekap_flat_csv():
    """CSV dengan header datar (export rekap) -> rekap -> cleaning."""
    raw = to_csv(BOM_HEADER, BOM_ROWS)
    df, report = bom_rekap.rekap_from_bytes(raw, "bom_proyek.csv")

    assert df.columns.tolist() == bom_rekap.KOLOM_REKAP
    assert report["header_start_row"] == 1
    assert report["header_row_count"] == 1
    assert report["detected_fields"] == 10, report["detected_fields"]
    assert report["missing_fields"] == ["BOM"], report["missing_fields"]
    assert report["source_from_column"] is True
    assert report["wbs_from_column"] is True
    assert report["wbs"] == "", "WBS diambil dari kolom, bukan metadata"
    # TOTAL + baris tanpa kode sudah dilewati tahap rekap
    assert report["rows_extracted"] == 4, report
    assert report["rows_skipped_ignored"] == 1, report
    assert report["rows_skipped_empty"] == 1, report
    # sumber & WBS diambil per baris dari CSV
    assert df["Nama Sheet Sumber"].tolist() == [
        "Interior", "Interior", "Interior", "Bogie"
    ], df["Nama Sheet Sumber"].tolist()
    assert df["WBS Elem"].tolist() == [
        "A-1.10", "A-1.10", "A-1.10", "B-2.20"
    ], df["WBS Elem"].tolist()

    # cleaning memakai logika clean_bom yang sudah ada
    clean, stats = clean_bom(df)
    assert stats == {
        "rows_in": 4, "rows_out": 3, "duplicates": 1,
        "blank_rows": 0, "invalid_rows": 0,
    }, stats
    assert clean["Kode Material Delete"].isna().tolist() == [True, False, True]
    # angka sudah dinilai algoritma rekap ('.' = desimal)
    assert df["Stock Total"].iloc[0] == 1.25, df["Stock Total"].iloc[0]
    assert df["QTY PR TOTAL"].iloc[0] == 750.0
    print(f"[OK] rekap CSV datar: {report['rows_extracted']} baris, "
          f"clean -> {len(clean)} baris")


def test_rekap_multirow_header():
    """Header multi-baris + baris metadata WBS (gaya sheet sumber)."""
    kosong = [""] * 10
    grid = [
        ["REKAP BOM PROYEK A", *kosong[1:]],          # baris judul
        ["Kode WBS: A-1.10", *kosong[1:]],            # baris metadata WBS
        ["Kode Material", "Kode Material Delete", "Stock Total",
         "Kode Material Stock", "Material / Komponen", "", "", "", "", ""],
        ["", "", "", "", "", "Spesifikasi", "Qty / TS (Series)", "UoM/Car",
         "Qty Total (Series) + Allowance", "QTY PR TOTAL"],
        ["1000123", "", "1250", "", "BAUT M8", "M8x20", "10", "pcs",
         "1250", "750"],
        ["", "", "", "", "TANPA KODE", "", "", "", "", ""],
        ["TOTAL", "", "", "", "Subtotal", "", "", "", "", ""],
        ["90001", "1000999", "0", "", "PANEL", "KULIT", "2", "pcs",
         "2", "0"],
    ]

    df, report = bom_rekap.rekap_grid(grid, "bom_ku")

    assert report["header_start_row"] == 3, report
    assert report["header_end_row"] == 4, report
    assert report["header_row_count"] == 2, report
    assert report["detected_fields"] == 10, report["detected_fields"]
    assert report["missing_fields"] == ["BOM"], report["missing_fields"]
    assert report["wbs"] == "A-1.10", report["wbs"]
    assert report["wbs_from_column"] is False
    assert report["source_from_column"] is False
    assert report["rows_extracted"] == 2, report
    assert report["rows_skipped_empty"] == 1, report
    assert report["rows_skipped_ignored"] == 1, report

    assert df["Nama Sheet Sumber"].tolist() == ["bom_ku", "bom_ku"]
    assert df["WBS Elem"].tolist() == ["A-1.10", "A-1.10"]
    assert df["Kode Material"].tolist() == ["1000123", "90001"]
    assert df["Kode Material Delete"].tolist() == ["", "1000999"]
    assert df["Stock Total"].tolist() == [1250.0, 0.0], \
        df["Stock Total"].tolist()
    assert df["Material / Komponen"].tolist() == ["BAUT M8", "PANEL"]
    assert df["QTY PR TOTAL"].tolist() == [750.0, 0.0]
    # field tanpa kolom di header -> nilai kosong / nol
    assert df["BOM"].tolist() == ["", ""]
    assert len(df.columns) == 13, len(df.columns)

    # cleaning di atas hasil rekap multi-row
    clean, stats = clean_bom(df)
    assert len(clean) == 2, (len(clean), clean)
    assert clean["Kode Material Delete"].isna().tolist() == [True, False]
    assert clean["Stock Total"].tolist() == [1250.0, 0.0]
    print("[OK] rekap multi-row header (2 baris header, WBS dari Row 2)")


def test_bom():
    """clean_bom langsung dari CSV (tanpa rekap): statistik & nilai."""
    raw = read_csv_flexible(to_csv(BOM_HEADER, BOM_ROWS))
    df, stats = clean_bom(raw)

    assert stats == {
        "rows_in": 6, "rows_out": 3, "duplicates": 1,
        "blank_rows": 0, "invalid_rows": 2,
    }, stats
    assert df["Kode Material"].tolist() == ["1000123", "90001", "1000456"]
    assert df["Kode Material Delete"].isna().tolist() == [True, False, True]
    assert set(BOM_REQUIRED_COLUMNS).issubset(df.columns)
    print(f"[OK] clean_bom: {stats}")
    return df


def test_sap():
    """clean_sap langsung dari CSV: statistik, angka, tanggal, WBS_Group."""
    raw = read_csv_flexible(to_csv(SAP_HEADER, SAP_ROWS))
    df, stats = clean_sap(raw)

    assert stats == {
        "rows_in": 3, "rows_out": 2, "duplicates": 1,
        "blank_rows": 0, "invalid_rows": 0, "wbs_missing": 0,
    }, stats
    assert set(SAP_REQUIRED_COLUMNS).issubset(df.columns)
    assert df["Ordered"].tolist() == [1234, 0], df["Ordered"].tolist()
    assert df["Qty Requested"].tolist() == [10, 5]
    assert df["WBS_Group"].tolist() == ["A-1.10", "B-2.20"]
    assert pd.api.types.is_datetime64_any_dtype(df["Req.Date"]), \
        df["Req.Date"].dtype
    assert df["Req.Date"].notna().all()
    print(f"[OK] clean_sap: {stats}")
    return df


def test_merge(bom, sap):
    """Merge inner on Kode Material + WBS (mengikuti material_planning_2)."""
    agg = aggregate_bom(bom)
    # kode dengan 'Kode Material Delete' terisi ikut terbuang
    assert set(agg["Kode Material"]) == {"1000123", "1000456"}, \
        sorted(set(agg["Kode Material"]))

    merge = build_merge(bom, sap)
    assert len(merge) == 1, len(merge)

    expected_cols = [
        "Kode Material", "Nama Sheet Sumber", "WBS Elem",
        "Kode Material Delete", "Kode Material Stock", "Stock Total",
        "Material / Komponen", "Spesifikasi_x", "Qty / TS (Series)",
        "UoM/Car", "Qty Total (Series) + Allowance", "QTY PR TOTAL",
        "WBS_Group", "WBS Description", "Mat. Description",
        "Spesifikasi_y", "PR Status", "Qty Requested", "Ordered",
    ]
    assert merge.columns.tolist() == expected_cols, merge.columns.tolist()
    assert merge["Kode Material"].tolist() == ["1000123"]
    assert int(merge["Ordered"].iloc[0]) == 1234
    print(f"[OK] build_merge: {len(merge)} baris -> {merge.columns.tolist()}")
    return merge


def test_prepare_for_sheets(bom, sap, merge):
    import numpy as np

    dirty = pd.DataFrame({
        "angka": [np.float64(1.5), np.nan],
        "waktu": [pd.Timestamp("2026-10-05"), pd.NaT],
        "teks": ["ok", None],
        "nint": [np.int64(3), np.int64(4)],
    })
    out = prepare_for_sheets(dirty)
    payload = out.astype(object).where(pd.notna(out), "").values.tolist()
    json.dumps(payload)  # tidak boleh meledak

    assert payload[0][1] == "05.10.2026", payload[0][1]
    assert payload[1][1] == "", payload[1][1]
    assert payload[1][0] == "", payload[1][0]
    assert str(payload[0][2]) == "ok"

    for name, frame in (("bom", bom), ("sap", sap), ("merge", merge)):
        safe = prepare_for_sheets(frame)
        json.dumps(safe.astype(object).values.tolist(), default=str)
    print("[OK] prepare_for_sheets: aman untuk JSON/Sheets API")


# ============================================================
# EXCEL -> GRID (pembacaan langsung semua sheet saat upload)
# ============================================================


def xlsx_bytes(grid) -> bytes:
    """Tulis grid mentah (list of list) sebagai file .xlsx satu sheet."""
    buf = io.BytesIO()
    pd.DataFrame(grid).to_excel(
        buf, index=False, header=False, engine="openpyxl"
    )
    return buf.getvalue()


def xlsx_sheets(sheets: dict) -> bytes:
    """Tulis {nama_sheet: grid} sebagai file .xlsx multi-sheet."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for title, grid in sheets.items():
            pd.DataFrame(grid).to_excel(
                writer, sheet_name=title, index=False, header=False
            )
    return buf.getvalue()


BOM_GRID = [
    ["REKAP BOM EXCEL", "", "", ""],                 # baris judul
    ["Kode WBS: A-1.10", "", "", ""],                # baris metadata WBS
    ["Kode Material", "Material / Komponen", "Stock Total",
     "Qty / TS (Series)"],
    [1000123, "BAUT M8", 1250, 10],                  # angka bulat
    [1000456, "KAWAT", 0.5, 1],                      # desimal tetap
]


def test_excel_all_sheets():
    """Excel dibaca langsung menjadi grid (tanpa konversi CSV): kode/angka
    bulat tanpa '.0', sel kosong jadi '', grid langsung masuk rekap BOM
    multi-baris."""
    assert is_excel_file("bom.xlsx") and is_excel_file("SAP.XLS")
    assert not is_excel_file("bom.csv")

    sheets = read_excel_sheets(xlsx_bytes(BOM_GRID), "bom.xlsx")

    assert len(sheets) == 1, sheets
    sheet = sheets[0]
    assert sheet["label"] == "Sheet1"
    assert (sheet["rows"], sheet["cols"]) == (5, 4), sheet

    grid = sheet["grid"]
    assert grid[3] == ["1000123", "BAUT M8", "1250", "10"], grid[3]
    assert grid[4] == ["1000456", "KAWAT", "0.5", "1"], grid[4]

    # grid langsung dipakai rekap BOM (header + metadata WBS)
    df, report = bom_rekap.rekap_grid(grid, sheet["label"])
    assert report["header_start_row"] == 3, report
    assert report["wbs"] == "A-1.10", report["wbs"]
    assert report["source_label"] == "Sheet1", report["source_label"]
    assert df["Kode Material"].tolist() == ["1000123", "1000456"], \
        df["Kode Material"].tolist()
    assert df["Stock Total"].tolist() == [1250.0, 0.5], \
        df["Stock Total"].tolist()
    print("[OK] excel->grid: kode/angka bulat tanpa '.0'; rekap BOM multi-baris jalan")


def test_excel_all_sheets_multi():
    """Workbook multi-sheet: SEMUA sheet dibaca berurutan, masing-masing
    di-rekap sendiri dengan label = nama sheet (seperti skrip asli yang
    mengiterasi sheet bom_dataset)."""
    header_grid = BOM_GRID[:3]
    interior = header_grid + [
        [1000123, "BAUT M8", 1250, 10],
        [1000456, "KAWAT", 0.5, 1],
    ]
    bogie = header_grid + [
        [1000789, "PEGAS", 2, 5],
    ]
    sheets = read_excel_sheets(
        xlsx_sheets({"Interior": interior, "Bogie": bogie}), "bom.xlsx"
    )
    assert [s["label"] for s in sheets] == ["Interior", "Bogie"], sheets

    hasil = []
    for s in sheets:
        hasil.append(bom_rekap.rekap_grid(s["grid"], s["label"]))

    labels = [rep["source_label"] for df, rep in hasil]
    assert labels == ["Interior", "Bogie"], labels
    # kode WBS Row 2 terdeteksi per sheet
    assert all(rep["wbs"] == "A-1.10" for df, rep in hasil), hasil

    gabung = pd.concat([df for df, rep in hasil], ignore_index=True)
    assert gabung["Nama Sheet Sumber"].tolist() == [
        "Interior", "Interior", "Bogie",
    ], gabung["Nama Sheet Sumber"].tolist()
    assert gabung["Kode Material"].tolist() == [
        "1000123", "1000456", "1000789",
    ], gabung["Kode Material"].tolist()
    print("[OK] excel multi-sheet: semua sheet dibaca, label = nama sheet")


def test_excel_sap_pipeline():
    """SAP berbentuk Excel (angka float bulat + sel tanggal asli) ->
    grid -> grid_to_dataframe -> cleaning SAP, hasil sama dengan jalur CSV."""
    ts = pd.Timestamp
    rows = [SAP_HEADER] + [
        [1000123.0, "A-1.10", "WP-PR1", "BAUT M8", "M8x20", "OPEN",
         10.0, 1234.0, ts("2026-10-05"), ts("2026-10-10"), ts("2026-10-01")],
        [1000123.0, "A-1.10", "WP-PR1", "BAUT M8", "M8x20", "OPEN",
         10.0, 1234.0, ts("2026-10-05"), ts("2026-10-10"), ts("2026-10-01")],
        [1000999.0, "B-2.20", "WP-PR2", "PEGAS", "SPRG", "OPEN",
         5.0, 0.0, ts("2026-10-05"), ts("2026-10-10"), ts("2026-10-01")],
    ]
    sheets = read_excel_sheets(xlsx_bytes(rows), "SAP.xlsx")
    assert sheets[0]["label"] == "Sheet1", sheets[0]["label"]

    raw = grid_to_dataframe(sheets[0]["grid"])
    df, stats = clean_sap(raw)

    assert stats == {
        "rows_in": 3, "rows_out": 2, "duplicates": 1,
        "blank_rows": 0, "invalid_rows": 0, "wbs_missing": 0,
    }, stats
    assert df["Kode Material"].tolist() == ["1000123", "1000999"], \
        df["Kode Material"].tolist()
    assert df["Ordered"].tolist() == [1234, 0], df["Ordered"].tolist()
    assert pd.api.types.is_datetime64_any_dtype(df["Req.Date"])
    assert df["Req.Date"].notna().all()
    print("[OK] excel->grid -> clean_sap: kode tanpa '.0', tanggal Excel terbaca")


def test_grid_to_dataframe():
    """grid_to_dataframe: header duplikat diberi akhiran .1 (gaya
    read_csv), baris kosong dilewati, baris pendek dipad, nilai teks."""
    grid = [
        ["Kode Material", "Qty", "Qty"],       # duplikat
        ["1000123", "10", "5"],
        ["", "", ""],                          # baris kosong -> dilewat
        ["1000456", "1"],                      # pendek -> dipad
    ]
    df = grid_to_dataframe(grid)
    assert df.columns.tolist() == ["Kode Material", "Qty", "Qty.1"], \
        df.columns.tolist()
    assert len(df) == 2, len(df)
    assert df.iloc[1].tolist() == ["1000456", "1", ""], df.iloc[1].tolist()
    print("[OK] grid_to_dataframe: dupe mangling, baris kosong, pad kolom")


def test_mendeteksi_output_rekap():
    """Guard anti-sheet-artefak: sheet Excel berkolom 'Nama Sheet Sumber'
    (output rekap lama, mis. tab TOTAL BOM di bom_dataset) terdeteksi;
    BOM mentah dan anotasi 'Nama Sheet: ...' tidak."""
    artefak = [
        ["Nama Sheet Sumber", "WBS Elem", "Kode Material", "Stock Total"],
        ["Elektrik", "S-22002.1", "B336E12101", "0"],
    ]
    assert bom_rekap.mendeteksi_output_rekap(artefak) is True

    # BOM mentah (grid fixture) tidak punya kolom itu
    assert bom_rekap.mendeteksi_output_rekap(BOM_GRID) is False

    # anotasi biasa di baris metadata bukan kolom sumber
    anotasi = [
        ["REKAP BOM", "", "", ""],
        ["Kode WBS: A-1.10", "Nama Sheet: Interior", "", ""],
        ["Kode Material", "Material / Komponen", "Stock Total", "Qty / TS (Series)"],
    ]
    assert bom_rekap.mendeteksi_output_rekap(anotasi) is False
    print("[OK] deteksi output rekap: artefak terdeteksi, BOM mentah aman")


def test_daftar_target_sheet():
    """29 nama target (Colab) — sejak 2026-10-20 dipakai untuk
    normalisasi nama sheet, BUKAN untuk memfilter rekap."""
    # cocok persis (normalisasi besar/kecil, spasi, tanda hubung)
    assert bom_rekap.nama_sheet_target("De-Scoping")
    assert bom_rekap.nama_sheet_target("de  scoping")
    assert bom_rekap.target_sheet("Interior") == "Interior"

    # nama sheet Excel dipotong 31 karakter -> tetap cocok, dinormalkan
    # ke nama target penuh
    assert bom_rekap.target_sheet("Tools & Consumable Tools Perba") == \
        "Tools & Consumable Tools Perbaikan"

    # bukan target -> None (bukan berarti ditolak; pemakai memakai nama
    # sheet apa adanya, lihat rekap_sumber_bom)
    assert not bom_rekap.nama_sheet_target("Review Bogie")
    assert not bom_rekap.nama_sheet_target("Sheet48")
    assert not bom_rekap.nama_sheet_target("TOTAL BOM")
    assert not bom_rekap.nama_sheet_target("Consumable Pre")
    assert not bom_rekap.nama_sheet_target("Komponen")
    print("[OK] 29 nama target: cocok persis, tahan potongan, bukan filter rekap")


def test_rekap_sumber_bom_tanpa_filter_target():
    """rekap_sumber_bom: SEMUA sheet ikut direkap — tidak ada lagi filter
    29 sheet target (2026-10-20). Sheet agregat tetap dilewati, dan label
    sheet terpotong tetap dinormalkan ke nama target penuh."""
    artefak = [
        ["Nama Sheet Sumber", "Kode Material", "Material / Komponen",
         "Qty / TS (Series)"],
        ["Elektrik", "B1", "Barang 1", "2"],
    ]
    sources = [
        {"label": "Interior", "grid": BOM_GRID},                       # target
        {"label": "Tools & Consumable Tools Perba", "grid": BOM_GRID}, # terpotong
        {"label": "Review Bogie", "grid": BOM_GRID},                   # non-target
        {"label": "Komponen", "grid": BOM_GRID},                       # non-target
        {"label": "TOTAL BOM", "grid": artefak},                       # agregat
    ]
    dfs, reports, dilewati = rekap_sumber_bom(sources)

    # 4 sheet mentah direkap, termasuk 2 yang di luar 29 nama target
    assert len(dfs) == 4, f"harus 4 sheet direkap, bukan {len(dfs)}"
    assert len(reports) == 4, len(reports)
    # label: target cocok persis / terpotong -> nama penuh; sisanya apa adanya
    assert set(dfs[0]["Nama Sheet Sumber"]) == {"Interior"}
    assert set(dfs[1]["Nama Sheet Sumber"]) == {
        "Tools & Consumable Tools Perbaikan"
    }
    assert set(dfs[2]["Nama Sheet Sumber"]) == {"Review Bogie"}
    assert set(dfs[3]["Nama Sheet Sumber"]) == {"Komponen"}
    # hanya sheet agregat yang dilewati (mencegah baris ganda)
    assert len(dilewati) == 1, dilewati
    assert "Nama Sheet Sumber" in dilewati[0], dilewati
    assert not any("29 sheet target" in d for d in dilewati), dilewati
    print("[OK] rekap_sumber_bom: tanpa filter 29 target; agregat tetap "
          "dilewati; nama terpotong dinormalkan")


def test_prepare_upload_state():
    """Mekanisme 'setelah upload, data siap diproses sebelum tombol
    rekap/cleaning ditekan': cache, invalidasi hasil, pembacaan Excel
    semua sheet, error."""
    class FakeUpload:
        def __init__(self, name, data):
            self.name = name
            self.size = len(data)
            self._data = data

        def getvalue(self):
            return self._data

    state = {}

    # CSV: satu sumber berisi bytes asli (tanpa konversi)
    prepare_upload_state(state, "bom", FakeUpload("bom.csv", BOM_BYTES))
    assert state["etl_bom_sources"] == [
        {"label": "bom", "bytes": BOM_BYTES}
    ], state["etl_bom_sources"]
    assert state["etl_bom_src_kind"] == "csv"
    assert "etl_bom_convert_error" not in state

    # Upload sama lagi -> TIDAK diproses ulang (cache tanda tangan file)
    state["etl_bom_sources"] = b"SENTINEL-TIDAK-DIGANTI"
    prepare_upload_state(state, "bom", FakeUpload("bom.csv", BOM_BYTES))
    assert state["etl_bom_sources"] == b"SENTINEL-TIDAK-DIGANTI"

    # File berganti -> hasil lama diinvalidasi, sumber baru disiapkan
    state["etl_bom_clean"] = "lama"
    state["etl_bom_rekap"] = "lama"
    state["etl_merge"] = "lama"
    prepare_upload_state(state, "bom", FakeUpload("bom.csv", BOM_BYTES + b"\n"))
    assert "etl_bom_clean" not in state
    assert "etl_bom_rekap" not in state
    assert "etl_merge" not in state
    assert state["etl_bom_sources"][0]["bytes"] == BOM_BYTES + b"\n"

    # File dihapus -> state upload & hasil dibersihkan
    prepare_upload_state(state, "bom", None)
    assert "etl_bom_sources" not in state
    assert "etl_bom_file_sig" not in state

    # Excel multi-sheet: SEMUA sheet dibaca jadi grid saat diunggah
    state2 = {}
    workbook = xlsx_sheets({
        "Interior": [SAP_HEADER] + SAP_ROWS,
        "Bogie": [SAP_HEADER] + SAP_ROWS[:1],
    })
    prepare_upload_state(state2, "sap", FakeUpload("SAP.XLSX", workbook))
    assert state2["etl_sap_src_kind"] == "xlsx", state2.get("etl_sap_src_kind")
    assert "etl_sap_convert_error" not in state2
    assert state2["etl_sap_converted"]["sheets"] == ["Interior", "Bogie"], \
        state2["etl_sap_converted"]
    assert state2["etl_sap_converted"]["rows"] == {
        "Interior": 1 + len(SAP_ROWS),
        "Bogie": 2,
    }, state2["etl_sap_converted"]["rows"]
    sources = state2["etl_sap_sources"]
    assert [s["label"] for s in sources] == ["Interior", "Bogie"], sources
    assert sources[0]["grid"][0] == SAP_HEADER, sources[0]["grid"][0]
    assert sources[0]["grid"][1] == SAP_ROWS[0], sources[0]["grid"][1]

    # File rusak -> error tercatat, sources tidak ada (tombol nonaktif)
    state3 = {}
    prepare_upload_state(state3, "sap", FakeUpload("rusak.xlsx", b"bukan-file-excel"))
    assert state3.get("etl_sap_sources") is None
    assert "etl_sap_convert_error" in state3
    print("[OK] prepare_upload_state: cache, invalidasi, Excel multi-sheet, error rusak")


def test_pilih_sheet_reset_state():
    """Pemilih sheet BOM (etl_bom_sheets): pilihan bertahan selama file
    yang sama, dan di-reset saat file berganti/dihapus — supaya file baru
    selalu berawal dengan SEMUA sheet tercentang (default)."""
    workbook = xlsx_sheets({"Interior": BOM_GRID, "De-Scoping": BOM_GRID})
    state = {}

    prepare_upload_state(state, "bom", FakeUpload("bom.xlsx", workbook))
    assert [s["label"] for s in state["etl_bom_sources"]] == [
        "Interior", "De-Scoping"
    ], state["etl_bom_sources"]
    assert "etl_bom_sheets" not in state, \
        "belum ada pilihan -> render_sheet_picker mengisi default semua sheet"

    # File yang sama diproses ulang (cache) -> pilihan & hasil tidak di-reset
    state["etl_bom_sheets"] = ["Interior"]
    state["etl_bom_clean"] = "lama"
    prepare_upload_state(state, "bom", FakeUpload("bom.xlsx", workbook))
    assert state["etl_bom_sheets"] == ["Interior"], state["etl_bom_sheets"]
    assert state["etl_bom_clean"] == "lama", "cache file sama tidak boleh hapus hasil"

    # File berganti -> pilihan lama (nama sheet lama) ikut dibersihkan
    prepare_upload_state(state, "bom", FakeUpload("bom_v2.xlsx", workbook))
    assert state["etl_bom_sources"], "sumber file baru harus terbaca"
    assert "etl_bom_sheets" not in state, state.get("etl_bom_sheets")
    assert "etl_bom_clean" not in state

    # File dihapus -> pilihan ikut hilang
    state["etl_bom_sheets"] = ["Interior"]
    prepare_upload_state(state, "bom", None)
    assert "etl_bom_sheets" not in state, state.get("etl_bom_sheets")
    assert "etl_bom_sources" not in state
    print("[OK] pemilih sheet: reset pilihan saat file berubah / dihapus")


def test_frame_from_sheet():
    """frame_from_sheet: '()' di kolom kode opsional -> None (agar .isna()
    di agregasi tetap jalan), angka -> numerik, tanggal dd.mm.yyyy -> datetime."""
    records = [
        {"Kode Material Delete": "", "Kode Material Stock": "",
         "Stock Total": 1250, "Req.Date": "05.10.2026", "Qty Requested": "10"},
        {"Kode Material Delete": "9999", "Kode Material Stock": "",
         "Stock Total": 0.5, "Req.Date": "", "Qty Requested": 0},
    ]

    bom = frame_from_sheet(
        records,
        numeric=["Stock Total"],
        optional_codes=["Kode Material Delete", "Kode Material Stock"],
    )
    assert pd.isna(bom["Kode Material Delete"].iloc[0]), \
        bom["Kode Material Delete"].tolist()
    assert bom["Kode Material Delete"].tolist()[1] == "9999"
    assert bom["Kode Material Stock"].isna().all()
    assert pd.api.types.is_numeric_dtype(bom["Stock Total"]), bom.dtypes
    assert bom["Stock Total"].tolist() == [1250.0, 0.5]

    sap = frame_from_sheet(
        records,
        numeric=["Qty Requested"],
        dates=["Req.Date"],
    )
    assert pd.api.types.is_datetime64_any_dtype(sap["Req.Date"]), sap.dtypes
    assert sap["Req.Date"].iloc[0] == pd.Timestamp("2026-10-05")
    assert pd.isna(sap["Req.Date"].iloc[1])
    assert sap["Qty Requested"].tolist() == [10.0, 0.0]
    print("[OK] frame_from_sheet: '' -> None, angka & tanggal pulih dari Sheets")


# ============================================================
# PENULISAN BESAR (blok + retry) — regresi kasus 503 SAP_CLEANED
# ============================================================

def _respon_gagal(status_code):
    """Response tiruan yang cukup untuk membangun gspread.APIError."""

    class _Respon:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return {"error": {"code": status_code, "message": "uji gangguan"}}

    return _Respon()


class _WorksheetPalsu:
    """Worksheet tiruan yang mencatat clear/resize/update."""

    def __init__(self, baris=1_000_000, kolom=64):
        self.row_count = baris
        self.col_count = kolom
        self.dikosongkan = False
        self.resize_panggilan = []
        self.update_panggilan = []  # [(rentang, values)]

    def clear(self):
        self.dikosongkan = True
        return {}

    def resize(self, rows=None, cols=None):
        self.resize_panggilan.append((rows, cols))
        self.row_count, self.col_count = rows, cols
        return {}

    def update(self, values, range_name=None, **kwargs):
        self.update_panggilan.append((range_name, [list(b) for b in values]))
        return {}


class _SpreadsheetPalsu:
    def __init__(self, worksheet=None):
        self._worksheet = worksheet

    def worksheet(self, tab_name):
        if self._worksheet is None:
            raise gspread.exceptions.WorksheetNotFound(tab_name)
        return self._worksheet

    def add_worksheet(self, title, rows, cols):
        self._worksheet = _WorksheetPalsu(baris=int(rows), kolom=int(cols))
        return self._worksheet


def test_tulis_besar_terpecah_blok():
    """DataFrame besar dikirim per blok berurutan, bukan satu request raksasa."""
    n = 4501
    df = pd.DataFrame({
        "A": range(n),
        "B": ["x"] * n,
        "C": [1.5] * n,
        "D": ["ket"] * n,
    })
    ws = _WorksheetPalsu()
    jumlah = mp2.write_recap_to_sheets(_SpreadsheetPalsu(ws), df, "SAP_CLEANED")

    assert jumlah == n
    assert ws.dikosongkan
    assert [r for r, _ in ws.update_panggilan] == [
        "A1:D2000", "A2001:D4000", "A4001:D4502",
    ]
    gabung = [b for _, blok in ws.update_panggilan for b in blok]
    assert gabung[0] == list(df.columns)
    assert len(gabung) == n + 1
    assert gabung[1][0] == 0 and gabung[-1][0] == n - 1
    assert not ws.resize_panggilan  # ukuran sheet sudah cukup
    print("[OK] tulis besar: 4.502 baris -> 3 request berurutan, data utuh")


def test_tulis_kecil_satu_request():
    df = pd.DataFrame({"A": [1, 2], "B": ["a", "b"]})
    ws = _WorksheetPalsu()
    jumlah = mp2.write_recap_to_sheets(_SpreadsheetPalsu(ws), df, "TOTAL BOM")
    assert jumlah == 2
    assert [r for r, _ in ws.update_panggilan] == ["A1:B3"]
    print("[OK] tulis kecil: satu request tunggal A1:B3")


def test_tulis_resize_bila_kekecilan():
    df = pd.DataFrame({"A": range(50), "B": ["z"] * 50, "C": range(50)})
    ws = _WorksheetPalsu(baris=10, kolom=2)
    mp2.write_recap_to_sheets(_SpreadsheetPalsu(ws), df, "X")
    assert ws.resize_panggilan == [(51, 3)]
    assert [r for r, _ in ws.update_panggilan] == ["A1:C51"]
    print("[OK] tulis besar: resize dulu bila sheet kekecilan")


def test_tulis_tab_baru_dibuat():
    df = pd.DataFrame({"A": [1]})
    sh = _SpreadsheetPalsu(None)
    mp2.write_recap_to_sheets(sh, df, "BELUM_ADA")
    assert sh._worksheet is not None
    assert not sh._worksheet.dikosongkan
    assert [r for r, _ in sh._worksheet.update_panggilan] == ["A1:A2"]
    print("[OK] tulis besar: tab baru dibuat otomatis")


def test_retry_transient_gangguan_sementara():
    """503 diulang dengan backoff 2, 4 dtk, lalu sukses."""
    import time as time_mod

    asli_sleep = time_mod.sleep
    jeda = []
    time_mod.sleep = lambda d: jeda.append(d)
    try:
        panggilan = {"n": 0}

        def gagal_dulu():
            panggilan["n"] += 1
            if panggilan["n"] <= 2:
                raise gspread.exceptions.APIError(_respon_gagal(503))
            return "berhasil"

        assert mp2._retry_transient(gagal_dulu) == "berhasil"
        assert panggilan["n"] == 3
        assert jeda == [2, 4]
    finally:
        time_mod.sleep = asli_sleep
    print("[OK] retry: 503 diulang backoff [2, 4] dtk lalu sukses")


def test_retry_transient_error_permanen():
    """403 tidak boleh diulang — langsung dilempar tanpa jeda."""
    import time as time_mod

    asli_sleep = time_mod.sleep
    jeda = []
    time_mod.sleep = lambda d: jeda.append(d)
    try:
        panggilan = {"n": 0}

        def selalu_403():
            panggilan["n"] += 1
            raise gspread.exceptions.APIError(_respon_gagal(403))

        melempar = False
        try:
            mp2._retry_transient(selalu_403)
        except gspread.exceptions.APIError:
            melempar = True
        assert melempar
        assert panggilan["n"] == 1
        assert jeda == []
    finally:
        time_mod.sleep = asli_sleep
    print("[OK] retry: 403 permanen langsung melempar (tanpa jeda)")


def test_retry_transient_habis_percobaan():
    """503 terus-menerus: 5 percobaan, backoff [2, 4, 8, 16] dtk, lalu lempar."""
    import time as time_mod

    asli_sleep = time_mod.sleep
    jeda = []
    time_mod.sleep = lambda d: jeda.append(d)
    try:
        panggilan = {"n": 0}

        def selalu_503():
            panggilan["n"] += 1
            raise gspread.exceptions.APIError(_respon_gagal(503))

        melempar = False
        try:
            mp2._retry_transient(selalu_503)
        except gspread.exceptions.APIError:
            melempar = True
        assert melempar
        assert panggilan["n"] == 5
        assert jeda == [2, 4, 8, 16]
    finally:
        time_mod.sleep = asli_sleep
    print("[OK] retry: 503 terus-menerus -> 5 percobaan + backoff penuh, lempar")


def test_unify_mixed_text():
    """Kolom object campur teks+angka -> seragam str (pencegah
    ArrowTypeError 'Expected bytes, got int' saat dataframe ditampilkan)."""
    df = pd.DataFrame({
        "kode": ["1000999", 1000999, 1000.0],
        "spesifikasi": ["teks", 2026, "lain"],
        "murni_teks": ["a", "b", "c"],
        "murni_angka": [1, 2, 3],
        "campur_kosong": [None, "", 42],
        "qty": [1.5, 2.5, 3.5],
    })

    out = mp2._unify_mixed_text(df.copy())

    assert out["kode"].tolist() == ["1000999", "1000999", "1000"], \
        out["kode"].tolist()
    assert out["spesifikasi"].tolist() == ["teks", "2026", "lain"], \
        out["spesifikasi"].tolist()
    assert out["murni_teks"].tolist() == ["a", "b", "c"]
    assert pd.api.types.is_integer_dtype(out["murni_angka"]), out.dtypes
    assert out["campur_kosong"].tolist() == [None, "", "42"], \
        out["campur_kosong"].tolist()
    assert pd.api.types.is_float_dtype(out["qty"]), out.dtypes
    print("[OK] _unify_mixed_text: campur -> str; kolom murni tak tersentuh")


def test_frame_from_sheet_seragam_teks():
    """Sel angka yang terselip di kolom teks saat dibaca balik dari
    Sheets (UNFORMATTED_VALUE) -> dijadikan str oleh frame_from_sheet."""
    records = [
        {"Kode Material": 1000999, "Spesifikasi": 2026},
        {"Kode Material": "9999AAA", "Spesifikasi": "Baja tahan karat"},
    ]

    df = frame_from_sheet(records)

    assert df["Kode Material"].tolist() == ["1000999", "9999AAA"], \
        df["Kode Material"].tolist()
    assert df["Spesifikasi"].tolist() == ["2026", "Baja tahan karat"], \
        df["Spesifikasi"].tolist()
    print("[OK] frame_from_sheet: kolom teks tercampur angka -> str seragam")


if __name__ == "__main__":
    test_tab_names()
    test_encoding_delimiter()
    test_normalize_columns_direct()
    test_normalize_columns_sap()
    test_sap_missing_column_hint()
    test_rekap_parse_number()
    test_rekap_without_header()
    test_rekap_requires_kode()
    test_clean_bom_missing_columns()
    test_rekap_flat_csv()
    test_rekap_multirow_header()
    bom = test_bom()
    sap = test_sap()
    merge = test_merge(bom, sap)
    test_prepare_for_sheets(bom, sap, merge)
    test_excel_all_sheets()
    test_excel_all_sheets_multi()
    test_excel_sap_pipeline()
    test_grid_to_dataframe()
    test_mendeteksi_output_rekap()
    test_daftar_target_sheet()
    test_rekap_sumber_bom_tanpa_filter_target()
    test_prepare_upload_state()
    test_pilih_sheet_reset_state()
    test_frame_from_sheet()
    test_tulis_besar_terpecah_blok()
    test_tulis_kecil_satu_request()
    test_tulis_resize_bila_kekecilan()
    test_tulis_tab_baru_dibuat()
    test_retry_transient_gangguan_sementara()
    test_retry_transient_error_permanen()
    test_retry_transient_habis_percobaan()
    test_unify_mixed_text()
    test_frame_from_sheet_seragam_teks()
    print("\nSEMUA TEST LULUS")
