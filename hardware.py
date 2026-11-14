"""Hardware profile detection and conservative auto-configuration.

The app runs on NVIDIA CUDA machines, AMD/Intel integrated GPUs, and plain
CPUs.  Detection is intentionally conservative: it never changes the user's
API provider or API key, and it only applies a profile when the hardware
signature changes (or when the user explicitly asks to re-apply).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import config
import models

ROOT = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000

PROFILE_LABELS = {
    "nvidia": "NVIDIA CUDA",
    "amd_vulkan": "AMD 核显 / 显卡（Vulkan）",
    "intel_vulkan": "Intel 核显 / 显卡（Vulkan）",
    "cpu": "纯 CPU",
}


def _run(command: list[str], timeout: float = 8.0) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _windows_inventory() -> dict[str, Any]:
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1 "
        "Name,NumberOfCores,NumberOfLogicalProcessors;"
        "$cs=Get-CimInstance Win32_ComputerSystem;"
        "$gpus=@(Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterRAM);"
        "[pscustomobject]@{cpu=$cpu;ram=$cs.TotalPhysicalMemory;gpus=$gpus} | "
        "ConvertTo-Json -Depth 5 -Compress"
    )
    raw = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _nvidia_smi() -> dict[str, Any]:
    raw = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    if not raw:
        return {}
    first = raw.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    return {
        "name": parts[0] if parts else "NVIDIA GPU",
        "memory": parts[1] if len(parts) > 1 else "",
        "driver": parts[2] if len(parts) > 2 else "",
    }


def _gpu_kind(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("nvidia", "geforce", "quadro", "rtx ", "gtx ")):
        return "nvidia"
    if "intel" in lower:
        return "intel"
    if "amd" in lower or "radeon" in lower:
        return "amd"
    if "virtual" in lower or "basic display" in lower or "remote" in lower:
        return "virtual"
    return "other"


def detect() -> dict[str, Any]:
    inventory = _windows_inventory() if sys.platform == "win32" else {}
    cpu = inventory.get("cpu") or {}
    if not isinstance(cpu, dict):
        cpu = {}
    gpus_raw = inventory.get("gpus") or []
    if isinstance(gpus_raw, dict):
        gpus_raw = [gpus_raw]
    gpus: list[dict[str, Any]] = []
    for item in gpus_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name", "") or "").strip()
        kind = _gpu_kind(name)
        if kind == "virtual" or not name:
            continue
        gpus.append({
            "name": name,
            "kind": kind,
            "driver": str(item.get("DriverVersion", "") or ""),
            "memory_bytes": int(item.get("AdapterRAM", 0) or 0),
        })
    nvidia = _nvidia_smi()
    if nvidia and not any(g["kind"] == "nvidia" for g in gpus):
        gpus.append({
            "name": nvidia.get("name", "NVIDIA GPU"),
            "kind": "nvidia",
            "driver": nvidia.get("driver", ""),
            "memory_bytes": 0,
        })
    ram_bytes = int(inventory.get("ram", 0) or 0)
    ram_gb = round(ram_bytes / (1024 ** 3), 1) if ram_bytes else 0.0
    cpu_name = str(cpu.get("Name", "") or platform.processor() or "Unknown CPU").strip()
    cpu_vendor = "unknown"
    lower_cpu = cpu_name.lower()
    if "intel" in lower_cpu:
        cpu_vendor = "intel"
    elif "amd" in lower_cpu:
        cpu_vendor = "amd"
    if any(g["kind"] == "nvidia" for g in gpus):
        profile = "nvidia"
    elif any(g["kind"] == "amd" for g in gpus):
        profile = "amd_vulkan"
    elif any(g["kind"] == "intel" for g in gpus):
        profile = "intel_vulkan"
    else:
        profile = "cpu"
    if profile == "nvidia":
        gpu_label = next((g["name"] for g in gpus if g["kind"] == "nvidia"), "NVIDIA GPU")
    elif profile in ("amd_vulkan", "intel_vulkan"):
        gpu_label = next((g["name"] for g in gpus if g["kind"] == profile.split("_")[0]), "")
    else:
        gpu_label = "无独立/集成 GPU"
    signature = "|".join([
        platform.system(), platform.machine(), cpu_vendor, cpu_name, str(ram_gb),
        profile, ",".join(sorted(g["name"] for g in gpus)),
    ])
    return {
        "platform": platform.platform(),
        "cpu": cpu_name,
        "cpu_vendor": cpu_vendor,
        "cpu_cores": int(cpu.get("NumberOfCores", 0) or os.cpu_count() or 0),
        "cpu_threads": int(cpu.get("NumberOfLogicalProcessors", 0) or os.cpu_count() or 0),
        "ram_gb": ram_gb,
        "gpus": gpus,
        "gpu_label": gpu_label,
        "profile": profile,
        "profile_label": PROFILE_LABELS.get(profile, profile),
        "signature": signature,
        "cuda": profile == "nvidia",
    }


def recommend(profile: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    mode = str(profile.get("profile", "cpu"))
    if mode == "nvidia":
        gpu_mode = "nvidia"
        asr_device = "cuda"
        llm_model = "qwen3.5-copilot"
        env = {}
    elif mode in ("amd_vulkan", "intel_vulkan"):
        gpu_mode = mode
        asr_device = "cpu"
        llm_model = "qwen-copilot"
        env = {"OLLAMA_IGPU_ENABLE": "1", "OLLAMA_VULKAN": "1"}
    else:
        gpu_mode = "cpu"
        asr_device = "cpu"
        llm_model = "qwen-copilot"
        env = {
            "OLLAMA_IGPU_ENABLE": "0",
            "OLLAMA_VULKAN": "0",
            "GGML_VK_VISIBLE_DEVICES": "-1",
            "ROCR_VISIBLE_DEVICES": "-1",
            "CUDA_VISIBLE_DEVICES": "-1",
        }
    llm_choice = models.recommend_llm(profile)
    if llm_choice:
        llm_model = llm_choice["name"]
    whisper_choice = models.recommend_whisper(profile)
    asr_model = whisper_choice["name"] if whisper_choice else "auto"
    return {
        "profile": mode,
        "local_gpu_mode": gpu_mode,
        "asr_device": asr_device,
        "asr_model": asr_model,
        "llm_model": llm_model,
        "ollama_env": env,
    }


def apply_to_settings(settings: dict[str, Any], force: bool = False) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Return (new_settings, profile, changed). Never touches API provider/key."""
    profile = detect()
    if not settings.get("hardware_auto_configure", True) and not force:
        return settings, profile, False
    if not force and settings.get("hardware_signature") == profile.get("signature"):
        return settings, profile, False
    rec = recommend(profile, settings)
    updated = {
        **settings,
        "local_gpu_mode": rec["local_gpu_mode"],
        "asr_device": rec["asr_device"],
        "asr_model": rec.get("asr_model", settings.get("asr_model", "auto")),
        "llm_model": rec["llm_model"],
        "hardware_signature": profile.get("signature", ""),
        "hardware_profile": rec["profile"],
    }
    return updated, profile, True


def ollama_env(settings: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, str]:
    mode = str(settings.get("local_gpu_mode", "auto") or "auto")
    if mode == "auto":
        mode = str((profile or detect()).get("profile", "cpu"))
    if mode == "nvidia":
        return {"OLLAMA_IGPU_ENABLE": "0"}
    if mode in ("amd_vulkan", "intel_vulkan"):
        return {"OLLAMA_IGPU_ENABLE": "1", "OLLAMA_VULKAN": "1"}
    if mode == "cpu":
        return {
            "OLLAMA_IGPU_ENABLE": "0",
            "OLLAMA_VULKAN": "0",
            "GGML_VK_VISIBLE_DEVICES": "-1",
            "ROCR_VISIBLE_DEVICES": "-1",
            "CUDA_VISIBLE_DEVICES": "-1",
        }
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect hardware and optionally apply Lan.Assistant profile")
    parser.add_argument("--apply", action="store_true", help="write recommended local_gpu_mode/asr_device/llm_model")
    parser.add_argument("--json", action="store_true", help="print machine-readable profile")
    args = parser.parse_args()

    settings = config.load_settings()
    if args.apply:
        updated, profile, changed = apply_to_settings(settings, force=True)
        if changed:
            config.save_settings(updated)
        print(json.dumps({"profile": profile, "recommendation": recommend(profile, settings), "changed": changed},
                         ensure_ascii=False, indent=2))
        return 0
    profile = detect()
    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print(f"CPU: {profile['cpu']} ({profile['cpu_cores']}C/{profile['cpu_threads']}T)")
        print(f"RAM: {profile['ram_gb']} GB")
        print(f"GPU: {profile['gpu_label']}")
        print(f"Profile: {profile['profile_label']}")
        print(f"Recommendation: {json.dumps(recommend(profile, settings), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
