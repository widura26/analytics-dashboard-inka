import re

import gspread
import pandas as pd

REKAP_TAB_NAME = "REKAP_BOM_TOTAL (Berdasarkan Kode Material) 3"

REKAP_COLUMNS = [
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

FIELD_ORDER = [
    "kode",
    "kode_delete",
    "stock_total",
    "kode_stock",
    "nama",
    "spek",
    "qty",
    "uom",
    "qty_allowance",
    "qty_pr_total",
]

NUMERIC_FIELDS = {"stock_total", "qty", "qty_allowance", "qty_pr_total"}

KATA_ABAIKAN = [
    "kode material",
    "kode",
    "total",
    "subtotal",
    "header",
    "item code",
    "part number",
    "k o d e",
]


def parse_number(value):
    if value is None:
        return 0.0

    text = str(value).strip()

    if not text:
        return 0.0

    if text.upper() in {"N/A", "-", "#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#REF"}:
        return 0.0

    text = text.replace(",", ".")

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if not match:
        return 0.0

    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def run_recap(spreadsheet, sheet_mappings):
    """Ekstrak data hanya untuk sheet yang punya mapping tersimpan.

    Mengembalikan (DataFrame, report) di mana report berisi status per sheet.
    Kolom custom (key di luar FIELD_ORDER) di-union lintas sheet, urut kemunculan.
    """
    rows = []
    report = []
    extra_labels = []
    extra_numeric = {}

    for sheet_name, saved in sheet_mappings.items():
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            report.append({
                "sheet": sheet_name,
                "status": "hilang",
                "message": "sheet tidak ditemukan di spreadsheet.",
            })
            continue
        except Exception as exc:
            report.append({
                "sheet": sheet_name,
                "status": "gagal",
                "message": f"gagal membuka sheet: {exc}",
            })
            continue

        data = worksheet.get_all_values()

        if not data:
            report.append({
                "sheet": sheet_name,
                "status": "kosong",
                "message": "sheet tidak memiliki data.",
            })
            continue

        mapping = saved.get("mapping", {})
        custom_fields = saved.get("custom_fields", {})
        wbs_row = saved.get("wbs_row", 2)
        wbs_col = saved.get("wbs_col", 0)
        data_start = (
            saved.get("header_start_row", 0)
            + saved.get("header_row_count", 1)
        )

        wbs_value = ""
        if wbs_row and wbs_row > 0 and wbs_row - 1 < len(data):
            source_row = data[wbs_row - 1]
            if wbs_col < len(source_row):
                wbs_value = str(source_row[wbs_col]).strip()

        if mapping.get("kode") is None and mapping.get("nama") is None:
            report.append({
                "sheet": sheet_name,
                "status": "gagal",
                "message": "mapping tidak memiliki Kode Material / Material-Komponen.",
            })
            continue

        extras = [key for key in mapping if key not in FIELD_ORDER]

        for label in extras:
            if label not in extra_labels:
                extra_labels.append(label)
            extra_numeric[label] = bool(
                custom_fields.get(label, {}).get("numeric", False)
            )

        kode_mapped = mapping.get("kode") is not None
        max_index = max(
            (idx for idx in mapping.values() if idx is not None),
            default=-1,
        )

        extracted = 0

        for raw_row in data[data_start:]:
            row = list(raw_row)

            if len(row) <= max_index:
                row = row + [""] * (max_index + 1 - len(row))

            values = {}

            for field in FIELD_ORDER:
                col = mapping.get(field)

                if col is None:
                    values[field] = 0.0 if field in NUMERIC_FIELDS else ""
                elif field in NUMERIC_FIELDS:
                    values[field] = parse_number(row[col])
                else:
                    values[field] = str(row[col]).strip()

            filter_value = (
                values["kode"] if kode_mapped else values["nama"]
            ).lower().strip()

            if not filter_value or filter_value in KATA_ABAIKAN:
                continue

            row_data = {"Nama Sheet Sumber": sheet_name, "WBS Elem": wbs_value}
            row_data.update({
                col_name: values[field]
                for field, col_name in zip(FIELD_ORDER, REKAP_COLUMNS[2:])
            })

            for label in extras:
                col = mapping.get(label)
                numeric = extra_numeric.get(label, False)

                if col is None:
                    row_data[label] = 0.0 if numeric else ""
                elif numeric:
                    row_data[label] = parse_number(row[col])
                else:
                    row_data[label] = str(row[col]).strip()

            rows.append(row_data)
            extracted += 1

        report.append({
            "sheet": sheet_name,
            "status": "ok",
            "message": f"{extracted} baris terambil.",
        })

    columns = REKAP_COLUMNS + extra_labels
    df = pd.DataFrame(rows, columns=columns)

    for label in extra_labels:
        df[label] = df[label].fillna(0.0 if extra_numeric.get(label) else "")

    return df, report


def write_recap_to_sheets(spreadsheet, df, tab_name=REKAP_TAB_NAME):
    """Tulis DataFrame rekap ke tab Google Sheets. Mengembalikan jumlah baris."""
    try:
        worksheet = spreadsheet.worksheet(tab_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=tab_name,
            rows="8000",
            cols=str(len(df.columns)),
        )

    values = [df.columns.values.tolist()] + df.values.tolist()

    if worksheet.col_count < len(df.columns) or worksheet.row_count < len(values):
        worksheet.resize(
            rows=max(worksheet.row_count, len(values)),
            cols=max(worksheet.col_count, len(df.columns)),
        )

    worksheet.update(values)

    return len(df)
