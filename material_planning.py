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
    # sap_dataset = load_csv(sap_data_url)

    bom_data.drop_duplicates(inplace=True)
    sap_data.drop_duplicates(inplace=True)

    bom_data.columns = bom_data.columns.str.strip()
    sap_data.columns = sap_data.columns.str.strip()
    # sap_dataset.columns = sap_dataset.columns.str.strip()

    sap_data['Qty Requested'] = pd.to_numeric(sap_data['Qty Requested'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Qty Requested'] = sap_data['Qty Requested'].fillna(0).astype(int)
    sap_data['Ordered'] = pd.to_numeric(sap_data['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Ordered'] = sap_data['Ordered'].fillna(0).astype(int)

    filtered_bom_data = bom_data[bom_data["Kode Material Delete"].isna()]
    filtered_sap_data = sap_data.groupby('Kode Material', as_index=False).agg(
        {
            'Mat. Description': 'first',
            'Spesifikasi': 'first',
            'PR Status': 'first',
            'Qty Requested': 'sum',
            'Ordered': 'sum'
        }
    )

    merge_data = pd.merge(filtered_bom_data, filtered_sap_data, on='Kode Material', how='inner')
    stockData = merge_data[(merge_data["Kode Material Stock"].isna() & (merge_data["Stock Total"] > 0.0)) | (merge_data["Kode Material Stock"].notna() & ((merge_data['Stock Total'] > 0.0) | (merge_data['Stock Total'] == 0.0)))]
    otherData = merge_data[merge_data['Kode Material Stock'].isna() & (merge_data['Stock Total'] == 0.0)]
    belumPRData = otherData[(otherData["QTY PR TOTAL"] > otherData["Qty Requested"])]
    sudahPRData = otherData[otherData["QTY PR TOTAL"] == otherData["Qty Requested"]]
    partialPRData = otherData[otherData["QTY PR TOTAL"] < otherData["Qty Requested"]]

    project_data = sap_data.groupby("WBS Description").size()
    project_tuple_data = tuple(project_data.index)

    st.selectbox(
        "Pilih Project",
        project_tuple_data
    )

    # statusN = filtered_sap_data[filtered_sap_data['PR Status'] == 'N']
    # statusAorK = filtered_sap_data[(filtered_sap_data['PR Status'] == 'A') | (filtered_sap_data['PR Status'] == 'K')]
    # statusB = filtered_sap_data[filtered_sap_data['PR Status'] == 'B']

    statusB = sudahPRData[sudahPRData["PR Status"] == "B"]
    statusAorK = sudahPRData[(sudahPRData["PR Status"] == "A") | (sudahPRData["PR Status"] == "K")]
    statusN = sudahPRData[sudahPRData["PR Status"] == "N"]

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)
    
        with col1:
            st.subheader("Total Data BOM")
            bom_data_total = len(bom_data)
            st.markdown(f"# {bom_data_total}")
        with col2:
            st.subheader("Total Data SAP")
            sap_data_total = len(sap_data)
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
                    'Status': ['Belum PR', 'Sudah PR', 'Stock', 'Partial PR'],
                    'Jumlah': [len(belumPRData), len(sudahPRData), len(stockData), len(partialPRData)]
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
            show_under_requested = st.toggle("PR Total < Requested")
            show_over_requested = st.toggle("Belum PR")
            show_same_requested = st.toggle("Sudah PR")
            df_terfilter = merge_data.copy()
            conditions = []

            if show_under_requested:
                conditions.append(
                    df_terfilter['QTY PR TOTAL'] < df_terfilter['Qty Requested']
                )

            if show_over_requested:
                conditions.append(
                    df_terfilter['QTY PR TOTAL'] > df_terfilter['Qty Requested']
                )

            if show_same_requested:
                conditions.append(
                    df_terfilter['QTY PR TOTAL'] == df_terfilter['Qty Requested']
                )

            if conditions:
                mask = conditions[0]

                for condition in conditions[1:]:
                    mask = mask | condition

                df_terfilter = df_terfilter[mask]

            st.subheader(f"Merger of BOM and SAP Data ({len(df_terfilter)})")
            st.dataframe(df_terfilter)

        with st.container(border=True):
            st.subheader("PR Partial Data")
            st.dataframe(partialPRData)