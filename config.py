"""Configuration and small persistence helpers for Lan.Assistant."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import api_secrets
import local_profile

FROZEN = bool(getattr(sys, "frozen", False))
_SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = Path(sys.executable).resolve().parent if FROZEN else _SCRIPT_ROOT
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
LOCAL_APP_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Lan.Assistant" if os.environ.get("LOCALAPPDATA") else Path.home() / "AppData" / "Local" / "Lan.Assistant"
DATA_DIR = (LOCAL_APP_DIR / "data") if FROZEN else (ROOT / "data")
SESSIONS_DIR = DATA_DIR / "sessions"
VOICE_HISTORY_PATH = DATA_DIR / "voice_history.json"
_PROJECT_MODELS_DIR = ROOT / "models"
_PROJECT_HAS_MODELS = bool(
    list(_PROJECT_MODELS_DIR.glob("faster-whisper-*/model.bin"))
    or list(_PROJECT_MODELS_DIR.glob("llm/*.gguf"))
)
MODELS_DIR = (LOCAL_APP_DIR / "models") if FROZEN or not _PROJECT_HAS_MODELS else _PROJECT_MODELS_DIR
PROFILE_PATH = local_profile.profile_path()
LEGACY_PROFILE_PATH = DATA_DIR / "profile.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
ASR_MODEL_DIR = MODELS_DIR / "faster-whisper-large-v3-turbo"
LLM_MODEL_PATH_35 = MODELS_DIR / "llm" / "Qwen3.5-9B-Q4_K_M.gguf"
LLM_MODEL_PATH_25 = MODELS_DIR / "llm" / "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
LLM_MODEL_PATH = LLM_MODEL_PATH_35
_PROJECT_OLLAMA_DIR = ROOT / "tools" / "ollama"
OLLAMA_DIR = (LOCAL_APP_DIR / "ollama") if FROZEN else (
    _PROJECT_OLLAMA_DIR if (_PROJECT_OLLAMA_DIR / "ollama.exe").exists() else LOCAL_APP_DIR / "ollama"
)
OLLAMA_BIN = OLLAMA_DIR / "ollama.exe"
OLLAMA_MODELS_DIR = MODELS_DIR / "ollama"

_API_FIELDS = (
    "llm_provider",
    "api_base_url",
    "api_model",
    "api_key",
    "api_disable_thinking",
)

_GLOSSARY_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9'’\- ]{1,40}?)\s*"
    r"(?:=|->|→|统一翻译为|翻译成|翻译为|译为|译作|对应为|对应)\s*"
    r"[“\"'「]?([^;；,，。\n”\"'」]+)"
)


def parse_glossary(text: str) -> dict[str, str]:
    """Parse simple glossary lines such as ``Nen = 念`` from preset context."""
    pairs: dict[str, str] = {}
    for match in _GLOSSARY_RE.finditer(str(text or "")):
        key = match.group(1).strip(" \t'’\"“”")
        value = match.group(2).strip(" \t'’\"“”")
        if key and value:
            pairs[key] = value
    return pairs


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """Correct near-miss ASR names (NAN -> Nen) before translation."""
    text = str(text or "")
    if not text or not glossary:
        return text
    keys = [key for key in glossary if len(key) >= 3]
    if not keys:
        return text
    tokens = re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}", text)
    replacements: dict[str, str] = {}
    for token in tokens:
        lower = token.lower()
        if any(lower == key.lower() for key in keys):
            continue
        best_key = ""
        best_score = 0.0
        for key in keys:
            score = SequenceMatcher(None, lower, key.lower()).ratio()
            if score > best_score:
                best_key, best_score = key, score
        if best_key and best_score >= 0.66:
            replacements[token] = best_key
    for original, canonical in replacements.items():
        text = re.sub(rf"\b{re.escape(original)}\b", canonical, text)
    return text

def parse_vocabulary(text: str) -> list[tuple[str, str]]:
    """Parse the manual voice vocabulary.

    One entry per line:
      正确词
      错词=正确词
    Blank lines and lines starting with # are ignored.
    """
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            wrong, correct = line.split("=", 1)
        else:
            wrong = correct = line
        wrong = wrong.strip(" \t'\"“”")
        correct = correct.strip(" \t'\"“”")
        if not wrong or not correct or len(wrong) > 80 or len(correct) > 80:
            continue
        pair = (wrong, correct)
        if pair not in seen:
            seen.add(pair)
            entries.append(pair)
        if len(entries) >= 300:
            break
    return entries


def apply_vocabulary(text: str, entries: list[tuple[str, str]] | None = None) -> str:
    """Apply unambiguous exact vocabulary replacements after ASR."""
    value = str(text or "")
    if not value or not entries:
        return value
    # Longest first avoids a short alias eating part of a longer term.
    for wrong, correct in sorted(entries, key=lambda item: len(item[0]), reverse=True):
        if not wrong or wrong == correct:
            continue
        if re.fullmatch(r"[A-Za-z0-9 ._'’+-]+", wrong):
            value = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(wrong)}(?![A-Za-z0-9])",
                lambda _m, dst=correct: dst,
                value,
                flags=re.IGNORECASE,
            )
        else:
            value = value.replace(wrong, correct)
    return value


def vocabulary_terms(entries: list[tuple[str, str]] | None = None) -> list[str]:
    """Return unique canonical terms for the Whisper/LLM prompt."""
    terms: list[str] = []
    for _wrong, correct in entries or []:
        if correct and correct not in terms:
            terms.append(correct)
    return terms[:40]


DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "job": "",
    "city": "",
    "personality": "",
    "hobbies": "",
    "goal": "",
    "relationship": "",
    "other_person": "",
    "english_level": "intermediate",
    "tone": "friendly",
    "notes": "",
    "phrases": "",
    "key_facts": "",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "other_device": None,
    "mic_device": None,
    "mic_enabled": True,
    "asr_device": "auto",
    "asr_model": "auto",
    "partial_asr": True,
    "auto_suggest": True,
    "translate": True,
    "suggest_count": 2,
    "vad_aggressiveness": 2,
    "silence_ms": 750,
    "call_silence_ms": 750,
    "call_vocabulary": "",
    "call_auto_summary": True,
    "long_passage_delay_ms": 2800,
    "max_segment_s": 20,
    "mic_gain": 1.0,
    "llm_model": "qwen3.5-copilot",
    "local_disable_thinking": True,
    "llm_base_url": "http://127.0.0.1:11434",
    "llm_provider": "local",
    "app_mode": "call",
    "restore_last_mode": True,
    "start_with_windows": False,
    "hardware_auto_configure": True,
    "hardware_signature": "",
    "hardware_profile": "auto",
    "local_gpu_mode": "auto",
    "asr_language": "en",
    "call_asr_language": "",
    "translate_asr_language": "",
    "meeting_asr_language": "zh",
    "translate_target_language": "zh",
    "media_type": "series",
    "media_title": "",
    "media_context": "",
    "subtitle_style": "natural",
    "subtitle_silence_ms": 450,
    "overlay_enabled": True,
    "meeting_auto_notes": True,
    "meeting_notes_language": "zh",
    "meeting_context": "",
    "voice_hotkey": "ctrl+alt+space",
    "voice_hotkey_hold": False,
    "voice_target_language": "zh",
    "voice_input_language": "zh",
    "voice_max_seconds": 90,
    "voice_trigger_mode": "hotkey",
    "voice_overlay_enabled": True,
    "voice_no_overlay_insert": "translation",
    "voice_focus_skip_fullscreen": True,
    "voice_focus_titles": "DeepSeek Harness",
    "voice_focus_silence_ms": 1200,
    "voice_auto_punctuation": True,
    "voice_auto_proofread": True,
    "voice_vocabulary": "",
    "api_base_url": "https://api.deepseek.com",
    "api_model": "deepseek-flash",
    "api_key": "",
    "api_disable_thinking": True,
    "ui_language": "",
    "echo_guard": True,
    "save_transcript": True,
    "session_note": "",
}

_write_lock = threading.Lock()


def _deep_merge(default: dict[str, Any], value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(default)
    merged = dict(default)
    for key, val in value.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return _deep_merge(default, json.load(handle))
    except FileNotFoundError:
        return dict(default)
    except (OSError, ValueError):
        return dict(default)


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _write_lock:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)


def load_profile() -> dict[str, Any]:
    raw = local_profile.load_profile()
    clean = {key: str(raw.get(key, "") or "")[:4000] for key in DEFAULT_PROFILE}
    clean["english_level"] = str(raw.get("english_level", "intermediate") or "intermediate")[:40]
    clean["tone"] = str(raw.get("tone", "friendly") or "friendly")[:40]
    return clean


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    clean = {key: str(profile.get(key, "") or "")[:4000] for key in DEFAULT_PROFILE}
    clean["english_level"] = str(profile.get("english_level", "intermediate") or "intermediate")[:40]
    clean["tone"] = str(profile.get("tone", "friendly") or "friendly")[:40]
    local_profile.save_profile(clean)
    return clean


def _raw_settings_file() -> dict[str, Any]:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _strip_api_fields(settings: dict[str, Any]) -> dict[str, Any]:
    clean = dict(settings)
    for key in _API_FIELDS:
        clean.pop(key, None)
    return clean


def _apply_api_profile(settings: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict) or not profile:
        return settings
    settings["llm_provider"] = str(profile.get("provider", settings.get("llm_provider", "local")) or "local")
    settings["api_base_url"] = str(profile.get("api_base_url", settings.get("api_base_url", "")) or "")
    settings["api_model"] = str(profile.get("api_model", settings.get("api_model", "")) or "")
    settings["api_key"] = str(profile.get("api_key", "") or "")
    settings["api_disable_thinking"] = bool(profile.get("api_disable_thinking", True))
    return settings


def load_settings() -> dict[str, Any]:
    merged = load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
    raw_file = _raw_settings_file()
    profile = api_secrets.load_profile()
    legacy = any(key in raw_file for key in _API_FIELDS)
    # Never import credentials from the project's settings file.  A legacy
    # data/settings.json may have been copied from another computer; it is
    # stripped rather than decrypted/imported.  A key is only accepted from
    # the machine-local DPAPI profile written by this installation.
    if profile:
        _apply_api_profile(merged, profile)
    else:
        merged["llm_provider"] = "local"
        merged["api_key"] = ""
    if legacy:
        try:
            save_json(SETTINGS_PATH, _strip_api_fields(merged))
        except OSError:
            pass
    return merged


def public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings safe to send to the browser; never includes the API key."""
    clean = {key: value for key, value in settings.items() if key != "api_key"}
    clean["api_key_saved"] = api_secrets.has_key()
    clean["api_profile_local"] = True
    return clean


def clear_api_profile(settings: dict[str, Any]) -> dict[str, Any]:
    api_secrets.clear_profile()
    clean = dict(settings)
    clean["llm_provider"] = "local"
    clean["api_key"] = ""
    return save_settings(clean)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    clean = _deep_merge(DEFAULT_SETTINGS, settings)
    try:
        clean["suggest_count"] = max(1, min(4, int(clean.get("suggest_count", 3))))
        clean["vad_aggressiveness"] = max(0, min(3, int(clean.get("vad_aggressiveness", 2))))
        clean["silence_ms"] = max(300, min(1600, int(clean.get("silence_ms", 750))))
        clean["call_silence_ms"] = max(
            300, min(1600, int(clean.get("call_silence_ms", clean.get("silence_ms", 750))))
        )
        clean["call_vocabulary"] = str(clean.get("call_vocabulary", "") or "")[:8000]
        clean["call_auto_summary"] = bool(clean.get("call_auto_summary", True))
        clean["long_passage_delay_ms"] = max(
            800, min(6000, int(clean.get("long_passage_delay_ms", 2800)))
        )
        clean["max_segment_s"] = max(5, min(30, int(clean.get("max_segment_s", 20))))
        clean["mic_gain"] = max(0.5, min(4.0, float(clean.get("mic_gain", 1.0))))
        clean["api_base_url"] = str(clean.get("api_base_url", "") or "")[:500]
        clean["api_model"] = str(clean.get("api_model", "") or "")[:200]
        clean["api_key"] = str(clean.get("api_key", "") or "")[:500]
        clean["ui_language"] = str(clean.get("ui_language", "") or "").lower()
        if clean["ui_language"] not in ("zh", "en", "ja", "ko"):
            clean["ui_language"] = ""
        clean["llm_provider"] = str(clean.get("llm_provider", "local") or "local")
        clean["app_mode"] = str(clean.get("app_mode", "call") or "call")
        if clean["app_mode"] not in ("call", "translate", "voice", "meeting"):
            clean["app_mode"] = "call"
        clean["restore_last_mode"] = bool(clean.get("restore_last_mode", True))
        clean["start_with_windows"] = bool(clean.get("start_with_windows", False))
        clean["hardware_auto_configure"] = bool(clean.get("hardware_auto_configure", True))
        clean["hardware_signature"] = str(clean.get("hardware_signature", "") or "")
        clean["hardware_profile"] = str(clean.get("hardware_profile", "auto") or "auto")
        clean["local_gpu_mode"] = str(clean.get("local_gpu_mode", "auto") or "auto")
        if clean["local_gpu_mode"] not in ("auto", "nvidia", "amd_vulkan", "intel_vulkan", "cpu"):
            clean["local_gpu_mode"] = "auto"
        clean["asr_language"] = str(clean.get("asr_language", "en") or "en")
        if clean["asr_language"] not in ("en", "ja", "ko", "auto"):
            clean["asr_language"] = "en"
        for key in ("call_asr_language", "translate_asr_language", "meeting_asr_language"):
            value = str(clean.get(key, "") or "")
            if value and value not in ("zh", "en", "ja", "ko", "auto"):
                value = ""
            if key == "meeting_asr_language" and not value:
                value = "zh"
            clean[key] = value
        clean["translate_target_language"] = str(
            clean.get("translate_target_language", "zh") or "zh"
        )
        if clean["translate_target_language"] not in ("zh", "en", "ja", "ko"):
            clean["translate_target_language"] = "zh"
        clean["media_type"] = str(clean.get("media_type", "series") or "series")
        if clean["media_type"] not in ("series", "movie", "anime", "documentary", "other"):
            clean["media_type"] = "series"
        clean["media_title"] = str(clean.get("media_title", "") or "")[:300]
        clean["media_context"] = str(clean.get("media_context", "") or "")[:4000]
        clean["subtitle_style"] = str(clean.get("subtitle_style", "natural") or "natural")
        if clean["subtitle_style"] not in ("natural", "literal", "terminology"):
            clean["subtitle_style"] = "natural"
        clean["subtitle_silence_ms"] = max(250, min(1200, int(clean.get("subtitle_silence_ms", 450))))
        clean["overlay_enabled"] = bool(clean.get("overlay_enabled", True))
        clean["meeting_auto_notes"] = bool(clean.get("meeting_auto_notes", True))
        clean["meeting_notes_language"] = str(clean.get("meeting_notes_language", "zh") or "zh")
        if clean["meeting_notes_language"] not in ("zh", "en", "ja", "ko"):
            clean["meeting_notes_language"] = "zh"
        clean["meeting_context"] = str(clean.get("meeting_context", "") or "")[:4000]
        hotkey = str(clean.get("voice_hotkey", "ctrl+alt+space") or "ctrl+alt+space")
        if hotkey not in ("ctrl+alt+space", "ctrl+shift+space", "alt+space", "f9", "f10"):
            hotkey = "ctrl+alt+space"
        clean["voice_hotkey"] = hotkey
        clean["voice_hotkey_hold"] = bool(clean.get("voice_hotkey_hold", False))
        target = str(clean.get("voice_target_language", "zh") or "zh")
        if target not in ("none", "zh", "en", "ja", "ko"):
            target = "zh"
        clean["voice_target_language"] = target
        input_language = str(clean.get("voice_input_language", "zh") or "zh")
        if input_language not in ("zh", "yue", "en", "ja", "ko", "auto"):
            input_language = "zh"
        clean["voice_input_language"] = input_language
        clean["voice_max_seconds"] = max(15, min(180, int(clean.get("voice_max_seconds", 90))))
        trigger = str(clean.get("voice_trigger_mode", "hotkey") or "hotkey")
        if trigger not in ("hotkey", "focus", "both"):
            trigger = "hotkey"
        clean["voice_trigger_mode"] = trigger
        clean["voice_overlay_enabled"] = bool(clean.get("voice_overlay_enabled", True))
        insert_mode = str(clean.get("voice_no_overlay_insert", "translation") or "translation")
        if insert_mode not in ("original", "translation"):
            insert_mode = "translation"
        clean["voice_no_overlay_insert"] = insert_mode
        clean["voice_focus_skip_fullscreen"] = bool(clean.get("voice_focus_skip_fullscreen", True))
        clean["voice_focus_titles"] = str(
            clean.get("voice_focus_titles", "DeepSeek Harness") or "DeepSeek Harness"
        )[:300]
        clean["voice_focus_silence_ms"] = max(
            600, min(3000, int(clean.get("voice_focus_silence_ms", 1200)))
        )
        clean["voice_auto_punctuation"] = bool(clean.get("voice_auto_punctuation", True))
        clean["voice_auto_proofread"] = bool(clean.get("voice_auto_proofread", True))
        clean["voice_vocabulary"] = str(clean.get("voice_vocabulary", "") or "")[:8000]
        clean["api_disable_thinking"] = bool(clean.get("api_disable_thinking", True))
        clean["local_disable_thinking"] = bool(clean.get("local_disable_thinking", True))
        clean["echo_guard"] = bool(clean.get("echo_guard", True))
    except (TypeError, ValueError):
        pass
    existing_profile = api_secrets.load_profile()
    key = str(clean.get("api_key", "") or "").strip()
    if not key:
        key = str(existing_profile.get("api_key", "") or "").strip()
    profile = {
        "provider": str(clean.get("llm_provider", "local") or "local"),
        "api_base_url": str(clean.get("api_base_url", "") or "")[:500],
        "api_model": str(clean.get("api_model", "") or "")[:200],
        "api_key": key[:500],
        "api_disable_thinking": bool(clean.get("api_disable_thinking", True)),
    }
    if profile["api_key"] or profile["provider"] != "local" or existing_profile:
        try:
            api_secrets.save_profile(profile)
        except Exception as exc:
            # Never write the key to the project file as a fallback.  Keep it
            # in this process only; the next launch must ask for it again.
            print(f"[secrets] warning: could not save API profile: {exc}", flush=True)
    runtime = dict(clean)
    _apply_api_profile(runtime, profile)
    # API credentials are machine-local.  settings.json is intentionally
    # project-portable and must not contain provider credentials.
    save_json(SETTINGS_PATH, _strip_api_fields(clean))
    return runtime


def ensure_dirs() -> None:
    for directory in (DATA_DIR, SESSIONS_DIR, MODELS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

