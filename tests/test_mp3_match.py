"""Uji validasi BOM <-> SAP di Statistic Dashboard 2 (material_planning_3).

Jalankan:  python tests/test_mp3_match.py   (dari root proyek)

Dua lapis uji:
1. Unit test fungsi murni (tanpa Streamlit runtime): derive_status_sap,
   check_qty_match, check_status_match, aggregate_sap_for_match,
   match_with_sap, attach_match, match_summary, match_crosstab.
2. AppTest halaman penuh lewat tests/mp3_under_test.py — data disuntikkan
   lewat session_state['_fake_csv'] (BOM) & ['_fake_sap_csv'] (SAP),
   sehingga tidak ada jaringan/secrets yang disentuh.
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from streamlit.testing.v1 import AppTest

import material_planning_3 as mp3

MP3_APP = Path(__file__).parent / "mp3_under_test.py"

# ============================================================
# FIXTURE
# ============================================================

# Persis kolom export sheet "Tindak Lanjut BOM" (lihat DEFAULT_BOM_URL).
BOM_HEADER = [
    "Sheet Asal", "REV", "NO", "Kode Material Konversi", "Kode Material",
    "Kode Material IMS", "Deskripsi Material", "Satuan", "Group", "Trainset",
    "Kebutuhan", "SUM", "Tindak Lanjut", "Status VE", "No PO VE", "Qty PR VE",
    "Status MS", "No PO MS", "Qty PR MS", "Deliv Date SAP",
]

BOM_ROWS = [
    # total kebutuhan = 10 + 10 = 20, SAP cuma 15 -> SAP KURANG
    ["Komponen", "", "1", "1000123", "1000123", "", "BAUT M8", "pcs", "A",
     "TS 9", "10", "10", "PR BARU", "", "", "0", "", "", "0", ""],
    ["Komponen", "", "1", "1000123", "1000123", "", "BAUT M8", "pcs", "A",
     "TS 10", "10", "10", "PR BARU", "", "", "0", "", "", "0", ""],
    # pakai stok & tidak ada di SAP -> TIDAK ADA DI SAP
    ["Komponen", "", "2", "777001", "777001", "", "PANEL", "pcs", "A",
     "TS 9", "5", "5", "MENGGUNAKAN STOK", "", "", "0", "", "", "0", ""],
    # kode SAP tercatat pakai kode KONVERSI (A28RK12017), bukan kode utama
    ["Komponen", "", "3", "A28RK12017", "B52TH1222", "", "SEAL", "pcs", "A",
     "TS 9", "3", "3", "PR BARU", "", "", "0", "", "", "0", ""],
    # BOM bilang BELUM PR, SAP punya Qty Requested 5 -> status tak sesuai
    ["Komponen", "", "4", "1000999", "1000999", "", "PEGAS", "pcs", "A",
     "TS 9", "4", "4", "BELUM PR", "", "", "0", "", "", "0", ""],
    # tanpa 'Tindak Lanjut' -> dibuang prepare_data, tidak ikut validasi
    ["Komponen", "", "5", "", "", "", "TANPA TL", "pcs", "A",
     "TS 9", "1", "1", "", "", "", "0", "", "", "0", ""],
]

# Persis gaya export sheet SAP (lihat SAP_SHEET_HEADER): nama kolomnya
# kotor ('      Ordered' berspasi depan), ada kolom di luar yang dipakai
# ('Un', 'Item', tanggal), dan kolom angkanya teks format Indonesia.
SAP_SHEET_HEADER = [
    "WBS Elem", "WBS Description", "Purch.Req.", "Del. Indicator", "PR Status",
    "Req.Date", "PR Deliv. Dat", "Chngd on", "Kode Material",
    "Mat. Description", "Spesifikasi", "Qty Requested", "Un", "      Ordered",
    "PO Number", "PO Date", " Item",
]

SAP_SHEET_ROWS = [
    # dua baris untuk satu kode -> dijumlah (10 + 5 = 15)
    ["A-1.10", "WP-PR1", "100001", "", "A", "05.10.2026", "10.10.2026",
     "01.10.2026", "1000123", "BAUT M8", "M8x20", "10", "PCS", "0",
     "4500000001", "", "00010"],
    ["A-1.20", "WP-PR1B", "100002", "", "B", "05.10.2026", "10.10.2026",
     "01.10.2026", "1000123", "BAUT M8", "M8x20", "5", "PCS", "5",
     "4500000002", "", "00010"],
    ["A-1.20", "WP-PR3", "100003", "", "N", "05.10.2026", "10.10.2026",
     "01.10.2026", "A28RK12017", "SEAL", "-", "3", "PCS", "0", "", "",
     "00010"],
    ["B-2.20", "WP-PR2", "100004", "", "N", "05.10.2026", "10.10.2026",
     "01.10.2026", "1000999", "PEGAS", "SPRG", "5", "PCS", "0", "", "",
     "00010"],
]


def to_csv(header, rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


BOM_BYTES = to_csv(BOM_HEADER, BOM_ROWS)
SAP_BYTES = to_csv(SAP_SHEET_HEADER, SAP_SHEET_ROWS)


def frame(header, rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=header)


def bom_frame() -> pd.DataFrame:
    return mp3.prepare_data(frame(BOM_HEADER, BOM_ROWS))


def sap_frame() -> pd.DataFrame:
    # Lewati loader (bukan pd.read_csv biasa) supaya unit test memakai
    # frame yang sama persis dengan jalur produksi.
    return mp3.read_sap_csv(SAP_BYTES)


def dataset_subheaders(at: AppTest):
    return [s.value for s in at.subheader
            if s.value.startswith("Dataset Bill Of Materials")]


def captions(at: AppTest) -> str:
    return " | ".join(c.value for c in at.caption)


def match_toggle(at: AppTest):
    matches = [t for t in at.toggle if t.key == "tl_toggle_sap_mismatch"]
    assert matches, f"toggle tidak tampil: {[t.key for t in at.toggle]}"
    return matches[0]


# ============================================================
# UNIT: fungsi murni
# ============================================================

def test_norm_key_dan_tolerance():
    assert mp3.norm_key(" d51qc0048 ") == "D51QC0048"
    assert mp3.norm_key(None) == ""
    assert mp3.norm_key(float("nan")) == ""

    assert mp3.qty_tolerance(0) == 0.01          # absolut, bukan 0
    assert mp3.qty_tolerance(100) == 1.0          # 1% dari kebutuhan
    assert mp3.qty_tolerance(None) == 0.01
    print("[OK] norm_key & qty_tolerance")


def test_qty_format_ribuan():
    """'1.056' di sheet = 1056 (pemisah ribuan gaya Indonesia), bukan 1.056.

    Angka ini nyata di export: kolom SUM tertulis '1.056' sementara
    kolom 'Qty PR VE' di baris yang sama tertulis polos '1056'. Bila
    dibaca sebagai float, total material jadi 8.448 alih-alih 8448 dan
    seluruh 'Cek Qty' salah.
    """
    header = BOM_HEADER
    rows = [
        ["Komponen", "", "9", "X1", "X1", "", "ITEM", "pcs", "A",
         "TS 9", "1.056", "1.056", "PR BARU", "", "", "1056", "", "", "0", ""],
        ["Komponen", "", "9", "X1", "X1", "", "ITEM", "pcs", "A",
         "TS 10", "1.056", "1.056", "PR BARU", "", "", "1056", "", "", "0", ""],
    ]

    # 1) Loader mempertahankan teks aslinya (bila sudah float, informasi
    #    pemisah ribuan hilang selamanya dan tak bisa dipulihkan).
    loaded = mp3.read_bom_csv(to_csv(header, rows))
    assert loaded["SUM"].dtype == object, loaded["SUM"].dtype
    assert loaded["SUM"].tolist() == ["1.056", "1.056"], loaded["SUM"].tolist()

    # 2) prepare_data mengubahnya jadi angka benar.
    data = mp3.prepare_data(loaded)
    assert data["SUM"].tolist() == [1056.0, 1056.0], data["SUM"].tolist()
    assert data["Kebutuhan"].tolist() == [1056.0, 1056.0]

    # 3) Total material & qty PR ikut benar setelah collapse.
    collapsed = mp3.collapse_material(data)
    assert collapsed.iloc[0]["Kebutuhan Total"] == 2112
    assert collapsed.iloc[0]["Qty PR (Sheet)"] == 2112
    print("[OK] format ribuan '1.056' -> 1056 (loader, prepare, collapse)")


def test_derive_status_sap():
    assert mp3.derive_status_sap(None, 100) == "TIDAK ADA DI SAP"
    assert mp3.derive_status_sap(0, 100) == "BELUM PR"
    assert mp3.derive_status_sap(50, 100) == "PR PARSIAL"
    assert mp3.derive_status_sap(100, 100) == "SUDAH PR"
    # masih dalam toleransi 1% -> dihitung SUDAH PR, bukan PARSIAL
    assert mp3.derive_status_sap(99.5, 100) == "SUDAH PR"
    print("[OK] derive_status_sap")


def test_check_qty_match():
    assert mp3.check_qty_match(None, 100) == "TIDAK ADA DI SAP"
    assert mp3.check_qty_match(100.005, 100) == "SESUAI"
    assert mp3.check_qty_match(100.5, 100) == "SESUAI"   # dalam toleransi 1%
    assert mp3.check_qty_match(90, 100) == "SAP KURANG"
    assert mp3.check_qty_match(110, 100) == "SAP LEBIH"
    assert mp3.check_qty_match(0, 0) == "SESUAI"
    print("[OK] check_qty_match")


def test_check_status_match():
    # BOM: sudah ada PR, SAP: ada Qty Requested
    assert mp3.check_status_match("SUDAH PR", 5) == "SESUAI"
    assert mp3.check_status_match("PR PARSIAL", 5) == "SESUAI"
    assert mp3.check_status_match("STOK DAN PR", 5) == "SESUAI"
    # BOM: stok / belum PR tapi SAP punya PR -> TIDAK SESUAI
    assert mp3.check_status_match("STOK", 5) == "TIDAK SESUAI"
    assert mp3.check_status_match("BELUM PR", 5) == "TIDAK SESUAI"
    # keduanya tanpa PR -> SESUAI
    assert mp3.check_status_match("STOK", 0) == "SESUAI"
    assert mp3.check_status_match("BELUM PR", 0) == "SESUAI"
    # material tidak ada di SAP / status tak dikenal
    assert mp3.check_status_match("SUDAH PR", None) == "TIDAK ADA DI SAP"
    assert mp3.check_status_match("STATUS LAIN / TIDAK DIKENAL", 5) == "TIDAK DIKENAL"

    assert mp3.canonical_bom_status("STOK DAN PR") == "ADA PR"
    assert mp3.canonical_bom_status("STOK") == "BELUM PR"
    assert mp3.canonical_bom_status("lainnya") == "TIDAK DIKENAL"
    print("[OK] check_status_match & canonical_bom_status")


def test_build_hasil_match():
    assert mp3.build_hasil_match("SESUAI", "SESUAI") == "SESUAI"
    assert mp3.build_hasil_match("SESUAI", "SAP KURANG") == "QTY TIDAK SESUAI"
    assert mp3.build_hasil_match("TIDAK SESUAI", "SESUAI") == "STATUS TIDAK SESUAI"
    assert (mp3.build_hasil_match("TIDAK SESUAI", "SAP LEBIH")
            == "STATUS & QTY TIDAK SESUAI")
    assert (mp3.build_hasil_match("TIDAK ADA DI SAP", "TIDAK ADA DI SAP")
            == "TIDAK ADA DI SAP")
    # status tak dikenal dihitung belum terbukti konsisten
    assert mp3.build_hasil_match("TIDAK DIKENAL", "SESUAI") == "STATUS TIDAK SESUAI"
    print("[OK] build_hasil_match")


def test_aggregate_sap_for_match():
    agg = mp3.aggregate_sap_for_match(sap_frame())

    # urutan grup ikut sortir groupby — urutkan dulu sebelum dibandingkan
    agg = agg.sort_values("Kode Material (SAP)").reset_index(drop=True)
    assert list(agg["Kode Material (SAP)"]) == ["1000123", "1000999", "A28RK12017"]
    row = agg[agg["Kode Material (SAP)"] == "1000123"].iloc[0]
    assert row["Qty Requested (SAP)"] == 15          # dua baris dijumlah
    assert row["Ordered (SAP)"] == 5
    assert row["Baris SAP"] == 2
    assert row["PR Status (SAP)"] == "A, B"          # unik, urut
    assert row["WBS (SAP)"] == "A-1.10, A-1.20"

    # kolom wajib hilang -> ValueError (pesan dipakai sebagai alasan
    # validasi nonaktif di dashboard)
    try:
        mp3.aggregate_sap_for_match(frame(["Kode Material"], [["1"]]))
    except ValueError as exc:
        assert "Qty Requested" in str(exc), exc
    else:
        raise AssertionError("kolom wajib hilang tidak memicu ValueError")

    # tanpa 'Ordered'/'PR Status' tetap jalan (kolom opsional)
    minimal = frame(["Kode Material", "Qty Requested"], [["1", "3"]])
    agg_minimal = mp3.aggregate_sap_for_match(minimal)
    assert agg_minimal.iloc[0]["Qty Requested (SAP)"] == 3
    assert "Ordered (SAP)" not in agg_minimal.columns
    print("[OK] aggregate_sap_for_match")


def test_match_with_sap():
    matched = mp3.match_with_sap(bom_frame(), sap_frame())

    # grain collapse: 4 material (baris tanpa 'Tindak Lanjut' gugur)
    assert len(matched) == 4, len(matched)
    assert "Status SAP" in matched.columns
    assert "Hasil Match" in matched.columns

    by_kode = matched.set_index("Kode Material")

    # 1) kebutuhan 20 vs SAP 15 -> status cocok, qty kurang
    baut = by_kode.loc["1000123"]
    assert baut["Kebutuhan Total"] == 20
    assert baut["Qty Requested (SAP)"] == 15
    assert baut["Selisih Qty"] == -5
    assert baut["Cek Status"] == "SESUAI"
    assert baut["Cek Qty"] == "SAP KURANG"
    assert baut["Hasil Match"] == "QTY TIDAK SESUAI"
    assert not baut["Cocok via Kode Konversi"]

    # 2) material tanpa pasangan di SAP harus TETAP tampil (left join)
    panel = by_kode.loc["777001"]
    assert panel["Status SAP"] == "TIDAK ADA DI SAP"
    assert panel["Hasil Match"] == "TIDAK ADA DI SAP"
    assert pd.isna(panel["Qty Requested (SAP)"])

    # 3) kunci cadangan 'Kode Material Konversi'
    seal = by_kode.loc["B52TH1222"]
    assert seal["Hasil Match"] == "SESUAI"
    assert seal["Cocok via Kode Konversi"]
    assert seal["Kode Material Konversi"] == "A28RK12017"

    # 4) BOM 'BELUM PR' tapi SAP punya qty -> dua-duanya meleset
    pegas = by_kode.loc["1000999"]
    assert pegas["Status PR"] == "BELUM PR"
    assert pegas["Cek Status"] == "TIDAK SESUAI"
    assert pegas["Cek Qty"] == "SAP LEBIH"
    assert pegas["Hasil Match"] == "STATUS & QTY TIDAK SESUAI"

    # tidak ada kolom bantu yang bocor ke tampilan
    assert not [c for c in matched.columns if c.startswith("_")]
    print("[OK] match_with_sap (cocok status, cocok qty, kunci konversi)")


def test_attach_match_broadcast():
    matched = mp3.match_with_sap(bom_frame(), sap_frame())
    rows = bom_frame()
    rows = rows[rows["Trainset"] == "TS 9"]

    attached = mp3.attach_match(rows, matched)

    # jumlah baris tidak berubah (kunci collapse unik -> tanpa fan-out)
    assert len(attached) == len(rows), (len(attached), len(rows))
    for col in mp3.MATCH_RESULT_COLUMNS:
        assert col in attached.columns, col
    # kolom asli tidak tertimpa
    assert "Tindak Lanjut" in attached.columns

    # broadcast: baris TS 9 material 1000123 memakai hasil match materialnya
    row = attached[attached["Kode Material"] == "1000123"].iloc[0]
    assert row["Hasil Match"] == "QTY TIDAK SESUAI"
    assert row["Selisih Qty"] == -5
    # selisihnya = selisih MATERIAL (bukan baris ini)
    assert row["Kebutuhan Total"] == 20
    print("[OK] attach_match (broadcast tanpa mengubah jumlah baris)")


def test_match_summary_dan_crosstab():
    matched = mp3.match_with_sap(bom_frame(), sap_frame())

    summary = mp3.match_summary(matched)
    assert list(summary.index) == mp3.MATCH_ORDER        # urut & tak ada hilang
    assert summary["SESUAI"] == 1
    assert summary["QTY TIDAK SESUAI"] == 1
    assert summary["STATUS & QTY TIDAK SESUAI"] == 1
    assert summary["TIDAK ADA DI SAP"] == 1
    assert summary["STATUS TIDAK SESUAI"] == 0
    assert int(summary.sum()) == len(matched)            # tidak ada baris jatuh

    table = mp3.match_crosstab(matched)
    assert list(table.index) == mp3.STATUS_PR_ORDER
    assert list(table.columns) == mp3.SAP_STATUS_ORDER
    assert int(table.values.sum()) == len(matched)

    # frame kosong tetap menghasilkan tabel berdimensi penuh (isi 0)
    empty_table = mp3.match_crosstab(matched.iloc[0:0])
    assert empty_table.shape == (len(mp3.STATUS_PR_ORDER), len(mp3.SAP_STATUS_ORDER))
    print("[OK] match_summary & match_crosstab")


def test_cek_counts():
    matched = mp3.match_with_sap(bom_frame(), sap_frame())

    status = mp3.cek_counts(matched, "Cek Status", mp3.CEK_STATUS_ORDER)
    assert list(status.index) == mp3.CEK_STATUS_ORDER
    assert status["SESUAI"] == 2              # 1000123 & B52TH1222
    assert status["TIDAK SESUAI"] == 1        # 1000999
    assert status["TIDAK ADA DI SAP"] == 1    # 777001
    assert int(status.sum()) == len(matched)  # tak ada baris jatuh

    qty = mp3.cek_counts(matched, "Cek Qty", mp3.CEK_QTY_ORDER)
    assert list(qty.index) == mp3.CEK_QTY_ORDER
    assert qty["SESUAI"] == 1
    assert qty["SAP KURANG"] == 1
    assert qty["SAP LEBIH"] == 1
    assert qty["TIDAK ADA DI SAP"] == 1

    # frame tanpa kolomnya / kosong -> nol, bukan KeyError
    kosong = mp3.cek_counts(matched.iloc[0:0], "Cek Qty", mp3.CEK_QTY_ORDER)
    assert int(kosong.sum()) == 0
    print("[OK] cek_counts (Cek Status & Cek Qty lengkap, tanpa baris jatuh)")


def test_read_sap_csv_header_kotor():
    """Sheet SAP: nama kolom kotor & kolom sisa — hanya yang dipakai dibaca.

    Header aslinya memuat '      Ordered' (spasi depan), kolom duplikat
    ' Item', dan kolom yang tidak dipakai ('Un', tanggal). Bila pemetaan
    nama kolomnya literal, kolom 'Ordered' hilang dan 'Cek Qty' kehilangan
    pembanding terurut.
    """
    loaded = mp3.read_sap_csv(SAP_BYTES)

    assert "Ordered" in loaded.columns          # spasi depan sudah dirapikan
    assert "Un" not in loaded.columns            # di luar SAP_USE_COLUMNS
    assert "Item" not in loaded.columns
    assert "Req.Date" not in loaded.columns

    # qty dibaca sebagai TEKS lalu diparse angka (bukan mengambang)
    assert loaded["Qty Requested"].tolist() == [10, 5, 3, 5]
    assert loaded["Ordered"].tolist() == [0, 5, 0, 0]
    assert "WBS_Group" in loaded.columns        # ekstra dari mp2.clean_sap
    print("[OK] read_sap_csv (header kotor, kolom dibatasi, qty numerik)")


def test_read_sap_csv_format_ribuan_dan_duplikat():
    """'56.544' = 56544, '8.200,00' = 8200; baris identik dibuang.

    Sheet SAP nyata mencampur kedua format dalam kolom yang sama. Bila
    dibaca sebagai float mentah, '8.200,00' jadi 8.2 dan seluruh
    'Cek Qty' material berharga besar salah arah.
    """
    rows = [
        ["A-1.10", "WP", "1", "", "A", "", "", "", "Q1", "X", "", "56.544",
         "PCS", "8.200,00", "", "", ""],
        ["A-1.10", "WP", "1", "", "A", "", "", "", "Q1", "X", "", "56.544",
         "PCS", "8.200,00", "", "", ""],   # duplikat persis
    ]
    loaded = mp3.read_sap_csv(to_csv(SAP_SHEET_HEADER, rows))

    assert len(loaded) == 1, loaded
    assert loaded.iloc[0]["Qty Requested"] == 56544
    assert loaded.iloc[0]["Ordered"] == 8200
    print("[OK] read_sap_csv (format ribuan/desimal Indonesia, duplikat dibuang)")


def test_read_sap_csv_kolom_wajib_hilang():
    """Tanpa 'Qty Requested'/'WBS Elem' -> ValueError (jadi alasan nonaktif)."""
    for header in (["Kode Material", "WBS Elem"], ["WBS Elem", "Qty Requested"]):
        try:
            mp3.read_sap_csv(to_csv(header, [["1", "A-1.10"]]))
        except ValueError as exc:
            assert "tidak memiliki kolom wajib" in str(exc), exc
        else:
            raise AssertionError(f"header {header} tidak memicu ValueError")
    print("[OK] read_sap_csv menolak sheet tanpa kolom wajib")


def test_scope_sap():
    sap = sap_frame()

    assert len(mp3.scope_sap(sap, "Semua")) == len(sap)
    scoped = mp3.scope_sap(sap, "A-1.10")
    assert len(scoped) == 1
    assert scoped.iloc[0]["Kode Material"] == "1000123"

    assert mp3.scope_sap(None, "A-1.10") is None
    # tanpa kolom WBS sekalipun tidak boleh meledak (KeyError)
    plain = frame(["Kode Material"], [["1"]])
    assert mp3.scope_sap(plain, "A-1.10") is plain
    print("[OK] scope_sap (per WBS, 'Semua', tanpa kolom WBS)")


# ============================================================
# APPTEST: halaman penuh (kedua mode hitung)
# ============================================================

def app_test() -> AppTest:
    return AppTest.from_file(str(MP3_APP), default_timeout=60)


def test_dashboard_dengan_validasi_sap():
    at = app_test()
    at.session_state["_fake_csv"] = BOM_BYTES
    at.session_state["_fake_sap_csv"] = SAP_BYTES
    at.run()

    assert not at.exception, at.exception

    subheaders = [s.value for s in at.subheader]
    assert "Validasi terhadap SAP" in subheaders, subheaders
    # mode default = "Semua" (fixture cuma TS 9 & TS 10) -> frame = hasil match
    assert "Dataset Bill Of Materials(4)" in dataset_subheaders(at), \
        dataset_subheaders(at)

    caps = captions(at)
    assert "Material divalidasi: 4" in caps, caps
    assert "sesuai: 1" in caps, caps

    # toggle "hanya yang tidak cocok" -> sisa 3 material (1 yang sesuai hilang)
    match_toggle(at).set_value(True).run()
    assert not at.exception, at.exception
    assert dataset_subheaders(at) == ["Dataset Bill Of Materials(3)"], \
        dataset_subheaders(at)

    # kolom hasil match ikut tampil di tabel
    frame_tables = [f.value for f in at.dataframe]
    assert any("Hasil Match" in t.columns for t in frame_tables), \
        [list(t.columns) for t in frame_tables]

    print("[OK] mode Semua: panel validasi, KPI, dan filter tidak-sesuai")


def test_dashboard_mode_trainset():
    at = app_test()
    at.session_state["_fake_csv"] = BOM_BYTES
    at.session_state["_fake_sap_csv"] = SAP_BYTES
    at.run()
    assert not at.exception, at.exception

    # Pilih trainset -> mode baris; hasil match di-broadcast, tidak dihitung ulang
    at.selectbox[0].select("TS 9").run()
    assert not at.exception, at.exception

    caps = captions(at)
    assert "adalah hasil agregat material lintas trainset" in caps, caps

    # 4 baris TS 9 (baris kelima tanpa 'Tindak Lanjut' sudah gugur)
    assert dataset_subheaders(at) == ["Dataset Bill Of Materials(4)"], \
        dataset_subheaders(at)

    tables = [f.value for f in at.dataframe]
    assert any("Hasil Match" in t.columns for t in tables), \
        [list(t.columns) for t in tables]

    print("[OK] mode trainset: broadcast hasil match ke baris")


def test_dashboard_tanpa_sap_tetap_jalan():
    """SAP tidak tersedia -> dashboard jalan seperti biasa, tanpa panel."""
    at = app_test()
    at.session_state["_fake_csv"] = BOM_BYTES   # tanpa _fake_sap_csv
    at.run()

    assert not at.exception, at.exception
    subheaders = [s.value for s in at.subheader]
    assert "Validasi terhadap SAP" not in subheaders, subheaders
    assert "sumber SAP tiruan belum disiapkan" in captions(at), captions(at)
    # mode Semua tetap menampilkan collapse material
    assert "Dataset Bill Of Materials(4)" in dataset_subheaders(at), \
        dataset_subheaders(at)

    print("[OK] tanpa data SAP: tanpa panel validasi, dashboard tetap jalan")


def test_validasi_bisa_dimatikan():
    at = app_test()
    at.session_state["_fake_csv"] = BOM_BYTES
    at.session_state["_fake_sap_csv"] = SAP_BYTES
    at.run()
    assert not at.exception, at.exception

    toggle = [t for t in at.toggle if t.key == "mp3_validasi_sap"]
    assert toggle, [t.key for t in at.toggle]
    toggle[0].set_value(False).run()

    assert not at.exception, at.exception
    subheaders = [s.value for s in at.subheader]
    assert "Validasi terhadap SAP" not in subheaders, subheaders
    assert dataset_subheaders(at) == ["Dataset Bill Of Materials(4)"], \
        dataset_subheaders(at)

    print("[OK] toggle 'Validasi terhadap SAP' bisa mematikan panel")


def test_dashboard_filter_wbs():
    """Filter 'Proyek (WBS)' menyempitkan basis pencocokan SAP.

    Sheet SAP berisi banyak proyek; tanpa saringan, qty lintas proyek
    tercampur. Memilih satu WBS membuat material di luar proyek itu
    berubah jadi TIDAK ADA DI SAP — jadi filter ini benar-benar
    mengubah isi validasi, bukan sekadar kosmetik.
    """
    at = app_test()
    at.session_state["_fake_csv"] = BOM_BYTES
    at.session_state["_fake_sap_csv"] = SAP_BYTES
    at.run()
    assert not at.exception, at.exception

    wbs = [s for s in at.selectbox if s.label == "Proyek (WBS)"]
    assert wbs, [s.label for s in at.selectbox]

    # defaultnya "Semua" -> semua baris SAP dipakai (4 baris, sesuai fixture)
    assert "4 baris sheet SAP (seluruh WBS)" in captions(at), captions(at)

    wbs[0].select("B-2.20").run()
    assert not at.exception, at.exception

    caps = captions(at)
    assert "4 baris sheet SAP → disaring ke WBS B-2.20 (1 baris)" in caps, caps
    assert "Material divalidasi: 4" in caps, caps
    # cuma 1000999 yang ada di WBS ini -> 3 material lain TIDAK ADA DI SAP,
    # sehingga tidak ada yang lagi SESUAI (fixture: sesuai = kode A28RK12017)
    assert "sesuai: 0" in caps, caps

    print("[OK] filter WBS: basis pencocokan SAP ikut tersaring")


if __name__ == "__main__":
    test_norm_key_dan_tolerance()
    test_qty_format_ribuan()
    test_derive_status_sap()
    test_check_qty_match()
    test_check_status_match()
    test_build_hasil_match()
    test_aggregate_sap_for_match()
    test_match_with_sap()
    test_attach_match_broadcast()
    test_match_summary_dan_crosstab()
    test_cek_counts()
    test_read_sap_csv_header_kotor()
    test_read_sap_csv_format_ribuan_dan_duplikat()
    test_read_sap_csv_kolom_wajib_hilang()
    test_scope_sap()
    test_dashboard_dengan_validasi_sap()
    test_dashboard_mode_trainset()
    test_dashboard_tanpa_sap_tetap_jalan()
    test_validasi_bisa_dimatikan()
    test_dashboard_filter_wbs()
    print("\nSEMUA MATCH TEST LULUS")
