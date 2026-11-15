"""Optional CUDA runtime components for the frozen app.

The installer deliberately does not bundle NVIDIA's ~1.3 GB of cuBLAS/cuDNN
wheels.  On an NVIDIA machine the first-run setup downloads and extracts only
the DLLs needed by CTranslate2; integrated-GPU/CPU machines skip this entirely.
"""
from __future__ import annotations

import hashlib
import html
import re
import shutil
import site
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

import config

CUDA_DIR = config.LOCAL_APP_DIR / "cuda"
WHEEL_DIR = config.LOCAL_APP_DIR / "downloads" / "cuda-wheels"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple/{name}/"
OFFICIAL = "https://pypi.org/pypi/{name}/{version}/json"

COMPONENTS = [
    {
        "name": "nvidia-cublas-cu12",
        "version": "12.9.2.10",
        "wheel": "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
    },
    {
        "name": "nvidia-cudnn-cu12",
        "version": "9.28.1.4",
        "wheel": "nvidia_cudnn_cu12-9.28.1.4-py3-none-win_amd64.whl",
    },
    {
        "name": "nvidia-cuda-nvrtc-cu12",
        "version": "12.9.86",
        "wheel": "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
    },
]

REQUIRED = (
    "nvidia/cublas/bin/cublas64_12.dll",
    "nvidia/cublas/bin/cublasLt64_12.dll",
    "nvidia/cudnn/bin/cudnn64_9.dll",
    "nvidia/cudnn/bin/cudnn_ops64_9.dll",
)

_lock = threading.RLock()


def _site_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        roots.extend(Path(p) for p in site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")
    return roots


def _roots() -> list[Path]:
    return [CUDA_DIR, *_site_roots()]


def _present(root: Path) -> bool:
    return all((root / rel).is_file() for rel in REQUIRED)


def is_available() -> bool:
    return any(_present(root) for root in _roots())


def setup_paths() -> None:
    """Make the optional runtime visible to CTranslate2."""
    seen: set[str] = set()
    for root in _roots():
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin", "cuda_runtime/bin"):
            path = nvidia / sub
            if not path.is_dir():
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                import os

                os.add_dll_directory(str(path))
            except (AttributeError, OSError):
                pass


def _request(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Lan.Assistant-CUDA/1.0", **(headers or {})},
    )
    return urllib.request.urlopen(request, timeout=60)


def _mirror_candidates(component: dict[str, str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    name = component["name"]
    filename = component["wheel"]
    try:
        with _request(MIRROR.format(name=name)) as response:
            page = response.read().decode("utf-8", "replace")
        for match in re.finditer(r'href="([^"]+)"', page):
            href = html.unescape(match.group(1))
            if filename not in href:
                continue
            digest = ""
            parsed = urllib.parse.urlparse(href)
            fragment = urllib.parse.parse_qs(parsed.fragment)
            digest = str((fragment.get("sha256") or [""])[0])
            url = urllib.parse.urljoin(MIRROR.format(name=name), href)
            found.append((url.split("#", 1)[0], digest))
        return found
    except Exception:
        return []


def _official_candidates(component: dict[str, str]) -> list[tuple[str, str]]:
    try:
        url = OFFICIAL.format(name=component["name"], version=component["version"])
        with _request(url) as response:
            import json

            data = json.loads(response.read().decode("utf-8"))
        result: list[tuple[str, str]] = []
        for item in data.get("urls", []) or []:
            if item.get("filename") != component["wheel"]:
                continue
            result.append(
                (
                    str(item.get("url", "")),
                    str((item.get("digests") or {}).get("sha256", "")),
                )
            )
        return result
    except Exception:
        return []


def _wheel_candidates(component: dict[str, str]) -> list[tuple[str, str]]:
    values = _mirror_candidates(component) + _official_candidates(component)
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, digest in values:
        if url and url not in seen:
            seen.add(url)
            result.append((url, digest))
    return result


def _download(url: str, target: Path, expected_hash: str = "") -> None:
    partial = target.with_name(target.name + ".part")
    offset = partial.stat().st_size if partial.is_file() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        response = _request(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            partial.unlink(missing_ok=True)
        raise
    with response:
        status = int(getattr(response, "status", 200) or 200)
        length = int(response.headers.get("Content-Length", "0") or 0)
        append = bool(offset and status == 206)
        if not append:
            offset = 0
        mode = "ab" if append else "wb"
        with partial.open(mode) as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    if length and partial.stat().st_size < offset + length:
        raise RuntimeError("CUDA 组件下载不完整，重试可断点继续")
    if not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        raise RuntimeError("CUDA 组件不是有效 wheel/ZIP")
    if expected_hash:
        digest = hashlib.sha256()
        with partial.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_hash.lower():
            partial.unlink(missing_ok=True)
            raise RuntimeError("CUDA 组件校验失败")
    partial.replace(target)


def _extract(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            normalized = name.replace("\\", "/")
            if not normalized.startswith("nvidia/"):
                continue
            if "/bin/" not in normalized or not normalized.lower().endswith(".dll"):
                continue
            target = CUDA_DIR / normalized
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)


def install(progress: Optional[Callable[[float, str], None]] = None) -> None:
    """Download/extract missing CUDA DLLs. Idempotent; raises on failure."""
    with _lock:
        if is_available():
            return
        WHEEL_DIR.mkdir(parents=True, exist_ok=True)
        total = len(COMPONENTS)
        last_error: Exception | None = None
        for index, component in enumerate(COMPONENTS):
            wheel = WHEEL_DIR / component["wheel"]
            candidates = _wheel_candidates(component)
            if not candidates:
                last_error = RuntimeError(f"找不到 {component['name']} 的 wheel")
                continue
            success = False
            for url, expected_hash in candidates:
                try:
                    if progress:
                        progress(index / total, f"正在下载 CUDA 组件 {index + 1}/{total}…")
                    _download(url, wheel, expected_hash)
                    _extract(wheel)
                    wheel.unlink(missing_ok=True)
                    success = True
                    break
                except Exception as exc:
                    last_error = exc
                    wheel.unlink(missing_ok=True)
                    wheel.with_name(wheel.name + ".part").unlink(missing_ok=True)
            if not success:
                raise RuntimeError(f"CUDA 组件下载失败：{last_error}")
        if not is_available():
            raise RuntimeError("CUDA 组件解压后不完整")
        if progress:
            progress(1.0, "CUDA 加速组件已安装。")
