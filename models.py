"""Discover Whisper folders and local Qwen GGUF files.

The app never hardcodes a model just because it exists in a screenshot: both
the UI and the hardware auto-configuration build their choices from this
catalog.  Drop a faster-whisper model folder under models/ or a .gguf under
models/llm/ and it becomes selectable on the next scan.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import config

_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_SECONDS = 2.0
CUSTOM_MODELS_PATH = config.DATA_DIR / "model_catalog.json"


def invalidate_cache() -> None:
    _CACHE["data"] = None
    _CACHE["at"] = 0.0

_WHISPER_LABELS = {
    "large-v3-turbo": "Large v3 Turbo（最准，较慢）",
    "large-v3": "Large v3（最准，最慢）",
    "distil-large-v3": "Distil Large v3（英文快）",
    "medium": "Medium（平衡）",
    "small": "Small（快，CPU 推荐）",
    "base": "Base（很快，准确率较低）",
    "tiny": "Tiny（最快，准确率低）",
}


def _human_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    return f"{max(1, round(size / 1024 ** 2))} MB"


def _whisper_label(name: str) -> str:
    suffix = name
    for prefix in ("faster-whisper-", "whisper-", "fw-"):
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    return _WHISPER_LABELS.get(suffix, suffix.replace("-", " ").title())


def whisper_catalog() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if not config.MODELS_DIR.exists():
        return options
    for folder in sorted(config.MODELS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        if not (folder / "model.bin").exists() or not (folder / "config.json").exists():
            continue
        name = folder.name
        if "whisper" not in name.lower():
            continue
        try:
            size = (folder / "model.bin").stat().st_size
        except Exception:
            size = 0
        options.append({
            "name": name,
            "path": str(folder),
            "label": _whisper_label(name),
            "size": _human_size(size),
            "size_bytes": size,
        })
    priority = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium", "small", "base", "tiny"]
    def rank(item: dict[str, Any]) -> tuple[int, int]:
        name = item["name"]
        for index, token in enumerate(priority):
            if token in name:
                return index, -int(item.get("size_bytes", 0))
        return len(priority), -int(item.get("size_bytes", 0))
    options.sort(key=rank)
    return options


def _params_from_name(text: str) -> float:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*B(?![a-zA-Z])", text, re.IGNORECASE)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _quant_from_name(text: str) -> str:
    match = re.search(r"(Q\d+[A-Z0-9_]*)", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._")
    return value[:70] or "local"


def _llm_ollama_name(path: Path, params: float) -> str:
    lower = path.stem.lower()
    if "3.5" in lower and "9b" in lower:
        return "qwen3.5-copilot"
    if "2.5" in lower and "7b" in lower:
        return "qwen-copilot"
    return f"lan-{_slug(path.stem)}"


def _llm_label(path: Path, params: float, quant: str) -> str:
    stem = path.stem
    # Strip quant suffix for display; keep the useful model name.
    display = re.sub(r"[-_.]?Q\d+[A-Z0-9_]*$", "", stem, flags=re.IGNORECASE)
    display = display.replace("_", "-")
    if quant:
        display += f" ({quant})"
    return display


def llm_catalog() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    llm_dir = config.MODELS_DIR / "llm"
    if not llm_dir.exists():
        return options
    for path in sorted(llm_dir.rglob("*.gguf")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        if size < 100 * 1024 * 1024:
            continue
        params = _params_from_name(path.stem)
        quant = _quant_from_name(path.stem)
        name = _llm_ollama_name(path, params)
        options.append({
            "name": name,
            "path": str(path),
            "file": path.name,
            "label": _llm_label(path, params, quant),
            "size": _human_size(size),
            "size_bytes": size,
            "params_b": params,
            "quant": quant,
        })
    options.sort(key=lambda item: (item.get("params_b", 0) or 999, -int(item.get("size_bytes", 0))))
    return options


def catalog(refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if not refresh and _CACHE["data"] is not None and now - float(_CACHE["at"]) < _CACHE_SECONDS:
        return dict(_CACHE["data"])
    data = {
        "whisper": whisper_catalog(),
        "llm": llm_catalog(),
    }
    _CACHE["at"] = now
    _CACHE["data"] = data
    return dict(data)


def find_llm(name: str) -> Optional[dict[str, Any]]:
    wanted = str(name or "").split(":")[0]
    for item in llm_catalog():
        if item["name"] == wanted:
            return item
    for item in llm_catalog():
        if item["file"] == name or Path(item["path"]).stem == name:
            return item
    return None


def whisper_name(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw in ("auto", "default"):
        return "auto"
    for item in whisper_catalog():
        if raw == item["name"] or raw == item["path"] or Path(raw).name == item["name"]:
            return item["name"]
    return raw


def resolve_whisper(value: str, profile: dict[str, Any] | None = None) -> str:
    options = whisper_catalog()
    raw = str(value or "").strip()
    if raw and raw not in ("auto", "default"):
        path = Path(raw)
        if path.exists():
            return str(path)
        for item in options:
            if item["name"] == raw or Path(item["path"]).name == raw:
                return item["path"]
    recommended = recommend_whisper(profile, options)
    if recommended:
        return str(recommended["path"])
    if config.ASR_MODEL_DIR.exists():
        return str(config.ASR_MODEL_DIR)
    return str(config.ASR_MODEL_DIR)


def recommend_whisper(profile: dict[str, Any] | None, options: list[dict[str, Any]] | None = None) -> Optional[dict[str, Any]]:
    options = options if options is not None else whisper_catalog()
    if not options:
        return None
    mode = str((profile or {}).get("profile", "cpu"))
    if mode == "nvidia":
        preference = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium", "small", "base", "tiny"]
    else:
        preference = ["small", "base", "medium", "distil-large-v3", "large-v3-turbo", "large-v3", "tiny"]
    for token in preference:
        for item in options:
            if token in item["name"]:
                return item
    return options[0]


def recommend_llm(profile: dict[str, Any] | None, options: list[dict[str, Any]] | None = None) -> Optional[dict[str, Any]]:
    options = options if options is not None else llm_catalog()
    if not options:
        return None
    mode = str((profile or {}).get("profile", "cpu"))
    try:
        ram = float((profile or {}).get("ram_gb", 0) or 0)
    except Exception:
        ram = 0.0
    if mode == "nvidia":
        # Prefer the highest-quality model that still fits a normal laptop GPU.
        candidates = [item for item in options if 0 < float(item.get("params_b", 0) or 0) <= 10]
        candidates.sort(key=lambda item: float(item.get("params_b", 0) or 0), reverse=True)
        return candidates[0] if candidates else options[0]
    # CPU / shared-memory iGPU: smallest usable model wins.
    usable = [item for item in options if float(item.get("params_b", 0) or 0) >= 3]
    if ram and ram < 16:
        small = [item for item in usable if float(item.get("params_b", 0) or 0) <= 4]
        if small:
            usable = small
    usable.sort(key=lambda item: float(item.get("params_b", 0) or 999))
    if usable:
        return usable[0]
    return options[0]


# ------------------------------------------------------------------ management
_DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "id": "whisper-large-v3-turbo",
        "kind": "whisper",
        "name": "faster-whisper-large-v3-turbo",
        "label": "Whisper Large v3 Turbo",
        "path": "faster-whisper-large-v3-turbo",
        "size_hint": "1.5 GB",
        "source": "Systran/faster-whisper-large-v3-turbo",
    },
    {
        "id": "whisper-small",
        "kind": "whisper",
        "name": "faster-whisper-small",
        "label": "Whisper Small",
        "path": "faster-whisper-small",
        "size_hint": "461 MB",
        "source": "Systran/faster-whisper-small",
    },
    {
        "id": "whisper-base",
        "kind": "whisper",
        "name": "faster-whisper-base",
        "label": "Whisper Base",
        "path": "faster-whisper-base",
        "size_hint": "138 MB",
        "source": "Systran/faster-whisper-base",
    },
    {
        "id": "qwen3.5-9b",
        "kind": "llm",
        "name": "qwen3.5-copilot",
        "label": "Qwen3.5-9B Q4_K_M",
        "path": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_hint": "5.3 GB",
        "source": "unsloth/Qwen3.5-9B-GGUF",
    },
    {
        "id": "qwen3.5-4b",
        "kind": "llm",
        "name": "lan-qwen3.5-4b-q4_k_m",
        "label": "Qwen3.5-4B Q4_K_M",
        "path": "Qwen3.5-4B-Q4_K_M.gguf",
        "size_hint": "2.6 GB",
        "source": "unsloth/Qwen3.5-4B-GGUF",
    },
    {
        "id": "qwen3.5-2b",
        "kind": "llm",
        "name": "lan-qwen3.5-2b-q4_k_m",
        "label": "Qwen3.5-2B Q4_K_M",
        "path": "Qwen3.5-2B-Q4_K_M.gguf",
        "size_hint": "1.2 GB",
        "source": "unsloth/Qwen3.5-2B-GGUF",
    },
    {
        "id": "qwen2.5-7b",
        "kind": "llm",
        "name": "qwen-copilot",
        "label": "Qwen2.5-7B Q4_K_M",
        "path": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "size_hint": "4.4 GB",
        "source": "Qwen/Qwen2.5-7B-Instruct-GGUF",
    },
    {
        "id": "qwen2.5-3b",
        "kind": "llm",
        "name": "lan-qwen2.5-3b-instruct-q4_k_m",
        "label": "Qwen2.5-3B Q4_K_M",
        "path": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "size_hint": "2.0 GB",
        "source": "Qwen/Qwen2.5-3B-Instruct-GGUF",
    },
    {
        "id": "qwen2.5-1.5b",
        "kind": "llm",
        "name": "lan-qwen2.5-1.5b-instruct-q4_k_m",
        "label": "Qwen2.5-1.5B Q4_K_M",
        "path": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "size_hint": "1.0 GB",
        "source": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
    },
]


def _load_custom() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CUSTOM_MODELS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "") or "")
        path = str(item.get("path", "") or "").strip()
        if kind not in ("llm", "whisper") or not path:
            continue
        rows.append(
            {
                "id": str(item.get("id", "") or f"custom-{time.time_ns()}"),
                "kind": kind,
                "name": str(item.get("name", "") or ""),
                "label": str(item.get("label", "") or path)[:120],
                "path": path,
                "size_hint": str(item.get("size_hint", "") or ""),
                "source": str(item.get("source", "") or "")[:500],
                "custom": True,
            }
        )
    return rows


def _save_custom(rows: list[dict[str, Any]]) -> None:
    clean = [{k: v for k, v in row.items() if k != "custom"} for row in rows]
    CUSTOM_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_MODELS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CUSTOM_MODELS_PATH)


def _resolve_model_path(kind: str, raw: str) -> Path:
    path = Path(str(raw or "").strip())
    if not path.is_absolute():
        if path.parts and path.parts[0].lower() == "models":
            path = config.ROOT / path
        else:
            path = (config.MODELS_DIR / "llm" / path) if kind == "llm" else (config.MODELS_DIR / path)
    return path.resolve()


def _is_under_models(path: Path) -> bool:
    try:
        path.relative_to(config.MODELS_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def _model_state(entry: dict[str, Any]) -> dict[str, Any]:
    kind = str(entry.get("kind", "") or "")
    path = _resolve_model_path(kind, str(entry.get("path", "") or ""))
    size = 0
    downloaded = False
    if kind == "whisper":
        downloaded = (path / "model.bin").exists() and (path / "config.json").exists()
        if (path / "model.bin").exists():
            try:
                size = (path / "model.bin").stat().st_size
            except Exception:
                size = 0
    else:
        downloaded = path.is_file() and path.stat().st_size >= 1024 * 1024
        try:
            size = path.stat().st_size if path.is_file() else 0
        except Exception:
            size = 0
    row = dict(entry)
    row.update(
        {
            "id": str(entry.get("id", "") or path.name),
            "kind": kind,
            "label": str(entry.get("label", "") or path.name),
            "path": str(path),
            "downloaded": bool(downloaded),
            "size_bytes": int(size),
            "size": _human_size(size) if downloaded else str(entry.get("size_hint", "") or "—"),
            "custom": bool(entry.get("custom")),
        }
    )
    row.pop("size_hint", None)
    return row


def _diarization_rows() -> list[dict[str, Any]]:
    """Built-in diarization files are managed alongside ASR/LLM models."""
    try:
        import diarization
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    entries = [
        (
            "diarization-segmentation",
            "pyannote Segmentation 3.0",
            diarization.SEGMENTATION_MODEL,
        ),
    ]
    for model_id, info in diarization.EMBEDDING_MODELS.items():
        entries.append(
            (
                f"diarization-{model_id}",
                f"{info['short']}（说话人声纹）",
                diarization.embedding_path(model_id),
            )
        )
    for model_id, label, path in entries:
        try:
            size = path.stat().st_size if path.is_file() else 0
        except Exception:
            size = 0
        downloaded = size > 1_000_000
        rows.append(
            {
                "id": model_id,
                "kind": "diarization",
                "label": label,
                "path": str(path),
                "downloaded": downloaded,
                "size_bytes": size,
                "size": _human_size(size) if downloaded else "—",
                "custom": False,
            }
        )
    return rows


def management(refresh: bool = False) -> dict[str, Any]:
    # Do not use the model cache here: the management page is the place where
    # deletes/adds must be reflected immediately.
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _DEFAULT_MODELS + _load_custom():
        row = _model_state(entry)
        key = f"{row['kind']}:{row['path']}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    for row in _diarization_rows():
        key = f"{row['kind']}:{row['path']}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    downloaded = [row for row in rows if row["downloaded"]]
    disk_size = 0
    try:
        for path in config.MODELS_DIR.rglob("*"):
            if path.is_file():
                try:
                    disk_size += path.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return {
        "models": rows,
        "downloaded": len(downloaded),
        "total": len(rows),
        "total_size_bytes": sum(int(row.get("size_bytes", 0) or 0) for row in downloaded),
        "disk_size_bytes": disk_size,
        "models_dir": str(config.MODELS_DIR),
    }


def add_custom(kind: str, label: str, path: str, source: str = "", size_hint: str = "") -> dict[str, Any]:
    kind = str(kind or "").strip()
    if kind not in ("llm", "whisper"):
        raise ValueError("模型类型必须是 llm 或 whisper")
    label = str(label or "").strip()
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("请填写模型路径或文件名")
    resolved = _resolve_model_path(kind, raw_path)
    if not _is_under_models(resolved):
        raise ValueError("模型路径必须位于项目 models 目录内")
    if kind == "llm" and resolved.suffix.lower() != ".gguf":
        resolved = resolved.with_suffix(".gguf")
    custom = _load_custom()
    if any(str(_resolve_model_path(row["kind"], row["path"])) == str(resolved) for row in custom):
        raise ValueError("这个模型路径已经存在")
    import uuid
    row = {
        "id": f"custom-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "name": resolved.stem if kind == "llm" else resolved.name,
        "label": label or resolved.stem,
        "path": str(resolved),
        "size_hint": str(size_hint or ""),
        "source": str(source or ""),
        "custom": True,
    }
    custom.append(row)
    _save_custom(custom)
    _CACHE["data"] = None
    return _model_state(row)


def delete_model(model_id: str) -> dict[str, Any]:
    if str(model_id).startswith("diarization-"):
        row = next(
            (item for item in _diarization_rows() if str(item["id"]) == str(model_id)),
            None,
        )
        if not row:
            raise ValueError("找不到这个说话人区分模型")
        path = Path(str(row["path"]))
        if not row.get("downloaded"):
            raise ValueError("这个模型还没有下载")
        if not _is_under_models(path):
            raise ValueError("拒绝删除 models 目录之外的文件")
        try:
            path.unlink()
        except OSError as exc:
            raise ValueError(f"删除失败：{exc}") from exc
        _CACHE["data"] = None
        return management(refresh=True)
    target = None
    custom_entry = None
    custom = _load_custom()
    default = next((row for row in _DEFAULT_MODELS if str(row.get("id")) == model_id), None)
    if default:
        target = _model_state(default)
    else:
        custom_entry = next((row for row in custom if str(row.get("id")) == model_id), None)
        if custom_entry:
            target = _model_state(custom_entry)
    if not target:
        raise ValueError("找不到这个模型")
    if not target.get("downloaded"):
        if custom_entry is not None:
            custom = [row for row in custom if str(row.get("id")) != model_id]
            _save_custom(custom)
            return management(refresh=True)
        raise ValueError("这个模型还没有下载")
    path = Path(str(target["path"]))
    if not _is_under_models(path) or path == config.MODELS_DIR.resolve():
        raise ValueError("拒绝删除 models 目录之外或目录本身")
    if target["kind"] == "llm":
        try:
            params = _params_from_name(path.stem)
            ollama_name = _llm_ollama_name(path, params)
            env = os.environ.copy()
            env["OLLAMA_HOST"] = "127.0.0.1:11434"
            env["OLLAMA_MODELS"] = str(config.OLLAMA_MODELS_DIR)
            subprocess.run(
                [str(config.OLLAMA_BIN), "rm", ollama_name],
                cwd=str(config.OLLAMA_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=0x08000000,
            )
        except Exception:
            pass
        path.unlink(missing_ok=True)
    else:
        if path.exists():
            shutil.rmtree(path)
    custom = [row for row in custom if str(row.get("id")) != model_id]
    _save_custom(custom)
    _CACHE["data"] = None
    return management(refresh=True)

