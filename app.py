import streamlit as st

def main():
    import io
    import time

    import requests
    import streamlit as st
    import pandas as pd
    import plotly.express as px

    st.set_page_config(layout="wide")


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


    bomUrl = "https://docs.google.com/spreadsheets/d/1Ibki18gicAFziEx1urTrvDv_KkzrkMMnRrbwqNYpOkw/export?format=csv&gid=386174298"
    bomdataset = load_csv(bomUrl)
    deleteKomatBomdataset = bomdataset[bomdataset['Kode Material Delete'].isna()]
    instockData = deleteKomatBomdataset[deleteKomatBomdataset["Kode Material Stock"].notna()]
    nostockData = deleteKomatBomdataset[deleteKomatBomdataset["Kode Material Stock"].isna()]

    sapdataseturl = "https://docs.google.com/spreadsheets/d/1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q/export?format=csv&gid=826586568"
    sapdataset = load_csv(sapdataseturl)
    sapdataset.columns = sapdataset.columns.str.strip()
    sapdataset['Qty Requested'] = pd.to_numeric(sapdataset['Qty Requested'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sapdataset['Qty Requested'] = sapdataset['Qty Requested'].fillna(0).astype(int)
    sapdataset['Ordered'] = pd.to_numeric(sapdataset['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sapdataset['Ordered'] = sapdataset['Ordered'].fillna(0).astype(int)

    project_data = sapdataset.groupby("WBS Description").size()
    project_tuple_data = tuple(project_data.index)

    option = st.selectbox(
        "Pilih Project",
        project_tuple_data
    )

    sapdatasetData = sapdataset.groupby('Kode Material', as_index=False).agg({
        'Mat. Description': 'first',
        'Spesifikasi': 'first',
        'PR Status': 'first',
        'Qty Requested': 'sum',
        'Ordered': 'sum'
    })

    merge_data = pd.merge(nostockData, sapdatasetData, on='Kode Material', how='inner')
    merge_data_stock = pd.merge(deleteKomatBomdataset, sapdatasetData, on='Kode Material', how='inner')
    x = merge_data[(merge_data["QTY PR TOTAL"] > merge_data["Qty Requested"]) | ((merge_data["QTY PR TOTAL"] == 0.0) & (merge_data["Qty Requested"] == 0))]
    y = merge_data[merge_data["QTY PR TOTAL"] == merge_data["Qty Requested"]]
    z = merge_data[merge_data["QTY PR TOTAL"] < merge_data["Qty Requested"]]
    zz = z[z["Stock Total"] > 0.0]
    # w = merge_data_stock[
    #     (merge_data_stock["QTY PR TOTAL"] < merge_data_stock["Qty Requested"]) & 
    #     (
    #         (merge_data_stock["Stock Total"] > 0.0) | 
    #         (merge_data_stock["Kode Material Stock"].notna() & (merge_data_stock["Stock Total"] == 0.0))
    #     )
    # ]
    w = merge_data_stock[(merge_data_stock["QTY PR TOTAL"] < merge_data_stock["Qty Requested"]) & (merge_data_stock["Stock Total"] > 0.0)]


    statusN = sapdatasetData[sapdatasetData['PR Status'] == 'N']
    statusAorK = sapdatasetData[(sapdatasetData['PR Status'] == 'A') | (sapdatasetData['PR Status'] == 'K')]
    statusB = sapdatasetData[sapdatasetData['PR Status'] == 'B']

    with st.container(border=True):
        col1, col2, col3 = st.columns(3, border=True)
        
        with col1:
            st.subheader("Total Data BOM")
            bom_data_total = len(bomdataset)
            st.markdown(f"# {bom_data_total}")
        with col2:
            st.subheader("Total Data SAP")
            sap_data_total = len(sapdataset)
            st.markdown(f"# {sap_data_total}")
        with col3:
            st.subheader("Total Proyek")
            project_total = len(project_data)
            st.markdown(f"# {project_total}")


    with st.container(border=True):
        col1, col2 = st.columns(2, border=True)
        col3, col4 = st.columns(2, border=True)
        
        with col1:
            pr = {
                'Status': ['Belum PR', 'Sudah PR', 'Stock', 'PR dan Stock', 'PR Partial'], 
                'Total': [len(x), len(y), len(instockData), len(w), len(z[z['Stock Total'] == 0.0])],
            }
            df = pd.DataFrame(pr)
            prchart = px.pie(df, values = 'Total', names = 'Status')
            st.subheader("Purchase Requisition")
            st.plotly_chart(prchart)
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

        # with col3:
        #     st.subheader("Purchase Requisition")
        #     st.echarts_chart(
        #         {
        #             "xAxis": {"type": "category", "data": ["A", "B", "C", "D", "E"]},
        #             "yAxis": {"type": "value"},
        #             "series": [{"type": "bar", "data": [5, 20, 36, 10, 10]}],
        #         }
        #     )

    with st.container(border=True):
        st.subheader(f"Bill Of Material Dataset")
        bomdataset

    with st.container(border=True):
        st.subheader(f"SAP Dataset")
        sapdataset

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
        st.subheader("Stock and PR Data")
        w

    with st.container(border=True):
        st.subheader("PR Partial Data")
        z[z['Stock Total'] == 0.0]

def second():
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

    bom_data_url = "https://docs.google.com/spreadsheets/d/1Ibki18gicAFziEx1urTrvDv_KkzrkMMnRrbwqNYpOkw/export?format=csv&gid=386174298"
    bom_data = load_csv(bom_data_url)

    sap_data_url = "https://docs.google.com/spreadsheets/d/1jnuEazMkGxbXcvP2mvvGlRZQZR-N0FMPf3aNhw5lH7Q/export?format=csv&gid=826586568"
    sap_data = load_csv(sap_data_url)
    sap_dataset = load_csv(sap_data_url)

    bom_data.columns = bom_data.columns.str.strip()

    sap_data.columns = sap_data.columns.str.strip()
    sap_dataset.columns = sap_dataset.columns.str.strip()

    sap_data['Qty Requested'] = pd.to_numeric(sap_data['Qty Requested'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Qty Requested'] = sap_data['Qty Requested'].fillna(0).astype(int)
    sap_data['Ordered'] = pd.to_numeric(sap_data['Ordered'].str.replace(',', '').str.replace('.', ''), errors='coerce')
    sap_data['Ordered'] = sap_data['Ordered'].fillna(0).astype(int)

    filtered_bom_data = bom_data[bom_data["Kode Material Delete"].isna()]
    filtered_sap_data = sap_data.groupby('Kode Material', as_index=False).agg({
        'Mat. Description': 'first',
        'Spesifikasi': 'first',
        'PR Status': 'first',
        'Qty Requested': 'sum',
        'Ordered': 'sum'
    })
    merge_data = pd.merge(filtered_bom_data, filtered_sap_data, on='Kode Material', how='inner')
    stockData = merge_data[(merge_data["Kode Material Stock"].isna() & (merge_data["Stock Total"] > 0.0)) | (merge_data["Kode Material Stock"].notna() & ((merge_data['Stock Total'] > 0.0) | (merge_data['Stock Total'] == 0.0)))]
    otherData = merge_data[merge_data['Kode Material Stock'].isna() & (merge_data['Stock Total'] == 0.0)]
    belumPRData = otherData[(otherData["QTY PR TOTAL"] > otherData["Qty Requested"])]
    sudahPRData = otherData[otherData["QTY PR TOTAL"] == otherData["Qty Requested"]]
    partialPRData = otherData[otherData["QTY PR TOTAL"] < otherData["Qty Requested"]]

    statusN = filtered_sap_data[filtered_sap_data['PR Status'] == 'N']
    statusAorK = filtered_sap_data[(filtered_sap_data['PR Status'] == 'A') | (filtered_sap_data['PR Status'] == 'K')]
    statusB = filtered_sap_data[filtered_sap_data['PR Status'] == 'B']

    with st.container(border=True):
        col1, col2 = st.columns(2, border=True)
        
        with col1:
            st.subheader("Total Data BOM")
            bom_data_total = len(bom_data)
            st.markdown(f"# {bom_data_total}")
        with col2:
            st.subheader("Total Data SAP")
            sap_data_total = len(sap_data)
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
                pochart = px.pie(df, values = 'Total', names = 'Status PO')
                st.subheader("Purchase Order")
                st.plotly_chart(pochart, width="stretch", height=450)
        with st.container(border=True):
            st.subheader(f"Bill Of Material Dataset")
            bom_data
    
        with st.container(border=True):
            st.subheader(f"SAP Dataset")
            sap_data
    
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
            partialPRData



page_names_to_funcs = {
    "Material Planning": main,
    "Material Planning 2": second,
}

demo_name = st.sidebar.selectbox("Choose a demo", page_names_to_funcs.keys())
page_names_to_funcs[demo_name]()
