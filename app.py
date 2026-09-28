import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(layout="wide")

bomUrl = "https://docs.google.com/spreadsheets/d/1Ibki18gicAFziEx1urTrvDv_KkzrkMMnRrbwqNYpOkw/export?format=csv&gid=1134789511"
bomdataset = pd.read_csv(bomUrl, low_memory=False)
instockData = bomdataset[bomdataset["Kode Material Stock"].notna()]
nostockData = bomdataset[bomdataset["Kode Material Stock"].isna()]

sapdataseturl = "https://docs.google.com/spreadsheets/d/1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q/export?format=csv&gid=826586568"
sapdataset = pd.read_csv(sapdataseturl, low_memory=False)   
sapdataset.columns = sapdataset.columns.str.strip()
sapdataset['Qty Requested'] = pd.to_numeric(sapdataset['Qty Requested'].str.replace(',', '').str.replace('.', ''), errors='coerce')
sapdataset['Qty Requested'] = sapdataset['Qty Requested'].fillna(0).astype(int)
sapdataset['Ordered'] = pd.to_numeric(sapdataset['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
sapdataset['Ordered'] = sapdataset['Ordered'].fillna(0).astype(int)

sapdataset = sapdataset.groupby('Kode Material', as_index=False).agg({
    'Mat. Description': 'first',
    'Spesifikasi': 'first',
    'PR Status': 'first',
    'Qty Requested': 'sum',
    'Ordered': 'sum'
})

merge_data = pd.merge(nostockData, sapdataset, on='Kode Material', how='inner')
x = merge_data[merge_data["QTY PR TOTAL"] != merge_data["Qty Requested"]]
y = merge_data[merge_data["QTY PR TOTAL"] == merge_data["Qty Requested"]]
z = merge_data[merge_data["QTY PR TOTAL"] < merge_data["Qty Requested"]]

statusN = sapdataset[sapdataset['PR Status'] == 'N']
statusAorK = sapdataset[(sapdataset['PR Status'] == 'A') | (sapdataset['PR Status'] == 'K')]
statusB = sapdataset[sapdataset['PR Status'] == 'B']

with st.container(border=True):
    col1, col2 = st.columns(2, border=True)
    
    with col1:
        pr = {
            'status PR': ['Belum PR', 'Sudah PR', 'Stock'],
            'Total': [len(x[(x["QTY PR TOTAL"] > x["Qty Requested"]) | ((x["QTY PR TOTAL"] == 0.0) & (x["Qty Requested"] == 0))]), len(y), len(instockData)],
        }
        df = pd.DataFrame(pr)
        prchart = px.pie(df, values = 'Total', names = 'status PR')
        st.subheader("Purchase Requisition")
        st.plotly_chart(prchart)

        # st.write("Data Stock diambil dari data-data BOM yang memiliki kode material stock.")
        # st.write("Data Sudah PR diambil dari data-data BOM yang tidak memiliki kode material stock dan 'QTY PR TOTAL' di BOM dataset sama dengan 'Qty Requested' di SAP dataset")
        # st.write("Data Belum PR diambil dari data-data BOM yang tidak memiliki kode material stock dan 'QTY PR TOTAL' di BOM dataset lebih besar dari 'Qty Requested' di SAP dataset")
    with col2:
        po = {
            'Status PO': ['N', 'A/K', 'B'],
            'Total': [len(statusN), len(statusAorK), len(statusB)],
        }
        df = pd.DataFrame(po)
        pochart = px.pie(df, values = 'Total', names = 'Status PO')
        st.subheader("Purchase Order")
        st.plotly_chart(pochart, width="stretch", height=450)

        # st.write("Data Stock diambil dari data-data BOM yang memiliki kode material stock.")
        # st.write("Data Sudah PR diambil dari data-data BOM yang tidak memiliki kode material stock dan 'QTY PR TOTAL' sama dengan 'Qty Requested'")
        # st.write("Data Belum PR diambil dari data-data BOM yang tidak memiliki kode material stock dan 'QTY PR TOTAL' lebih besar dari 'Qty Requested'")

with st.container(border=True):
    st.subheader("Bill Of Material Dataset")
    bomdataset

with st.container(border=True):
    st.subheader("SAP Dataset")
    sapdataset

with st.container(border=True):
    st.subheader("Match dataset")
    merge_data

with st.container(border=True):
    st.subheader("Sample Dataset QTY PR TOTAL < QTY Requested")
    x[(x["QTY PR TOTAL"] < x["Qty Requested"]) | ((x["QTY PR TOTAL"] == 0.0) & (x["Qty Requested"] == 0))]

