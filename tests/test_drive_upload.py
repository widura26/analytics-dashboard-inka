"""Unit test upload ke Google Drive: folder, resolusi parent, upload, sharing.

Jalankan:  python tests/test_drive_upload.py   (dari root proyek)
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drive_upload as du


FOLDER_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz01234"
FILE_ID = "1ZyXwVuTsRqPoNmLkJiHgFdSa0987654321"

# --- tiruan googleapiclient (tanpa jaringan) ---

NAME_RE = re.compile(r"name = '((?:\\.|[^'])*)'")
PARENT_RE = re.compile(r"'([^']+)' in parents")


class FakeCall:
    def __init__(self, result=None, error: Exception = None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


def _media_content(media_body) -> bytes:
    """googleapiclient 2.x: .stream adalah method; posisinya sudah di akhir."""
    stream = media_body.stream
    stream = stream() if callable(stream) else stream
    try:
        stream.seek(0)
    except Exception:
        pass
    return stream.read()


def _media_mime(media_body) -> str:
    mime = getattr(media_body, "mimetype", None)
    mime = mime() if callable(mime) else mime
    return mime or ""


class FakeFiles:
    def __init__(self, drive: "FakeDrive"):
        self.drive = drive

    def list(self, q=None, **kwargs):
        name_match = NAME_RE.search(q or "")
        parent_match = PARENT_RE.search(q or "")
        name = name_match.group(1) if name_match else None
        parent = parent_match.group(1) if parent_match else None

        files = [
            f for f in self.drive.files_store
            if (name is None or f["name"] == name)
            and (parent is None or f.get("parents") == [parent])
            and f["mimeType"] == du.FOLDER_MIME
        ]
        return FakeCall({"files": [{"id": f["id"]} for f in files]})

    def create(self, body=None, media_body=None, fields=None, **kwargs):
        if body.get("mimeType") == du.FOLDER_MIME:
            self.drive.created_folders.append(body)
            return FakeCall({"id": "folder-baru"})

        self.drive.created_files.append({
            "body": body,
            "content": _media_content(media_body) if media_body else b"",
            "mime": _media_mime(media_body) if media_body else "",
            "kwargs": {k: v for k, v in kwargs.items()},
        })
        return FakeCall({
            "id": "file-baru",
            "name": body.get("name"),
            "webViewLink": "https://drive.google.com/file/d/file-baru/view",
        })

    def get(self, fileId=None, fields=None, **kwargs):
        found = next(
            (f for f in self.drive.files_store if f["id"] == fileId),
            None,
        )
        if not found:
            return FakeCall(error=RuntimeError("404: file not found"))
        meta = {"id": fileId, "mimeType": found["mimeType"]}
        if found.get("driveId"):
            meta["driveId"] = found["driveId"]
        return FakeCall(meta)


class FakePermissions:
    def __init__(self, drive: "FakeDrive"):
        self.drive = drive

    def create(self, fileId=None, body=None, sendNotificationEmail=None,
               **kwargs):
        if self.drive.share_fails:
            return FakeCall(error=RuntimeError("permission denied"))
        self.drive.grants.append((fileId, body, sendNotificationEmail))
        return FakeCall({"id": "perm-1"})


class FakeDrive:
    def __init__(self, files=None, share_fails: bool = False):
        self.files_store = files or []
        self.share_fails = share_fails
        self.created_folders = []
        self.created_files = []
        self.grants = []

    def files(self):
        return FakeFiles(self)

    def permissions(self):
        return FakePermissions(self)


def folder_drive(folder_id: str = FOLDER_ID, share_fails: bool = False) -> FakeDrive:
    """Drive Bersama (ada driveId) tempat folder Upload BOM berada."""
    return FakeDrive(
        [{
            "id": folder_id,
            "name": "Upload BOM",
            "mimeType": du.FOLDER_MIME,
            "parents": ["root"],
            "driveId": folder_id,
        }],
        share_fails=share_fails,
    )


# --- test ---

def test_extract_folder_id():
    url = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    assert du.extract_folder_id(url) == FOLDER_ID, url

    url_share = f"https://drive.google.com/file/d/{FOLDER_ID}/view"
    assert du.extract_folder_id(url_share) == FOLDER_ID, url_share

    url_docs = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit"
    assert du.extract_folder_id(url_docs) == FILE_ID, url_docs

    assert du.extract_folder_id(f"https://drive.google.com/drive/u/0/?id={FOLDER_ID}") == FOLDER_ID

    # Bukan ID: nama folder biasa, string pendek, kosong.
    assert du.extract_folder_id("Upload BOM") is None
    assert du.extract_folder_id("abc") is None
    assert du.extract_folder_id("") is None
    assert du.extract_folder_id(None) is None

    # ID mentah (token panjang) ikut dikenali.
    assert du.extract_folder_id(FOLDER_ID) == FOLDER_ID
    print("[OK] extract_folder_id: URL/id/nama")


def test_resolve_parent():
    # Belum diatur -> pesan jelas menyuruh mengisi URL Drive Bersama.
    try:
        du.resolve_parent(FakeDrive(), "")
        raise AssertionError("seharusnya menolak tujuan kosong")
    except RuntimeError as exc:
        assert "belum diatur" in str(exc), exc

    # URL & ID Drive Bersama yang bisa diakses -> dipakai langsung.
    drive = folder_drive()
    url = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    assert du.resolve_parent(drive, url) == FOLDER_ID
    assert du.resolve_parent(drive, FOLDER_ID) == FOLDER_ID

    # Nama folder biasa -> ditolak (service account tak punya kuota).
    try:
        du.resolve_parent(drive, "Upload BOM")
        raise AssertionError("seharusnya menolak nama folder")
    except RuntimeError as exc:
        assert "bukan URL/ID" in str(exc), exc

    # ID tapi bukan folder -> ditolak.
    pdf_drive = FakeDrive([{
        "id": FILE_ID,
        "name": "dokumen.pdf",
        "mimeType": "application/pdf",
        "parents": ["root"],
        "driveId": FOLDER_ID,
    }])
    try:
        du.resolve_parent(pdf_drive, FILE_ID)
        raise AssertionError("seharusnya menolak non-folder")
    except RuntimeError as exc:
        assert "bukan folder" in str(exc), exc

    # Tidak bisa diakses -> pesan menyuruh mengundang service account.
    try:
        du.resolve_parent(FakeDrive(), FOLDER_ID)
        raise AssertionError("seharusnya gagal diakses")
    except RuntimeError as exc:
        assert "tidak bisa diakses" in str(exc), exc
        assert "Content Manager" in str(exc), exc

    # Folder pribadi (tanpa driveId) -> tetap diterima, peringatan dicetak.
    personal = FakeDrive([{
        "id": FOLDER_ID,
        "name": "Folder Saya",
        "mimeType": du.FOLDER_MIME,
        "parents": ["root"],
    }])
    assert du.resolve_parent(personal, FOLDER_ID) == FOLDER_ID
    print("[OK] resolve_parent: kosong/URL/ID/nama salah/salah akses")


def test_upload_file():
    drive = FakeDrive()
    info = du.upload_file(
        drive, "bom.xlsx", b"isi-excel", "folder-1", "application/vnd.ms-excel",
    )

    assert info["id"] == "file-baru"
    assert info["name"] == "bom.xlsx"
    assert info["link"].endswith("/file-baru/view")

    saved = drive.created_files[0]
    assert saved["body"]["parents"] == ["folder-1"]
    assert saved["content"] == b"isi-excel"
    assert saved["mime"] == "application/vnd.ms-excel"
    # Upload ke shared drive wajib supportsAllDrives.
    assert saved["kwargs"].get("supportsAllDrives") is True, saved["kwargs"]
    print("[OK] upload_file: parent, isi, mime, supportsAllDrives")


def test_share_file():
    drive = FakeDrive()
    du.share_file(drive, "file-baru", "  user@contoh.co.id ")

    file_id, body, notify = drive.grants[0]
    assert file_id == "file-baru"
    assert body["emailAddress"] == "user@contoh.co.id"
    assert body["role"] == "reader" and body["type"] == "user"
    assert notify is False

    # Email kosong -> tidak memanggil API sama sekali.
    du.share_file(drive, "file-baru", "   ")
    assert len(drive.grants) == 1
    print("[OK] share_file: reader + tanpa notifikasi")


def test_upload_files():
    drive = folder_drive()
    original = du.connect_drive
    du.connect_drive = lambda: drive
    try:
        results = du.upload_files(
            [
                {"name": "a.csv", "content": b"1,2", "mime": "text/csv"},
                {"name": "b.pdf", "content": b"%PDF", "mime": ""},
            ],
            f"https://drive.google.com/drive/folders/{FOLDER_ID}",
            "user@contoh.co.id",
        )
    finally:
        du.connect_drive = original

    assert len(results) == 2, results
    assert [r["name"] for r in results] == ["a.csv", "b.pdf"]
    assert len(drive.created_files) == 2
    assert len(drive.grants) == 2
    assert all(not r.get("share_error") for r in results)

    # Sharing gagal -> upload tetap sukses, kesalahan dicatat per file.
    drive = folder_drive(share_fails=True)
    du.connect_drive = lambda: drive
    try:
        results = du.upload_files(
            [{"name": "c.csv", "content": b"x", "mime": "text/csv"}],
            f"https://drive.google.com/drive/folders/{FOLDER_ID}",
            "user@contoh.co.id",
        )
    finally:
        du.connect_drive = original

    assert len(results) == 1
    assert "permission denied" in results[0]["share_error"]
    assert results[0]["link"]
    print("[OK] upload_files: banyak file + kegagalan sharing tidak membatalkan")


if __name__ == "__main__":
    test_extract_folder_id()
    test_resolve_parent()
    test_upload_file()
    test_share_file()
    test_upload_files()
    print("\nSEMUA TEST LULUS")
