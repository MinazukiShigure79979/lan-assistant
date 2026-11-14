"""Generate a PyInstaller Windows version resource from version.py."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import version  # noqa: E402


def _parts() -> tuple[int, int, int, int]:
    text = version.INSTALLER_VERSION
    parts = [int(part) for part in text.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def build() -> str:
    file_version = _parts()
    version_text = f"{version.VERSION} ({version.DISPLAY_VERSION})"
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version!r},
    prodvers={file_version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Lan.Assistant'),
          StringStruct('FileDescription', 'Lan.Assistant desktop assistant'),
          StringStruct('FileVersion', {version_text!r}),
          StringStruct('InternalName', 'Lan.Assistant'),
          StringStruct('OriginalFilename', 'Lan.Assistant.exe'),
          StringStruct('ProductName', 'Lan.Assistant'),
          StringStruct('ProductVersion', {version_text!r}),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=str(ROOT / "build" / "lan_version_info.txt"))
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
