import ctypes
import ctypes.wintypes
from pathlib import Path

from app.core.paths import DATA_DIR


_SECRET_FILE = DATA_DIR / "telegram_check_api_token.bin"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    ), buffer


def _bytes_from_blob(blob):
    if not blob.pbData or not blob.cbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def save_telegram_check_api_token(token):
    value = str(token or "").strip()
    if not value:
        raise ValueError("API 암호를 입력해주세요.")

    raw = value.encode("utf-8")
    in_blob, in_buffer = _blob_from_bytes(raw)
    out_blob = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "AngelToggle Telegram Check API",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("Windows 보안 저장소 암호화에 실패했습니다.")

    try:
        encrypted = _bytes_from_blob(out_blob)
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(encrypted)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def load_telegram_check_api_token():
    if not _SECRET_FILE.exists():
        return ""

    encrypted = _SECRET_FILE.read_bytes()
    if not encrypted:
        return ""

    in_blob, in_buffer = _blob_from_bytes(encrypted)
    out_blob = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        return ""

    try:
        return _bytes_from_blob(out_blob).decode("utf-8").strip()
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def has_telegram_check_api_token():
    return bool(load_telegram_check_api_token())


def clear_telegram_check_api_token():
    if _SECRET_FILE.exists():
        _SECRET_FILE.unlink()
