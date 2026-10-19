import streamlit as st

import bom_firestore
import material_planning_2 as mp2


st.set_page_config(layout="wide")


def fourth():
    mp2.render()


def fifth():
    bom_firestore.render()


menus = {
    "Statistic Dashboard": fourth,
    "Kelola Data BOM": fifth,
}


def select_menu(label: str):
    st.session_state.menu = label


if st.session_state.get("menu") not in menus:
    st.session_state.menu = next(iter(menus))

with st.sidebar:
    st.subheader("Menu")
    for label in menus:
        st.button(
            label,
            key=f"menu_{label}",
            width="stretch",
            type="primary" if st.session_state.menu == label else "secondary",
            on_click=select_menu,
            args=(label,),
        )

menus[st.session_state.menu]()
