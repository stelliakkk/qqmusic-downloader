"""Keep runtime files beside the application and protect QQ login with DPAPI."""

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile

from qqmusic_api.models.request import Credential


PROJECT = Path(__file__).resolve().parents[2] if not getattr(sys, "frozen", False) else None
ROOT = PROJECT or Path(sys.executable).resolve().parent
DATA = ROOT / "data"
DOWNLOADS = ROOT / "downloads"


class DataBlob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _crypt(payload: bytes, *, decrypt: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(payload)
    source = DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DataBlob()
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.data, destination.length)
    finally:
        kernel32.LocalFree(destination.data)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            file.write(payload)
            file.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


class Storage:
    def __init__(self, data: Path = DATA):
        self.data = data
        self.credential_path = data / "qqmusic_credential.dpapi"
        self.device_path = data / "qqmusic_device.json"
        self.settings_path = data / "settings.json"

    def load_credential(self) -> Credential | None:
        try:
            payload = _crypt(self.credential_path.read_bytes(), decrypt=True)
            credential = Credential.model_validate_json(payload)
        except (OSError, ValueError):
            return None
        return credential if credential.is_valid() else None

    def save_credential(self, credential: Credential) -> None:
        payload = credential.model_dump_json(by_alias=True).encode("utf-8")
        atomic_write(self.credential_path, _crypt(payload, decrypt=False))

    def download_folder(self, fallback: Path = DOWNLOADS) -> Path:
        try:
            folder = Path(json.loads(self.settings_path.read_text(encoding="utf-8"))["folder"])
            if folder.is_absolute():
                return folder
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return fallback

    def set_download_folder(self, folder: Path) -> None:
        atomic_write(self.settings_path, json.dumps({"folder": str(folder)}, ensure_ascii=False).encode("utf-8"))
