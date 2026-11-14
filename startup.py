"""Manage the per-user Windows Start Menu startup shortcut.

A Startup-folder shortcut is used instead of a Run registry value because it
carries the working directory, which keeps all of the app's relative paths
stable when Windows starts it at sign-in.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
APP_NAME = "Lan.Assistant"
SHORTCUT_NAME = "Lan.Assistant.lnk"
CREATE_NO_WINDOW = 0x08000000


def _startup_dir() -> Path:
    appdata = Path(os.environ.get("APPDATA", "") or (Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return _startup_dir() / SHORTCUT_NAME


def _pythonw() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        windowed = executable.with_name("pythonw.exe")
        if windowed.exists():
            return windowed
    return executable


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail[:500] or f"PowerShell exited with {result.returncode}"


def set_enabled(enabled: bool) -> tuple[bool, str]:
    target = shortcut_path()
    if not enabled:
        try:
            target.unlink(missing_ok=True)
        except Exception as exc:
            return False, str(exc)
        return True, ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, str(exc)
    pythonw = _pythonw()
    tray = ROOT / "tray.py"
    icon = RESOURCE_ROOT / "static" / "favicon.ico"
    frozen = bool(getattr(sys, "frozen", False))
    arguments = "" if frozen else (chr(34) + str(tray) + chr(34))
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "$sh = New-Object -ComObject WScript.Shell\n"
        f"$lnk = $sh.CreateShortcut({_ps_quote(target)})\n"
        f"$lnk.TargetPath = {_ps_quote(pythonw)}\n"
        f"$lnk.Arguments = {_ps_quote(arguments)}\n"
        f"$lnk.WorkingDirectory = {_ps_quote(ROOT)}\n"
        f"$lnk.IconLocation = {_ps_quote(str(icon) + ',0')}\n"
        f"$lnk.Description = {_ps_quote(APP_NAME)}\n"
        "$lnk.Save()\n"
        f"if (-not (Test-Path -LiteralPath {_ps_quote(target)})) {{ exit 1 }}\n"
        "} catch { Write-Error $_; exit 1 }\n"
    )
    ok, error = _run_powershell(script)
    if not ok:
        return False, error
    if not target.exists():
        return False, "启动快捷方式创建后未找到文件"
    return True, ""


def is_enabled() -> bool:
    return shortcut_path().exists()
