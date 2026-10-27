"""Statistic Dashboard 2 — progres tindak lanjut material per Trainset.

Halaman analytics yang MENDEPAN sumber data berbeda dari Statistic
Dashboard (material_planning_2): tidak lewat pipeline ETL/upload file,
melainkan membaca langsung export CSV dari Google Sheets. Logikanya
sederhana — filter baris yang sudah punya nilai 'Tindak Lanjut',
seragamkan label yang mirip, lalu tampilkan sebaran status tindak
lanjut per 'Sheet Asal' dalam bentuk jumlah item dan persentase.

Dua mode hitung:
- Trainset spesifik: hitung per baris (satu baris = satu material di
  satu trainset), sama seperti olahan per-TS.
- "Semua": material di-collapse dulu LINTAS trainset (kunci
  NO + Kode Material + Sheet Asal), seluruh nilai Tindak Lanjut-nya
  disatukan jadi teks koma, lalu diklasifikasi check_pr_status()
  menjadi 'Status PR' (PR PARSIAL, STOK DAN PR, SUDAH PR, BELUM PR,
  STOK, STATUS LAIN). Satu material hanya dihitung sekali.
"""
import io

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# ============================================================
# CONFIG
# ============================================================

# Sheet "Tindak Lanjut BOM" — export CSV publik (tanpa autentikasi).
DEFAULT_BOM_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1axKrvf147x0eQ9hsuHZA4Ur943bzQFttJ6W0oQuK7RQ/"
    "export?format=csv&gid=176301186"
)

# Urutan status di chart & tabel (reindex: status kosong -> 0).
STATUS_ORDER = [
    "PR EKSISTING",
    "PR BARU",
    "BELUM PR",
    "MENUNGGU KOMAT",
    "MENGGUNAKAN STOK",
    "MENUNGGU EVALUASI PEMAKAIAN",
]

# Label yang ejaannya berbeda di sheet tapi maksudnya sama -> diseragamkan
# supaya tidak terpecah jadi dua status saat digroupby.
STATUS_RENAME = {
    "MENGGUNAKAN STOK GUDANG": "MENGGUNAKAN STOK",
}

# Warna tetap per status supaya konsisten antar chart & rerun.
STATUS_COLORS = {
    "PR EKSISTING": "#636EFA",
    "PR BARU": "#00CC96",
    "BELUM PR": "#EF553B",
    "MENUNGGU KOMAT": "#FFA15A",
    "MENGGUNAKAN STOK": "#AB63FA",
    "MENUNGGU EVALUASI PEMAKAIAN": "#FF9896",
}

# Kategori hasil check_pr_status (mode "Semua"). Urutan = urutan tampil
# di chart/tabel; STATUS LAIN tetap tampil supaya tidak ada data hilang.
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

# Data Sheets bisa berubah sewaktu-waktu; cache singkat + tombol muat
# ulang di sidebar supaya perubahan terbaru bisa ditarik paksa.
DATA_CACHE_TTL = 300


# ============================================================
# DATA
# ============================================================

@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner="Membaca data BOM dari Google Sheets...")
def load_bom_data(url: str) -> pd.DataFrame:
    """Ambil CSV publik Google Sheets -> DataFrame apa adanya."""
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return pd.read_csv(io.BytesIO(response.content), low_memory=False)


def prepare_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Bersihkan data mentah untuk keperluan dashboard.

    - Buang baris tanpa 'Tindak Lanjut' (belum ada keputusan tindak
      lanjut, jadi tidak ikut perhitungan status).
    - Seragamkan label status yang bervariasi (STATUS_RENAME).
    """
    df = raw[raw["Tindak Lanjut"].notna()].copy()
    df["Tindak Lanjut"] = df["Tindak Lanjut"].replace(STATUS_RENAME)
    return df


def count_by_status(
    df: pd.DataFrame,
    status_col: str = "Tindak Lanjut",
    order=STATUS_ORDER,
) -> pd.DataFrame:
    """Jumlah baris per `status_col` x 'Sheet Asal' (reindex urut `order`).

    `status_col` = 'Tindak Lanjut' (mode trainset spesifik) atau
    'Status PR' (mode Semua, lihat collapse_material).
    """
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



# ============================================================
# MODE "SEMUA": COLLAPSE PER MATERIAL + KLASIFIKASI STATUS PR
# ============================================================

def check_pr_status(text) -> str:
    if pd.isna(text) or not str(text).strip():
        return "BELUM PR"

    arr_status = {
        status.strip().upper()
        for status in str(text).split(",")
    }

    # 1. PR PARSIAL
    # Jika terdapat PR BARU dan BELUM PR
    # atau PR BARU bersama MENUNGGU EVALUASI PEMAKAIAN
    if (
        "PR BARU" in arr_status
        and (
            "BELUM PR" in arr_status
            or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
        )
    ):
        return "PR PARSIAL"

    # 2. STOK DAN PR
    # PR BARU atau PR EKSISTING bersama MENGGUNAKAN STOK,
    # selama tidak memenuhi kondisi PR PARSIAL
    if (
        "MENGGUNAKAN STOK" in arr_status
        and (
            "PR BARU" in arr_status
            or "PR EKSISTING" in arr_status
        )
    ):
        return "STOK DAN PR"

    # 3. STOK
    if arr_status == {"MENGGUNAKAN STOK"}:
        return "STOK"

    # 4. SUDAH PR
    if (
        "PR BARU" in arr_status
        or "PR EKSISTING" in arr_status
    ):
        return "SUDAH PR"

    # 5. BELUM PR
    if (
        "BELUM PR" in arr_status
        or "MENUNGGU EVALUASI PEMAKAIAN" in arr_status
    ):
        return "BELUM PR"

    return "STATUS LAIN / TIDAK DIKENAL"


def collapse_material(data: pd.DataFrame) -> pd.DataFrame:
    """Satukan seluruh baris per material LINTAS trainset jadi satu baris.

    Kunci: NO + Kode Material + Sheet Asal. NO di-cast ke str dulu
    (persis olahan asli) supaya baris tanpa NO tidak terbuang oleh
    groupby default. Semua nilai 'Tindak Lanjut' unik disatukan jadi teks
    koma (urut kata kedua, deterministic), lalu diklasifikasi menjadi
    kolom 'Status PR' oleh check_pr_status().

    Kenapa bukan kunci 'Kode Material' saja: 60 kode material punya >1
    baris NO (784 grup vs 446 kode unik) — memakai NO menjaga baris
    sheet yang memang terpisah tetap terpisah.
    """
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
    joined = pd.merge(joined, desc, on=["NO", "Kode Material", "Sheet Asal"], how="left")

    # Daftar trainset yang menyumbang material ini (urut nomor TS,
    # mis. "TS 9, TS 10, ..., TS 16") — informatif di tabel detail.
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


# ============================================================
# AGREGASI CHART TAMBAHAN (TREN DELIV DATE)
# ============================================================

def build_delivery_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Jumlah item per bulan kolom 'Deliv Date SAP' (format dd.mm.yyyy).

    Bulan tanpa pengiriman di tengah rentang direindex jadi 0, supaya
    celah timeline terlihat apa adanya (mis. tidak ada pengiriman antara
    2024-10 dan 2025-03). Baris tanpa tanggal / tanggal tidak valid
    tidak dihitung — pemanggil bisa membandingkan sum(Jumlah) dengan
    len(df) untuk tahu sisanya.

    Mengembalikan frame ['Bulan' (string 'YYYY-MM', urut), 'Jumlah'].
    Kosong bila kolomnya tidak ada atau tidak ada satu pun tanggal valid.
    """
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
    """Persentase komposisi 'Tindak Lanjut' per Trainset (stack 100%).

    Dihitung dari baris apa adanya, BUKAN dari 'Status PR' hasil
    collapse — semua material punya baris di setiap trainset, jadi
    Status PR akan menghasilkan batang identik di semua trainset.
    Nilai = (jumlah baris per status / total baris trainset) x 100.

    all_trainsets: daftar lengkap trainset yang harus tampil di sumbu-X.
    Trainset tanpa baris di df (mis. difilter keluar) tetap muncul
    dengan nilai 0, jadi chart tidak menyusut saat filter menyempit.

    Urutan baris = numerik (TS 9, TS 10, ..., TS 16); status yang tidak
    muncul di data dibuang dari kolom (legend tidak menampilkan kategori
    kosong). Mengembalikan frame kosong bila datanya kosong.
    """
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


# ============================================================
# SIDEBAR
# ============================================================

def reset_sidebar():
    """Bersihkan cache data sumber.

    Dipakai sebagai on_click callback.
    """
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

        st.button(
            "Reset",
            width="stretch",
            key="btn_reset_tl",
            on_click=reset_sidebar,
        )


# ============================================================
# DASHBOARD
# ============================================================

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

    # ------------------------------------------------------------
    # Filter (di luar container utama, seperti dashboard pertama)
    # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # Mode hitung:  
    # - "Semua"      -> collapse per material lintas trainset, lalu
    #                   klasifikasi 'Status PR' (check_pr_status).
    # - Trainset tertentu -> hitung per baris (satu baris = satu
    #                   material di satu trainset), status = nilai
    #                   'Tindak Lanjut' apa adanya.
    # Layout tampilan sama persis; hanya kolom status & daftarnya yang
    # berbeda.
    # ------------------------------------------------------------

    mode_semua = selected_trainset == "Semua"

    if mode_semua:
        frame = collapse_material(data)
        status_col = "Status PR"
        status_order = STATUS_PR_ORDER
        status_colors = STATUS_PR_COLORS
    else:
        frame = data
        status_col = "Tindak Lanjut"
        status_order = STATUS_ORDER
        status_colors = STATUS_COLORS

    counts = count_by_status(frame, status_col, status_order)
    percentages = percentage_by_status(counts)

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)

        with col1:
            st.subheader("Total Item")
            st.markdown(f"# {len(frame):,}")
        with col2:
            st.subheader("Jumlah Material")
            st.markdown(f"# {frame['Kode Material'].nunique():,}")
        with col3:
            st.subheader("Trainset")
            st.markdown(f"# {data['Trainset'].nunique()}")

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
            

        # ------------------------------------------------------------
        # Komposisi tindak lanjut per trainset. Semua trainset selalu
        # tampil di sumbu-X; di mode trainset spesifik hanya trainset
        # terpilih yang terisi, sisanya 0. Pakai Tindak Lanjut per
        # baris; Status PR hasil collapse tidak bisa dipakai di sini
        # karena semua material ada di 8 trainset sehingga batangnya
        # akan identik.
        # ------------------------------------------------------------
        with st.container(border=True):
            st.subheader("Komposisi Tindak Lanjut per Trainset")
            semua_trainset = list(trainset_options[1:])  # tanpa "Semua"
            per_ts = status_by_trainset(data, all_trainsets=semua_trainset)
            if per_ts.empty:
                st.info("Belum ada baris untuk ditampilkan.")
            else:
                if mode_semua:
                    st.caption(
                        "Persentase dari jumlah baris tiap trainset "
                        "(Tindak Lanjut per baris, bukan Status PR hasil "
                        "gabungan lintas trainset)."
                    )
                else:
                    st.caption(
                        f"Persentase dari jumlah baris {selected_trainset}; "
                        "trainset lain tampil 0 karena datanya sudah "
                        "difilter."
                    )
                ts_long = per_ts.reset_index().melt(
                    id_vars="Trainset",
                    var_name="Tindak Lanjut",
                    value_name="Persentase (%)",
                )
                ts_chart = px.bar(
                    ts_long,
                    x="Trainset",
                    y="Persentase (%)",
                    color="Tindak Lanjut",
                    barmode="stack",
                    color_discrete_map=STATUS_COLORS,
                    category_orders={
                        "Trainset": list(per_ts.index),
                        "Tindak Lanjut": list(per_ts.columns),
                    },
                )
                ts_chart.update_yaxes(ticksuffix="%", range=[0, 100])
                ts_chart.update_layout(
                    xaxis_title="Trainset",
                    yaxis_title="Persentase (%)",
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.15,
                        xanchor="center",
                        x=0.5,
                    ),
                )
                st.plotly_chart(ts_chart, width="stretch", height=450)

        # ------------------------------------------------------------
        # Tren Deliv Date SAP per bulan (dihitung dari baris, bukan
        # hasil collapse — tanggal beda antar trainset)
        # ------------------------------------------------------------
        with st.container(border=True):
            st.subheader("Tren Deliv Date SAP per Bulan")
            trend = build_delivery_trend(data)
            if trend.empty:
                st.info(
                    "Tidak ada 'Deliv Date SAP' yang valid pada data "
                    "terpilih."
                )
            else:
                terjadwal = int(trend["Jumlah"].sum())
                # tanpa_tanggal = len(data) - terjadwal
                trend_chart = px.line(
                    trend,
                    x="Bulan",
                    y="Jumlah",
                    markers=True,
                    text="Jumlah",
                )
                trend_chart.update_traces(textposition="top center")
                trend_chart.update_layout(
                    xaxis_title="Bulan",
                    yaxis_title="Jumlah Item",
                    showlegend=False,
                )
                st.plotly_chart(trend_chart, width="stretch", height=450)

        with st.container(border=True):
            st.subheader(f"Jumlah per {status_col}")
            st.dataframe(counts)

        with st.container(border=True):
            st.subheader(f"Persentase per {status_col} (%)")
            st.dataframe(percentages)

        with st.container(border=True):
            # Filter status lewat toggle (seperti dashboard pertama):
            # tanpa satu pun toggle aktif -> seluruh data tampil.
            # Daftar toggle = status_order, jadi di mode Semua tampil
            # kategori Status PR (termasuk STATUS LAIN — tidak ada data
            # yang disembunyikan), di mode trainset tampil Tindak Lanjut.
            toggle_cols = st.columns(3)
            selected_labels = []
            for i, status in enumerate(status_order):
                with toggle_cols[i % 3]:
                    if st.toggle(status, key=f"tl_toggle_{status_col}_{status}"):
                        selected_labels.append(status)

            df_terfilter = frame.copy()
            if selected_labels:
                df_terfilter = frame[frame[status_col].isin(selected_labels)]

            st.subheader(
                f"Dataset Bill Of Materials"
                f"({len(df_terfilter):,})"
            )
            st.dataframe(df_terfilter)

            st.download_button(
                "Download CSV",
                data=df_terfilter.to_csv(index=False).encode("utf-8"),
                file_name=f"tindak_lanjut_{selected_trainset.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )


# ============================================================
# MAIN
# ============================================================

def render():
    st.title("Statistics Dashboard")

    render_sidebar()
    render_dashboard()
