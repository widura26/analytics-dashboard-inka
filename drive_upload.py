"""Upload file ke Google Drive lewat service account aplikasi.

Batasan resmi Google (developers.google.com/workspace/drive/api/guides/
handle-errors):

    Service accounts don't have storage quota and can't own any files.
    Instead, they must upload files and folders into shared drives...

Karena itu tujuan upload WAJIB berupa Drive Bersama (atau folder di
dalamnya) yang sudah mengundang email service account sebagai member
(Content Manager). Kolom sidebar "Folder Drive" diisi URL/ID Drive
Bersama itu — mis. https://drive.google.com/drive/folders/<ID_DRIVE>.

Kredensial diambil dari modul bom_firestore (secrets dulu, lalu
credentials.json); scope drive cukup untuk membaca/menulis file.

Impor googleapiclient ditunda ke dalam fungsi (lazy) seperti Firestore.
"""
import io
import re
from urllib.parse import parse_qs, urlparse

import streamlit as st

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Tujuan kosong = belum dikonfigurasi (harus diisi URL/ID Drive Bersama).
DEFAULT_FOLDER = ""

FOLDER_MIME = "application/vnd.google-apps.folder"

# Semua operasi harus menyangkut drive bersama (shared drive), bukan
# hanya My Drive.
SUPPORTS = {"supportsAllDrives": True}
SUPPORTS_LIST = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}

# ID file Drive: token panjang (biasanya 33 karakter) berisi huruf,
# angka, tanda minus dan underscore.
_ID_CANDIDATE = re.compile(r"^[A-Za-z0-9_-]{25,}$")

ERROR_STORAGE_QUOTA = "storage quota"


@st.cache_resource
def connect_drive():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    import bom_firestore

    credentials = Credentials.from_service_account_info(
        bom_firestore.service_account_info(),
        scopes=DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def extract_folder_id(value: str):
    """Ambil ID folder/Drive Bersama dari URL/id yang ditempel; None bila bukan ID."""
    value = str(value or "").strip()
    if not value:
        return None

    if "drive.google.com" in value or "docs.google.com" in value:
        parsed = urlparse(value)
        parts = [p for p in parsed.path.split("/") if p]
        for marker in ("folders", "d"):
            if marker in parts:
                index = parts.index(marker)
                if index + 1 < len(parts):
                    candidate = parts[index + 1]
                    if _ID_CANDIDATE.match(candidate):
                        return candidate
        match = parse_qs(parsed.query).get("id")
        if match and _ID_CANDIDATE.match(match[0]):
            return match[0]
        return None

    if _ID_CANDIDATE.match(value):
        return value

    return None


def resolve_parent(drive, folder_value: str) -> str:
    """Terjemahkan kolom 'Folder Drive' menjadi ID parent untuk upload.

    Hanya URL/ID yang diterima; nama folder biasa ditolak karena service
    account tidak bisa membuat folder di My Drive miliknya sendiri.
    """
    value = str(folder_value or "").strip()

    if not value:
        raise RuntimeError(
            "Tujuan upload belum diatur. Isi kolom 'Folder Drive' di "
            "sidebar dengan URL Drive Bersama Anda (lihat petunjuk di "
            "kolom tersebut)."
        )

    folder_id = extract_folder_id(value)
    if not folder_id:
        raise RuntimeError(
            f"'{value}' bukan URL/ID Drive Bersama. Salin URL-nya dari "
            "Google Drive, bentuknya "
            "https://drive.google.com/drive/folders/<ID_DRIVE>."
        )

    try:
        meta = (
            drive.files()
            .get(
                fileId=folder_id,
                fields="id,name,mimeType,driveId",
                **SUPPORTS,
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(
            f"Drive/folder '{value}' tidak bisa diakses service account "
            f"({exc}). Buka Drive Bersama -> Manage members -> tambahkan "
            "email service account sebagai Content Manager."
        ) from exc

    if meta.get("mimeType") != FOLDER_MIME:
        raise RuntimeError(f"'{value}' bukan folder atau Drive Bersama.")

    if not meta.get("driveId"):
        # Folder di My Drive pribadi: tetap dicoba, tapi Google
        # kemungkinan menolak (service account tanpa kuota penyimpanan).
        st.warning(
            "Folder ini berada di My Drive pribadi, bukan Drive Bersama. "
            "Service account tidak punya kuota penyimpanan sehingga "
            "upload kemungkinan ditolak Google. Pakai Drive Bersama."
        )

    return folder_id


def upload_file(
    drive,
    filename: str,
    content: bytes,
    parent: str,
    mime: str = "",
) -> dict:
    """Upload satu berkas; kembalikan {id, name, link}."""
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(
        io.BytesIO(content),
        mimetype=mime or "application/octet-stream",
        resumable=True,
    )
    created = (
        drive.files()
        .create(
            body={"name": filename, "parents": [parent]},
            media_body=media,
            fields="id,name,webViewLink",
            **SUPPORTS,
        )
        .execute()
    )

    file_id = created["id"]
    return {
        "id": file_id,
        "name": created.get("name") or filename,
        "link": created.get("webViewLink")
        or f"https://drive.google.com/file/d/{file_id}/view",
    }


def share_file(drive, file_id: str, email: str, role: str = "reader") -> None:
    """Bagikan file ke email (reader/writer) tanpa email notifikasi."""
    if not str(email or "").strip():
        return

    (
        drive.permissions()
        .create(
            fileId=file_id,
            body={
                "type": "user",
                "role": role,
                "emailAddress": str(email).strip(),
            },
            sendNotificationEmail=False,
            **SUPPORTS,
        )
        .execute()
    )


def upload_files(items: list, folder_value: str, share_email: str = "") -> list:
    """Upload banyak berkas sekaligus.

    items: daftar {"name": str, "content": bytes, "mime": str}.
    Mengembalikan daftar {"name", "id", "link", "share_error"?}.
    """
    drive = connect_drive()
    parent = resolve_parent(drive, folder_value)

    results = []
    for item in items:
        info = upload_file(
            drive,
            item["name"],
            item["content"],
            parent,
            item.get("mime", ""),
        )

        if str(share_email or "").strip():
            try:
                share_file(drive, info["id"], share_email)
            except Exception as exc:
                info["share_error"] = str(exc)

        results.append(info)

    return results
