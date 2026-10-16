import io
import re
import time

import gspread
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

import bom_rekap
from csv_utils import grid_to_dataframe, read_csv_text, sniff_delimiter
from excel_convert import file_stem, file_suffix, is_excel_file, read_excel_sheets
from bom_rekap import KATA_ABAIKAN

# ============================================================
# CONFIG
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_SPREADSHEET = "etl_bom_sap"

TAB_BOM_REKAP = "TOTAL BOM"
TAB_BOM_CLEAN = "BOM_CLEANED"
TAB_SAP_CLEAN = "SAP_CLEANED"
TAB_MERGE = "MERGE_BOM_SAP"

BOM_REQUIRED_COLUMNS = [
    "Nama Sheet Sumber",
    "WBS Elem",
    "Kode Material",
    "Kode Material Delete",
    "Stock Total",
    "Kode Material Stock",
    "Material / Komponen",
    "Spesifikasi",
    "Qty / TS (Series)",
    "UoM/Car",
    "Qty Total (Series) + Allowance",
    "QTY PR TOTAL",
]

BOM_NUMERIC_COLUMNS = [
    "Stock Total",
    "Qty / TS (Series)",
    "Qty Total (Series) + Allowance",
    "QTY PR TOTAL",
]

BOM_TEXT_COLUMNS = [
    "Nama Sheet Sumber",
    "WBS Elem",
    "Material / Komponen",
    "Spesifikasi",
    "UoM/Car",
]

SAP_REQUIRED_COLUMNS = [
    "Kode Material",
    "WBS Elem",
    "WBS Description",
    "Mat. Description",
    "Spesifikasi",
    "PR Status",
    "Qty Requested",
    "Ordered",
]

SAP_NUMERIC_COLUMNS = ["Qty Requested", "Ordered"]

SAP_DATE_COLUMNS = ["Req.Date", "PR Deliv. Dat", "Chngd on"]

SAP_TEXT_COLUMNS = [
    "WBS Elem",
    "WBS Description",
    "Mat. Description",
    "Spesifikasi",
    "PR Status",
]

SAP_DATE_FORMAT = "%d.%m.%Y"

# Format yang diterima file_uploader (Excel dibaca langsung jadi grid).
UPLOADER_TYPES = ["csv", "xlsx", "xlsm", "xls", "xlsb"]

WBS_GROUP_PATTERN = r"^([A-Z]-\d+\.\d+)"

THOUSANDS_ONLY = re.compile(r"^-?\d{1,3}(?:\.\d{3})+$")

# Key yang tidak ikut dihapus saat tombol Reset ETL ditekan.
RESET_EXCLUDE_KEYS = {"etl_spreadsheet_name", "etl_share_email"}


# ============================================================
# GOOGLE SHEETS
# ============================================================

@st.cache_resource
def connect_google_sheets():
    if "gcp_service_account" not in st.secrets:
        raise FileNotFoundError(
            "Secret 'gcp_service_account' tidak ditemukan di "
            ".streamlit/secrets.toml."
        )

    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


@st.cache_resource
def open_spreadsheet(spreadsheet_name: str, share_email: str = ""):
    """Buka spreadsheet; bila belum ada, buat baru (opsional dibagikan ke email)."""
    client = connect_google_sheets()

    try:
        return client.open(spreadsheet_name)
    except gspread.SpreadsheetNotFound:
        try:
            spreadsheet = client.create(spreadsheet_name)
        except Exception as exc:
            raise RuntimeError(
                f"Spreadsheet '{spreadsheet_name}' belum ada dan gagal dibuat "
                f"otomatis ({exc}). Umumnya karena kuota Google Drive service "
                f"account penuh. Solusi: buat spreadsheet baru di Google Drive "
                f"ANDA SENDIRI, bagikan ke email service account (lihat sidebar), "
                f"lalu isi nama spreadsheet itu di sidebar."
            ) from exc

        if share_email:
            try:
                spreadsheet.share(share_email, perm_type="user", role="writer")
            except Exception as exc:
                st.warning(
                    f"Spreadsheet baru dibuat, tetapi gagal membagikan akses "
                    f"ke '{share_email}': {exc}"
                )

        return spreadsheet


def service_account_email() -> str:
    try:
        return str(st.secrets["gcp_service_account"].get("client_email", ""))
    except Exception:
        return ""


def _json_safe(value):
    """Ubah isi sel menjadi tipe yang aman untuk Google Sheets API."""
    if isinstance(value, str):
        return value

    if value is None:
        return ""

    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass

    if isinstance(value, bool):
        return value

    # numpy scalar (int64, float64, ...) -> tipe Python asli
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass

    if isinstance(value, (int, float)):
        return value

    return str(value)


def prepare_for_sheets(df: pd.DataFrame) -> pd.DataFrame:
    """Ubah nilai yang tidak bisa dikirim ke Sheets API (NaN, NaT, Timestamp)."""
    out = df.copy()

    for col in out.columns:
        series = out[col]

        if pd.api.types.is_datetime64_any_dtype(series):
            out[col] = series.dt.strftime(SAP_DATE_FORMAT)

    return out.map(_json_safe)


# Penulisan besar: Google menolak payload raksasa dengan 503 (lihat kasus
# SAP_CLEANED ~102 ribu baris). Kita kirim per blok kecil + ulangi otomatis
# bila gangguan sementara (429/5xx/timeout).
CHUNK_ROWS_PER_REQUEST = 2000
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def _retry_transient(fn, percobaan: int = 5):
    """Jalankan operasi Sheets API; ulangi bila errornya gangguan sementara.

    429/5xx/timeout diulang dengan backoff 2, 4, 8, 16 detik. Error permanen
    (mis. 403) langsung dilempar tanpa jeda agar pesannya tetap cepat muncul.
    """
    for percobaan_ke in range(1, percobaan + 1):
        try:
            return fn()
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in TRANSIENT_STATUS or percobaan_ke == percobaan:
                raise
        except requests.exceptions.RequestException:
            if percobaan_ke == percobaan:
                raise
        time.sleep(2 ** percobaan_ke)


def _update_chunked(worksheet, values) -> None:
    """Kirim `values` ke worksheet per blok baris (lihat CHUNK_ROWS_PER_REQUEST).

    Tiap blok jadi request terpisah dengan range A1 eksplisit, sehingga ukuran
    request selalu kecil berapa pun ukuran tabelnya.
    """
    if not values:
        return

    nkolom = len(values[0])
    for awal in range(0, len(values), CHUNK_ROWS_PER_REQUEST):
        potongan = values[awal:awal + CHUNK_ROWS_PER_REQUEST]
        rentang = "{}:{}".format(
            rowcol_to_a1(awal + 1, 1),
            rowcol_to_a1(awal + len(potongan), nkolom),
        )
        _retry_transient(lambda p=potongan, r=rentang: worksheet.update(p, r))


def write_recap_to_sheets(spreadsheet, df: pd.DataFrame, tab_name: str) -> int:
    """Tulis DataFrame ke tab Google Sheets (tab dibuat bila belum ada).

    Mengembalikan jumlah baris yang ditulis.
    """

    def _siapkan_worksheet():
        try:
            worksheet = spreadsheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            return spreadsheet.add_worksheet(
                title=tab_name,
                rows="8000",
                cols=str(len(df.columns)),
            )
        worksheet.clear()
        return worksheet

    worksheet = _retry_transient(_siapkan_worksheet)

    values = [df.columns.values.tolist()] + df.values.tolist()

    if worksheet.col_count < len(df.columns) or worksheet.row_count < len(values):
        _retry_transient(
            lambda: worksheet.resize(
                rows=max(worksheet.row_count, len(values)),
                cols=max(worksheet.col_count, len(df.columns)),
            )
        )

    _update_chunked(worksheet, values)

    return len(df)


def write_dataframe(spreadsheet, df: pd.DataFrame, tab_name: str) -> int:
    """Tulis DataFrame ke tab spreadsheet. Mengembalikan jumlah baris."""
    return write_recap_to_sheets(
        spreadsheet,
        prepare_for_sheets(df),
        tab_name=tab_name,
    )


# ============================================================
# CSV READER (in-memory)
# ============================================================

def read_csv_flexible(uploaded_bytes: bytes) -> pd.DataFrame:
    """Baca CSV dari memori: deteksi encoding dan pemisah kolom."""
    text = read_csv_text(uploaded_bytes)
    return pd.read_csv(io.StringIO(text), sep=sniff_delimiter(text), low_memory=False)


# ============================================================
# PARSING HELPERS
# ============================================================

def _is_missing(value) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _norm_code(value) -> str:
    """Kode material -> string seragam ("" bila kosong)."""
    if _is_missing(value):
        return ""

    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))

    return str(value).strip()


def _norm_optional_code(value):
    """Kode opsional (delete/stock): None bila kosong agar .isna() tetap bekerja."""
    if _is_missing(value):
        return None

    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))

    text = str(value).strip()
    return text or None


def _norm_text(value) -> str:
    if _is_missing(value):
        return ""

    return str(value).strip()


def parse_amount(value) -> float:
    """Angka format Indonesia ('1.234,56') maupun format biasa ('1234.56')."""
    if value is None or _is_missing(value):
        return 0.0

    if isinstance(value, bool):
        return float(value)

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    invalid = {
        "n/a", "-", "#n/a", "#value!", "#ref!", "#div/0!", "#name?", "#ref",
    }

    if text.lower() in invalid:
        return 0.0

    if "," in text:
        # Format Indonesia: '.' pemisah ribuan, ',' pemisah desimal.
        text = text.replace(".", "").replace(",", ".")
    elif THOUSANDS_ONLY.match(text):
        # '1.234' -> 1234 (pemisah ribuan), bukan 1.234.
        text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else 0.0


def parse_amount_series(series: pd.Series) -> pd.Series:
    return series.map(parse_amount)


def parse_date_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format=SAP_DATE_FORMAT, errors="coerce")

    if parsed.notna().sum() == 0 and series.notna().any():
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)

    return parsed


def missing_columns(df: pd.DataFrame, required) -> list:
    return [col for col in required if col not in df.columns]


def _normalize_col_name(name) -> str:
    """Rapikan nama kolom: spasi biasa/NBSP, spasi ganda, spasi ujung."""
    text = str(name).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_columns(df: pd.DataFrame, required=()) -> pd.DataFrame:
    """Salinan df dengan nama kolom dirapikan, plus alias kolom wajib
    yang tidak peka huruf besar/kecil.

    Contoh kasus nyata: header CSV SAP 'Ordered ' (spasi ekor) atau
    'ordered' dianggap kolom wajib 'Ordered' setelah normalisasi ini.
    """
    out = df.copy()
    out.columns = [_normalize_col_name(col) for col in out.columns]

    lookup = {}
    for col in out.columns:
        lookup.setdefault(str(col).casefold(), col)

    rename = {}
    for col in required:
        if col in out.columns:
            continue
        actual = lookup.get(str(col).casefold())
        if actual is None or actual in rename:
            continue
        rename[actual] = col

    if rename:
        out = out.rename(columns=rename)

    return out


def _column_hint(df: pd.DataFrame) -> str:
    """Petunjuk nama kolom terbaca (repr, agar spasi tersembunyi terlihat)."""
    cols = [repr(col) for col in df.columns]
    shown = ", ".join(cols[:40])

    if len(cols) > 40:
        shown += f", ... (+{len(cols) - 40} kolom lain)"

    return f" Kolom yang terbaca ({len(cols)}): {shown}."


# ============================================================
# CLEANING
# ============================================================

def clean_bom(raw: pd.DataFrame):
    """Rekap & cleaning data Bill Of Material. Mengembalikan (df, stats)."""
    df = normalize_columns(raw, BOM_REQUIRED_COLUMNS)
    missing = missing_columns(df, BOM_REQUIRED_COLUMNS)

    if missing:
        raise ValueError(
            "File BOM tidak memiliki kolom wajib: " + ", ".join(missing) + "."
            + _column_hint(raw)
        )

    rows_in = len(df)
    duplicates = int(df.duplicated().sum())
    df = df.drop_duplicates()
    df = df.dropna(how="all")
    blank_rows = rows_in - duplicates - len(df)

    for col in ("Kode Material Delete", "Kode Material Stock"):
        df[col] = df[col].map(_norm_optional_code)

    df["Kode Material"] = df["Kode Material"].map(_norm_code)

    for col in BOM_TEXT_COLUMNS:
        df[col] = df[col].map(_norm_text)

    for col in BOM_NUMERIC_COLUMNS:
        df[col] = parse_amount_series(df[col])

    kode = df["Kode Material"]
    valid = kode.ne("") & ~kode.str.lower().isin(KATA_ABAIKAN)
    invalid_rows = int((~valid).sum())
    df = df[valid].reset_index(drop=True)

    stats = {
        "rows_in": rows_in,
        "rows_out": len(df),
        "duplicates": duplicates,
        "blank_rows": blank_rows,
        "invalid_rows": invalid_rows,
    }

    return df, stats


def clean_sap(raw: pd.DataFrame):
    """Rekap & cleaning data SAP. Mengembalikan (df, stats)."""
    df = normalize_columns(raw, SAP_REQUIRED_COLUMNS)
    missing = missing_columns(df, SAP_REQUIRED_COLUMNS)

    if missing:
        raise ValueError(
            "File SAP tidak memiliki kolom wajib: " + ", ".join(missing) + "."
            + _column_hint(raw)
        )

    rows_in = len(df)
    duplicates = int(df.duplicated().sum())
    df = df.drop_duplicates()
    df = df.dropna(how="all")
    blank_rows = rows_in - duplicates - len(df)

    df["Kode Material"] = df["Kode Material"].map(_norm_code)

    for col in SAP_TEXT_COLUMNS:
        df[col] = df[col].map(_norm_text)

    for col in SAP_DATE_COLUMNS:
        if col in df.columns:
            df[col] = parse_date_series(df[col])

    for col in SAP_NUMERIC_COLUMNS:
        df[col] = parse_amount_series(df[col]).astype(int)

    df["WBS_Group"] = df["WBS Elem"].str.extract(WBS_GROUP_PATTERN, expand=False)

    kode = df["Kode Material"]
    valid = kode.ne("") & ~kode.str.lower().isin(KATA_ABAIKAN)
    invalid_rows = int((~valid).sum())
    df = df[valid].reset_index(drop=True)

    stats = {
        "rows_in": rows_in,
        "rows_out": len(df),
        "duplicates": duplicates,
        "blank_rows": blank_rows,
        "invalid_rows": invalid_rows,
        "wbs_missing": int(df["WBS_Group"].isna().sum()),
    }

    return df, stats


# ============================================================
# MERGE (agregasi + inner join)
# ============================================================

def aggregate_bom(bom: pd.DataFrame) -> pd.DataFrame:
    filtered = bom[bom["Kode Material Delete"].isna()]

    return filtered.groupby(
        ["Kode Material", "Nama Sheet Sumber"],
        as_index=False,
    ).agg({
        "WBS Elem": "first",
        "Kode Material Delete": "first",
        "Kode Material Stock": "first",
        "Stock Total": "sum",
        "Material / Komponen": "first",
        "Spesifikasi": "first",
        "Qty / TS (Series)": "sum",
        "UoM/Car": "first",
        "Qty Total (Series) + Allowance": "sum",
        "QTY PR TOTAL": "sum",
    })


def aggregate_sap(sap: pd.DataFrame) -> pd.DataFrame:
    return sap.groupby(
        ["Kode Material", "WBS_Group"],
        as_index=False,
    ).agg({
        "WBS Description": "first",
        "Mat. Description": "first",
        "Spesifikasi": "first",
        "PR Status": "first",
        "Qty Requested": "sum",
        "Ordered": "sum",
    })


def merge_aggregates(bom_agg: pd.DataFrame, sap_agg: pd.DataFrame) -> pd.DataFrame:
    return pd.merge(
        bom_agg,
        sap_agg,
        left_on=["Kode Material", "WBS Elem"],
        right_on=["Kode Material", "WBS_Group"],
        how="inner",
    )


def build_merge(bom: pd.DataFrame, sap: pd.DataFrame) -> pd.DataFrame:
    return merge_aggregates(aggregate_bom(bom), aggregate_sap(sap))


# ============================================================
# SESSION STATE / ETL STEPS
# ============================================================

# Konfigurasi pemulihan hasil per tab: kolom yang tipenya perlu
# dikembalikan setelah dibaca ulang dari Sheets (angka -> numerik,
# tanggal -> datetime; '' pada kolom kode opsional -> None).
RESTORE_CONFIG = {
    TAB_BOM_REKAP: {},
    TAB_BOM_CLEAN: {
        "numeric": BOM_NUMERIC_COLUMNS,
        "optional_codes": ("Kode Material Delete", "Kode Material Stock"),
    },
    TAB_SAP_CLEAN: {
        "numeric": SAP_NUMERIC_COLUMNS,
        "dates": SAP_DATE_COLUMNS,
    },
    TAB_MERGE: {
        "numeric": [*BOM_NUMERIC_COLUMNS, *SAP_NUMERIC_COLUMNS],
    },
}


def frame_from_sheet(
    records: list,
    numeric=(),
    dates=(),
    optional_codes=(),
) -> pd.DataFrame:
    """Ubah rekaman tab spreadsheet (get_all_records) kembali ke
    DataFrame siap pakai.

    Saat ditulis ke Sheets: NaN -> '', datetime -> 'dd.mm.yyyy', angka
    tetap angka. Saat dibaca balik, '' pada kolom kode opsional harus
    kembali jadi None (agar .isna() di agregasi tetap bekerja) dan
    kolom angka/tanggal dinilai ulang sesuai tipe aslinya.
    """
    df = pd.DataFrame.from_records(records)

    for col in dates:
        if col in df.columns:
            df[col] = parse_date_series(df[col])

    for col in numeric:
        if col in df.columns:
            df[col] = parse_amount_series(df[col])

    for col in optional_codes:
        if col in df.columns:
            df[col] = df[col].map(_norm_optional_code)

    return df


def restore_from_spreadsheet(
    spreadsheet_name: str,
    share_email: str = "",
) -> bool:
    """Pulihkan hasil ETL dari tab di spreadsheet.

    Dipakai setelah refresh browser: sesi Streamlit baru = seluruh
    session_state kosong, padahal hasil rekap/cleaning/merge sudah
    pernah ditulis ke spreadsheet. Membaca ke-4 tab lalu mengisi ulang
    state pipeline sehingga dashboard dan tombol Generate tampil lagi
    tanpa mengunggah ulang file. Mengembalikan True bila berhasil.
    """
    frames = {}

    try:
        spreadsheet = open_spreadsheet(spreadsheet_name, share_email)
    except Exception as exc:
        st.error(f"Gagal membuka spreadsheet '{spreadsheet_name}': {exc}")
        return False

    with st.spinner("Membaca hasil dari spreadsheet..."):
        for tab_name, config in RESTORE_CONFIG.items():
            try:
                worksheet = spreadsheet.worksheet(tab_name)
            except gspread.exceptions.WorksheetNotFound:
                continue  # tab belum pernah ditulis

            try:
                records = worksheet.get_all_records(
                    value_render_option="UNFORMATTED_VALUE",
                )
            except Exception as exc:
                st.error(f"Gagal membaca tab '{tab_name}': {exc}")
                return False

            if not records:
                continue

            frames[tab_name] = frame_from_sheet(records, **config)

    if not frames:
        st.warning(
            f"Belum ada hasil ETL di spreadsheet '{spreadsheet_name}'. "
            "Jalankan pipeline dulu — hasilnya tersimpan di tab "
            f"'{TAB_BOM_REKAP}', '{TAB_BOM_CLEAN}', '{TAB_SAP_CLEAN}', "
            f"dan '{TAB_MERGE}'."
        )
        return False

    # Dashboard membaca kedua frame bersih, jadi merge hanya dipulihkan
    # bila keduanya ikut ada.
    bom_clean = frames.get(TAB_BOM_CLEAN)
    sap_clean = frames.get(TAB_SAP_CLEAN)
    merge = (
        frames.get(TAB_MERGE)
        if bom_clean is not None and sap_clean is not None
        else None
    )

    st.session_state.etl_bom_rekap = frames.get(TAB_BOM_REKAP)
    st.session_state.etl_bom_clean = bom_clean
    st.session_state.etl_sap_clean = sap_clean
    st.session_state.etl_merge = merge

    ringkasan = ", ".join(
        f"{tab} {len(df):,} baris" for tab, df in frames.items()
    )
    st.success(f"Hasil ETL dipulihkan dari spreadsheet: {ringkasan}.")

    if merge is None and TAB_MERGE in frames:
        st.caption(
            "Tab merge ada, tetapi salah satu data bersih tidak lengkap — "
            "tombol Generate menunggu kedua dataset."
        )

    return True


def reset_etl():
    for key in [
        k
        for k in list(st.session_state)
        if k.startswith("etl_") and k not in RESET_EXCLUDE_KEYS
    ]:
        del st.session_state[key]

    st.rerun()


def prepare_upload_state(state, prefix: str, uploaded) -> None:
    """Sinkronkan state upload: invalidasi hasil ETL ketika file upload
    berubah atau dihapus, dan baca file siap-proses tepat saat diunggah
    (in-memory) — sehingga sebelum user menekan tombol rekap/cleaning,
    datanya sudah siap.

    - CSV  -> satu sumber berisi bytes asli.
    - Excel-> SEMUA sheet dibaca langsung menjadi grid sel (tanpa konversi
      CSV), satu sumber per sheet, label = nama sheet.

    state: mapping mirip session_state (dict biasa saat unit test).
    Kunci yang ditulis:
      etl_{prefix}_file_sig      -- (nama, ukuran) file aktif
      etl_{prefix}_sources       -- daftar {label, bytes} | {label, grid}
      etl_{prefix}_src_kind      -- 'csv' | 'xlsx' | ... (ekstensi tanpa titik)
      etl_{prefix}_converted     -- info Excel (daftar sheet, jumlah baris)
      etl_{prefix}_convert_error -- pesan gagal pembacaan
    """
    sig_key = f"etl_{prefix}_file_sig"
    sources_key = f"etl_{prefix}_sources"
    kind_key = f"etl_{prefix}_src_kind"
    conv_key = f"etl_{prefix}_converted"
    err_key = f"etl_{prefix}_convert_error"

    def clear_all():
        for key in (
            sig_key,
            sources_key,
            kind_key,
            conv_key,
            err_key,
            f"etl_{prefix}_clean",
            f"etl_{prefix}_stats",
            f"etl_{prefix}_rekap",
            f"etl_{prefix}_rekap_report",
            "etl_merge",
        ):
            state.pop(key, None)

    if uploaded is None:
        if state.get(sig_key) is not None:
            clear_all()
        return

    signature = (uploaded.name, uploaded.size)

    if state.get(sig_key) == signature:
        # File sama -> jangan dibaca ulang tiap rerun.
        return

    # File baru -> hasil lama tidak berlaku lagi.
    clear_all()
    state[sig_key] = signature

    try:
        if is_excel_file(uploaded.name):
            sheets = read_excel_sheets(
                uploaded.getvalue(),
                uploaded.name,
                date_format=SAP_DATE_FORMAT,
            )
            state[sources_key] = [
                {"label": sheet["label"], "grid": sheet["grid"]}
                for sheet in sheets
            ]
            state[kind_key] = file_suffix(uploaded.name)
            state[conv_key] = {
                "sheets": [sheet["label"] for sheet in sheets],
                "rows": {
                    sheet["label"]: sheet["rows"] for sheet in sheets
                },
                "total_rows": sum(sheet["rows"] for sheet in sheets),
            }
        else:
            state[kind_key] = file_suffix(uploaded.name) or "csv"
            state[sources_key] = [
                {
                    "label": file_stem(uploaded.name),
                    "bytes": uploaded.getvalue(),
                }
            ]
    except Exception as exc:
        state[err_key] = str(exc)


def sync_upload(prefix: str, uploaded) -> None:
    """Invalidasi hasil ETL saat file berubah; baca file Excel langsung
    menjadi grid per sheet in-memory (lihat prepare_upload_state)."""
    prepare_upload_state(st.session_state, prefix, uploaded)


def render_upload_status(prefix: str) -> bool:
    """Tampilkan status file upload (error pembacaan / Excel terbaca).
    Mengembalikan True bila file siap diproses."""
    error = st.session_state.get(f"etl_{prefix}_convert_error")
    if error:
        st.error(f"File tidak bisa diproses: {error}")
        return False

    if not st.session_state.get(f"etl_{prefix}_sources"):
        return False

    if st.session_state.get(f"etl_{prefix}_src_kind") != "csv":
        info = st.session_state.get(f"etl_{prefix}_converted", {})
        sheets = info.get("sheets", [])
        shown = ", ".join(sheets[:8])
        if len(sheets) > 8:
            shown += f", … (+{len(sheets) - 8} lainnya)"
        st.info(
            f"Detected sheets: **{len(sheets)} sheet** "
            f"({shown})"
        )

    return True


def write_step_result(tab_name: str, df: pd.DataFrame, spreadsheet_name: str, share_email: str) -> bool:
    with st.spinner(f"Menulis {len(df):,} baris ke tab '{tab_name}'..."):
        try:
            spreadsheet = open_spreadsheet(spreadsheet_name, share_email)
            write_dataframe(spreadsheet, df, tab_name)
        except Exception as exc:
            st.error(
                f"Data berhasil dibersihkan, tetapi gagal ditulis ke "
                f"'{spreadsheet_name}'!'{tab_name}': {exc}"
            )
            return False

    st.success(
        f"{len(df):,} baris ditulis ke tab '{tab_name}' "
        f"(spreadsheet '{spreadsheet_name}')."
    )
    return True


def render_stats(stats: dict):
    cols = st.columns(4)
    cols[0].metric("Baris mentah", f"{stats['rows_in']:,}")
    cols[1].metric("Baris bersih", f"{stats['rows_out']:,}")
    cols[2].metric("Duplikat dibuang", f"{stats['duplicates']:,}")
    cols[3].metric("Baris tidak valid", f"{stats.get('invalid_rows', 0):,}")


def render_rekap_report(reports: list):
    """Ringkasan tahap rekap (deteksi header + ekstraksi).

    `reports` = daftar report per sumber (per sheet Excel / per file CSV).
    Satu sumber -> tampilan rinci; banyak sumber -> metrik gabungan + tabel
    ringkas per sheet.
    """
    if not reports:
        return

    if len(reports) == 1:
        _render_single_report(reports[0])
        return

    total_extracted = sum(r["rows_extracted"] for r in reports)
    total_skipped = sum(
        r["rows_skipped_empty"] + r["rows_skipped_ignored"] for r in reports
    )
    detected = [r["detected_fields"] for r in reports]
    total_fields = reports[0]["total_fields"]

    cols = st.columns(4)
    cols[0].metric("Sheet diproses", f"{len(reports):,}")
    cols[1].metric("Baris direkap", f"{total_extracted:,}")
    cols[2].metric("Baris dilewati", f"{total_skipped:,}")
    cols[3].metric("Field terdeteksi", f"{min(detected)}–{max(detected)}/{total_fields}")

    rows = []
    for r in reports:
        if r["wbs_from_column"]:
            wbs = "per baris (kolom)"
        else:
            wbs = r["wbs"] or "TIDAK DITEMUKAN"
        rows.append(
            {
                "Sheet": r["source_label"],
                "Header": (
                    f"Row {r['header_start_row']}–{r['header_end_row']} "
                    f"({r['header_row_count']} baris)"
                ),
                "Field": f"{r['detected_fields']}/{r['total_fields']}",
                "Direkap": r["rows_extracted"],
                "Dilewati": (
                    r["rows_skipped_empty"] + r["rows_skipped_ignored"]
                ),
                "Kode WBS": wbs,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    no_wbs = [
        r["source_label"]
        for r in reports
        if not r["wbs_from_column"] and not r["wbs"]
    ]
    if no_wbs:
        st.warning(
            "Kode WBS tidak ditemukan pada sheet: " + ", ".join(no_wbs)
        )

    missing = sorted({f for r in reports for f in r["missing_fields"]})
    if missing:
        st.info(
            "Field yang tidak terdeteksi di sebagian sheet "
            "(kolom opsional, boleh tidak ada): " + ", ".join(missing)
        )

    with st.expander("🔎 Detail mapping header per sheet"):
        detail = [
            {"Sheet": r["source_label"], **item}
            for r in reports
            for item in r["fields"]
        ]
        st.dataframe(pd.DataFrame(detail), width="stretch", hide_index=True)


def _render_single_report(report: dict):
    """Tampilan rinci report rekap untuk satu sumber (satu file)."""
    cols = st.columns(4)
    cols[0].metric("Baris direkap", f"{report['rows_extracted']:,}")
    cols[1].metric("Baris header", report["header_row_count"])
    cols[2].metric(
        "Field terdeteksi",
        f"{report['detected_fields']}/{report['total_fields']}",
    )
    cols[3].metric(
        "Baris dilewati",
        f"{report['rows_skipped_empty'] + report['rows_skipped_ignored']:,}",
    )

    if report["source_from_column"]:
        sumber = "kolom 'Nama Sheet Sumber' di CSV"
    else:
        sumber = f"nama sumber ('{report['source_label']}')"
    if report["wbs_from_column"]:
        wbs = "diambil per baris dari kolom CSV"
    else:
        wbs = report["wbs"] or "TIDAK DITEMUKAN"

    st.caption(
        f"Header: Row {report['header_start_row']}–{report['header_end_row']} "
        f"({report['header_row_count']} baris)  |  Kode WBS: {wbs}  |  "
        f"Nama sheet sumber: {sumber}."
    )

    if not report["wbs_from_column"] and not report["wbs"]:
        st.warning(
            "Kode WBS tidak ditemukan (baris metadata Row 2 maupun kolom CSV)."
        )

    if report["missing_fields"]:
        st.info(
            "Field yang tidak terdeteksi (kolom opsional, boleh tidak ada): "
            + ", ".join(report["missing_fields"])
        )

    with st.expander("🔎 Detail mapping header rekap"):
        st.dataframe(
            pd.DataFrame(report["fields"]),
            width="stretch",
            hide_index=True,
        )


def rekap_sumber_bom(sources: list):
    """Rekap tiap sumber BOM (algoritma deteksi header fuzzy multi-baris).

    Sumber Excel (dict {'label', 'grid'}) hanya direkap bila nama sheet
    cocok dengan daftar 29 target algoritma asli
    (bom_rekap.DAFTAR_SHEET_TARGET); sheet agregat berkolom
    'Nama Sheet Sumber' dijaga bom_rekap.mendeteksi_output_rekap.
    Nama sheet Excel yang terpotong (batas 31 karakter) dinormalkan ke
    nama target penuh, supaya label 'Nama Sheet Sumber' cocok dengan
    hasil algoritma asli di Colab. Sumber CSV (dict {'label', 'bytes'})
    direkap tanpa filter nama.

    Mengembalikan (dfs, reports, dilewati, target_terpakai) — murni,
    tanpa Streamlit/network, supaya mudah diuji.
    """
    dfs, reports, dilewati = [], [], []
    target_terpakai = set()

    for src in sources:
        label = src.get("label") or "CSV"
        try:
            if "grid" in src:
                # Sheet Excel yang memuat kolom 'Nama Sheet Sumber' adalah
                # data agregat hasil rekap (bukan BOM mentah) -> jangan
                # direkap ulang agar baris sumber tidak terhitung ganda.
                if bom_rekap.mendeteksi_output_rekap(src["grid"]):
                    dilewati.append(
                        f"{label} (sheet berisi kolom 'Nama Sheet Sumber' — "
                        "data agregat hasil rekap, dilewati agar baris dari "
                        "sheet sumber tidak terhitung ganda)"
                    )
                    continue

                # Hanya 29 sheet target algoritma asli (Colab) yang direkap.
                target = bom_rekap.target_sheet(label)
                if target is None:
                    dilewati.append(f"{label} (di luar daftar 29 sheet target)")
                    continue

                df_src, report = bom_rekap.rekap_grid(src["grid"], target)
                target_terpakai.add(target)
            else:
                df_src, report = bom_rekap.rekap_from_bytes(src["bytes"], label)
        except Exception as exc:
            dilewati.append(f"{label} ({exc})")
            continue

        if df_src.empty:
            dilewati.append(f"{label} (tidak menghasilkan baris)")
            continue

        dfs.append(df_src)
        reports.append(report)

    return dfs, reports, dilewati, target_terpakai


def run_bom_etl(
    sources: list,
    spreadsheet_name: str,
    share_email: str,
):
    """Rekap + cleaning BOM dari daftar sumber siap-proses.

    Sumber = dict {'label', 'bytes'} (file CSV) atau {'label', 'grid'}
    (satu sheet Excel — file Excel dibaca langsung per sheet di tahap
    upload, lihat prepare_upload_state). Tiap sumber di-rekap sendiri
    lalu digabung, dengan 'Nama Sheet Sumber' = label (nama sheet /
    nama file) — persis skrip asli yang mengiterasi sheet bom_dataset.
    Sheet Excel hanya direkap bila namanya cocok dengan daftar 29 sheet
    target algoritma asli (bom_rekap.DAFTAR_SHEET_TARGET); sisanya
    dilewati dan dilaporkan. Sheet yang header-nya tidak terdeteksi
    juga dilewati; sheet berkolom 'Nama Sheet Sumber' (data agregat
    hasil rekap, mis. tab 'TOTAL BOM' di bom_dataset) dijaga
    bom_rekap.mendeteksi_output_rekap. Lihat rekap_sumber_bom.
    """
    if not sources:
        st.error("Tidak ada file BOM siap diproses.")
        return

    # Hasil lama tidak berlaku lagi setelah file / hasil rekap berubah.
    for key in (
        "etl_merge",
        "etl_bom_clean",
        "etl_bom_stats",
        "etl_bom_rekap",
        "etl_bom_rekap_report",
    ):
        st.session_state.pop(key, None)

    # ------------------------------------------------------------
    # TAHAP 1 — REKAP (algoritma deteksi header fuzzy multi-baris)
    # ------------------------------------------------------------
    dfs, reports, dilewati, target_terpakai = rekap_sumber_bom(sources)

    if not reports:
        st.error("Rekap BOM gagal: " + "; ".join(dilewati))
        return

    if dilewati:
        st.warning(
            "Sumber/sheet dilewati:\n"
            + "\n".join(f"- {ditem}" for ditem in dilewati)
        )

    # Ringkasan cakupan 29 sheet target (khusus unggahan Excel) —
    # pembanding langsung untuk audit dengan Google Colab.
    if any("grid" in s for s in sources):
        kurang = [
            t for t in bom_rekap.DAFTAR_SHEET_TARGET if t not in target_terpakai
        ]
        st.info(
            f"Target terpenuhi: "
            f"{len(bom_rekap.DAFTAR_SHEET_TARGET) - len(kurang)}"
            f"/{len(bom_rekap.DAFTAR_SHEET_TARGET)} sheet target"
            + (f" — belum ada di file: {', '.join(kurang)}" if kurang else "")
        )

    df_rekap = (
        pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
    )
    st.session_state.etl_bom_rekap = df_rekap
    st.session_state.etl_bom_rekap_report = reports

    render_rekap_report(reports)

    if df_rekap.empty:
        st.error("Rekap tidak menghasilkan satu baris pun. Periksa isi file CSV.")
        return

    write_step_result(TAB_BOM_REKAP, df_rekap, spreadsheet_name, share_email)

    # ------------------------------------------------------------
    # TAHAP 2 — CLEANING (logika clean_bom yang sudah ada)
    # ------------------------------------------------------------
    try:
        clean, stats = clean_bom(df_rekap)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state.etl_bom_clean = clean
    st.session_state.etl_bom_stats = stats

    render_stats(stats)
    write_step_result(TAB_BOM_CLEAN, clean, spreadsheet_name, share_email)

    with st.expander("👁️ Preview hasil rekap (sebelum cleaning)"):
        st.dataframe(df_rekap.head(50), width="stretch")

    with st.expander("👁️ Preview data BOM bersih"):
        st.dataframe(clean.head(50), width="stretch")


def run_sap_etl(
    sources: list,
    spreadsheet_name: str,
    share_email: str,
):
    """Cleaning SAP dari daftar sumber siap-proses.

    CSV -> bytes (baca fleksibel encoding/pecah); Excel -> grid per sheet
    (dibaca langsung di tahap upload). Bila ada lebih dari satu sumber,
    seluruh baris digabung sebelum cleaning.
    """
    if not sources:
        st.error("Tidak ada sumber data SAP.")
        return

    frames = []
    for src in sources:
        label = src.get("label") or "SAP"
        try:
            if "grid" in src:
                frames.append(grid_to_dataframe(src["grid"]))
            else:
                frames.append(read_csv_flexible(src["bytes"]))
        except Exception as exc:
            st.error(f"Gagal membaca data SAP '{label}': {exc}")
            return

    raw = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    try:
        clean, stats = clean_sap(raw)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state.etl_sap_clean = clean
    st.session_state.etl_sap_stats = stats
    st.session_state.pop("etl_merge", None)

    render_stats(stats)

    if stats.get("wbs_missing"):
        st.caption(
            f"Baris tanpa kode WBS terdeteksi: {stats['wbs_missing']:,} "
            "(tidak akan ikut merge)."
        )

    write_step_result(TAB_SAP_CLEAN, clean, spreadsheet_name, share_email)

    with st.expander("Preview data SAP bersih"):
        st.dataframe(clean.head(50), width="stretch")


def run_generate(spreadsheet_name: str, share_email: str):
    bom = st.session_state.etl_bom_clean
    sap = st.session_state.etl_sap_clean

    with st.spinner("Menggabungkan data BOM dan SAP..."):
        merge = build_merge(bom, sap)

    st.session_state.etl_merge = merge

    if merge.empty:
        st.warning(
            "Merge menghasilkan 0 baris — tidak ada 'Kode Material' + WBS "
            "yang cocok antara data BOM dan SAP."
        )

    write_step_result(TAB_MERGE, merge, spreadsheet_name, share_email)

    try:
        spreadsheet = open_spreadsheet(spreadsheet_name, share_email)
        st.caption(f"Link spreadsheet: {spreadsheet.url}")
    except Exception:
        pass

    with st.expander("👁️ Preview hasil merge"):
        st.dataframe(merge.head(50), width="stretch")


# ============================================================
# SIDEBAR
# ============================================================

def render_sidebar():
    with st.sidebar:
        st.header("Google Sheets")

        st.text_input(
            "Nama Spreadsheet",
            value=DEFAULT_SPREADSHEET,
            key="etl_spreadsheet_name",
            help=(
                "Bila spreadsheet belum ada, dibuat otomatis lewat API. "
                "Bila pembuatan otomatis ditolak (kuota Drive service "
                "account penuh), buat spreadsheet manual di Google Drive "
                "ANDA lalu bagikan ke email service account di bawah."
            ),
        )

        st.text_input(
            "Email penerima akses (opsional)",
            key="etl_share_email",
            placeholder="nama@perusahaan.co.id",
            help=(
                "Dipakai saat spreadsheet baru dibuat, supaya Anda tetap "
                "bisa membukanya (spreadsheet dibuat oleh service account)."
            ),
        )

        email = service_account_email()
        if email:
            with st.expander("Email service account"):
                st.code(email, language=None)

        if st.button(
            "Muat hasil dari spreadsheet",
            width="stretch",
            key="btn_restore",
        ):
            restore_from_spreadsheet(
                st.session_state.etl_spreadsheet_name.strip(),
                st.session_state.etl_share_email.strip(),
            )

        st.divider()

        if st.button("Reset ETL", width="stretch", key="btn_reset_etl"):
            reset_etl()

def render_pipeline():
    spreadsheet_name = st.session_state.etl_spreadsheet_name.strip()
    share_email = st.session_state.etl_share_email.strip()

    with st.container(border=True):
        st.markdown("### Upload Bill Of Material")

        bom_file = st.file_uploader(
            "File Excel Bill Of Material",
            type=UPLOADER_TYPES,
            key="bom_csv_file",
            help=(
                "Excel (.xlsx/.xlsm/.xls/.xlsb). File hanya dibaca "
                "ke memori, tidak disimpan ke sistem; file Excel dibaca "
                "langsung, seluruh sheet diproses."
            ),
        )
        sync_upload("bom", bom_file)
        bom_file_ready = render_upload_status("bom")

        col_bom, col_state = st.columns([1, 2])

        with col_bom:
            bom_clicked = st.button(
                "Rekap & Cleaning",
                type="primary",
                key="btn_clean_bom",
                disabled=not bom_file_ready,
                width="stretch",
            )

        with col_state:
            if bom_clicked:
                st.caption("Sedang diproses...")
            elif st.session_state.get("etl_bom_clean") is not None:
                rekap_note = ""
                if st.session_state.get("etl_bom_rekap") is not None:
                    rekap_note = (
                        f" (dari {len(st.session_state.etl_bom_rekap):,} baris rekap)"
                    )
                st.success(
                    f"BOM selesai direkap & dibersihkan: "
                    f"{len(st.session_state.etl_bom_clean):,} baris bersih"
                    f"{rekap_note}."
                )
            elif bom_file_ready:
                st.info("File siap. Tekan tombol untuk memproses.")
            else:
                st.caption("Belum ada file BOM.")

        if bom_clicked and st.session_state.get("etl_bom_sources"):
            run_bom_etl(
                st.session_state.etl_bom_sources,
                spreadsheet_name,
                share_email,
            )

    # ------------------------------------------------------------
    # 2. SAP
    # ------------------------------------------------------------

    with st.container(border=True):
        st.markdown("### Upload SAP")

        sap_file = st.file_uploader(
            "File Excel SAP",
            type=UPLOADER_TYPES,
            key="sap_csv_file",
            help=(
                "CSV atau Excel (.xlsx/.xlsm/.xls/.xlsb). File hanya dibaca "
                "ke memori, tidak disimpan ke sistem; file Excel dibaca "
                "langsung, seluruh sheet diproses."
            ),
        )
        sync_upload("sap", sap_file)
        sap_file_ready = render_upload_status("sap")

        col_sap, col_state = st.columns([1, 2])

        with col_sap:
            sap_clicked = st.button(
                "Rekap & Cleaning",
                type="primary",
                key="btn_clean_sap",
                disabled=not sap_file_ready,
                width="stretch",
            )

        with col_state:
            if sap_clicked:
                st.caption("Sedang diproses...")
            elif st.session_state.get("etl_sap_clean") is not None:
                st.success(
                    f"SAP selesai diproses: "
                    f"{len(st.session_state.etl_sap_clean):,} baris bersih."
                )
            elif sap_file_ready:
                st.info("File siap. Tekan tombol untuk memproses.")
            else:
                st.caption("Belum ada file SAP.")

        if sap_clicked and st.session_state.get("etl_sap_sources"):
            run_sap_etl(
                st.session_state.etl_sap_sources,
                spreadsheet_name,
                share_email,
            )

    # ------------------------------------------------------------
    # 3. GENERATE
    # ------------------------------------------------------------

    with st.container(border=True):
        st.markdown("### Generate Data")

        bom_ready = st.session_state.get("etl_bom_clean") is not None
        sap_ready = st.session_state.get("etl_sap_clean") is not None

        if not (bom_ready and sap_ready):
            pending = [
                label
                for label, ready in (
                    ("Bill Of Material", bom_ready),
                    ("SAP", sap_ready),
                )
                if not ready
            ]
            st.info(
                "Tombol **Generate Data** muncul setelah kedua dataset "
                "selesai direkap & dibersihkan. Menunggu: "
                + ", ".join(pending)
                + "."
            )
        else:
            col_bom, col_sap, col_gen = st.columns([1, 1, 1])

            with col_bom:
                st.caption(f"✅ BOM: {len(st.session_state.etl_bom_clean):,} baris")
            with col_sap:
                st.caption(f"✅ SAP: {len(st.session_state.etl_sap_clean):,} baris")
            with col_gen:
                generate_clicked = st.button(
                    "⚙️ Generate Data",
                    type="primary",
                    key="btn_generate",
                    width="stretch",
                )

            if generate_clicked:
                run_generate(spreadsheet_name, share_email)

            if st.session_state.get("etl_merge") is not None:
                st.caption(
                    f"Hasil merge terakhir: "
                    f"{len(st.session_state.etl_merge):,} baris "
                    f"di tab '{TAB_MERGE}'."
                )


# ============================================================
# DASHBOARD
# ============================================================

def render_dashboard():
    merge_full = st.session_state.get("etl_merge")

    if merge_full is None:
        st.info(
            "Dashboard tampil setelah langkah **Generate Data** dijalankan. "
            "Bila hasil sudah pernah dibuat lalu browser di-refresh, muat "
            "kembali lewat tombol **Muat hasil dari spreadsheet** di "
            "sidebar."
        )
        return

    st.subheader("Dashboard")
    st.caption(
        f"Gabungan BOM × SAP: {len(merge_full):,} baris "
        f"(tab '{TAB_MERGE}', spreadsheet "
        f"'{st.session_state.etl_spreadsheet_name}')."
    )

    bom_data = st.session_state.etl_bom_clean.copy()
    sap_data = st.session_state.etl_sap_clean.copy()

    project_options = ("Semua",) + tuple(sap_data["WBS_Group"].dropna().unique())
    col_project, col_sheet = st.columns([1, 1])
    with col_project:
        selected_project = st.selectbox("Pilih Project", project_options)
    if selected_project != "Semua":
        sap_data = sap_data[sap_data["WBS_Group"] == selected_project]
        bom_data = bom_data[bom_data["WBS Elem"] == selected_project]

    sheet_options = ("Semua",) + tuple(bom_data["Nama Sheet Sumber"].dropna().unique())
    with col_sheet:
        selected_sheet = st.selectbox("Daftar Kebutuhan Material", sheet_options)
    if selected_sheet != "Semua":
        bom_data = bom_data[bom_data["Nama Sheet Sumber"] == selected_sheet]

    filtered_bom_data = aggregate_bom(bom_data)
    filtered_sap_data = aggregate_sap(sap_data)

    merge_data = merge_aggregates(filtered_bom_data, filtered_sap_data)

    stockData = merge_data[(merge_data["Kode Material Stock"].isna() & (merge_data["Stock Total"] > 0.0)) | (merge_data["Kode Material Stock"].notna() & ((merge_data['Stock Total'] > 0.0) | (merge_data['Stock Total'] == 0.0)))]
    otherData = merge_data[merge_data['Kode Material Stock'].isna() & (merge_data['Stock Total'] == 0.0)]
    belumPRData = otherData[(otherData["QTY PR TOTAL"] > otherData["Qty Requested"])]
    sudahPRData = otherData[(otherData["QTY PR TOTAL"] == otherData["Qty Requested"])]
    partialPRData = otherData[(otherData["QTY PR TOTAL"] < otherData["Qty Requested"])]
    stockPRData = stockData[(stockData["QTY PR TOTAL"] < stockData["Qty Requested"]) & (stockData["Stock Total"] > 0.0)]


    project_data = sap_data.groupby("WBS Description").size()

    statusB = sudahPRData[sudahPRData["PR Status"] == "B"]
    statusAorK = sudahPRData[(sudahPRData["PR Status"] == "A") | (sudahPRData["PR Status"] == "K")]
    statusN = sudahPRData[sudahPRData["PR Status"] == "N"]

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)
    
        with col1:
            st.subheader("Data BOM")
            bom_data_total = len(filtered_bom_data)
            st.markdown(f"# {bom_data_total}")
        with col2:
            st.subheader("Data SAP")
            sap_data_total = len(filtered_sap_data)
            st.markdown(f"# {sap_data_total}")
        with col3:
            st.subheader("Proyek")
            sap_data_total = len(project_data)
            st.markdown(f"# {sap_data_total}")
        
        with st.container(border=True):
            col1, col2 = st.columns(2, border=True)
            col3, col4 = st.columns(2, border=True)
        
            with col1:
                data = {
                    'Status': ['Belum PR', 'Sudah PR', 'Stock', 'Partial PR', 'PR & Stock'],
                    'Jumlah': [len(belumPRData), len(sudahPRData), len(stockData), len(partialPRData), len(stockPRData)]
                }
                df = pd.DataFrame(data)
                prchart = px.pie(df, values = 'Jumlah', names = 'Status', hole=0.3)
                st.subheader("Purchase Requisition")
                st.plotly_chart(prchart)
            with col2:
                po = {
                    'Status PO': ['N', 'A/K', 'B'],
                    'Total': [len(statusN), len(statusAorK), len(statusB)],
                }
                df = pd.DataFrame(po)
                pochart = px.pie(df, values = 'Total', names = 'Status PO', hole=0.3)
                st.subheader("Purchase Order")
                st.plotly_chart(pochart, width="stretch", height=450)

        ### diagram batang
        with st.container(border=True):

            belumPRData = otherData[(otherData["QTY PR TOTAL"] > otherData["Qty Requested"])].copy()
            belumPRData["Status PR"] = "Belum PR"

            sudahPRData = otherData[(otherData["QTY PR TOTAL"] == otherData["Qty Requested"])].copy()
            sudahPRData["Status PR"] = "Sudah PR"

            partialPRData = otherData[(otherData["QTY PR TOTAL"] < otherData["Qty Requested"])].copy()
            partialPRData["Status PR"] = "Partial PR"

            stockPRData = stockData[(stockData["QTY PR TOTAL"] < stockData["Qty Requested"]) & (stockData["Stock Total"] > 0.0)].copy()
            stockPRData["Status PR"] = "Stock PR"

            # 2. Gabungkan semua data menjadi satu DataFrame
            df_all = pd.concat([belumPRData, sudahPRData, partialPRData, stockPRData], ignore_index=True)

            # 3. Hitung jumlah item berdasarkan 'Nama Sheet Sumber' (atau nama kolom sheet Anda) dan 'Status PR'
            df_grouped = df_all.groupby(["Nama Sheet Sumber", "Status PR"]).size().reset_index(name="Jumlah")

            # 4. Tampilkan grafik dengan st.bar_chart
            st.bar_chart(
                df_grouped,
                x="Nama Sheet Sumber", # Kategori di sumbu Y (karena horizontal=True)
                y="Jumlah",            # Nilai angka yang ditumpuk
                color="Status PR",     # Warna berdasarkan kelompok Status
                horizontal=True
            )
        
        with st.container(border=True):
            st.subheader(f"Bill Of Material Dataset")
            st.dataframe(bom_data)

        with st.container(border=True):
            st.subheader(f"SAP Dataset")
            st.dataframe(sap_data)

        with st.container(border=True):
            # Merger of BOM and SAP Data
            show_under_requested = st.toggle("PR Partial")
            show_over_requested = st.toggle("Belum PR")
            show_same_requested = st.toggle("Sudah PR")
            show_stock_data = st.toggle("Stock")
            show_stock_pr_data = st.toggle("Stock & PR")
            df_terfilter = merge_data.copy()
            selected = []

            if show_under_requested:
                selected.append(partialPRData)

            if show_over_requested:
                selected.append(belumPRData)

            if show_same_requested:
                selected.append(sudahPRData)

            if show_stock_data:
                selected.append(stockData)

            if show_stock_pr_data:
                selected.append(stockPRData)

            if selected:
                df_terfilter = pd.concat(selected)

            st.subheader(f"Merger of BOM and SAP Data ({len(df_terfilter)})")
            st.dataframe(df_terfilter)

        with st.container(border=True):
            st.subheader("Stock and PR Data")
            st.dataframe(stockPRData)

        with st.container(border=True):
            st.subheader("PR Partial Data")
            st.dataframe(partialPRData)


# ============================================================
# MAIN
# ============================================================

def render():
    st.title("Statistik Dashboard")

    render_sidebar()
    render_pipeline()
    render_dashboard()
