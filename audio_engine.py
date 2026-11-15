"""WASAPI capture (system loopback + microphone), VAD and segmenting.

The other person's voice is captured from the Windows render endpoint that
WeChat plays through.  The user's voice is captured from a normal microphone.
Both streams are converted to 16 kHz mono and cut into utterances by WebRTC VAD.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pyaudiowpatch as pyaudio
import webrtcvad

from audio_lock import AUDIO_LOCK

import sys

SILERO_MODEL = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "models" / "silero_vad.onnx"

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
MIN_SPEECH_MS = 320
DEFAULT_SILENCE_MS = 750
DEFAULT_MAX_SEGMENT_S = 20
PARTIAL_EVERY_MS = 800
PARTIAL_WINDOW_MS = 6000


def _device_dict(info: dict[str, Any], is_loopback: bool, is_default: bool = False) -> dict[str, Any]:
    name = str(info.get("name", "Unknown device")).strip()
    return {
        "index": int(info.get("index", -1)),
        "name": name,
        "channels": int(info.get("maxInputChannels", 0) or 0),
        "rate": int(round(float(info.get("defaultSampleRate", 48000) or 48000))),
        "is_loopback": bool(is_loopback),
        "is_default": bool(is_default),
    }


def list_devices() -> dict[str, Any]:
    """Return capture devices, serialized against other PyAudio lifecycles."""
    with AUDIO_LOCK:
        return _list_devices_locked()


def _list_devices_locked() -> dict[str, Any]:
    """Return capture devices.  Safe to call while capture is running."""
    pa = pyaudio.PyAudio()
    loopback: list[dict[str, Any]] = []
    microphones: list[dict[str, Any]] = []
    default_loop: Optional[dict[str, Any]] = None
    default_mic: Optional[dict[str, Any]] = None
    error = ""
    try:
        try:
            info = pa.get_default_wasapi_loopback()
            default_loop = _device_dict(info, True, True)
        except Exception as exc:  # no loopback endpoint
            error = str(exc)
        try:
            info = pa.get_default_input_device_info()
            default_mic = _device_dict(info, False, True)
        except Exception as exc:
            error = error or str(exc)

        seen_loop: set[tuple[int, str]] = set()
        seen_mic: set[tuple[int, str]] = set()
        for index in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(index)
            except Exception:
                continue
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            is_loop = bool(info.get("isLoopbackDevice"))
            item = _device_dict(info, is_loop, False)
            key = (item["index"], item["name"])
            if is_loop:
                if key not in seen_loop:
                    seen_loop.add(key)
                    loopback.append(item)
            elif key not in seen_mic:
                seen_mic.add(key)
                microphones.append(item)
        loopback.sort(key=lambda d: (not d["name"].lower().startswith("speakers"), d["name"].lower()))
        microphones.sort(key=lambda d: d["name"].lower())
    finally:
        try:
            pa.terminate()
        except Exception:
            pass

    return {
        "loopback": loopback,
        "microphones": microphones,
        "default_loopback": default_loop,
        "default_mic": default_mic,
        "error": error,
    }


def _match(devices: list[dict[str, Any]], saved: Any) -> Optional[dict[str, Any]]:
    if not isinstance(saved, dict):
        return None
    name = str(saved.get("name", "") or "")
    index = saved.get("index")
    if name:
        for dev in devices:
            if dev["name"] == name:
                return dev
    if isinstance(index, int):
        for dev in devices:
            if dev["index"] == index:
                return dev
    return None


def resolve_devices(settings: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], dict[str, Any]]:
    devices = list_devices()
    other = _match(devices["loopback"], settings.get("other_device"))
    if other is None:
        other = devices.get("default_loopback")
        if other is None and devices["loopback"]:
            other = devices["loopback"][0]
    mic = _match(devices["microphones"], settings.get("mic_device"))
    if settings.get("mic_enabled", True) is False:
        mic = None
    elif mic is None:
        mic = devices.get("default_mic")
        if mic is None and devices["microphones"]:
            mic = devices["microphones"][0]
    return other, mic, devices


def _lowpass_taps(factor: int) -> np.ndarray:
    # Small anti-alias FIR before integer decimation (speech only needs ~7 kHz).
    taps = max(9, factor * 4 + 1)
    n = np.arange(taps) - (taps - 1) / 2.0
    cutoff = 0.42 / factor
    h = np.sinc(2 * cutoff * n) * np.hamming(taps)
    total = float(h.sum())
    return h / total if total else np.array([1.0])


def to_16k_mono(data: bytes, channels: int, rate: int, gain: float = 1.0) -> np.ndarray:
    x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if channels > 1:
        usable = (len(x) // channels) * channels
        if usable <= 0:
            return np.empty(0, dtype=np.int16)
        x = x[:usable].reshape(-1, channels).mean(axis=1)
    if gain and gain != 1.0:
        x = x * float(gain)
    if rate != SAMPLE_RATE and len(x):
        if rate % SAMPLE_RATE == 0 and rate // SAMPLE_RATE <= 8:
            factor = rate // SAMPLE_RATE
            x = np.convolve(x, _lowpass_taps(factor), mode="same")
            x = x[::factor]
        else:
            target = max(1, int(round(len(x) * SAMPLE_RATE / float(rate))))
            x = np.interp(
                np.linspace(0.0, len(x) - 1.0, target, dtype=np.float32),
                np.arange(len(x), dtype=np.float32),
                x,
            ).astype(np.float32)
    return np.clip(x, -32768, 32767).astype(np.int16)


class Segmenter:
    """Energy/VAD state machine that emits whole utterances."""

    def __init__(
        self,
        speaker: str,
        on_segment: Callable[[str, np.ndarray, dict[str, Any]], None],
        on_partial: Callable[[str, np.ndarray, dict[str, Any]], None],
        vad_aggressiveness: int = 2,
        silence_ms: int = DEFAULT_SILENCE_MS,
        max_segment_s: int = DEFAULT_MAX_SEGMENT_S,
        partial_enabled: bool = True,
        partial_every_ms: int = PARTIAL_EVERY_MS,
        partial_window_ms: int = PARTIAL_WINDOW_MS,
    ) -> None:
        self.speaker = speaker
        self.on_segment = on_segment
        self.on_partial = on_partial
        self.vad = webrtcvad.Vad(max(0, min(3, int(vad_aggressiveness))))
        self.silero: Any = None
        self.vad_kind = "webrtc"
        self._silero_buf = np.empty(0, dtype=np.float32)
        self._silero_state = np.zeros((2, 1, 128), dtype=np.float32)
        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            self.silero = ort.InferenceSession(
                str(SILERO_MODEL),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self.vad_kind = "silero"
        except Exception:
            self.silero = None
            self.vad_kind = "webrtc"
        self.silence_ms = max(150, int(silence_ms))
        self.max_segment_ms = max(2000, int(max_segment_s) * 1000)
        self.partial_enabled = bool(partial_enabled)
        self.partial_every_ms = max(200, int(partial_every_ms))
        self.partial_window_ms = max(1000, int(partial_window_ms))
        self.preroll: deque[np.ndarray] = deque(maxlen=10)
        self.frames: list[np.ndarray] = []
        self.active = False
        self.speech_ms = 0
        self.silence_ms_now = 0
        self.total_ms = 0
        self.started_at = 0.0
        self.seq = 0
        self._frames_since_partial = 0
        self._lock = threading.Lock()
        self.noise_floor = 0.0018

    def _speech_decision(self, frame: np.ndarray, rms: float) -> bool:
        if rms < 0.0015:
            return False
        if self.silero is None:
            try:
                return bool(self.vad.is_speech(frame.tobytes(), SAMPLE_RATE))
            except Exception:
                return False
        # Silero v5 operates on 512-sample (32 ms) windows at 16 kHz.  Feed it
        # a continuous buffer while the segmenter works in 30 ms frames.
        self._silero_buf = np.concatenate(
            [self._silero_buf, frame.astype(np.float32) / 32768.0]
        )
        last_prob = 0.0
        ran = False
        while self._silero_buf.size >= 512:
            chunk = self._silero_buf[:512]
            self._silero_buf = self._silero_buf[512:]
            try:
                output, self._silero_state = self.silero.run(
                    None,
                    {
                        "input": chunk.reshape(1, 512).astype(np.float32),
                        "state": self._silero_state,
                        "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                    },
                )
                last_prob = float(np.asarray(output).reshape(-1)[0])
                ran = True
            except Exception:
                # A broken VAD must never stop capture; fall back for this frame.
                try:
                    return bool(self.vad.is_speech(frame.tobytes(), SAMPLE_RATE))
                except Exception:
                    return False
        if not ran:
            return False
        return last_prob >= 0.45

    def process(self, frame: np.ndarray) -> None:
        if frame.size != FRAME_SAMPLES:
            return
        with self._lock:
            rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
            is_speech = False
            gate = max(0.0022, min(0.012, self.noise_floor * 2.1))
            if rms >= gate:
                is_speech = self._speech_decision(frame, rms)
            elif not self.active:
                self.noise_floor = max(0.0008, min(0.015, self.noise_floor * 0.98 + rms * 0.02))

            if not self.active:
                self.preroll.append(frame)
                if is_speech:
                    self.active = True
                    self.seq += 1
                    self.frames = list(self.preroll)
                    self.speech_ms = FRAME_MS
                    self.silence_ms_now = 0
                    self.total_ms = FRAME_MS * len(self.frames)
                    self.started_at = time.time() - self.total_ms / 1000.0
                    self._frames_since_partial = 0
                return

            self.frames.append(frame)
            self.total_ms += FRAME_MS
            self._frames_since_partial += 1
            if is_speech:
                self.speech_ms += FRAME_MS
                self.silence_ms_now = 0
            else:
                self.silence_ms_now += FRAME_MS

            if self.silence_ms_now >= self.silence_ms and self.speech_ms >= MIN_SPEECH_MS:
                self._finalize("silence")
                return
            if self.total_ms >= self.max_segment_ms:
                self._finalize("max_length")
                return

            if (
                self.partial_enabled
                and self.speech_ms >= 900
                and self.silence_ms_now < 180
                and self._frames_since_partial * FRAME_MS >= self.partial_every_ms
            ):
                self._frames_since_partial = 0
                window = max(1, self.partial_window_ms // FRAME_MS)
                pcm = np.concatenate(self.frames[-window:]).copy()
                meta = {
                    "seq": self.seq,
                    "duration_ms": int(len(pcm) * 1000 / SAMPLE_RATE),
                    "started_at": self.started_at,
                    "reason": "partial",
                }
                try:
                    self.on_partial(self.speaker, pcm, meta)
                except Exception:
                    pass

    def _finalize(self, reason: str) -> None:
        if not self.frames:
            self._reset()
            return
        frames = list(self.frames)
        speech_ms = self.speech_ms
        silence_ms = self.silence_ms_now
        seq = self.seq
        started_at = self.started_at
        self._reset()
        if speech_ms < MIN_SPEECH_MS:
            return
        # Keep 180 ms of tail silence; Whisper likes a little context.
        trim_ms = max(0, silence_ms - 180)
        trim = int(trim_ms * SAMPLE_RATE / 1000)
        pcm = np.concatenate(frames)
        if trim > 0 and len(pcm) > trim + SAMPLE_RATE // 4:
            pcm = pcm[:-trim]
        pcm = pcm.astype(np.float32)
        peak = float(np.max(np.abs(pcm))) if len(pcm) else 0.0
        if 0.01 < peak < 0.28:
            pcm = pcm * min(4.0, 0.55 / peak)
        pcm = np.clip(pcm, -32768, 32767).astype(np.int16)
        if len(pcm) < int(SAMPLE_RATE * 0.3):
            return
        meta = {
            "seq": seq,
            "duration_ms": int(len(pcm) * 1000 / SAMPLE_RATE),
            "started_at": started_at,
            "reason": reason,
        }
        try:
            self.on_segment(self.speaker, pcm, meta)
        except Exception:
            pass

    def _reset(self) -> None:
        self.frames = []
        self.active = False
        self.speech_ms = 0
        self.silence_ms_now = 0
        self.total_ms = 0
        self.preroll.clear()
        self._frames_since_partial = 0

    def flush(self) -> None:
        with self._lock:
            if self.active:
                self._finalize("stop")


class EchoGate:
    """A tiny half-duplex gate driven by far-end (loopback) energy.

    It is not a full acoustic echo canceller, but it prevents the microphone
    from transcribing the speakers while the other person is talking.
    """

    def __init__(self) -> None:
        self._last = 0.0
        self._lock = threading.Lock()

    def mark(self, level: float) -> None:
        if level >= 0.012:
            with self._lock:
                self._last = time.monotonic()

    def active(self, window: float = 0.28) -> bool:
        with self._lock:
            return (time.monotonic() - self._last) < window


class SourceCapture:
    """One capture stream (loopback or microphone) running in its own thread.

    All streams share one PyAudio/PortAudio instance.  Opening and closing two
    independent PyAudio instances from two threads can crash the native audio
    stack on Windows, so streams are opened sequentially and closed from the
    thread that reads them.
    """

    def __init__(
        self,
        pa: Any,
        speaker: str,
        device: dict[str, Any],
        on_segment: Callable[[str, np.ndarray, dict[str, Any]], None],
        on_partial: Callable[[str, np.ndarray, dict[str, Any]], None],
        on_level: Callable[[str, float], None],
        on_error: Callable[[str, str], None],
        vad_aggressiveness: int = 2,
        silence_ms: int = DEFAULT_SILENCE_MS,
        max_segment_s: int = DEFAULT_MAX_SEGMENT_S,
        partial_enabled: bool = True,
        mic_gain: float = 1.0,
        echo_gate: Any = None,
        echo_guard: bool = False,
        partial_every_ms: int = PARTIAL_EVERY_MS,
        partial_window_ms: int = PARTIAL_WINDOW_MS,
    ) -> None:
        self._pa = pa
        self.speaker = speaker
        self.device = device
        self.on_level = on_level
        self.on_error = on_error
        self.mic_gain = float(mic_gain) if speaker == "me" else 1.0
        self.echo_gate = echo_gate
        self.echo_guard = bool(echo_guard) and speaker == "me"
        self.segmenter = Segmenter(
            speaker=speaker,
            on_segment=on_segment,
            on_partial=on_partial,
            vad_aggressiveness=vad_aggressiveness,
            silence_ms=silence_ms,
            max_segment_s=max_segment_s,
            partial_enabled=partial_enabled,
            partial_every_ms=partial_every_ms,
            partial_window_ms=partial_window_ms,
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._stream: Any = None
        self._rate = int(device.get("rate", 48000) or 48000)
        self._channels = 1
        self._pending = np.empty(0, dtype=np.int16)
        self._last_level = 0.0
        self._last_debug = 0.0
        self._last_frame_at = time.monotonic()
        self.open_error = ""

    def open(self) -> bool:
        native_channels = max(1, int(self.device.get("channels", 1) or 1))
        if self.speaker == "me":
            channel_options = [1, native_channels] if native_channels > 1 else [1]
        else:
            channel_options = [native_channels, min(2, native_channels)]
        channel_options = list(dict.fromkeys([c for c in channel_options if c > 0]))
        rate_options = list(dict.fromkeys([self._rate, 48000, 44100]))
        last_error = ""
        for try_rate in rate_options:
            for try_channels in channel_options:
                try:
                    frames = max(1, int(try_rate * FRAME_MS / 1000))
                    self._stream = self._pa.open(
                        format=pyaudio.paInt16,
                        channels=try_channels,
                        rate=try_rate,
                        input=True,
                        input_device_index=int(self.device["index"]),
                        frames_per_buffer=frames,
                    )
                    self._rate = try_rate
                    self._channels = try_channels
                    return True
                except Exception as exc:
                    last_error = str(exc)
        self.open_error = last_error or "无法打开音频设备"
        return False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.speaker}", daemon=True)
        self._watchdog = threading.Thread(target=self._watch_idle, name=f"idle-{self.speaker}", daemon=True)
        self._thread.start()
        self._watchdog.start()

    def _watch_idle(self) -> None:
        # WASAPI loopback can stop delivering frames when the renderer goes
        # idle.  Without this, the last sentence would stay stuck as a partial
        # transcript forever.  Flush the segmenter after a quiet gap.
        while not self._stop.is_set():
            if self._stop.wait(0.2):
                break
            if time.monotonic() - self._last_frame_at > 1.25:
                try:
                    self.segmenter.flush()
                except Exception:
                    pass
                self._last_frame_at = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        # Unblock a read() that is waiting on an idle WASAPI endpoint.  The
        # capture thread will still close the stream in its finally block.
        stream = self._stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
        for thread in (self._thread, self._watchdog):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)

    def _run(self) -> None:
        if self._stream is None:
            return
        last_error = ""
        try:
            frames_per_chunk = max(1, int(self._rate * FRAME_MS / 1000))
            while not self._stop.is_set():
                try:
                    data = self._stream.read(frames_per_chunk, exception_on_overflow=False)
                except Exception as exc:
                    if not self._stop.is_set():
                        last_error = str(exc)
                    break
                if not data:
                    continue
                self._last_frame_at = time.monotonic()
                pcm = to_16k_mono(data, self._channels, self._rate, gain=self.mic_gain)
                if pcm.size == 0:
                    continue
                self._pending = np.concatenate([self._pending, pcm])
                while self._pending.size >= FRAME_SAMPLES:
                    frame = self._pending[:FRAME_SAMPLES].copy()
                    self._pending = self._pending[FRAME_SAMPLES:]
                    level = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
                    if self.speaker == "other" and self.echo_gate is not None:
                        self.echo_gate.mark(level)
                    if self.echo_guard and self.echo_gate is not None and self.echo_gate.active():
                        continue
                    self.segmenter.process(frame)
                    now = time.monotonic()
                    if now - self._last_debug >= 2.0:
                        self._last_debug = now
                        print(
                            f"[audio] {self.speaker} level={level:.4f} "
                            f"speech_ms={self.segmenter.speech_ms} active={self.segmenter.active}",
                            flush=True,
                        )
                    if now - self._last_level >= 0.1:
                        self._last_level = now
                        try:
                            self.on_level(self.speaker, min(1.0, level * 5.0))
                        except Exception:
                            pass
        except Exception as exc:
            if not self._stop.is_set():
                last_error = str(exc)
        finally:
            try:
                self.segmenter.flush()
            except Exception:
                pass
            stream = self._stream
            self._stream = None
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if last_error and not self._stop.is_set():
                try:
                    self.on_error(self.speaker, f"{self.device.get('name', '音频设备')}：{last_error}")
                except Exception:
                    pass


class AudioEngine:
    def __init__(
        self,
        on_segment: Callable[[str, np.ndarray, dict[str, Any]], None],
        on_partial: Callable[[str, np.ndarray, dict[str, Any]], None],
        on_level: Callable[[str, float], None],
        on_error: Callable[[str, str], None],
    ) -> None:
        self.on_segment = on_segment
        self.on_partial = on_partial
        self.on_level = on_level
        self.on_error = on_error
        self.sources: list[SourceCapture] = []
        self._lock = threading.Lock()
        self._pa: Any = None
        self.echo_gate = EchoGate()

    @property
    def running(self) -> bool:
        return bool(self.sources)

    def start(self, other: Optional[dict[str, Any]], mic: Optional[dict[str, Any]], settings: dict[str, Any]) -> None:
        with AUDIO_LOCK:
            self._start_locked(other, mic, settings)

    def _start_locked(self, other: Optional[dict[str, Any]], mic: Optional[dict[str, Any]], settings: dict[str, Any]) -> None:
        self.stop()
        pa = pyaudio.PyAudio()
        candidates: list[SourceCapture] = []
        if other:
            candidates.append(
                SourceCapture(
                    pa,
                    "other",
                    other,
                    self.on_segment,
                    self.on_partial,
                    self.on_level,
                    self.on_error,
                    vad_aggressiveness=int(settings.get("vad_aggressiveness", 2)),
                    silence_ms=int(settings.get("silence_ms", DEFAULT_SILENCE_MS)),
                    max_segment_s=int(settings.get("max_segment_s", DEFAULT_MAX_SEGMENT_S)),
                    partial_enabled=bool(settings.get("partial_asr", True)),
                    partial_every_ms=int(settings.get("partial_interval_ms", PARTIAL_EVERY_MS)),
                    partial_window_ms=int(settings.get("partial_window_ms", PARTIAL_WINDOW_MS)),
                    echo_gate=self.echo_gate,
                )
            )
        if mic:
            candidates.append(
                SourceCapture(
                    pa,
                    "me",
                    mic,
                    self.on_segment,
                    self.on_partial,
                    self.on_level,
                    self.on_error,
                    vad_aggressiveness=int(settings.get("vad_aggressiveness", 2)),
                    silence_ms=int(settings.get("silence_ms", DEFAULT_SILENCE_MS)),
                    max_segment_s=int(settings.get("max_segment_s", DEFAULT_MAX_SEGMENT_S)),
                    partial_enabled=bool(settings.get("partial_asr", True)),
                    mic_gain=float(settings.get("mic_gain", 1.0)),
                    echo_gate=self.echo_gate,
                    echo_guard=bool(settings.get("echo_guard", True)),
                    partial_every_ms=int(settings.get("partial_interval_ms", PARTIAL_EVERY_MS)),
                    partial_window_ms=int(settings.get("partial_window_ms", PARTIAL_WINDOW_MS)),
                )
            )
        opened: list[SourceCapture] = []
        for source in candidates:
            if source.open():
                opened.append(source)
            else:
                try:
                    self.on_error(source.speaker, f"{source.device.get('name', '音频设备')}：{source.open_error}")
                except Exception:
                    pass
        with self._lock:
            self.sources = opened
            self._pa = pa if opened else None
        if not opened:
            try:
                pa.terminate()
            except Exception:
                pass
            return
        for source in opened:
            source.start()

    def stop(self) -> None:
        with AUDIO_LOCK:
            self._stop_locked()

    def _stop_locked(self) -> None:
        with self._lock:
            sources = list(self.sources)
            self.sources = []
            pa = self._pa
            self._pa = None
        for source in sources:
            source.stop()
        alive = any(source._thread and source._thread.is_alive() for source in sources)
        if not alive and pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass
