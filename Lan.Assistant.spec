# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for Lan.Assistant (onedir, no bundled models/Ollama)."""
import os
import site
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

try:
    project_root = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    project_root = os.path.abspath(os.getcwd())

datas = [
    (os.path.join(project_root, "static"), "static"),
    (os.path.join(project_root, "models", "silero_vad.onnx"), "models"),
]
binaries = []
hiddenimports = [
    "version",
    "api_secrets",
    "local_profile",
    "language",
    "setup_manager",
    "server",
    "diarization",
    "tts_server",
    "subtitle_overlay",
    "voice_overlay",
    "voice_input",
    "session_store",
    "log_utils",
    "audio_cue",
    "restart_app",
    "pystray._win32",
    "pystray._base",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "huggingface_hub",
    "huggingface_hub.file_download",
    "huggingface_hub.snapshot_download",
]

for package in ("faster_whisper", "ctranslate2", "pyaudiowpatch", "webrtcvad", "onnxruntime", "sherpa_onnx", "onnx"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception:
        # A missing optional dependency should fail later with a useful
        # runtime error rather than making the spec itself unreadable.
        pass

# CTranslate2 loads cuBLAS/cuDNN through the NVIDIA wheels on the source
# machine.  A frozen app does not have those site-packages, so copy the
# runtime DLLs and let cuda_env.py add their directory to the DLL search path.
_seen_nvidia: set[str] = set()
_nvidia_roots = []
try:
    _nvidia_roots.extend(Path(p) for p in site.getsitepackages())
except Exception:
    pass
try:
    _nvidia_roots.append(Path(site.getusersitepackages()))
except Exception:
    pass
for _root in _nvidia_roots:
    for _sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin", "cuda_runtime/bin"):
        _dir = _root / "nvidia" / _sub
        if not _dir.is_dir():
            continue
        for _dll in _dir.glob("*.dll"):
            _key = str(_dll).lower()
            if _key in _seen_nvidia:
                continue
            _seen_nvidia.add(_key)
            binaries.append((str(_dll), f"nvidia/{_sub}"))

version_file = os.environ.get("LAN_BUILD_VERSION_FILE") or None

a = Analysis(
    ["tray.py"],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lan.Assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(project_root, "static", "favicon.ico"),
    version=version_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Lan.Assistant",
)
