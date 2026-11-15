"""Native Windows file/folder picker used by the local setup page."""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def _run(script: str, timeout: float = 600.0) -> str:
    if sys.platform != "win32":
        return ""
    try:
        process = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return process.stdout.strip() if process.returncode == 0 else ""
    except Exception:
        return ""


def pick_file(title: str, file_filter: str) -> str:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        f"$d.Title='{title.replace(chr(39), chr(39) * 2)}';"
        f"$d.Filter='{file_filter.replace(chr(39), chr(39) * 2)}';"
        "$d.CheckFileExists=$true;$d.Multiselect=$false;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.TopMost=$true;$owner.ShowInTaskbar=$false;"
        "$owner.FormBorderStyle='None';$owner.Opacity=0.01;"
        "$owner.StartPosition='CenterScreen';"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Show();"
        "$r=$d.ShowDialog($owner);$owner.Close();"
        "if($r -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::Out.Write($d.FileName)}"
    )
    return _run(script)


def pick_folder(title: str) -> str:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description='{title.replace(chr(39), chr(39) * 2)}';"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.TopMost=$true;$owner.ShowInTaskbar=$false;"
        "$owner.FormBorderStyle='None';$owner.Opacity=0.01;"
        "$owner.StartPosition='CenterScreen';"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Show();"
        "$r=$d.ShowDialog($owner);$owner.Close();"
        "if($r -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::Out.Write($d.SelectedPath)}"
    )
    return _run(script)
