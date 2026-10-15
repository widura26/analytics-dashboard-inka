"""Pembaca file Excel -> grid sel in-memory (semua sheet, tanpa konversi CSV).

Dipakai alur ETL: begitu user mengunggah file .xlsx/.xls, SELURUH sheet dibaca
lewat pandas menjadi grid (daftar baris × kolom sel string) — format yang sama
dengan hasil pembacaan CSV — sehingga tahap rekap & cleaning bekerja sama persis
baik untuk file CSV maupun Excel.

Grid dipertahankan apa adanya (header=None, tanpa inferensi nama kolom) supaya
deteksi header multi-baris pada rekap BOM dan pembacaan SAP bekerja sama persis
seperti pada file CSV. Sel angka bulat ditulis tanpa ".0" (kode material
1000123 tidak boleh berubah jadi "1000123.0"), dan sel tanggal diformat ulang
memakai format tanggal standar aplikasi (dd.mm.yyyy).
"""
import io
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# Format tanggal standar aplikasi (sama dengan SAP_DATE_FORMAT).
DEFAULT_DATE_FORMAT = "%d.%m.%Y"

# Ekstensi yang dikenali beserta engine pembacanya.
ENGINE_BY_SUFFIX = {
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".xls": "xlrd",
    ".xlsb": "pyxlsb",
}
EXCEL_SUFFIXES = tuple(ENGINE_BY_SUFFIX)


def file_suffix(filename: str) -> str:
    """Ekstensi huruf kecil tanpa titik: 'bom.csv' -> 'csv'."""
    return Path(filename).suffix.lower().lstrip(".")


def file_stem(filename: str) -> str:
    """Nama file tanpa ekstensi: 'bom_proyek.csv' -> 'bom_proyek'."""
    return Path(filename).stem


def is_excel_file(filename: str) -> bool:
    """True bila nama file berekstensi Excel yang dikenali."""
    return Path(filename).suffix.lower() in ENGINE_BY_SUFFIX


def read_excel_sheets(
    raw: bytes,
    filename: str,
    date_format: str = DEFAULT_DATE_FORMAT,
):
    """Baca SEMUA sheet file Excel menjadi grid sel (tanpa lewat CSV).

    Mengembalikan daftar dict per sheet:
      {"label": nama sheet, "grid": list[list[str]], "rows": int, "cols": int}
    """
    suffix = Path(filename).suffix.lower()
    engine = ENGINE_BY_SUFFIX.get(suffix)
    if engine is None:
        raise ValueError(
            f"Format '{suffix or filename}' bukan Excel yang didukung "
            f"({', '.join(sorted(ENGINE_BY_SUFFIX))})."
        )

    try:
        excel = pd.ExcelFile(io.BytesIO(raw), engine=engine)
    except ImportError as exc:
        raise ValueError(
            f"Pembaca format '{suffix}' ('{engine}') belum terpasang di "
            f"server aplikasi. {exc}"
        ) from exc
    except Exception as exc:
        raise ValueError(f"Gagal membuka file Excel: {exc}") from exc

    hasil = []
    try:
        for title in excel.sheet_names:
            sheet_df = excel.parse(title, header=None)
            grid = [
                [_cell_to_text(value, date_format) for value in row]
                for row in sheet_df.itertuples(index=False, name=None)
            ]
            hasil.append({
                "label": title,
                "grid": grid,
                "rows": int(len(sheet_df)),
                "cols": int(len(sheet_df.columns)),
            })
    except Exception as exc:
        raise ValueError(f"Gagal membaca sheet Excel: {exc}") from exc
    finally:
        excel.close()

    if not hasil:
        raise ValueError("File Excel tidak berisi sheet apa pun.")

    return hasil


def _cell_to_text(value, date_format: str = DEFAULT_DATE_FORMAT) -> str:
    """Ubah satu sel Excel menjadi teks sel seperti pada file CSV."""
    if pd.isna(value):
        return ""

    if isinstance(value, bool):
        return str(value)

    # Termasuk pd.Timestamp (turunan datetime) & date.
    if isinstance(value, (datetime, date)):
        return value.strftime(date_format)

    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(number)

    return str(value)
