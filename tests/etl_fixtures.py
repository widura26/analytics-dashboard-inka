"""Fixture & helper bersama untuk suite uji ETL.

Menambahkan ROOT proyek ke sys.path agar `import material_planning_2`
berfungsi walau test dijalankan dari folder tests/.
"""
import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------
# BOM
# ------------------------------------------------------------

BOM_HEADER = [
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

# urutan: duplikat, baris TOTAL, baris tanpa kode, kode delete terisi,
# baris beda WBS (B-2.20, tanpa pasangan SAP)
BOM_ROWS = [
    ["Interior", "A-1.10", "1000123", "", "1.250", "", "BAUT M8", "M8x20",
     "10", "pcs", "1.250", "750"],
    ["Interior", "A-1.10", "1000123", "", "1.250", "", "BAUT M8", "M8x20",
     "10", "pcs", "1.250", "750"],
    ["Interior", "A-1.10", "TOTAL", "", "", "", "Subtotal", "",
     "", "", "", ""],
    ["Interior", "A-1.10", "", "", "", "", "BARIS KOSONG", "",
     "", "", "0", "0"],
    ["Interior", "A-1.10", "90001", "1000999", "0", "", "PANEL", "KULIT",
     "2", "pcs", "2", "0"],
    ["Bogie", "B-2.20", "1000456", "", "0,5", "", "KAWAT", "SWG",
     "1", "kg", "1", "1"],
]

# ------------------------------------------------------------
# SAP
# ------------------------------------------------------------

SAP_HEADER = [
    "Kode Material",
    "WBS Elem",
    "WBS Description",
    "Mat. Description",
    "Spesifikasi",
    "PR Status",
    "Qty Requested",
    "Ordered",
    "Req.Date",
    "PR Deliv. Dat",
    "Chngd on",
]

# urutan: baris pertama, duplikatnya, kode tanpa pasangan BOM
SAP_ROWS = [
    ["1000123", "A-1.10", "WP-PR1", "BAUT M8", "M8x20", "OPEN", "10", "1234",
     "05.10.2026", "10.10.2026", "01.10.2026"],
    ["1000123", "A-1.10", "WP-PR1", "BAUT M8", "M8x20", "OPEN", "10", "1234",
     "05.10.2026", "10.10.2026", "01.10.2026"],
    ["1000999", "B-2.20", "WP-PR2", "PEGAS", "SPRG", "OPEN", "5", "0",
     "05.10.2026", "10.10.2026", "01.10.2026"],
]


# ------------------------------------------------------------
# HELPER
# ------------------------------------------------------------

def to_csv(header, rows, sep=",", encoding="utf-8-sig") -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=sep)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode(encoding)


BOM_BYTES = to_csv(BOM_HEADER, BOM_ROWS)
SAP_BYTES = to_csv(SAP_HEADER, SAP_ROWS)

# CSV tanpa header yang dikenali algoritma rekap
BAD_BOM_BYTES = to_csv(["Kolom Ache", "Lainnya"], [["data", "1"]])


class FakeUpload:
    """Peniru UploadedFile Streamlit (name/size/getvalue)."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.size = len(data)
        self._data = data

    def getvalue(self) -> bytes:
        return self._data
