"""End-to-end test aplikasi Streamlit (AppTest) untuk alur ETL Statistic Dashboard.

Jalankan:  python tests/test_app.py   (dari root proyek)

Catatan: AppTest tidak mendukung file_uploader, sehingga tombol dipicu lewat
flag session_state oleh tests/app_under_test.py (lihat docstring di sana).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.errors import AppTestError

import material_planning_2 as mp2
from etl_fixtures import BOM_BYTES, BAD_BOM_BYTES, SAP_BYTES

APP = Path(__file__).parent / "app_under_test.py"


def app_test() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=60)


def assert_no_exception(at: AppTest, label: str):
    assert not at.exception, f"{label}: {at.exception}"


def button_keys(at: AppTest):
    return [b.key for b in at.button]


def info_text(at: AppTest) -> str:
    return " | ".join(i.value for i in at.info)


def success_text(at: AppTest) -> str:
    return " | ".join(s.value for s in at.success)


def caption_text(at: AppTest) -> str:
    return " | ".join(c.value for c in at.caption)


def warning_text(at: AppTest) -> str:
    return " | ".join(w.value for w in at.warning)


def build_fake_tabs(at: AppTest) -> dict:
    """Tiruan isi tab spreadsheet dari state sesi ETL (apa adanya seperti
    hasil get_all_records — prepare_for_sheets = yang ditulis ke Sheets)."""
    tabs = {}
    for key, tab in (
        ("etl_bom_rekap", mp2.TAB_BOM_REKAP),
        ("etl_bom_clean", mp2.TAB_BOM_CLEAN),
        ("etl_sap_clean", mp2.TAB_SAP_CLEAN),
        ("etl_merge", mp2.TAB_MERGE),
    ):
        safe = mp2.prepare_for_sheets(at.session_state[key])
        tabs[tab] = [
            dict(zip(safe.columns, row)) for row in safe.values.tolist()
        ]
    return tabs


def merger_subheaders(at: AppTest):
    return [s.value for s in at.subheader
            if s.value.startswith("Merger of BOM")]


def bom_sheet_grid(kode, nama, wbs="A-1.10"):
    """Grid satu sheet BOM (judul + metadata WBS + header + 1 baris data)."""
    return [
        ["REKAP BOM", "", "", ""],
        [f"Kode WBS: {wbs}", "", "", ""],
        ["Kode Material", "Material / Komponen", "Stock Total",
         "Qty / TS (Series)"],
        [kode, nama, "1250", "10"],
    ]


def pasang_sheet_bom(at: AppTest):
    """Isi state dua sheet Excel BOM — tiruan hasil prepare_upload_state
    (AppTest tidak mendukung file_uploader, lihat app_under_test.py)."""
    at.session_state["etl_bom_sources"] = [
        {"label": "Interior", "grid": bom_sheet_grid("1000123", "BAUT M8")},
        {"label": "De-Scoping",
         "grid": bom_sheet_grid("777001", "PANEL SCOPING", "B-2.20")},
    ]
    at.session_state["etl_bom_src_kind"] = "xlsx"
    at.session_state["etl_bom_converted"] = {
        "sheets": ["Interior", "De-Scoping"],
        "rows": {"Interior": 4, "De-Scoping": 4},
        "total_rows": 8,
    }


def picker_sheet_bom(at: AppTest):
    """Elemen multiselect pemilih sheet BOM (key 'etl_bom_sheets')."""
    matches = [m for m in at.multiselect if m.key == "etl_bom_sheets"]
    assert matches, f"pemilih sheet tidak tampil: {[m.key for m in at.multiselect]}"
    return matches[0]


def test_fresh_page():
    at = app_test()
    at.run()

    assert_no_exception(at, "halaman kosong")
    assert "btn_generate" not in button_keys(at), button_keys(at)

    infos = info_text(at)
    assert "Menunggu: Bill Of Material, SAP." in infos, infos
    assert "Dashboard tampil setelah" in infos, infos

    # Auto-restore sesi segar harus diam bila belum ada hasil
    # (pengguna baru tidak dikasih peringatan membingungkan).
    assert "Belum ada hasil ETL" not in warning_text(at), warning_text(at)
    print("[OK] halaman kosong: tanpa exception; Generate & dashboard "
          "menunggu ETL; auto-restore diam bila spreadsheet kosong")


def test_full_flow():
    at = app_test()
    at.run()
    assert_no_exception(at, "halaman awal")

    # ------------------------------------------------------------
    # 1. Rekap & cleaning BOM
    # ------------------------------------------------------------
    at.session_state["bom_bytes"] = BOM_BYTES
    at.session_state["simulate_bom"] = True
    at.run()
    assert_no_exception(at, "rekap BOM")

    succ = success_text(at)
    assert "TOTAL BOM" in succ, succ
    assert "BOM_CLEANED" in succ, succ
    assert "3 baris bersih" in succ, succ
    assert "4 baris rekap" in succ, succ
    assert at.session_state["_writes"] == [
        ("TOTAL BOM", 4), ("BOM_CLEANED", 3)
    ], at.session_state["_writes"]
    assert "btn_generate" not in button_keys(at)
    infos = info_text(at)
    assert "Menunggu: SAP." in infos, infos
    print("[OK] rekap+clean BOM -> TOTAL BOM (4) & BOM_CLEANED (3); "
          "Generate masih tersembunyi")

    # ------------------------------------------------------------
    # 2. Rekap & cleaning SAP
    # ------------------------------------------------------------
    at.session_state["sap_bytes"] = SAP_BYTES
    at.session_state["simulate_sap"] = True
    at.run()
    assert_no_exception(at, "rekap SAP")

    succ = success_text(at)
    assert "SAP_CLEANED" in succ, succ
    assert ("SAP_CLEANED", 2) in at.session_state["_writes"], \
        at.session_state["_writes"]
    assert "btn_generate" in button_keys(at), button_keys(at)
    infos = info_text(at)
    assert "Menunggu" not in infos, infos
    print("[OK] rekap+clean SAP -> SAP_CLEANED (2); tombol Generate muncul")

    # ------------------------------------------------------------
    # 3. Generate data (merge)
    # ------------------------------------------------------------
    at.session_state["simulate_generate"] = True
    at.run()
    assert_no_exception(at, "generate")

    writes = at.session_state["_writes"]
    assert writes == [
        ("TOTAL BOM", 4), ("BOM_CLEANED", 3),
        ("SAP_CLEANED", 2), ("MERGE_BOM_SAP", 1),
    ], writes
    assert "Dashboard tampil setelah" not in info_text(at)
    # plotly + bar_chart (vega) tidak punya accessor property di AppTest
    assert len(at.get("plotly_chart")) == 2, len(at.get("plotly_chart"))
    assert len(at.get("vega_lite_chart")) >= 1, len(at.get("vega_lite_chart"))
    assert len(at.dataframe) >= 5, len(at.dataframe)

    caps = caption_text(at)
    assert "Hasil merge terakhir: 1 baris" in caps, caps
    print(f"[OK] generate -> 4 tab ditulis; dashboard tampil "
          f"({len(at.dataframe)} dataframe, {len(at.get('plotly_chart'))} "
          f"chart)")

    # ------------------------------------------------------------
    # 4. Filter project
    # ------------------------------------------------------------
    assert at.selectbox[0].options == ["Semua", "A-1.10", "B-2.20"], \
        at.selectbox[0].options
    at.selectbox[0].select("A-1.10").run()
    assert_no_exception(at, "filter project")
    assert at.selectbox[1].options == ["Semua", "Interior"], \
        at.selectbox[1].options
    subs = merger_subheaders(at)
    assert subs and subs[-1].endswith("(1)"), subs
    print(f"[OK] filter project A-1.10; opsi sheet: {at.selectbox[1].options}")

    # ------------------------------------------------------------
    # 5. Filter sheet sumber
    # ------------------------------------------------------------
    at.selectbox[1].select("Interior").run()
    assert_no_exception(at, "filter sheet sumber")
    subs = merger_subheaders(at)
    assert subs and subs[-1].endswith("(1)"), subs
    print("[OK] filter sheet sumber 'Interior'")

    # ------------------------------------------------------------
    # 6. Toggle filter merger (PR Partial)
    # ------------------------------------------------------------
    at.toggle[0].set_value(True).run()   # "PR Partial"
    assert_no_exception(at, "toggle PR Partial")
    subs = merger_subheaders(at)
    assert subs and subs[-1].endswith("(0)"), subs
    print("[OK] toggle PR Partial -> merger (0)")

    # ------------------------------------------------------------
    # 7. Reset ETL
    # ------------------------------------------------------------
    at.button(key="btn_reset_etl").click().run()
    assert_no_exception(at, "reset ETL")
    assert at.session_state.get("etl_bom_clean") is None
    assert at.session_state.get("etl_sap_clean") is None
    assert at.session_state.get("etl_merge") is None
    assert at.session_state.get("etl_bom_rekap") is None
    assert "btn_generate" not in button_keys(at)
    # nama spreadsheet tidak ikut ter-reset (widget state)
    assert at.session_state.get("etl_spreadsheet_name") == "etl_bom_sap"
    print("[OK] Reset ETL menghapus seluruh state pipeline")


def test_bad_bom_reported():
    at = app_test()
    at.session_state["bom_bytes"] = BAD_BOM_BYTES
    at.session_state["simulate_bom"] = True
    at.run()

    assert_no_exception(at, "file BOM tanpa header dikenali")
    assert at.error, "harus menampilkan error"
    assert "Header BOM tidak ditemukan" in at.error[0].value, \
        at.error[0].value
    assert at.session_state.get("etl_bom_clean") is None
    print(f"[OK] header tak dikenal dilaporkan: "
          f"{at.error[0].value[:80]}...")


def test_pilih_sheet_rekap_bom():
    """User memilih sheet mana saja dari file BOM Excel yang ikut tahap
    Rekap & Cleaning: default semua sheet, pilihan menyaring hasil, dan
    tanpa centang tombol tidak memicu ETL."""
    at = app_test()
    pasang_sheet_bom(at)
    at.run()
    assert_no_exception(at, "pemilih sheet tampil")

    ms = picker_sheet_bom(at)
    assert ms.options == ["Interior", "De-Scoping"], ms.options
    assert ms.value == ["Interior", "De-Scoping"], \
        f"default harus semua sheet, bukan {ms.value}"
    caps = caption_text(at)
    assert "2/2 sheet dipilih untuk rekap & cleaning." in caps, caps
    print("[OK] pemilih sheet: multiselect muncul, default semua sheet "
          "tercentang")

    # ------------------------------------------------------------
    # 1. Pilihan default -> KEDUA sheet ikut rekap & cleaning
    # ------------------------------------------------------------
    at.button(key="btn_clean_bom").click().run()
    assert_no_exception(at, "rekap dua sheet")

    rekap = at.session_state["etl_bom_rekap"]
    assert len(rekap) == 2, len(rekap)
    assert set(rekap["Nama Sheet Sumber"]) == {"Interior", "De-Scoping"}, \
        rekap["Nama Sheet Sumber"].tolist()
    bersih = at.session_state["etl_bom_clean"]["Kode Material"].tolist()
    assert bersih == ["1000123", "777001"], bersih
    assert ("TOTAL BOM", 2) in at.session_state["_writes"], \
        at.session_state["_writes"]
    print("[OK] pilihan default: kedua sheet direkap (2 baris) & dibersihkan")

    # ------------------------------------------------------------
    # 2. Pilih satu sheet -> hanya sheet itu yang diproses
    # ------------------------------------------------------------
    picker_sheet_bom(at).set_value(["De-Scoping"]).run()
    assert_no_exception(at, "ubah pilihan sheet")
    caps = caption_text(at)
    assert "1/2 sheet dipilih untuk rekap & cleaning." in caps, caps

    at.button(key="btn_clean_bom").click().run()
    assert_no_exception(at, "rekap satu sheet")

    rekap = at.session_state["etl_bom_rekap"]
    assert rekap["Nama Sheet Sumber"].tolist() == ["De-Scoping"], \
        rekap["Nama Sheet Sumber"].tolist()
    bersih = at.session_state["etl_bom_clean"]["Kode Material"].tolist()
    assert bersih == ["777001"], bersih
    assert ("TOTAL BOM", 1) in at.session_state["_writes"], \
        at.session_state["_writes"]
    print("[OK] pilih 1 sheet: rekap & cleaning hanya memuat sheet terpilih")

    # ------------------------------------------------------------
    # 3. Tanpa centang apa pun -> ETL berhenti, hasil lama tidak tertimpa
    # ------------------------------------------------------------
    picker_sheet_bom(at).set_value([]).run()
    assert_no_exception(at, "kosongkan pilihan")
    assert "Belum ada sheet yang dicentang" in info_text(at), info_text(at)
    assert "Centang minimal satu sheet di atas." in caption_text(at), \
        caption_text(at)
    rekap = at.session_state["etl_bom_rekap"]
    assert rekap["Nama Sheet Sumber"].tolist() == ["De-Scoping"], \
        "hasil rekap sebelumnya tidak boleh diganti tanpa sheet terpilih"

    # Tombol nonaktif: browser (dan AppTest) tidak bisa mengkliknya,
    # sehingga ETL tidak mungkin jalan tanpa minimal satu sheet terpilih.
    try:
        at.button(key="btn_clean_bom").click().run()
    except AppTestError as exc:
        assert "disabled button" in str(exc), str(exc)
    else:
        raise AssertionError(
            "tombol Rekap & Cleaning harus nonaktif saat tidak ada sheet "
            "terpilih"
        )
    print("[OK] tanpa centang: peringatan tampil dan tombol Rekap & Cleaning "
          "nonaktif")


def test_lewati_baris_berformat():
    """Baris yang ditandai orang di Excel (tersembunyi/collapse, dicoret,
    blok hitam) TIDAK ikut rekap & cleaning, dan jumlahnya dilaporkan
    supaya bisa diaudit."""
    at = app_test()
    grid = bom_sheet_grid("1000123", "BAUT M8")
    grid.append(["1000456", "BARIS DICORET", "999", "7"])   # idx 4
    at.session_state["etl_bom_sources"] = [
        {"label": "Interior", "grid": grid, "tandai": {4: ["dicoret"]}},
    ]
    at.session_state["etl_bom_src_kind"] = "xlsx"
    at.session_state["etl_bom_converted"] = {
        "sheets": ["Interior"],
        "rows": {"Interior": len(grid)},
        "total_rows": len(grid),
    }
    at.run()
    assert_no_exception(at, "sumber bertanda tampil")

    at.button(key="btn_clean_bom").click().run()
    assert_no_exception(at, "rekap baris bertanda")

    rekap = at.session_state["etl_bom_rekap"]
    assert rekap["Kode Material"].tolist() == ["1000123"], \
        rekap["Kode Material"].tolist()
    report = at.session_state["etl_bom_rekap_report"][0]
    assert report["rows_skipped_marked"] == 1, report
    assert report["mark_summary"] == {"dicoret": 1}, report["mark_summary"]
    assert report["rows_extracted"] == 1, report

    info = info_text(at)
    assert "1 baris ditandai di Excel tidak ikut rekap" in info, info
    assert "[1 dicoret]" in info, info
    assert "Interior (1)" in info, info

    bersih = at.session_state["etl_bom_clean"]["Kode Material"].tolist()
    assert bersih == ["1000123"], bersih
    print("[OK] baris ditandai (dicoret/hidden/blok) tidak ikut rekap & "
          "cleaning; jumlah + alasan dilaporkan")


def test_rekap_sheet_di_luar_29_target():
    """Sheet yang NAMANYA bukan salah satu dari 29 nama target (mis.
    'Komponen') tetap ikut rekap & cleaning — filter itu sudah dilepas
    (2026-10-20); dulu menghasilkan error 'di luar daftar 29 sheet target'."""
    at = app_test()
    at.session_state["etl_bom_sources"] = [
        {"label": "Komponen",
         "grid": bom_sheet_grid("555001", "T BOLT", "C-3.30")},
    ]
    at.session_state["etl_bom_src_kind"] = "xlsx"
    at.session_state["etl_bom_converted"] = {
        "sheets": ["Komponen"],
        "rows": {"Komponen": 4},
        "total_rows": 4,
    }
    at.run()
    assert_no_exception(at, "sheet non-target tampil di UI")
    assert "di luar daftar 29 sheet target" not in warning_text(at), \
        warning_text(at)

    at.button(key="btn_clean_bom").click().run()
    assert_no_exception(at, "rekap sheet non-target")
    assert not at.error, [e.value for e in at.error]

    rekap = at.session_state["etl_bom_rekap"]
    assert rekap["Nama Sheet Sumber"].tolist() == ["Komponen"], \
        rekap["Nama Sheet Sumber"].tolist()
    bersih = at.session_state["etl_bom_clean"]["Kode Material"].tolist()
    assert bersih == ["555001"], bersih
    assert "1 sheet direkap dari 1 sumber dipilih." in caption_text(at), \
        caption_text(at)
    assert ("TOTAL BOM", 1) in at.session_state["_writes"], \
        at.session_state["_writes"]
    print("[OK] sheet di luar 29 nama target ('Komponen') direkap & "
          "dibersihkan tanpa error")


def test_restore_from_spreadsheet():
    """Setelah refresh browser (sesi baru, state kosong), tombol sidebar
    'Muat hasil dari spreadsheet' membaca 4 tab -> pipeline & dashboard
    pulih tanpa unggah file lagi."""
    # 1) Sesi #1: jalankan ETL penuh untuk menghasilkan frame asli.
    at1 = app_test()
    at1.session_state["bom_bytes"] = BOM_BYTES
    at1.session_state["sap_bytes"] = SAP_BYTES
    at1.session_state["simulate_bom"] = True
    at1.session_state["simulate_sap"] = True
    at1.run()
    assert_no_exception(at1, "sesi #1: ETL")
    at1.session_state["simulate_generate"] = True
    at1.run()
    assert_no_exception(at1, "sesi #1: generate")

    # 2) Tiru isi tab spreadsheet dari state sesi #1.
    tabs = build_fake_tabs(at1)

    # 3) Sesi #2 (setara refresh browser): state kosong, pulihkan dari tab.
    at2 = app_test()
    at2.session_state["_fake_tabs"] = tabs
    at2.session_state["simulate_restore"] = True
    at2.run()
    assert_no_exception(at2, "sesi #2: restore setelah refresh")

    bom = at2.session_state.get("etl_bom_clean")
    sap = at2.session_state.get("etl_sap_clean")
    merge = at2.session_state.get("etl_merge")
    assert bom is not None and len(bom) == 3, None if bom is None else len(bom)
    assert sap is not None and len(sap) == 2, None if sap is None else len(sap)
    assert merge is not None and len(merge) == 1, None if merge is None else len(merge)

    # Tipenya benar: '' kode opsional -> None (filter agregasi tetap
    # jalan), angka -> numerik, tanggal -> datetime.
    deleted = bom["Kode Material Delete"]
    assert deleted.isna().sum() == 2, deleted.tolist()   # '' -> None
    assert deleted.dropna().tolist() == ["1000999"], deleted.tolist()
    assert pd.api.types.is_numeric_dtype(bom["Stock Total"]), bom.dtypes
    assert pd.api.types.is_numeric_dtype(sap["Qty Requested"]), sap.dtypes
    assert pd.api.types.is_datetime64_any_dtype(sap["Req.Date"]), sap.dtypes

    # Agregasi (langkah merge) menghasilkan hasil sama dengan sesi asli.
    assert len(mp2.aggregate_bom(bom)) == len(
        mp2.aggregate_bom(at1.session_state["etl_bom_clean"])
    )
    assert len(mp2.aggregate_sap(sap)) == len(
        mp2.aggregate_sap(at1.session_state["etl_sap_clean"])
    )

    # Pipeline menampilkan status selesai + tombol Generate muncul lagi;
    # dashboard tampil penuh (2 chart).
    assert "btn_generate" in button_keys(at2), button_keys(at2)
    assert "BOM selesai direkap" in success_text(at2), success_text(at2)
    assert len(at2.get("plotly_chart")) == 2, len(at2.get("plotly_chart"))
    print("[OK] restore dari spreadsheet: pipeline & dashboard pulih "
          "setelah refresh (tanpa unggah ulang)")


def test_auto_restore_setelah_refresh():
    """Sesi baru TANPA klik apa pun (setara refresh browser): hasil ETL
    dipulihkan otomatis — dashboard tampil kembali tanpa tombol sidebar."""
    # 1) Sesi #1: ETL penuh -> bangun isi tab tiruan.
    at1 = app_test()
    at1.session_state["bom_bytes"] = BOM_BYTES
    at1.session_state["sap_bytes"] = SAP_BYTES
    at1.session_state["simulate_bom"] = True
    at1.session_state["simulate_sap"] = True
    at1.run()
    assert_no_exception(at1, "auto: sesi #1 ETL")
    at1.session_state["simulate_generate"] = True
    at1.run()
    assert_no_exception(at1, "auto: sesi #1 generate")
    tabs = build_fake_tabs(at1)

    # 2) Sesi #2 = refresh: state kosong, TANPA flag simulate_restore.
    at2 = app_test()
    at2.session_state["_fake_tabs"] = tabs
    at2.run()
    assert_no_exception(at2, "auto: sesi #2 auto-restore")

    assert at2.session_state.get("etl_merge") is not None
    # Ringkasan restore menyebut ke-4 tab (tahan perubahan wording).
    assert "MERGE_BOM_SAP 1 baris" in success_text(at2), \
        success_text(at2)
    assert "btn_generate" in button_keys(at2), button_keys(at2)
    assert len(at2.get("plotly_chart")) == 2, len(at2.get("plotly_chart"))
    print("[OK] auto-restore: sesi segar memulihkan pipeline & dashboard "
          "tanpa klik apa pun")


if __name__ == "__main__":
    test_fresh_page()
    test_full_flow()
    test_bad_bom_reported()
    test_pilih_sheet_rekap_bom()
    test_rekap_sheet_di_luar_29_target()
    test_lewati_baris_berformat()
    test_restore_from_spreadsheet()
    test_auto_restore_setelah_refresh()
    print("\nSEMUA APP TEST LULUS")
