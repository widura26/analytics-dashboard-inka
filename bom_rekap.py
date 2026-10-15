"""Rekap BOM: deteksi header fuzzy multi-baris di atas grid sel CSV.

Algoritma diadopsi dari skrip rekap yang diberikan (deteksi header multi-baris,
keyword per field dengan threshold, ekstraksi per KOLOM_CONFIG, kode WBS dari
baris metadata). Sumber datanya diganti: dari Google Sheets menjadi file CSV
yang di-upload (in-memory), dan "Nama Sheet Sumber" memakai nama file CSV
(bila CSV punya kolom sumbernya sendiri, kolom itu yang dipakai).
"""
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from csv_utils import split_rows

# ============================================================
# 1. KOLOM CONFIG
# Tambahkan kolom baru cukup di list ini.
# type: "text" atau "number"
# ============================================================

KOLOM_CONFIG = [
    {
        "key": "kode",
        "label": "Kode Material",
        "keywords": [
            "kode material", "material code", "material kode", "item code",
            "part number", "part no", "part no.", "kode material asli",
        ],
        "threshold": 78,
        "type": "text",
    },
    {
        "key": "kode_delete",
        "label": "Kode Material Delete",
        "keywords": [
            "kode material delete", "material delete code", "delete material code",
        ],
        "threshold": 78,
        "type": "text",
    },
    {
        "key": "stock_total",
        "label": "Stock Total",
        "keywords": ["stock total", "total stock", "stock total (series)"],
        "threshold": 70,
        "type": "number",
    },
    {
        "key": "kode_stock",
        "label": "Kode Material Stock",
        "keywords": [
            "kode material stock", "kode material stock 1", "kode material stock 2",
            "kode material stock 3", "kode material stock 4", "kode material stock 5",
            "kode material stok", "kode material stok 1", "kode material stok 2",
            "kode material stok 3", "kode material stok 4", "kode material stok 5",
            "material stock code", "stock material code", "stock code",
            "kode stock", "kode stok",
        ],
        "threshold": 78,
        "type": "text",
    },
    {
        "key": "nama",
        "label": "Material / Komponen",
        "keywords": ["material / komponen", "material/komponen", "deskripsi material"],
        "threshold": 72,
        "type": "text",
    },
    {
        "key": "spek",
        "label": "Spesifikasi",
        "keywords": ["spesifikasi", "specification", "spec", "detail spesifikasi"],
        "threshold": 72,
        "type": "text",
    },
    {
        "key": "qty",
        "label": "Qty / TS (Series)",
        "keywords": ["qty / ts (series)", "qty/ts (series)", "qty ts series", "qty/bogie (series)"],
        "threshold": 72,
        "type": "number",
    },
    {
        "key": "uom",
        "label": "UoM/Car",
        "keywords": ["uom/car", "uom / car", "uom", "unit", "satuan", "uom/bogie"],
        "threshold": 75,
        "type": "text",
    },
    {
        "key": "qty_allowance",
        "label": "Qty Total (Series) + Allowance",
        "keywords": [
            "qty total (series)", "qty total series", "qty total (series) + allowance",
        ],
        "threshold": 80,
        "type": "number",
    },
    {
        "key": "qty_pr_total",
        "label": "QTY PR TOTAL",
        "keywords": ["qty pr total", "total pr"],
        "threshold": 70,
        "type": "number",
    },
    {
        "key": "bom",
        "label": "BOM",
        "keywords": ["bom", "bill of materials"],
        "threshold": 75,
        "type": "text",
    },
]

# Validasi konfigurasi agar tidak ada key duplikat.
_config_keys = [item["key"] for item in KOLOM_CONFIG]
if len(_config_keys) != len(set(_config_keys)):
    raise ValueError("Ada nilai 'key' duplikat di KOLOM_CONFIG.")

KATA_KUNCI = {item["key"]: item["keywords"] for item in KOLOM_CONFIG}
THRESHOLD_FIELD = {item["key"]: item["threshold"] for item in KOLOM_CONFIG}
FIELD_ORDER = [item["key"] for item in KOLOM_CONFIG]

KOLOM_REKAP = [
    "Nama Sheet Sumber",
    "WBS Elem",
    *[item["label"] for item in KOLOM_CONFIG],
]

# Header kolom opsional yang dipakai per baris bila tersedia di CSV.
SHEET_SOURCE_HEADERS = {
    "nama sheet sumber", "sheet sumber", "sumber sheet", "nama sheet", "source sheet",
}
WBS_HEADERS = {
    "wbs elem", "wbs element", "wbs elemen", "kode wbs", "wbs code", "wbs",
}

KATA_ABAIKAN = {
    "kode material", "kode", "total", "subtotal",
    "header", "item code", "part number", "k o d e",
}


# ============================================================
# 2. NORMALIZATION (CACHED)
# ============================================================

@lru_cache(maxsize=50000)
def normalize_text(text):
    if text is None:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@lru_cache(maxsize=50000)
def normalize_compact(text):
    return re.sub(r"[^a-z0-9]", "", normalize_text(text))


# ============================================================
# 2b. DAFTAR 29 SHEET TARGET (algoritma asli / Colab)
# ============================================================

# Hanya sheet yang namanya cocok dengan daftar ini yang direkap —
# persis cakupan skrip asli (recap.py / notebook Google Colab).
DAFTAR_SHEET_TARGET = [
    "Elektrik + Pre Series", "De-Scoping", "Interior", "TMS", "CKD VVVF",
    "CKD Pantograph", "CKD SIV", "Elektrik", "Komponen Utama", "Sistem Mekanik",
    "Raw Material Interior", "Bogie KIT", "Raw Material Bogie",
    "Realisasi Raw Material", "Carbody", "Fastening Mekanik",
    "Fastening Bogie", "Fastening Interior", "Crashwrothiness", "Welding",
    "Welding Bogie", "Consumable Tools", "Consumable Series",
    "Welding WS BWI", "Jig Tools 6TS Pelokalan", "Jig Tool", "Tools",
    "Tools & Consumable Tools Perbaikan", "Consumable Pre Series",
]

_TARGET_COMPACT = {
    normalize_compact(nama): nama for nama in DAFTAR_SHEET_TARGET
}

# Nama sheet di Excel maksimal 31 karakter (Google Sheets memotong nama
# saat export) — pencocokan prefiks menangani nama terpotong, dengan
# ambang panjang supaya nama pendek tidak nyasar ke target lebih panjang
# (mis. "Consumable" tidak boleh nyasar ke "Consumable Tools").
_MIN_PANJANG_PREFIKS = 20


def target_sheet(nama: str):
    """Nama target yang cocok dengan `nama` sheet, atau None bila bukan target."""
    kunci = normalize_compact(nama)
    if not kunci:
        return None

    if kunci in _TARGET_COMPACT:
        return _TARGET_COMPACT[kunci]

    if len(kunci) >= _MIN_PANJANG_PREFIKS:
        for kunci_target, nama_target in _TARGET_COMPACT.items():
            if kunci_target.startswith(kunci):
                return nama_target

    return None


def nama_sheet_target(nama: str) -> bool:
    """True bila `nama` sheet termasuk daftar 29 sheet target."""
    return target_sheet(nama) is not None


KEYWORD_CACHE = {
    field: [(kw, normalize_text(kw), normalize_compact(kw)) for kw in keywords]
    for field, keywords in KATA_KUNCI.items()
}


# ============================================================
# 3. PREPARE HEADER PARTS AND MATCHING
# ============================================================

@lru_cache(maxsize=50000)
def header_parts_normalized(text):
    if text is None:
        return ()
    result = []
    for part in str(text).split("||"):
        part = part.strip()
        if part:
            norm = normalize_text(part)
            if norm:
                result.append((norm, normalize_compact(norm)))
    return tuple(result)


def gabungkan_header(data, start_row, jumlah_baris):
    end_row = min(start_row + jumlah_baris, len(data))
    if start_row >= end_row:
        return []

    max_col = max((len(data[r]) for r in range(start_row, end_row)), default=0)
    hasil = []

    for col_idx in range(max_col):
        values = []
        seen = set()
        for row_idx in range(start_row, end_row):
            if col_idx >= len(data[row_idx]):
                continue
            value = str(data[row_idx][col_idx]).strip()
            if value and value not in seen:
                values.append(value)
                seen.add(value)
        hasil.append(" || ".join(values))

    return hasil


def cari_mapping_header(header_columns):
    kandidat = []

    for col_idx, header_text in enumerate(header_columns):
        if not header_text or not normalize_text(header_text):
            continue

        parts = header_parts_normalized(str(header_text))
        if not parts:
            continue

        prepared_parts = [
            (norm, compact, "delete" in norm.split(), "pre" in norm.split())
            for norm, compact in parts
        ]

        for field in FIELD_ORDER:
            best_score = 0
            best_keyword = None

            for keyword, key_norm, key_compact in KEYWORD_CACHE[field]:
                score_for_keyword = 0
                for text_norm, text_compact, has_delete, has_pre in prepared_parts:
                    if field == "kode" and has_delete:
                        continue
                    if field == "qty_allowance" and has_pre:
                        continue

                    if text_norm == key_norm:
                        score_for_keyword = 100
                        break
                    if text_compact == key_compact:
                        score_for_keyword = max(score_for_keyword, 98)

                if score_for_keyword > best_score:
                    best_score = score_for_keyword
                    best_keyword = keyword
                if best_score == 100:
                    break

            if best_score >= THRESHOLD_FIELD[field]:
                kandidat.append({
                    "field": field,
                    "col_idx": col_idx,
                    "score": best_score,
                    "keyword": best_keyword,
                    "header": header_text,
                })

    kandidat.sort(key=lambda x: x["score"], reverse=True)
    mapping = {field: None for field in FIELD_ORDER}
    field_scores = {field: 0 for field in FIELD_ORDER}
    kolom_terpakai = set()

    for item in kandidat:
        field = item["field"]
        col_idx = item["col_idx"]
        if mapping[field] is not None or col_idx in kolom_terpakai:
            continue
        mapping[field] = col_idx
        field_scores[field] = item["score"]
        kolom_terpakai.add(col_idx)

    return mapping, field_scores, kandidat


# ============================================================
# 4. HEADER DETECTION
# ============================================================

def deteksi_header(data, max_scan=100, max_header_rows=5):
    kandidat_header = []
    jumlah_baris_scan = min(max_scan, len(data))

    for start_row in range(jumlah_baris_scan):
        first_mapping, _, _ = cari_mapping_header(data[start_row])
        jumlah_field_first_row = sum(v is not None for v in first_mapping.values())

        for jumlah_baris in range(1, max_header_rows + 1):
            end_row_exclusive = start_row + jumlah_baris
            if end_row_exclusive > len(data):
                break

            header_columns = gabungkan_header(data, start_row, jumlah_baris)
            mapping, field_scores, kandidat_field = cari_mapping_header(header_columns)
            jumlah_field = sum(v is not None for v in mapping.values())

            if jumlah_field == 0:
                continue
            if mapping.get("kode") is None and mapping.get("nama") is None:
                continue

            total_score = sum(field_scores.values())
            if mapping.get("kode") is not None:
                total_score += 25
            if mapping.get("nama") is not None:
                total_score += 35
            total_score += 5 * sum(score >= 95 for score in field_scores.values())

            kandidat_header.append({
                "row_idx": start_row,
                "end_row_idx": end_row_exclusive - 1,
                "jumlah_baris": jumlah_baris,
                "mapping": mapping,
                "field_scores": field_scores,
                "score": total_score,
                "jumlah_field": jumlah_field,
                "jumlah_field_first_row": jumlah_field_first_row,
                "header_columns": header_columns,
                "kandidat_field": kandidat_field,
            })

    if not kandidat_header:
        return None

    kandidat_header.sort(
        key=lambda x: (
            x["jumlah_field"],
            x["jumlah_field_first_row"],
            x["score"],
            -x["jumlah_baris"],
        ),
        reverse=True,
    )
    return kandidat_header[0]


# ============================================================
# 5. VALUE HELPERS
# ============================================================

INVALID_VALUES = {
    "N/A", "-", "#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#REF"
}
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


def parse_number(value):
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text.upper() in INVALID_VALUES:
        return 0.0
    match = NUMBER_PATTERN.search(text.replace(",", "."))
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def get_cell(row, col_idx, default=""):
    if col_idx is None or col_idx < 0 or col_idx >= len(row):
        return default
    return str(row[col_idx]).strip()


def mendeteksi_output_rekap(data) -> bool:
    """True bila grid memuat kolom 'Nama Sheet Sumber' di 10 baris atas.

    Tanda khas sheet HASIL REKAP (output), bukan data BOM mentah —
    contohnya tab 'TOTAL BOM' lama di bom_dataset yang ikut ter-export
    ke Excel dan akan salah di-rekap ulang bila tidak dijaga. Hanya
    dipakai untuk sumber Excel; file CSV tetap diperbolehkan memakai
    kolom sumber per baris (memang didukung alur CSV).
    """
    for row in data[:10]:
        for cell in row:
            if normalize_text(cell) in SHEET_SOURCE_HEADERS:
                return True
    return False


def ambil_kode_wbs(data):
    if len(data) < 2:
        return ""

    row_wbs = data[1]
    values = [str(v).strip() for v in row_wbs if str(v).strip()]
    if not values:
        return ""

    for value in values:
        match = re.search(r"\bwbs\b\s*[:\-]?\s*(.+)$", value, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()

    for idx, value in enumerate(row_wbs):
        if normalize_text(value) == "wbs":
            for next_value in row_wbs[idx + 1:]:
                next_value = str(next_value).strip()
                if next_value:
                    return next_value

    return values[0]


def find_header_column(header_columns, wanted: set):
    """Cari kolom header berdasarkan nama persis (setelah normalisasi)."""
    wanted_compact = {normalize_compact(name) for name in wanted}

    for idx, text in enumerate(header_columns):
        if normalize_text(text) in wanted or normalize_compact(text) in wanted_compact:
            return idx

    return None


# ============================================================
# 6. REKAP DARI GRID CSV
# ============================================================

def rekap_grid(data, source_label: str = ""):
    """Rekap satu grid sel CSV. Mengembalikan (DataFrame, report)."""
    if not data:
        raise ValueError("File CSV tidak memiliki baris data.")

    hasil = deteksi_header(data, max_scan=100, max_header_rows=5)

    if hasil is None:
        raise ValueError(
            "Header BOM tidak ditemukan. Pastikan file memiliki kolom seperti "
            "'Kode Material' atau 'Material / Komponen'."
        )

    mapping = hasil["mapping"]

    if mapping.get("kode") is None:
        raise ValueError(
            "Kolom 'Kode Material' tidak terdeteksi pada header file. "
            "Kolom ini wajib karena menjadi kunci merge dengan data SAP. "
            "Periksa baris header (maksimal 5 baris digabung)."
        )

    header_columns = hasil["header_columns"]
    source_col = find_header_column(header_columns, SHEET_SOURCE_HEADERS)
    wbs_col = find_header_column(header_columns, WBS_HEADERS)
    kode_wbs = ambil_kode_wbs(data)

    data_start_idx = hasil["end_row_idx"] + 1
    rekap_data = []
    rows_skipped_empty = 0
    rows_skipped_ignored = 0

    for row in data[data_start_idx:]:
        kode_material = get_cell(row, mapping.get("kode"))
        kode_material_delete = get_cell(row, mapping.get("kode_delete"))

        if not kode_material and not kode_material_delete:
            rows_skipped_empty += 1
            continue
        if kode_material.lower() in KATA_ABAIKAN:
            rows_skipped_ignored += 1
            continue
        if kode_material_delete.lower() in KATA_ABAIKAN:
            rows_skipped_ignored += 1
            continue

        # Nilai field dibuat otomatis sesuai urutan KOLOM_CONFIG.
        nilai_field = []
        for item in KOLOM_CONFIG:
            field = item["key"]
            nilai = get_cell(row, mapping.get(field))
            if item["type"] == "number":
                nilai = parse_number(nilai)
            nilai_field.append(nilai)

        if source_col is not None:
            sumber = get_cell(row, source_col) or source_label
        else:
            sumber = source_label

        if wbs_col is not None:
            wbs = get_cell(row, wbs_col) or kode_wbs
        else:
            wbs = kode_wbs

        # Dua kolom metadata + semua field dinamis.
        rekap_data.append([sumber, wbs, *nilai_field])

    jumlah_kolom_diharapkan = len(KOLOM_REKAP)
    baris_tidak_sesuai = [
        (idx + 1, len(row))
        for idx, row in enumerate(rekap_data)
        if len(row) != jumlah_kolom_diharapkan
    ]
    if baris_tidak_sesuai:
        raise ValueError(
            f"Ada {len(baris_tidak_sesuai)} baris dengan jumlah nilai tidak "
            f"sesuai. Seharusnya {jumlah_kolom_diharapkan} kolom. "
            f"Contoh: {baris_tidak_sesuai[:5]}"
        )

    df_rekap = pd.DataFrame(rekap_data, columns=KOLOM_REKAP)

    fields = []
    for item in KOLOM_CONFIG:
        col_idx = mapping.get(item["key"])
        fields.append({
            "Field": item["label"],
            "Tipe": item["type"],
            "Kolom": str(col_idx + 1) if col_idx is not None else "-",
            "Skor": (
                str(hasil["field_scores"].get(item["key"], 0))
                if col_idx is not None
                else "-"
            ),
            "Header": header_columns[col_idx] if col_idx is not None else "-",
        })

    report = {
        "source_label": source_label,
        "header_start_row": hasil["row_idx"] + 1,
        "header_end_row": hasil["end_row_idx"] + 1,
        "header_row_count": hasil["jumlah_baris"],
        "detected_fields": sum(v is not None for v in mapping.values()),
        "total_fields": len(KOLOM_CONFIG),
        "missing_fields": [
            item["label"] for item in KOLOM_CONFIG if mapping.get(item["key"]) is None
        ],
        "fields": fields,
        # "" bila kode WBS diambil dari kolom CSV (nilai per baris).
        "wbs": "" if wbs_col is not None else kode_wbs,
        "wbs_from_column": wbs_col is not None,
        "source_from_column": source_col is not None,
        "rows_extracted": len(rekap_data),
        "rows_skipped_empty": rows_skipped_empty,
        "rows_skipped_ignored": rows_skipped_ignored,
    }

    return df_rekap, report


def rekap_from_bytes(raw: bytes, filename: str = ""):
    """Rekap file CSV yang di-upload (in-memory). Mengembalikan (df, report)."""
    grid = split_rows(raw)

    if filename:
        source_label = Path(filename).stem
    else:
        source_label = "CSV"

    return rekap_grid(grid, source_label)
