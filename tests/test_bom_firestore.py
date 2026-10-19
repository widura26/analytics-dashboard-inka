"""Unit test menu Kelola Data BOM (Firestore): payload, validasi, CRUD, pengaturan.

Jalankan:  python tests/test_bom_firestore.py   (dari root proyek)
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

import bom_firestore as bf


# ---------- tiruan Firestore (tanpa jaringan) ----------

class FakeDoc:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self.exists = True
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeMissing:
    def __init__(self, doc_id: str):
        self.id = doc_id
        self.exists = False

    def to_dict(self):
        return None


class FakeRef:
    def __init__(self, doc_id: str):
        self.id = doc_id


class FakeDocumentRef:
    def __init__(self, collection: "FakeCollection", doc_id: str):
        self._collection = collection
        self.id = doc_id

    def update(self, data: dict) -> None:
        self._collection.update(self.id, data)

    def delete(self) -> None:
        self._collection.remove(self.id)

    def get(self):
        doc = self._collection.find(self.id)
        return doc if doc else FakeMissing(self.id)


class FakeCollection:
    """Cukup alur yang dipakai aplikasi: add, where, limit, document()."""

    def __init__(self, docs: list):
        self.docs = list(docs)
        self.added = []

    def find(self, doc_id: str):
        for doc in self.docs:
            if doc.id == doc_id:
                return doc
        return None

    def update(self, doc_id: str, data: dict) -> None:
        doc = self.find(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        doc._data.update(data)

    def remove(self, doc_id: str) -> None:
        self.docs = [d for d in self.docs if d.id != doc_id]

    def document(self, doc_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self, doc_id)

    def add(self, data: dict):
        self.added.append(data)
        self.docs.append(FakeDoc("doc-baru", dict(data)))
        return None, FakeRef("doc-baru")

    def where(self, filter=None, **kwargs):
        # filter = google.cloud.firestore_v1.base_query.FieldFilter
        matched = [
            d for d in self.docs
            if d.to_dict().get(filter.field_path) == filter.value
        ]
        return FakeQuery(matched)

    def limit(self, n: int):
        return FakeQuery(self.docs[:n])


class FakeQuery:
    def __init__(self, docs: list):
        self._docs = docs

    def limit(self, n: int):
        return FakeQuery(self._docs[:n])

    def stream(self):
        return iter(self._docs)


class FakeClient:
    def __init__(self, docs: list):
        self.collections = {}
        self._docs = docs

    def collection(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection(self._docs)
        return self.collections[name]


def use_client(client: FakeClient):
    """Ganti koneksi Firestore modul selama satu test."""
    bf.connect_firestore = lambda *args, **kwargs: client
    use_client.last_client = client


def sample_docs() -> list:
    older = dt.datetime(2026, 1, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    newer = dt.datetime(2026, 2, 2, 9, 30, 0, tzinfo=dt.timezone.utc)
    return [
        FakeDoc("lama", {
            "proyek": "KRL", "nama_bom": "Body", "nomor_bom": "BOM-1",
            "tanggal_terbit": "2026-01-01", "dibuat_pada": older,
        }),
        FakeDoc("baru", {
            "proyek": "MRT", "nama_bom": "Bogie", "nomor_bom": "BOM-2",
            "tanggal_terbit": "2026-02-02", "dibuat_pada": newer,
        }),
    ]


# ---------- payload & validasi ----------

def test_build_payload():
    payload = bf.build_payload(
        "  KRL INKA ", " BOM Body ", " BOM-2026-001 ", dt.date(2026, 10, 19),
    )
    assert payload == {
        "proyek": "KRL INKA",
        "nama_bom": "BOM Body",
        "nomor_bom": "BOM-2026-001",
        "tanggal_terbit": "2026-10-19",
    }, payload

    # datetime (hasil date_input ganda) dan string ikut dinormalkan.
    dari_datetime = bf.build_payload("a", "b", "c", dt.datetime(2026, 3, 5))
    assert dari_datetime["tanggal_terbit"] == "2026-03-05", dari_datetime
    dari_string = bf.build_payload("a", "b", "c", "2026-03-06")
    assert dari_string["tanggal_terbit"] == "2026-03-06", dari_string
    assert bf.build_payload(None, "", " ", None) == {
        "proyek": "", "nama_bom": "", "nomor_bom": "", "tanggal_terbit": "",
    }
    print("[OK] build_payload: rapikan + tanggal ISO")


def test_payload_errors():
    valid = bf.build_payload("KRL", "Body", "BOM-1", dt.date(2026, 10, 19))
    assert bf.payload_errors(valid) == [], bf.payload_errors(valid)

    kosong = bf.build_payload("", "", "", None)
    errors = bf.payload_errors(kosong)
    assert len(errors) == 4, errors
    assert all("wajib diisi" in message for message in errors), errors

    salah = bf.build_payload("KRL", "Body", "BOM-1", "19-10-2026")
    errors = bf.payload_errors(salah)
    assert len(errors) == 1 and "Tanggal Terbit" in errors[0], errors
    print("[OK] payload_errors: wajib isi + tanggal valid")


def test_parse_date():
    assert bf.parse_date("2026-10-19") == dt.date(2026, 10, 19)
    assert bf.parse_date("") == dt.date.today()
    assert bf.parse_date("bukan-tanggal") == dt.date.today()
    print("[OK] parse_date: ISO -> date, gagal -> hari ini")


# ---------- CRUD ----------

def test_add_document():
    client = FakeClient([])
    use_client(client)

    doc_id = bf.add_document(
        bf.build_payload("KRL", "Body", "BOM-1", dt.date(2026, 10, 19))
    )

    assert doc_id == "doc-baru", doc_id
    saved = client.collection(bf.DEFAULT_COLLECTION).added
    assert len(saved) == 1, saved
    assert saved[0]["proyek"] == "KRL"
    assert saved[0]["tanggal_terbit"] == "2026-10-19"
    assert "dibuat_pada" in saved[0] and "diperbarui_pada" in saved[0]
    print("[OK] add_document: 1 dokumen + timestamp server")


def test_get_document():
    use_client(FakeClient(sample_docs()))

    row = bf.get_document("baru")
    assert row["nomor_bom"] == "BOM-2", row
    assert row["id"] == "baru"
    assert bf.get_document("tidak-ada") is None
    print("[OK] get_document: ada & tidak ada")


def test_update_document():
    client = FakeClient(sample_docs())
    use_client(client)

    payload = bf.build_payload("MRT", "Bogie Baru", "BOM-2X", "2026-03-03")
    bf.update_document("baru", payload)

    row = bf.get_document("baru")
    assert row["nama_bom"] == "Bogie Baru", row
    assert row["nomor_bom"] == "BOM-2X", row
    assert row["tanggal_terbit"] == "2026-03-03", row
    # Dokumen lain tidak ikut berubah.
    assert bf.get_document("lama")["nama_bom"] == "Body"
    print("[OK] update_document: field berubah, dokumen lain aman")


def test_delete_document():
    client = FakeClient(sample_docs())
    use_client(client)

    bf.delete_document("lama")

    assert bf.get_document("lama") is None
    assert [r["id"] for r in bf.fetch_recent()] == ["baru"]
    print("[OK] delete_document: hilang dari koleksi")


def test_find_by_nomor_bom():
    use_client(FakeClient(sample_docs()))

    assert [r["id"] for r in bf.find_by_nomor_bom("BOM-2")] == ["baru"]
    assert bf.find_by_nomor_bom("BOM-TIDAK-ADA") == []
    print("[OK] find_by_nomor_bom: cocok & tidak cocok")


def test_fetch_recent():
    use_client(FakeClient(sample_docs()))

    rows = bf.fetch_recent(limit=1)
    assert [r["id"] for r in rows] == ["baru"], rows

    rows = bf.fetch_recent(limit=10)
    assert [r["id"] for r in rows] == ["baru", "lama"], rows
    assert rows[0]["nomor_bom"] == "BOM-2"
    print("[OK] fetch_recent: urut turun by diperbarui/dibuat + limit")


def test_fetch_recent_koleksi_kosong():
    use_client(FakeClient([]))
    assert bf.fetch_recent() == []
    print("[OK] fetch_recent: koleksi kosong -> []")


def test_display_frame():
    rows = [
        {
            "id": "baru", "proyek": "MRT", "nama_bom": "Bogie",
            "nomor_bom": "BOM-2", "tanggal_terbit": "2026-02-02",
            "dibuat_pada": dt.datetime(2026, 2, 2, 9, 30),
        },
        # Dokumen lama tanpa field baru tetap tampil.
        {"id": "aneh", "proyek": "LAMA"},
    ]
    frame = bf._display_frame(rows)

    assert frame.columns.tolist() == (
        ["Proyek", "Nama BOM", "Nomor BOM", "Tanggal Terbit",
         "Dibuat Pada", "ID Dokumen"]
    ), frame.columns.tolist()
    assert len(frame) == 2, len(frame)
    assert frame.iloc[0]["Dibuat Pada"] == "2026-02-02 09:30:00"
    assert frame.iloc[1]["Nomor BOM"] == "", frame.iloc[1].tolist()
    assert frame.iloc[1]["ID Dokumen"] == "aneh"
    print("[OK] _display_frame: kolom lengkap walau field hilang")


# ---------- guard duplikat ----------

def test_guard_duplicate_ditahan():
    use_client(FakeClient(sample_docs()))
    payload = bf.build_payload("MRT", "Bogie", "BOM-2", dt.date(2026, 2, 2))

    try:
        st.session_state.pop("bomfs_pending", None)
        st.session_state.pop("bomfs_override", None)

        # Nomor BOM kembar -> ditahan, payload disimpan sementara.
        assert bf._guard_duplicate(payload) is False
        assert st.session_state.get("bomfs_pending") == payload

        # Override ditekan -> _resolve_pending melanjutkan penyimpanan.
        st.session_state["bomfs_override"] = True
        bf._resolve_pending()

        assert st.session_state.get("bomfs_pending") is None
        assert st.session_state.get("bomfs_override") is None
        saved = use_client.last_client.collections[bf.DEFAULT_COLLECTION].added
        assert len(saved) == 1, saved
        print("[OK] _guard_duplicate: kembar ditahan, override menyimpan")
    finally:
        st.session_state.pop("bomfs_pending", None)
        st.session_state.pop("bomfs_override", None)


def test_guard_duplicate_diubah():
    # Saat mengubah data, nomor BOM sendiri tidak dianggap kembar.
    use_client(FakeClient(sample_docs()))
    payload = bf.build_payload("MRT", "Bogie", "BOM-2", dt.date(2026, 2, 2))

    assert bf._guard_duplicate(payload, exclude_id="baru") is True

    # Kembar dengan dokumen lain -> ditolak (tanpa alur tunda).
    payload = bf.build_payload("MRT", "Bogie", "BOM-1", dt.date(2026, 2, 2))
    assert bf._guard_duplicate(payload, exclude_id="baru") is False
    assert st.session_state.get("bomfs_pending") is None
    print("[OK] _guard_duplicate: exclude_id saat ubah data")


def test_guard_duplicate_pemeriksaan_gagal():
    original = bf.find_by_nomor_bom

    def boom(*args, **kwargs):
        raise RuntimeError("jaringan mati")

    bf.find_by_nomor_bom = boom
    try:
        payload = bf.build_payload("KRL", "Body", "BOM-9", dt.date(2026, 1, 9))
        # Pemeriksaan sekunder gagal -> penyimpanan tetap diteruskan.
        assert bf._guard_duplicate(payload) is True
        assert st.session_state.get("bomfs_pending") is None
        print("[OK] _guard_duplicate: gagal cek tidak menghalangi simpan")
    finally:
        bf.find_by_nomor_bom = original


# ---------- pengaturan tersimpan ----------

def test_action_frame():
    rows = [{
        "id": "d1", "proyek": "KRL", "nama_bom": "Body",
        "nomor_bom": "BOM-1", "tanggal_terbit": "2026-01-01",
        "dibuat_pada": dt.datetime(2026, 1, 1),
    }]
    frame = bf._action_frame(rows)

    # Kolom aksi ada di akhir, berisi label tombol per baris.
    assert frame.columns.tolist()[-2:] == ["Update", "Hapus"], frame.columns
    assert frame["Update"].tolist() == ["Update"]
    assert frame["Hapus"].tolist() == ["Hapus"]
    print("[OK] _action_frame: kolom Update & Hapus per baris")


def test_action_click():
    try:
        st.session_state[bf.ROW_IDS_KEY] = ["id-a", "id-b"]
        st.session_state.pop("bomfs_dialog", None)

        # Klik tombol Update di baris ke-2 -> modal edit dokumen ke-2.
        st.session_state["bomfs_action_edit"] = {"row": 1, "label": "Update"}
        bf._on_action_click("edit")
        assert st.session_state.get("bomfs_dialog") == {
            "mode": "edit", "id": "id-b",
        }, st.session_state.get("bomfs_dialog")

        # Klik tombol Hapus di baris ke-1 -> modal hapus dokumen pertama.
        st.session_state["bomfs_action_delete"] = {"row": 0, "label": "Hapus"}
        bf._on_action_click("delete")
        assert st.session_state.get("bomfs_dialog") == {
            "mode": "delete", "id": "id-a",
        }, st.session_state.get("bomfs_dialog")

        # Baris di luar jarak data -> diabaikan (state tidak berubah).
        st.session_state["bomfs_action_edit"] = {"row": 99, "label": "Update"}
        bf._on_action_click("edit")
        assert st.session_state["bomfs_dialog"]["mode"] == "delete"

        # Tanpa klik sama sekali -> diabaikan.
        st.session_state.pop("bomfs_action_edit", None)
        bf._on_action_click("edit")
        assert st.session_state["bomfs_dialog"]["mode"] == "delete"
        print("[OK] _on_action_click: baris -> dokumen, batas aman")
    finally:
        for key in (bf.ROW_IDS_KEY, "bomfs_dialog",
                    "bomfs_action_edit", "bomfs_action_delete"):
            st.session_state.pop(key, None)


def test_settings_roundtrip():
    original_path = bf.SETTINGS_PATH

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "firestore_settings.json"
        bf.SETTINGS_PATH = path
        try:
            # File belum ada -> default.
            settings = bf.load_settings()
            assert settings == {
                "project": "",
                "database": bf.DEFAULT_DATABASE,
                "collection": bf.DEFAULT_COLLECTION,
                "folder": bf.drive_upload.DEFAULT_FOLDER,
                "share_email": "",
            }, settings

            bf.save_settings({
                "project": "ppic-analytics-dashboard",
                "database": "(default)",
                "collection": "bill_of_materials",
                "folder": "Arsip BOM",
                "share_email": "user@contoh.co.id",
            })
            settings = bf.load_settings()
            assert settings["project"] == "ppic-analytics-dashboard"
            assert settings["collection"] == "bill_of_materials"
            assert settings["folder"] == "Arsip BOM"
            assert settings["share_email"] == "user@contoh.co.id"

            # File rusak -> tidak bikin crash, kembali default.
            path.write_text("{rusak", encoding="utf-8")
            assert bf.load_settings()["collection"] == bf.DEFAULT_COLLECTION
        finally:
            bf.SETTINGS_PATH = original_path

    print("[OK] load/save_settings: tersimpan di file + tahan file rusak")


def test_session_settings():
    try:
        st.session_state["bomfs_collection"] = "  uji_koleksi  "
        assert bf.collection_name() == "uji_koleksi"
        st.session_state["bomfs_database"] = "  produksi  "
        assert bf.database_id() == "produksi"
        st.session_state["bomfs_project"] = "  proyek-lain  "
        assert bf.target_project_id() == "proyek-lain"

        # Kosong -> kembali ke default (koleksi/database) & key (project).
        st.session_state["bomfs_collection"] = "   "
        st.session_state["bomfs_database"] = "   "
        st.session_state["bomfs_project"] = "   "
        assert bf.collection_name() == bf.DEFAULT_COLLECTION
        assert bf.database_id() == bf.DEFAULT_DATABASE
        assert bf.target_project_id() == bf.project_id()
        print("[OK] nama koleksi/database/project: trim + fallback")
    finally:
        for key in ("bomfs_collection", "bomfs_database", "bomfs_project"):
            st.session_state.pop(key, None)


if __name__ == "__main__":
    test_build_payload()
    test_payload_errors()
    test_parse_date()
    test_add_document()
    test_get_document()
    test_update_document()
    test_delete_document()
    test_find_by_nomor_bom()
    test_fetch_recent()
    test_fetch_recent_koleksi_kosong()
    test_display_frame()
    test_action_frame()
    test_action_click()
    test_guard_duplicate_ditahan()
    test_guard_duplicate_diubah()
    test_guard_duplicate_pemeriksaan_gagal()
    test_settings_roundtrip()
    test_session_settings()
    print("\nSEMUA TEST LULUS")
