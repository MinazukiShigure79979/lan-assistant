"""Lan.Assistant local server: audio capture, ASR, LLM suggestions, and web UI."""
from __future__ import annotations

import argparse
import base64
import ctypes
import difflib
import io
import json
import mimetypes
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

# Make the console UTF-8 safe on Chinese Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
import audio_cue
import diarization
import hardware
import language
import models
import session_store
import setup_manager
import startup
import version
from audio_engine import AudioEngine, list_devices, resolve_devices
from brain import Brain, BrainError, fallback_suggestions
from transcriber import Transcriber
from voice_input import (
    GlobalHotkey,
    HoldHotkey,
    VoiceRecorder,
    get_focus_info,
    get_foreground_window,
    insert_text_into_window,
    is_editable_focus,
)

APP_NAME = "Lan.Assistant"
APP_VERSION = version.VERSION
APP_DISPLAY_VERSION = version.DISPLAY_VERSION
APP_WINDOW_TITLE_MARKER = "\u2063"
STATIC_DIR = config.RESOURCE_DIR / "static"
DEFAULT_PORT = 8888


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm(text: str) -> str:
    # Keep Unicode letters/digits; the old ASCII-only expression turned every
    # Chinese/Japanese/Korean final transcript into an empty string.
    return re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


_FILLERS = {
    "mm", "mmm", "mmmm", "mmmmm", "hmm", "hmmm", "hmmmm",
    "mhm", "mmhmm", "uh", "uhh", "um", "umm", "ah", "ahh",
    "eh", "oh", "ohh", "bleh", "blah", "huh",
}
_FILLER_BRACKETS = re.compile(r"^\s*[\[(（【].*[\])）】]\s*$")


def _is_filler_text(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    if _FILLER_BRACKETS.match(value):
        return True
    normalized = re.sub(r"[^a-z]+", "", value.lower())
    if normalized in _FILLERS:
        return True
    return bool(re.fullmatch(r"(?:m{2,}|h{2,}|u{1,3}|a{1,3}|e{1,2}h?|o{1,2}h?)", normalized))


def _auto_punctuate(text: str, language: str) -> str:
    """Lightweight punctuation cleanup after ASR.

    Whisper already emits punctuation most of the time; this guarantees a
    terminal mark and normalizes the most common Chinese ASCII punctuation.
    It deliberately does not rewrite clauses or invent commas.
    """
    value = str(text or "").strip()
    if not value:
        return value
    lang = str(language or "").lower()
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", value))
    if has_cjk:
        value = re.sub(r"(?<=[\u3400-\u9fff])\s*,\s*", "，", value)
        value = re.sub(r"(?<=[\u3400-\u9fff])\s*\?\s*", "？", value)
        value = re.sub(r"(?<=[\u3400-\u9fff])\s*!\s*", "！", value)
    terminal = "。！？!?…"
    if lang == "en":
        value = re.sub(r"\bi\b", "I", value)
        value = value[0].upper() + value[1:] if value else value
        terminal = ".!?…"
    if value[-1] not in terminal:
        if value[-1] in ",，、;；:":
            value = value[:-1]
        if lang in ("zh", "ja") or has_cjk:
            value += "。"
        elif lang == "ko":
            value += "."
        elif lang == "en" or re.match(r"^[A-Za-z]", value):
            value += "."
    return value.strip()


_SELF_INTRO_PATTERNS = [    r"\bintroduce\s+(yourself|urself)\b",
    r"\btell\s+(me|us)\s+(a\s+(little|bit)\s+)?about\s+(yourself|you)\b",
    r"\bwalk\s+(me|us)\s+through\s+your\s+(background|experience|career)\b",
    r"\b(tell|talk)\s+(me|us)\s+about\s+your\s+(background|experience|career)\b",
    r"\bwho\s+are\s+you\b",
    r"\bdescribe\s+yourself\b",
    r"\bgive\s+(me|us)\s+(a\s+)?(quick|brief|short)?\s*(intro|introduction)\b",
    r"\bsay\s+a\s+few\s+words\s+about\s+(yourself|you)\b",
    r"\bself[- ]introduction\b",
    r"\b(give|provide|prepare|make)\b.{0,55}\b(intro|introduction|overview|pitch)\b.{0,25}\b(yourself|your background|your experience|your career|about you)\b",
    r"\b(intro|introduction|overview|pitch)\b.{0,30}\b(yourself|your background|your experience|your career)\b",
    r"\b(professional|career)\s+journey\b",
    r"自我介绍",
    r"介绍(一下|下)?(你|您)自己",
    r"(说|讲|聊).{0,6}(背景|经历|职业)",
]


def _is_self_intro_request(text: str) -> bool:
    low = (text or "").lower()
    if any(re.search(pattern, low) for pattern in _SELF_INTRO_PATTERNS):
        return True
    # A length explicitly attached to an intro/background request is a strong
    # sign even when the phrasing does not match the short patterns above.
    if re.search(
        r"\b(intro|introduction|overview|pitch|background|experience|career|journey)\b",
        low,
    ) and re.search(
        r"\b(yourself|your|professional|career|work|job)\b",
        low,
    ):
        return True
    if _extract_intro_target(low) and re.search(
        r"\b(intro|introduction|overview|pitch|background|yourself)\b", low
    ):
        return True
    return False


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a couple of": 2,
    "a few": 3,
}
_NUM_PATTERN = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|a\s+couple\s+of|a\s+few)"


def _number_value(token: str) -> float:
    token = re.sub(r"\s+", " ", token.strip().lower())
    try:
        return float(token)
    except ValueError:
        return float(_NUMBER_WORDS.get(token, 0))


_CN_NUMBER_VALUES = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_number(token: str) -> float:
    token = token.strip()
    if not token:
        return 0
    if token.isdigit() or "." in token:
        return float(token)
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_NUMBER_VALUES.get(left, 1) if left else 1
        ones = _CN_NUMBER_VALUES.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    return float(_CN_NUMBER_VALUES.get(token, 0))


def _words_for_seconds(seconds: float) -> tuple[int, int]:
    # A relaxed non-native speaking pace: roughly 75 words per minute.
    center = max(24.0, min(450.0, seconds * 1.25))
    return max(20, int(round(center * 0.9))), max(24, int(round(center * 1.1)))


def _extract_intro_target(text: str) -> Optional[dict[str, Any]]:
    low = (text or "").lower()
    word_match = re.search(_NUM_PATTERN + r"\s*(?:-)?\s*words?\b", low)
    if word_match:
        center = max(40.0, min(420.0, _number_value(word_match.group(0).split()[0])))
        # Handle "a couple of words" barely; normal word counts are numeric.
        numeric = re.search(r"\d+(?:\.\d+)?", word_match.group(0))
        if numeric:
            center = max(40.0, min(420.0, float(numeric.group(0))))
        lo, hi = max(30, int(center * 0.9)), max(36, int(center * 1.1))
        return {
            "seconds": int(round(center / 1.25)),
            "words_min": lo,
            "words_max": hi,
            "label": f"约 {int(center)} 词",
        }
    # Ranges such as "2-3 minutes" or "two to three minutes" use the midpoint.
    range_match = re.search(
        r"(" + _NUM_PATTERN + r")\s*(?:-|–|to)\s*(" + _NUM_PATTERN + r")\s*(minutes?|mins?|seconds?|secs?)\b",
        low,
    )
    single_match = re.search(
        r"(" + _NUM_PATTERN + r")\s*(?:-)?\s*(minutes?|mins?|seconds?|secs?)\b",
        low,
    )
    match = range_match or single_match
    if not match:
        cn_duration = re.search(r"([零一二两三四五六七八九十\d]+)\s*(分钟|分|秒)", low)
        if cn_duration:
            amount = _cn_number(cn_duration.group(1))
            seconds = amount * 60 if cn_duration.group(2) in ("分钟", "分") else amount
            if amount > 0:
                seconds = max(15.0, min(600.0, seconds))
                lo, hi = _words_for_seconds(seconds)
                return {
                    "seconds": int(round(seconds)),
                    "words_min": lo,
                    "words_max": hi,
                    "label": f"约 {int(round(seconds))} 秒" if seconds < 90 else f"约 {seconds / 60:.1f} 分钟",
                }
        if re.search(r"\b(detailed|comprehensive|in[- ]depth|long|extended|thorough)\b", low):
            lo, hi = _words_for_seconds(105)
            return {
                "seconds": 105,
                "words_min": lo,
                "words_max": hi,
                "label": "详细介绍",
            }
        return None
    if range_match:
        first = _number_value(range_match.group(1))
        second = _number_value(range_match.group(2))
        amount = (first + second) / 2.0
        unit = range_match.group(3)
    else:
        amount = _number_value(single_match.group(1))
        unit = single_match.group(2)
    if amount <= 0:
        return None
    seconds = amount * 60 if unit.startswith("min") else amount
    seconds = max(15.0, min(600.0, seconds))
    lo, hi = _words_for_seconds(seconds)
    if seconds < 90:
        label = f"约 {int(round(seconds))} 秒"
    else:
        label = f"约 {seconds / 60:.1f} 分钟".rstrip("0").rstrip(".")
    return {
        "seconds": int(round(seconds)),
        "words_min": lo,
        "words_max": hi,
        "label": label,
    }


def _similar_text(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if len(a) < 12 or len(b) < 12:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "th": "Thai",
    "vi": "Vietnamese",
}


def _language_name(code: str) -> str:
    code = (code or "").lower()
    return _LANGUAGE_NAMES.get(code, code.upper() if code else "English")


def _stop_local_models(models: list[str]) -> None:
    """Unload project-local Ollama models (used when switching to an API)."""
    exe = config.OLLAMA_BIN
    if not exe.exists():
        return
    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    env["OLLAMA_MODELS"] = str(config.OLLAMA_MODELS_DIR)
    for model in models:
        try:
            subprocess.run(
                [str(exe), "stop", model],
                cwd=str(config.OLLAMA_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        except Exception:
            pass


_UNFINISHED_WORDS = {
    "and", "but", "because", "so", "or", "that", "which", "who", "whom", "whose",
    "when", "where", "if", "then", "than", "like", "well", "um", "uh", "hmm",
    "is", "are", "was", "were", "am", "be", "been", "being", "have", "has", "had",
    "can", "could", "will", "would", "should", "may", "might", "must", "do", "does",
    "did", "to", "for", "with", "about", "of", "in", "on", "at", "as", "my", "your",
    "our", "their", "the", "a", "an", "i", "we", "you", "they", "he", "she", "it",
    "actually", "basically", "honestly", "literally", "okay",
}


def _looks_unfinished(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.endswith(("...", "…", ",", "，", "、", "—", "-")):
        return True
    words = re.findall(r"[a-z']+", raw.lower())
    if not words:
        return False
    if len(words) >= 2 and words[-1] == words[-2]:
        return True
    return words[-1] in _UNFINISHED_WORDS


def _stable_partial_prefix(previous: str, current: str) -> str:
    """LocalAgreement-style stable prefix for live subtitles.

    Partial Whisper decodes revise the tail.  Only the common prefix of two
    consecutive decodes is considered stable; Latin prefixes are cut back to a
    word boundary so a word is never committed half-way.
    """
    prev = str(previous or "").strip()
    curr = str(current or "").strip()
    if not prev or not curr:
        return ""
    limit = min(len(prev), len(curr))
    index = 0
    while index < limit and prev[index] == curr[index]:
        index += 1
    prefix = curr[:index]
    if not prefix:
        return ""
    if re.search(r"[A-Za-z]", prefix):
        cut = prefix.rfind(" ")
        if cut > 0:
            prefix = prefix[:cut]
    return prefix.strip()


def _initial_partial_prefix(text: str) -> str:
    """Show a conservative first partial instead of waiting for two decodes."""
    value = str(text or "").strip()
    if not value:
        return ""
    if re.search(r"[A-Za-z]", value):
        words = value.split()
        if len(words) > 3:
            return " ".join(words[:-2]).strip()
        return value
    # CJK has no spaces; hold back the two most likely-to-change characters.
    return value[:-2].strip() if len(value) > 4 else value


class EventBus:
    def __init__(self) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


class App:
    def __init__(self) -> None:
        config.ensure_dirs()
        self.profile = config.load_profile()
        fresh_settings = not config.SETTINGS_PATH.exists()
        self.settings = config.load_settings()
        if not self.settings.get("ui_language"):
            detected_language = language.detect_system_language()
            self.settings["ui_language"] = detected_language
            if fresh_settings:
                self.settings.update(language.defaults_for_ui(detected_language))
            self.settings = config.save_settings(self.settings)
        self.hardware = hardware.detect()
        if self.settings.get("hardware_auto_configure", True):
            updated, detected, changed = hardware.apply_to_settings(self.settings)
            self.hardware = detected
            if changed:
                self.settings = config.save_settings(updated)
        normalized_asr = models.whisper_name(str(self.settings.get("asr_model", "auto") or "auto"))
        if normalized_asr != self.settings.get("asr_model"):
            self.settings = config.save_settings({**self.settings, "asr_model": normalized_asr})
        self.mode = (
            str(self.settings.get("app_mode", "call") or "call")
            if self.settings.get("restore_last_mode", True)
            else "call"
        )
        if self.mode not in ("call", "translate", "voice", "meeting"):
            self.mode = "call"
        self.bus = EventBus()
        self.lock = threading.RLock()
        self.turns: list[dict[str, Any]] = []
        self.suggestion: Optional[dict[str, Any]] = None
        self.continuation: Optional[dict[str, Any]] = None
        self.call_summary: Optional[dict[str, Any]] = None
        self._call_summary_gen = 0
        self.meeting_notes: Optional[dict[str, Any]] = None
        self._meeting_audio: list[dict[str, Any]] = []
        self._diarization_thread: Optional[threading.Thread] = None
        self.diarization_state: dict[str, Any] = {
            "status": "idle",
            "progress": 0,
            "message": "",
            "error": "",
            "speakers": 0,
            "turns": 0,
        }
        self.capture_running = False
        self.started_at: Optional[float] = None
        self.partial: dict[str, Optional[dict[str, Any]]] = {"other": None, "me": None}
        self._partial_prev: dict[str, tuple[int, str]] = {}
        self._partial_committed: dict[str, tuple[int, str]] = {}
        self._partial_translate_queue: queue.Queue = queue.Queue(maxsize=1)
        self._partial_translate_gen = 0
        self._partial_translate_last: tuple[int, str] = (0, "")
        self.levels = {"other": 0.0, "me": 0.0}
        self.active_seq = {"other": 0, "me": 0}
        self.asr_status = "loading"
        self.asr_message = "正在加载语音识别模型…"
        self.llm_status = "loading"
        self.llm_message = (
            "正在准备本地语言模型…"
            if self.settings.get("llm_provider", "local") == "local"
            else "正在检查 API 设置…"
        )
        self.capture_error = ""
        self.device_names = {"other": "", "me": ""}
        self.session_path: Optional[Path] = None
        self.server_port = DEFAULT_PORT
        self._turns_lock = threading.RLock()
        self._asr_queue: queue.Queue = queue.Queue()
        self._partial_queue: queue.Queue = queue.Queue(maxsize=2)
        self._final_pending = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self.startup_error = ""
        self.voice_history: list[dict[str, Any]] = self._load_voice_history()
        self._suggest_gen = 0
        self._debounce: Optional[threading.Timer] = None
        self._translation_timer: Optional[threading.Timer] = None
        self._continuation_timer: Optional[threading.Timer] = None
        self._continuation_gen = 0
        self._tts_proc: Optional[subprocess.Popen] = None
        self._last_final: dict[str, tuple[str, float]] = {}
        self._last_error_at = 0.0
        self._last_echo_warning = 0.0
        self._last_level_at = 0.0
        self._last_other_final_at = 0.0
        self._passage_mode = False
        self._closed = False
        self.overlay_proc: Optional[subprocess.Popen] = None
        self.voice_overlay_proc: Optional[subprocess.Popen] = None
        self.voice_recorder: Optional[VoiceRecorder] = None
        self._voice_lock = threading.RLock()
        self._voice_id = 0
        self._voice_timer: Optional[threading.Timer] = None
        self._voice_transcribe_timer: Optional[threading.Timer] = None
        self._voice_automatic = False
        self._voice_hold_active = False
        self._hold_release_pending = False
        self._focus_last_hwnd = 0
        self._focus_cooldown_until = 0.0
        self.voice_state: dict[str, Any] = {
            "status": "idle",
            "id": 0,
            "hotkey": str(self.settings.get("voice_hotkey", "ctrl+alt+space")),
            "hotkey_error": "",
            "target_language": str(self.settings.get("voice_target_language", "zh")),
            "original": "",
            "translated": "",
            "error": "",
            "started_at": None,
            "hold": False,
            "proofread": False,
        }
        self._hotkey_hold = bool(self.settings.get("voice_hotkey_hold", False))
        if self._hotkey_hold:
            self.voice_hotkey = HoldHotkey(
                str(self.settings.get("voice_hotkey", "ctrl+alt+space")),
                self._on_hold_press,
                self._on_hold_release,
            )
        else:
            self.voice_hotkey = GlobalHotkey(
                str(self.settings.get("voice_hotkey", "ctrl+alt+space")),
                self._on_voice_hotkey,
            )
        self._turn_seq = 0
        self._background_started = False

        self.transcriber = Transcriber(
            models.resolve_whisper(
                str(self.settings.get("asr_model", "auto") or "auto"),
                self.hardware,
            ),
            device=str(self.settings.get("asr_device", "auto")),
            on_status=lambda status, msg: self._set_status("asr", status, msg),
        )
        self.brain = Brain(
            self.settings,
            lambda: self.profile,
            on_status=lambda status, msg: self._set_status("llm", status, msg),
        )
        self.engine = AudioEngine(
            on_segment=self._on_segment,
            on_partial=self._on_partial,
            on_level=self._on_level,
            on_error=self._on_audio_error,
        )
        self.setup = setup_manager.SetupManager(self)

    # ------------------------------------------------------------------ startup
    def refresh_local_model_bindings(self) -> None:
        """Recreate ASR/LLM bindings after first-run model selection."""
        models.invalidate_cache()
        self.transcriber = Transcriber(
            models.resolve_whisper(
                str(self.settings.get("asr_model", "auto") or "auto"),
                self.hardware,
            ),
            device=str(self.settings.get("asr_device", "auto")),
            on_status=lambda status, msg: self._set_status("asr", status, msg),
        )
        self.brain = Brain(
            self.settings,
            lambda: self.profile,
            on_status=lambda status, msg: self._set_status("llm", status, msg),
        )

    def start_background(self) -> None:
        config.write_runtime_port(self.server_port)
        if getattr(self, "setup", None) is not None and self.setup.required:
            self.asr_status = "setup"
            self.asr_message = "首次启动：等待模型准备完成"
            self.llm_status = "setup"
            self.llm_message = "首次启动：安装 Ollama 并下载模型后启动"
            self._publish_status()
            return
        if self._background_started:
            return
        self._background_started = True
        for target, name in (
            (self._asr_worker, "asr-worker"),
            (self._partial_worker, "partial-worker"),
            (self._partial_translation_worker, "partial-translate"),
            (self._init_models, "model-init"),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._worker_threads.append(thread)
        threading.Thread(target=self.start_voice_hotkey, name="voice-hotkey-start", daemon=True).start()
        threading.Thread(target=self._focus_watch_loop, name="voice-focus-watch", daemon=True).start()
        threading.Thread(target=self.apply_startup_setting, name="startup-shortcut", daemon=True).start()
        if self.settings.get("voice_overlay_enabled", True):
            self.start_voice_overlay()
        if self.mode == "translate" and self.settings.get("overlay_enabled", True):
            self.start_overlay()

    def apply_startup_setting(self) -> tuple[bool, str]:
        enabled = bool(self.settings.get("start_with_windows", False))
        ok, error = startup.set_enabled(enabled)
        self.startup_error = "" if ok else error
        if not ok:
            print(f"[startup] {error}", flush=True)
        return ok, self.startup_error

    def _load_voice_history(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(config.VOICE_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original", "") or "").strip()
            if not original:
                continue
            rows.append(
                {
                    "id": str(item.get("id", "") or f"v{int(time.time() * 1000)}"),
                    "original": original[:4000],
                    "translated": str(item.get("translated", "") or "")[:4000],
                    "target_language": str(item.get("target_language", "") or ""),
                    "language": str(item.get("language", "") or ""),
                    "proofread": bool(item.get("proofread")),
                    "created_at": float(item.get("created_at", 0) or time.time()),
                }
            )
            if len(rows) >= 30:
                break
        return rows

    def _save_voice_history(self) -> None:
        try:
            config.save_json(config.VOICE_HISTORY_PATH, self.voice_history[:30])  # type: ignore[arg-type]
        except Exception:
            pass

    def record_voice_history(
        self,
        original: str,
        translated: str,
        target_language: str,
        language: str,
        proofread: bool,
    ) -> None:
        text = str(original or "").strip()
        if not text:
            return
        item = {
            "id": f"v{int(time.time() * 1000)}",
            "original": text[:4000],
            "translated": str(translated or "")[:4000],
            "target_language": str(target_language or ""),
            "language": str(language or ""),
            "proofread": bool(proofread),
            "created_at": time.time(),
        }
        with self._voice_lock:
            self.voice_history.insert(0, item)
            del self.voice_history[30:]
            snapshot = [dict(row) for row in self.voice_history]
        self._save_voice_history()
        self.bus.publish({"type": "voice_history", "data": snapshot})

    def clear_voice_history(self) -> None:
        with self._voice_lock:
            self.voice_history = []
        self._save_voice_history()
        self.bus.publish({"type": "voice_history", "data": []})

    # --------------------------------------------------------------- voice input
    def start_voice_hotkey(self) -> None:
        ok = self.voice_hotkey.start()
        if not ok:
            self.voice_state["hotkey_error"] = self.voice_hotkey.error
            self._publish_voice()
        else:
            self.voice_state["hotkey_error"] = ""
            self._publish_voice()

    def _on_voice_hotkey(self) -> None:
        threading.Thread(target=self.toggle_voice_input, name="voice-hotkey-action", daemon=True).start()

    def _on_hold_press(self) -> None:
        self._hold_release_pending = False
        threading.Thread(target=self._hold_start_worker, name="voice-hold-press", daemon=True).start()

    def _on_hold_release(self) -> None:
        self._hold_release_pending = True
        threading.Thread(target=self._hold_release_worker, name="voice-hold-release", daemon=True).start()

    def _hold_release_worker(self) -> None:
        with self._voice_lock:
            recording = self.voice_state.get("status") == "recording" and self._voice_hold_active
        if recording:
            self.stop_voice_input()

    def _hold_start_worker(self) -> None:
        result = self.start_voice_input(hold=True)
        if self._hold_release_pending and result.get("ok"):
            self.stop_voice_input()

    def rebind_voice_hotkey(self) -> None:
        try:
            self.voice_hotkey.stop()
        except Exception:
            pass
        if self._voice_hold_active:
            self.cancel_voice_input()
        self._hotkey_hold = bool(self.settings.get("voice_hotkey_hold", False))
        hotkey = str(self.settings.get("voice_hotkey", "ctrl+alt+space") or "ctrl+alt+space")
        if self._hotkey_hold:
            self.voice_hotkey = HoldHotkey(hotkey, self._on_hold_press, self._on_hold_release)
        else:
            self.voice_hotkey = GlobalHotkey(hotkey, self._on_voice_hotkey)
        ok = self.voice_hotkey.start()
        self.voice_state["hotkey"] = hotkey
        self.voice_state["hotkey_error"] = "" if ok else self.voice_hotkey.error
        self._publish_voice()

    def _focus_allow_titles(self) -> tuple[str, ...]:
        raw = str(self.settings.get("voice_focus_titles", "DeepSeek Harness") or "")
        normalized = raw.replace("，", ",").replace("；", ";").replace("\r", "\n")
        values: list[str] = []
        for part in re.split(r"[,;\n]+", normalized):
            token = part.strip()
            if token and token.lower() not in {value.lower() for value in values}:
                values.append(token[:200])
        return tuple(values)

    def _focus_watch_loop(self) -> None:
        """Auto-start one dictation when focus enters an editable control.

        This is deliberately conservative: it only runs in the voice workspace,
        skips fullscreen windows by default, and accepts only common editable
        control classes. Games that render their own chat UI simply won't match.
        """
        while not self._closed:
            time.sleep(0.35)
            try:
                trigger = str(self.settings.get("voice_trigger_mode", "hotkey") or "hotkey")
                if trigger not in ("focus", "both") or self.mode != "voice":
                    self._focus_last_hwnd = 0
                    continue
                status = str(self.voice_state.get("status", "idle") or "idle")
                with self._voice_lock:
                    recorder = self.voice_recorder
                if status == "recording" and recorder is not None and self._voice_automatic:
                    info = get_focus_info()
                    focused = is_editable_focus(
                        info,
                        bool(self.settings.get("voice_focus_skip_fullscreen", True)),
                        self._focus_allow_titles(),
                    )
                    if not focused:
                        if recorder.has_speech:
                            self.stop_voice_input()
                        else:
                            self.cancel_voice_input()
                        self._focus_last_hwnd = 0
                    elif recorder.has_speech:
                        silence_s = max(
                            0.6, min(3.0, int(self.settings.get("voice_focus_silence_ms", 1200)) / 1000.0)
                        )
                        if time.time() - recorder.last_voice_at >= silence_s:
                            self.stop_voice_input()
                    elif time.time() - recorder.started_at > 7.0:
                        self.cancel_voice_input()
                    continue
                if status == "error":
                    info = get_focus_info()
                    if not is_editable_focus(
                        info,
                        bool(self.settings.get("voice_focus_skip_fullscreen", True)),
                        self._focus_allow_titles(),
                    ):
                        self.cancel_voice_input()
                        self._focus_last_hwnd = 0
                    continue
                if status != "idle":
                    continue
                if time.time() < self._focus_cooldown_until:
                    continue
                info = get_focus_info()
                focused = is_editable_focus(
                    info,
                    bool(self.settings.get("voice_focus_skip_fullscreen", True)),
                    self._focus_allow_titles(),
                )
                title = str(info.get("title", "") or "")
                allow = self._focus_allow_titles()
                if "Lan.Assistant" in title and not any(token.lower() in title.lower() for token in allow):
                    focused = False
                if focused:
                    key = int(info.get("focus_hwnd", 0) or 0)
                    if key and key != self._focus_last_hwnd:
                        self._focus_last_hwnd = key
                        self.start_voice_input(automatic=True)
                else:
                    self._focus_last_hwnd = 0
            except Exception:
                pass

    def _publish_voice(self) -> None:
        self.bus.publish({"type": "voice_input", "data": self.voice_public_state()})

    def voice_public_state(self) -> dict[str, Any]:
        with self._voice_lock:
            data = dict(self.voice_state)
        data.pop("target_hwnd", None)
        return data

    def _set_voice_state(self, **changes: Any) -> None:
        with self._voice_lock:
            self.voice_state.update(changes)
        self._publish_voice()

    def toggle_voice_input(self) -> dict[str, Any]:
        with self._voice_lock:
            status = self.voice_state.get("status")
        if status == "recording":
            return self.stop_voice_input()
        if status in ("transcribing",):
            return {"ok": False, "error": "正在识别上一段语音，请稍候。"}
        if status == "starting":
            return {"ok": False, "error": "正在打开麦克风，请稍候。"}
        if status == "ready":
            return {"ok": False, "error": "请先处理浮窗里的文字（插入或取消）。"}
        return self.start_voice_input()

    def start_voice_input(self, automatic: bool = False, hold: bool = False) -> dict[str, Any]:
        if self.capture_running:
            self._set_voice_state(
                status="error",
                id=self._voice_id,
                error="请先结束当前通话/会议记录，再使用语音输入。",
                original="",
                translated="",
            )
            return {"ok": False, "error": "请先结束当前通话/会议记录。"}
        with self._voice_lock:
            if self.voice_state.get("status") == "recording":
                return {"ok": True, "state": self.voice_public_state()}
            if self.voice_state.get("status") in ("starting", "transcribing", "ready"):
                return {"ok": False, "error": "上一段语音输入还没有结束，请稍候。"}
            if self._voice_transcribe_timer is not None:
                self._voice_transcribe_timer.cancel()
                self._voice_transcribe_timer = None
            target = get_foreground_window()
            self._voice_id += 1
            voice_id = self._voice_id
        self._set_voice_state(status="starting", id=voice_id, error="")
        rec = VoiceRecorder({**self.settings, "mic_enabled": True})
        if not rec.start():
            self._voice_automatic = False
            self._voice_hold_active = False
            self._set_voice_state(
                status="error",
                id=voice_id,
                error=rec.error or "无法打开麦克风。",
                original="",
                translated="",
            )
            return {"ok": False, "error": rec.error}
        if self.capture_running:
            rec.cancel()
            self._voice_automatic = False
            self._voice_hold_active = False
            self._set_voice_state(
                status="error",
                id=voice_id,
                error="通话/会议已经开始，语音输入已取消。",
                original="",
                translated="",
            )
            return {"ok": False, "error": "通话/会议已经开始。"}
        with self._voice_lock:
            self.voice_recorder = rec
            self._voice_automatic = bool(automatic)
            self._voice_hold_active = bool(hold)
        if self.settings.get("voice_overlay_enabled", True):
            self.start_voice_overlay()
        self._set_voice_state(
            status="recording",
            id=voice_id,
            automatic=bool(automatic),
            hold=bool(hold),
            target_hwnd=target,
            hotkey=str(self.settings.get("voice_hotkey", "ctrl+alt+space")),
            target_language=str(self.settings.get("voice_target_language", "zh")),
            original="",
            translated="",
            error="",
            started_at=time.time(),
        )
        if self.settings.get("voice_cue_enabled", True):
            audio_cue.play_start()
        max_seconds = max(15, min(180, int(self.settings.get("voice_max_seconds", 90))))
        timer = threading.Timer(max_seconds, self.stop_voice_input)
        timer.daemon = True
        with self._voice_lock:
            self._voice_timer = timer
        timer.start()
        return {"ok": True, "state": self.voice_public_state()}

    def stop_voice_input(self) -> dict[str, Any]:
        with self._voice_lock:
            rec = self.voice_recorder
            status = self.voice_state.get("status")
            if rec is None or status != "recording":
                return {"ok": False, "error": "当前没有正在录音的语音输入。"}
            timer = self._voice_timer
            self._voice_timer = None
            voice_id = int(self.voice_state.get("id", 0) or 0)
        if timer:
            timer.cancel()
        pcm = rec.stop()
        with self._voice_lock:
            self.voice_recorder = None
        if pcm.size == 0:
            self._voice_hold_active = False
            self._set_voice_state(status="error", error="没有录到声音，请再试一次。")
            return {"ok": False, "error": "没有录到声音。"}
        if self.settings.get("voice_cue_enabled", True):
            audio_cue.play_stop()
        self._voice_hold_active = False
        self._focus_cooldown_until = time.time() + 2.5
        self._set_voice_state(status="transcribing", error="")
        threading.Thread(
            target=self._voice_transcribe_worker,
            args=(pcm, str(self.settings.get("voice_target_language", "zh")), voice_id),
            name="voice-transcribe",
            daemon=True,
        ).start()
        timeout = threading.Timer(60.0, lambda vid=voice_id: self._voice_transcribe_timeout(vid))
        timeout.daemon = True
        with self._voice_lock:
            if self._voice_transcribe_timer is not None:
                self._voice_transcribe_timer.cancel()
            self._voice_transcribe_timer = timeout
        timeout.start()
        return {"ok": True, "state": self.voice_public_state()}

    def _voice_transcribe_timeout(self, voice_id: int) -> None:
        with self._voice_lock:
            if int(self.voice_state.get("id", 0) or 0) != voice_id:
                return
            if self.voice_state.get("status") != "transcribing":
                return
            self._voice_id += 1
            self.voice_state["id"] = self._voice_id
        self._voice_hold_active = False
        self._set_voice_state(
            status="error",
            error="语音识别超时，请重试；如果反复出现，换用更小的 Whisper 模型或关闭其他占用显卡的程序。",
            original="",
            translated="",
        )

    def _voice_transcribe_worker(self, pcm: Any, target_language: str, voice_id: int = 0) -> None:
        try:
            with self._voice_lock:
                if voice_id and int(self.voice_state.get("id", 0) or 0) != voice_id:
                    return
            input_language = str(self.settings.get("voice_input_language", "zh") or "zh")
            vocab_entries = config.parse_vocabulary(str(self.settings.get("voice_vocabulary", "") or ""))
            result = self.transcriber.transcribe(
                pcm,
                partial=False,
                language=input_language,
                initial_prompt=self._voice_vocabulary_prompt(vocab_entries, input_language),
                fast=True,
            )
            with self._voice_lock:
                if voice_id and int(self.voice_state.get("id", 0) or 0) != voice_id:
                    return
            text = str(result.get("text", "") or "").strip()
            if not text:
                self._set_voice_state(status="error", error=result.get("error") or "没有识别出文字。")
                return
            detected = str(result.get("language", "") or input_language)
            if self.settings.get("voice_auto_punctuation", True):
                text = _auto_punctuate(text, detected)
            text = config.apply_vocabulary(text, vocab_entries)
            proofread = False
            if self.settings.get("voice_auto_proofread", True):
                try:
                    corrected = self.brain.proofread_text(
                        text,
                        detected,
                        vocabulary=str(self.settings.get("voice_vocabulary", "") or ""),
                    )
                    if corrected and corrected != text:
                        text = corrected
                        proofread = True
                except Exception:
                    proofread = False
            translated = ""
            if target_language in ("zh", "en", "ja", "ko") and target_language != detected:
                try:
                    translated = self.brain.translate_to(text, target_language)
                except Exception:
                    translated = ""
            with self._voice_lock:
                if voice_id and int(self.voice_state.get("id", 0) or 0) != voice_id:
                    return
            self._set_voice_state(
                status="ready",
                original=text,
                translated=translated,
                target_language=target_language,
                error="",
                proofread=proofread,
                asr_ms=int(result.get("latency_ms", 0) or 0),
            )
            self.record_voice_history(text, translated, target_language, detected, proofread)
            if not self.settings.get("voice_overlay_enabled", True):
                insert_mode = str(self.settings.get("voice_no_overlay_insert", "translation") or "translation")
                chosen = text if insert_mode == "original" else (translated or text)
                self.insert_voice_input(chosen)
        except Exception as exc:
            with self._voice_lock:
                if voice_id and int(self.voice_state.get("id", 0) or 0) != voice_id:
                    return
            self._set_voice_state(status="error", error=f"语音识别失败：{exc}")

    def insert_voice_input(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "没有可插入的文字。"}
        with self._voice_lock:
            hwnd = int(self.voice_state.get("target_hwnd", 0) or 0)
        ok, error = insert_text_into_window(hwnd, text)
        if not ok:
            self._set_voice_state(status="error", error=error)
            self._voice_automatic = False
            self._voice_hold_active = False
            return {"ok": False, "error": error}
        with self._voice_lock:
            if self._voice_transcribe_timer is not None:
                self._voice_transcribe_timer.cancel()
                self._voice_transcribe_timer = None
            self._voice_id += 1
            voice_id = self._voice_id
        self._voice_automatic = False
        self._voice_hold_active = False
        self._focus_cooldown_until = time.time() + 1.5
        self._set_voice_state(
            status="idle",
            id=voice_id,
            original="",
            translated="",
            error="",
            target_hwnd=0,
        )
        return {"ok": True}

    def cancel_voice_input(self) -> dict[str, Any]:
        with self._voice_lock:
            rec = self.voice_recorder
            timer = self._voice_timer
            transcribe_timer = self._voice_transcribe_timer
            self._voice_timer = None
            self._voice_transcribe_timer = None
            self._voice_id += 1
            voice_id = self._voice_id
        if timer:
            timer.cancel()
        if transcribe_timer:
            transcribe_timer.cancel()
        if rec:
            rec.cancel()
        with self._voice_lock:
            self.voice_recorder = None
        self._voice_automatic = False
        self._voice_hold_active = False
        self._focus_cooldown_until = time.time() + 1.5
        self._set_voice_state(
            status="idle",
            id=voice_id,
            original="",
            translated="",
            error="",
            target_hwnd=0,
        )
        return {"ok": True}

    def voice_overlay_running(self) -> bool:
        proc = self.voice_overlay_proc
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        self.voice_overlay_proc = None
        return False

    def _helper_command(self, script_name: str, frozen_flag: str) -> Optional[list[str]]:
        if getattr(sys, "frozen", False):
            return [sys.executable, frozen_flag]
        script = config.ROOT / script_name
        if not script.exists():
            return None
        pythonw = Path(sys.executable)
        if pythonw.name.lower() == "python.exe":
            candidate = pythonw.with_name("pythonw.exe")
            if candidate.exists():
                pythonw = candidate
        return [str(pythonw), str(script)]

    def start_voice_overlay(self) -> bool:
        if not self.settings.get("voice_overlay_enabled", True):
            return False
        if self.voice_overlay_running():
            return True
        _kill_stale_voice_overlays()
        command = self._helper_command("voice_overlay.py", "--voice-overlay")
        if command is None:
            return False
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["LAN_ASSISTANT_PORT"] = str(self.server_port)
        try:
            self.voice_overlay_proc = subprocess.Popen(
                command,
                cwd=str(config.ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            return True
        except Exception:
            self.voice_overlay_proc = None
            return False

    def stop_voice_overlay(self) -> None:
        proc = self.voice_overlay_proc
        self.voice_overlay_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def release_local_models(self, models_to_stop: Optional[list[str]] = None) -> None:
        """Unload local Ollama models in the background after switching to API."""
        targets = models_to_stop or [item["name"] for item in models.llm_catalog()]
        if not targets:
            targets = ["qwen3.5-copilot", "qwen-copilot"]
        threading.Thread(
            target=_stop_local_models,
            args=(targets,),
            name="release-local-models",
            daemon=True,
        ).start()

    def _init_models(self) -> None:
        try:
            self.transcriber.load()
            if self.transcriber.model is not None:
                self.transcriber.warmup()
            if self.brain.ensure_ready():
                self.brain.warmup()
            if self.settings.get("llm_provider") != "local":
                # A previous run may have left the local model resident.
                self.release_local_models()
        except Exception as exc:
            self._emit_error("init", f"初始化失败：{exc}")
            print(traceback.format_exc())

    def _set_status(self, scope: str, status: str, message: str) -> None:
        if scope == "asr":
            self.asr_status = status
            self.asr_message = message
        elif scope == "llm":
            self.llm_status = status
            self.llm_message = message
        print(f"[{scope}] {status}: {message}")
        self._publish_status()

    def _publish_status(self) -> None:
        self.bus.publish(
            {
                "type": "status",
                "data": {
                    "asr": {"status": self.asr_status, "message": self.asr_message},
                    "llm": {"status": self.llm_status, "message": self.llm_message},
                    "capture": {
                        "running": self.capture_running,
                        "started_at": self.started_at,
                        "error": self.capture_error,
                        "devices": self.device_names,
                    },
                },
            }
        )

    # ------------------------------------------------------------------- audio
    def _on_level(self, speaker: str, level: float) -> None:
        self.levels[speaker] = max(0.0, min(1.0, float(level)))
        now = time.monotonic()
        if now - self._last_level_at >= 0.12:
            self._last_level_at = now
            self.bus.publish({"type": "levels", "data": dict(self.levels)})

    def _on_audio_error(self, scope: str, message: str) -> None:
        self.capture_error = message
        self._emit_error("audio", message)
        self._publish_status()

    def _on_segment(self, speaker: str, pcm: Any, meta: dict[str, Any]) -> None:
        if not self.capture_running:
            return
        if speaker == "other":
            # The final decode has priority over speculative partials.
            self._final_pending.set()
        item = {
            "speaker": speaker,
            "pcm": pcm.copy(),
            "meta": dict(meta),
            "received_at": time.time(),
        }
        self._asr_queue.put(item)

    def _on_partial(self, speaker: str, pcm: Any, meta: dict[str, Any]) -> None:
        if not self.capture_running or not self.settings.get("partial_asr", True):
            return
        if speaker == "other" and self._final_pending.is_set():
            return
        # The segmenter increments seq when speech starts; learn it here so the
        # first partial result is not discarded as stale.
        self.active_seq[speaker] = max(
            int(self.active_seq.get(speaker, 0)),
            int(meta.get("seq", 0)),
        )
        try:
            self._partial_queue.put_nowait(
                {"speaker": speaker, "pcm": pcm.copy(), "meta": dict(meta), "received_at": time.time()}
            )
        except queue.Full:
            pass

    def _correct_media_terms(self, text: str) -> str:
        if self.mode != "translate":
            return text
        context = str(self.settings.get("media_context", "") or "")
        glossary = config.parse_glossary(context)
        return config.apply_glossary(text, glossary) if glossary else text

    def _asr_language(self) -> str:
        key = f"{self.mode}_asr_language"
        value = str(self.settings.get(key, "") or "")
        if value in ("zh", "en", "ja", "ko", "auto"):
            return value
        if self.mode == "meeting":
            return "zh"
        if self.mode == "translate":
            # Subtitle mode defaults to all-language auto detection.  An empty
            # stored value must not silently fall back to English and strip
            # Chinese/Japanese/Korean text.
            return "auto"
        fallback = str(self.settings.get("asr_language", "en") or "en")
        return fallback if fallback in ("zh", "en", "ja", "ko", "auto") else "en"

    def _initial_prompt(self, language: str = "en") -> str:
        if self.mode == "translate":
            title = str(self.settings.get("media_title", "") or "").strip()
            context = str(self.settings.get("media_context", "") or "").strip()
            bits = [b for b in (title, context[:220]) if b]
            return ". ".join(bits)[:350]
        if language != "en":
            return ""
        p = self.profile
        bits = []
        for key in ("name", "job", "hobbies"):
            val = str(p.get(key, "") or "").strip()
            if val:
                bits.append(val[:120])
        other = str(p.get("other_person", "") or "").strip()
        if other:
            bits.append(other[:100])
        tail = "Natural conversational English on a live voice call."
        if bits:
            return ("Context: " + "; ".join(bits) + ". " + tail)[:700]
        return tail

    def _voice_vocabulary_prompt(
        self, entries: list[tuple[str, str]], language: str
    ) -> str:
        terms = config.vocabulary_terms(entries)
        if not terms:
            return ""
        prefix = "常用词" if language in ("zh", "yue") else "Vocabulary"
        return (prefix + ": " + ", ".join(terms))[:320]

    def _asr_prompt(self, language: str) -> str:
        prompt = self._initial_prompt(language)
        if self.mode == "call":
            entries = config.parse_vocabulary(str(self.settings.get("call_vocabulary", "") or ""))
            terms = config.vocabulary_terms(entries)
            if terms:
                prompt = (prompt + " Call terms: " + ", ".join(terms)).strip()
        return prompt[:900]

    def _asr_worker(self) -> None:
        while not self._closed:
            try:
                item = self._asr_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if item.get("speaker") == "other":
                self._final_pending.set()
            if not self.transcriber.model:
                self._final_pending.clear()
                if self.asr_status == "error":
                    self._emit_error("asr", self.transcriber.error or "语音识别模型未就绪")
                continue
            try:
                result = self.transcriber.transcribe(
                    item["pcm"],
                    partial=False,
                    initial_prompt=self._asr_prompt(self._asr_language()),
                    language=self._asr_language(),
                    fast=(self.mode == "translate"),
                )
                if result.get("text"):
                    result["text"] = self._correct_media_terms(str(result["text"]))
                    if self.mode == "call":
                        entries = config.parse_vocabulary(
                            str(self.settings.get("call_vocabulary", "") or "")
                        )
                        result["text"] = config.apply_vocabulary(str(result["text"]), entries)
                if result.get("error"):
                    self._final_pending.clear()
                    self._emit_error("asr", str(result["error"]))
                    continue
                text = str(result.get("text", "") or "").strip()
                if not text:
                    self._final_pending.clear()
                    continue
                self._handle_final(item["speaker"], text, item, result)
            except Exception as exc:
                self._final_pending.clear()
                # A malformed glossary/settings value or transient decoder
                # failure must not kill the only ASR consumer thread.
                self._emit_error("asr", f"识别处理失败：{exc}")
                continue

    def _partial_worker(self) -> None:
        while not self._closed:
            try:
                item = self._partial_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if not self.transcriber.model:
                continue
            if self._final_pending.is_set():
                continue
            speaker = item["speaker"]
            seq = int(item["meta"].get("seq", 0))
            if seq != self.active_seq.get(speaker, 0) or not self.capture_running:
                continue
            result = self.transcriber.transcribe(
                item["pcm"],
                partial=True,
                initial_prompt=self._asr_prompt(self._asr_language()),
                language=self._asr_language(),
            )
            text = str(result.get("text", "") or "").strip()
            if not text:
                continue
            if self._final_pending.is_set() and speaker == "other":
                continue
            if seq != self.active_seq.get(speaker, 0) or not self.capture_running:
                continue
            prev_seq, prev_text = self._partial_prev.get(speaker, (0, ""))
            if prev_seq != seq:
                prev_text = ""
            committed_seq, committed = self._partial_committed.get(speaker, (0, ""))
            if prev_text:
                candidate = _stable_partial_prefix(prev_text, text)
            else:
                candidate = _initial_partial_prefix(text)
            if committed_seq == seq and committed and candidate.startswith(committed):
                stable = candidate
            elif committed_seq == seq and committed:
                stable = committed
            else:
                stable = candidate
            self._partial_prev[speaker] = (seq, text)
            self._partial_committed[speaker] = (seq, stable)
            previous_partial = self.partial.get(speaker) or {}
            previous_zh = ""
            previous_zh_for = ""
            if previous_partial.get("seq") == seq:
                previous_zh = str(previous_partial.get("zh", "") or "")
                previous_zh_for = str(previous_partial.get("zh_for", "") or "")
            self.partial[speaker] = {
                "speaker": speaker,
                "text": text,
                "stable": stable,
                "seq": seq,
                "ts": time.time(),
                "zh": previous_zh,
                "zh_for": previous_zh_for,
            }
            self.bus.publish({"type": "partial", "data": self.partial[speaker]})
            if speaker == "other":
                self._queue_partial_translation(seq, stable)
            if speaker == "me":
                # A manual “我卡住了” hint must survive the user starting to
                # speak; it is exactly what they are trying to use.
                self._cancel_continuation(clear=True, keep_manual=True)

    # ------------------------------------------------------------------ turns
    def _handle_final(self, speaker: str, text: str, item: dict[str, Any], result: dict[str, Any]) -> None:
        if speaker == "other":
            self._final_pending.clear()
        text = text.strip()
        if _is_filler_text(text):
            return
        if len(_norm(text)) < 1:
            return
        now = time.time()
        if speaker == "me" and self._is_echo_of_other(text, now):
            self._warn_echo_once(now)
            return
        last_text, last_at = self._last_final.get(speaker, ("", 0.0))
        if _norm(text) == _norm(last_text) and now - last_at < 5.0:
            return
        self._last_final[speaker] = (text, now)
        if speaker == "me" and self._merge_me_fragment(text, item, result, now):
            return
        with self._turns_lock:
            self._turn_seq += 1
            turn = {
                "id": f"t{self._turn_seq}",
                "speaker": speaker,
                "en": text,
                "zh": "",
                "ts": now,
                "duration_ms": int(result.get("audio_ms", 0) or item["meta"].get("duration_ms", 0) or 0),
                "asr_ms": int(result.get("latency_ms", 0) or 0),
                "language": str(result.get("language", "") or ""),
                "status": "final",
                "updated_at": now,
                "fragments": 1,
            }
            self.turns.append(turn)
            self.partial[speaker] = None
            self._partial_prev.pop(speaker, None)
            self._partial_committed.pop(speaker, None)
            self.active_seq[speaker] = int(item["meta"].get("seq", 0)) + 1
            self.bus.publish({"type": "turn", "data": turn})
            self._save_turn(turn)
        if (
            speaker == "other"
            and self.mode == "meeting"
            and self.settings.get("diarization_enabled", False)
        ):
            try:
                pcm = item.get("pcm")
                if pcm is not None:
                    with self._turns_lock:
                        self._meeting_audio.append(
                            {
                                "turn_id": turn["id"],
                                "pcm": pcm.copy(),
                                "started_at": float(item["meta"].get("started_at", now) or now),
                            }
                        )
            except Exception:
                pass
        if speaker == "other":
            self._invalidate_partial_translation()
            self._cancel_continuation(clear=True)
            # The microphone often transcribes the speaker output a fraction of
            # a second before the loopback copy.  Remove those duplicates when
            # the authoritative loopback turn arrives.
            self._remove_mic_echoes_of(turn)
            if self.mode == "translate":
                with self.lock:
                    self._suggest_gen += 1
                self._schedule_translation_batch(
                    max(0.0, min(1.0, int(self.settings.get("translate_delay_ms", 80)) / 1000.0))
                )
                return
            if self.mode == "meeting":
                if self.settings.get("translate", True):
                    self._schedule_translation_batch(0.4)
                return
            gap = now - self._last_other_final_at if self._last_other_final_at else 999.0
            duration_s = float(turn.get("duration_ms", 0) or 0) / 1000.0
            self._passage_mode = bool(gap <= 4.0 or duration_s >= 8.0)
            self._last_other_final_at = now
            with self.lock:
                self._suggest_gen += 1
            delay_s = max(0.8, min(6.0, int(self.settings.get("long_passage_delay_ms", 2800)) / 1000.0))
            self._schedule_suggestion(delay_s if self._passage_mode else 0.85)
            if not self.settings.get("auto_suggest", True) and self.settings.get("translate", True):
                self._schedule_translation_batch(1.2)
        else:
            # The user has started answering: keep a manually requested
            # “我卡住了” hint visible while they speak; only auto hints are
            # stale once the user starts a new sentence.
            kept = self._cancel_continuation(clear=True, keep_manual=True)
            if not kept and self.mode == "call" and _looks_unfinished(text):
                self._schedule_continuation(1.15)

    def _merge_me_fragment(
        self, text: str, item: dict[str, Any], result: dict[str, Any], now: float
    ) -> bool:
        """Merge closely spaced 'me' ASR fragments into one answer block.

        The microphone VAD intentionally cuts at short pauses.  In call mode,
        those cuts should not turn one answer into eight independent turns.
        """
        if self.mode != "call" or not self.capture_running:
            return False
        with self._turns_lock:
            if not self.turns:
                return False
            last = self.turns[-1]
            if last.get("speaker") != "me" or last.get("edited"):
                return False
            last_at = float(last.get("updated_at", last.get("ts", 0)) or 0)
            if now - last_at > 3.5:
                return False
            previous = str(last.get("en", "") or "").strip()
            if not previous:
                return False
            last["en"] = (previous + " " + text).strip()
            last["duration_ms"] = int(last.get("duration_ms", 0) or 0) + int(
                result.get("audio_ms", 0) or item["meta"].get("duration_ms", 0) or 0
            )
            last["asr_ms"] = int(last.get("asr_ms", 0) or 0) + int(result.get("latency_ms", 0) or 0)
            last["updated_at"] = now
            last["fragments"] = int(last.get("fragments", 1) or 1) + 1
            self.partial["me"] = None
            self._partial_prev.pop("me", None)
            self._partial_committed.pop("me", None)
            self.active_seq["me"] = int(item["meta"].get("seq", 0)) + 1
            snapshot = dict(last)
        self.bus.publish({"type": "turn_update", "data": snapshot})
        self._save_turn(snapshot)
        self._cancel_continuation(clear=True, keep_manual=True)
        return True

    def _is_echo_of_other(self, text: str, now: float) -> bool:
        with self._turns_lock:
            others = [
                t for t in self.turns
                if t.get("speaker") == "other" and now - float(t.get("ts", 0)) < 6.0
            ]
        return any(_similar_text(text, str(t.get("en", ""))) >= 0.72 for t in others)

    def _remove_mic_echoes_of(self, other_turn: dict[str, Any]) -> None:
        removed: list[str] = []
        with self._turns_lock:
            for turn in list(self.turns):
                if turn.get("speaker") != "me":
                    continue
                if abs(float(turn.get("ts", 0)) - float(other_turn.get("ts", 0))) > 6.0:
                    continue
                if _similar_text(str(turn.get("en", "")), str(other_turn.get("en", ""))) >= 0.72:
                    self.turns.remove(turn)
                    removed.append(str(turn.get("id")))
        for turn_id in removed:
            self.bus.publish({"type": "turn_remove", "data": {"id": turn_id}})
        if removed:
            self._warn_echo_once(time.time())

    def _warn_echo_once(self, now: float) -> None:
        if now - self._last_echo_warning < 90:
            return
        self._last_echo_warning = now
        self._emit_error(
            "audio",
            "已自动过滤麦克风里的对方声音（回声）。如果仍经常出现，戴耳机或在设置中关闭麦克风采集会更稳。",
        )

    def _turns_for_llm(
        self,
        exclude_last_other: bool = False,
        exclude_ids: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        with self._turns_lock:
            rows = [dict(t) for t in self.turns]
        if exclude_ids:
            rows = [row for row in rows if row.get("id") not in exclude_ids]
        if exclude_last_other:
            for index in range(len(rows) - 1, -1, -1):
                if rows[index].get("speaker") == "other":
                    rows.pop(index)
                    break
        return rows

    def _last_other(self) -> Optional[dict[str, Any]]:
        with self._turns_lock:
            for turn in reversed(self.turns):
                if turn.get("speaker") == "other":
                    return dict(turn)
        return None

    def _last_me(self) -> Optional[dict[str, Any]]:
        with self._turns_lock:
            for turn in reversed(self.turns):
                if turn.get("speaker") == "me":
                    return dict(turn)
        return None

    def _cancel_continuation(self, clear: bool = False, keep_manual: bool = False) -> bool:
        """Cancel pending auto continuation work.

        Returns True when a manual continuation was deliberately preserved.
        Auto continuations are stale as soon as the user resumes speaking, but
        a manual “我卡住了” panel is the thing the user is actively using.
        """
        with self.lock:
            manual = bool(
                keep_manual
                and self.continuation is not None
                and not self.continuation.get("auto")
            )
            if manual:
                return True
            self._continuation_gen += 1
            if self._continuation_timer is not None:
                self._continuation_timer.cancel()
                self._continuation_timer = None
            had = self.continuation is not None
            if clear:
                self.continuation = None
        if clear and had:
            self.bus.publish({"type": "continuation", "data": None})
        return False

    def _schedule_continuation(self, delay: float = 1.15) -> None:
        if self.mode != "call" or not self.capture_running:
            return
        with self.lock:
            if self._continuation_timer is not None:
                self._continuation_timer.cancel()
            self._continuation_timer = threading.Timer(
                delay, lambda: self.generate_continuation(auto=True)
            )
            self._continuation_timer.daemon = True
            self._continuation_timer.start()

    def generate_continuation(
        self, auto: bool = False, fragment: str = "", manual: bool = False
    ) -> dict[str, Any]:
        if self.mode != "call":
            return {"ok": False, "error": "只有通话副驾模式需要续接建议"}
        latest_me = self._last_me()
        partial_me = self.partial.get("me") or {}
        fragment = (fragment or "").strip()
        if not fragment:
            fragment = str(partial_me.get("text", "") or "").strip()
        if not fragment and latest_me:
            fragment = str(latest_me.get("en", "") or "").strip()
        if not fragment:
            return {"ok": False, "error": "还没有听到你说话"}
        with self.lock:
            self._continuation_gen += 1
            gen = self._continuation_gen
            if self._continuation_timer is not None:
                self._continuation_timer.cancel()
                self._continuation_timer = None
            self.continuation = {
                "status": "thinking",
                "fragment": fragment,
                "options": [],
                "created_at": time.time(),
                "auto": auto,
            }
        self.bus.publish({"type": "continuation", "data": self.continuation})
        with self._turns_lock:
            history = [dict(t) for t in self.turns[-6:]]
        language = ""
        if latest_me:
            language = str(latest_me.get("language", "") or "")
        if not language or language == "auto":
            fallback = self._asr_language()
            language = fallback if fallback in ("zh", "en", "ja", "ko") else "en"
        count = max(2, min(3, int(self.settings.get("suggest_count", 2))))
        try:
            result = self.brain.continue_text(
                fragment,
                history,
                reply_language=_language_name(language),
                count=count,
            )
        except Exception as exc:
            if gen == self._continuation_gen:
                self.continuation = {
                    "status": "error",
                    "fragment": fragment,
                    "options": [],
                    "error": str(exc),
                    "created_at": time.time(),
                    "auto": auto,
                }
                self.bus.publish({"type": "continuation", "data": self.continuation})
            return {"ok": False, "error": str(exc)}
        if gen != self._continuation_gen:
            return {"ok": False, "error": "cancelled"}
        self.continuation = {
            "status": "ready",
            "fragment": fragment,
            "options": result.get("options", []),
            "latency_ms": result.get("latency_ms", 0),
            "model": result.get("model", ""),
            "created_at": time.time(),
            "auto": auto,
        }
        self.bus.publish({"type": "continuation", "data": self.continuation})
        return {"ok": True, "continuation": self.continuation}

    def _schedule_suggestion(self, delay: float = 0.85) -> None:
        if not self.settings.get("auto_suggest", True):
            return
        with self.lock:
            if self._debounce is not None:
                self._debounce.cancel()
            self._debounce = threading.Timer(delay, lambda: self.generate_suggestion(auto=True))
            self._debounce.daemon = True
            self._debounce.start()

    def _suggestion_base(
        self,
        for_turn: Optional[str],
        opening: bool,
        long_passage: bool = False,
        self_intro: bool = False,
        merged_turns: int = 1,
    ) -> dict[str, Any]:
        return {
            "id": f"s{int(time.time() * 1000)}",
            "for_turn": for_turn,
            "status": "thinking",
            "translation": "",
            "options": [],
            "note": "",
            "opening": opening,
            "long_passage": bool(long_passage),
            "self_intro": bool(self_intro),
            "merged_turns": int(merged_turns),
            "started_at": time.time(),
            "created_at": None,
            "latency_ms": 0,
            "error": "",
            "fallback": False,
        }

    def generate_suggestion(self, auto: bool = False, opening: bool = False, manual: bool = False) -> None:
        if self.mode != "call" or not self.capture_running:
            return
        now = time.time()
        with self.lock:
            if manual:
                self._suggest_gen += 1
            gen = self._suggest_gen
            previous = self.suggestion or {}
            previous_options = [str(o.get("en", "")) for o in previous.get("options", []) if o.get("en")] if manual else []
        latest_turn = None if opening else self._last_other()
        if not opening and latest_turn is None:
            return
        if self.settings.get("auto_suggest", True) is False and auto:
            return

        collected: list[dict[str, Any]] = []
        if latest_turn is not None:
            with self._turns_lock:
                rows = [dict(t) for t in self.turns]
            if rows and rows[-1].get("speaker") == "other":
                for row in reversed(rows):
                    if row.get("speaker") != "other":
                        break
                    if now - float(row.get("ts", 0)) > 20.0:
                        break
                    collected.append(row)
                    if len(collected) >= 5:
                        break
                collected.reverse()
        if not collected and latest_turn is not None:
            collected = [latest_turn]

        latest = " ".join(str(t.get("en", "") or "").strip() for t in collected).strip()
        latest_language = ""
        if collected:
            latest_language = str(collected[-1].get("language", "") or "")
        if not latest_language or latest_language == "auto":
            fallback = self._asr_language()
            latest_language = fallback if fallback in ("zh", "en", "ja", "ko") else "en"
        reply_language = _language_name(latest_language)
        self_intro = _is_self_intro_request(latest)
        intro_target = _extract_intro_target(latest) if self_intro else None
        if self_intro and not intro_target:
            # A bare "introduce yourself" used to fall back to a generic 12–32
            # word reply.  Default to a speakable one-minute intro unless the
            # interviewer explicitly asked for something brief.
            brief = bool(re.search(r"\b(brief|briefly|short|quickly|quick)\b", latest.lower()))
            seconds = 45 if brief else 90
            lo, hi = _words_for_seconds(seconds)
            intro_target = {
                "seconds": seconds,
                "words_min": lo,
                "words_max": hi,
                "label": f"约 {seconds} 秒",
            }
        total_duration = sum(int(t.get("duration_ms", 0) or 0) for t in collected)
        long_passage = bool(len(collected) > 1 or len(latest) > 240 or total_duration >= 8000)
        for_turn = str(collected[-1].get("id")) if collected else None
        exclude_ids = {str(t.get("id")) for t in collected}
        history = self._turns_for_llm(exclude_ids=exclude_ids)
        count = int(self.settings.get("suggest_count", 2))

        base = self._suggestion_base(
            for_turn,
            opening or not latest,
            long_passage=long_passage,
            self_intro=self_intro,
            merged_turns=len(collected),
        )
        if intro_target:
            base["intro_seconds"] = intro_target.get("seconds")
            base["intro_words_min"] = intro_target.get("words_min")
            base["intro_words_max"] = intro_target.get("words_max")
            base["intro_target_label"] = intro_target.get("label")
        self.suggestion = base
        self.bus.publish({"type": "suggestion", "data": base})

        try:
            if not self.brain.ensure_ready():
                raise BrainError(self.brain.last_error or "语言模型未就绪")

            if gen != self._suggest_gen:
                return
            result = self.brain.suggest(
                history=history,
                latest=latest,
                count=count,
                opening=opening or not latest,
                nonce="manual" if manual else "",
                avoid=previous_options,
                self_intro=self_intro,
                long_passage=long_passage,
                reply_language=reply_language,
                intro_words=(
                    (
                        int(intro_target.get("words_min", 45)),
                        int(intro_target.get("words_max", 90)),
                    )
                    if intro_target
                    else None
                ),
                intro_seconds=int(intro_target.get("seconds", 0)) if intro_target else None,
            )
            if gen != self._suggest_gen:
                return
            if not self.capture_running:
                return
            payload = dict(base)
            payload["reply_language"] = reply_language
            payload.update(
                {
                    "status": "ready",
                    "options": result.get("options", []),
                    "note": result.get("note", ""),
                    "model": result.get("model", ""),
                    "latency_ms": result.get("latency_ms", 0),
                    "created_at": time.time(),
                    "fallback": bool(result.get("fallback")),
                }
            )
            self.suggestion = payload
            self.bus.publish({"type": "suggestion", "data": payload})
            if latest and latest_turn and self.settings.get("translate", True):
                self._schedule_translation_batch(0.5)
        except Exception as exc:
            if gen != self._suggest_gen:
                return
            if not self.capture_running:
                return
            fb = fallback_suggestions(latest, count)
            payload = dict(base)
            payload.update(
                {
                    "status": "ready",
                    "options": fb["options"],
                    "note": fb["note"],
                    "error": str(exc),
                    "fallback": True,
                    "created_at": time.time(),
                    "latency_ms": 0,
                }
            )
            self.suggestion = payload
            self.bus.publish({"type": "suggestion", "data": payload})
            self._emit_error("llm", f"回答建议生成失败：{exc}")
            if latest and latest_turn and self.settings.get("translate", True):
                self._schedule_translation_batch(0.5)

    def _schedule_translation_batch(self, delay: float = 0.6) -> None:
        if self.mode != "translate" and self.settings.get("translate", True) is False:
            return
        with self.lock:
            if self._translation_timer is not None:
                self._translation_timer.cancel()
            self._translation_timer = threading.Timer(delay, self._translate_pending)
            self._translation_timer.daemon = True
            self._translation_timer.start()

    def _subtitle_dialogue_context(self, before_turn_id: str = "", limit: int = 6) -> str:
        """Previous subtitle lines, used only to keep the translation coherent."""
        limit = max(0, min(12, int(limit)))
        if limit <= 0:
            return ""
        with self._turns_lock:
            rows = [
                dict(turn) for turn in self.turns
                if turn.get("speaker") == "other" and str(turn.get("en", "") or "").strip()
            ]
        if before_turn_id:
            for index, row in enumerate(rows):
                if str(row.get("id", "")) == str(before_turn_id):
                    rows = rows[:index]
                    break
        rows = rows[-limit:]
        if not rows:
            return ""
        lines = [
            "RECENT SUBTITLE DIALOGUE (context only; do not translate, quote or continue it):",
            "Use it only to keep names, pronouns, terminology and conversational flow consistent.",
        ]
        for index, row in enumerate(rows, 1):
            en = str(row.get("en", "") or "").strip()[:450]
            translated = str(row.get("zh", "") or "").strip()[:450]
            if not en:
                continue
            lines.append(f"[{index}] SOURCE: {en}")
            if translated:
                lines.append(f"     TRANSLATION: {translated}")
        return "\n".join(lines) if len(lines) > 2 else ""

    def _invalidate_partial_translation(self) -> None:
        with self.lock:
            self._partial_translate_gen += 1
            self._partial_translate_last = (0, "")
        while True:
            try:
                self._partial_translate_queue.get_nowait()
            except queue.Empty:
                break

    def _queue_partial_translation(self, seq: int, text: str) -> None:
        if self.mode != "translate" or not self.settings.get("translate", True):
            return
        value = str(text or "").strip()
        if len(value) < 2:
            return
        with self.lock:
            if self._partial_translate_last == (seq, value):
                return
            self._partial_translate_last = (seq, value)
            item = {
                "gen": self._partial_translate_gen,
                "seq": seq,
                "text": value,
            }
        try:
            self._partial_translate_queue.put_nowait(item)
        except queue.Full:
            try:
                self._partial_translate_queue.get_nowait()
                self._partial_translate_queue.put_nowait(item)
            except Exception:
                pass

    def _partial_translation_worker(self) -> None:
        while not self._closed:
            try:
                item = self._partial_translate_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if (
                item.get("gen") != self._partial_translate_gen
                or not self.capture_running
                or self.mode != "translate"
            ):
                continue
            current = self.partial.get("other") or {}
            if current.get("seq") != item.get("seq"):
                continue
            target = str(self.settings.get("translate_target_language", "zh") or "zh")
            if target not in ("zh", "en", "ja", "ko"):
                target = "zh"
            try:
                zh = self.brain.translate(
                    str(item.get("text", "")),
                    target,
                    dialogue_context=self._subtitle_dialogue_context(
                        limit=max(0, min(8, int(self.settings.get("partial_context_turns", 4))))
                    ),
                )
            except Exception:
                continue
            if not zh:
                continue
            if item.get("gen") != self._partial_translate_gen:
                continue
            current = self.partial.get("other") or {}
            if current.get("seq") != item.get("seq"):
                continue
            if not str(current.get("stable", "") or "").startswith(str(item.get("text", "") or "")):
                continue
            current["zh"] = str(zh)[:1200]
            current["zh_for"] = str(item.get("text", ""))[:1200]
            current["zh_at"] = time.time()
            self.partial["other"] = current
            self.bus.publish({"type": "partial", "data": dict(current)})

    def _translate_pending(self) -> None:
        """Fill Chinese translations for recent turns that don't have one yet."""
        with self._turns_lock:
            pending = [
                dict(t) for t in self.turns
                if t.get("speaker") == "other" and not t.get("zh") and not t.get("edited")
            ][-4:]
        if not pending:
            return
        dialogue_context = self._subtitle_dialogue_context(
            str(pending[0].get("id", "")),
            max(0, min(12, int(self.settings.get("translate_context_turns", 6)))),
        )
        try:
            target = (
                str(self.settings.get("translate_target_language", "zh") or "zh")
                if self.mode == "translate"
                else "zh"
            )
            translations = self.brain.translate_many(
                [str(t.get("en", "")) for t in pending],
                target,
                dialogue_context=dialogue_context,
            )
        except Exception as exc:
            self._emit_error("translate", f"翻译暂时失败：{exc}")
            return
        if not translations:
            return
        for turn, zh in zip(pending, translations):
            if not zh:
                continue
            now = time.time()
            latency_ms = int((now - float(turn.get("ts", now))) * 1000)
            with self._turns_lock:
                for current in self.turns:
                    if current.get("id") == turn["id"]:
                        if current.get("edited"):
                            break
                        current["zh"] = zh
                        current["zh_at"] = now
                        current["subtitle_ms"] = latency_ms
                        break
            self.bus.publish(
                {"type": "turn_update", "data": {"id": turn["id"], "zh": zh, "subtitle_ms": latency_ms}}
            )
            if self.suggestion and self.suggestion.get("for_turn") == turn["id"]:
                self.suggestion["translation"] = zh

    # ------------------------------------------------------------------ control
    def start_capture(self, fresh: bool = True) -> dict[str, Any]:
        voice_status = str(self.voice_state.get("status", "idle") or "idle")
        if voice_status in ("starting", "recording", "transcribing"):
            return {
                "ok": False,
                "error": "语音输入正在进行中，请先结束或取消它，再开始通话/会议。",
            }
        if self.mode == "voice":
            return {
                "ok": False,
                "error": "语音输入使用全局快捷键，不通过开始按钮采集。",
            }
        other, mic, devices = resolve_devices(self.settings)
        if other is None:
            return {
                "ok": False,
                "error": "没有找到系统声音（扬声器回环）设备。请确认扬声器已启用，并在设置中刷新设备。",
            }
        if self.mode == "translate":
            mic = None
        if fresh:
            self.clear_session()
            if self.mode == "meeting":
                self.meeting_notes = None
                self._meeting_audio.clear()
                self.diarization_state = {
                    "status": "idle",
                    "progress": 0,
                    "message": "",
                    "error": "",
                    "speakers": 0,
                    "turns": 0,
                }
        self.capture_error = ""
        self.device_names = {"other": other.get("name", ""), "me": (mic or {}).get("name", "")}
        engine_settings = dict(self.settings)
        engine_settings["partial_interval_ms"] = max(
            300, min(3000, int(self.settings.get("partial_interval_ms", 800)))
        )
        engine_settings["partial_window_ms"] = max(
            2000, min(10000, int(self.settings.get("partial_window_ms", 6000)))
        )
        if self.mode == "call":
            engine_settings["silence_ms"] = int(
                self.settings.get("call_silence_ms", self.settings.get("silence_ms", 750))
            )
        if self.mode == "translate":
            engine_settings["silence_ms"] = max(
                200, min(1200, int(self.settings.get("subtitle_silence_ms", 350)))
            )
            engine_settings["max_segment_s"] = max(
                3, min(20, int(self.settings.get("subtitle_max_segment_s", 6)))
            )
        self.engine.start(other, mic, engine_settings)
        self.capture_running = True
        self._final_pending.clear()
        self.started_at = time.time()
        self._last_final.clear()
        self._open_session()
        self._publish_status()
        if self.mode == "call" and self.settings.get("auto_suggest", True):
            threading.Timer(0.8, lambda: self.generate_suggestion(opening=True)).start()
        if self.mode == "translate" and self.settings.get("overlay_enabled", True):
            self.start_overlay()
            self.bus.publish({"type": "overlay", "data": {"running": self.overlay_running()}})
        warning = ""
        if self.mode in ("call", "meeting") and mic is None:
            warning = (
                "已关闭麦克风采集，只采集对方声音。"
                if self.settings.get("mic_enabled", True) is False
                else "未找到麦克风，只采集对方声音。"
            )
            if self.mode == "call":
                warning += "回答建议仍然可用。"
            else:
                warning += "会议纪要只能记录会议侧声音。"
            self._emit_error("audio", warning)
        return {"ok": True, "devices": self.device_names, "warning": warning}

    def stop_capture(self) -> dict[str, Any]:
        with self.lock:
            self._suggest_gen += 1
            if self._debounce is not None:
                self._debounce.cancel()
                self._debounce = None
            if self._translation_timer is not None:
                self._translation_timer.cancel()
                self._translation_timer = None
        self.engine.stop()
        self.capture_running = False
        self._final_pending.clear()
        self._invalidate_partial_translation()
        self.levels = {"other": 0.0, "me": 0.0}
        self.partial = {"other": None, "me": None}
        self._partial_prev.clear()
        self._partial_committed.clear()
        self._invalidate_partial_translation()
        self.suggestion = None
        self.bus.publish({"type": "suggestion", "data": None})
        self._cancel_continuation(clear=True)
        if self.mode == "translate":
            self.stop_overlay()
            self.bus.publish({"type": "overlay", "data": {"running": False}})
        self._publish_status()
        self.bus.publish({"type": "levels", "data": dict(self.levels)})
        return {"ok": True, "started_at": self.started_at}

    def overlay_running(self) -> bool:
        proc = self.overlay_proc
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        self.overlay_proc = None
        return False

    def start_overlay(self) -> bool:
        if self.overlay_running():
            return True
        # Remove a hidden/orphaned subtitle overlay from an older app process
        # so appearance settings always reach the current implementation.
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                        "Where-Object { $_.CommandLine -like '*subtitle_overlay.py*' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                    ),
                ],
                capture_output=True,
                timeout=5,
                creationflags=0x08000000,
            )
        except Exception:
            pass
        command = self._helper_command("subtitle_overlay.py", "--subtitle-overlay")
        if command is None:
            return False
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["LAN_ASSISTANT_PORT"] = str(self.server_port)
        try:
            self.overlay_proc = subprocess.Popen(
                command,
                cwd=str(config.ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            return True
        except Exception:
            self.overlay_proc = None
            return False

    def stop_overlay(self) -> None:
        proc = self.overlay_proc
        self.overlay_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def tts_running(self) -> bool:
        proc = self._tts_proc
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        self._tts_proc = None
        return False

    def stop_tts(self) -> None:
        proc = self._tts_proc
        self._tts_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def speak_text(self, text: str, lang: str = "en") -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        self.stop_tts()
        powershell = (
            Path(os.environ.get("WINDIR", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not powershell.exists():
            return False
        script = r"""
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$lang = $env:TTS_LANG
if ($lang) {
  $voice = $speaker.GetInstalledVoices() | Where-Object {
    $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $lang
  } | Select-Object -First 1
  if ($voice) { $speaker.SelectVoice($voice.VoiceInfo.Name) }
}
$speaker.Rate = 0
$speaker.Volume = 100
$speaker.Speak($env:TTS_TEXT)
$speaker.Dispose()
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        env = os.environ.copy()
        env["TTS_TEXT"] = text[:1600]
        env["TTS_LANG"] = (lang or "en")[:2].lower()
        try:
            self._tts_proc = subprocess.Popen(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-EncodedCommand",
                    encoded,
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            return True
        except Exception:
            self._tts_proc = None
            return False

    def clear_session(self) -> None:
        was_running = bool(self.capture_running)
        self._final_pending.clear()
        with self._turns_lock:
            self.turns = []
            self._turn_seq = 0
            self.suggestion = None
            self.partial = {"other": None, "me": None}
            self._partial_prev.clear()
            self._partial_committed.clear()
            self._invalidate_partial_translation()
            self.active_seq = {"other": 0, "me": 0}
            self.session_path = None
            self._last_final.clear()
            self._last_other_final_at = 0.0
            self._passage_mode = False
        self._cancel_continuation(clear=True)
        with self.lock:
            self._suggest_gen += 1
            if self._debounce is not None:
                self._debounce.cancel()
                self._debounce = None
            if self._translation_timer is not None:
                self._translation_timer.cancel()
                self._translation_timer = None
        self.bus.publish({"type": "clear", "data": {}})
        self.bus.publish({"type": "suggestion", "data": None})
        self.call_summary = None
        self._call_summary_gen += 1
        self.bus.publish({"type": "call_summary", "data": None})
        self.meeting_notes = None
        self._meeting_audio.clear()
        self.bus.publish({"type": "meeting_notes", "data": None})
        if was_running:
            # Clearing during a live session used to leave session_path=None,
            # silently stopping all subsequent transcript writes.
            self._open_session()

    def _open_session(self) -> None:
        if not self.settings.get("save_transcript", True):
            self.session_path = None
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = config.SESSIONS_DIR / f"lan-assistant-{stamp}.jsonl"
        try:
            header = {
                "type": "session",
                "started_at": now_iso(),
                "mode": self.mode,
                "profile": self.profile,
                "version": APP_VERSION,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(header, ensure_ascii=False) + "\n")
            self.session_path = path
        except Exception:
            self.session_path = None

    def _save_turn(self, turn: dict[str, Any]) -> None:
        path = self.session_path
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "turn", **turn}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def export_data(self) -> dict[str, Any]:
        with self._turns_lock:
            turns = [dict(t) for t in self.turns]
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "exported_at": now_iso(),
            "started_at": datetime.fromtimestamp(self.started_at).astimezone().isoformat(timespec="seconds")
            if self.started_at
            else None,
            "profile": self.profile,
            "turns": turns,
            "suggestion": self.suggestion,
            "meeting_notes": self.meeting_notes,
        }

    # -------------------------------------------------------------- meeting notes
    def start_meeting_diarization_async(self) -> Optional[threading.Thread]:
        if self.mode != "meeting" or not self.settings.get("diarization_enabled", False):
            return None
        thread = self._diarization_thread
        if thread is not None and thread.is_alive():
            return thread

        def worker() -> None:
            # Let the last VAD segment land after stop_capture().
            time.sleep(2.0)
            try:
                self.run_meeting_diarization()
            except Exception as exc:
                self.diarization_state = {
                    "status": "error",
                    "progress": 0,
                    "message": "说话人区分失败",
                    "error": str(exc)[:500],
                    "speakers": 0,
                    "turns": len(self._meeting_audio),
                }
                self.bus.publish({"type": "diarization", "data": self.diarization_state})

        thread = threading.Thread(
            target=worker,
            name="meeting-diarization",
            daemon=True,
        )
        self._diarization_thread = thread
        thread.start()
        return thread

    def run_meeting_diarization(self) -> dict[str, Any]:
        """Cluster speakers from final meeting turn audio before notes are made."""
        if self.mode != "meeting":
            return {"ok": False, "error": "只有会议纪要工作区可以区分与会人"}
        if not self.settings.get("diarization_enabled", False):
            return {"ok": False, "error": "会议纪要没有开启与会人区分"}
        model_id = str(self.settings.get("diarization_model", diarization.DEFAULT_MODEL) or "")
        if not diarization.is_ready(model_id):
            self.diarization_state = {
                "status": "error",
                "progress": 0,
                "message": "说话人区分模型尚未下载",
                "error": "请先在设置 → 模型（全局）→ 说话人区分中下载模型",
                "speakers": 0,
                "turns": 0,
            }
            self.bus.publish({"type": "diarization", "data": self.diarization_state})
            return {"ok": False, "error": "说话人区分模型尚未下载"}
        with self._turns_lock:
            items = list(self._meeting_audio)
        if not items:
            self.diarization_state = {
                "status": "error",
                "progress": 0,
                "message": "没有可分析的会议语音",
                "error": "开启区分后需要重新录制会议",
                "speakers": 0,
                "turns": 0,
            }
            self.bus.publish({"type": "diarization", "data": self.diarization_state})
            return {"ok": False, "error": "没有可分析的会议语音"}

        def on_progress(progress: int, message: str) -> None:
            self.diarization_state = {
                "status": "running",
                "progress": max(1, min(99, int(progress))),
                "message": message,
                "error": "",
                "speakers": 0,
                "turns": len(items),
            }
            self.bus.publish({"type": "diarization", "data": self.diarization_state})

        on_progress(1, "正在准备说话人分离…")
        try:
            result = diarization.diarize_turns(items, model_id, progress=on_progress)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if not result.get("ok"):
            self.diarization_state = {
                "status": "error",
                "progress": 0,
                "message": "说话人区分失败",
                "error": str(result.get("error", ""))[:500],
                "speakers": 0,
                "turns": len(items),
            }
            self.bus.publish({"type": "diarization", "data": self.diarization_state})
            return {"ok": False, "error": self.diarization_state["error"]}
        assignments = result.get("assignments") or {}
        with self._turns_lock:
            updated = []
            saved_turns = []
            for turn in self.turns:
                label = assignments.get(str(turn.get("id")))
                if label:
                    turn["speaker_label"] = str(label)
                    turn["updated_at"] = time.time()
                    updated.append(
                        {"id": turn["id"], "speaker_label": str(label)}
                    )
                    saved_turns.append(dict(turn))
        for turn in saved_turns:
            self._save_turn(turn)
        for row in updated:
            self.bus.publish({"type": "turn_update", "data": row})
        with self._turns_lock:
            self._meeting_audio.clear()
        self.diarization_state = {
            "status": "ready",
            "progress": 100,
            "message": f"已区分 {result.get('speakers', 0)} 位与会人",
            "error": "",
            "speakers": int(result.get("speakers", 0) or 0),
            "turns": len(items),
        }
        self.bus.publish({"type": "diarization", "data": self.diarization_state})
        return {"ok": True, **self.diarization_state, "assignments": assignments}

    def _meeting_lines(self) -> list[str]:
        with self._turns_lock:
            turns = [dict(t) for t in self.turns]
        base = float(self.started_at or (turns[0].get("ts", time.time()) if turns else time.time()))
        lines = []
        for turn in turns:
            offset = max(0, int(float(turn.get("ts", base)) - base))
            stamp = f"{offset // 3600:02d}:{(offset % 3600) // 60:02d}:{offset % 60:02d}"
            speaker = "对方" if turn.get("speaker") == "other" else "我"
            if turn.get("speaker") == "other" and turn.get("speaker_label"):
                speaker = str(turn.get("speaker_label"))
            text = str(turn.get("en", "") or "").strip()
            zh = str(turn.get("zh", "") or "").strip()
            if not text:
                continue
            line = f"[{stamp}] {speaker}: {text}"
            if zh and zh != text:
                line += f" / {zh}"
            lines.append(line[:1600])
        return lines

    @staticmethod
    def _chunk_lines(lines: list[str], limit: int = 6500) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for line in lines:
            if current and size + len(line) + 1 > limit:
                chunks.append("\n".join(current))
                current = []
                size = 0
            current.append(line)
            size += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        return chunks

    # ----------------------------------------------------------- call summary
    def _call_review_blocks(self) -> list[dict[str, str]]:
        with self._turns_lock:
            rows = [dict(t) for t in self.turns]
        blocks: list[dict[str, str]] = []
        for turn in rows:
            speaker = str(turn.get("speaker", "") or "")
            text = str(turn.get("en", "") or "").strip()
            if speaker not in ("other", "me") or not text:
                continue
            if blocks and blocks[-1]["speaker"] == speaker:
                blocks[-1]["text"] = (blocks[-1]["text"] + " " + text).strip()
            else:
                blocks.append({"speaker": speaker, "text": text})
        return blocks

    def _call_review_pairs(self, blocks: list[dict[str, str]]) -> list[dict[str, str]]:
        pairs: list[dict[str, str]] = []
        question = ""
        for block in blocks:
            if block["speaker"] == "other":
                question = block["text"]
            elif question:
                pairs.append({"question": question, "answer": block["text"]})
                question = ""
        return pairs

    @staticmethod
    def _call_review_fallback(
        blocks: list[dict[str, str]], pairs: list[dict[str, str]], error: str = ""
    ) -> dict[str, Any]:
        intro_question = next(
            (p["question"] for p in pairs if _is_self_intro_request(p["question"])), ""
        )
        return {
            "status": "ready",
            "overview": (
                f"本次通话共识别到 {len(pairs)} 个问题和 {sum(1 for b in blocks if b['speaker'] == 'me')} 段回答。"
                "语言模型总结暂时不可用，以下为按原文整理的逐题记录。"
            ),
            "questions": [
                {
                    "question": p["question"],
                    "answer": p["answer"],
                    "improved_answer": "",
                    "feedback": "",
                }
                for p in pairs
            ],
            "strengths": [],
            "improvements": ["逐题回看上面的问答；带 ASR 噪声的句子需要结合上下文判断。"],
            "phrases": [],
            "intro": {
                "present": bool(intro_question),
                "feedback": "",
                "improved": "",
            },
            "error": error,
            "model": "",
            "latency_ms": 0,
            "created_at": time.time(),
        }

    def generate_call_summary(self) -> dict[str, Any]:
        if self.mode != "call":
            return {"ok": False, "error": "只有通话副驾可以生成通话总结。"}
        blocks = self._call_review_blocks()
        pairs = self._call_review_pairs(blocks)
        if not pairs:
            return {"ok": False, "error": "还没有可总结的通话内容。"}
        if self.call_summary and self.call_summary.get("status") == "thinking":
            return {"ok": True, "summary": self.call_summary}
        with self.lock:
            self._call_summary_gen += 1
            gen = self._call_summary_gen
        thinking = {
            "status": "thinking",
            "started_at": time.time(),
            "overview": "",
            "questions": [],
            "strengths": [],
            "improvements": [],
            "phrases": [],
            "intro": {"present": any(_is_self_intro_request(p["question"]) for p in pairs), "feedback": "", "improved": ""},
            "error": "",
        }
        self.call_summary = thinking
        self.bus.publish({"type": "call_summary", "data": thinking})
        started = time.perf_counter()
        transcript = "\n".join(
            f"{'Interviewer' if b['speaker'] == 'other' else 'Candidate'}: {b['text']}"
            for b in blocks
        )[-16000:]
        language = "English"
        try:
            result = self.brain.call_review(transcript, language=language, profile=self.profile)
            if gen != self._call_summary_gen:
                return {"ok": False, "error": "cancelled"}
            payload = {
                "status": "ready",
                "overview": str(result.get("overview", "") or ""),
                "questions": [
                    {
                        "question": str(item.get("question", "") or ""),
                        "answer": str(item.get("answer", "") or ""),
                        "improved_answer": str(item.get("improved_answer", "") or ""),
                        "feedback": str(item.get("feedback", "") or ""),
                    }
                    for item in (result.get("questions") or [])
                    if isinstance(item, dict)
                ],
                "strengths": [str(x) for x in (result.get("strengths") or []) if str(x).strip()],
                "improvements": [str(x) for x in (result.get("improvements") or []) if str(x).strip()],
                "phrases": [
                    {
                        "phrase": str(item.get("phrase", "") or ""),
                        "meaning": str(item.get("meaning", "") or ""),
                    }
                    for item in (result.get("phrases") or [])
                    if isinstance(item, dict)
                ],
                "intro": result.get("intro") if isinstance(result.get("intro"), dict) else {
                    "present": False, "feedback": "", "improved": ""
                },
                "error": "",
                "model": getattr(self.brain, "model", ""),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "created_at": time.time(),
            }
        except Exception as exc:
            if gen != self._call_summary_gen:
                return {"ok": False, "error": "cancelled"}
            payload = self._call_review_fallback(blocks, pairs, str(exc))
            payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        self.call_summary = payload
        self.bus.publish({"type": "call_summary", "data": payload})
        return {"ok": True, "summary": payload}

    def call_summary_export_text(self) -> str:
        data = self.call_summary or {}
        lines = ["# Lan.Assistant 通话总结", ""]
        if data.get("status") != "ready":
            lines.append("通话总结还没有生成完成。")
            return "\n".join(lines) + "\n"
        if data.get("overview"):
            lines += ["## 概述", "", str(data["overview"]), ""]
        questions = data.get("questions") or []
        if questions:
            lines += ["## 问答复盘", ""]
            for index, item in enumerate(questions, 1):
                lines.append(f"### {index}. {item.get('question', '')}")
                lines.append("")
                lines.append(f"**你的回答**：{item.get('answer', '')}")
                if item.get("improved_answer"):
                    lines += ["", f"**更自然的说法**：{item.get('improved_answer', '')}"]
                if item.get("feedback"):
                    lines += ["", f"**反馈**：{item.get('feedback', '')}"]
                lines.append("")
        for key, title in (("strengths", "做得好的地方"), ("improvements", "可改进的地方")):
            values = data.get(key) or []
            if values:
                lines += [f"## {title}", ""]
                lines += [f"- {value}" for value in values]
                lines.append("")
        phrases = data.get("phrases") or []
        if phrases:
            lines += ["## 可复习短语", ""]
            for item in phrases:
                lines.append(f"- **{item.get('phrase', '')}** — {item.get('meaning', '')}")
            lines.append("")
        intro = data.get("intro") or {}
        if intro.get("present"):
            lines += ["## 自我介绍复盘", ""]
            if intro.get("feedback"):
                lines += [str(intro["feedback"]), ""]
            if intro.get("improved"):
                lines += ["**改进版**：", "", str(intro["improved"]), ""]
        return "\n".join(lines).strip() + "\n"

    def generate_meeting_notes(self) -> dict[str, Any]:
        if self.mode != "meeting":
            return {"ok": False, "error": "只有会议纪要工作区可以生成纪要。"}
        thread = self._diarization_thread
        if (
            self.settings.get("diarization_enabled", False)
            and thread is not None
            and thread.is_alive()
        ):
            return {"ok": False, "error": "正在区分与会人，请稍候再生成纪要。"}
        lines = self._meeting_lines()
        if not lines:
            return {"ok": False, "error": "还没有会议转录内容，先开始记录。"}
        current = self.meeting_notes or {}
        if current.get("status") == "thinking":
            return {"ok": True, "notes": current}
        self.meeting_notes = {
            "status": "thinking",
            "notes": None,
            "error": "",
            "generated_at": None,
            "transcript_chars": sum(len(line) for line in lines),
            "chunks": 0,
        }
        self.bus.publish({"type": "meeting_notes", "data": self.meeting_notes})
        thread = threading.Thread(
            target=self._meeting_notes_worker,
            args=(lines,),
            name="meeting-notes",
            daemon=True,
        )
        thread.start()
        return {"ok": True, "notes": self.meeting_notes}

    def _meeting_notes_worker(self, lines: list[str]) -> None:
        try:
            context = str(self.settings.get("meeting_context", "") or "")
            language = str(self.settings.get("meeting_notes_language", "zh") or "zh")
            full = "\n".join(lines)
            chunks = self._chunk_lines(lines) if len(full) > 12000 else [full]
            if len(chunks) > 1:
                summaries = []
                for index, chunk in enumerate(chunks):
                    summary = self.brain.meeting_chunk_summary(chunk, language)
                    if summary:
                        summaries.append(f"[Part {index + 1}]\n{summary}")
                    self.meeting_notes = {
                        **(self.meeting_notes or {}),
                        "chunks_done": index + 1,
                        "chunks": len(chunks),
                    }
                    self.bus.publish({"type": "meeting_notes", "data": self.meeting_notes})
                source = "\n\n".join(summaries)[:24000]
            else:
                source = full
            notes = self.brain.meeting_notes(source, context, language)
            if not str(notes.get("summary", "") or "").strip():
                points = notes.get("key_points") or []
                notes["summary"] = "；".join(str(p) for p in points[:3]) if points else "本次会议没有足够内容生成摘要。"
            data = {
                "status": "ready",
                "notes": notes,
                "error": "",
                "generated_at": now_iso(),
                "transcript_chars": len(full),
                "chunks": len(chunks),
            }
            self.meeting_notes = data
            self._save_meeting_notes(data)
            self.bus.publish({"type": "meeting_notes", "data": data})
        except Exception as exc:
            self.meeting_notes = {
                **(self.meeting_notes or {}),
                "status": "error",
                "error": str(exc)[:400],
            }
            self.bus.publish({"type": "meeting_notes", "data": self.meeting_notes})

    def _save_meeting_notes(self, data: dict[str, Any]) -> None:
        path = self.session_path
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"type": "meeting_notes", "generated_at": data.get("generated_at"),
                                "notes": data.get("notes")}, ensure_ascii=False) + "\n"
                )
        except Exception:
            pass

    def meeting_export_text(self) -> str:
        data = self.meeting_notes or {}
        notes = data.get("notes") or {}
        if data.get("status") != "ready" or not notes:
            return "# Lan.Assistant 会议纪要\n\n还没有生成纪要。先记录会议并点击「生成纪要」。\n"
        title = str(notes.get("title", "") or "会议纪要")
        lines = [f"# {title}", ""]
        if self.started_at:
            lines.append(f"- 时间：{datetime.fromtimestamp(self.started_at).astimezone().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"- 模型：{notes.get('model', '')}")
        lines.append("- 生成工具：Lan.Assistant")
        lines.append("")
        summary = str(notes.get("summary", "") or "").strip()
        if summary:
            lines.extend(["## 摘要", "", summary, ""])
        def bullet_section(name: str, key: str) -> None:
            rows = notes.get(key, []) or []
            if rows:
                lines.extend([f"## {name}", ""])
                lines.extend(f"- {row}" for row in rows)
                lines.append("")
        bullet_section("关键讨论", "key_points")
        bullet_section("已确认决定", "decisions")
        actions = notes.get("action_items", []) or []
        if actions:
            lines.extend(["## 待办事项", "", "| 事项 | 负责人 | 截止时间 |", "|---|---|---|"])
            for item in actions:
                task = str(item.get("task", "") or "").replace("|", "\\|")
                owner = str(item.get("owner", "") or "待定").replace("|", "\\|")
                due = str(item.get("due", "") or "待定").replace("|", "\\|")
                lines.append(f"| {task} | {owner} | {due} |")
            lines.append("")
        bullet_section("未解决问题", "open_questions")
        return "\n".join(lines).strip() + "\n"

    def _save_subtitle_edit(self, turn: dict[str, Any]) -> None:
        path = self.session_path
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "subtitle_edit",
                            "id": turn.get("id"),
                            "en": turn.get("en", ""),
                            "zh": turn.get("zh", ""),
                            "at": now_iso(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass

    @staticmethod
    def _format_timestamp(seconds: float, comma: bool = True) -> str:
        seconds = max(0.0, float(seconds))
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            seconds += 1
            millis = 0
        total = int(seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        sep = "," if comma else "."
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"

    def subtitle_export(self, fmt: str = "srt", lang: str = "both") -> str:
        with self._turns_lock:
            turns = [dict(t) for t in self.turns if t.get("speaker") == "other"]
        if not turns:
            return ""
        base = float(turns[0].get("ts", time.time()))
        rows: list[tuple[float, float, str]] = []
        for turn in turns:
            start = max(0.0, float(turn.get("ts", base)) - base)
            duration = max(1.0, float(turn.get("duration_ms", 0) or 0) / 1000.0)
            en = str(turn.get("en", "") or "").strip()
            zh = str(turn.get("zh", "") or "").strip()
            if lang == "zh":
                text = zh
            elif lang == "source":
                text = en
            else:
                text = "\n".join(part for part in (en, zh) if part)
            if text:
                rows.append((start, start + duration, text))
        fmt = (fmt or "srt").lower()
        if fmt == "txt":
            return "\n\n".join(text for _, _, text in rows) + ("\n" if rows else "")
        lines: list[str] = []
        if fmt == "vtt":
            lines.append("WEBVTT")
            lines.append("")
        for index, (start, end, text) in enumerate(rows, start=1):
            if fmt == "vtt":
                lines.extend(
                    [
                        f"{self._format_timestamp(start, comma=False)} --> {self._format_timestamp(end, comma=False)}",
                        text.replace("-->", "→"),
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        str(index),
                        f"{self._format_timestamp(start)} --> {self._format_timestamp(end)}",
                        text.replace("-->", "→"),
                        "",
                    ]
                )
        return "\n".join(lines)

    def update_subtitles(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {str(item.get("id", "")): item for item in updates if isinstance(item, dict)}
        changed: list[dict[str, Any]] = []
        with self._turns_lock:
            for turn in self.turns:
                if turn.get("speaker") != "other":
                    continue
                item = by_id.get(str(turn.get("id", "")))
                if item is None:
                    continue
                en = str(item.get("en", turn.get("en", "")) or "").strip()[:4000]
                zh = str(item.get("zh", turn.get("zh", "")) or "").strip()[:4000]
                if not en:
                    continue
                if en == str(turn.get("en", "") or "").strip() and zh == str(turn.get("zh", "") or "").strip():
                    continue
                turn["en"] = en
                turn["zh"] = zh
                turn["edited"] = True
                turn["edited_at"] = time.time()
                snapshot = dict(turn)
                changed.append(snapshot)
                self._save_subtitle_edit(snapshot)
        if changed:
            self.bus.publish({"type": "subtitles_updated", "data": changed})
        return changed

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        with self._turns_lock:
            turns = [dict(t) for t in self.turns]
            suggestion = dict(self.suggestion) if self.suggestion else None
        return {
            "version": APP_VERSION,
            "display_version": APP_DISPLAY_VERSION,
            "server_time": time.time(),
            "port": self.server_port,
            "mode": self.mode,
            "overlay_running": self.overlay_running(),
            "continuation": self.continuation,
            "call_summary": dict(self.call_summary) if self.call_summary else None,
            "profile": self.profile,
            "settings": config.public_settings(self.settings),
            "setup": self.setup.public_state(),
            "hardware": self.hardware,
            "hardware_recommendation": hardware.recommend(self.hardware, self.settings),
            "models": models.catalog(),
            "turns": turns,
            "suggestion": suggestion,
            "meeting_notes": self.meeting_notes,
            "voice_input": self.voice_public_state(),
            "diarization": diarization.public_state(
                str(self.settings.get("diarization_model", diarization.DEFAULT_MODEL) or "")
            ),
            "diarization_run": dict(self.diarization_state),
            "voice_history": [dict(row) for row in self.voice_history[:20]],
            "partial": self.partial,
            "levels": self.levels,
            "status": {
                "capture": {
                    "running": self.capture_running,
                    "started_at": self.started_at,
                    "error": self.capture_error,
                    "devices": self.device_names,
                },
                "asr": {"status": self.asr_status, "message": self.asr_message},
                "llm": {"status": self.llm_status, "message": self.llm_message},
            },
        }

    def _emit_error(self, scope: str, message: str) -> None:
        now = time.time()
        if now - self._last_error_at > 3.0 or scope in ("llm", "translate"):
            self._last_error_at = now
            self.bus.publish({"type": "error", "data": {"scope": scope, "message": message}})

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> None:
        self._closed = True
        config.clear_runtime_port(self.server_port)
        try:
            if self.settings.get("restore_last_mode", True):
                self.settings = config.save_settings({**self.settings, "app_mode": self.mode})
        except Exception:
            pass
        try:
            self.voice_hotkey.stop()
        except Exception:
            pass
        try:
            if self.voice_recorder is not None:
                self.voice_recorder.cancel()
        except Exception:
            pass
        try:
            if self._voice_timer is not None:
                self._voice_timer.cancel()
        except Exception:
            pass
        try:
            self.voice_overlay_proc and self.voice_overlay_proc.terminate()
        except Exception:
            pass
        try:
            self.capture_running = False
            self._final_pending.clear()
            self.engine.stop()
        except Exception:
            pass
        self.stop_overlay()
        self.stop_tts()
        self._cancel_continuation(clear=True)
        with self.lock:
            if self._debounce is not None:
                self._debounce.cancel()
            if self._translation_timer is not None:
                self._translation_timer.cancel()
        try:
            self._asr_queue.put_nowait(None)
            self._partial_queue.put_nowait(None)
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = f"{APP_NAME}/{APP_VERSION}"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and str(args[0]).startswith(("GET /api/events", "GET /api/state")):
            return
        print("%s - %s" % (self.address_string(), fmt % args))

    def _headers(self, code: int, content_type: str, length: Optional[int] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(code, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"ok": False, "error": message}, code)

    def _redirect(self, location: str, code: int = 302) -> None:
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > 2_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _handle_get(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/events":
            self._events()
            return
        if path == "/api/state":
            self._json(self.app.state())
            return
        if path == "/api/subtitle":
            latest = None
            with self.app._turns_lock:
                for turn in reversed(self.app.turns):
                    if turn.get("speaker") == "other":
                        latest = dict(turn)
                        break
            self._json(
                {
                    "mode": self.app.mode,
                    "capture": self.app.capture_running,
                    "latest": latest,
                    "partial": self.app.partial.get("other"),
                    "appearance": {
                        "font_family": str(
                            self.app.settings.get("subtitle_font_family", "Microsoft YaHei UI")
                            or "Microsoft YaHei UI"
                        ),
                        "original_size": int(
                            self.app.settings.get("subtitle_original_size", 15) or 15
                        ),
                        "translation_size": int(
                            self.app.settings.get("subtitle_translation_size", 23) or 23
                        ),
                        "original_color": str(
                            self.app.settings.get("subtitle_original_color", "#f4f8fd")
                            or "#f4f8fd"
                        ),
                        "translation_color": str(
                            self.app.settings.get("subtitle_translation_color", "#ffffff")
                            or "#ffffff"
                        ),
                    },
                    "poll_ms": max(80, min(1000, int(self.app.settings.get("overlay_poll_ms", 150)))),
                }
            )
            return
        if path == "/api/devices":
            try:
                self._json({"ok": True, "devices": list_devices(), "current": self.app.settings})
            except Exception as exc:
                self._error(f"读取音频设备失败：{exc}", 500)
            return
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "version": APP_VERSION,
                    "display_version": APP_DISPLAY_VERSION,
                    "asr": {"status": self.app.asr_status, "message": self.app.asr_message,
                            "device": self.app.transcriber.device, "compute_type": self.app.transcriber.compute_type},
                    "llm": {"status": self.app.llm_status, "message": self.app.llm_message,
                            "model": self.app.brain.model, "device": "ollama"},
                    "capture": self.app.capture_running,
                    "session_path": str(self.app.session_path) if self.app.session_path else None,
                }
            )
            return
        if path == "/api/tts/status":
            self._json({"running": self.app.tts_running()})
            return
        if path == "/api/voice/status":
            self._json(self.app.voice_public_state())
            return
        if path == "/api/hardware":
            self._json({"hardware": self.app.hardware,
                        "recommendation": hardware.recommend(self.app.hardware, self.app.settings)})
            return
        if path == "/api/models":
            query = urllib.parse.parse_qs(parsed.query)
            refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
            self._json(models.catalog(refresh=refresh))
            return
        if path == "/api/diarization":
            query = urllib.parse.parse_qs(parsed.query)
            model_id = str(
                (query.get("model", [""])[0] or "")
                or self.app.settings.get("diarization_model", diarization.DEFAULT_MODEL)
                or diarization.DEFAULT_MODEL
            )
            self._json(diarization.public_state(model_id))
            return
        if path == "/api/model-management":
            self._json(models.management(refresh=True))
            return
        if path == "/api/subtitles/export":
            query = urllib.parse.parse_qs(parsed.query)
            fmt = (query.get("format", ["srt"])[0] or "srt").lower()
            lang = (query.get("lang", ["both"])[0] or "both").lower()
            if fmt not in ("srt", "vtt", "txt"):
                fmt = "srt"
            if lang not in ("both", "zh", "source"):
                lang = "both"
            text = self.app.subtitle_export(fmt, lang)
            body = text.encode("utf-8")
            mime = "application/x-subrip; charset=utf-8" if fmt == "srt" else (
                "text/vtt; charset=utf-8" if fmt == "vtt" else "text/plain; charset=utf-8"
            )
            filename = f"lan-assistant-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{fmt}"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/meeting/export":
            text = self.app.meeting_export_text()
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            filename = f"lan-assistant-meeting-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/call/summary/export":
            text = self.app.call_summary_export_text()
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            filename = f"lan-assistant-call-summary-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/export":
            data = self.app.export_data()
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="lan-assistant-export.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/sessions":
            active = self.app.session_path.name if self.app.session_path else ""
            self._json({"ok": True, **session_store.list_sessions(active)})
            return
        if path.startswith("/api/sessions/"):
            session_id = urllib.parse.unquote(path[len("/api/sessions/"):])
            try:
                self._json({"ok": True, "session": session_store.read_session(session_id)})
            except FileNotFoundError:
                self._error("会话不存在", 404)
            except Exception as exc:
                self._error(f"读取会话失败：{exc}", 400)
            return
        if path == "/api/setup/state":
            self._json(self.app.setup.public_state())
            return
        if path == "/":
            if self.app.setup.required:
                self._redirect("/setup")
                return
            self._serve_file(STATIC_DIR / "index.html")
            return
        if path == "/setup":
            if not self.app.setup.required:
                self._redirect("/")
                return
            self._serve_file(STATIC_DIR / "setup.html")
            return
        if path == "/favicon.ico":
            self._serve_file(STATIC_DIR / "favicon.ico")
            return
        if path.startswith("/static/"):
            rel = urllib.parse.unquote(path[len("/static/"):])
            target = (STATIC_DIR / rel).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self._error("not found", 404)
                return
            self._serve_file(target)
            return
        self._error("not found", 404)

    def _handle_post(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._body()
        except Exception as exc:
            self._error(str(exc), 400)
            return
        if path == "/api/sessions/delete":
            raw_ids = body.get("ids")
            ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
            try:
                older = body.get("older_than_days")
                older = int(older) if older is not None and str(older).strip() != "" else None
                if older is not None:
                    older = max(0, min(3650, older))
            except (TypeError, ValueError):
                self._error("删除天数无效", 400)
                return
            mode = str(body.get("mode", "all") or "all")
            if mode not in ("all", "call", "translate", "meeting", "unknown"):
                mode = "all"
            if not ids and older is None:
                self._error("请选择要删除的会话，或设置删除条件", 400)
                return
            active = self.app.session_path.name if self.app.session_path else ""
            try:
                result = session_store.delete_sessions(
                    ids=ids,
                    older_than_days=older,
                    mode=mode,
                    active_name=active,
                )
            except Exception as exc:
                self._error(f"删除会话失败：{exc}", 400)
                return
            self._json({"ok": True, **result})
            return
        if path == "/api/setup/install-ollama":
            started = self.app.setup.install_ollama_async(
                str(body.get("source_url", "") or "")
            )
            self._json(
                {
                    "ok": started or self.app.setup.public_state().get("ollama", {}).get("installed", False),
                    "busy": self.app.setup.public_state().get("busy", False),
                    "error": self.app.setup.public_state().get("error", ""),
                }
            )
            return
        if path == "/api/setup/download":
            whisper_ids = body.get("whisper_ids")
            llm_ids = body.get("llm_ids")
            if not isinstance(whisper_ids, list):
                whisper_ids = []
            if not isinstance(llm_ids, list):
                llm_ids = []
            started = self.app.setup.download_async(
                [str(value) for value in whisper_ids],
                [str(value) for value in llm_ids],
            )
            state = self.app.setup.public_state()
            self._json(
                {
                    "ok": started,
                    "error": state.get("error", ""),
                    "state": state,
                },
                200 if started else 400,
            )
            return
        if path == "/api/start":
            fresh = bool(body.get("fresh", True))
            result = self.app.start_capture(fresh=fresh)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if path == "/api/voice/start":
            self._json(self.app.start_voice_input())
            return
        if path == "/api/voice/stop":
            self._json(self.app.stop_voice_input())
            return
        if path == "/api/voice/toggle":
            self._json(self.app.toggle_voice_input())
            return
        if path == "/api/voice/insert":
            text = str(body.get("text", "") or "")
            self._json(self.app.insert_voice_input(text))
            return
        if path == "/api/voice/cancel":
            self._json(self.app.cancel_voice_input())
            return
        if path == "/api/voice/history/clear":
            self.app.clear_voice_history()
            self._json({"ok": True})
            return
        if path == "/api/model-management/add":
            try:
                row = models.add_custom(
                    str(body.get("kind", "") or ""),
                    str(body.get("label", "") or ""),
                    str(body.get("path", "") or ""),
                    str(body.get("source", "") or ""),
                    str(body.get("size_hint", "") or ""),
                )
                self._json({"ok": True, "model": row, "management": models.management(refresh=True)})
            except Exception as exc:
                self._error(f"添加模型失败：{exc}", 400)
            return
        if path == "/api/models/download":
            ok, message = self.app.setup.download_preset_async(
                str(body.get("kind", "") or ""),
                str(body.get("id", "") or ""),
            )
            if not ok:
                self._error(message, 409)
                return
            self._json({"ok": True, "message": message})
            return
        if path == "/api/model-management/delete":
            try:
                deleted_id = str(body.get("id", "") or "")
                result = models.delete_model(deleted_id)
                if deleted_id.startswith("diarization-") and not diarization.is_ready(
                    str(self.app.settings.get("diarization_model", diarization.DEFAULT_MODEL) or "")
                ):
                    self.app.settings = config.save_settings(
                        {**self.app.settings, "diarization_enabled": False}
                    )
                    self.app.brain.set_settings(self.app.settings)
                self._json(
                    {
                        "ok": True,
                        "management": result,
                        "settings": config.public_settings(self.app.settings),
                    }
                )
            except Exception as exc:
                self._error(f"删除模型失败：{exc}", 400)
            return
        if path == "/api/hardware/apply":
            old_profile = (
                self.app.settings.get("local_gpu_mode"),
                self.app.settings.get("asr_device"),
                self.app.settings.get("llm_model"),
            )
            updated, profile, changed = hardware.apply_to_settings(self.app.settings, force=True)
            if changed:
                old_model = str(self.app.settings.get("llm_model", ""))
                self.app.settings = config.save_settings(updated)
                self.app.brain.set_settings(self.app.settings)
                self.app.hardware = profile
                new_profile = (
                    self.app.settings.get("local_gpu_mode"),
                    self.app.settings.get("asr_device"),
                    self.app.settings.get("llm_model"),
                )
                if old_model != self.app.settings.get("llm_model"):
                    self.app.release_local_models([old_model] if old_model else None)
                if new_profile != old_profile:
                    self.app.llm_status = "loading"
                    self.app.llm_message = "硬件配置已更新，生成时重新检查本地模型…"
                    self.app._publish_status()
            self._json({
                "ok": True,
                "changed": changed,
                "hardware": profile,
                "recommendation": hardware.recommend(profile, self.app.settings),
                "settings": config.public_settings(self.app.settings),
            })
            return
        if path == "/api/stop":
            result = self.app.stop_capture()
            if (
                self.app.mode == "meeting"
                and self.app.settings.get("diarization_enabled", False)
            ):
                # Diarization is independent from auto-notes: it still runs if
                # the user prefers to generate notes manually later.
                self.app.start_meeting_diarization_async()
            if (
                self.app.mode == "meeting"
                and self.app.settings.get("meeting_auto_notes", True)
            ):
                def auto_notes() -> None:
                    # The last VAD segment can arrive just after stop; give it
                    # a moment so the final sentence is included in the notes.
                    time.sleep(2.0)
                    if self.app.settings.get("diarization_enabled", False):
                        thread = self.app._diarization_thread
                        if thread is not None:
                            thread.join(timeout=900)
                    if self.app.turns:
                        self.app.generate_meeting_notes()
                threading.Thread(
                    target=auto_notes,
                    name="meeting-notes-auto",
                    daemon=True,
                ).start()
            if self.app.mode == "call" and self.app.settings.get("call_auto_summary", True):
                def auto_call_summary() -> None:
                    time.sleep(1.5)
                    if self.app.turns and not self.app.capture_running:
                        self.app.generate_call_summary()
                threading.Thread(
                    target=auto_call_summary,
                    name="call-summary-auto",
                    daemon=True,
                ).start()
            self._json(result)
            return
        if path == "/api/call/summary":
            if self.app.mode != "call":
                self._json({"ok": False, "error": "只有通话副驾可以生成通话总结。"}, 400)
                return
            threading.Thread(
                target=self.app.generate_call_summary,
                name="call-summary-manual",
                daemon=True,
            ).start()
            self._json({"ok": True})
            return
        if path == "/api/meeting/notes":
            self._json(self.app.generate_meeting_notes())
            return
        if path == "/api/meeting/export":
            text = self.app.meeting_export_text()
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            filename = f"lan-assistant-meeting-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/clear":
            self.app.clear_session()
            self._json({"ok": True})
            return
        if path == "/api/subtitles/update":
            updates = body.get("updates")
            if not isinstance(updates, list):
                self._error("updates 必须是数组", 400)
                return
            changed = self.app.update_subtitles(updates)
            self._json({"ok": True, "updated": len(changed), "turns": changed})
            return
        if path == "/api/profile":
            try:
                self.app.profile = config.save_profile(body)
                self._json({"ok": True, "profile": self.app.profile})
            except Exception as exc:
                self._error(f"保存预设失败：{exc}", 500)
            return
        if path == "/api/media-preset":
            allowed = (
                "media_type",
                "media_title",
                "media_context",
                "subtitle_style",
                "subtitle_silence_ms",
                "overlay_enabled",
            )
            patch = {key: body[key] for key in allowed if key in body}
            try:
                self.app.settings = config.save_settings({**self.app.settings, **patch})
                self.app.brain.set_settings(self.app.settings)
                if "overlay_enabled" in patch:
                    if patch["overlay_enabled"]:
                        if self.app.mode == "translate":
                            self.app.start_overlay()
                    else:
                        self.app.stop_overlay()
                    self.app.bus.publish(
                        {"type": "overlay", "data": {"running": self.app.overlay_running()}}
                    )
                self._json(
                    {
                        "ok": True,
                        "settings": {key: self.app.settings.get(key) for key in allowed},
                    }
                )
            except Exception as exc:
                self._error(f"保存字幕预设失败：{exc}", 500)
            return
        if path == "/api/api-profile/clear":
            try:
                self.app.settings = config.clear_api_profile(self.app.settings)
                self.app.brain.set_settings(self.app.settings)
                self.app._publish_status()
                self._json({"ok": True, "settings": config.public_settings(self.app.settings)})
            except Exception as exc:
                self._error(f"清除本机 API 配置失败：{exc}", 500)
            return
        if path == "/api/settings":
            try:
                engine_keys = (
                    "llm_provider",
                    "llm_model",
                    "api_base_url",
                    "api_model",
                    "api_key",
                    "api_disable_thinking",
                )
                old_engine = tuple(self.app.settings.get(key) for key in engine_keys)
                old_voice_hotkey = str(self.app.settings.get("voice_hotkey", "ctrl+alt+space"))
                old_voice_hold = bool(self.app.settings.get("voice_hotkey_hold", False))
                old_voice_overlay = bool(self.app.settings.get("voice_overlay_enabled", True))
                old_auto_hardware = bool(self.app.settings.get("hardware_auto_configure", True))
                old_ui_language = language.normalize(self.app.settings.get("ui_language", "zh"))
                requested_ui_language = language.normalize(
                    body.get("ui_language", old_ui_language) or old_ui_language
                )
                merged = {**self.app.settings, **body}
                if requested_ui_language != old_ui_language:
                    # Interface language drives the per-feature defaults.
                    merged.update(language.defaults_for_ui(requested_ui_language))
                merged["ui_language"] = requested_ui_language
                requested_diarization_model = str(
                    merged.get("diarization_model", diarization.DEFAULT_MODEL)
                    or diarization.DEFAULT_MODEL
                )
                if bool(merged.get("diarization_enabled", False)) and not diarization.is_ready(
                    requested_diarization_model
                ):
                    self._error("需要先下载说话人区分模型，才能开启与会人区分。", 409)
                    return
                self.app.settings = config.save_settings(merged)
                self.app.brain.set_settings(self.app.settings)
                if self.app.mode == "translate":
                    overlay_was_running = self.app.overlay_running()
                    if self.app.settings.get("overlay_enabled", True):
                        self.app.start_overlay()
                    else:
                        self.app.stop_overlay()
                    if overlay_was_running != self.app.overlay_running():
                        self.app.bus.publish(
                            {"type": "overlay", "data": {"running": self.app.overlay_running()}}
                        )
                if not old_auto_hardware and self.app.settings.get("hardware_auto_configure", True):
                    updated, profile, changed = hardware.apply_to_settings(self.app.settings, force=True)
                    self.app.hardware = profile
                    if changed:
                        self.app.settings = config.save_settings(updated)
                        self.app.brain.set_settings(self.app.settings)
                if old_voice_overlay and not self.app.settings.get("voice_overlay_enabled", True):
                    self.app.stop_voice_overlay()
                elif not old_voice_overlay and self.app.settings.get("voice_overlay_enabled", True):
                    self.app.start_voice_overlay()
                if self.app.settings.get("restore_last_mode", True):
                    # Keep the saved workspace in sync with the live one even
                    # when settings are saved from a stale page.
                    self.app.settings = config.save_settings(
                        {**self.app.settings, "app_mode": self.app.mode}
                    )
                    self.app.brain.set_settings(self.app.settings)
                new_voice_hotkey = str(self.app.settings.get("voice_hotkey", "ctrl+alt+space"))
                self.app.voice_state["hotkey"] = new_voice_hotkey
                self.app.voice_state["target_language"] = str(
                    self.app.settings.get("voice_target_language", "zh")
                )
                if old_voice_hotkey != new_voice_hotkey or old_voice_hold != bool(
                    self.app.settings.get("voice_hotkey_hold", False)
                ):
                    threading.Thread(
                        target=self.app.rebind_voice_hotkey,
                        name="voice-hotkey-rebind",
                        daemon=True,
                    ).start()
                self.app._publish_voice()
                new_engine = tuple(self.app.settings.get(key) for key in engine_keys)
                released_local = False
                if old_engine[0] == "local" and new_engine[0] != "local":
                    self.app.release_local_models()
                    released_local = True
                elif (
                    old_engine[0] == "local"
                    and new_engine[0] == "local"
                    and old_engine[1] != new_engine[1]
                ):
                    self.app.release_local_models([str(old_engine[1])])
                    released_local = True
                if old_engine != new_engine:
                    self.app.llm_status = "loading"
                    self.app.llm_message = (
                        "设置已更新，生成时检查 API…"
                        if self.app.settings.get("llm_provider") != "local"
                        else "设置已更新，生成时检查本地模型…"
                    )
                    self.app._publish_status()
                _, startup_error = self.app.apply_startup_setting()
                self._json(
                    {
                        "ok": True,
                        "settings": config.public_settings(self.app.settings),
                        "released_local": released_local,
                        "startup_error": startup_error,
                    }
                )
            except Exception as exc:
                self._error(f"保存设置失败：{exc}", 500)
            return
        if path == "/api/mode":
            mode = str(body.get("mode", "") or "")
            if mode not in ("call", "translate", "voice", "meeting"):
                self._error("mode 必须是 call / translate / voice / meeting", 400)
                return
            if self.app.mode == mode:
                if self.app.settings.get("app_mode") != mode:
                    self.app.settings = config.save_settings({**self.app.settings, "app_mode": mode})
                    self.app.brain.set_settings(self.app.settings)
                self._json({"ok": True, "mode": mode})
                return
            if self.app.capture_running:
                self.app.stop_capture()
            self.app.mode = mode
            self.app._cancel_continuation(clear=True)
            if mode != "translate":
                self.app.stop_overlay()
            elif self.app.settings.get("overlay_enabled", True):
                self.app.start_overlay()
                self.app.bus.publish({"type": "overlay", "data": {"running": self.app.overlay_running()}})
            self.app.settings = config.save_settings({**self.app.settings, "app_mode": mode})
            self.app.brain.set_settings(self.app.settings)
            self.app.clear_session()
            self.app._publish_status()
            self.app.bus.publish({"type": "mode", "data": {"mode": mode}})
            self._json({"ok": True, "mode": mode})
            return
        if path == "/api/ui/navigate":
            suffix = str(body.get("suffix", "") or "")
            if not suffix.startswith("/"):
                suffix = "/" + suffix
            self.app.bus.publish({"type": "ui_navigate", "data": {"suffix": suffix}})
            self._json({"ok": True})
            return
        if path == "/api/diarization/download":
            model_id = str(
                body.get("model")
                or self.app.settings.get("diarization_model", diarization.DEFAULT_MODEL)
                or diarization.DEFAULT_MODEL
            )
            result = diarization.start_download(model_id)
            self._json(
                {
                    "ok": bool(result.get("ok")),
                    "state": result.get("state") or diarization.public_state(model_id),
                    "diarization": diarization.public_state(model_id),
                }
            )
            return
        if path == "/api/diarization/download-component":
            result = diarization.start_component_download(
                str(body.get("component", "") or "")
            )
            if not result.get("ok"):
                self._error(str(result.get("error", "下载失败")), 409)
                return
            self._json(result)
            return
        if path == "/api/overlay":
            action = str(body.get("action", "toggle") or "toggle")
            running = self.app.overlay_running()
            if action == "toggle":
                action = "stop" if running else "start"
            if action == "start":
                self.app.start_overlay()
                enabled = True
            elif action == "stop":
                self.app.stop_overlay()
                enabled = False
            else:
                self._error("action 必须是 start / stop / toggle", 400)
                return
            self.app.settings = config.save_settings({**self.app.settings, "overlay_enabled": enabled})
            running = self.app.overlay_running()
            self.app.bus.publish({"type": "overlay", "data": {"running": running}})
            self._json({"ok": True, "running": running})
            return
        if path == "/api/tts":
            action = str(body.get("action", "") or "")
            if action == "stop":
                self.app.stop_tts()
                self._json({"ok": True, "running": False})
                return
            text = str(body.get("text", "") or "").strip()
            if not text:
                self._error("text 不能为空", 400)
                return
            lang = str(body.get("lang", "en") or "en")
            ok = self.app.speak_text(text, lang)
            self._json({"ok": ok, "running": self.app.tts_running()})
            return
        if path == "/api/continue":
            fragment = str(body.get("fragment", "") or "").strip()
            threading.Thread(
                target=self.app.generate_continuation,
                kwargs={"manual": True, "fragment": fragment},
                daemon=True,
            ).start()
            self._json({"ok": True})
            return
        if path == "/api/suggest":
            opening = bool(body.get("opening", False))
            threading.Thread(
                target=self.app.generate_suggestion,
                kwargs={"auto": False, "opening": opening, "manual": True},
                daemon=True,
            ).start()
            self._json({"ok": True})
            return
        if path == "/api/test-llm":
            try:
                result = self.app.brain.test()
                if result.get("options"):
                    self.app.llm_status = "ready"
                    label = "DeepSeek" if self.app.settings.get("llm_provider") == "deepseek" else "API"
                    self.app.llm_message = (
                        f"{label} API 就绪（{self.app.settings.get('api_model')}）"
                        if self.app.settings.get("llm_provider") != "local"
                        else f"语言模型就绪（{self.app.settings.get('llm_model')}）"
                    )
                    self.app._publish_status()
                self._json({"ok": True, "result": result})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/restart":
            try:
                _spawn_restart_helper(self.app.server_port)
            except Exception as exc:
                self._error(f"无法启动重启进程：{exc}", 500)
                return
            self._json({"ok": True, "admin": bool(config.load_settings().get("run_as_admin", False))})

            def restart_all() -> None:
                time.sleep(0.35)
                try:
                    self.server.app.shutdown()  # type: ignore[attr-defined]
                finally:
                    self.server.shutdown()

            threading.Thread(target=restart_all, daemon=True).start()
            return
        if path == "/api/shutdown":
            self._json({"ok": True})

            def stop_all() -> None:
                try:
                    self.server.app.shutdown()  # type: ignore[attr-defined]
                finally:
                    self.server.shutdown()

            threading.Thread(target=stop_all, daemon=True).start()
            return
        self._error("not found", 404)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._handle_get()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self._error(f"服务器内部错误：{exc}", 500)
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._handle_post()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self._error(f"服务器内部错误：{exc}", 500)
            except Exception:
                pass

    def _serve_file(self, target: Path) -> None:
        if not target.exists() or not target.is_file():
            self._error("not found", 404)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif target.suffix == ".ico":
            ctype = "image/x-icon"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache" if target.suffix == ".html" else "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = self.app.bus.subscribe()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.write(
                ("data: " + json.dumps({"type": "hello", "data": {"server_time": time.time()}}) + "\n\n").encode("utf-8")
            )
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.bus.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def _find_edge() -> Optional[Path]:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _decode_ui_port(token: str) -> int:
    """Decode the zero-width port token used in the browser window title."""
    if not token or any(char not in ("\u200b", "\u2060") for char in token):
        return 0
    try:
        return int("".join("1" if char == "\u200b" else "0" for char in token), 2)
    except ValueError:
        return 0


def _close_stale_ui(port: int) -> None:
    """Close Lan.Assistant windows left on an older port after a port change."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buffer, length + 2)
            title = buffer.value.strip()
            if APP_WINDOW_TITLE_MARKER not in title or "lan.assistant" not in title.lower():
                return True
            parts = title.split(APP_WINDOW_TITLE_MARKER)
            if len(parts) < 3:
                stale = len(parts) == 2
            else:
                actual_port = _decode_ui_port(parts[1])
                stale = actual_port != int(port) if actual_port else bool(parts[1])
            if not stale:
                return True
            klass = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, klass, 128)
            if klass.value in ("Chrome_WidgetWin_1", "MozillaWindowClass"):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True

        user32.EnumWindows(_callback, 0)
    except Exception:
        pass


def _focus_existing_ui(port: int = 0) -> bool:
    """Bring an already-open Lan.Assistant window to the front.

    Clicking the tray page/settings entry used to spawn another Edge --app
    window every time.  Match the visible browser/app window by title, focus it
    and let the caller skip creating a duplicate.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buffer, length + 2)
            title = buffer.value.strip()
            if APP_WINDOW_TITLE_MARKER not in title:
                return True
            if "lan.assistant" not in title.lower():
                return True
            parts = title.split(APP_WINDOW_TITLE_MARKER)
            if len(parts) < 3:
                return True
            actual_port = _decode_ui_port(parts[1])
            if not actual_port or (port and actual_port != int(port)):
                return True
            klass = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, klass, 128)
            # Browser app windows are Chromium/Chrome_WidgetWin_1 (Edge/Chrome)
            # or MozillaWindowClass (Firefox).  Avoid focusing a terminal or
            # another random process that happens to mention the app name.
            if klass.value not in ("Chrome_WidgetWin_1", "MozillaWindowClass"):
                return True
            found.append(int(hwnd))
            return False

        user32.EnumWindows(_callback, 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return True
    except Exception:
        return False


def _kill_stale_voice_overlays() -> None:
    """Remove an orphaned Tk voice window from an earlier app process."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            # Hidden windows (after a cancel) still need cleanup.
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buffer, length + 2)
            title = buffer.value
            if "Lan.Assistant" not in title or "语音输入" not in title:
                return True
            klass = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, klass, 128)
            if "Tk" not in klass.value:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value or int(pid.value) == os.getpid():
                return True
            handle = kernel32.OpenProcess(0x0001, False, pid.value)  # PROCESS_TERMINATE
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(_callback, 0)
    except Exception:
        pass
    # A hidden Tk window can outlive its old server process and no longer have
    # a discoverable title.  Remove any orphaned overlay helper as a fallback.
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*voice_overlay.py*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                ),
            ],
            capture_output=True,
            timeout=5,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def _spawn_restart_helper(port: int) -> None:
    """Start a detached process that relaunches the app after shutdown."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--restart-helper"]
    else:
        command = [sys.executable, str(config.ROOT / "tray.py"), "--restart-helper"]
    # The helper inherits our integrity; it decides whether to request UAC.
    command.append("--admin" if config.load_settings().get("run_as_admin", False) else "--normal")
    command.extend(["--port", str(int(port))])
    subprocess.Popen(
        command,
        cwd=str(config.ROOT),
        close_fds=True,
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_existing_ui(port: int, suffix: str) -> None:
    if not suffix or suffix == "/":
        return
    try:
        payload = json.dumps({"suffix": suffix}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ui/navigate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=1.0).read()
    except Exception:
        pass


def open_ui(port: int, suffix: str = "") -> None:
    _close_stale_ui(port)
    if _focus_existing_ui(port):
        _notify_existing_ui(port, suffix)
        return
    url = f"http://127.0.0.1:{port}{suffix}"
    edge = _find_edge()
    try:
        if edge:
            subprocess.Popen(
                [
                    str(edge),
                    f"--app={url}",
                    "--window-size=1440,940",
                    "--window-position=60,30",
                    "--no-first-run",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(url)
    except Exception:
        webbrowser.open(url)


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open", action="store_true", help="open the UI after startup")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not STATIC_DIR.exists():
        print(f"Missing static directory: {STATIC_DIR}")
        return 1

    # If a previous instance recorded its runtime port, reuse it.
    existing = config.read_runtime_port()
    if existing:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{existing}/api/health", timeout=0.8
            ) as response:
                if response.status == 200:
                    print(f"{APP_NAME} 已在运行：http://127.0.0.1:{existing}")
                    if not args.no_browser:
                        open_ui(existing)
                    return 0
        except Exception:
            pass

    settings = config.load_settings()
    preferred = args.port or int(settings.get("server_port", 0) or 0)
    port = config.resolve_server_port(preferred)

    app = App()
    app.server_port = port
    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError:
        port = config.resolve_server_port(0)
        app.server_port = port
        server = Server(("127.0.0.1", port), Handler)
    server.app = app  # type: ignore[attr-defined]
    app.start_background()
    print(f"\n{APP_NAME} v{APP_VERSION}  ->  http://127.0.0.1:{port}\n")
    print("音频只在本机处理；不会上传，也不会保存录音。\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: open_ui(port)).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        app.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

