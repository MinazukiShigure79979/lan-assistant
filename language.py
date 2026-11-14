"""UI language and per-feature default languages."""
from __future__ import annotations

import ctypes
import os
from typing import Any

SUPPORTED: dict[str, dict[str, str]] = {
    "zh": {"label": "中文", "html": "zh-CN"},
    "en": {"label": "English", "html": "en"},
    "ja": {"label": "日本語", "html": "ja"},
    "ko": {"label": "한국어", "html": "ko"},
}
FALLBACK = "en"


def normalize(value: object) -> str:
    code = str(value or "").strip().lower().replace("_", "-")
    if code.startswith("zh"):
        return "zh"
    if code.startswith("en"):
        return "en"
    if code.startswith("ja"):
        return "ja"
    if code.startswith("ko"):
        return "ko"
    return FALLBACK


def detect_system_language() -> str:
    """Windows UI language, restricted to the four supported UI packs."""
    if os.name != "nt":
        return FALLBACK
    langid = 0
    try:
        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        try:
            langid = int(ctypes.windll.kernel32.GetSystemDefaultUILanguage())
        except Exception:
            return FALLBACK
    primary = langid & 0x3FF
    if primary == 0x04:
        return "zh"
    if primary == 0x11:
        return "ja"
    if primary == 0x12:
        return "ko"
    if primary == 0x09:
        return "en"
    return FALLBACK


def defaults_for_ui(ui_language: object) -> dict[str, str]:
    """Per-feature defaults derived from the interface language.

    The call copilot remains English-first for Chinese/English UI because the
    product is primarily an English call assistant.  Japanese/Korean UI users
    default to their language for calls.  Translation source is automatic so a
    source language is never forced to equal the target.
    """
    lang = normalize(ui_language)
    call_language = "en" if lang in ("zh", "en") else lang
    return {
        "asr_language": "en" if lang in ("zh", "en") else lang,
        "call_asr_language": call_language,
        "translate_asr_language": "auto",
        "translate_target_language": lang,
        "voice_input_language": lang,
        "voice_target_language": "none",
        "meeting_asr_language": lang,
        "meeting_notes_language": lang,
    }


def apply_defaults(settings: dict[str, Any], ui_language: object) -> dict[str, Any]:
    updated = dict(settings)
    updated["ui_language"] = normalize(ui_language)
    updated.update(defaults_for_ui(updated["ui_language"]))
    return updated
