"""Statistic Dashboard 2 — progres tindak lanjut material per Trainset.

Halaman analytics yang MENDEPAN sumber data berbeda dari Statistic
Dashboard (material_planning_2): tidak lewat pipeline ETL/upload file,
melainkan membaca langsung export CSV dari Google Sheets. Logikanya
sederhana — filter baris yang sudah punya nilai 'Tindak Lanjut',
seragamkan label yang mirip, lalu tampilkan sebaran status tindak
lanjut per 'Sheet Asal' dalam bentuk jumlah item dan persentase.
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


def count_by_status(df: pd.DataFrame) -> pd.DataFrame:
    """Jumlah item per 'Tindak Lanjut' x 'Sheet Asal' (reindex urut STATUS_ORDER)."""
    counts = (
        df.groupby(["Tindak Lanjut", "Sheet Asal"])
        .size()
        .unstack(fill_value=0)
    )
    return counts.reindex(STATUS_ORDER, fill_value=0)


def percentage_by_status(counts: pd.DataFrame) -> pd.DataFrame:
    """Persentase per status terhadap total tiap 'Sheet Asal' (kolom)."""
    if counts.empty or counts.columns.empty:
        return counts.astype(float)

    total_items = counts.sum(axis=0)
    return counts.div(total_items, axis=1).mul(100).round(2)


def to_long(counts: pd.DataFrame, value_name: str) -> pd.DataFrame:
    """Pivot lebar (status x sheet) -> panjang, siap dipakai plotly."""
    long = counts.reset_index().melt(
        id_vars="Tindak Lanjut",
        var_name="Sheet Asal",
        value_name=value_name,
    )
    # Buang baris tanpa sheet (bila ada status yang tidak muncul di
    # sheet tertentu tetap aman karena fill_value=0 di reindex).
    return long[long[value_name].notna()]


# ============================================================
# SIDEBAR
# ============================================================

def reset_sidebar():
    """Kembalikan URL sumber ke default & buang cache data.

    Dipakai sebagai on_click callback: di callback, widget state boleh
    dihapus karena belum terinstansiasi pada run berikutnya.
    """
    st.session_state.pop("tl_bom_url", None)
    st.session_state.pop("tl_trainset_manual", None)
    st.cache_data.clear()


def render_sidebar():
    with st.sidebar:
        st.header("Google Sheets")

        st.text_input(
            "URL export CSV",
            value=DEFAULT_BOM_URL,
            key="tl_bom_url",
            help=(
                "Export CSV publik (File > Share > Publish, atau tambahkan "
                "'/export?format=csv&gid=...' di akhir URL spreadsheet)."
            ),
        )

        st.text_input(
            "Pilih Trainset (opsional)",
            key="tl_trainset_manual",
            placeholder="TS 14",
            help=(
                "Isi manual bila ingin langsung membuka trainset tertentu "
                "tanpa memilih di dropdown. Kosongkan untuk memakai pilihan "
                "dropdown."
            ),
        )

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
    url = st.session_state.tl_bom_url.strip()

    try:
        raw = load_bom_data(url)
    except Exception as exc:
        st.error(
            f"Gagal membaca data dari Google Sheets: {exc}. "
            "Periksa URL export CSV di sidebar — pastikan spreadsheet "
            "dishare publik."
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
        default_trainset = st.session_state.get("tl_trainset_manual", "").strip()
        if default_trainset in trainset_options:
            index = trainset_options.index(default_trainset)
        elif "TS 14" in trainset_options:
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

    counts = count_by_status(data)
    percentages = percentage_by_status(counts)

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)

        with col1:
            st.subheader("Total Item")
            st.markdown(f"# {len(data):,}")
        with col2:
            st.subheader("Material Unik")
            st.markdown(f"# {data['Kode Material'].nunique():,}")
        with col3:
            st.subheader("Trainset")
            st.markdown(f"# {data['Trainset'].nunique()}")

        with st.container(border=True):
            col1, col2 = st.columns(2, border=True)

            with col1:
                status_totals = counts.sum(axis=1)
                pie_df = status_totals[status_totals > 0].reset_index()
                pie_df.columns = ["Tindak Lanjut", "Jumlah"]
                st.subheader("Tindak Lanjut")
                if pie_df.empty:
                    st.info("Belum ada data untuk ditampilkan.")
                else:
                    tlchart = px.pie(
                        pie_df,
                        values="Jumlah",
                        names="Tindak Lanjut",
                        hole=0.3,
                        color="Tindak Lanjut",
                        color_discrete_map=STATUS_COLORS,
                    )
                    st.plotly_chart(tlchart)
            with col2:
                sheet_totals = counts.sum(axis=0)
                sheet_df = sheet_totals[sheet_totals > 0].reset_index()
                sheet_df.columns = ["Sheet Asal", "Jumlah"]
                st.subheader("Sheet Asal")
                if sheet_df.empty:
                    st.info("Belum ada data untuk ditampilkan.")
                else:
                    shchart = px.pie(
                        sheet_df,
                        values="Jumlah",
                        names="Sheet Asal",
                        hole=0.3,
                    )
                    st.plotly_chart(shchart, width="stretch", height=450)

        ### diagram batang
        with st.container(border=True):
            st.subheader("Jumlah Item per Sheet Asal")
            st.bar_chart(
                to_long(counts, "Jumlah"),
                x="Sheet Asal",   # kategori di sumbu (horizontal=True)
                y="Jumlah",       # nilai angka yang ditumpuk
                color="Tindak Lanjut",
                horizontal=True,
            )

        with st.container(border=True):
            st.subheader("Persentase Tindak Lanjut (%)")
            st.caption(
                "Persentase dihitung terhadap total item per Sheet Asal "
                "(kolom = 100%)."
            )
            pct_bar = px.bar(
                to_long(percentages, "Persentase (%)"),
                x="Sheet Asal",
                y="Persentase (%)",
                color="Tindak Lanjut",
                color_discrete_map=STATUS_COLORS,
                barmode="stack",
                text_auto=",.2f",
            )
            pct_bar.update_yaxes(ticksuffix="%")
            st.plotly_chart(pct_bar, width="stretch", height=450)

        with st.container(border=True):
            st.subheader("Jumlah Item per Status")
            st.dataframe(counts)

        with st.container(border=True):
            st.subheader("Persentase per Status (%)")
            st.dataframe(percentages)

        with st.container(border=True):
            # Filter status lewat toggle (seperti dashboard pertama):
            # tanpa satu pun toggle aktif -> seluruh data tampil.
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                show_pr_eksisting = st.toggle("PR Eksisting")
                show_pr_baru = st.toggle("PR Baru")
            with col_b:
                show_belum_pr = st.toggle("Belum PR")
                show_komat = st.toggle("Menunggu Komat")
            with col_c:
                show_stok = st.toggle("Menggunakan Stok")
                show_eval = st.toggle("Menunggu Evaluasi Pemakaian")

            selected = []
            if show_pr_eksisting:
                selected.append(data[data["Tindak Lanjut"] == "PR EKSISTING"])
            if show_pr_baru:
                selected.append(data[data["Tindak Lanjut"] == "PR BARU"])
            if show_belum_pr:
                selected.append(data[data["Tindak Lanjut"] == "BELUM PR"])
            if show_komat:
                selected.append(data[data["Tindak Lanjut"] == "MENUNGGU KOMAT"])
            if show_stok:
                selected.append(data[data["Tindak Lanjut"] == "MENGGUNAKAN STOK"])
            if show_eval:
                selected.append(data[data["Tindak Lanjut"] == "MENUNGGU EVALUASI PEMAKAIAN"])

            df_terfilter = data.copy()
            if selected:
                df_terfilter = pd.concat(selected)

            st.subheader(f"Dataset Tindak Lanjut ({len(df_terfilter):,})")
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
    st.title("Statistik Dashboard 2")

    render_sidebar()
    render_dashboard()
