"""Global-hotkey microphone capture and text insertion for Lan.Assistant.

The microphone is opened only for the short voice-input utterance, so it does
not conflict with the call/meeting capture engine.  Insertion goes straight to
the window that was focused when the hotkey was pressed; no clipboard content
is overwritten.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
import pyaudiowpatch as pyaudio

from audio_engine import SAMPLE_RATE, resolve_devices
from audio_lock import AUDIO_LOCK

CREATE_NO_WINDOW = 0x08000000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
VK_RETURN = 0x0D
VK_TAB = 0x09


def parse_hotkey(value: str) -> tuple[int, int]:
    """Parse one of the UI's fixed hotkey choices into Win32 modifiers/VK."""
    text = (value or "").strip().lower().replace(" ", "")
    mods = 0
    vk = 0
    for part in [p for p in text.split("+") if p]:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "meta"):
            mods |= MOD_WIN
        elif part in ("space", "空格"):
            vk = 0x20
        elif part in ("f9",):
            vk = 0x78
        elif part in ("f10",):
            vk = 0x79
        elif len(part) == 1:
            vk = ord(part.upper())
    if not mods and not vk:
        mods, vk = MOD_CONTROL | MOD_ALT, 0x20
    return mods, vk


def get_foreground_window() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", _RECT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def get_focus_info() -> dict[str, Any]:
    """Return current foreground/focus/caret info without UI Automation.

    This is intentionally a cheap heuristic: games and custom-rendered apps
    often expose no standard keyboard focus, so they simply won't auto-trigger.
    """
    info: dict[str, Any] = {
        "hwnd": 0, "focus_hwnd": 0, "class_name": "", "title": "",
        "caret": False, "fullscreen": False,
    }
    try:
        user32 = ctypes.windll.user32
        fg = int(user32.GetForegroundWindow() or 0)
        if not fg:
            return info
        info["hwnd"] = fg
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(fg, title_buf, 512)
        info["title"] = title_buf.value
        pid = ctypes.c_ulong()
        tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        focus_hwnd = fg
        if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            if gti.hwndFocus:
                focus_hwnd = int(gti.hwndFocus)
            info["caret"] = bool(gti.hwndCaret)
        info["focus_hwnd"] = focus_hwnd
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(focus_hwnd, class_buf, 256)
        info["class_name"] = class_buf.value
        # Compare the foreground window rectangle with its monitor rectangle.
        rect = _RECT()
        if user32.GetWindowRect(fg, ctypes.byref(rect)):
            MONITOR_DEFAULTTONEAREST = 2
            monitor = user32.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST)
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                m = mi.rcMonitor
                info["fullscreen"] = (
                    abs(rect.left - m.left) <= 3 and abs(rect.top - m.top) <= 3
                    and abs(rect.right - m.right) <= 3 and abs(rect.bottom - m.bottom) <= 3
                )
    except Exception:
        pass
    return info


_EDITABLE_CLASS_HINTS = (
    "edit", "richedit", "scintilla", "textinput", "textbox", "textarea",
    "chrome_renderwidgethosthwnd", "mozilla", "qt", "wechat", "notepad", "tk",
)


def is_editable_focus(
    info: dict[str, Any], skip_fullscreen: bool = True, allow_titles: tuple[str, ...] = ()
) -> bool:
    if not info or not info.get("focus_hwnd"):
        return False
    if skip_fullscreen and info.get("fullscreen"):
        return False
    title = str(info.get("title", "") or "").lower()
    if any(token and token.lower() in title for token in allow_titles):
        # Chromium/Electron render their edit controls without a native child
        # HWND or caret, so a title allowlist is the only practical signal.
        return True
    cls = str(info.get("class_name", "") or "").lower()
    if not any(hint in cls for hint in _EDITABLE_CLASS_HINTS):
        return False
    # A caret is strong evidence; a plain Edit/RichEdit control is accepted
    # even when the caret is not exposed by the host application.
    return bool(info.get("caret")) or "edit" in cls or "scintilla" in cls


def focus_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        foreground = user32.GetForegroundWindow()
        current_tid = kernel32.GetCurrentThreadId()
        foreground_tid = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = False
        if foreground_tid and foreground_tid != current_tid:
            attached = bool(user32.AttachThreadInput(current_tid, foreground_tid, True))
        # SW_RESTORE un-maximizes a window.  Only restore if it is actually
        # minimized; a normal/maximized target must keep its state.
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if int(user32.GetForegroundWindow() or 0) != int(hwnd):
            # Foreground lock fallback.  A synthetic Alt tap is less invasive
            # than switching the window's show state.
            VK_MENU = 0x12
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        if attached:
            user32.AttachThreadInput(current_tid, foreground_tid, False)
        time.sleep(0.4)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:
        return False


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    item = _INPUT()
    item.type = INPUT_KEYBOARD
    item.ki.wVk = vk
    item.ki.wScan = scan
    item.ki.dwFlags = flags
    return item


def send_unicode_text(text: str) -> int:
    """Type Unicode text into the currently focused control via SendInput."""
    if not text:
        return 0
    events: list[_INPUT] = []
    for char in text:
        if char == "\n":
            events.append(_key_event(VK_RETURN))
            events.append(_key_event(VK_RETURN, flags=KEYEVENTF_KEYUP))
        elif char == "\t":
            events.append(_key_event(VK_TAB))
            events.append(_key_event(VK_TAB, flags=KEYEVENTF_KEYUP))
        else:
            code = ord(char)
            if code > 0xFFFF:
                code -= 0x10000
                high = 0xD800 + (code >> 10)
                low = 0xDC00 + (code & 0x3FF)
                for value in (high, low):
                    events.append(_key_event(scan=value, flags=KEYEVENTF_UNICODE))
                    events.append(_key_event(scan=value, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            else:
                events.append(_key_event(scan=code, flags=KEYEVENTF_UNICODE))
                events.append(_key_event(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    if not events:
        return 0
    array = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))
    return int(sent or 0)


def insert_text_into_window(hwnd: int, text: str) -> tuple[bool, str]:
    if not hwnd:
        return False, "没有找到刚才的输入框窗口"
    if not focus_window(hwnd):
        return False, "无法把焦点还给刚才的窗口，请手动点一下输入框后重试"
    time.sleep(0.2)
    # A focus transition can swallow the first injected event.  Prime the
    # input queue with a zero-distance mouse event (no keystroke, so it cannot
    # type a space, toggle a game action, or open a menu).
    try:
        nudge = _INPUT()
        nudge.type = INPUT_MOUSE
        nudge.mi.dx = 0
        nudge.mi.dy = 0
        nudge.mi.mouseData = 0
        nudge.mi.dwFlags = 0
        arr = (_INPUT * 1)(nudge)
        ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(_INPUT))
        time.sleep(0.06)
    except Exception:
        pass
    sent = send_unicode_text(text)
    if sent <= 0:
        return False, "文字插入失败，请手动复制文本"
    return True, ""


class VoiceRecorder:
    """Short microphone-only recording with an explicit stop call."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._frames: list[np.ndarray] = []
        self._rate = SAMPLE_RATE
        self._lock = threading.Lock()
        self.error = ""
        self.started_at = 0.0
        self.level = 0.0
        self.has_speech = False
        self.last_voice_at = 0.0

    @property
    def recording(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> bool:
        with AUDIO_LOCK:
            return self._start_locked()

    def _start_locked(self) -> bool:
        if self.recording:
            return True
        self.error = ""
        self._frames = []
        self._stop.clear()
        self.has_speech = False
        self.last_voice_at = 0.0
        self.level = 0.0
        _other, mic, _devices = resolve_devices(self.settings)
        if not mic:
            self.error = "没有找到麦克风，请在设置中检查麦克风设备。"
            return False
        try:
            self._pa = pyaudio.PyAudio()
            rate = 16000
            try:
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=rate,
                    input=True,
                    input_device_index=int(mic["index"]),
                    frames_per_buffer=1024,
                )
            except Exception:
                rate = int(mic.get("rate", 48000) or 48000)
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=rate,
                    input=True,
                    input_device_index=int(mic["index"]),
                    frames_per_buffer=2048,
                )
            self._rate = rate
        except Exception as exc:
            self.error = f"打开麦克风失败：{exc}"
            self._cleanup_audio()
            return False
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="voice-input-recorder", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                data = self._stream.read(1024, exception_on_overflow=False)
                pcm = np.frombuffer(data, dtype=np.int16).copy()
                if pcm.size:
                    with self._lock:
                        self._frames.append(pcm)
                    peak = float(np.max(np.abs(pcm))) / 32768.0
                    self.level = peak
                    if peak > 0.018:
                        self.has_speech = True
                        self.last_voice_at = time.time()
        except Exception as exc:
            self.error = f"录音中断：{exc}"
        finally:
            with self._lock:
                self._stop.set()
            # A device/driver error must not leave the microphone open until
            # the app's maximum-recording timer fires.
            self._cleanup_audio()

    def stop(self) -> np.ndarray:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=2.0)
        with self._lock:
            frames = list(self._frames)
        self._cleanup_audio()
        if not frames:
            return np.array([], dtype=np.int16)
        pcm = np.concatenate(frames).astype(np.int16)
        if self._rate != SAMPLE_RATE and pcm.size:
            # Resample with linear interpolation; voice input is short and this
            # avoids bringing in another audio dependency.
            duration = pcm.size / float(self._rate)
            target = max(1, int(duration * SAMPLE_RATE))
            src = np.linspace(0, pcm.size - 1, num=pcm.size)
            dst = np.linspace(0, pcm.size - 1, num=target)
            pcm = np.interp(dst, src, pcm.astype(np.float32)).astype(np.int16)
        return pcm

    def _cleanup_audio(self) -> None:
        with AUDIO_LOCK:
            try:
                if self._stream is not None:
                    self._stream.stop_stream()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None
            try:
                if self._pa is not None:
                    self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def cancel(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=1.0)
        self._cleanup_audio()


class HoldHotkey:
    """Low-level keyboard hook for hold-to-talk.

    RegisterHotKey only reports key-down, so hold mode needs WH_KEYBOARD_LL.
    The trigger key is swallowed so it does not type a space (or invoke F9)
    while the user is dictating.
    """

    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WH_KEYBOARD_LL = 13
    MODS = {MOD_CONTROL: {0x11, 0xA2, 0xA3}, MOD_ALT: {0x12, 0xA4, 0xA5},
            MOD_SHIFT: {0x10, 0xA0, 0xA1}, MOD_WIN: {0x5B, 0x5C}}

    def __init__(self, hotkey: str, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._hook = None
        self._hook_proc = None
        self._thread_id = 0
        self._active = False
        self._down: set[int] = set()

    def start(self) -> bool:
        self.stop()
        self.error = ""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="voice-hold-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return not self.error

    def _mods_ok(self) -> bool:
        mods, _vk = parse_hotkey(self.hotkey)
        for bit, keys in self.MODS.items():
            if mods & bit and not (self._down & keys):
                return False
        return True

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        mods, vk = parse_hotkey(self.hotkey)
        self._thread_id = int(kernel32.GetCurrentThreadId())

        hook_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.POINTER(KBDLLHOOKSTRUCT)
        )

        def hook_proc(n_code, w_param, l_param):
            if n_code >= 0:
                try:
                    kb = l_param.contents
                    code = int(kb.vkCode)
                    if w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                        self._down.add(code)
                        if code == vk and self._mods_ok():
                            if not self._active:
                                self._active = True
                                threading.Thread(
                                    target=self._watch_release,
                                    args=(vk,),
                                    name="voice-hold-release-watch",
                                    daemon=True,
                                ).start()
                                self.on_press()
                            return 1
                    elif w_param in (self.WM_KEYUP, self.WM_SYSKEYUP):
                        if code == vk and self._active:
                            self._active = False
                            self.on_release()
                            self._down.discard(code)
                            return 1
                        self._down.discard(code)
                except Exception:
                    pass
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._hook_proc = hook_type(hook_proc)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_type, ctypes.c_void_p, ctypes.c_ulong]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
        try:
            self._hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._hook_proc, None, 0)
        except Exception as exc:
            self.error = f"按住快捷键钩子安装失败：{exc}"
            self._ready.set()
            return
        if not self._hook:
            self.error = "按住快捷键钩子安装失败；可以改用切换模式。"
            self._ready.set()
            return
        self._ready.set()
        msg = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._thread_id = 0
            self._active = False

    def _watch_release(self, vk: int) -> None:
        """Fallback for lost modifier key-up events.

        Do not check the trigger key itself: the low-level hook swallows its
        key-down, so GetAsyncKeyState(trigger) can read as up while the user is
        still holding it.  Modifier keys are not swallowed and are safe to
        poll.  For a modifier-less hotkey (F9), rely on the normal key-up hook
        and the maximum-recording timeout.
        """
        mods, _vk = parse_hotkey(self.hotkey)
        if not mods:
            return
        required: list[set[int]] = []
        for bit, keys in self.MODS.items():
            if mods & bit:
                required.append(keys)
        user32 = ctypes.windll.user32
        while self._active:
            time.sleep(0.2)
            try:
                still_down = all(
                    any(int(user32.GetAsyncKeyState(key)) & 0x8000 for key in keys)
                    for keys in required
                )
                if not still_down:
                    if self._active:
                        self._active = False
                        self.on_release()
                    return
            except Exception:
                return

    def stop(self) -> None:
        thread = self._thread
        if not thread or not thread.is_alive():
            self._thread = None
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        thread.join(timeout=1.0)
        self._thread = None

    def restart(self, hotkey: str) -> bool:
        self.hotkey = hotkey or "ctrl+alt+space"
        return self.start()


class GlobalHotkey:
    """RegisterHotKey message loop running on its own thread."""

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._armed = True

    def start(self) -> bool:
        self.stop()
        self.error = ""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="voice-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return not self.error

    def _wait_release(self, vk: int) -> None:
        user32 = ctypes.windll.user32
        while True:
            try:
                if not (int(user32.GetAsyncKeyState(vk)) & 0x8000):
                    time.sleep(0.04)
                    self._armed = True
                    return
            except Exception:
                self._armed = True
                return
            time.sleep(0.02)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        mods, vk = parse_hotkey(self.hotkey)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = bool(user32.RegisterHotKey(None, 1, mods, vk))
        if not registered:
            self.error = "全局快捷键注册失败，可能被其他程序占用。"
            self._ready.set()
            return
        self._ready.set()
        try:
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    if not self._armed:
                        # RegisterHotKey can post auto-repeat messages while
                        # the key is held.  Do not turn one press into a
                        # start/stop/start sequence.
                        continue
                    self._armed = False
                    threading.Thread(
                        target=self._wait_release,
                        args=(vk,),
                        name="voice-hotkey-release",
                        daemon=True,
                    ).start()
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            try:
                user32.UnregisterHotKey(None, 1)
            except Exception:
                pass
            self._thread_id = 0

    def stop(self) -> None:
        thread = self._thread
        if not thread or not thread.is_alive():
            self._thread = None
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        thread.join(timeout=1.0)
        self._thread = None

    def restart(self, hotkey: str) -> bool:
        self.hotkey = hotkey or "ctrl+alt+space"
        return self.start()
