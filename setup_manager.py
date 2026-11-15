"""First-run provisioning for Lan.Assistant.

The installer is intentionally code-only: Ollama and model weights are not
bundled.  On the first launch this module:

1. downloads the portable Ollama runtime into %LOCALAPPDATA%,
2. lets the user pick at least one Whisper and one GGUF model (the hardware
   recommendation is selected by default),
3. downloads the selected models,
4. applies the choices and only then starts the main ASR/LLM workers.

A completed marker lives in data/setup.json.  Existing developer installs that
already contain Ollama + models are marked complete automatically.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

import config
import cuda_runtime
import hardware
import models
import proxy_utils
import version

proxy_utils.apply_system_proxy()

SETUP_PATH = config.DATA_DIR / "setup.json"
OLLAMA_ZIP_URL = "https://ollama.com/download/ollama-windows-amd64.zip"
OLLAMA_SOURCES = [
    {
        "id": "official",
        "name": "Ollama 官方",
        "url": OLLAMA_ZIP_URL,
    },
    {
        "id": "github",
        "name": "GitHub Release",
        "url": "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
    },
]
HF_ENDPOINT = os.environ.get("LAN_HF_ENDPOINT", "https://hf-mirror.com")


class _InvalidArchive(RuntimeError):
    """Downloaded content is HTML/truncated/invalid, not an Ollama ZIP."""

WHISPER_OPTIONS: list[dict[str, Any]] = [
    {
        "id": "large-v3-turbo",
        "dir": "faster-whisper-large-v3-turbo",
        "label": "Whisper Large v3 Turbo",
        "desc": "最准确，适合 NVIDIA 显卡；会议和口音较重时更稳。",
        "repo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "size_bytes": 1_617_884_929,
        "size": "1.5 GB",
    },
    {
        "id": "large-v3",
        "dir": "faster-whisper-large-v3",
        "label": "Whisper Large v3",
        "desc": "比 Turbo 更准，适合中文、口音、专名和长内容；速度更慢。",
        "repo": "Systran/faster-whisper-large-v3",
        "size_bytes": 3_087_284_237,
        "size": "2.9 GB",
    },
    {
        "id": "small",
        "dir": "faster-whisper-small",
        "label": "Whisper Small",
        "desc": "速度和准确率平衡，CPU / 核显推荐。",
        "repo": "Systran/faster-whisper-small",
        "size_bytes": 483_546_902,
        "size": "461 MB",
    },
    {
        "id": "base",
        "dir": "faster-whisper-base",
        "label": "Whisper Base",
        "desc": "下载最快、占用最小；准确率低于 Small。",
        "repo": "Systran/faster-whisper-base",
        "size_bytes": 145_000_000,
        "size": "138 MB",
    },
    {
        "id": "medium",
        "dir": "faster-whisper-medium",
        "label": "Whisper Medium",
        "desc": "比 Small 更准，但纯 CPU 会明显变慢。",
        "repo": "Systran/faster-whisper-medium",
        "size_bytes": 1_530_000_000,
        "size": "1.5 GB",
    },
    {
        "id": "tiny",
        "dir": "faster-whisper-tiny",
        "label": "Whisper Tiny",
        "desc": "最低配备用；只适合快速草稿。",
        "repo": "Systran/faster-whisper-tiny",
        "size_bytes": 75_000_000,
        "size": "75 MB",
    },
]

LLM_OPTIONS: list[dict[str, Any]] = [
    {
        "id": "qwen3.5-9b",
        "name": "qwen3.5-copilot",
        "file": "Qwen3.5-9B-Q4_K_M.gguf",
        "label": "Qwen3.5-9B Q4_K_M",
        "desc": "质量最好，适合 12 GB 以上显存的 NVIDIA 独显。",
        "repo": "unsloth/Qwen3.5-9B-GGUF",
        "size_bytes": 5_300_000_000,
        "size": "5.3 GB",
    },
    {
        "id": "qwen3.5-4b",
        "name": "lan-qwen3.5-4b-q4_k_m",
        "file": "Qwen3.5-4B-Q4_K_M.gguf",
        "label": "Qwen3.5-4B Q4_K_M",
        "desc": "速度和质量的平衡，适合中端独显 / 核显。",
        "repo": "unsloth/Qwen3.5-4B-GGUF",
        "size_bytes": 2_600_000_000,
        "size": "2.6 GB",
    },
    {
        "id": "qwen3.5-2b",
        "name": "lan-qwen3.5-2b-q4_k_m",
        "file": "Qwen3.5-2B-Q4_K_M.gguf",
        "label": "Qwen3.5-2B Q4_K_M",
        "desc": "低配机器友好，回答质量有限但够日常使用。",
        "repo": "unsloth/Qwen3.5-2B-GGUF",
        "size_bytes": 1_200_000_000,
        "size": "1.2 GB",
    },
    {
        "id": "qwen2.5-7b",
        "name": "qwen-copilot",
        "file": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "label": "Qwen2.5-7B Q4_K_M",
        "desc": "成熟稳定的备用模型，兼容性最好。",
        "repo": "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "size_bytes": 4_400_000_000,
        "size": "4.4 GB",
    },
    {
        "id": "qwen2.5-3b",
        "name": "lan-qwen2.5-3b-instruct-q4_k_m",
        "file": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "label": "Qwen2.5-3B Q4_K_M",
        "desc": "小体积、较稳，适合纯 CPU。",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "size_bytes": 2_000_000_000,
        "size": "2.0 GB",
    },
    {
        "id": "qwen2.5-1.5b",
        "name": "lan-qwen2.5-1.5b-instruct-q4_k_m",
        "file": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "label": "Qwen2.5-1.5B Q4_K_M",
        "desc": "最低配兜底，下载和运行都快。",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "size_bytes": 1_000_000_000,
        "size": "1.0 GB",
    },
]


def _read_setup() -> dict[str, Any]:
    try:
        raw = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_setup(data: dict[str, Any]) -> None:
    config.save_json(SETUP_PATH, data)


def _has_all_models() -> bool:
    return bool(models.whisper_catalog()) and bool(models.llm_catalog())


class SetupManager:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.RLock()
        self._busy = False
        self._job: Optional[threading.Thread] = None
        self._stage = "models"
        self._progress = 0.0
        self._message = ""
        self._error = ""
        self._installing_ollama = False
        self._downloading = False
        self._cuda_error = ""
        self._downloads: dict[str, dict[str, Any]] = {}
        self._ollama_source_url = OLLAMA_ZIP_URL
        self._ollama_local_path = ""
        self._hardware: dict[str, Any] = dict(getattr(app, "hardware", {}) or {})
        self._recommended: dict[str, str] = {}
        self._whisper: list[dict[str, Any]] = []
        self._llm: list[dict[str, Any]] = []
        self._refresh_options()
        marker = _read_setup()
        ready = config.OLLAMA_BIN.exists() and _has_all_models()
        if marker.get("complete") and ready:
            self._required = False
            self._stage = "done"
            self._progress = 1.0
        elif ready:
            # Existing developer / carried installation: do not show the
            # first-run wizard merely because the new marker is absent.
            self._required = False
            self._stage = "done"
            self._progress = 1.0
            self._write_complete()
        else:
            self._required = True
            self._stage = "ollama" if not config.OLLAMA_BIN.exists() else "models"
            self._message = "首次启动需要准备本地运行环境。"

    @property
    def required(self) -> bool:
        return self._required

    def _write_complete(self, whisper: str = "", llm: str = "") -> None:
        try:
            _write_setup(
                {
                    "complete": True,
                    "completed_at": time.time(),
                    "whisper": whisper,
                    "llm": llm,
                    "ollama": str(config.OLLAMA_BIN),
                    "models_dir": str(config.MODELS_DIR),
                }
            )
        except Exception:
            pass

    def _detect_hardware(self) -> dict[str, Any]:
        if not self._hardware:
            try:
                self._hardware = hardware.detect()
            except Exception:
                self._hardware = {"profile": "cpu", "ram_gb": 0, "gpu_label": "", "cpu": ""}
        profile = self._hardware
        mode = str(profile.get("profile", "cpu") or "cpu")
        ram = float(profile.get("ram_gb", 0) or 0)
        if mode == "nvidia":
            whisper_id = "large-v3-turbo"
            llm_id = "qwen3.5-9b" if ram >= 16 else "qwen3.5-4b"
        elif mode in ("amd_vulkan", "intel_vulkan"):
            whisper_id = "small"
            llm_id = "qwen3.5-4b" if ram >= 16 else "qwen3.5-2b"
        else:
            whisper_id = "base"
            llm_id = "qwen2.5-1.5b" if ram and ram < 16 else "qwen2.5-3b"
        self._recommended = {"whisper": whisper_id, "llm": llm_id}
        return profile

    def _refresh_options(self) -> None:
        profile = self._detect_hardware()
        whisper_rows = models.whisper_catalog()
        llm_rows = models.llm_catalog()
        w_downloaded = {item["name"] for item in whisper_rows}
        l_downloaded = {item["name"] for item in llm_rows}
        self._whisper = []
        preset_whisper_dirs = {item["dir"] for item in WHISPER_OPTIONS}
        for item in WHISPER_OPTIONS:
            row = dict(item)
            row["downloaded"] = item["dir"] in w_downloaded
            row["recommended"] = item["id"] == self._recommended.get("whisper")
            self._whisper.append(row)
        for item in whisper_rows:
            if item["name"] in preset_whisper_dirs:
                continue
            self._whisper.append(
                {
                    "id": f"local-whisper-{item['name']}",
                    "dir": item["name"],
                    "label": item.get("label") or item["name"],
                    "desc": "本地已有模型（models 目录）",
                    "repo": "",
                    "size_bytes": int(item.get("size_bytes", 0) or 0),
                    "size": item.get("size", ""),
                    "downloaded": True,
                    "recommended": False,
                    "local": True,
                }
            )
        self._llm = []
        preset_llm_names = {item["name"] for item in LLM_OPTIONS}
        for item in LLM_OPTIONS:
            row = dict(item)
            row["downloaded"] = item["name"] in l_downloaded
            row["recommended"] = item["id"] == self._recommended.get("llm")
            self._llm.append(row)
        for item in llm_rows:
            if item["name"] in preset_llm_names:
                continue
            self._llm.append(
                {
                    "id": f"local-llm-{item['name']}",
                    "name": item["name"],
                    "file": item.get("file", ""),
                    "label": item.get("label") or item["name"],
                    "desc": "本地已有模型（models/llm 目录）",
                    "repo": "",
                    "size_bytes": int(item.get("size_bytes", 0) or 0),
                    "size": item.get("size", ""),
                    "downloaded": True,
                    "recommended": False,
                    "local": True,
                }
            )

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def import_local_model(self, kind: str, source_path: str) -> dict[str, Any]:
        """Copy a local Whisper folder / GGUF into models/ and refresh setup options.

        Presets are matched by folder name, GGUF file name, or model.bin/file
        size so a local copy of a preset is shown as "已下载" instead of
        becoming a duplicate entry.
        """
        kind = str(kind or "").strip()
        if kind not in ("whisper", "llm"):
            raise ValueError("模型类型必须是 whisper 或 llm")
        if self._busy:
            raise ValueError("正在下载模型，请稍后再导入")
        source = Path(str(source_path or "").strip().strip('"')).expanduser()
        if not source.exists():
            raise ValueError("选择的模型路径不存在")
        models_dir = config.MODELS_DIR
        models_dir.mkdir(parents=True, exist_ok=True)
        self._set(_message="正在导入本地模型…")
        self._publish()
        matched_preset = False
        selected_id = ""
        target: Path
        if kind == "whisper":
            if not source.is_dir() or source.resolve() == models_dir.resolve():
                raise ValueError("Whisper 模型必须选择一个 faster-whisper 目录")
            if not (source / "model.bin").is_file() or not (source / "config.json").is_file():
                raise ValueError("Whisper 目录必须包含 model.bin 和 config.json")
            source_size = (source / "model.bin").stat().st_size
            preset = next(
                (
                    item
                    for item in WHISPER_OPTIONS
                    if item["dir"] in (source.name, f"faster-whisper-{source.name}")
                ),
                None,
            )
            if preset is None:
                preset = next(
                    (
                        item
                        for item in WHISPER_OPTIONS
                        if abs(source_size - int(item["size_bytes"]))
                        <= max(2_000_000, int(int(item["size_bytes"]) * 0.005))
                    ),
                    None,
                )
            if preset is not None:
                target = models_dir / preset["dir"]
                matched_preset = True
                selected_id = str(preset["id"])
            else:
                name = source.name if "whisper" in source.name.lower() else f"faster-whisper-{source.name}"
                target = models_dir / name
            if source.resolve() != target.resolve():
                if target.exists():
                    if not (target / "model.bin").is_file() or not (target / "config.json").is_file():
                        raise ValueError("models 目录里已有同名模型，但不完整")
                elif self._is_under(source, models_dir):
                    source.rename(target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_name(target.name + ".importing")
                    shutil.rmtree(temp, ignore_errors=True)
                    try:
                        shutil.copytree(source, temp)
                        temp.rename(target)
                    except Exception:
                        shutil.rmtree(temp, ignore_errors=True)
                        raise
            models.invalidate_cache()
            if preset is None:
                try:
                    models.add_custom("whisper", target.name, str(target), "本地导入")
                except ValueError:
                    pass
                selected_id = f"local-whisper-{target.name}"
        else:
            if not source.is_file() or source.suffix.lower() != ".gguf":
                raise ValueError("语言模型必须选择一个 .gguf 文件")
            if source.stat().st_size < 100 * 1024 * 1024:
                raise ValueError("这个 GGUF 文件太小，不像完整模型")
            source_size = source.stat().st_size
            preset = next(
                (item for item in LLM_OPTIONS if item["file"].lower() == source.name.lower()),
                None,
            )
            if preset is None:
                preset = next(
                    (
                        item
                        for item in LLM_OPTIONS
                        if abs(source_size - int(item["size_bytes"]))
                        <= max(5_000_000, int(int(item["size_bytes"]) * 0.005))
                    ),
                    None,
                )
            if preset is not None:
                target = models_dir / "llm" / preset["file"]
                matched_preset = True
                selected_id = str(preset["id"])
            else:
                target = models_dir / "llm" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != target.resolve():
                if target.exists():
                    if target.stat().st_size < 100 * 1024 * 1024:
                        raise ValueError("models/llm 目录里已有同名模型，但不完整")
                elif self._is_under(source, models_dir):
                    source.replace(target)
                else:
                    temp = target.with_name(target.name + ".importing")
                    temp.unlink(missing_ok=True)
                    try:
                        shutil.copy2(source, temp)
                        temp.replace(target)
                    except Exception:
                        temp.unlink(missing_ok=True)
                        raise
            models.invalidate_cache()
            if preset is None:
                try:
                    models.add_custom("llm", target.stem, str(target), "本地导入")
                except ValueError:
                    pass
                selected_id = f"local-llm-{models._llm_ollama_name(target, models._params_from_name(target.stem))}"
        models.invalidate_cache()
        self._refresh_options()
        self._set(_message="本地模型已加入列表。")
        self._publish()
        return {
            "kind": kind,
            "path": str(target),
            "selected_id": selected_id,
            "matched_preset": matched_preset,
            "state": self.public_state(),
        }

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "required": self._required,
                "complete": not self._required,
                "version": version.VERSION,
                "display_version": version.DISPLAY_VERSION,
                "ui_language": str(getattr(self.app, "settings", {}).get("ui_language", "zh") or "zh"),
                "stage": self._stage,
                "progress": round(max(0.0, min(1.0, self._progress)), 4),
                "message": self._message,
                "error": self._error,
                "busy": self._busy,
                "installing_ollama": self._installing_ollama,
                "downloading": self._downloading,
                "ollama": {
                    "installed": config.OLLAMA_BIN.exists(),
                    "path": str(config.OLLAMA_BIN),
                },
                "ollama_sources": [dict(item) for item in OLLAMA_SOURCES],
                "ollama_source_url": self._ollama_source_url,
                "cuda": {
                    "required": str(self._hardware.get("profile", "cpu")) == "nvidia",
                    "available": cuda_runtime.is_available(),
                    "error": self._cuda_error,
                },
                "hardware": self._hardware,
                "recommended": dict(self._recommended),
                "whisper": [dict(row) for row in self._whisper],
                "llm": [dict(row) for row in self._llm],
                "downloads": {key: dict(value) for key, value in self._downloads.items()},
                "models_dir": str(config.MODELS_DIR),
                "setup_path": str(SETUP_PATH),
            }

    def _set(self, **updates: Any) -> None:
        with self._lock:
            for key, value in updates.items():
                setattr(self, key, value)

    def _publish(self) -> None:
        try:
            self.app.bus.publish({"type": "setup", "data": self.public_state()})
        except Exception:
            pass

    def install_ollama_async(self, source_url: str = "", local_path: str = "") -> bool:
        raw_local = str(local_path or "").strip().strip('"')
        local_zip = Path(raw_local).expanduser() if raw_local else None
        if local_zip is not None:
            try:
                if not local_zip.is_file() or not zipfile.is_zipfile(local_zip):
                    raise ValueError("不是有效的 ZIP")
                with zipfile.ZipFile(local_zip) as archive:
                    if not any(name.replace("\\", "/").endswith("ollama.exe") for name in archive.namelist()):
                        raise ValueError("ZIP 中没有 ollama.exe")
            except Exception as exc:
                with self._lock:
                    self._error = f"本地 Ollama 安装包无效：{exc}"
                    self._stage = "error"
                    self._message = self._error
                self._publish()
                return False
            selected_url = ""
        else:
            raw_url = str(source_url or "").strip()
            if raw_url:
                try:
                    parsed = urllib.parse.urlparse(raw_url)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        raise ValueError("unsupported URL")
                except Exception:
                    with self._lock:
                        self._error = "Ollama 下载地址无效，请填写 http/https ZIP 地址。"
                        self._stage = "error"
                        self._message = self._error
                    self._publish()
                    return False
                selected_url = raw_url[:1000]
            else:
                selected_url = OLLAMA_ZIP_URL
        with self._lock:
            if self._busy:
                return False
            if config.OLLAMA_BIN.exists():
                self._stage = "models" if not _has_all_models() else "done"
                self._message = "Ollama 已就绪。"
                return True
            self._ollama_source_url = selected_url
            self._ollama_local_path = str(local_zip) if local_zip is not None else ""
            self._busy = True
            self._installing_ollama = True
            self._error = ""
            self._stage = "ollama"
            self._progress = 0.0
            self._message = (
                "正在安装本地 Ollama 安装包…" if local_zip is not None else "正在下载 Ollama 便携版…"
            )
        self._job = threading.Thread(target=self._install_ollama_job, name="setup-ollama", daemon=True)
        self._job.start()
        self._publish()
        return True

    def _install_ollama_job(self) -> None:
        try:
            self._install_ollama()
            self._install_cuda_if_needed()
            with self._lock:
                self._installing_ollama = False
                self._stage = "models"
                self._progress = 0.0
                self._message = "Ollama 安装完成。请选择要下载的模型。"
                self._busy = False
                if self._cuda_error:
                    self._message = f"将使用 CPU：{self._cuda_error}"
            self._publish()
        except Exception as exc:
            with self._lock:
                self._installing_ollama = False
                self._busy = False
                self._stage = "error"
                self._error = f"Ollama 安装失败：{exc}"
                self._message = "可以重试，或手动安装 Ollama 后点重试。"
            self._publish()

    def _install_cuda_if_needed(self) -> None:
        profile = self._detect_hardware()
        if str(profile.get("profile", "cpu")) != "nvidia":
            return
        if cuda_runtime.is_available():
            return
        self._set(
            _progress=0.86,
            _message="检测到 NVIDIA 显卡，正在下载 CUDA 加速组件（约 1.3 GB）…",
        )
        self._publish()
        try:
            def on_progress(value: float, message: str) -> None:
                self._set(_progress=0.86 + max(0.0, min(1.0, value)) * 0.12, _message=message)
                self._publish()

            cuda_runtime.install(on_progress)
            self._cuda_error = ""
        except Exception as exc:
            # Keep the app usable on CPU if the optional runtime cannot be fetched.
            self._cuda_error = f"CUDA 加速组件下载失败：{exc}"
            self._set(_message=self._cuda_error + "；将继续使用 CPU。")
            self._publish()

    def _ollama_candidates(self) -> list[str]:
        selected = str(getattr(self, "_ollama_source_url", OLLAMA_ZIP_URL) or OLLAMA_ZIP_URL)
        values = [selected]
        values.extend(str(item["url"]) for item in OLLAMA_SOURCES)
        result: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in result:
                result.append(value)
        return result

    def _download_ollama_archive(self, url: str, archive: Path) -> None:
        partial = archive.with_name(archive.name + ".part")
        offset = partial.stat().st_size if partial.is_file() else 0
        headers = {"User-Agent": "Lan.Assistant-Setup/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                partial.unlink(missing_ok=True)
            raise
        with response:
            status = int(getattr(response, "status", 200) or 200)
            total_header = int(response.headers.get("Content-Length", "0") or 0)
            append = bool(offset and status == 206)
            if not append:
                offset = 0
            total = offset + total_header if total_header else 0
            received = 0
            mode = "ab" if append else "wb"
            with partial.open(mode) as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if total:
                        self._set(_progress=min(0.85, (offset + received) / total * 0.85))
                    self._publish()
        downloaded = partial.stat().st_size if partial.is_file() else 0
        if total_header and downloaded < total:
            raise RuntimeError(
                f"下载不完整：{downloaded}/{total} 字节；保留断点，重试会继续"
            )
        if downloaded < 1_000_000:
            partial.unlink(missing_ok=True)
            raise _InvalidArchive("下载内容太小，可能不是 Ollama ZIP")
        if not zipfile.is_zipfile(partial):
            partial.unlink(missing_ok=True)
            raise _InvalidArchive("下载内容不是有效 ZIP，可能被代理/镜像返回了 HTML")
        partial.replace(archive)

    def _install_ollama(self) -> None:
        target_dir = config.OLLAMA_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        downloads = config.LOCAL_APP_DIR / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        local_path = str(getattr(self, "_ollama_local_path", "") or "").strip()
        downloaded_archive = not bool(local_path)
        if local_path:
            archive = Path(local_path)
            if not archive.is_file() or not zipfile.is_zipfile(archive):
                raise RuntimeError("本地 Ollama ZIP 不存在或已损坏")
            self._set(_progress=0.15, _message=f"正在使用本地 Ollama 安装包：{archive.name}")
            self._publish()
        else:
            archive = downloads / "ollama-windows-amd64.zip"
            partial = archive.with_name(archive.name + ".part")
            archive.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
            last_error: Exception | None = None
            for url in self._ollama_candidates():
                for attempt in (1, 2):
                    try:
                        host = urllib.parse.urlparse(url).netloc or "下载源"
                        self._set(
                            _progress=0.0,
                            _message=f"正在从 {host} 下载 Ollama（约 1.6 GB）…",
                        )
                        self._publish()
                        self._download_ollama_archive(url, archive)
                        break
                    except _InvalidArchive as exc:
                        last_error = exc
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt == 1 and partial.is_file():
                            self._set(_message="下载中断，正在从断点继续…")
                            self._publish()
                            continue
                        partial.unlink(missing_ok=True)
                        break
                if archive.is_file():
                    break
            if not archive.is_file():
                raise RuntimeError(
                    f"Ollama 安装包下载失败，已尝试多个下载源。最后一次错误：{last_error}"
                )
        self._set(_progress=0.88, _message="正在解压 Ollama…")
        self._publish()
        temp_extract = downloads / "ollama-extract"
        if temp_extract.exists():
            shutil.rmtree(temp_extract, ignore_errors=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temp_extract)
        candidates = list(temp_extract.rglob("ollama.exe"))
        if not candidates:
            raise RuntimeError("下载的压缩包中没有 ollama.exe")
        source = candidates[0].parent
        for item in source.iterdir():
            dest = target_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        if not config.OLLAMA_BIN.exists():
            raise RuntimeError("ollama.exe 未安装到预期位置")
        shutil.rmtree(temp_extract, ignore_errors=True)
        if downloaded_archive:
            archive.unlink(missing_ok=True)
        self._set(_progress=0.95, _message="Ollama 已安装。")
        self._publish()

    def download_preset_async(self, kind: str, model_id: str) -> tuple[bool, str]:
        """Download one preset Whisper/LLM model from the model manager.

        Unlike the first-run flow this never changes the selected model, and it
        is only available for built-in options (custom files are manual imports).
        """
        kind = str(kind or "")
        model_id = str(model_id or "")
        with self._lock:
            if self._busy:
                return False, "已有模型正在下载，请稍候"
            if kind == "whisper":
                whisper_id = model_id[len("whisper-"):] if model_id.startswith("whisper-") else model_id
                item = next(
                    (
                        row
                        for row in self._whisper
                        if row["id"] in (model_id, whisper_id) or row["dir"] == model_id
                    ),
                    None,
                )
            elif kind == "llm":
                item = next((row for row in self._llm if row["id"] == model_id), None)
            else:
                return False, "只支持预设的语音识别或语言模型"
            if item is None:
                return False, "找不到这个预设模型"
            if kind == "whisper":
                target = config.MODELS_DIR / item["dir"]
                if (target / "model.bin").exists() and (target / "config.json").exists():
                    return True, "模型已经存在"
            else:
                target = config.MODELS_DIR / "llm" / item["file"]
                if target.exists() and target.stat().st_size > 100 * 1024 * 1024:
                    return True, "模型已经存在"
            self._busy = True
            self._downloading = True
            self._error = ""
            self._stage = "models"
            self._progress = 0.0
            self._message = f"正在下载 {item['label']}…"
            self._downloads = {
                item["id"]: {
                    "label": item["label"],
                    "kind": kind,
                    "status": "pending",
                    "progress": 0.0,
                    "size": item.get("size", ""),
                }
            }
            self._job = threading.Thread(
                target=self._download_preset_job,
                args=(kind, dict(item)),
                name="model-manager-download",
                daemon=True,
            )
            self._job.start()
        self._publish()
        return True, "已开始下载"

    def _download_preset_job(self, kind: str, item: dict[str, Any]) -> None:
        try:
            self._update_download(item["id"], status="downloading", progress=0.0)
            self._publish()
            if kind == "whisper":
                self._download_whisper(item)
            else:
                self._download_llm(item)
            models.invalidate_cache()
            self._update_download(item["id"], status="done", progress=1.0)
            self._set(_message=f"{item['label']} 下载完成")
        except Exception as exc:
            with self._lock:
                self._error = f"模型下载失败：{exc}"
                self._message = "检查网络后重试；已下载的文件会保留"
                self._stage = "error"
                self._update_download(item["id"], status="error")
        finally:
            with self._lock:
                self._downloading = False
                self._busy = False
            self._publish()

    def download_async(self, whisper_ids: list[str], llm_ids: list[str]) -> bool:
        with self._lock:
            if self._busy:
                return False
            whisper_ids = [str(value) for value in whisper_ids if value]
            llm_ids = [str(value) for value in llm_ids if value]
            if not whisper_ids or not llm_ids:
                self._error = "请至少选择一个 Whisper 和一个 LLM 模型。"
                return False
            options_w = {item["id"]: item for item in self._whisper}
            options_l = {item["id"]: item for item in self._llm}
            selected_w = [options_w[item] for item in whisper_ids if item in options_w]
            selected_l = [options_l[item] for item in llm_ids if item in options_l]
            if not selected_w or not selected_l:
                self._error = "模型选项无效。"
                return False
            self._busy = True
            self._downloading = True
            self._error = ""
            self._stage = "models"
            self._progress = 0.0
            self._message = "准备下载模型…"
            self._downloads = {}
            for item in selected_w + selected_l:
                self._downloads[item["id"]] = {
                    "label": item["label"],
                    "kind": "whisper" if item in selected_w else "llm",
                    "status": "pending",
                    "progress": 0.0,
                    "size": item["size"],
                }
        self._job = threading.Thread(
            target=self._download_job,
            args=(selected_w, selected_l),
            name="setup-models",
            daemon=True,
        )
        self._job.start()
        self._publish()
        return True

    def _update_download(self, item_id: str, **updates: Any) -> None:
        with self._lock:
            current = self._downloads.get(item_id)
            if current is None:
                return
            current.update(updates)

    def _download_job(self, selected_w: list[dict[str, Any]], selected_l: list[dict[str, Any]]) -> None:
        try:
            if not config.OLLAMA_BIN.exists():
                with self._lock:
                    self._installing_ollama = True
                self._install_ollama()
                with self._lock:
                    self._installing_ollama = False
            total = len(selected_w) + len(selected_l)
            done = 0
            for item in selected_w:
                self._update_download(item["id"], status="downloading", progress=0.0)
                self._set(_message=f"正在下载 {item['label']}…")
                self._publish()
                self._download_whisper(item)
                self._update_download(item["id"], status="done", progress=1.0)
                done += 1
                self._set(_progress=done / total)
                self._publish()
            for item in selected_l:
                self._update_download(item["id"], status="downloading", progress=0.0)
                self._set(_message=f"正在下载 {item['label']}…")
                self._publish()
                self._download_llm(item)
                self._update_download(item["id"], status="done", progress=1.0)
                done += 1
                self._set(_progress=done / total)
                self._publish()
            self._finish_setup(selected_w[0], selected_l[0])
        except Exception as exc:
            with self._lock:
                self._downloading = False
                self._busy = False
                self._stage = "error"
                self._error = f"模型下载失败：{exc}"
                self._message = "检查网络后重试；已经下载完的文件会保留。"
            self._publish()

    def _download_whisper(self, item: dict[str, Any]) -> None:
        target = config.MODELS_DIR / item["dir"]
        if (target / "model.bin").exists() and (target / "config.json").exists():
            return
        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:
            raise RuntimeError("缺少 huggingface_hub，无法下载模型") from exc
        target.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        monitor = threading.Thread(
            target=self._monitor_size,
            args=(item["id"], target, int(item["size_bytes"]), stop),
            name="setup-progress",
            daemon=True,
        )
        monitor.start()
        try:
            self._hf_snapshot(item["repo"], target)
        finally:
            stop.set()
        self._update_download(item["id"], progress=1.0)
        if not (target / "model.bin").exists() or not (target / "config.json").exists():
            raise RuntimeError(f"{item['label']} 下载不完整")

    def _download_llm(self, item: dict[str, Any]) -> None:
        target = config.MODELS_DIR / "llm" / item["file"]
        if target.exists() and target.stat().st_size > 100 * 1024 * 1024:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        monitor = threading.Thread(
            target=self._monitor_file,
            args=(item["id"], target, int(item["size_bytes"]), stop),
            name="setup-progress",
            daemon=True,
        )
        monitor.start()
        try:
            self._hf_file(item["repo"], item["file"], target.parent)
        finally:
            stop.set()
        if not target.exists() or target.stat().st_size < 100 * 1024 * 1024:
            raise RuntimeError(f"{item['label']} 下载不完整")

    def _hf_endpoints(self) -> list[str]:
        """Selected download source first, then built-in mirrors as fallbacks."""
        settings = getattr(self.app, "settings", {}) or {}
        sources = settings.get("model_download_sources")
        if not isinstance(sources, list):
            sources = []
        selected_id = str(settings.get("model_download_source", "hf-mirror") or "hf-mirror")
        selected = next(
            (item for item in sources if isinstance(item, dict) and str(item.get("id")) == selected_id),
            None,
        )
        endpoints: list[str] = []
        for value in (
            (selected or {}).get("url"),
            HF_ENDPOINT,
            "https://huggingface.co",
        ):
            url = str(value or "").strip().rstrip("/")
            if url and url not in endpoints:
                endpoints.append(url)
        return endpoints

    def _hf_snapshot(self, repo: str, target: Path) -> None:
        from huggingface_hub import snapshot_download

        last_error: Optional[Exception] = None
        for endpoint in self._hf_endpoints():
            try:
                snapshot_download(
                    repo_id=repo,
                    local_dir=str(target),
                    endpoint=endpoint,
                    local_files_only=False,
                    max_workers=4,
                )
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error or "模型仓库下载失败"))

    def _hf_file(self, repo: str, filename: str, local_dir: Path) -> None:
        from huggingface_hub import hf_hub_download

        last_error: Optional[Exception] = None
        for endpoint in self._hf_endpoints():
            try:
                hf_hub_download(
                    repo_id=repo,
                    filename=filename,
                    local_dir=str(local_dir),
                    endpoint=endpoint,
                )
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error or "模型文件下载失败"))

    def _monitor_size(self, item_id: str, target: Path, expected: int, stop: threading.Event) -> None:
        while not stop.wait(0.8):
            size = 0
            try:
                for path in target.rglob("*"):
                    if path.is_file():
                        size += path.stat().st_size
            except OSError:
                pass
            self._update_download(
                item_id,
                progress=min(0.97, size / expected if expected else 0.0),
            )
            self._publish()

    def _monitor_file(self, item_id: str, target: Path, expected: int, stop: threading.Event) -> None:
        incomplete = target.with_name(target.name + ".incomplete")
        while not stop.wait(0.8):
            size = 0
            for path in (target, incomplete):
                try:
                    if path.exists():
                        size = max(size, path.stat().st_size)
                except OSError:
                    pass
            self._update_download(
                item_id,
                progress=min(0.97, size / expected if expected else 0.0),
            )
            self._publish()

    def _finish_setup(self, whisper: dict[str, Any], llm: dict[str, Any]) -> None:
        profile = self._hardware or self._detect_hardware()
        updated = {
            **self.app.settings,
            "asr_model": whisper["dir"],
            "llm_model": llm["name"],
            "local_gpu_mode": "nvidia" if profile.get("profile") == "nvidia" else (
                "amd_vulkan" if profile.get("profile") == "amd_vulkan" else (
                    "intel_vulkan" if profile.get("profile") == "intel_vulkan" else "cpu"
                )
            ),
            "asr_device": "cuda" if profile.get("profile") == "nvidia" else "cpu",
            "hardware_auto_configure": False,
        }
        try:
            self.app.settings = config.save_settings(updated)
            models.invalidate_cache()
            self.app.refresh_local_model_bindings()
            self.app.brain.set_settings(self.app.settings)
        except Exception:
            pass
        self._write_complete(whisper["dir"], llm["name"])
        with self._lock:
            self._required = False
            self._downloading = False
            self._busy = False
            self._stage = "done"
            self._progress = 1.0
            self._message = "模型准备完成，正在启动主服务…"
            self._refresh_options()
        self._publish()
        try:
            threading.Thread(
                target=self.app.start_background,
                name="setup-start-app",
                daemon=True,
            ).start()
        except Exception:
            pass
