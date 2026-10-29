"""Statistic Dashboard 2 — progres tindak lanjut material per Trainset.

Halaman analytics yang MENDEPAN sumber data berbeda dari Statistic
Dashboard (material_planning_2): tidak lewat pipeline ETL/upload file,
melainkan membaca langsung export CSV dari Google Sheets. Logikanya
sederhana — filter baris yang sudah punya nilai 'Tindak Lanjut',
seragamkan label yang mirip, lalu tampilkan sebaran status tindak
lanjut per 'Sheet Asal' dalam bentuk jumlah item dan persentase.

Dua mode hitung:
- Trainset spesifik: hitung per baris (satu baris = satu material di
  satu trainset), sama seperti olahan per-TS.
- "Semua": material di-collapse dulu LINTAS trainset (kunci
  NO + Kode Material + Sheet Asal), seluruh nilai Tindak Lanjut-nya
  disatukan jadi teks koma, lalu diklasifikasi check_pr_status()
  menjadi 'Status PR' (PR PARSIAL, STOK DAN PR, SUDAH PR, BELUM PR,
  STOK, STATUS LAIN). Satu material hanya dihitung sekali.

Validasi terhadap SAP (opsional, lihat VALIDASI SAP):
- Sumbernya export CSV PUBLIK satu spreadsheet SAP (DEFAULT_SAP_URL,
  gid 826586568) — tanpa upload file Excel, tanpa autentikasi, dan
  tanpa ikut pipeline ETL dashboard pertama. Filenya besar (±27 MB,
  ±108 ribu baris) jadi di-cache 1 jam (SAP_CACHE_TTL).
- Sheet itu memuat banyak proyek (53 kode WBS), sementara data BOM ini
  satu proyek — karena itu ada filter 'Proyek (WBS)' di baris filter
  (default "Semua": seluruh WBS ikut dijumlah).
- Karena Qty SAP adalah total lintas trainset, perhitungan match
  SELALU di grain material (hasil collapse_material). Di mode
  trainset hasil match di-broadcast balik ke tiap baris lewat
  attach_match() — bukan dihitung ulang per baris.
- Dua sumbu pembuktian terpisah: 'Cek Status' (ada-tidaknya PR di
  BOM vs ada-tidaknya Qty Requested di SAP) dan 'Cek Qty' (total
  kebutuhan material vs Qty Requested, toleransi 1%/0.01).
"""
import io

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

import material_planning_2 as mp2

# ============================================================
# CONFIG
# ============================================================

# Sheet "Tindak Lanjut BOM" — export CSV publik (tanpa autentikasi).
DEFAULT_BOM_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1axKrvf147x0eQ9hsuHZA4Ur943bzQFttJ6W0oQuK7RQ/"
    "export?format=csv&gid=176301186"
)

# Sheet "SAP" (menu Statistic Dashboard New Algorithms) — export CSV publik,
# TANPA upload file Excel dan tanpa autentikasi. Isinya ±108 ribu baris /
# 12 ribu kode material / 53 WBS dari banyak proyek, jadi hanya kolom yang
# dipakai yang dibaca (SAP_USE_COLUMNS) dan hasilnya di-cache 1 jam.
SAP_SHEET_ID = "1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q"
SAP_SHEET_GID = 826586568
DEFAULT_SAP_URL = (
    f"https://docs.google.com/spreadsheets/d/{SAP_SHEET_ID}"
    f"/export?format=csv&gid={SAP_SHEET_GID}"
)

# Kolom yang dipakai dari sheet SAP. Nama kolom aslinya kotor (mis.
# '      Ordered' berspasi depan, header ' Item' terduplikasi), jadi
# pemetaannya lewat mp2.normalize_columns — lihat read_sap_csv().
SAP_USE_COLUMNS = (
    "WBS Elem",
    "WBS Description",
    "Kode Material",
    "Mat. Description",
    "Spesifikasi",
    "PR Status",
    "Qty Requested",
    "Ordered",
    "Purch.Req.",
    "PO Number",
)
# Dua ini minimal; tanpanya validasi tidak bisa jalan (read_sap_csv
# melempar ValueError, pesannya jadi alasan validasi nonaktif).
SAP_REQUIRED_COLUMNS = ("Kode Material", "Qty Requested")
# Kolom angka yang dibaca sebagai teks lalu diparse mp2.parse_amount
# (format Indonesia di sheet ini campur: '56.544' dan '8.200,00').
SAP_QTY_COLUMNS = ("Qty Requested", "Ordered")

# Urutan status di chart & tabel (reindex: status kosong -> 0).
STATUS_ORDER = [
    "PR EKSISTING",
    "PR BARU",
    "BELUM PR",
    "MENUNGGU KOMAT",
    "MENGGUNAKAN STOK",
    "MENUNGGU EVALUASI PEMAKAIAN",
]

# Label yang ejaannya berbeda di sheet tapi maksudnya sama -> diseragamkan
# supaya tidak terpecah jadi dua status saat digroupby.
STATUS_RENAME = {
    "MENGGUNAKAN STOK GUDANG": "MENGGUNAKAN STOK",
}

# Warna tetap per status supaya konsisten antar chart & rerun.
STATUS_COLORS = {
    "PR EKSISTING": "#636EFA",
    "PR BARU": "#00CC96",
    "BELUM PR": "#EF553B",
    "MENUNGGU KOMAT": "#FFA15A",
    "MENGGUNAKAN STOK": "#AB63FA",
    "MENUNGGU EVALUASI PEMAKAIAN": "#FF9896",
}

# Kategori hasil check_pr_status (mode "Semua"). Urutan = urutan tampil
# di chart/tabel; STATUS LAIN tetap tampil supaya tidak ada data hilang.
STATUS_PR_ORDER = [
    "BELUM PR",
    "PR PARSIAL",
    "SUDAH PR",
    "STOK DAN PR",
    "STOK",
    "STATUS LAIN / TIDAK DIKENAL",
]

STATUS_PR_COLORS = {
    "BELUM PR": "#EF553B",
    "PR PARSIAL": "#FFA15A",
    "SUDAH PR": "#00CC96",
    "STOK DAN PR": "#636EFA",
    "STOK": "#AB63FA",
    "STATUS LAIN / TIDAK DIKENAL": "#7F7F7F",
}

REQUEST_TIMEOUT = 30

# Data Sheets bisa berubah sewaktu-waktu; cache singkat + tombol muat
# ulang di sidebar supaya perubahan terbaru bisa ditarik paksa.
DATA_CACHE_TTL = 300

# Sheet SAP jauh lebih besar (±27 MB CSV) — cache 1 jam supaya tidak
# diunduh tiap rerun; tombol 'Load Data' tetap bisa memaksa tarik ulang.
SAP_CACHE_TTL = 3600
SAP_REQUEST_TIMEOUT = 120

# ------------------------------------------------------------
# VALIDASI SAP
# ------------------------------------------------------------

# Toleransi kecocokan quantity: selisih dianggap SESUAI bila berada
# dalam ±1% dari kebutuhan ATAU ±0.01 satuan (pembulatan desimal sheet),
# mana yang lebih longgar. Angka kecil (qty 0.something) tetap pakai
# ambang absolut supaya tidak dianggap selisih oleh pembulatan.
QTY_TOLERANCE_REL = 0.01
QTY_TOLERANCE_ABS = 0.01

# Kolom 'SUM' di sheet = kebutuhan satu baris trainset (pada sheet
# Komponen isinya sama dengan 'Kebutuhan', pada sheet Consumable hanya
# 'SUM' yang terisi), jadi total material = JUMLAH seluruh barisnya.
QTY_BOM_COLUMN = "SUM"
QTY_BOM_FALLBACK = "Kebutuhan"

# Kolom qty di export sheet memakai pemisah ribuan gaya Indonesia
# ('1.056' = 1056, bukan 1.056 — terbukti dari kolom 'Qty PR VE' di baris
# yang sama yang tertulis polos '1056'). Kolom-kolom ini WAJIB dibaca
# sebagai TEKS saat load, karena begitu pandas meng-infer-nya jadi float
# informasi "titiknya pemisah ribuan" hilang selamanya. Pemrosesannya ada
# di prepare_data() lewat mp2.parse_amount_series.
QTY_RAW_COLUMNS = ("Kebutuhan", "SUM", "Qty PR VE", "Qty PR MS")

# Kode material: 'Kode Material' dulu; 'Kode Material Konversi' dipakai
# sebagai kunci cadangan (3.4% baris beda kode — mis. B52TH1222 ->
# A28RK12017) supaya material tetap bisa dicocokkan ke SAP.
KEY_MATERIAL = "Kode Material"
KEY_MATERIAL_ALT = "Kode Material Konversi"

# Kunci collapse (grain material) juga dipakai sebagai kunci broadcast
# hasil match ke baris trainset.
MATCH_KEYS = ["NO", KEY_MATERIAL, "Sheet Asal"]

# Urutan 'Status SAP' (turunan dari Qty Requested SAP) — dipakai sebagai
# kolom matriks bukti (match_crosstab).
SAP_STATUS_ORDER = ["SUDAH PR", "PR PARSIAL", "BELUM PR", "TIDAK ADA DI SAP"]

# Urutan & warna hasil 'Hasil Match' (ringkasan dua sumbu pembuktian).
MATCH_ORDER = [
    "SESUAI",
    "QTY TIDAK SESUAI",
    "STATUS TIDAK SESUAI",
    "STATUS & QTY TIDAK SESUAI",
    "TIDAK ADA DI SAP",
]
MATCH_COLORS = {
    "SESUAI": "#00CC96",
    "QTY TIDAK SESUAI": "#FFA15A",
    "STATUS TIDAK SESUAI": "#636EFA",
    "STATUS & QTY TIDAK SESUAI": "#EF553B",
    "TIDAK ADA DI SAP": "#7F7F7F",
}

# Label pendek untuk kartu KPI (layar sempit bila pakai label penuh).
MATCH_SHORT_LABEL = {
    "SESUAI": "Sesuai",
    "QTY TIDAK SESUAI": "Qty salah",
    "STATUS TIDAK SESUAI": "Status salah",
    "STATUS & QTY TIDAK SESUAI": "Keduanya salah",
    "TIDAK ADA DI SAP": "Tidak ada di SAP",
}

CEK_STATUS_ORDER = [
    "SESUAI",
    "TIDAK SESUAI",
    "TIDAK DIKENAL",
    "TIDAK ADA DI SAP",
]
CEK_QTY_ORDER = ["SESUAI", "SAP KURANG", "SAP LEBIH", "TIDAK ADA DI SAP"]


# ============================================================
# DATA
# ============================================================

def read_bom_csv(content: bytes) -> pd.DataFrame:
    """Baca export CSV BOM; kolom qty sengaja dibaca sebagai TEKS.

    Header dibaca dulu (nrows=0) hanya untuk tahu kolom mana yang memang
    ada — dtype untuk kolom yang tidak ada akan membuat read_csv gagal.
    Alasan kolom qty dipertahankan sebagai teks: lihat QTY_RAW_COLUMNS.
    """
    buf = io.BytesIO(content)
    header = pd.read_csv(buf, nrows=0).columns
    buf.seek(0)
    dtype = {col: str for col in QTY_RAW_COLUMNS if col in header}
    return pd.read_csv(buf, dtype=dtype or None, low_memory=False)


@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner="Membaca data BOM dari Google Sheets...")
def load_bom_data(url: str) -> pd.DataFrame:
    """Ambil CSV publik Google Sheets -> DataFrame apa adanya."""
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return read_bom_csv(response.content)


def read_sap_csv(content: bytes) -> pd.DataFrame:
    """Baca export CSV sheet SAP (lihat DEFAULT_SAP_URL) -> frame bersih.

    Hanya kolom SAP_USE_COLUMNS yang diambil dari file ±27 MB, lalu
    pembersihannya diserahkan ke mp2.clean_sap() — semantiknya persis
    seperti tab SAP_CLEANED di dashboard pertama (buang duplikat, kode
    material dirapikan, qty format Indonesia diparse, WBS_Group diekstrak).

    Dua hal yang harus hati-hati:
    - Nama kolom aslinya kotor ('      Ordered' berspasi depan, header
      ' Item' terduplikasi), jadi pemetaannya dilakukan dengan mencocokkan
      nama yang sudah dirapikan, bukan nama literal.
    - Kolom qty (SAP_QTY_COLUMNS) dibaca sebagai TEKS dulu supaya
      mp2.parse_amount bisa membedakan '56.544' (= 56544) dan
      '8.200,00' (= 8200) — bila sudah jadi float, makanya hilang.

    Melempar ValueError bila kolom wajib absen; pemanggil mengubahnya
    jadi alasan "validasi nonaktif" di dashboard.
    """
    buf = io.BytesIO(content)
    header = list(pd.read_csv(buf, nrows=0).columns)

    def _norm(name) -> str:
        return str(name).replace("\xa0", " ").strip()

    usecols = [col for col in header if _norm(col) in SAP_USE_COLUMNS]
    if not usecols:
        raise ValueError("sheet SAP tidak memuat kolom yang dikenali")

    dtype = {col: str for col in usecols if _norm(col) in SAP_QTY_COLUMNS}
    buf.seek(0)
    raw = pd.read_csv(
        buf,
        usecols=usecols,
        dtype=dtype or None,
        low_memory=False,
    )

    cleaned, _stats = mp2.clean_sap(raw)
    return cleaned


@st.cache_data(
    ttl=SAP_CACHE_TTL,
    show_spinner="Membaca data SAP dari sheet publik...",
)
def load_sap_data():
    """Unduh export CSV sheet SAP (DEFAULT_SAP_URL).

    Mengembalikan tuple (DataFrame | None, pesan galat | None). Galat ikut
    di-cache (sebagai nilai, bukan exception) supaya sumber yang sedang
    bermasalah tidak membuat unduhan 27 MB diulang tiap rerun.
    """
    try:
        response = requests.get(DEFAULT_SAP_URL, timeout=SAP_REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        return None, f"gagal mengunduh sheet SAP: {exc}"

    # Bila sheet tidak di-share publik, Google membalas halaman HTML
    # (bukan CSV) — lebih jelas pesannya daripada galat parser CSV.
    isi = response.content.lstrip()[:1].lower()
    if isi == b"<":
        return None, "sheet SAP tidak di-share publik (balasannya HTML, bukan CSV)"

    try:
        return read_sap_csv(response.content), None
    except Exception as exc:
        return None, str(exc)


def prepare_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Bersihkan data mentah untuk keperluan dashboard.

    - Buang baris tanpa 'Tindak Lanjut' (belum ada keputusan tindak
      lanjut, jadi tidak ikut perhitungan status). Nilai kosong/putih
      dianggap kosong juga — "" bukan NaN di mata pandas, padahal tetap
      berarti "belum ada keputusan".
    - Seragamkan label status yang bervariasi (STATUS_RENAME).
    - Seragamkan kolom quantity ke numerik lewat mp2.parse_amount_series:
      '1.056' (pemisah ribuan gaya Indonesia) jadi 1056, '0,5' jadi 0.5,
      kolom yang sudah angka diteruskan apa adanya.
    """
    tindak = raw["Tindak Lanjut"]
    punya_tindak = tindak.notna() & tindak.astype(str).str.strip().ne("")

    df = raw[punya_tindak].copy()
    df["Tindak Lanjut"] = df["Tindak Lanjut"].replace(STATUS_RENAME)

    for col in QTY_RAW_COLUMNS:
        if col in df.columns:
            df[col] = mp2.parse_amount_series(df[col])

    return df


def count_by_status(
    df: pd.DataFrame,
    status_col: str = "Tindak Lanjut",
    order=STATUS_ORDER,
) -> pd.DataFrame:
    """Jumlah baris per `status_col` x 'Sheet Asal' (reindex urut `order`).

    `status_col` = 'Tindak Lanjut' (mode trainset spesifik) atau
    'Status PR' (mode Semua, lihat collapse_material).
    """
    counts = (
        df.groupby([status_col, "Sheet Asal"])
        .size()
        .unstack(fill_value=0)
    )
    return counts.reindex(order, fill_value=0)


def percentage_by_status(counts: pd.DataFrame) -> pd.DataFrame:
    if counts.empty or counts.columns.empty:
        return counts.astype(float)

    total_items = counts.sum(axis=0)
    return counts.div(total_items, axis=1).mul(100).round(2)



# ============================================================
# MODE "SEMUA": COLLAPSE PER MATERIAL + KLASIFIKASI STATUS PR
# ============================================================

def check_pr_status(text) -> str:
    if pd.isna(text) or not str(text).strip():
        return "BELUM PR"

    arr_status = {
        status.strip().upper()
        for status in str(text).split(",")
    }

    # 1. PR PARSIAL
    # Jika terdapat PR BARU dan BELUM PR
    # atau PR BARU bersama MENUNGGU EVALUASI PEMAKAIAN
    if (
        "PR BARU" in arr_status
        and (
            "BELUM PR" in arr_status
            or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
        )
    ):
        return "PR PARSIAL"

    # 2. STOK DAN PR
    # PR BARU atau PR EKSISTING bersama MENGGUNAKAN STOK,
    # selama tidak memenuhi kondisi PR PARSIAL
    if (
        "MENGGUNAKAN STOK" in arr_status
        and (
            "PR BARU" in arr_status
            or "PR EKSISTING" in arr_status
        )
    ):
        return "STOK DAN PR"

    # 3. STOK
    if arr_status == {"MENGGUNAKAN STOK"}:
        return "STOK"

    # 4. SUDAH PR
    if (
        "PR BARU" in arr_status
        or "PR EKSISTING" in arr_status
    ):
        return "SUDAH PR"

    # 5. BELUM PR
    if (
        "BELUM PR" in arr_status
        or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
    ):
        return "BELUM PR"

    return "STATUS LAIN / TIDAK DIKENAL"


def collapse_material(data: pd.DataFrame) -> pd.DataFrame:
    """Satukan seluruh baris per material LINTAS trainset jadi satu baris.

    Kunci: NO + Kode Material + Sheet Asal. NO di-cast ke str dulu
    (persis olahan asli) supaya baris tanpa NO tidak terbuang oleh
    groupby default. Semua nilai 'Tindak Lanjut' unik disatukan jadi teks
    koma (urut kata kedua, deterministic), lalu diklasifikasi menjadi
    kolom 'Status PR' oleh check_pr_status().

    Kenapa bukan kunci 'Kode Material' saja: 60 kode material punya >1
    baris NO (784 grup vs 446 kode unik) — memakai NO menjaga baris
    sheet yang memang terpisah tetap terpisah.
    """
    df = data.copy()
    df["NO"] = df["NO"].astype(str).str.strip()

    grouped = df.groupby(["NO", "Kode Material", "Sheet Asal"], dropna=False)

    joined = grouped["Tindak Lanjut"].apply(
        lambda s: ", ".join(
            sorted(
                s.unique(),
                key=lambda t: str(t.split()[1]) if len(t.split()) > 1 else str(t),
            )
        )
    ).reset_index()

    desc = grouped["Deskripsi Material"].first().reset_index()
    joined = pd.merge(joined, desc, on=MATCH_KEYS, how="left")

    # Total kebutuhan material LINTAS trainset: kolom 'SUM' dijumlah per
    # grup (nilainya per baris trainset — lihat QTY_BOM_COLUMN). Bila sheet
    # sumber tidak memuat 'SUM', fallback ke 'Kebutuhan'. Inilah angka yang
    # dipakai 'Cek Qty' terhadap SAP.
    qty_col = (
        QTY_BOM_COLUMN
        if QTY_BOM_COLUMN in df.columns
        else (QTY_BOM_FALLBACK if QTY_BOM_FALLBACK in df.columns else None)
    )
    if qty_col:
        qty = grouped[qty_col].sum().reset_index(name="Kebutuhan Total")
        joined = pd.merge(joined, qty, on=MATCH_KEYS, how="left")

    # Qty PR yang tercatat di sheet (VE + MS) — hanya informatif di tabel,
    # pembanding resminya tetap 'Qty Requested' SAP.
    pr_cols = [c for c in ("Qty PR VE", "Qty PR MS") if c in df.columns]
    if pr_cols:
        pr = grouped[pr_cols].sum().reset_index()
        pr["Qty PR (Sheet)"] = pr[pr_cols].sum(axis=1)
        joined = pd.merge(
            joined, pr[MATCH_KEYS + ["Qty PR (Sheet)"]], on=MATCH_KEYS, how="left"
        )

    # Kunci cadangan saat match ke SAP: sebagian baris memuat kode hasil
    # konversi yang berbeda dari 'Kode Material' (mis. B52TH1222 ->
    # A28RK12017), kode inilah yang kadang tercatat di SAP.
    if KEY_MATERIAL_ALT in df.columns:
        alt = grouped[KEY_MATERIAL_ALT].first().reset_index()
        joined = pd.merge(joined, alt, on=MATCH_KEYS, how="left")

    # Daftar trainset yang menyumbang material ini (urut nomor TS,
    # mis. "TS 9, TS 10, ..., TS 16") — informatif di tabel detail.
    joined["Trainset"] = grouped["Trainset"].apply(
        lambda s: ", ".join(
            sorted(
                s.dropna().unique(),
                key=lambda t: int(t.split()[1]) if t.split()[1].isdigit() else 0,
            )
        )
    ).values

    joined["Status PR"] = joined["Tindak Lanjut"].apply(check_pr_status)

    return joined


# ============================================================
# VALIDASI SAP (match status PR + quantity)
# ============================================================

def norm_key(value) -> str:
    """Seragamkan kode material untuk pencocokan (trim + huruf besar)."""
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    return "" if text == "NAN" else text


def qty_tolerance(kebutuhan) -> float:
    """Ambang selisih qty yang masih dianggap SESUAI.

    Relatif 1% dari kebutuhan ATAU absolut 0.01 satuan — ambil yang lebih
    longgar, supaya pembulatan desimal sheet tidak menghasilkan selisih
    palsu (kebutuhan 0 -> tetap pakai ambang absolut).
    """
    if kebutuhan is None or pd.isna(kebutuhan):
        return QTY_TOLERANCE_ABS
    return max(QTY_TOLERANCE_ABS, abs(float(kebutuhan)) * QTY_TOLERANCE_REL)


def derive_status_sap(qty_requested, kebutuhan) -> str:
    """Status PR versi SAP, diturunkan dari 'Qty Requested'-nya.

    - NaN            -> TIDAK ADA DI SAP (material tidak muncul di export)
    - <= 0           -> BELUM PR (tidak ada baris PR)
    - < kebutuhan    -> PR PARSIAL (ada PR tapi kurang dari kebutuhan)
    - >= kebutuhan   -> SUDAH PR
    """
    if qty_requested is None or pd.isna(qty_requested):
        return "TIDAK ADA DI SAP"
    if float(qty_requested) <= 0:
        return "BELUM PR"
    keb = 0.0 if kebutuhan is None or pd.isna(kebutuhan) else float(kebutuhan)
    if float(qty_requested) + qty_tolerance(keb) < keb:
        return "PR PARSIAL"
    return "SUDAH PR"


def canonical_bom_status(status_pr: str) -> str:
    """Petakan 'Status PR' BOM ke vocabulary bersama (vs SAP).

    Sisi BOM membedakan STOK/STOK DAN PR, sisi SAP hanya bisa memberi
    tahu ada-tidaknya Qty Requested — jadi perbandingannya di level
    "ADA PR" vs "BELUM PR". Sumbu "kurang/lebih"-nya qty diukur terpisah
    oleh check_qty_match().
    """
    if status_pr in ("SUDAH PR", "PR PARSIAL", "STOK DAN PR"):
        return "ADA PR"
    if status_pr in ("STOK", "BELUM PR"):
        return "BELUM PR"
    return "TIDAK DIKENAL"  # 'STATUS LAIN / TIDAK DIKENAL'


def check_status_match(status_pr, qty_requested) -> str:
    """Kecocokan ADA-tidaknya PR: BOM ('Status PR') vs SAP (Qty Requested)."""
    if qty_requested is None or pd.isna(qty_requested):
        return "TIDAK ADA DI SAP"
    if status_pr is None or pd.isna(status_pr):
        return "TIDAK DIKENAL"

    bom = canonical_bom_status(str(status_pr))
    if bom == "TIDAK DIKENAL":
        return "TIDAK DIKENAL"

    sap = "ADA PR" if float(qty_requested) > 0 else "BELUM PR"
    return "SESUAI" if bom == sap else "TIDAK SESUAI"


def check_qty_match(qty_requested, kebutuhan) -> str:
    """Kecocokan quantity: kebutuhan total material vs 'Qty Requested' SAP."""
    if qty_requested is None or pd.isna(qty_requested):
        return "TIDAK ADA DI SAP"

    keb = 0.0 if kebutuhan is None or pd.isna(kebutuhan) else float(kebutuhan)
    selisih = float(qty_requested) - keb
    if abs(selisih) <= qty_tolerance(keb):
        return "SESUAI"
    return "SAP KURANG" if selisih < 0 else "SAP LEBIH"


def build_hasil_match(cek_status: str, cek_qty: str) -> str:
    """Ringkasan dua sumbu pembuktian jadi satu label 'Hasil Match'.

    'TIDAK DIKENAL' pada status dihitung tidak sesuai — status yang tidak
    bisa dipastikan memang belum terbukti konsisten.
    """
    if "TIDAK ADA DI SAP" in (cek_status, cek_qty):
        return "TIDAK ADA DI SAP"
    if cek_status == "SESUAI" and cek_qty == "SESUAI":
        return "SESUAI"
    if cek_status != "SESUAI" and cek_qty != "SESUAI":
        return "STATUS & QTY TIDAK SESUAI"
    return "STATUS TIDAK SESUAI" if cek_status != "SESUAI" else "QTY TIDAK SESUAI"


def aggregate_sap_for_match(sap: pd.DataFrame) -> pd.DataFrame:
    """Satukan baris SAP ke grain SATU baris per Kode Material.

    Satu kode bisa punya banyak baris di SAP (lintas WBS / lintas PR), jadi
    'Qty Requested' & 'Ordered' dijumlah, sedangkan 'PR Status' dan 'WBS
    Elem' disatukan jadi teks koma unik. Kolom hasil memakai akhiran
    '(SAP)' supaya tidak bentrok dengan kolom BOM saat di-merge.

    Melempar ValueError bila kolom wajib absen (pemanggil menampilkan
    pesannya, validasi dimatikan, dashboard tetap jalan).
    """
    missing = [c for c in ("Kode Material", "Qty Requested") if c not in sap.columns]
    if missing:
        raise ValueError("data SAP tanpa kolom: " + ", ".join(missing))

    work = sap.copy()
    work["_key"] = work["Kode Material"].map(norm_key)
    work = work[work["_key"] != ""]
    if work.empty:
        raise ValueError("data SAP tanpa kode material")

    # Angka dari export bisa berbentuk teks ('1.234,5' / '10') — nilainya
    # diseragamkan dulu supaya sum() tidak sial menjumlah string ("10"+"5").
    work["Qty Requested"] = pd.to_numeric(work["Qty Requested"], errors="coerce")
    if "Ordered" in work.columns:
        work["Ordered"] = pd.to_numeric(work["Ordered"], errors="coerce")

    def _join(values, upper: bool = False):
        items = {str(v).strip() for v in values if pd.notna(v) and str(v).strip()}
        if upper:
            items = {item.upper() for item in items}
        return ", ".join(sorted(items)) or None

    agg = {"Kode Material": "first", "Qty Requested": "sum"}
    if "Ordered" in work.columns:
        agg["Ordered"] = "sum"
    if "PR Status" in work.columns:
        agg["PR Status"] = lambda s: _join(s, upper=True)
    if "WBS Elem" in work.columns:
        agg["WBS Elem"] = _join

    grouped = work.groupby("_key", dropna=False)
    out = grouped.agg(agg).reset_index()
    out["Baris SAP"] = grouped.size().values

    return out.rename(
        columns={
            "Kode Material": "Kode Material (SAP)",
            "Qty Requested": "Qty Requested (SAP)",
            "Ordered": "Ordered (SAP)",
            "PR Status": "PR Status (SAP)",
            "WBS Elem": "WBS (SAP)",
        }
    )


MATCH_RESULT_COLUMNS = [
    "Status SAP",
    "Cek Status",
    "Cek Qty",
    "Hasil Match",
    "Selisih Qty",
    "Qty Requested (SAP)",
    "Kebutuhan Total",
]


def match_with_sap(data: pd.DataFrame, sap: pd.DataFrame) -> pd.DataFrame:
    """Cocokkan material hasil collapse dengan data SAP (LEFT join).

    Grain keluaran = grain collapse_material (NO + Kode Material +
    Sheet Asal), sehingga satu baris = satu material dan quantity
    BOM-nya sudah total lintas trainset — satu-satunya grain yang adil
    untuk dibandingkan dengan Qty Requested SAP.

    Kunci: 'Kode Material' dulu, lalu 'Kode Material Konversi' bila kode
    utama tidak ada di SAP. Bukan inner join: material yang TIDAK ADA di
    SAP justru wajib tampil (itu bagian dari bukti konsistensi), makanya
    pakai left join + label 'TIDAK ADA DI SAP'.
    """
    mat = collapse_material(data)
    sap_agg = aggregate_sap_for_match(sap)

    mat["_key"] = mat[KEY_MATERIAL].map(norm_key)
    if KEY_MATERIAL_ALT in mat.columns:
        mat["_key_alt"] = mat[KEY_MATERIAL_ALT].map(norm_key)
    else:
        mat["_key_alt"] = ""

    sap_keys = set(sap_agg["_key"])
    mat["_key_sap"] = [
        next((k for k in (key, alt) if k in sap_keys), "")
        for key, alt in zip(mat["_key"], mat["_key_alt"])
    ]

    out = pd.merge(
        mat.drop(columns=["_key", "_key_alt"]),
        sap_agg,
        left_on="_key_sap",
        right_on="_key",
        how="left",
    )

    # Kode utama tidak tercatat di SAP tapi kode konversinya ada —
    # ditandai supaya user tahu kunci cadanganlah yang dipakai.
    out["Cocok via Kode Konversi"] = (
        (out["_key_sap"] != "")
        & (out[KEY_MATERIAL].map(norm_key) != out["_key_sap"])
    )
    out = out.drop(columns=["_key", "_key_sap"])

    qty = pd.to_numeric(out.get("Qty Requested (SAP)"), errors="coerce")
    keb = pd.to_numeric(out.get("Kebutuhan Total"), errors="coerce")

    out["Selisih Qty"] = qty - keb
    out["Status SAP"] = [
        derive_status_sap(q, k) for q, k in zip(qty, keb)
    ]
    out["Cek Status"] = [
        check_status_match(status, q)
        for status, q in zip(out["Status PR"], qty)
    ]
    out["Cek Qty"] = [check_qty_match(q, k) for q, k in zip(qty, keb)]
    out["Hasil Match"] = [
        build_hasil_match(a, b) for a, b in zip(out["Cek Status"], out["Cek Qty"])
    ]

    return out


def attach_match(rows: pd.DataFrame, matched: pd.DataFrame) -> pd.DataFrame:
    """Bawa kolom hasil match ke frame baris (mode trainset spesifik).

    Hasil match dihitung sekali di grain material (lihat match_with_sap),
    lalu di-BROADCAST ke tiap baris trainset lewat kunci collapse — bukan
    dihitung ulang per baris. Akibatnya 'Selisih Qty' di baris trainset =
    selisih material (lintas trainset), bukan selisih baris itu sendiri.

    Kunci di-cast ke str di kedua sisi supaya baris tanpa NO / tanpa kode
    material tetap ketemu (NaN -> string 'nan' di kedua sisi).
    """
    cols = [
        c
        for c in MATCH_RESULT_COLUMNS + ["Cocok via Kode Konversi"]
        if c in matched.columns and c not in rows.columns
    ]

    left = rows.copy()
    right = matched[cols + MATCH_KEYS].copy()

    for frame in (left, right):
        for key in MATCH_KEYS:
            frame[f"_k_{key}"] = frame[key].astype(str).str.strip()
    join_cols = [f"_k_{key}" for key in MATCH_KEYS]

    # Kunci asli di frame kanan dibuang supaya merge tidak menghasilkan
    # kolom bersufiks _x/_y ('Kode Material' dst. sudah ada di frame kiri).
    merged = left.merge(right.drop(columns=MATCH_KEYS), on=join_cols, how="left")
    return merged.drop(columns=join_cols)


def match_summary(matched: pd.DataFrame) -> pd.Series:
    """Jumlah material per label 'Hasil Match' (urut MATCH_ORDER, 0 diisi)."""
    if matched.empty or "Hasil Match" not in matched.columns:
        return pd.Series(0, index=MATCH_ORDER, name="Jumlah")
    counts = matched["Hasil Match"].value_counts()
    return counts.reindex(MATCH_ORDER, fill_value=0).astype(int).rename("Jumlah")


def match_crosstab(matched: pd.DataFrame) -> pd.DataFrame:
    """Matriks bukti: 'Status PR' BOM (baris) x 'Status SAP' (kolom).

    Dihitung dari frame material (bukan frame baris) supaya satu material
    tidak terhitung berkali-kali. Reindex agar status yang tidak muncul
    tetap terlihat sebagai baris/kolom 0 — tidak ada temuan yang hilang.
    """
    empty = pd.DataFrame(0, index=STATUS_PR_ORDER, columns=SAP_STATUS_ORDER)
    if matched.empty or "Status SAP" not in matched.columns:
        return empty

    table = pd.crosstab(matched["Status PR"], matched["Status SAP"])
    return (
        table.reindex(index=STATUS_PR_ORDER, columns=SAP_STATUS_ORDER, fill_value=0)
        .fillna(0)
        .astype(int)
    )


def cek_counts(matched: pd.DataFrame, col: str, order) -> pd.Series:
    """Jumlah material per label kolom `col` ('Cek Status' / 'Cek Qty').

    Reindex dengan `order` masing-masing sumbu supaya label yang tidak
    muncul tetap terlihat sebagai 0 — tidak ada temuan yang hilang.
    """
    if matched.empty or col not in matched.columns:
        return pd.Series(0, index=list(order), dtype=int, name=col)
    return matched[col].value_counts().reindex(order, fill_value=0).astype(int).rename(col)


# ============================================================
# AGREGASI CHART TAMBAHAN (TREN DELIV DATE)
# ============================================================

def build_delivery_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Jumlah item per bulan kolom 'Deliv Date SAP' (format dd.mm.yyyy).

    Bulan tanpa pengiriman di tengah rentang direindex jadi 0, supaya
    celah timeline terlihat apa adanya (mis. tidak ada pengiriman antara
    2024-10 dan 2025-03). Baris tanpa tanggal / tanggal tidak valid
    tidak dihitung — pemanggil bisa membandingkan sum(Jumlah) dengan
    len(df) untuk tahu sisanya.

    Mengembalikan frame ['Bulan' (string 'YYYY-MM', urut), 'Jumlah'].
    Kosong bila kolomnya tidak ada atau tidak ada satu pun tanggal valid.
    """
    if "Deliv Date SAP" not in df.columns:
        return pd.DataFrame(columns=["Bulan", "Jumlah"])

    dates = pd.to_datetime(df["Deliv Date SAP"], format="%d.%m.%Y", errors="coerce")
    per_bulan = dates.dropna().dt.to_period("M")
    if per_bulan.empty:
        return pd.DataFrame(columns=["Bulan", "Jumlah"])

    rentang = pd.period_range(per_bulan.min(), per_bulan.max(), freq="M")
    jumlah = per_bulan.value_counts().reindex(rentang, fill_value=0).sort_index()

    out = jumlah.rename_axis("Bulan").reset_index(name="Jumlah")
    out["Bulan"] = out["Bulan"].astype(str)
    return out


def status_by_trainset(
    df: pd.DataFrame,
    order=STATUS_ORDER,
    all_trainsets=None,
) -> pd.DataFrame:
    """Persentase komposisi 'Tindak Lanjut' per Trainset (stack 100%).

    Dihitung dari baris apa adanya, BUKAN dari 'Status PR' hasil
    collapse — semua material punya baris di setiap trainset, jadi
    Status PR akan menghasilkan batang identik di semua trainset.
    Nilai = (jumlah baris per status / total baris trainset) x 100.

    all_trainsets: daftar lengkap trainset yang harus tampil di sumbu-X.
    Trainset tanpa baris di df (mis. difilter keluar) tetap muncul
    dengan nilai 0, jadi chart tidak menyusut saat filter menyempit.

    Urutan baris = numerik (TS 9, TS 10, ..., TS 16); status yang tidak
    muncul di data dibuang dari kolom (legend tidak menampilkan kategori
    kosong). Mengembalikan frame kosong bila datanya kosong.
    """
    if df.empty or "Trainset" not in df.columns:
        return pd.DataFrame()

    def _ts_key(t):
        parts = str(t).split()
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    if all_trainsets is None:
        all_trainsets = df["Trainset"].dropna().unique()
    ts_order = sorted(all_trainsets, key=_ts_key)

    ctab = pd.crosstab(df["Trainset"], df["Tindak Lanjut"])
    ctab = ctab.reindex(index=ts_order, fill_value=0)
    ctab = ctab[[s for s in order if s in ctab.columns]]

    total = ctab.sum(axis=1).astype(float)
    total = total.where(total > 0)  # 0 -> NaN, dibuang di fillna
    return ctab.div(total, axis=0).fillna(0) * 100


# ============================================================
# SIDEBAR
# ============================================================

def reset_sidebar():
    """Bersihkan cache data sumber.

    Dipakai sebagai on_click callback.
    """
    st.cache_data.clear()


def render_sidebar():
    with st.sidebar:

        if st.button(
            "Load Data",
            width="stretch",
            key="btn_reload_tl",
        ):
            st.cache_data.clear()
            st.rerun()

        st.divider()

        # Validasi terhadap SAP: sumbernya satu spreadsheet SAP yang
        # di-export sebagai CSV publik (DEFAULT_SAP_URL) — tanpa upload
        # file Excel dan tanpa mengubah apa pun di spreadsheet itu.
        st.toggle(
            "Validasi terhadap SAP",
            value=True,
            key="mp3_validasi_sap",
            help=(
                "Cocokkan status PR dan quantity material dengan sheet SAP "
                "(publik, ±27 MB, di-cache 1 jam). Bila sheet-nya tidak bisa "
                "dibaca, matikan lewat toggle ini — dashboard tetap jalan "
                "tanpa kolom validasi."
            ),
        )

        st.divider()

        st.button(
            "Reset",
            width="stretch",
            key="btn_reset_tl",
            on_click=reset_sidebar,
        )


# ============================================================
# DASHBOARD
# ============================================================

def scope_sap(sap, wbs: str = "Semua"):
    """Saring frame SAP ke satu WBS bila diminta; 'Semua' = tanpa saringan.

    Kebutuhan BOM di sini hanya satu proyek, sedangkan sheet SAP berisi
    banyak proyek (±53 kode WBS). Tanpa saringan, qty SAP tercampur lintas
    proyek sehingga material yang dipakai bersama beberapa proyek terlihat
    'SAP LEBIH' padahal bukan milik proyek ini.
    """
    if sap is None:
        return None

    if not wbs or wbs == "Semua" or "WBS Elem" not in sap.columns:
        return sap

    return sap[sap["WBS Elem"] == wbs]


def load_match(data: pd.DataFrame, sap):
    """Hitung hasil match BOM <-> SAP untuk `data` (grain material).

    `sap` sudah disaring lewat scope_sap() di pemanggil supaya pilihan
    filter WBS dan data yang dipakai tidak bisa meleset. Mengembalikan
    None bila validasi dimatikan di sidebar atau data SAP tidak tersedia
    — pesannya tampil sebagai caption dan dashboard tetap berjalan tanpa
    kolom match (perilaku lama persis).
    """
    if not st.session_state.get("mp3_validasi_sap", True):
        return None

    if data.empty or sap is None:
        return None

    if sap.empty:
        st.caption("Validasi SAP tidak aktif: sheet SAP kosong setelah disaring.")
        return None

    try:
        return match_with_sap(data, sap)
    except ValueError as exc:
        st.caption(f"Validasi SAP tidak aktif: {exc}.")
        return None


def render_validation(matched: pd.DataFrame, sumber: str = "") -> None:
    """Panel bukti konsistensi BOM <-> SAP (grain material).

    Isinya: kartu KPI per 'Hasil Match', matriks 'Status PR' x 'Status
    SAP', dan sebaran hasil match. Semua dihitung dari frame material —
    bukan frame baris — supaya satu material tidak dihitung berkali-kali.
    `sumber` (opsional) menjelaskan berapa baris sheet SAP yang dipakai
    dan apakah sudah disaring ke satu WBS.
    """
    with st.container(border=True):
        st.subheader("Validasi terhadap SAP")

        if matched.empty:
            st.info("Tidak ada material untuk divalidasi.")
            return

        st.caption(
            f"Grain: satu baris per material (lintas trainset). "
            f"Kebutuhan = jumlah kolom '{QTY_BOM_COLUMN}', pembanding = "
            f"'Qty Requested' SAP (toleransi {QTY_TOLERANCE_REL:.0%} atau "
            f"{QTY_TOLERANCE_ABS} satuan). 'Cek Status' membandingkan "
            "ada-tidaknya PR di kedua sisi. Kolom qty memakai pemisah "
            "ribuan gaya Indonesia, sudah dinormalkan ('1.056' -> 1056)."
            + (f" Sumber: {sumber}." if sumber else "")
        )

        summary = match_summary(matched)
        total = len(matched)
        sesuai = int(summary["SESUAI"])

        kpi_cols = st.columns(len(MATCH_ORDER))
        for col, label in zip(kpi_cols, MATCH_ORDER):
            with col:
                st.subheader(MATCH_SHORT_LABEL[label])
                st.markdown(f"# {int(summary[label]):,}")

        st.caption(
            f"Material divalidasi: {total:,} | sesuai: {sesuai:,} "
            f"({sesuai / total * 100:.1f}%) | "
            f"tidak sesuai: {total - sesuai:,}"
        )

        col_matrix, col_chart = st.columns(2, border=True)
        with col_matrix:
            st.markdown("**Status PR (BOM) x Status SAP**")
            st.dataframe(match_crosstab(matched))
        with col_chart:
            st.markdown("**Sebaran Hasil Match**")
            bar = summary[summary > 0].reset_index()
            bar.columns = ["Hasil Match", "Jumlah"]
            if bar.empty:
                st.info("Belum ada data untuk ditampilkan.")
            else:
                match_chart = px.pie(
                    bar,
                    values="Jumlah",
                    names="Hasil Match",
                    hole=0.3,
                    color="Hasil Match",
                    color_discrete_map=MATCH_COLORS,
                )
                match_chart.update_layout(
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.1,
                        xanchor="center",
                        x=0.5,
                    )
                )
                st.plotly_chart(match_chart, width="stretch")

        col_status, col_qty = st.columns(2, border=True)
        with col_status:
            st.markdown("**Rincian Cek Status**")
            st.dataframe(cek_counts(matched, "Cek Status", CEK_STATUS_ORDER))
        with col_qty:
            st.markdown("**Rincian Cek Qty**")
            st.dataframe(cek_counts(matched, "Cek Qty", CEK_QTY_ORDER))


def render_dashboard():
    try:
        raw = load_bom_data(DEFAULT_BOM_URL)
    except Exception as exc:
        st.error(
            f"Gagal membaca data dari Google Sheets: {exc}. "
            "Periksa koneksi & pastikan spreadsheet dishare publik."
        )
        return

    data = prepare_data(raw)

    if data.empty:
        st.warning(
            "Tidak ada baris yang memiliki nilai 'Tindak Lanjut'. "
            "Periksa isi sheet sumber."
        )
        return

    data_lintas_ts = data

    if st.session_state.get("mp3_validasi_sap", True):
        sap, sap_error = load_sap_data()
        if sap_error:
            st.caption(f"Validasi SAP tidak aktif: {sap_error}.")
    else:
        sap = None

    # ------------------------------------------------------------
    # Filter (di luar container utama, seperti dashboard pertama)
    # ------------------------------------------------------------

    trainset_options = ("Semua",) + tuple(sorted(data["Trainset"].dropna().unique()))
    col_trainset, col_sheet = st.columns([1, 1])
    with col_trainset:
        if "TS 14" in trainset_options:
            index = trainset_options.index("TS 14")
        else:
            index = 0
        selected_trainset = st.selectbox("Pilih Trainset", trainset_options, index=index)
    if selected_trainset != "Semua":
        data = data[data["Trainset"] == selected_trainset]

    sheet_options = ("Semua",) + tuple(sorted(data["Sheet Asal"].dropna().unique()))
    with col_sheet:
        selected_sheet = st.selectbox("Sheet Asal", sheet_options)
    if selected_sheet != "Semua":
        data = data[data["Sheet Asal"] == selected_sheet]
        data_lintas_ts = data_lintas_ts[data_lintas_ts["Sheet Asal"] == selected_sheet]

    # Filter WBS hanya muncul bila SAP terbaca; default "Semua" =
    # seluruh proyek ikut dijumlah (lihat scope_sap).
    selected_wbs = "Semua"
    if sap is not None and "WBS Elem" in sap.columns:
        wbs_options = ["Semua"] + sorted(
            {str(v).strip() for v in sap["WBS Elem"].dropna() if str(v).strip()}
        )
        desc_map = {}
        if "WBS Description" in sap.columns:
            desc_map = (
                sap.dropna(subset=["WBS Elem"])
                .groupby("WBS Elem")["WBS Description"]
                .first()
                .to_dict()
            )

        def _wbs_label(value):
            if value == "Semua":
                return "Semua WBS (seluruh proyek)"
            desc = str(desc_map.get(value, "")).strip()
            return f"{value} — {desc}" if desc else value

        col_wbs = st.columns([1])[0]
        with col_wbs:
            selected_wbs = st.selectbox(
                "Proyek (WBS)",
                wbs_options,
                format_func=_wbs_label,
                help=(
                    "Sheet SAP memuat banyak proyek. Pilih WBS milik "
                    "proyek BOM ini bila 'Cek Qty' terlihat melebar — "
                    "qty SAP yang dijumlah lintas proyek memang tidak "
                    "adil dibandingkan kebutuhan satu proyek."
                ),
            )

    sap_scope = scope_sap(sap, selected_wbs)
    matched = load_match(data_lintas_ts, sap_scope)

    if matched is not None:
        sumber = f"{len(sap):,} baris sheet SAP"
        if selected_wbs != "Semua":
            sumber += f" → disaring ke WBS {selected_wbs} ({len(sap_scope):,} baris)"
        else:
            sumber += " (seluruh WBS)"
    else:
        sumber = ""

    mode_semua = selected_trainset == "Semua"

    if mode_semua:
        # matched = collapse_material(data_lintas_ts) + kolom match,
        # jadi di mode ini frame-nya sudah persis grain collapse.
        frame = matched if matched is not None else collapse_material(data)
        status_col = "Status PR"
        status_order = STATUS_PR_ORDER
        status_colors = STATUS_PR_COLORS
    else:
        # Hasil match di-broadcast dari grain material ke baris trainset
        # (attach_match) — tidak dihitung ulang per baris.
        frame = attach_match(data, matched) if matched is not None else data
        status_col = "Tindak Lanjut"
        status_order = STATUS_ORDER
        status_colors = STATUS_COLORS

    counts = count_by_status(frame, status_col, status_order)
    percentages = percentage_by_status(counts)

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)

        with col1:
            st.subheader("Total Item")
            st.markdown(f"# {len(frame):,}")
        with col2:
            st.subheader("Jumlah Material")
            st.markdown(f"# {frame['Kode Material'].nunique():,}")
        with col3:
            st.subheader("Trainset")
            st.markdown(f"# {data['Trainset'].nunique()}")

        with st.container(border=True):
            col1, col2 = st.columns(2, border=True)

            with col1:
                sheet_totals = counts.sum(axis=0)
                sheet_df = sheet_totals[sheet_totals > 0].reset_index()
                sheet_df.columns = ["Group", "Jumlah"]
                st.subheader("Group")
                if sheet_df.empty:
                    st.info("Belum ada data untuk ditampilkan.")
                else:
                    shchart = px.pie(
                        sheet_df,
                        values="Jumlah",
                        names="Group",
                        hole=0.3,
                    )
                    shchart.update_layout(
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.1,
                            xanchor="center",
                            x=0.5
                        )
                    )
                    st.plotly_chart(shchart, width="stretch", height=450)
            with col2:
                status_totals = counts.sum(axis=1)
                pie_df = status_totals[status_totals > 0].reset_index()
                pie_df.columns = [status_col, "Jumlah"]
                st.subheader("Status PR")
                if pie_df.empty:
                    st.info("Belum ada data untuk ditampilkan.")
                else:
                    tlchart = px.pie(
                        pie_df,
                        values="Jumlah",
                        names=status_col,
                        hole=0.3,
                        color=status_col,
                        color_discrete_map=status_colors,
                    )
                    tlchart.update_layout(
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.1,
                            xanchor="center",
                            x=0.5
                        )
                    )
                    st.plotly_chart(tlchart)
            

        with st.container(border=True):
            st.subheader(f"Jumlah per {status_col}")
            st.dataframe(counts)

        with st.container(border=True):
            st.subheader(f"Persentase per {status_col} (%)")
            st.dataframe(percentages)

        if matched is not None:
            render_validation(matched, sumber)

        with st.container(border=True):
            toggle_cols = st.columns(3)
            selected_labels = []
            for i, status in enumerate(status_order):
                with toggle_cols[i % 3]:
                    if st.toggle(status, key=f"tl_toggle_{status_col}_{status}"):
                        selected_labels.append(status)

            df_terfilter = frame.copy()
            if selected_labels:
                df_terfilter = frame[frame[status_col].isin(selected_labels)]

            if matched is not None:
                if st.toggle(
                    "Hanya baris yang tidak cocok dengan SAP",
                    key="tl_toggle_sap_mismatch",
                ):
                    df_terfilter = df_terfilter[
                        df_terfilter["Hasil Match"] != "SESUAI"
                    ]
                if not mode_semua:
                    st.caption(
                        "Kolom 'Status SAP', 'Cek Status', 'Cek Qty', "
                        "'Hasil Match' & 'Selisih Qty' adalah hasil agregat "
                        "material lintas trainset (sama untuk tiap baris "
                        "material tsb)."
                    )

            st.subheader(
                f"Dataset Bill Of Materials"
                f"({len(df_terfilter):,})"
            )
            st.dataframe(df_terfilter)

            st.download_button(
                "Download CSV",
                data=df_terfilter.to_csv(index=False).encode("utf-8"),
                file_name=f"tindak_lanjut_{selected_trainset.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )

def render():
    st.title("Statistics Dashboard")

    render_sidebar()
    render_dashboard()
