"""Detached restart helper for Lan.Assistant.

The HTTP server starts this before shutting down.  It waits for port 8765 to be
released, then starts the same app either with normal or elevated rights.
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import log_utils

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
SW_SHOWNORMAL = 1
PORT = 8765


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _log_path() -> Path:
    if FROZEN:
        base = Path(os.environ.get("LOCALAPPDATA", "") or Path.home()) / "Lan.Assistant" / "data"
    else:
        base = ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "restart.log"


def _log(message: str) -> None:
    try:
        path = _log_path()
        log_utils.rotate_file(path, 512 * 1024, 2)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except Exception:
        pass


def _port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.35):
            return True
    except OSError:
        return False


def _wait_for_shutdown(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_open():
            time.sleep(0.9)
            return True
        time.sleep(0.35)
    return False


def _app_command() -> list[str]:
    if FROZEN:
        return [sys.executable, "--restart-open"]
    return [sys.executable, str(ROOT / "tray.py"), "--restart-open"]


def _spawn_direct(command: list[str]) -> None:
    subprocess.Popen(
        command,
        cwd=str(ROOT),
        close_fds=True,
        creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
    )


def _spawn_elevated(command: list[str]) -> bool:
    params = subprocess.list2cmdline(command[1:])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", command[0], params, str(ROOT), SW_SHOWNORMAL
        )
        return int(result) > 32
    except Exception as exc:
        _log(f"elevated launch failed: {exc}")
        return False


def _spawn_normal_from_elevated() -> bool:
    """Use Explorer as the normal-integrity launcher.

    An elevated process cannot directly create a medium-integrity child.  A
    tiny temporary .cmd file lets Explorer start the app in the user's normal
    integrity level, preserving the requested 'normal' mode.
    """
    wrapper = Path(tempfile.gettempdir()) / "lan_assistant_restart.cmd"
    command = _app_command()
    quoted = subprocess.list2cmdline(command)
    try:
        wrapper.write_text(
            "@echo off\r\n"
            f'cd /d "{ROOT}"\r\n'
            f"start \"\" {quoted}\r\n",
            encoding="mbcs",
        )
        subprocess.Popen(
            ["explorer.exe", str(wrapper)],
            cwd=str(ROOT),
            close_fds=True,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
        )
        return True
    except Exception as exc:
        _log(f"normal relaunch via explorer failed: {exc}")
        return False


def main() -> int:
    want_admin = "--admin" in sys.argv
    _log(
        f"restart helper started: want_admin={want_admin} current_admin={_is_admin()} "
        f"frozen={FROZEN}"
    )
    if not _wait_for_shutdown():
        _log("old server did not close port in time; aborting")
        return 1
    command = _app_command()
    if want_admin:
        if _is_admin():
            _spawn_direct(command)
        elif not _spawn_elevated(command):
            _log("UAC was cancelled or elevation failed")
            return 1
    else:
        if _is_admin():
            if not _spawn_normal_from_elevated():
                return 1
        else:
            _spawn_direct(command)
    _log("restart launch dispatched")
    return 0
