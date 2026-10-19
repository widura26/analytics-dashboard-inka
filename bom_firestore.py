"""Menu "Kelola Data BOM": tambah, ubah, dan hapus data di Firestore.

Tampilan sederhana: satu tabel sebagai pusat halaman, tombol
**Update** dan **Hapus** untuk baris terpilih (masing-masing membuka
modal), serta tombol **Tambah Data**.

Field tiap dokumen: proyek, nama_bom, nomor_bom, tanggal_terbit
(tanggal disimpan sebagai teks ISO YYYY-MM-DD) + metadata dibuat_pada
/ diperbarui_pada (timestamp server).

Koneksi:
- Kredensial memakai service account yang sama dengan menu Statistic
  Dashboard (st.secrets["gcp_service_account"]); bila secrets tidak
  ada, dipakai credentials.json di root proyek.
- Pengaturan (project, database, koleksi) disimpan ke
  firestore_settings.json sehingga tidak perlu diisi ulang tiap reload.

Impor google.cloud.firestore sengaja ditunda ke dalam fungsi (lazy)
supaya start Statistic Dashboard tidak menanggung biaya import grpc.
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import streamlit as st

import drive_upload

ROOT = Path(__file__).resolve().parent

SETTINGS_PATH = ROOT / "firestore_settings.json"

FIRESTORE_SCOPES = ["https://www.googleapis.com/auth/datastore"]

DEFAULT_COLLECTION = "bill_of_materials"

# "(default)" = database tunggal; dipakai bila dibuat database bernama.
DEFAULT_DATABASE = "(default)"

# Batas baris yang diambil lalu diurutkan di sisi klien, supaya tidak
# butuh index komposit di Firestore.
SCAN_LIMIT = 200
RECENT_LIMIT = 20

# Kolom Firestore <-> label tampilan.
FIELD_LABELS = {
    "proyek": "Proyek",
    "nama_bom": "Nama BOM",
    "nomor_bom": "Nomor BOM",
    "tanggal_terbit": "Tanggal Terbit",
}

FIELDS = tuple(FIELD_LABELS)

COL_CREATED = "dibuat_pada"
COL_UPDATED = "diperbarui_pada"
COL_ID = "id"

# Kolom aksi per baris (tombol dalam sel tabel).
ACTION_COLS = {"bomfs_action_edit": "edit", "bomfs_action_delete": "delete"}
ACTION_LABELS = {"edit": "Update", "delete": "Hapus"}

# ID dokumen tiap baris tabel; dipakai callback tombol aksi untuk
# menerjemahkan indeks baris -> ID dokumen.
ROW_IDS_KEY = "bomfs_row_ids"


# ============================================================
# PENGATURAN (tersimpan di file, bertahan setelah reload)
# ============================================================

def load_settings() -> dict:
    """Baca pengaturan koneksi; file hilang/rusak -> pakai default."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    return {
        "project": str(data.get("project") or "").strip(),
        "database": str(data.get("database") or "").strip() or DEFAULT_DATABASE,
        "collection": (
            str(data.get("collection") or "").strip() or DEFAULT_COLLECTION
        ),
        "folder": (
            str(data.get("folder") or "").strip() or drive_upload.DEFAULT_FOLDER
        ),
        "share_email": str(data.get("share_email") or "").strip(),
    }


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _current_settings() -> dict:
    """Pengaturan hasil isi kolom sidebar (sudah dinormalkan)."""
    return {
        "project": str(st.session_state.get("bomfs_project", "")).strip(),
        "database": (
            str(st.session_state.get("bomfs_database", "")).strip()
            or DEFAULT_DATABASE
        ),
        "collection": (
            str(st.session_state.get("bomfs_collection", "")).strip()
            or DEFAULT_COLLECTION
        ),
        "folder": (
            str(st.session_state.get("bomfs_folder", "")).strip()
            or drive_upload.DEFAULT_FOLDER
        ),
        "share_email": str(
            st.session_state.get("bomfs_share_email", "")
        ).strip(),
    }


def _persist_settings() -> None:
    """Simpan pengaturan bila berubah; dipanggil tiap render sidebar."""
    current = _current_settings()

    if current == load_settings():
        return

    try:
        save_settings(current)
    except Exception as exc:
        st.warning(
            f"Pengaturan tidak bisa disimpan ke {SETTINGS_PATH.name}: {exc}"
        )


# ============================================================
# KREDENSIAL & KONEKSI
# ============================================================

def service_account_info() -> dict:
    """Ambil kunci service account: secrets dulu, credentials.json cadangan."""
    try:
        info = st.secrets.get("gcp_service_account")
    except Exception:
        info = None

    if info:
        return dict(info)

    path = ROOT / "credentials.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    raise FileNotFoundError(
        "Kredensial tidak ditemukan. Isi "
        "'.streamlit/secrets.toml' [gcp_service_account] atau sediakan "
        "credentials.json di root proyek."
    )


def service_account_email() -> str:
    try:
        return str(service_account_info().get("client_email", ""))
    except Exception:
        return ""


def project_id() -> str:
    """Project milik kunci service account."""
    try:
        return str(service_account_info().get("project_id", ""))
    except Exception:
        return ""


@st.cache_resource
def connect_firestore(database: str = DEFAULT_DATABASE, project: str = ""):
    from google.cloud import firestore
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_info(
        service_account_info(),
        scopes=FIRESTORE_SCOPES,
    )
    return firestore.Client(
        credentials=credentials,
        project=project or credentials.project_id,
        database=database,
    )


def database_id() -> str:
    name = str(st.session_state.get("bomfs_database", DEFAULT_DATABASE))
    name = name.strip()
    return name or DEFAULT_DATABASE


def target_project_id() -> str:
    """Project GCP tempat database berada; kosong = project service account."""
    name = str(st.session_state.get("bomfs_project", "")).strip()
    return name or project_id()


def collection_name() -> str:
    name = str(st.session_state.get("bomfs_collection", DEFAULT_COLLECTION))
    name = name.strip()
    return name or DEFAULT_COLLECTION


def _client():
    return connect_firestore(database_id(), target_project_id())


def _target_hint() -> str:
    return (
        f"Target: project `{target_project_id() or '?'}`, "
        f"database `{database_id()}`, koleksi `{collection_name()}`."
    )


# ============================================================
# LOGIKA DATA (tanpa Streamlit, aman diuji)
# ============================================================

def build_payload(proyek, nama_bom, nomor_bom, tanggal_terbit) -> dict:
    """Rapikan input jadi dokumen Firestore (tanggal disimpan ISO YYYY-MM-DD)."""
    if isinstance(tanggal_terbit, dt.datetime):
        tanggal_terbit = tanggal_terbit.date()

    if isinstance(tanggal_terbit, dt.date):
        tanggal = tanggal_terbit.isoformat()
    else:
        tanggal = str(tanggal_terbit or "").strip()

    return {
        "proyek": str(proyek or "").strip(),
        "nama_bom": str(nama_bom or "").strip(),
        "nomor_bom": str(nomor_bom or "").strip(),
        "tanggal_terbit": tanggal,
    }


def payload_errors(payload: dict) -> list:
    """Daftar pesan kesalahan payload; kosong berarti payload valid."""
    errors = []

    for key in FIELDS:
        if not str(payload.get(key) or "").strip():
            errors.append(f"{FIELD_LABELS[key]} wajib diisi.")

    tanggal = str(payload.get("tanggal_terbit") or "")
    if tanggal:
        try:
            dt.date.fromisoformat(tanggal)
        except ValueError:
            errors.append("Tanggal Terbit harus berupa tanggal yang valid.")

    return errors


def parse_date(value):
    """Teks ISO -> date (untuk mengisi form edit); gagal -> hari ini."""
    try:
        return dt.date.fromisoformat(str(value or "").strip())
    except ValueError:
        return dt.date.today()


def _doc_to_row(doc) -> dict:
    row = {COL_ID: doc.id}
    row.update(doc.to_dict() or {})
    return row


def _created_timestamp(row: dict) -> float:
    value = row.get(COL_UPDATED) or row.get(COL_CREATED)

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.timestamp()

    return 0.0


def add_document(payload: dict) -> str:
    """Tambah satu dokumen; kembalikan ID dokumennya."""
    from google.cloud import firestore

    data = dict(payload)
    data[COL_CREATED] = firestore.SERVER_TIMESTAMP
    data[COL_UPDATED] = firestore.SERVER_TIMESTAMP

    _, ref = _client().collection(collection_name()).add(data)
    return ref.id


def update_document(doc_id: str, payload: dict) -> None:
    from google.cloud import firestore

    data = dict(payload)
    data[COL_UPDATED] = firestore.SERVER_TIMESTAMP
    _client().collection(collection_name()).document(doc_id).update(data)


def delete_document(doc_id: str) -> None:
    _client().collection(collection_name()).document(doc_id).delete()


def get_document(doc_id: str):
    """Ambil satu dokumen (None bila tidak ada)."""
    snapshot = _client().collection(collection_name()).document(doc_id).get()
    return _doc_to_row(snapshot) if snapshot.exists else None


def find_by_nomor_bom(nomor_bom: str, limit: int = 5) -> list:
    """Cari dokumen dengan nomor_bom sama (dicegah sebelum duplikat ditulis)."""
    from google.cloud import firestore

    query = (
        _client()
        .collection(collection_name())
        .where(filter=firestore.FieldFilter("nomor_bom", "==", nomor_bom))
        .limit(limit)
    )
    return [_doc_to_row(doc) for doc in query.stream()]


def fetch_recent(limit: int = RECENT_LIMIT) -> list:
    """Ambil dokumen terbaru (diurutkan turun by diperbarui/dibuat_pada)."""
    rows = [
        _doc_to_row(doc)
        for doc in _client()
        .collection(collection_name())
        .limit(SCAN_LIMIT)
        .stream()
    ]
    rows.sort(key=_created_timestamp, reverse=True)
    return rows[:limit]


def _display_frame(rows: list) -> pd.DataFrame:
    columns = list(FIELD_LABELS.values()) + ["Dibuat Pada", "ID Dokumen"]
    records = []

    for row in rows:
        record = {
            label: str(row.get(key) or "")
            for key, label in FIELD_LABELS.items()
        }
        record["Dibuat Pada"] = _format_created(row.get(COL_CREATED))
        record["ID Dokumen"] = str(row.get(COL_ID) or "")
        records.append(record)

    return pd.DataFrame(records, columns=columns)


def _format_created(value) -> str:
    # isinstance saja tidak cukup: NaT juga mengaku datetime.datetime.
    if isinstance(value, dt.datetime) and not pd.isna(value):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return ""


# ============================================================
# TAMPILAN — umum
# ============================================================

def _flash(message: str) -> None:
    """Pesan sukses yang tampil pada render berikutnya (setelah rerun)."""
    st.session_state["bomfs_flash"] = message


def _render_flash():
    message = st.session_state.pop("bomfs_flash", None)
    if message:
        st.success(message)


def _error_hint(exc: Exception) -> str:
    return (
        f"{exc}\n\n{_target_hint()}\n\n"
        "Pastikan database sudah dibuat, Firestore API aktif, dan "
        "service account punya peran Cloud Datastore User "
        "(roles/datastore.user)."
    )


def _row_label(row: dict) -> str:
    return " — ".join(
        [
            str(row.get("nomor_bom") or "(tanpa nomor)"),
            str(row.get("nama_bom") or "(tanpa nama)"),
            f"[{row.get('proyek') or '-'}]",
        ]
    )


def _open_dialog(mode: str, doc_id: str = "") -> None:
    """Buka modal (add/edit/delete); state-nya bertahan antar rerun."""
    st.session_state["bomfs_dialog"] = {"mode": mode, "id": doc_id}


def _close_dialog() -> None:
    st.session_state.pop("bomfs_dialog", None)


def _dialog_state() -> dict:
    return st.session_state.get("bomfs_dialog") or {}


# ============================================================
# TAMPILAN — sidebar
# ============================================================

def render_sidebar():
    settings = load_settings()

    with st.sidebar:
        st.header("Firestore")

        st.text_input(
            "Nama Koleksi",
            value=settings["collection"],
            key="bomfs_collection",
            help=(
                "Dokumen disimpan ke koleksi ini. Koleksi yang belum ada "
                "dibuat otomatis oleh Firestore saat dokumen pertama ditulis."
            ),
        )

        st.text_input(
            "ID Database",
            value=settings["database"],
            key="bomfs_database",
            help=(
                "(default) adalah database tunggal project. Isi nama "
                "database lain hanya bila Anda memang membuat database "
                "bernama (Firebase Console -> Firestore -> Databases)."
            ),
        )

        st.text_input(
            "Project ID Firestore",
            value=settings["project"],
            key="bomfs_project",
            placeholder=project_id(),
            help=(
                "Project GCP tempat database berada; kosongkan bila sama "
                "dengan project service account. Pengaturan ini tersimpan "
                "otomatis, tidak perlu diisi ulang tiap reload."
            ),
        )

        st.header("Google Drive")

        st.text_input(
            "Folder Drive (URL/ID Drive Bersama)",
            value=settings["folder"],
            key="bomfs_folder",
            help=(
                "Service account TIDAK punya kuota penyimpanan pribadi, "
                "jadi tujuan wajib berupa Drive Bersama. Caranya: "
                "1) Google Drive -> + New -> Shared drive; "
                "2) klik kanan drive-nya -> Manage members -> tambahkan "
                "email service account (lihat di bawah) sebagai Content "
                "Manager; 3) salin URL-nya (https://drive.google.com/"
                "drive/folders/<ID>) ke kolom ini. Simpan otomatis."
            ),
        )

        st.text_input(
            "Email penerima (opsional)",
            value=settings["share_email"],
            key="bomfs_share_email",
            placeholder="nama@perusahaan.co.id",
            help=(
                "File yang diunggah dibagikan ke email ini (baca) "
                "otomatis setelah upload. Berguna bila tujuannya folder "
                "service account."
            ),
        )

        email = service_account_email()
        if email:
            with st.expander("Service account"):
                st.caption(f"Project key: {project_id() or '?'}")
                st.code(email, language=None)

        _persist_settings()


# ============================================================
# TAMPILAN — form dalam modal
# ============================================================

def _form_body(defaults: dict, form_key: str):
    """Form 4 field di dalam modal; kembalikan (submitted, payload)."""
    defaults = defaults or {}

    with st.form(form_key):
        col_left, col_right = st.columns(2)

        with col_left:
            proyek = st.text_input(
                "Proyek",
                value=str(defaults.get("proyek") or ""),
                key=f"{form_key}_proyek",
                placeholder="mis. KRL INKA",
            )
            nomor_bom = st.text_input(
                "Nomor BOM",
                value=str(defaults.get("nomor_bom") or ""),
                key=f"{form_key}_nomor",
                placeholder="mis. BOM-2026-001",
            )

        with col_right:
            nama_bom = st.text_input(
                "Nama BOM",
                value=str(defaults.get("nama_bom") or ""),
                key=f"{form_key}_nama",
                placeholder="mis. BOM Body Carriage",
            )
            tanggal_terbit = st.date_input(
                "Tanggal Terbit",
                value=parse_date(defaults.get("tanggal_terbit")),
                key=f"{form_key}_tanggal",
            )

        submitted = st.form_submit_button(
            "Simpan",
            type="primary",
            width="stretch",
        )

    payload = build_payload(proyek, nama_bom, nomor_bom, tanggal_terbit)
    return submitted, payload


def _guard_duplicate(payload: dict, exclude_id: str = "") -> bool:
    """True bila payload siap disimpan; False bila ditahan menunggu konfirmasi."""
    try:
        existing = [
            row for row in find_by_nomor_bom(payload["nomor_bom"])
            if row[COL_ID] != exclude_id
        ]
    except Exception as exc:
        # Pemeriksaan sekunder: kegagalannya tidak boleh menghalangi
        # penyimpanan (kalaupun bermasalah, error tulis akan muncul sendiri).
        st.warning(
            f"Pemeriksaan nomor BOM ke Firestore gagal ({exc}); "
            "penyimpanan tetap dicoba."
        )
        return True

    if not existing:
        return True

    ids = ", ".join(row[COL_ID] for row in existing)
    st.warning(
        f"Nomor BOM '{payload['nomor_bom']}' sudah tercatat "
        f"({len(existing)} dokumen: {ids})."
    )

    if exclude_id:
        # Ubah data: tidak perlu alur tunda, cukup konfirmasi tombol.
        return False

    # Tambah data: tahan dulu; bila pengguna menekan override,
    # _resolve_pending menyimpannya pada render berikutnya.
    st.session_state["bomfs_pending"] = payload
    st.caption("Tekan 'Tetap simpan' bila tetap ingin menyimpan kembarannya.")

    if st.button("Tetap simpan", key="bomfs_override_btn"):
        st.session_state["bomfs_override"] = True
        st.rerun()

    return False


def _resolve_pending():
    """Lanjutkan penyimpanan yang ditunda karena nomor BOM kembar."""
    payload = st.session_state.get("bomfs_pending")

    if payload and st.session_state.pop("bomfs_override", False):
        try:
            doc_id = add_document(payload)
        except Exception as exc:
            st.error("Gagal menyimpan ke Firestore: " + _error_hint(exc))
            return

        st.session_state.pop("bomfs_pending", None)
        _close_dialog()
        _flash(f"Data tersimpan — ID dokumen: {doc_id}")


# ============================================================
# TAMPILAN — modal (dialog)
# ============================================================

@st.dialog("Tambah Data", width="large")
def dialog_add():
    submitted, payload = _form_body({}, "bomfs_add")

    if not submitted:
        return

    errors = payload_errors(payload)
    if errors:
        for message in errors:
            st.error(message)
        return

    if not _guard_duplicate(payload):
        return

    try:
        doc_id = add_document(payload)
    except Exception as exc:
        st.error("Gagal menyimpan ke Firestore: " + _error_hint(exc))
        return

    st.session_state.pop("bomfs_pending", None)
    st.session_state.pop("bomfs_override", None)
    _close_dialog()
    _flash(f"Data tersimpan — ID dokumen: {doc_id}")
    st.rerun()


@st.dialog("Ubah Data", width="large")
def dialog_edit(doc_id: str):
    try:
        row = get_document(doc_id)
    except Exception as exc:
        st.error("Gagal membuka dokumen: " + _error_hint(exc))
        return

    if row is None:
        st.info("Dokumen sudah tidak ada (mungkin sudah dihapus).")
        _close_dialog()
        st.rerun()
        return

    st.caption(f"ID dokumen: {doc_id}")

    submitted, payload = _form_body(row, f"bomfs_edit_{doc_id[:16]}")

    if st.button("Batal", width="stretch", key="bomfs_edit_cancel"):
        _close_dialog()
        st.rerun()
        return

    if not submitted:
        return

    errors = payload_errors(payload)
    if errors:
        for message in errors:
            st.error(message)
        return

    if not _guard_duplicate(payload, exclude_id=doc_id):
        st.info("Nomor BOM kembar dengan dokumen lain — perubahan dibatalkan.")
        return

    try:
        update_document(doc_id, payload)
    except Exception as exc:
        st.error("Gagal memperbarui Firestore: " + _error_hint(exc))
        return

    _close_dialog()
    _flash(f"Data diperbarui — ID dokumen: {doc_id}")
    st.rerun()


@st.dialog("Hapus Data", width="small")
def dialog_delete(doc_id: str):
    try:
        row = get_document(doc_id)
    except Exception as exc:
        st.error("Gagal membuka dokumen: " + _error_hint(exc))
        return

    if row is None:
        st.info("Dokumen sudah tidak ada.")
        _close_dialog()
        st.rerun()
        return

    st.warning("Data ini akan dihapus permanen.")
    st.code(f"{_row_label(row)}\nID: {doc_id}", language=None)

    col_hapus, col_batal = st.columns(2)

    if col_hapus.button(
        "Ya, hapus",
        type="primary",
        width="stretch",
        key="bomfs_confirm_delete",
    ):
        try:
            delete_document(doc_id)
        except Exception as exc:
            st.error("Gagal menghapus dari Firestore: " + _error_hint(exc))
            return

        _close_dialog()
        _flash(f"Data dihapus — ID dokumen: {doc_id}")
        st.rerun()

    if col_batal.button("Batal", width="stretch", key="bomfs_delete_cancel"):
        _close_dialog()
        st.rerun()


def _dispatch_dialog():
    """Render modal sesuai state; dialog harus dipanggil tiap rerun."""
    state = _dialog_state()
    mode = state.get("mode")

    if mode == "add":
        dialog_add()
    elif mode == "edit":
        dialog_edit(state.get("id", ""))
    elif mode == "delete":
        dialog_delete(state.get("id", ""))


# ============================================================
# TAMPILAN — halaman
# ============================================================

def _on_action_click(mode: str) -> None:
    """Callback tombol dalam sel: baris terklik -> buka modal aksi."""
    key = next(
        (k for k, action in ACTION_COLS.items() if action == mode),
        "",
    )
    click = st.session_state.get(key)

    try:
        row_index = int(click["row"])
    except Exception:
        return

    row_ids = st.session_state.get(ROW_IDS_KEY) or []
    if not 0 <= row_index < len(row_ids):
        return

    _open_dialog(mode, row_ids[row_index])


def _action_frame(rows: list) -> pd.DataFrame:
    """Tabel tampilan + kolom aksi (tombol Update & Hapus per baris)."""
    frame = _display_frame(rows)
    frame[ACTION_LABELS["edit"]] = ACTION_LABELS["edit"]
    frame[ACTION_LABELS["delete"]] = ACTION_LABELS["delete"]
    return frame


def _render_toolbar():
    """Baris atas: judul informasi di kiri, tombol tambah kecil di kanan."""
    col_info, col_add = st.columns([10, 2])

    with col_info:
        st.caption(f"{_target_hint()} — pengaturan tersimpan otomatis.")

    with col_add:
        if st.button(
            "Tambah Data",
            type="primary",
            icon=":material/add:",
            width="stretch",
            key="bomfs_btn_add",
        ):
            _open_dialog("add")


def _render_table(rows: list):
    # Petakan indeks baris -> ID dokumen, dipakai callback tombol aksi.
    st.session_state[ROW_IDS_KEY] = [row[COL_ID] for row in rows]

    st.dataframe(
        _action_frame(rows),
        column_config={
            "Proyek": st.column_config.TextColumn("Proyek", width="medium"),
            "Nama BOM": st.column_config.TextColumn("Nama BOM", width="medium"),
            "Nomor BOM": st.column_config.TextColumn("Nomor BOM", width="small"),
            "Tanggal Terbit": st.column_config.TextColumn(
                "Tanggal Terbit", width="small"
            ),
            "Dibuat Pada": st.column_config.TextColumn(
                "Dibuat Pada", width="small"
            ),
            "ID Dokumen": st.column_config.TextColumn(
                "ID Dokumen", width="medium"
            ),
            ACTION_LABELS["edit"]: st.column_config.ButtonColumn(
                ACTION_LABELS["edit"],
                width="small",
                type="secondary",
                help="Ubah data pada baris ini",
                on_click=_on_action_click,
                args=("edit",),
                key="bomfs_action_edit",
            ),
            ACTION_LABELS["delete"]: st.column_config.ButtonColumn(
                ACTION_LABELS["delete"],
                width="small",
                type="secondary",
                help="Hapus data pada baris ini",
                on_click=_on_action_click,
                args=("delete",),
                key="bomfs_action_delete",
            ),
        },
        hide_index=True,
        width="stretch",
        key="bomfs_table",
    )

    st.caption(f"{len(rows)} data terbaru (maks. {RECENT_LIMIT}).")


def _drive_folder_value() -> str:
    return (
        str(st.session_state.get("bomfs_folder", "")).strip()
        or drive_upload.DEFAULT_FOLDER
    )


def _drive_error_hint(exc: Exception) -> str:
    text = str(exc)
    email = service_account_email()

    if "storagequota" in text.lower() or "storage quota" in text.lower():
        return (
            f"{text}\n\n"
            "Penyebab: service account tidak punya kuota penyimpanan "
            "pribadi (kebijakan Google), jadi tujuan wajib **Drive "
            "Bersama**. Langkahnya:\n"
            "1) Google Drive -> + New -> Shared drive.\n"
            f"2) Klik kanan drive-nya -> Manage members -> tambahkan "
            f"{email or 'email service account'} sebagai Content Manager.\n"
            "3) Salin URL drive itu (https://drive.google.com/drive/"
            "folders/<ID>) ke kolom 'Folder Drive' di sidebar."
        )

    return (
        f"{text}\n\nPeriksa: (1) Google Drive API aktif di project "
        "service account; (2) Drive Bersama sudah mengundang "
        f"{email or 'email service account'} sebagai Content Manager; "
        "(3) URL yang ditempel benar (https://drive.google.com/drive/"
        "folders/<ID>)."
    )


def _do_upload(files) -> None:
    share_email = str(st.session_state.get("bomfs_share_email", "")).strip()
    items = [
        {
            "name": f.name,
            "content": f.getvalue(),
            "mime": getattr(f, "type", "") or "",
        }
        for f in files
    ]

    with st.status(f"Mengunggah {len(items)} file...", expanded=True) as status:
        try:
            results = drive_upload.upload_files(
                items,
                _drive_folder_value(),
                share_email,
            )
        except Exception as exc:
            status.update(label="Upload gagal", state="error")
            st.error(
                "Gagal mengunggah ke Google Drive: " + _drive_error_hint(exc)
            )
            return

        status.update(label=f"{len(results)} file terunggah", state="complete")

    for info in results:
        baris = f"{info['name']} — [buka di Drive]({info['link']})"
        if info.get("share_error"):
            st.warning(
                f"{baris}\nGagal membagikan ke {share_email}: "
                f"{info['share_error']}"
            )
        else:
            st.markdown(f"- {baris}")


def _render_upload():
    with st.container(border=True):
        st.markdown("**Upload ke Google Drive**")

        share_email = str(
            st.session_state.get("bomfs_share_email", "")
        ).strip()
        folder = _drive_folder_value()
        keterangan = (
            f"Folder tujuan: `{folder}`"
            if folder
            else "Folder tujuan belum diatur — isi 'Folder Drive' di sidebar "
            "(wajib URL Drive Bersama)."
        )
        if share_email:
            keterangan += f" · dibagikan ke {share_email}"
        st.caption(keterangan)

        files = st.file_uploader(
            "File (boleh lebih dari satu)",
            key="bomfs_files",
            accept_multiple_files=True,
            help=(
                "Excel, PDF, gambar, atau file apa pun. Tujuan folder "
                "diatur di sidebar (Folder Drive); kolom 'Email penerima' "
                "dipakai untuk membagikan file yang sudah terunggah."
            ),
        )

        if st.button(
            "Upload",
            type="primary",
            key="bomfs_btn_upload",
            disabled=not files,
        ):
            _do_upload(files)


def render():
    st.title("Kelola Data BOM")

    render_sidebar()
    _resolve_pending()
    _render_toolbar()

    try:
        rows = fetch_recent()
    except Exception as exc:
        st.error("Gagal membaca Firestore: " + _error_hint(exc))
        _dispatch_dialog()
        _render_flash()
        return

    if rows:
        _render_table(rows)
    else:
        st.info("Belum ada data. Klik 'Tambah Data' untuk memulai.")

    _render_upload()

    # Modal dirender tiap run selama state-nya masih aktif.
    _dispatch_dialog()
    _render_flash()
