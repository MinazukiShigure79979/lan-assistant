"""Faster-Whisper wrapper with GPU auto-fallback and hallucination filtering."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

import config
from cuda_env import setup_cuda_dlls

from faster_whisper import WhisperModel  # noqa: E402

SAMPLE_RATE = 16000

_HALLUCINATIONS = {
    "thank you.",
    "thanks for watching.",
    "thank you for watching.",
    "please subscribe.",
    "like and subscribe.",
    "bye.",
    "you",
    "i'm sorry.",
    "sorry.",
    "okay.",
    "ok.",
    "yeah.",
    "yes.",
    "no.",
    "hmm.",
    "uh.",
    "um.",
    "so",
    "and",
    "the",
    "...",
    "music",
    "[music]",
    "(music)",
    "♪",
}


def _script_language(text: str) -> str:
    """Return a script-based language hint for auto-detected text."""
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    return ""


def _clean_text(text: str, language: str = "en", strip_cjk: Optional[bool] = None) -> str:
    text = text.replace("\u200b", " ").replace("\xa0", " ")
    text = re.sub(r"\[(?:blank_audio|silence|music|applause|laughter)\]", " ", text, flags=re.I)
    text = re.sub(r"\((?:silence|music|applause|laughter)\)", " ", text, flags=re.I)
    if strip_cjk is None:
        strip_cjk = language == "en"
    if strip_cjk:
        # Explicit English mode: drop stray CJK runs, which Whisper sometimes
        # hallucinates when it hears background audio.  Auto mode must keep
        # them: a short mixed-language segment can be labelled "en" even when
        # it is actually Chinese.
        text = re.sub(r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([.!?])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\.{3,}", "...", text)
    if language == "en" and text:
        text = re.sub(r"\bi\b", "I", text)
        text = text[0].upper() + text[1:]
        if text[-1] not in ".!?":
            text += "."
    return text.strip()


def _looks_like_hallucination(
    text: str,
    duration_s: float,
    avg_logprob: float,
    no_speech_prob: float,
    language: str = "en",
) -> bool:
    low = text.lower().strip(" .!?,")
    if not low:
        return True
    if language != "en":
        # Do not apply English word heuristics to Japanese/Korean/auto text.
        if avg_logprob < -1.25 and no_speech_prob > 0.65:
            return True
        if duration_s > 2.0 and len(low) <= 1:
            return True
        return False
    words = re.findall(r"[a-z']+", low)
    if duration_s < 6.0 and len(words) >= 2 and len(set(words)) == 1:
        return True
    if duration_s < 4.0 and len(words) <= 4 and set(words) <= {"you", "the", "a", "and", "so", "uh", "um", "oh"}:
        return True
    if duration_s < 2.2 and low in {h.strip(" .!?,") for h in _HALLUCINATIONS}:
        return True
    if avg_logprob < -1.25 and no_speech_prob > 0.65:
        return True
    if len(words) <= 3 and avg_logprob < -0.9 and no_speech_prob > 0.4:
        return True
    if duration_s > 2.0 and len(low) <= 2:
        return True
    return False


class Transcriber:
    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        on_status: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.model_path = str(model_path)
        self.requested_device = device or "auto"
        self.on_status = on_status or (lambda scope, message: None)
        self.model: Optional[WhisperModel] = None
        self.device = "cpu"
        self.compute_type = "int8"
        self.error = ""
        self.load_ms = 0
        self._lock = threading.Lock()
        self._loaded = threading.Event()

    def load(self) -> None:
        started = time.perf_counter()
        path = Path(self.model_path)
        if not path.exists():
            fallback = Path(config.ASR_MODEL_DIR)
            if fallback.exists():
                path = fallback
                self.model_path = str(fallback)
        if not path.exists():
            self.error = f"语音识别模型不存在：{path}"
            self.on_status("error", self.error)
            self._loaded.set()
            return
        model_bin = path / "model.bin" if path.is_dir() else path
        if path.is_dir() and (not model_bin.exists() or model_bin.stat().st_size < 100 * 1024 * 1024):
            self.error = "语音识别模型文件不完整，请重新运行 setup.ps1 下载模型。"
            self.on_status("error", self.error)
            self._loaded.set()
            return

        attempts: list[tuple[str, str]] = []
        if self.requested_device in ("auto", "cuda"):
            setup_cuda_dlls()
        if self.requested_device in ("auto", "cuda"):
            attempts.append(("cuda", "float16"))
        attempts.append(("cpu", "int8"))
        last_error = ""
        for device, compute_type in attempts:
            try:
                self.on_status("loading", f"正在加载语音识别模型（{device.upper()}）…")
                self.model = WhisperModel(
                    self.model_path,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=8,
                    num_workers=1,
                    download_root=None,
                )
                self.device = device
                self.compute_type = compute_type
                self.load_ms = int((time.perf_counter() - started) * 1000)
                label = Path(self.model_path).name or "whisper"
                self.on_status(
                    "ready",
                    f"语音识别就绪（{device.upper()} / {compute_type} / {label}）",
                )
                self._loaded.set()
                return
            except Exception as exc:
                last_error = f"{device}: {exc}"
                self.model = None
                continue
        self.error = f"语音识别模型加载失败：{last_error}"
        self.on_status("error", self.error)
        self._loaded.set()

    def wait_loaded(self, timeout: float = 120.0) -> bool:
        return self._loaded.wait(timeout) and self.model is not None

    def warmup(self) -> None:
        if self.model is None:
            return
        try:
            silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
            list(self.model.transcribe(silence, language="en", beam_size=1, vad_filter=False)[0])
        except Exception:
            pass

    def transcribe(
        self,
        pcm: np.ndarray,
        partial: bool = False,
        initial_prompt: str = "",
        language: str = "en",
        fast: bool = False,
        hotwords: str = "",
    ) -> dict[str, Any]:
        if self.model is None:
            return {"text": "", "error": self.error or "模型未就绪", "latency_ms": 0, "audio_ms": 0}
        if pcm.dtype != np.float32:
            audio = pcm.astype(np.float32) / 32768.0
        else:
            audio = pcm
        if audio.size == 0:
            return {"text": "", "latency_ms": 0, "audio_ms": 0}
        requested = language if language in ("en", "ja", "ko", "zh", "yue") else None
        audio_ms = int(audio.size * 1000 / SAMPLE_RATE)
        started = time.perf_counter()
        with self._lock:
            try:
                segments, info = self.model.transcribe(
                    audio,
                    language=requested,
                    task="transcribe",
                    beam_size=1 if (partial or fast) else 5,
                    best_of=1 if (partial or fast) else 5,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    without_timestamps=True,
                    initial_prompt=initial_prompt or None,
                    hotwords=hotwords or None,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                )
                rows = list(segments)
            except Exception as exc:
                return {
                    "text": "",
                    "error": f"识别失败：{exc}",
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "audio_ms": audio_ms,
                }
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw_text = " ".join(seg.text for seg in rows)
        detected = str(getattr(info, "language", "") or requested or "en")
        if requested is None and detected in ("", "en"):
            # Auto mode is intentionally all-language.  Short segments are
            # often mislabelled "en"; use strong scripts as a safety net so
            # Chinese/Japanese/Korean text is not treated as English.
            detected = _script_language(raw_text) or detected
        text = _clean_text(raw_text, detected, strip_cjk=(requested == "en"))
        if not text:
            return {
                "text": "",
                "language": detected,
                "latency_ms": latency_ms,
                "audio_ms": audio_ms,
                "device": self.device,
            }
        avg_logprob = sum(float(getattr(s, "avg_logprob", 0.0) or 0.0) for s in rows) / max(1, len(rows))
        no_speech = max((float(getattr(s, "no_speech_prob", 0.0) or 0.0) for s in rows), default=0.0)
        last_prob = float(getattr(rows[-1], "avg_logprob", -1.0) or -1.0) if rows else -1.0
        if _looks_like_hallucination(text, audio_ms / 1000.0, avg_logprob, no_speech, detected):
            return {
                "text": "",
                "filtered": True,
                "language": detected,
                "latency_ms": latency_ms,
                "audio_ms": audio_ms,
                "device": self.device,
                "avg_logprob": round(avg_logprob, 3),
            }
        return {
            "text": text,
            "language": detected,
            "latency_ms": latency_ms,
            "audio_ms": audio_ms,
            "device": self.device,
            "avg_logprob": round(avg_logprob, 3),
            "no_speech_prob": round(no_speech, 3),
            "last_logprob": round(last_prob, 3),
        }
