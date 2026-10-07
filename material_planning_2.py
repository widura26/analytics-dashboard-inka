import io
import time

import requests
import streamlit as st
import pandas as pd
import plotly.express as px

@st.cache_data(ttl=300, show_spinner="Memuat dataset...")
def load_csv(url: str, max_retries: int = 3) -> pd.DataFrame:
    for attempt in range(max_retries):
        try:
            with requests.get(url, timeout=60, stream=True) as resp:
                resp.raise_for_status()
                return pd.read_csv(io.BytesIO(resp.content), low_memory=False)
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)

def render():
    bom_data_url = "https://docs.google.com/spreadsheets/d/1Ibki18gicAFziEx1urTrvDv_KkzrkMMnRrbwqNYpOkw/export?format=csv&gid=386174298"
    bom_data = load_csv(bom_data_url)
    bom_data.duplicated().sum()

    sap_data_url = "https://docs.google.com/spreadsheets/d/1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q/export?format=csv&gid=826586568"
    sap_data = load_csv(sap_data_url)
    sap_data.duplicated().sum()

    bom_data.drop_duplicates(inplace=True)
    sap_data.drop_duplicates(inplace=True)

    bom_data.columns = bom_data.columns.str.strip()
    sap_data.columns = sap_data.columns.str.strip()

    sap_data['Qty Requested'] = pd.to_numeric(sap_data['Qty Requested'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Qty Requested'] = sap_data['Qty Requested'].fillna(0).astype(int)
    sap_data['Ordered'] = pd.to_numeric(sap_data['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Ordered'] = sap_data['Ordered'].fillna(0).astype(int)
    sap_data['Req.Date'] = pd.to_datetime(sap_data['Req.Date'], format='%d.%m.%Y')
    sap_data['PR Deliv. Dat'] = pd.to_datetime(sap_data['PR Deliv. Dat'], format='%d.%m.%Y')
    sap_data['Chngd on'] = pd.to_datetime(sap_data['Chngd on'], format='%d.%m.%Y')

    sap_data["WBS_Group"] = sap_data["WBS Elem"].str.extract(
        r"^([A-Z]-\d+\.\d+)"
    )

    project_options = ("Semua",) + tuple(sap_data["WBS_Group"].dropna().unique())
    col_project, col_sheet = st.columns(2)
    with col_project:
        selected_project = st.selectbox("Pilih Project 2", project_options)
    if selected_project != "Semua":
        sap_data = sap_data[sap_data["WBS_Group"] == selected_project]
        bom_data = bom_data[bom_data["WBS Elem"] == selected_project]

    sheet_options = ("Semua",) + tuple(bom_data["Nama Sheet Sumber"].dropna().unique())
    with col_sheet:
        selected_sheet = st.selectbox("Nama Sheet Sumber", sheet_options)
    if selected_sheet != "Semua":
        bom_data = bom_data[bom_data["Nama Sheet Sumber"] == selected_sheet]

    filtered_bom_data = bom_data[bom_data["Kode Material Delete"].isna()]
    filtered_bom_data = filtered_bom_data.groupby(["Kode Material", "Nama Sheet Sumber"], as_index=False).agg({
        'WBS Elem': 'first',
        'Kode Material Delete': 'first',
        'Kode Material Stock': 'first',
        'Stock Total': 'sum',
        'Material / Komponen': 'first',
        'Spesifikasi': 'first',
        'Qty / TS (Series)': 'sum',
        'UoM/Car': 'first',
        'Qty Total (Series) + Allowance': 'sum',
        'QTY PR TOTAL': 'sum'
    })

    filtered_sap_data = sap_data.groupby('Kode Material', as_index=False).agg(
        {
            'Mat. Description': 'first',
            'Spesifikasi': 'first',
            'PR Status': 'first',
            'Qty Requested': 'sum',
            'Ordered': 'sum'
        }
    )

    filtered_sap_data = sap_data.groupby(['Kode Material', 'WBS_Group'], as_index=False).agg({
        'WBS Description': 'first',
        'Mat. Description': 'first',
        'Spesifikasi': 'first',
        'PR Status': 'first',
        'Qty Requested': 'sum',
        'Ordered': 'sum'
    })

    merge_data = pd.merge(
        filtered_bom_data, 
        filtered_sap_data, 
        left_on=['Kode Material', 'WBS Elem'], 
        right_on=['Kode Material', 'WBS_Group'], 
        how='inner'
    )

    stockData = merge_data[(merge_data["Kode Material Stock"].isna() & (merge_data["Stock Total"] > 0.0)) | (merge_data["Kode Material Stock"].notna() & ((merge_data['Stock Total'] > 0.0) | (merge_data['Stock Total'] == 0.0)))]
    otherData = merge_data[merge_data['Kode Material Stock'].isna() & (merge_data['Stock Total'] == 0.0)]
    belumPRData = otherData[(otherData["QTY PR TOTAL"] > otherData["Qty Requested"])]
    sudahPRData = otherData[otherData["QTY PR TOTAL"] == otherData["Qty Requested"]]
    partialPRData = otherData[otherData["QTY PR TOTAL"] < otherData["Qty Requested"]]
    stockPRData = stockData[(stockData["QTY PR TOTAL"] < stockData["Qty Requested"]) & (stockData["Stock Total"] > 0.0)]


    project_data = sap_data.groupby("WBS Description").size()

    statusB = sudahPRData[sudahPRData["PR Status"] == "B"]
    statusAorK = sudahPRData[(sudahPRData["PR Status"] == "A") | (sudahPRData["PR Status"] == "K")]
    statusN = sudahPRData[sudahPRData["PR Status"] == "N"]

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)
    
        with col1:
            st.subheader("Total Data BOM")
            bom_data_total = len(filtered_bom_data)
            st.markdown(f"# {bom_data_total}")
        with col2:
            st.subheader("Total Data SAP")
            sap_data_total = len(filtered_sap_data)
            st.markdown(f"# {sap_data_total}")
        with col3:
            st.subheader("Total Proyek")
            sap_data_total = len(project_data)
            st.markdown(f"# {sap_data_total}")


        with st.container(border=True):
            col1, col2 = st.columns(2, border=True)
            col3, col4 = st.columns(2, border=True)
        
            with col1:
                data = {
                    'Status': ['Belum PR', 'Sudah PR', 'Stock', 'Partial PR', 'PR & Stock'],
                    'Jumlah': [len(belumPRData), len(sudahPRData), len(stockData), len(partialPRData), len(stockPRData)]
                }
                df = pd.DataFrame(data)
                prchart = px.pie(df, values = 'Jumlah', names = 'Status', hole=0.3)
                st.subheader("Purchase Requisition")
                st.plotly_chart(prchart)
            with col2:
                po = {
                    'Status PO': ['N', 'A/K', 'B'],
                    'Total': [len(statusN), len(statusAorK), len(statusB)],
                }
                df = pd.DataFrame(po)
                pochart = px.pie(df, values = 'Total', names = 'Status PO', hole=0.3)
                st.subheader("Purchase Order")
                st.plotly_chart(pochart, width="stretch", height=450)
        with st.container(border=True):
            st.subheader(f"Bill Of Material Dataset")
            st.dataframe(bom_data)

        with st.container(border=True):
            st.subheader(f"SAP Dataset")
            st.dataframe(sap_data)

        with st.container(border=True):
            # Merger of BOM and SAP Data
            show_under_requested = st.toggle("PR Partial")
            show_over_requested = st.toggle("Belum PR")
            show_same_requested = st.toggle("Sudah PR")
            show_stock_data = st.toggle("Stock")
            show_stock_pr_data = st.toggle("Stock & PR")
            df_terfilter = merge_data.copy()
            selected = []

            if show_under_requested:
                selected.append(partialPRData)

            if show_over_requested:
                selected.append(belumPRData)

            if show_same_requested:
                selected.append(sudahPRData)

            if show_stock_data:
                selected.append(stockData)

            if show_stock_pr_data:
                selected.append(stockPRData)

            if selected:
                df_terfilter = pd.concat(selected)

            st.subheader(f"Merger of BOM and SAP Data ({len(df_terfilter)})")
            st.dataframe(df_terfilter)