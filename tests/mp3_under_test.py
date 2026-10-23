"""Aplikasi uji AppTest: halaman Statistic Dashboard 2 (material_planning_3).

Dijalankan oleh tests/test_app.py lewat AppTest.from_file(). Halaman ini
sumber datanya export CSV Google Sheets (lihat material_planning_3), jadi
uji tidak boleh bergantung jaringan: set session_state['_fake_csv'] =
bytes CSV sebelum at.run() untuk menyuntikkan data tiruan (load_bom_data
dipalsukan di bawah). Tanpa flag itu, halaman memuat data asli.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import io

import pandas as pd
import streamlit as st

import material_planning_3 as mp3


def _fake_load_bom_data(url: str) -> pd.DataFrame:
    csv_bytes = st.session_state.get("_fake_csv")
    if csv_bytes is None:
        return mp3.load_bom_data.__wrapped__(url)
    return pd.read_csv(io.BytesIO(csv_bytes), low_memory=False)


# Patch level modul (dipakai render_dashboard saat runtime).
mp3.load_bom_data = _fake_load_bom_data

mp3.render()
