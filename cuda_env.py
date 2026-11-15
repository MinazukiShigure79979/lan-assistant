"""Make the pip-installed NVIDIA runtime DLLs visible to CTranslate2 on Windows."""
from __future__ import annotations

import os
import site
import sys
from pathlib import Path

_configured = False


def setup_cuda_dlls() -> None:
    global _configured
    if _configured or sys.platform != "win32":
        return
    _configured = True
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        # PyInstaller puts the collected nvidia runtime under _internal/nvidia.
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    try:
        roots.extend(Path(p) for p in site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")

    seen: set[str] = set()
    for root in roots:
        nvidia = root / "nvidia"
        if not nvidia.exists():
            continue
        for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin", "cuda_runtime/bin"):
            path = nvidia / sub
            if not path.exists():
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                os.add_dll_directory(str(path))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
