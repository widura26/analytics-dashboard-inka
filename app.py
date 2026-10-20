import streamlit as st

import bom_firestore
import material_planning_2 as mp2


st.set_page_config(
    page_title="Analytics Dashboard INKA",
    layout="wide",
)


def page_statistic_dashboard():
    mp2.render()


def page_kelola_data_bom():
    bom_firestore.render()


pages = [
    st.Page(
        page_statistic_dashboard,
        title="Statistic Dashboard",
        icon=":material/monitoring:",
        url_path="statistic-dashboard",
        default=True,
    ),
    st.Page(
        page_kelola_data_bom,
        title="Kelola Data BOM",
        icon=":material/factory:",
        url_path="kelola-data-bom",
    ),
]

navigation = st.navigation(
    {"Menu": pages},
    position="sidebar",
    expanded=True,
)

navigation.run()
