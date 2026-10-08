import json
import re
from pathlib import Path

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from recap_engine import REKAP_TAB_NAME, run_recap, write_recap_to_sheets

# ============================================================
# CONFIG
# ============================================================

SPREADSHEET_DEFAULT = "bom_dataset"
CREDENTIALS_FILE = ".streamlit/secrets.toml"
MAPPINGS_FILE = "sheet_mappings.json"

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

STANDARD_FIELDS = {
    "kode": "Kode Material",
    "kode_delete": "Kode Material Delete",
    "stock_total": "Stock Total",
    "kode_stock": "Kode Material Stock",
    "nama": "Material / Komponen",
    "spek": "Spesifikasi",
    "qty": "Qty / TS (Series)",
    "uom": "UoM/Car",
    "qty_allowance": "Qty Total (Series) + Allowance",
    "qty_pr_total": "QTY PR TOTAL",
}

# Alias hanya digunakan untuk memberi saran awal.
# Mapping final TETAP ditentukan oleh user melalui UI.
KATA_KUNCI = {
    "kode": [
        "kode material", "material code", "material kode", "item code",
        "part number", "part no", "part no.",
    ],
    "kode_delete": [
        "kode material delete", "material delete code", "delete material code",
        "kode delete",
    ],
    "stock_total": [
        "stock total", "total stock", "stock total (series)",
    ],
    "kode_stock": [
        "kode material stock", "kode material stok", "material stock code",
        "stock material code", "stock code", "kode stock", "kode stok",
    ],
    "nama": [
        "material / komponen", "material/komponen", "deskripsi material",
    ],
    "spek": [
        "spesifikasi", "specification", "spec", "detail spesifikasi",
    ],
    "qty": [
        "qty / ts (series)", "qty/ts (series)", "qty ts series", "qty ts",
        "qty series", "quantity series",
    ],
    "uom": [
        "uom/car", "uom / car", "uom", "unit", "satuan",
    ],
    "qty_allowance": [
        "qty total (series)",
    ],
    "qty_pr_total": [
        "qty pr total", "total pr",
    ],
}


# ============================================================
# GOOGLE SHEETS
# ============================================================

@st.cache_resource
def connect_google_sheets():
    credentials_path = Path(CREDENTIALS_FILE)

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"File '{CREDENTIALS_FILE}' tidak ditemukan. "
            "Letakkan credentials.json di folder yang sama dengan aplikasi."
        )
    credentials_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    gc = gspread.authorize(creds)

    return gc


@st.cache_resource
def open_spreadsheet(spreadsheet_name):
    client = connect_google_sheets()
    return client.open(spreadsheet_name)


@st.cache_data(ttl=60)
def get_sheet_names(spreadsheet_name):
    spreadsheet = open_spreadsheet(spreadsheet_name)
    return [ws.title for ws in spreadsheet.worksheets()]


@st.cache_data(ttl=60)
def get_sheet_data(spreadsheet_name, sheet_name):
    spreadsheet = open_spreadsheet(spreadsheet_name)
    worksheet = spreadsheet.worksheet(sheet_name)
    return worksheet.get_all_values()


# ============================================================
# NORMALIZATION / HELPER
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_compact(value):
    return re.sub(r"[^a-z0-9]", "", normalize_text(value))


def column_letter(index_zero_based):
    """0 -> A, 25 -> Z, 26 -> AA."""
    number = index_zero_based + 1
    result = ""

    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result

    return result


def cell_label(col_idx, header_text=""):
    letter = column_letter(col_idx)
    header = str(header_text).strip()

    if header:
        return f"{letter} — {header}"

    return f"{letter} — (tanpa nama header)"


def get_max_columns(data):
    if not data:
        return 0
    return max(len(row) for row in data)


def pad_rows(data, width):
    return [list(row) + [""] * (width - len(row)) for row in data]


# ============================================================
# MULTI-ROW HEADER
# ============================================================

def combine_header_rows(data, start_row, header_row_count):
    """
    Menggabungkan beberapa baris header berdasarkan posisi kolom.

    Contoh:

        Row 5: Qty Total |      |      | Material
        Row 6: Series    | Allow|      | / Komponen

    menjadi:

        Qty Total Series
        Allow
        Material / Komponen

    Fungsi ini TIDAK menentukan mapping final.
    Hasilnya hanya menjadi label yang membantu user memilih kolom.
    """
    if not data:
        return []

    if start_row < 0 or start_row >= len(data):
        return []

    end_row = min(start_row + header_row_count, len(data))
    width = get_max_columns(data)
    rows = pad_rows(data[start_row:end_row], width)

    combined = []

    for col_idx in range(width):
        values = []

        for row in rows:
            value = str(row[col_idx]).strip()
            if value and value not in values:
                values.append(value)

        combined.append(" ".join(values))

    return combined


def suggest_mapping(header_columns):
    """
    Memberikan saran exact-match saja.
    Tidak menggunakan fuzzy matching.
    User tetap harus memeriksa/mengubah hasilnya.
    """
    suggestions = {field: None for field in STANDARD_FIELDS}
    used_columns = set()

    for field, aliases in KATA_KUNCI.items():
        alias_normalized = {
            normalize_text(alias) for alias in aliases
        }
        alias_compact = {
            normalize_compact(alias) for alias in aliases
        }

        for col_idx, header in enumerate(header_columns):
            if col_idx in used_columns:
                continue

            normalized = normalize_text(header)
            compact = normalize_compact(header)

            # Kode Material Delete tidak boleh masuk ke Kode Material.
            if field == "kode" and "delete" in normalized.split():
                continue

            if normalized in alias_normalized or compact in alias_compact:
                suggestions[field] = col_idx
                used_columns.add(col_idx)
                break

    return suggestions


def build_column_options(header_columns):
    options = ["— Tidak dipilih —"]

    for col_idx, header in enumerate(header_columns):
        options.append(cell_label(col_idx, header))

    return options


def option_to_index(option, header_columns):
    if option == "— Tidak dipilih —":
        return None

    for col_idx, header in enumerate(header_columns):
        if option == cell_label(col_idx, header):
            return col_idx

    return None


def index_to_option(col_idx, header_columns):
    if col_idx is None:
        return "— Tidak dipilih —"

    if col_idx < 0 or col_idx >= len(header_columns):
        return "— Tidak dipilih —"

    return cell_label(col_idx, header_columns[col_idx])


# ============================================================
# SESSION STATE
# ============================================================

def load_mappings_from_file():
    path = Path(MAPPINGS_FILE)

    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def persist_mappings():
    Path(MAPPINGS_FILE).write_text(
        json.dumps(st.session_state.sheet_mappings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def initialize_state():
    if "sheet_mappings" not in st.session_state:
        st.session_state.sheet_mappings = load_mappings_from_file()

    if "selected_sheet" not in st.session_state:
        st.session_state.selected_sheet = None


def get_saved_mapping(sheet_name):
    return st.session_state.sheet_mappings.get(sheet_name, {})


def save_mapping(sheet_name, mapping, header_start_row, header_row_count):
    st.session_state.sheet_mappings[sheet_name] = {
        "header_start_row": header_start_row,
        "header_row_count": header_row_count,
        "mapping": mapping.copy(),
    }
    persist_mappings()


# ============================================================
# UI HELPERS
# ============================================================

def show_raw_preview(data, start_row=0, max_rows=30):
    if not data:
        st.info("Sheet tidak memiliki data.")
        return

    width = get_max_columns(data)
    preview = pad_rows(data[start_row:start_row + max_rows], width)

    columns = [
        f"{column_letter(i)}"
        for i in range(width)
    ]

    df = pd.DataFrame(preview, columns=columns)
    df.index = range(start_row + 1, start_row + 1 + len(df))
    df.index.name = "Row"

    st.dataframe(
        df,
        width='stretch',
        height=420,
    )


def show_header_preview(data, start_row, header_row_count):
    header_columns = combine_header_rows(
        data,
        start_row,
        header_row_count,
    )

    if not header_columns:
        st.warning("Header belum dapat dibuat dari pilihan tersebut.")
        return []

    st.markdown("#### Header gabungan yang digunakan untuk mapping")

    rows = []
    for col_idx, header in enumerate(header_columns):
        rows.append({
            "Kolom": column_letter(col_idx),
            "Nomor": col_idx + 1,
            "Header gabungan": header,
        })

    st.dataframe(
        pd.DataFrame(rows),
        width='stretch',
        hide_index=True,
        height=300,
    )

    return header_columns


def preview_mapped_data(data, header_columns, data_start_row, mapping, max_rows=20):
    width = get_max_columns(data)
    data_rows = pad_rows(
        data[data_start_row:data_start_row + max_rows],
        width,
    )

    output = {}

    for field, label in STANDARD_FIELDS.items():
        col_idx = mapping.get(field)

        if col_idx is None:
            output[label] = [""] * len(data_rows)
        else:
            output[label] = [
                row[col_idx] if col_idx < len(row) else ""
                for row in data_rows
            ]

    if not data_rows:
        st.info("Belum ada baris data setelah header.")
        return

    st.markdown("#### Preview hasil mapping")
    st.dataframe(
        pd.DataFrame(output),
        width='stretch',
        height=350,
    )


def validate_mapping(mapping, header_columns):
    errors = []
    selected = {}

    for field, label in STANDARD_FIELDS.items():
        col_idx = mapping.get(field)

        if col_idx is None:
            continue

        if col_idx < 0 or col_idx >= len(header_columns):
            errors.append(f"{label}: kolom tidak valid.")
            continue

        selected.setdefault(col_idx, []).append(label)

    for col_idx, labels in selected.items():
        if len(labels) > 1:
            errors.append(
                f"Kolom {column_letter(col_idx)} dipakai lebih dari satu field: "
                + ", ".join(labels)
            )

    return errors


# ============================================================
# MAIN
# ============================================================

def render():
    initialize_state()

    st.title("🗂️ BOM Sheet Mapping Tool")

    # ------------------------------------------------------------
    # SIDEBAR
    # ------------------------------------------------------------

    with st.sidebar:
        st.header("Google Sheets")

        spreadsheet_name = st.text_input(
            "Nama Spreadsheet",
            value=SPREADSHEET_DEFAULT,
        ).strip()

        if st.button("🔄 Refresh Spreadsheet", width='stretch'):
            get_sheet_names.clear()
            get_sheet_data.clear()
            st.rerun()

        st.divider()

        try:
            sheet_names = get_sheet_names(spreadsheet_name)
        except Exception as exc:
            st.error(f"Gagal membuka spreadsheet: {exc}")
            st.stop()

        sheet_options = sheet_names

        if not sheet_options:
            st.warning("Tidak ada worksheet yang ditemukan.")
            st.stop()

        if "recap_sheets" not in st.session_state:
            st.session_state.recap_sheets = [
                n for n in sheet_options if n in st.session_state.sheet_mappings
            ]

        st.multiselect(
            "Pilih Sheet Sumber Rekap",
            sheet_options,
            key="recap_sheets",
        )

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

    # ------------------------------------------------------------
    # LOAD SHEET
    # ------------------------------------------------------------

    try:
        data = get_sheet_data(spreadsheet_name, selected_sheet)
    except Exception as exc:
        st.error(f"Gagal membaca sheet '{selected_sheet}': {exc}")
        st.stop()

    if not data:
        st.warning(f"Sheet '{selected_sheet}' kosong.")
        st.stop()

    width = get_max_columns(data)

    st.subheader(f"Sheet: {selected_sheet}")
    st.write(
        f"**{len(data):,} baris** × **{width:,} kolom** "
        f"berhasil dibaca dari Google Sheets."
    )

    # ------------------------------------------------------------
    # RAW DATA
    # ------------------------------------------------------------

    with st.expander("👁️ Lihat data mentah dari Google Sheets", expanded=True):
        preview_rows = st.number_input(
            "Jumlah baris preview",
            min_value=5,
            max_value=min(100, len(data)),
            value=min(30, len(data)),
            step=5,
        )

        show_raw_preview(data, 0, int(preview_rows))

    # ------------------------------------------------------------
    # HEADER CONFIGURATION
    # ------------------------------------------------------------

    st.divider()
    st.subheader("1. Tentukan struktur multi-row header")

    saved = get_saved_mapping(selected_sheet)

    saved_start = saved.get("header_start_row", 0)
    saved_count = saved.get("header_row_count", 1)

    max_start = max(0, len(data) - 1)

    col1, col2 = st.columns(2)

    with col1:
        header_start_row = st.number_input(
            "Baris header pertama",
            min_value=1,
            max_value=len(data),
            value=min(saved_start + 1, len(data)),
            step=1,
            help="Nomor baris seperti yang terlihat di preview Google Sheets.",
        )

    with col2:
        max_header_rows = min(6, len(data) - int(header_start_row) + 1)
        max_header_rows = max(1, max_header_rows)

        header_row_count = st.number_input(
            "Jumlah baris header",
            min_value=1,
            max_value=max_header_rows,
            value=min(max(1, saved_count), max_header_rows),
            step=1,
            help="Gunakan >1 jika header terdiri dari beberapa baris.",
        )

    header_start_idx = int(header_start_row) - 1
    header_row_count = int(header_row_count)

    data_start_row = header_start_idx + header_row_count

    header_columns = show_header_preview(
        data,
        header_start_idx,
        header_row_count,
    )

    if not header_columns:
        st.stop()

    st.info(
        f"Header: Row {header_start_row} sampai "
        f"Row {header_start_row + header_row_count - 1}. "
        f"Data mulai dari Row {data_start_row + 1}."
    )

    # ------------------------------------------------------------
    # AUTO SUGGESTION
    # ------------------------------------------------------------

    suggestions = suggest_mapping(header_columns)

    with st.expander("💡 Saran mapping exact-match", expanded=False):
        st.caption(
            "Saran ini hanya berdasarkan kecocokan header yang persis setelah normalisasi. "
            "Tidak menggunakan fuzzy matching dan tidak menentukan mapping secara otomatis."
        )

        suggestion_rows = []
        for field, label in STANDARD_FIELDS.items():
            col_idx = suggestions.get(field)
            suggestion_rows.append({
                "Field": label,
                "Saran Kolom": (
                    cell_label(col_idx, header_columns[col_idx])
                    if col_idx is not None
                    else "Tidak ditemukan"
                ),
            })

        st.dataframe(
            pd.DataFrame(suggestion_rows),
            width='stretch',
            hide_index=True,
        )

    if st.button(
        "✨ Isi Mapping dari Saran Exact-Match",
        help="Mengisi pilihan berdasarkan saran. Kamu tetap bisa mengubahnya setelah itu.",
    ):
        mapping = suggestions.copy()
    else:
        saved_mapping = saved.get("mapping", {})
        mapping = {
            field: saved_mapping.get(field)
            for field in STANDARD_FIELDS
        }

    # ------------------------------------------------------------
    # MANUAL MAPPING
    # ------------------------------------------------------------

    st.divider()
    st.subheader("2. Pilih kolom untuk setiap field")
    st.caption(
        "Pilihan diambil langsung dari kolom sheet. Nama header hanya ditampilkan sebagai "
        "petunjuk; aplikasi menggunakan posisi kolom yang kamu pilih."
    )

    column_options = build_column_options(header_columns)

    new_mapping = {}

    fields = list(STANDARD_FIELDS.items())

    for start in range(0, len(fields), 2):
        cols = st.columns(2)

        for offset, (field, label) in enumerate(fields[start:start + 2]):
            with cols[offset]:
                current_idx = mapping.get(field)
                default_option = index_to_option(current_idx, header_columns)

                selected_option = st.selectbox(
                    label,
                    column_options,
                    index=(
                        column_options.index(default_option)
                        if default_option in column_options
                        else 0
                    ),
                    key=f"mapping_{selected_sheet}_{field}",
                )

                new_mapping[field] = option_to_index(
                    selected_option,
                    header_columns,
                )

    # ------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------

    errors = validate_mapping(new_mapping, header_columns)

    if errors:
        st.error("Terdapat masalah pada mapping:")
        for error in errors:
            st.write(f"- {error}")
    else:
        st.success("Mapping tidak memiliki kolom yang dipakai ganda.")

    required_identity = [
        new_mapping.get("kode"),
        new_mapping.get("kode_delete"),
        new_mapping.get("nama"),
    ]

    if all(value is None for value in required_identity):
        st.warning(
            "Minimal pilih salah satu dari: Kode Material, Kode Material Delete, "
            "atau Material / Komponen."
        )

    # ------------------------------------------------------------
    # SAVE MAPPING TO SESSION
    # ------------------------------------------------------------

    st.divider()
    st.subheader("3. Simpan dan preview")

    save_col, reset_col = st.columns(2)

    with save_col:
        if st.button(
            "💾 Simpan Mapping Sheet Ini",
            type="primary",
            width='stretch',
            disabled=bool(errors),
        ):
            save_mapping(
                selected_sheet,
                new_mapping,
                header_start_idx,
                header_row_count,
            )
            st.success(
                f"Mapping untuk '{selected_sheet}' berhasil disimpan "
                f"(session + file {MAPPINGS_FILE})."
            )

    with reset_col:
        if st.button(
            "↩️ Reset Mapping Sheet",
            width='stretch',
        ):
            st.session_state.sheet_mappings.pop(selected_sheet, None)
            persist_mappings()

            for field in STANDARD_FIELDS:
                key = f"mapping_{selected_sheet}_{field}"
                st.session_state.pop(key, None)

            st.rerun()

    # ------------------------------------------------------------
    # PREVIEW
    # ------------------------------------------------------------

    preview_mapped_data(
        data,
        header_columns,
        data_start_row,
        new_mapping,
        max_rows=20,
    )

    # ------------------------------------------------------------
    # CURRENT MAPPING INFO
    # ------------------------------------------------------------

    with st.expander("🔎 Mapping aktif sheet ini"):
        mapping_rows = []

        for field, label in STANDARD_FIELDS.items():
            col_idx = new_mapping.get(field)

            mapping_rows.append({
                "Field": label,
                "Kolom": column_letter(col_idx) if col_idx is not None else "-",
                "Nomor Kolom": col_idx + 1 if col_idx is not None else "-",
                "Header": (
                    header_columns[col_idx]
                    if col_idx is not None
                    else "-"
                ),
            })

        st.dataframe(
            pd.DataFrame(mapping_rows),
            width='stretch',
            hide_index=True,
        )

    # ------------------------------------------------------------
    # GENERATE REKAP
    # ------------------------------------------------------------

    st.divider()
    st.subheader("4. Generate Rekap")

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

                if df.empty:
                    st.error(
                        "Tidak ada baris yang bisa diekstrak dari sheet "
                        "dengan mapping tersimpan."
                    )
                else:
                    total = write_recap_to_sheets(spreadsheet, df)
                    ok_count = len([r for r in report if r["status"] == "ok"])
                    st.success(
                        f"Rekap selesai: {total} baris dari {ok_count} sheet "
                        f"ditulis ke '{REKAP_TAB_NAME}'."
                    )

                for item in report:
                    if item["status"] != "ok":
                        st.warning(f"{item['sheet']}: {item['message']}")
            except Exception as exc:
                st.error(f"Generate rekap gagal: {exc}")

    # ------------------------------------------------------------
    # SESSION SUMMARY
    # ------------------------------------------------------------

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

    st.caption(
        f"Catatan: mapping disimpan di session dan file {MAPPINGS_FILE}, "
        "sehingga tetap ada setelah refresh. "
        "Data sumber tetap berasal langsung dari Google Sheets."
    )
