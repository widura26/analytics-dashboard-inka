"""Utilitas baca CSV dari memori: deteksi encoding dan pemisah kolom."""
import csv
import io

import pandas as pd

ENCODINGS = ("utf-8-sig", "latin-1")
DELIMITERS = [",", ";", "\t", "|"]


def decode_text(data: bytes) -> str:
    """Decode bytes CSV: coba utf-8-sig lalu latin-1 (gaya export Excel/SAP)."""
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def sniff_delimiter(text: str) -> str:
    """Tebak pemisah kolom dari baris pertama (',' ';' tab '|')."""
    first_line = next((line for line in text.splitlines() if line.strip()), "")

    if not first_line:
        return ","

    delimiter = max(DELIMITERS, key=first_line.count)
    return delimiter if first_line.count(delimiter) else ","


def read_csv_text(data: bytes) -> str:
    """Kembalikan isi CSV sebagai teks; error bila kosong."""
    text = decode_text(data)

    if not text.splitlines() or not any(line.strip() for line in text.splitlines()):
        raise ValueError("File CSV kosong.")

    return text


def split_rows(data: bytes) -> list:
    """Pecah CSV menjadi grid string mentah (tanpa inferensi tipe pandas)."""
    text = read_csv_text(data)
    delimiter = sniff_delimiter(text)

    return [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]


def grid_to_dataframe(grid: list) -> pd.DataFrame:
    """Ubah grid sel (baris pertama = header) menjadi DataFrame.

    Padanan bacaan read_csv untuk grid dari CSV maupun Excel:
      - baris yang seluruhnya kosong dilewati (skip_blank_lines),
      - header duplikat diberi akhiran '.1', '.2' (gaya mangle_dupe),
      - semua nilai tetap sebagai teks — penilaian angka/tanggal dilakukan
        tahap cleaning (clean_sap), bukan saat membaca.
    """
    if not grid:
        raise ValueError("Tidak ada baris data.")

    counts: dict = {}
    columns = []
    for cell in grid[0]:
        name = "" if cell is None else str(cell)
        if name in counts:
            columns.append(f"{name}.{counts[name]}")
            counts[name] += 1
        else:
            counts[name] = 1
            columns.append(name)

    jumlah_kolom = len(columns)
    rows = []
    for row in grid[1:]:
        if not row or all(
            cell is None or not str(cell).strip() for cell in row
        ):
            continue  # baris kosong

        cells = ["" if cell is None else str(cell) for cell in row]
        if len(cells) < jumlah_kolom:
            cells = cells + [""] * (jumlah_kolom - len(cells))
        rows.append(cells[:jumlah_kolom])

    return pd.DataFrame(rows, columns=columns)
