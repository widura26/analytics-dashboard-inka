#tambahan kolom : Stock total,
import gspread
import pandas as pd
import re

client = gspread.service_account(filename='credentials.json')
nama_file_sheets = "bom_dataset"
spreadsheet = client.open(nama_file_sheets)

# --- LIST LENGKAP ALL 29 TARGET SHEET ---
DAFTAR_SHEET_TARGET = [
    "Elektrik + Pre Series", "De-Scoping", "Interior", "TMS", "CKD VVVF",
    "CKD Pantograph", "CKD SIV", "Elektrik", "Komponen Utama", "Sistem Mekanik",
    "Raw Material Interior", "Bogie KIT", "Raw Material Bogie", "Realisasi Raw Material",
    "Carbody", "Fastening Mekanik", "Fastening Bogie", "Fastening Interior",
    "Crashwrothiness", "Welding", "Welding Bogie", "Consumable Tools",
    "Consumable Series", "Welding WS BWI", "Jig Tools 6TS Pelokalan", "jig Tool",
    "Tools", "Tools & Consumable Tools Perbaikan", "Consumable Pre Series"
]

semua_tab = spreadsheet.worksheets()
sheet_names_aktual = [sheet.title for sheet in semua_tab]

def normalize_text(text):
    """
    Normalisasi teks supaya perbedaan:
    - huruf besar/kecil
    - spasi
    - slash
    - underscore
    - tanda baca

    tidak terlalu berpengaruh.
    """
    if text is None:
        return ""

    text = str(text).strip().lower()

    # Hilangkan karakter non alphanumeric
    text = re.sub(r'[^a-z0-9]+', ' ', text)

    # Hilangkan spasi berlebih
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def normalize_compact(text):
    """
    Contoh:
    'Kode Material'       -> 'kodematerial'
    'Kode-Material'       -> 'kodematerial'
    'kode_material'       -> 'kodematerial'
    """
    return re.sub(r'[^a-z0-9]', '', normalize_text(text))


def cari_sheet_aktual(nama_target):

    target = normalize_text(nama_target)
    target_compact = normalize_compact(nama_target)

    # Exact normalized
    for actual in sheet_names_aktual:
        if normalize_text(actual) == target:
            return actual

    # Compact
    for actual in sheet_names_aktual:
        if normalize_compact(actual) == target_compact:
            return actual

    return None


target_sheets_matched = []

for nama_target in DAFTAR_SHEET_TARGET:

    matched = cari_sheet_aktual(nama_target)

    if matched and matched not in target_sheets_matched:
        target_sheets_matched.append(matched)


print(
    f"[INFO] Memetakan "
    f"{len(target_sheets_matched)} dari "
    f"{len(DAFTAR_SHEET_TARGET)} target sheet.\n"
)


# ============================================================
# 4. MASTER HEADER
# ============================================================

KATA_KUNCI = {

    "kode": [
        "kode material",
        "kode",
        "material code",
        "material kode",
        "item code",
        "part number",
        "part no",
        "part no."
    ],

    "kode_stock": [
        "kode material stock",
        "kode material stok",
        "material stock code",
        "stock material code",
        "stock code",
        "kode stock",
        "kode stok"
    ],

    "nama": [
        "material / komponen",
        "material/komponen",
        "deskripsi material"
    ],

    "spek": [
        "spesifikasi",
        "specification",
        "spec",
        "detail spesifikasi",
        "SPESIFIKASI"
    ],

    "qty": [
        "qty / ts (series)",
        "qty/ts (series)",
        "qty ts series",
        "qty ts",
        "qty series",
        "quantity series"
    ],

    "uom": [
        "uom/car",
        "uom / car",
        "uom",
        "unit",
        "satuan"
    ],

    "qty_allowance": [
        "qty total (series)",
        # "qty total series",
        # "qty total",
        # "total qty",
        # "quantity total"
    ],

    "qty_pr_total": [
        "qty pr total",
        "qty pr",
        "pr total",
        "quantity pr total"
    ]
}


# ============================================================
# 5. DETEKSI HEADER DALAM SATU BARIS
# ============================================================

def cari_header_di_baris(row):

    hasil = {
        "kode": None,
        "kode_stock": None,
        "nama": None,
        "spek": None,
        "qty": None,
        "uom": None,
        "qty_allowance": None,
        "qty_pr_total": None
    }

    skor = 0

    for col_idx, cell in enumerate(row):

        cell_normalized = normalize_text(cell)
        cell_compact = normalize_compact(cell)

        if not cell_normalized:
            continue

        for field, keywords in KATA_KUNCI.items():

            # Jangan overwrite kalau sudah ketemu
            if hasil[field] is not None:
                continue

            for keyword in keywords:

                keyword_normalized = normalize_text(keyword)
                keyword_compact = normalize_compact(keyword)

                # Exact
                if cell_normalized == keyword_normalized:
                    hasil[field] = col_idx
                    skor += 3
                    break

                # Compact
                if cell_compact == keyword_compact:
                    hasil[field] = col_idx
                    skor += 3
                    break

                # Contains untuk header yang lebih panjang
                if (
                    len(keyword_normalized) >= 5
                    and keyword_normalized in cell_normalized
                ):
                    hasil[field] = col_idx
                    skor += 1
                    break

    return hasil, skor


# ============================================================
# 6. DETEKSI HEADER TERBAIK
# ============================================================

def deteksi_header(data, max_scan=100):

    kandidat = []

    jumlah_baris = min(max_scan, len(data))

    for row_idx in range(jumlah_baris):

        row = data[row_idx]

        hasil, skor = cari_header_di_baris(row)

        jumlah_field = sum(
            1 for value in hasil.values()
            if value is not None
        )

        # Minimal harus punya Kode atau Nama
        punya_kode = hasil["kode"] is not None
        punya_nama = hasil["nama"] is not None

        if punya_kode or punya_nama:

            kandidat.append({
                "row_idx": row_idx,
                "mapping": hasil,
                "score": skor,
                "jumlah_field": jumlah_field
            })

    if not kandidat:
        return None

    # Prioritaskan jumlah header yang ditemukan,
    # kemudian skor
    kandidat.sort(
        key=lambda x: (
            x["jumlah_field"],
            x["score"]
        ),
        reverse=True
    )

    return kandidat[0]


# ============================================================
# 7. EKSTRAKSI ANGKA
# ============================================================

def parse_number(value):

    if value is None:
        return 0.0

    text = str(value).strip()

    if not text:
        return 0.0

    text_upper = text.upper()

    invalid_values = [
        "N/A",
        "-",
        "#N/A",
        "#VALUE!",
        "#REF!",
        "#DIV/0!",
        "#NAME?",
        "#REF"
    ]

    if text_upper in invalid_values:
        return 0.0

    # Hilangkan pemisah ribuan sederhana
    text = text.replace(",", ".")

    match = re.search(
        r'-?\d+(?:\.\d+)?',
        text
    )

    if not match:
        return 0.0

    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


# ============================================================
# 8. PROSES SEMUA SHEET
# ============================================================

rekap_data = []


for target_name in target_sheets_matched:

    sheet = spreadsheet.worksheet(target_name)

    data_mentah = sheet.get_all_values()

    if not data_mentah:

        print(
            f"Sheet '{target_name}' -> [SKIP] Tidak ada data."
        )

        continue


    # --------------------------------------------------------
    # DETEKSI HEADER
    # --------------------------------------------------------

    hasil_header = deteksi_header(
        data_mentah,
        max_scan=100
    )


    if hasil_header is None:

        print(
            f"Sheet '{target_name}' -> "
            f"[SKIP] Header tidak ditemukan."
        )

        continue


    header_idx = hasil_header["row_idx"]
    mapping = hasil_header["mapping"]

    print(
        f"\nSheet '{target_name}'"
    )

    print(
        f"  Header ditemukan pada baris "
        f"{header_idx + 1}"
    )

    print(
        f"  Jumlah header terdeteksi: "
        f"{hasil_header['jumlah_field']}"
    )

    print(
        f"  Mapping: {mapping}"
    )


    # --------------------------------------------------------
    # VALIDASI MINIMAL
    # --------------------------------------------------------

    if (
        mapping["kode"] is None
        and mapping["nama"] is None
    ):

        print(
            "  -> [SKIP] Tidak ada kolom Kode Material "
            "atau Material/Komponen."
        )

        continue


    # --------------------------------------------------------
    # EKSTRAKSI DATA
    # --------------------------------------------------------

    baris_terproses = 0


    for row in data_mentah[header_idx + 1:]:

        # Pastikan row cukup panjang
        max_index = max(
            [
                idx for idx in mapping.values()
                if idx is not None
            ],
            default=0
        )

        if len(row) <= max_index:
            row = row + [""] * (
                max_index + 1 - len(row)
            )


        # ----------------------------------------------------
        # AMBIL DATA
        # ----------------------------------------------------

        kode_material = (
            str(row[mapping["kode"]]).strip()
            if mapping["kode"] is not None
            else ""
        )

        kode_stock = (
            str(row[mapping["kode_stock"]]).strip()
            if mapping["kode_stock"] is not None
            else ""
        )

        nama_material = (
            str(row[mapping["nama"]]).strip()
            if mapping["nama"] is not None
            else ""
        )

        spesifikasi = (
            str(row[mapping["spek"]]).strip()
            if mapping["spek"] is not None
            else ""
        )

        uom_car = (
            str(row[mapping["uom"]]).strip()
            if mapping["uom"] is not None
            else ""
        )


        # ----------------------------------------------------
        # QTY
        # ----------------------------------------------------

        valid_qty = (
            parse_number(row[mapping["qty"]])
            if mapping["qty"] is not None
            else 0.0
        )


        # ----------------------------------------------------
        # ALLOWANCE
        # ----------------------------------------------------

        valid_allowance = (
            parse_number(
                row[mapping["qty_allowance"]]
            )
            if mapping["qty_allowance"] is not None
            else 0.0
        )


        # ----------------------------------------------------
        # PR TOTAL
        # ----------------------------------------------------

        valid_pr_total = (
            parse_number(
                row[mapping["qty_pr_total"]]
            )
            if mapping["qty_pr_total"] is not None
            else 0.0
        )


        # ----------------------------------------------------
        # FILTER BARIS
        # ----------------------------------------------------

        kata_abaikan = [
            "kode material",
            "kode",
            "total",
            "subtotal",
            "header",
            "item code",
            "part number",
            "k o d e"
        ]


        kode_lower = kode_material.lower().strip()


        if (
            not kode_material
            or kode_lower in kata_abaikan
        ):
            continue


        # ----------------------------------------------------
        # SIMPAN
        # ----------------------------------------------------

        rekap_data.append([
            target_name,
            kode_material,
            kode_stock,
            nama_material,
            spesifikasi,
            valid_qty,
            uom_car,
            valid_allowance,
            valid_pr_total
        ])

        baris_terproses += 1


    print(
        f"  -> {baris_terproses} baris terambil."
    )


# ============================================================
# 9. DATAFRAME
# ============================================================

df_rekap = pd.DataFrame(
    rekap_data,
    columns=[
        "Nama Sheet Sumber",
        "Kode Material",
        "Kode Material Stock",
        "Material / Komponen",
        "Spesifikasi",
        "Qty / TS (Series)",
        "UoM/Car",
        "Qty Total (Series) + Allowance",
        "QTY PR TOTAL"
    ]
)


# ============================================================
# 10. RINGKASAN
# ============================================================

print("\n--- RINGKASAN HASIL PER SHEET ---")

if not df_rekap.empty:

    print(
        df_rekap
        .groupby("Nama Sheet Sumber")
        .size()
    )

else:

    print("Tidak ada data yang berhasil diambil.")


# ============================================================
# 11. TULIS KE GOOGLE SHEETS
# ============================================================

nama_tab_rekap = (
    "REKAP_BOM_TOTAL (Berdasarkan Kode Material) 2"
)


try:

    sheet_rekap = spreadsheet.worksheet(
        nama_tab_rekap
    )

    sheet_rekap.clear()


except gspread.exceptions.WorksheetNotFound:

    sheet_rekap = spreadsheet.add_worksheet(
        title=nama_tab_rekap,
        rows="8000",
        cols="9"
    )


data_ke_sheets = (
    [df_rekap.columns.values.tolist()]
    + df_rekap.values.tolist()
)


sheet_rekap.update(
    data_ke_sheets
)


print(
    f"\n[SUKSES] TOTAL "
    f"{len(df_rekap)} baris data berhasil "
    f"ditarik dan disimpan ke sheet "
    f"'{nama_tab_rekap}'."
)