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

import pandas as pd
import streamlit as st

import material_planning_3 as mp3


def _fake_load_bom_data(url: str) -> pd.DataFrame:
    csv_bytes = st.session_state.get("_fake_csv")
    if csv_bytes is None:
        return mp3.load_bom_data.__wrapped__(url)
    # Lewati read_bom_csv (bukan pd.read_csv biasa) supaya kolom qty tetap
    # diproses persis seperti jalur produksi — teks -> parse_amount.
    return mp3.read_bom_csv(csv_bytes)


def _fake_load_sap_data():
    """Tiruan loader SAP: baca session_state['_fake_sap_csv'] (bytes CSV).

    Sengaja lewat mp3.read_sap_csv (bukan pd.read_csv biasa) supaya
    pembersihannya persis seperti jalur produksi: header kotor
    ('      Ordered'), qty teks -> parse_amount, buang duplikat.

    Tanpa flag itu, validasi SAP dinonaktifkan dengan pesan galat — uji
    tidak boleh menyentuh jaringan/secrets (lihat docstring modul).
    """
    csv_bytes = st.session_state.get("_fake_sap_csv")
    if csv_bytes is None:
        return None, "sumber SAP tiruan belum disiapkan"
    try:
        return mp3.read_sap_csv(csv_bytes), None
    except Exception as exc:
        return None, str(exc)


# Patch level modul (dipakai render_dashboard saat runtime).
mp3.load_bom_data = _fake_load_bom_data
mp3.load_sap_data = _fake_load_sap_data

mp3.render()
