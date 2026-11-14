"""Machine-local API credentials for Lan.Assistant.

API keys must never be written into the project's data/settings.json: that file
is commonly copied to another computer during an upgrade.  This module stores
the API profile in %LOCALAPPDATA% and encrypts it with Windows DPAPI, so a
copied file cannot be decrypted by another Windows user or on another machine.

Only the API profile lives here.  Non-secret application settings remain in
data/settings.json.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
from pathlib import Path
from typing import Any

APP_DIR_NAME = "Lan.Assistant"
PROFILE_FILE_NAME = "api_profile.dat"
_ENTROPY = b"Lan.Assistant.API.v1"


def profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", "")
    root = Path(base) if base else (Path.home() / "AppData" / "Local")
    return root / APP_DIR_NAME / PROFILE_FILE_NAME


def _localappdata_available() -> bool:
    return bool(os.environ.get("LOCALAPPDATA", "") or os.name == "nt")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("API 凭据加密仅支持 Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, keep_in = _make_blob(data)
    entropy, keep_entropy = _make_blob(_ENTROPY)
    blob_out = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.wintypes.BOOL
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "Lan.Assistant API profile",
        ctypes.byref(entropy),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("Windows DPAPI 加密失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("API 凭据解密仅支持 Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, keep_in = _make_blob(data)
    entropy, keep_entropy = _make_blob(_ENTROPY)
    blob_out = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.wintypes.BOOL
    description = ctypes.wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        0x1,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("Windows DPAPI 解密失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
        if description:
            kernel32.LocalFree(description)


def load_profile() -> dict[str, Any]:
    path = profile_path()
    try:
        payload = _unprotect(path.read_bytes())
        raw = json.loads(payload.decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "provider": str(raw.get("provider", "local") or "local"),
        "api_base_url": str(raw.get("api_base_url", "") or ""),
        "api_model": str(raw.get("api_model", "") or ""),
        "api_key": str(raw.get("api_key", "") or ""),
        "api_disable_thinking": bool(raw.get("api_disable_thinking", True)),
    }


def save_profile(profile: dict[str, Any]) -> None:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "provider": str(profile.get("provider", "local") or "local"),
            "api_base_url": str(profile.get("api_base_url", "") or "")[:500],
            "api_model": str(profile.get("api_model", "") or "")[:200],
            "api_key": str(profile.get("api_key", "") or "")[:500],
            "api_disable_thinking": bool(profile.get("api_disable_thinking", True)),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    encrypted = _protect(payload)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encrypted)
    os.replace(tmp, path)


def clear_profile() -> None:
    try:
        profile_path().unlink()
    except FileNotFoundError:
        pass


def has_key() -> bool:
    return bool(load_profile().get("api_key"))


def public_status() -> dict[str, Any]:
    """Return non-secret information for the UI."""
    profile = load_profile()
    return {
        "api_key_saved": bool(profile.get("api_key")),
        "api_profile_path": str(profile_path()),
        "api_profile_local": True,
    }
