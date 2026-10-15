"""Aplikasi uji AppTest: alur Statistic Dashboard tanpa tulisan Sheets asli.

Dijalankan oleh tests/test_app.py lewat AppTest.from_file(). Dua penyesuaian:

1. Tulisan ke Google Sheets ditangkap (open_spreadsheet + write_dataframe
   dipalsukan) dan dicatat ke session_state['_writes'].
2. AppTest tidak mendukung file_uploader, sehingga tombol digantikan flag di
   session_state — set sebelum at.run():
       simulate_bom  = True  (plus bom_bytes: bytes CSV)
       simulate_sap  = True  (plus sap_bytes: bytes CSV)
       simulate_generate = True
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gspread
import streamlit as st

import material_planning_2 as mp2


class FakeWorksheet:
    """Meniru gspread.Worksheet: cukup get_all_records untuk restore."""

    def __init__(self, records: list):
        self._records = records

    def get_all_records(self, **kwargs) -> list:
        return self._records


class FakeSpreadsheet:
    def __init__(self, name: str):
        self.name = name
        self.url = "https://example.test/spreadsheet"

    def worksheet(self, tab_name: str):
        # Rekaman tab diset lewat session_state['_fake_tabs'] (dict tab -> records).
        tabs = st.session_state.get("_fake_tabs") or {}
        if tab_name not in tabs:
            raise gspread.exceptions.WorksheetNotFound(tab_name)
        return FakeWorksheet(tabs[tab_name])


def _fake_open_spreadsheet(spreadsheet_name: str, share_email: str = ""):
    return FakeSpreadsheet(spreadsheet_name)


def _fake_write_dataframe(spreadsheet, df, tab_name: str) -> int:
    writes = st.session_state.setdefault("_writes", [])
    writes.append((tab_name, len(df)))
    return len(df)


# Patch level modul (dipakai write_step_result & run_generate saat runtime).
mp2.open_spreadsheet = _fake_open_spreadsheet
mp2.write_dataframe = _fake_write_dataframe


# --- simulasi tombol (dijalankan sebelum render agar hasil tampil sekali) ---
# Meniru alur asli: file sudah berupa daftar sumber siap proses (pada
# aplikasi asli, sumber disiapkan prepare_upload_state saat upload:
# CSV -> bytes; Excel -> grid per sheet).

if st.session_state.pop("simulate_bom", False):
    mp2.run_bom_etl(
        [
            {
                "label": st.session_state.get("bom_name", "bom.csv"),
                "bytes": st.session_state["bom_bytes"],
            }
        ],
        mp2.DEFAULT_SPREADSHEET,
        "",
    )

if st.session_state.pop("simulate_sap", False):
    mp2.run_sap_etl(
        [{"label": "SAP.csv", "bytes": st.session_state["sap_bytes"]}],
        mp2.DEFAULT_SPREADSHEET,
        "",
    )

if st.session_state.pop("simulate_generate", False):
    mp2.run_generate(mp2.DEFAULT_SPREADSHEET, "")

# Simulasi tombol sidebar "Muat hasil dari spreadsheet": membaca tab
# tiruan dari session_state['_fake_tabs'] (lihat FakeSpreadsheet).
if st.session_state.pop("simulate_restore", False):
    mp2.restore_from_spreadsheet(mp2.DEFAULT_SPREADSHEET, "")

mp2.render()
