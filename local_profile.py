"""Machine-local, DPAPI-encrypted user profile.

The profile contains the user's name, company, role and key facts.  It must
not travel with the installer, a copied project folder, or a migrated data
directory.  It lives under %LOCALAPPDATA% and is protected by Windows DPAPI for
the current Windows user/machine.

There is intentionally no import of a legacy data/profile.json here.  The
migration is performed once by the developer/upgrade script; after that,
copying a settings/data folder cannot bring personal preset information to a
new computer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import api_secrets

APP_DIR_NAME = "Lan.Assistant"
PROFILE_FILE_NAME = "profile.dat"


def profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", "")
    root = Path(base) if base else (Path.home() / "AppData" / "Local")
    return root / APP_DIR_NAME / PROFILE_FILE_NAME


def load_profile() -> dict[str, Any]:
    try:
        payload = api_secrets._unprotect(profile_path().read_bytes())
        raw = json.loads(payload.decode("utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_profile(profile: dict[str, Any]) -> None:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile, ensure_ascii=False).encode("utf-8")
    encrypted = api_secrets._protect(payload)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encrypted)
    os.replace(tmp, path)


def has_profile() -> bool:
    return bool(load_profile())


def clear_profile() -> None:
    try:
        profile_path().unlink()
    except FileNotFoundError:
        pass
