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
        "keywords": ["qty pr total", "total pr", "total"],
        "threshold": 70,
        "type": "number",
    },
    {
        "key": "bom",
        "label": "BOM",
        "keywords": ["bom", "bill of materials", "group"],
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

# Dua kolom metadata yang SELALU ada di hasil rekap, apa pun modenya:
# provenance baris (sheet sumber) dan kode WBS (dari baris metadata
# atau kolom sheet). Keduanya jadi kunci agregasi/merge di tahap berikut.
KOLOM_METADATA = ["Nama Sheet Sumber", "WBS Elem"]

KOLOM_REKAP = [
    *KOLOM_METADATA,
    *[item["label"] for item in KOLOM_CONFIG],
]

# Header kolom opsional yang dipakai per baris bila tersedia di CSV.
SHEET_SOURCE_HEADERS = {
    "nama sheet sumber", "sheet sumber", "sumber sheet", "nama sheet", "source sheet",
}
WBS_HEADERS = {
    "wbs elem", "wbs element", "wbs elemen", "kode wbs", "wbs code", "wbs",
}

# Label blok metadata ekspor SAP BOM (baris 1..5: judul lalu lima label
# ini, berurutan). Bukan nilai — kolom nilai di sebelahnya bisa kosong.
# Dipakai ambil_kode_wbs() supaya label ini tidak tertukar jadi 'kode'.
LABEL_METADATA_SAP = {
    "nomor proyek", "nama proyek", "tanggal terbit", "revisi", "halaman",
    "nomor dokumen", "dibuat oleh", "diperiksa", "disetujui",
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
# 2b. DAFTAR 29 NAMA SHEET (algoritma asli / Colab)
# ============================================================

# Daftar nama sheet yang dulu menjadi cakupan rekap skrip asli
# (recap.py / notebook Google Colab). SEJAK 2026-10-20 daftar ini bukan
# lagi filter: semua sheet yang dipilih user ikut direkap (lihat
# material_planning_2.rekap_sumber_bom). Fungsinya sekarang sekadar
# normalisasi NAMA — sheet Excel terpotong (batas 31 karakter) dipetakan
# ke nama penuhnya lewat target_sheet(), supaya label 'Nama Sheet Sumber'
# konsisten dengan hasil Colab.
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
    """Salah satu dari 29 nama target yang cocok dengan `nama`, atau None.

    Bukan filter rekap — bila None, pemakai memakai `nama` sheet apa
    adanya (lihat material_planning_2.rekap_sumber_bom).
    """
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
    """True bila `nama` cocok dengan salah satu dari 29 nama target."""
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
# 3b. PROPAGASI SEL MERGED (perbaikan header induk yang hilang)
# ============================================================

# pandas membaca sel merged HANYA di sel anchor-nya, sehingga header induk
# yang membentang lintas kolom (mis. '-TS9' pada AX6:BJ6 di sheet Komponen)
# hilang untuk kolom lain di rentang itu. Tanpa langkah ini sub-kolom
# 'TINDAK LANJUT' di bawah TS9..TS16 terbaca identik — delapan kolom dengan
# nama persis sama, tidak mungkin dibedakan.

def propagate_merged(grid, merged, mulai_baris=None, sampai_baris=None):
    """Kembalikan salinan `grid` dengan sel merged diisi ke seluruh rentangnya.

    merged = [(r1, c1, r2, c2), ...] koordinat 1-based inklusif (dari
    excel_convert.read_excel_sheets). mulai_baris & sampai_baris = batas
    baris 0-based inklusif: PROPAGASI DIBATASI BLOK HEADER SAJA.

    Pembatasan itu sengaja, dan batas ATAS sama pentingnya dengan bawah:

    - banyak sheet juga memakai merged pada AREA DATA (mis. 'B289:B304'
      pada sheet Komponen). Mengisinya akan mereplikasi nilai ke baris
      yang semula kosong dan membuat baris data palsu;
    - sheet juga memakai merged judul yang membentang ke BAWAH baris
      metadata (mis. 'G1:U5' = 'BILL OF MATERIAL' pada sheet Komponen).
      Bila ikut terisi, baris metadata itu terbaca berisi dan kode WBS
      yang diambil ambil_kode_wbs menjadi sampah ('BILL OF MATERIAL').

    Yang dibutuhkan rekap hanya konteks header, jadi cukup persis blok
    header. Baris di luar rentang merged dipakai ulang apa adanya (tanpa
    disalin).
    """
    if not merged:
        return grid

    salinan = {}

    def baris(idx):
        if idx not in salinan:
            salinan[idx] = list(grid[idx])
        return salinan[idx]

    for r1, c1, r2, c2 in merged:
        r1, c1, r2, c2 = r1 - 1, c1 - 1, r2 - 1, c2 - 1
        if r1 < 0 or r1 >= len(grid) or r1 > r2:
            continue
        if sampai_baris is not None and r1 > sampai_baris:
            continue

        # Nilai anchor; rentang yang anchor-nya kosong tidak diapa-apakan.
        # Bila anchor di LUAR blok header (mis. judul di baris 1), rentang
        # tidak diisi sama sekali — nilainya memang bukan header.
        if c1 < 0 or c1 >= len(grid[r1]):
            continue
        if mulai_baris is not None and r1 < mulai_baris:
            continue
        nilai = grid[r1][c1]
        if not str(nilai).strip():
            continue

        # Rentang dipotong agar tidak melewati batas akhir blok header.
        batas_r = r2 if sampai_baris is None else min(r2, sampai_baris)

        for r in range(r1, min(batas_r, len(grid) - 1) + 1):
            row = baris(r)
            for c in range(max(c1, 0), min(c2, len(row) - 1) + 1):
                if not str(row[c]).strip():
                    row[c] = nilai

    hasil = list(grid)
    for idx, row in salinan.items():
        hasil[idx] = row
    return hasil


# ============================================================
# 3c. NAMA KOLOM DINAMIS (rekap mode "dinamis")
# ============================================================

# Penulisan label TS berbeda antar sheet: 'TS9', 'TS 9', '-TS9', 'TS10',
# 'TS 10', bahkan 'TS 9-16'. Tanpa penyamaan, kolom semantik yang sama
# dari dua sheet berbeda menjadi dua kolom terpisah setelah pd.concat.
# IGNORECASE wajib: label di sheet memakai huruf besar ('TS 9'), dan
# pola tanpa itu tidak akan cocok sama sekali.
_TS_TUNGGAL = re.compile(r"^[\s\-–—]*ts\s*0*(\d+)\s*$", re.IGNORECASE)
_TS_RENTANG = re.compile(
    r"^[\s\-–—]*ts\s*0*(\d+)\s*[-–—]\s*0*(\d+)\s*$", re.IGNORECASE
)


def rapikan_label_ts(label) -> str:
    """Seragamkan penulisan label TS: 'TS 9', '-TS9', 'ts9' -> 'TS9'.

    Label non-TS dikembalikan apa adanya (rapikan spasi/newline saja).
    """
    text = re.sub(r"\s+", " ", str(label).replace("\n", " ")).strip()

    match = _TS_RENTANG.match(text)
    if match:
        return f"TS{int(match.group(1))}-{int(match.group(2))}"

    match = _TS_TUNGGAL.match(text)
    if match:
        return f"TS{int(match.group(1))}"

    return text


def kolom_dinamis(header_columns, dikecualikan=()):
    """Bangun nama kolom unik dari path header tiap kolom sumber.

    Mengembalikan [(col_idx, nama, dialias), ...]. Nama = bagian-bagian
    header (parent || child) yang sudah dirapikan dan di-dedupe, mis. kolom
    AZ di sheet Komponen menjadi '-TS9 || TINDAK LANJUT'. Nama kosong
    diberi penanda posisi; nama yang sama muncul lebih dari sekali diberi
    sufiks ' (2)', ' (3)', ... supaya tetap bisa dirujuk.

    Nama hasil lalu lewat terapkan_alias() — langkah PENYERAGAMAN NAMA
    yang membuat kolom beda-namun-semakna antar sheet menyatu saat
    pd.concat (lihat 3d). `dialias` True bila langkah itu benar-benar
    mengubah nama, dipakai report supaya rename bisa diaudit.

    dikecualikan = indeks kolom yang TIDAK ikut sebagai kolom data (kolom
    'Nama Sheet Sumber' / 'WBS Elem' yang nilainya sudah diambil sebagai
    metadata, supaya tidak dobel).
    """
    hasil = []
    dipakai = {}

    for idx, text in enumerate(header_columns):
        if idx in dikecualikan:
            continue

        bagian = [rapikan_label_ts(p) for p in str(text).split("||")]
        bagian = [p for p in bagian if p]
        # dict.fromkeys = dedupe sambil mempertahankan urutan
        nama_awal = " || ".join(dict.fromkeys(bagian)) or f"Kolom {idx + 1}"
        nama = terapkan_alias(nama_awal)
        dialias = nama != nama_awal

        nomor = dipakai.get(nama, 0) + 1
        dipakai[nama] = nomor
        if nomor > 1:
            nama = f"{nama} ({nomor})"

        hasil.append((idx, nama, dialias))

    return hasil


# ============================================================
# 3d. ALIAS KOLOM — PENYERAGAMAN NAMA, BUKAN SELEKSI
# ============================================================
# Bedanya dari KOLOM_CONFIG: alias TIDAK menentukan kolom mana yang
# diambil (mode dinamis tetap mengambil SEMUA kolom sheet). Alias hanya
# MENGUBAH NAMA supaya kolom yang semantiknya sama tapi ditulis beda
# antar sheet menyatu saat pd.concat. Menambah alias tidak pernah
# menghilangkan data dari hasil rekap.
#
# Semua isi daftar ini TURUNAN DARI AUDIT DATA (file LIST DESCOPING KCI,
# 2026-10-21) — bukan tebakan. Kandidat yang terbukti salah sengaja tidak
# masuk; daftarnya dicatat di bawah sebagai catatan audit.
#
# Kunci memakai normalize_text() (huruf kecil, tanda baca -> spasi)
# supaya peka-tidak terhadap huruf besar, spasi ganda, dan newline.

ALIAS_KOLOM = {
    # Komponen punya dua kolom satuan yang ISI-NYA IDENTIK sel demi sel
    # (738/738, terverifikasi). 'SATUAN' adalah duplikatnya.
    "satuan": "QUANTITY || UNIT",
}

# Kandidat yang DITOLAK berdasar bukti, supaya tidak ditambahkan lagi
# tanpa audit ulang:
#   - 'TOTAL || TS9-16' (Komponen) vs 'TOTAL PR' (CPF): TERBUKTI beda.
#     Yang pertama = jumlah KEBUTUHAN/TS TS9..TS16 (sum 290.412, identik
#     dengan jumlah kolom KEBUTUHAN/TS), bukan jumlah PR.
#   - 'TSn || QTY' (CPF) vs 'TSn || QTY PR (VE)'/'(MS)' (Komponen):
#     TIDAK JELAS. CPF punya satu track PR, Komponen dua (vendor
#     eksisting & mitra strategis) — memilih salah satu adalah tebakan.
#   - 'UOM' (CPF): berisi 36 nilai (BT, CRD, ROLL) di baris yang pada
#     kolom UNIT berbeda — kolom sisa, bukan padanan.
#   - 'KODE MATERIAL IMS' vs 'KODE MATERIAL SUBTITUSI': makna berbeda.

ALIAS_POLA = [
    # Blok per-TS: Komponen menulis 'DELIV DATE SAP', CPF menulis
    # 'DELIV DATE' — sama-sama tanggal kirim per TS, posisinya juga sama
    # (langsung setelah NO PO). Pola dipakai karena mengulang untuk
    # TS9..TS16 (8 kolom per sheet).
    (
        re.compile(r"^(TS\d+) \|\| DELIV DATE SAP$", re.IGNORECASE),
        r"\1 || DELIV DATE",
    ),
]


def terapkan_alias(nama: str) -> str:
    """Kembalikan nama kanonis bila `nama` memuat alias, selain itu nama.

    Dijalankan per kolom di kolom_dinamis, SEBELUM penomoran '(2)', jadi
    dua kolom yang memetakan ke nama kanonis yang sama otomatis ditandai
    sebagai duplikat nyata — bukan bentrok yang hilang tanpa jejak.
    """
    kunci = normalize_text(nama)
    if kunci:
        kanonis = ALIAS_KOLOM.get(kunci)
        if kanonis is not None:
            return kanonis

    for pola, ganti in ALIAS_POLA:
        if pola.match(nama):
            return pola.sub(ganti, nama, count=1)

    return nama


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


def adalah_angka(text) -> bool:
    """True bila teks seluruhnya terbaca sebagai angka (bukan sebagian).

    Berbeda dari parse_number yang MEMAKSA angka keluar dari teks apa pun
    ('12 pcs' -> 12.0), fungsi ini menolak teks campuran sehingga aman
    dipakai untuk menyimpulkan TIPE kolom pada rekap dinamis: tanggal
    '21.08.2026' dan kode 'B52TH02054' harus tetap teks.
    """
    text = str(text).strip()
    if not text or text.upper() in INVALID_VALUES:
        return False
    try:
        float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return False
    return True


def simpulkan_tipe_kolom(seri) -> bool:
    """True bila kolom berisi angka saja (kosong diabaikan).

    Kolom yang seluruh isinya kosong dianggap teks supaya tidak
    menghasilkan kolom numerik penuh nol yang menyesatkan.
    """
    terisi = [v for v in seri if str(v).strip()]
    return bool(terisi) and all(adalah_angka(v) for v in terisi)


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

    # Baris 2 berpola eksplisit: 'Kode WBS: A-1.10' / 'WBS - A-1.10'
    for value in values:
        match = re.search(r"\bwbs\b\s*[:\-]?\s*(.+)$", value, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()

    # Baris 2 berpola label-sendiri: sel 'WBS', nilainya di sel sesudahnya
    for idx, value in enumerate(row_wbs):
        if normalize_text(value) == "wbs":
            for next_value in row_wbs[idx + 1:]:
                next_value = str(next_value).strip()
                if next_value:
                    return next_value

    # Fallback terakhir: baris 2 tanpa pola WBS sama sekali.
    #
    # JANGAN mengembalikan nilai pertama apa adanya. Blok metadata ekspor
    # SAP BOM berbentuk 'Label | : | nilai', dan pada file yang kolom
    # nilainya tidak diisi, seluruh baris 2 hanyalah LABEL — balasan apa
    # adanya membuat 'WBS Elem' terisi "Nama Proyek". Dampaknya dua:
    # merge dengan SAP (left_on Kode Material + WBS Elem) tidak akan
    # pernah match, DAN peringatan "Kode WBS tidak ditemukan" di UI hilang
    # karena nilai dianggap non-kosong. Kembalikan "" supaya peringatan
    # itu tetap muncul dan pengguna tahu kodenya memang absen.
    #
    # Pemisah ':' juga dibuang — sheet seperti 'Consumable Painting &
    # Finishing' menulis 'Nama Proyek | : | :' sehingga tanpa penyaringan
    # ini ':' akan lolos sebagai 'kode'.
    kandidat = [
        v
        for v in values
        if normalize_text(v) not in LABEL_METADATA_SAP
        and not re.fullmatch(r"[:\-|=]+", v)
    ]
    return kandidat[0] if kandidat else ""


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

def rekap_grid(
    data,
    source_label: str = "",
    abaikan: dict = None,
    merged: list = None,
    mode: str = "dinamis",
    wbs_manual: str = "",
):
    """Rekap satu grid sel. Mengembalikan (DataFrame, report).

    MODE (default "dinamis", sejak 2026-10-21):

    - "dinamis" -> kolom hasil = SEMUA kolom yang ada pada sheet, dinamai
      dari path header-nya (parent || child, lihat kolom_dinamis).
      Tidak ada daftar kolom yang dikonfigurasi di kode, jadi sheet baru
      dengan tata letak berbeda ikut terbaca lengkap — termasuk sub-kolom
      per TS (TS9..TS16) yang sebelumnya tidak tersentuh. Kolom yang
      tidak dimiliki sebuah sheet ikut sebagai kolom kosong saat beberapa
      frame digabung (pd.concat).
    - "schema" -> perilaku lama: ekstraksi terbatas 11 field di
      KOLOM_CONFIG, selalu menghasilkan KOLOM_REKAP yang tetap.

    merged = rentang sel merged (dari excel_convert.read_excel_sheets).
    Dipakai untuk MENGEMBALIKAN header induk yang hilang: pandas hanya
    membaca nilai sel merged pada anchor-nya, sehingga sub-kolom di bawah
    group TS tidak bisa dibedakan. Propagasi dibatasi blok header saja
    (lihat propagate_merged).

    abaikan = {baris 0-based: [alasan, ...]} — baris yang ditandai orang di
    file Excel (tersembunyi/collapse, dicoret, blok gelap) sehingga tidak
    boleh ikut rekap (lihat excel_convert.read_excel_sheets). Hanya baris
    DATA yang dibuang; baris header tetap dipakai untuk deteksi header.
    Tiap baris yang dilewati karena tanda tercatat di report
    (rows_skipped_marked + mark_summary) supaya bisa diaudit.

    wbs_manual = kode WBS buatan pengguna (input teks di UI, lihat
    material_planning_2.render_pipeline). Dipakai HANYA bila deteksi
    otomatis gagal TOTAL: sheet tidak memuat kolom 'WBS Elem' DAN baris
    metadata tidak memuat kode (ambil_kode_wbs -> ""). Kasus nyata file
    LIST DESCOPING KCI (2026-10-21): baris metadata SAP-nya kosong semua
    sehingga merge dengan data SAP tidak akan pernah match tanpa kode.
    Kode yang terdeteksi SELALU didahulukan — wbs_manual tidak pernah
    menimpa nilai yang ada, termasuk sel kosong di kolom 'WBS Elem'
    sebuah sheet (sumber nilai per baris tetap milik file). Asalnya
    tercatat di report['wbs_sumber'] ('kolom' | 'deteksi' | 'manual' | '')
    supaya UI bisa menampilkan provenance, bukan seolah-olah kodenya
    berasal dari file.

    File CSV tidak punya format sel -> abaikan/merged None (perilaku lama
    persis).
    """
    if not data:
        raise ValueError("File CSV tidak memiliki baris data.")
    if mode not in ("dinamis", "schema"):
        raise ValueError(
            f"Mode rekap tidak dikenal: {mode!r} (pakai 'dinamis' atau 'schema')."
        )

    # Normalisasi: terima dict alasan, set/list indeks polos, atau None.
    if not abaikan:
        abaikan = {}
    elif not isinstance(abaikan, dict):
        abaikan = {int(idx): [] for idx in abaikan}
    else:
        abaikan = {
            int(idx): (
                [alasan] if isinstance(alasan, str) else list(alasan)
            )
            for idx, alasan in abaikan.items()
        }

    hasil = deteksi_header(data, max_scan=100, max_header_rows=5)

    if hasil is None:
        raise ValueError(
            "Header BOM tidak ditemukan. Pastikan file memiliki kolom seperti "
            "'Kode Material' atau 'Material / Komponen'."
        )

    # ------------------------------------------------------------
    # PROPAGASI SEL MERGED PADA BLOK HEADER.
    # Deteksi header jalan dulu di grid mentah supaya hasilnya tidak
    # berubah oleh merged pada AREA DATA; setelah blok header ketemu,
    # sel merged di dalamnya diisi ulang lalu mapping dihitung ulang.
    # ------------------------------------------------------------
    if merged:
        data = propagate_merged(
            data,
            merged,
            mulai_baris=hasil["row_idx"],
            sampai_baris=hasil["end_row_idx"],
        )
        header_columns = gabungkan_header(
            data, hasil["row_idx"], hasil["jumlah_baris"]
        )
        mapping_ulang, scores_ulang, kandidat_ulang = cari_mapping_header(
            header_columns
        )
        hasil = {
            **hasil,
            "mapping": mapping_ulang,
            "field_scores": scores_ulang,
            "kandidat_field": kandidat_ulang,
            "header_columns": header_columns,
        }

    mapping = hasil["mapping"]
    header_columns = hasil["header_columns"]

    if mode == "schema" and mapping.get("kode") is None:
        raise ValueError(
            "Kolom 'Kode Material' tidak terdeteksi pada header file. "
            "Kolom ini wajib karena menjadi kunci merge dengan data SAP. "
            "Periksa baris header (maksimal 5 baris digabung)."
        )
    source_col = find_header_column(header_columns, SHEET_SOURCE_HEADERS)
    wbs_col = find_header_column(header_columns, WBS_HEADERS)

    # ambil_kode_wbs() SELALU membaca data[1] sebagai blok metadata —
    # padahal baris itu hanya metadata bila header mulai di indeks >= 2.
    # Bila header di indeks 0/1, data[1] adalah baris DATA atau baris
    # HEADER sendiri: fallback-nya lolos penyaringan dan menangkap sampah
    # ('1000123' dari sel Kode Material baris data / 'Kode Material' dari
    # header) yang lalu disuntikkan ke 'WBS Elem' SE tiap baris — kode
    # material menyamar jadi WBS, tanpa peringatan apa pun. Jadi: blok
    # metadata hanya dipakai kalau memang ada di atas header.
    kode_wbs_terdeteksi = (
        ambil_kode_wbs(data) if hasil["row_idx"] >= 2 else ""
    )
    kode_wbs = kode_wbs_terdeteksi

    # Kode WBS manual dari pengguna: pengganti BILA deteksi gagal saja —
    # dan hanya bila sheet TIDAK memuat kolom 'WBS Elem'. Sheet yang punya
    # kolom itu dianggap sudah punya sumber nilai sendiri per baris; sel
    # kosong di dalamnya TIDAK disuntik kode manual (kode file menang
    # utuh, termasuk kekosongannya).
    wbs_manual = (wbs_manual or "").strip()
    if wbs_col is None and not kode_wbs and wbs_manual:
        kode_wbs = wbs_manual

    # ------------------------------------------------------------
    # DAFTAR KOLOM HASIL.
    # Mode dinamis: semua kolom sheet ikut, dinamai dari path header-nya.
    # Kolom sumber 'Nama Sheet Sumber' / 'WBS Elem' dikeluarkan karena
    # nilainya sudah diambil sebagai metadata (tidak boleh dobel).
    # ------------------------------------------------------------
    if mode == "dinamis":
        dikecualikan = {c for c in (source_col, wbs_col) if c is not None}
        kolom_hasil = kolom_dinamis(header_columns, dikecualikan=dikecualikan)

        # 'Kode Material' adalah kunci inner-join dengan data SAP, jadi
        # kolomnya dijamin SELALU ada — kosong bila sheet tidak memuatnya.
        # Tanpa ini clean_bom menolak hasilnya dan tahap merge gagal.
        sudah_ada = {
            nama.casefold() for _, nama, _ in kolom_hasil
        }
        if "kode material" not in sudah_ada:
            kolom_hasil.insert(0, (None, "Kode Material", False))
    else:
        kolom_hasil = [
            (mapping.get(item["key"]), item["label"], False)
            for item in KOLOM_CONFIG
        ]

    daftar_kolom = [
        *KOLOM_METADATA, *[nama for _, nama, _ in kolom_hasil]
    ]

    data_start_idx = hasil["end_row_idx"] + 1
    # Filter baris data: utamanya kolom Kode Material / Kode Material
    # Delete — baris judul seksi dan subtotal tidak memuat kode, jadi
    # memang tidak boleh ikut rekap. Bila SHEET TIDAK MEMUAT KEDUANYA
    # (boleh terjadi pada mode dinamis), kriteria jatuh ke "ada isi di
    # kolom mana pun" supaya sheet tetap terbaca, bukan diam-diam
    # menghasilkan nol baris.
    filter_lewat_kode = (
        mapping.get("kode") is not None
        or mapping.get("kode_delete") is not None
    )

    rekap_data = []
    rows_skipped_empty = 0
    rows_skipped_ignored = 0
    rows_skipped_marked = 0
    mark_summary = {}

    for offset, row in enumerate(data[data_start_idx:]):
        # Baris ditandai di Excel (hidden/collapse, dicoret, blok gelap)
        # tidak ikut rekap — ini inti fitur "lewati baris berformat".
        alasan_tanda = abaikan.get(data_start_idx + offset)
        if alasan_tanda:
            rows_skipped_marked += 1
            # Satu baris dihitung satu alasan (yang pertama) supaya total
            # alasan selalu = jumlah baris ditandai.
            mark_summary[alasan_tanda[0]] = (
                mark_summary.get(alasan_tanda[0], 0) + 1
            )
            continue

        kode_material = get_cell(row, mapping.get("kode"))
        kode_material_delete = get_cell(row, mapping.get("kode_delete"))

        if filter_lewat_kode:
            if not kode_material and not kode_material_delete:
                rows_skipped_empty += 1
                continue
        elif not any(str(v).strip() for v in row):
            rows_skipped_empty += 1
            continue
        if kode_material.lower() in KATA_ABAIKAN:
            rows_skipped_ignored += 1
            continue
        if kode_material_delete.lower() in KATA_ABAIKAN:
            rows_skipped_ignored += 1
            continue

        # Mode dinamis: nilai diambil apa adanya per kolom sheet; tipe
        # kolom disimpulkan SETELAH semua baris terkumpul (lihat bawah).
        # Mode schema: nilai dibuat sesuai urutan KOLOM_CONFIG + tipenya.
        if mode == "dinamis":
            nilai_field = [
                get_cell(row, col) for col, _, _ in kolom_hasil
            ]
        else:
            nilai_field = []
            for item in KOLOM_CONFIG:
                nilai = get_cell(row, mapping.get(item["key"]))
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

        # Dua kolom metadata + semua kolom hasil.
        rekap_data.append([sumber, wbs, *nilai_field])

    jumlah_kolom_diharapkan = len(daftar_kolom)
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

    df_rekap = pd.DataFrame(rekap_data, columns=daftar_kolom)

    # Mode dinamis: kolom yang seluruh isinya angka dijadikan numerik
    # supaya agregasi/merge di tahap berikut tetap bekerja. Disimpulkan
    # dari isi, bukan dari header, karena nama kolom sheet bervariasi.
    #
    # SEL KOSONG TIDAK DIUBAH JADI 0: '' berarti 'tidak ada nilai', dan
    # mengubahnya menjadi nol merusak makna kolom kode opsional —
    # 'Kode Material Delete' = 0 dianggap terisi sehingga aggregate_bom
    # (yang menyaring lewat .isna()) membuang SEMUA baris. Kosong tetap
    # kosong (NaN); pandas 'sum' memang melewati NaN, jadi agregasi
    # tetap menghasilkan angka yang benar.
    #
    # Kolom KODE DIKECUALIKAN dari inferensi numerik: kode material
    # ('1000123', '1000999') memang berupa digit, tapi ia IDENTITAS
    # barang, bukan kuantitas — dijadikan float ia kehilangan bentuk
    # aslinya dan bisa tertukar dengan angka sungguhan saat merge.
    if mode == "dinamis" and not df_rekap.empty:
        for _, nama, _ in kolom_hasil:
            if "kode" in normalize_text(nama):
                continue
            if simpulkan_tipe_kolom(df_rekap[nama]):
                df_rekap[nama] = df_rekap[nama].map(
                    lambda v: parse_number(v) if str(v).strip() else float("nan")
                )

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

    # Kolom hasil mode dinamis + tipenya (informatif, dipakai UI).
    kolom_report = []
    for col_idx, nama, dialias in kolom_hasil:
        kolom_report.append({
            # None bila kolom di-inject (tidak bersumber dari sheet)
            "Kolom": str(col_idx + 1) if col_idx is not None else "-",
            "Nama": nama,
            "Header": header_columns[col_idx] if col_idx is not None else "-",
            # 'ya' bila nama diganti oleh ALIAS_KOLOM/ALIAS_POLA — jejak
            # audit supaya rename tidak tersembunyi dari pengguna.
            "Alias": "ya" if dialias else "",
            "Tipe": (
                "number"
                if nama in df_rekap.columns
                and pd.api.types.is_numeric_dtype(df_rekap[nama])
                else "text"
            ),
        })

    report = {
        "mode": mode,
        "source_label": source_label,
        "header_start_row": hasil["row_idx"] + 1,
        "header_end_row": hasil["end_row_idx"] + 1,
        "header_row_count": hasil["jumlah_baris"],
        "detected_fields": sum(v is not None for v in mapping.values()),
        "total_fields": len(KOLOM_CONFIG),
        # Mode dinamis tidak kehilangan kolom apa pun (semua kolom sheet
        # ikut), jadi daftar 'hilang' hanya berarti untuk mode schema.
        "missing_fields": (
            []
            if mode == "dinamis"
            else [
                item["label"]
                for item in KOLOM_CONFIG
                if mapping.get(item["key"]) is None
            ]
        ),
        "fields": fields,
        # Rincian kolom hasil — jumlah & nama kolom yang benar-benar keluar.
        "columns": kolom_report,
        "columns_total": len(kolom_hasil),
        # 'Kode Material' jadi kunci merge dengan data SAP; mode dinamis
        # tetap mengeluarkannya (kosong bila sheet tidak memuatnya).
        "kode_terdeteksi": mapping.get("kode") is not None,
        # "" bila kode WBS diambil dari kolom CSV (nilai per baris).
        "wbs": "" if wbs_col is not None else kode_wbs,
        "wbs_from_column": wbs_col is not None,
        # Asal kode WBS: 'kolom' = per baris dari sheet/CSV, 'deteksi' =
        # baris metadata file, 'manual' = input pengguna (wbs_manual), ""
        # = tidak ada sama sekali -> peringatan di UI.
        "wbs_sumber": (
            "kolom"
            if wbs_col is not None
            else (
                "deteksi"
                if kode_wbs_terdeteksi
                else ("manual" if kode_wbs else "")
            )
        ),
        "source_from_column": source_col is not None,
        "rows_extracted": len(rekap_data),
        "rows_skipped_empty": rows_skipped_empty,
        "rows_skipped_ignored": rows_skipped_ignored,
        # Baris ditandai di Excel yang sengaja tidak ikut rekap + alasan
        # pertamanya (per baris dihitung sekali -> jumlah = rows_skipped_marked).
        "rows_skipped_marked": rows_skipped_marked,
        "mark_summary": mark_summary,
    }

    return df_rekap, report


def rekap_from_bytes(raw: bytes, filename: str = "", wbs_manual: str = ""):
    """Rekap file CSV yang di-upload (in-memory). Mengembalikan (df, report).

    wbs_manual diteruskan ke rekap_grid (kode WBS buatan pengguna bila
    deteksi otomatis gagal — lihat docstring rekap_grid)."""
    grid = split_rows(raw)

    if filename:
        source_label = Path(filename).stem
    else:
        source_label = "CSV"

    return rekap_grid(grid, source_label, wbs_manual=wbs_manual)
