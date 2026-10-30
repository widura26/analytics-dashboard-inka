import io

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

import material_planning_2 as mp2

DEFAULT_BOM_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1axKrvf147x0eQ9hsuHZA4Ur943bzQFttJ6W0oQuK7RQ/"
    "export?format=csv&gid=176301186"
)

SAP_SHEET_ID = "1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q"
SAP_SHEET_GID = 826586568
DEFAULT_SAP_URL = (
    f"https://docs.google.com/spreadsheets/d/{SAP_SHEET_ID}"
    f"/export?format=csv&gid={SAP_SHEET_GID}"
)

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
    "Item",  # Pembeda item dalam PR yang sama (00070 vs 00330)
    "PR Deliv. Dat",  # Tanggal pengiriman pembeda TS 12 & TS 13
    "Requested By",  # PPR-STS-TS12 vs PPR-STS-TS13
)

SAP_REQUIRED_COLUMNS = ("Kode Material", "Qty Requested")
SAP_QTY_COLUMNS = ("Qty Requested", "Ordered")

STATUS_ORDER = [
    "PR EKSISTING",
    "PR BARU",
    "BELUM PR",
    "MENUNGGU KOMAT",
    "MENGGUNAKAN STOK",
    "MENUNGGU EVALUASI PEMAKAIAN",
]

STATUS_RENAME = {
    "MENGGUNAKAN STOK GUDANG": "MENGGUNAKAN STOK",
}

STATUS_COLORS = {
    "PR EKSISTING": "#636EFA",
    "PR BARU": "#00CC96",
    "BELUM PR": "#EF553B",
    "MENUNGGU KOMAT": "#FFA15A",
    "MENGGUNAKAN STOK": "#AB63FA",
    "MENUNGGU EVALUASI PEMAKAIAN": "#FF9896",
}

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
DATA_CACHE_TTL = 300
SAP_CACHE_TTL = 3600
SAP_REQUEST_TIMEOUT = 120

QTY_TOLERANCE_REL = 0.01
QTY_TOLERANCE_ABS = 0.01
QTY_BOM_COLUMN = "SUM"
QTY_BOM_FALLBACK = "Kebutuhan"

QTY_RAW_COLUMNS = ("Kebutuhan", "SUM", "Qty PR VE", "Qty PR MS")
KEY_MATERIAL = "Kode Material"
KEY_MATERIAL_ALT = "Kode Material Konversi"
MATCH_KEYS = ["NO", KEY_MATERIAL, "Sheet Asal"]
SAP_STATUS_ORDER = ["SUDAH PR", "PR PARSIAL", "BELUM PR", "TIDAK ADA DI SAP"]

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

def read_bom_csv(content: bytes) -> pd.DataFrame:
    buf = io.BytesIO(content)
    header = pd.read_csv(buf, nrows=0).columns
    buf.seek(0)
    dtype = {col: str for col in QTY_RAW_COLUMNS if col in header}
    return pd.read_csv(buf, dtype=dtype or None, low_memory=False)


@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner="Membaca data BOM dari Google Sheets...")
def load_bom_data(url: str) -> pd.DataFrame:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return read_bom_csv(response.content)


def read_sap_csv(content: bytes) -> pd.DataFrame:
    buf = io.BytesIO(content)
    header = list(pd.read_csv(buf, nrows=0).columns)

    def _norm(name) -> str:
        return str(name).replace("\xa0", " ").strip()

    # Gunakan dict.fromkeys untuk mencegah duplikasi nama kolom di usecols
    usecols = list(
        dict.fromkeys(col for col in header if _norm(col) in SAP_USE_COLUMNS)
    )
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
    try:
        response = requests.get(DEFAULT_SAP_URL, timeout=SAP_REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        return None, f"gagal mengunduh sheet SAP: {exc}"

    isi = response.content.lstrip()[:1].lower()
    if isi == b"<":
        return None, "sheet SAP tidak di-share publik (balasannya HTML, bukan CSV)"

    try:
        return read_sap_csv(response.content), None
    except Exception as exc:
        return None, str(exc)


def prepare_data(raw: pd.DataFrame) -> pd.DataFrame:
    tindak = raw["Tindak Lanjut"]
    punya_tindak = tindak.notna() & tindak.astype(str).str.strip().ne("")

    # Tambahkan filter Group jika kolom Group tersedia
    if "Group" in raw.columns:
        punya_group = raw["Group"].notna() & raw["Group"].astype(str).str.strip().ne("")
        mask = punya_tindak & punya_group
    else:
        mask = punya_tindak

    df = raw[mask].copy()
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

def check_pr_status(text) -> str:
    if pd.isna(text) or not str(text).strip():
        return "BELUM PR"

    arr_status = {
        status.strip().upper()
        for status in str(text).split(",")
    }

    if (
        "PR BARU" in arr_status
        and (
            "BELUM PR" in arr_status
            or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
        )
    ):
        return "PR PARSIAL"

    if (
        "MENGGUNAKAN STOK" in arr_status
        and (
            "PR BARU" in arr_status
            or "PR EKSISTING" in arr_status
        )
    ):
        return "STOK DAN PR"

    if arr_status == {"MENGGUNAKAN STOK"}:
        return "STOK"

    if (
        "PR BARU" in arr_status
        or "PR EKSISTING" in arr_status
    ):
        return "SUDAH PR"

    if (
        "BELUM PR" in arr_status
        or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
    ):
        return "BELUM PR"

    return "STATUS LAIN / TIDAK DIKENAL"


def collapse_material(data: pd.DataFrame) -> pd.DataFrame:
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

    qty_col = (
        QTY_BOM_COLUMN
        if QTY_BOM_COLUMN in df.columns
        else (QTY_BOM_FALLBACK if QTY_BOM_FALLBACK in df.columns else None)
    )
    if qty_col:
        qty = grouped[qty_col].sum().reset_index(name="Kebutuhan")
        joined = pd.merge(joined, qty, on=MATCH_KEYS, how="left")

    pr_cols = [c for c in ("Qty PR VE", "Qty PR MS") if c in df.columns]
    if pr_cols:
        pr = grouped[pr_cols].sum().reset_index()
        pr["Qty PR (BOM)"] = pr[pr_cols].sum(axis=1)
        joined = pd.merge(
            joined, pr[MATCH_KEYS + ["Qty PR (BOM)"]], on=MATCH_KEYS, how="left"
        )

    if KEY_MATERIAL_ALT in df.columns:
        alt = grouped[KEY_MATERIAL_ALT].first().reset_index()
        joined = pd.merge(joined, alt, on=MATCH_KEYS, how="left")

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


def collapse_material_total_trainset(data: pd.DataFrame) -> pd.DataFrame:
    """Gabungkan baris BOM seperti 'final_bom_data' pada algoritma referensi:

    - Group by ['NO', 'Kode Material', 'Group'] (bukan 'Sheet Asal')
    - Gabungkan nilai 'Trainset' dan 'Sheet Asal' menjadi string pemisah koma
    - Terapkan check_pr_status pada 'Tindak Lanjut' yang sudah digabung
    - Paksa 'WBS Elem' = 'S-22002.1' supaya inner-join dengan SAP (WBS_Group)
      bisa bekerja sebagaimana algoritma referensi.
    """
    df = data.copy()
    df["NO"] = df["NO"].astype(str).str.strip()

    grouped = df.groupby(["NO", "Kode Material", "Group"], dropna=False)

    joined_tl = grouped["Tindak Lanjut"].apply(
        lambda s: ", ".join(sorted(s.unique(), key=lambda t: str(t)))
    ).reset_index()

    desc = grouped["Deskripsi Material"].first().reset_index()
    joined_tl = pd.merge(
        joined_tl, desc, on=["NO", "Kode Material", "Group"], how="left"
    )

    qty_col = (
        QTY_BOM_COLUMN
        if QTY_BOM_COLUMN in df.columns
        else (QTY_BOM_FALLBACK if QTY_BOM_FALLBACK in df.columns else None)
    )
    if qty_col:
        qty = grouped[qty_col].sum().reset_index(name="Kebutuhan")
        joined_tl = pd.merge(
            joined_tl, qty, on=["NO", "Kode Material", "Group"], how="left"
        )

    pr_cols = [c for c in ("Qty PR VE", "Qty PR MS") if c in df.columns]
    if pr_cols:
        pr = grouped[pr_cols].sum().reset_index()
        pr["Qty PR (BOM)"] = pr[pr_cols].sum(axis=1)
        joined_tl = pd.merge(
            joined_tl,
            pr[["NO", "Kode Material", "Group", "Qty PR (BOM)"]],
            on=["NO", "Kode Material", "Group"],
            how="left",
        )

    if KEY_MATERIAL_ALT in df.columns:
        alt = grouped[KEY_MATERIAL_ALT].first().reset_index()
        joined_tl = pd.merge(
            joined_tl, alt, on=["NO", "Kode Material", "Group"], how="left"
        )

    joined_tl["Trainset"] = grouped["Trainset"].apply(
        lambda s: ", ".join(
            sorted(
                s.dropna().unique(),
                key=lambda t: int(t.split()[1]) if t.split()[1].isdigit() else 0,
            )
        )
    ).values

    joined_tl["Sheet Asal"] = grouped["Sheet Asal"].apply(
        lambda s: ", ".join(sorted(s.unique()))
    ).values

    joined_tl["Status PR"] = joined_tl["Tindak Lanjut"].apply(check_pr_status)
    joined_tl["WBS Elem"] = "S-22002.1"
    return joined_tl


def norm_key(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    return "" if text == "NAN" else text


def qty_tolerance(kebutuhan) -> float:
    if kebutuhan is None or pd.isna(kebutuhan):
        return QTY_TOLERANCE_ABS
    return max(QTY_TOLERANCE_ABS, abs(float(kebutuhan)) * QTY_TOLERANCE_REL)


def derive_status_sap(qty_requested, kebutuhan) -> str:
    if qty_requested is None or pd.isna(qty_requested):
        return "TIDAK ADA DI SAP"
    if float(qty_requested) <= 0:
        return "BELUM PR"
    keb = 0.0 if kebutuhan is None or pd.isna(kebutuhan) else float(kebutuhan)
    if float(qty_requested) + qty_tolerance(keb) < keb:
        return "PR PARSIAL"
    return "SUDAH PR"


def canonical_bom_status(status_pr: str) -> str:
    if status_pr in ("SUDAH PR", "PR PARSIAL", "STOK DAN PR"):
        return "ADA PR"
    if status_pr in ("STOK", "BELUM PR"):
        return "BELUM PR"
    return "TIDAK DIKENAL"


def check_status_match(status_pr, qty_requested) -> str:
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
    if qty_requested is None or pd.isna(qty_requested):
        return "TIDAK ADA DI SAP"

    keb = 0.0 if kebutuhan is None or pd.isna(kebutuhan) else float(kebutuhan)
    selisih = float(qty_requested) - keb
    if abs(selisih) <= qty_tolerance(keb):
        return "SESUAI"
    return "SAP KURANG" if selisih < 0 else "SAP LEBIH"


def build_hasil_match(cek_status: str, cek_qty: str) -> str:
    if "TIDAK ADA DI SAP" in (cek_status, cek_qty):
        return "TIDAK ADA DI SAP"
    if cek_status == "SESUAI" and cek_qty == "SESUAI":
        return "SESUAI"
    if cek_status != "SESUAI" and cek_qty != "SESUAI":
        return "STATUS & QTY TIDAK SESUAI"
    return "STATUS TIDAK SESUAI" if cek_status != "SESUAI" else "QTY TIDAK SESUAI"


def aggregate_sap_for_match(sap: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in ("Kode Material", "Qty Requested") if c not in sap.columns]
    if missing:
        raise ValueError("data SAP tanpa kolom: " + ", ".join(missing))

    work = sap.copy()
    work["_key"] = work["Kode Material"].map(norm_key)
    work = work[work["_key"] != ""]
    if work.empty:
        raise ValueError("data SAP tanpa kode material")

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
            "Qty Requested": "Qty Requested",
            "Ordered": "Ordered",
            "PR Status": "PR Status (SAP)",
        }
    )

MATCH_RESULT_COLUMNS = [
    "Status SAP",
    "Cek Status",
    "Cek Qty",
    "Hasil Match",
    "Qty Requested",
    "Kebutuhan",
    "Baris SAP"
]

def match_with_sap(data: pd.DataFrame, sap: pd.DataFrame) -> pd.DataFrame:
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
    out = out.drop(columns=["_key", "_key_sap"])

    qty = pd.to_numeric(out.get("Qty Requested"), errors="coerce")
    keb = pd.to_numeric(out.get("Kebutuhan"), errors="coerce")
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

    merged = left.merge(right.drop(columns=MATCH_KEYS), on=join_cols, how="left")
    return merged.drop(columns=join_cols)


def match_summary(matched: pd.DataFrame) -> pd.Series:
    if matched.empty or "Hasil Match" not in matched.columns:
        return pd.Series(0, index=MATCH_ORDER, name="Jumlah")
    counts = matched["Hasil Match"].value_counts()
    return counts.reindex(MATCH_ORDER, fill_value=0).astype(int).rename("Jumlah")

def match_crosstab(matched: pd.DataFrame) -> pd.DataFrame:
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
    if matched.empty or col not in matched.columns:
        return pd.Series(0, index=list(order), dtype=int, name=col)
    return matched[col].value_counts().reindex(order, fill_value=0).astype(int).rename(col)

def build_delivery_trend(df: pd.DataFrame) -> pd.DataFrame:
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

def reset_sidebar():
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

def scope_sap(sap, wbs: str = "Semua"):
    if sap is None:
        return None

    if not wbs or wbs == "Semua" or "WBS Elem" not in sap.columns:
        return sap

    return sap[sap["WBS Elem"] == wbs]


def load_match(data: pd.DataFrame, sap):
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

@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner="Membaca data BOM dari Google Sheets...")
def render_sap_data() -> None:
    sap_url_data = DEFAULT_SAP_URL
    sap_data = pd.read_csv(sap_url_data, low_memory=False)
    sap_data.drop_duplicates(inplace=True)

    sap_data.columns = sap_data.columns.str.strip()

    sap_data['Qty Requested'] = (sap_data['Qty Requested'].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False))
    sap_data['Qty Requested'] = (pd.to_numeric(sap_data['Qty Requested'], errors='coerce').fillna(0).astype(int))

    sap_data['Ordered'] = pd.to_numeric(sap_data['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Ordered'] = sap_data['Ordered'].fillna(0).astype(int)

    sap_data['Req.Date'] = pd.to_datetime(sap_data['Req.Date'], format='%d.%m.%Y')
    sap_data['PR Deliv. Dat'] = pd.to_datetime(sap_data['PR Deliv. Dat'], format='%d.%m.%Y')
    sap_data['Chngd on'] = pd.to_datetime(sap_data['Chngd on'], format='%d.%m.%Y')
    sap_data["WBS_Group"] = sap_data["WBS Elem"].str.extract(r"^([A-Z]-\d+\.\d+)")

    sap_data = sap_data[sap_data["Kode Material"].notna()]
    filtered_sap_data = sap_data.groupby(['Kode Material', 'WBS_Group'], as_index=False).agg({
        'WBS Description': 'first',
        'Mat. Description': 'first',
        'Spesifikasi': 'first',
        'PR Status': 'first',
        'Qty Requested': 'sum',
        'Ordered': 'sum'
    })
    return filtered_sap_data


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
    sheet_semua = selected_sheet == "Semua"

    if sheet_semua:
        frame = collapse_material_total_trainset(data)
        status_col = "Status PR"
        status_order = STATUS_PR_ORDER
        status_colors = STATUS_PR_COLORS
        matched = None
    elif mode_semua:
        frame = collapse_material(data)
        status_col = "Status PR"
        status_order = STATUS_PR_ORDER
        status_colors = STATUS_PR_COLORS
    else:
        frame = data.copy()
        status_col = "Tindak Lanjut"
        status_order = STATUS_ORDER
        status_colors = STATUS_COLORS

    counts = count_by_status(frame, status_col, status_order)
    percentages = percentage_by_status(counts)

    with st.container(border=True):
        col1, col2 = st.columns(2, border=True)

        with col1:
            st.subheader("Total Item")
            st.markdown(f"# {len(frame):,}")
        with col2:
            st.subheader("Jumlah Material")
            st.markdown(f"# {frame['Kode Material'].nunique():,}")
        # with col3:
        #     st.subheader("Trainset")
        #     st.markdown(f"# {data['Trainset'].nunique()}")

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
            
        # with st.container(border=True):
        #     st.subheader("Komposisi Tindak Lanjut per Trainset")
        #     semua_trainset = list(trainset_options[1:])  # tanpa "Semua"
        #     per_ts = status_by_trainset(data, all_trainsets=semua_trainset)
        #     if per_ts.empty:
        #         st.info("Belum ada baris untuk ditampilkan.")
        #     else:
        #         if mode_semua:
        #             st.caption(
        #                 "Persentase dari jumlah baris tiap trainset "
        #                 "(Tindak Lanjut per baris, bukan Status PR hasil "
        #                 "gabungan lintas trainset)."
        #             )
        #         else:
        #             st.caption(
        #                 f"Persentase dari jumlah baris {selected_trainset}; "
        #                 "trainset lain tampil 0 karena datanya sudah "
        #                 "difilter."
        #             )
        #         ts_long = per_ts.reset_index().melt(
        #             id_vars="Trainset",
        #             var_name="Tindak Lanjut",
        #             value_name="Persentase (%)",
        #         )
        #         ts_chart = px.bar(
        #             ts_long,
        #             x="Trainset",
        #             y="Persentase (%)",
        #             color="Tindak Lanjut",
        #             barmode="stack",
        #             color_discrete_map=STATUS_COLORS,
        #             category_orders={
        #                 "Trainset": list(per_ts.index),
        #                 "Tindak Lanjut": list(per_ts.columns),
        #             },
        #         )
        #         ts_chart.update_yaxes(ticksuffix="%", range=[0, 100])
        #         ts_chart.update_layout(
        #             xaxis_title="Trainset",
        #             yaxis_title="Persentase (%)",
        #             legend=dict(
        #                 orientation="h",
        #                 yanchor="top",
        #                 y=-0.15,
        #                 xanchor="center",
        #                 x=0.5,
        #             ),
        #         )
        #         st.plotly_chart(ts_chart, width="stretch", height=450)

        with st.container(border=True):
            st.subheader(f"Jumlah per {status_col}")
            st.dataframe(counts)

        # with st.container(border=True):
        #     st.subheader(f"Persentase per {status_col} (%)")
        #     st.dataframe(percentages)

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

            # if matched is not None:
            #     if st.toggle(
            #         "Hanya baris yang tidak cocok dengan SAP",
            #         key="tl_toggle_sap_mismatch",
            #     ):
            #         mismatched = matched[matched["Hasil Match"] != "SESUAI"]
            #         # Ambil kunci yang tidak sesuai
            #         mismatch_keys = set(
            #             zip(
            #                 mismatched["NO"].astype(str).str.strip(),
            #                 mismatched["Kode Material"].astype(str).str.strip(),
            #                 mismatched["Sheet Asal"].astype(str).str.strip(),
            #             )
            #         )
            #         curr_keys = zip(
            #             df_terfilter["NO"].astype(str).str.strip(),
            #             df_terfilter["Kode Material"].astype(str).str.strip(),
            #             df_terfilter["Sheet Asal"].astype(str).str.strip(),
            #         )
            #         mask = [k in mismatch_keys for k in curr_keys]
            #         df_terfilter = df_terfilter[mask]

            st.subheader(
                f"Dataset Bill Of Materials ({len(df_terfilter):,})"
            )
            st.dataframe(df_terfilter)

            st.download_button(
                "Download CSV",
                data=df_terfilter.to_csv(index=False).encode("utf-8"),
                file_name=f"tindak_lanjut_{selected_trainset.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )

        with st.expander(
            "Total PR SAP Dataset"
        ):
            with st.container(border=True):
                try:
                    sap_full = render_sap_data()
                    filtered_sap_data = sap_full[
                        sap_full["WBS_Group"] == "S-22002.1"
                    ]
                except Exception as exc:
                    st.error(f"Gagal memuat data SAP: {exc}")
                    filtered_sap_data = pd.DataFrame()
                if filtered_sap_data.empty:
                    st.info("Tidak ada data SAP untuk ditampilkan.")
                else:
                    st.dataframe(filtered_sap_data, width="stretch")

        # with st.container(border=True):
        #     if filtered_sap_data.empty:
        #         st.info("Data SAP tidak tersedia, merger dataset tidak bisa ditampilkan.")
        #     else:
        #         sap_cols_already = any(
        #             c in df_terfilter.columns for c in ("Qty Requested", "Status SAP")
        #         )

        #         if sap_cols_already:
        #             merge_data = df_terfilter.copy()
        #             if "Kebutuhan" in merge_data.columns and "Qty Requested" in merge_data.columns:
        #                 pass  # sudah ada berdampingan
        #         else:
        #             bom_for_merge = df_terfilter.copy()
        #             if "WBS Elem" not in bom_for_merge.columns:
        #                 bom_for_merge["WBS Elem"] = "S-22002.1"

        #             sap_for_merge = filtered_sap_data.copy()
        #             rename_map = {
        #                 "Qty Requested": "Qty Requested",
        #                 "Ordered": "Ordered",
        #                 "PR Status": "PR Status (SAP)",
        #             }
        #             sap_for_merge = sap_for_merge.rename(
        #                 columns={k: v for k, v in rename_map.items() if k in sap_for_merge.columns}
        #             )
        #             drop_cols = [c for c in ("WBS Description", "Mat. Description", "Spesifikasi")
        #                          if c in sap_for_merge.columns and c in bom_for_merge.columns]
        #             sap_for_merge = sap_for_merge.drop(columns=drop_cols, errors="ignore")

        #             merge_data = pd.merge(
        #                 bom_for_merge,
        #                 sap_for_merge,
        #                 left_on=["Kode Material", "WBS Elem"],
        #                 right_on=["Kode Material", "WBS_Group"],
        #                 how="left",
        #             )
        #             # Hapus kunci SAP yang duplikat setelah merge.
        #             if "WBS_Group" in merge_data.columns:
        #                 merge_data = merge_data.drop(columns=["WBS_Group"])

        #         st.subheader(
        #             f"Merger Dataset ({len(merge_data):,})"
        #         )
        #         st.dataframe(merge_data, width="stretch")

def render():
    st.title("Statistics Dashboard")

    render_sidebar()
    render_dashboard()
