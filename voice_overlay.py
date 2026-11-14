"""Small always-on-top dictation window for Lan.Assistant.

It polls the local voice-input state:
- recording/transcribing: a compact no-activate status bar
- ready: an editable proofreading window with original + translation
- idle: hidden
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
_DATA = (Path(os.environ.get("LOCALAPPDATA", "")) / "Lan.Assistant" / "data") if FROZEN else (ROOT / "data")
LOG = _DATA / "voice-overlay.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
try:
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.unlink()
except Exception:
    pass
_log = LOG.open("a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log


def _enable_dpi_awareness() -> None:
    """Must run before Tk initializes, otherwise DWM bitmap-stretches the UI."""
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

import tkinter as tk
import tkinter.font as tkfont

STATUS_URL = "http://127.0.0.1:8765/api/voice/status"
INSERT_URL = "http://127.0.0.1:8765/api/voice/insert"
CANCEL_URL = "http://127.0.0.1:8765/api/voice/cancel"
POLL_SECONDS = 0.3
BG = "#0d141c"
PANEL = "#17222d"
PANEL_DEEP = "#111a23"
LINE = "#2a3b4a"
TEXT = "#f0f6fb"
MUTED = "#9aafc0"
FAINT = "#72879a"
AMBER = "#58a6ff"
AMBER_SOFT = "#a9ddff"
RED = "#ff8e8e"
FONT = "Microsoft YaHei UI"
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000


def _post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _set_noactivate(root: tk.Tk, enabled: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = root.winfo_id()
        target = user32.GetParent(hwnd) or hwnd
        style = user32.GetWindowLongW(target, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_NOACTIVATE
        else:
            style &= ~WS_EX_NOACTIVATE
        user32.SetWindowLongW(target, GWL_EXSTYLE, style)
    except Exception:
        pass


def _round_window(root: tk.Tk) -> None:
    """Ask Windows 11 DWM for rounded corners; harmless on older builds."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)
        )
    except Exception:
        pass


class VoiceWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.ui_scale = max(1.0, round(self.root.winfo_fpixels("1i") / 96.0, 3))
        self.root.title("Lan.Assistant · 语音输入")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.withdraw()
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.status = "idle"
        self.current_id = 0
        self.last_ready_id = 0
        self.remote = {"status": "idle", "id": 0}
        self.drag_x = 0
        self.drag_y = 0
        self._build()
        self.root.update_idletasks()
        _round_window(self.root)
        self.root.after(200, self.poll)
        threading.Thread(target=self._poll_loop, name="voice-overlay-poll", daemon=True).start()

    def _px(self, value: float) -> int:
        return max(1, int(round(value * self.ui_scale)))

    def _build(self) -> None:
        self.shell = tk.Frame(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)
        self.shell.pack(fill="both", expand=True)
        self.accent = tk.Frame(self.shell, bg=AMBER, height=3)
        self.accent.pack(fill="x")

        self.head = tk.Frame(self.shell, bg=BG)
        self.head.pack(fill="x", padx=18, pady=(13, 4))
        self.head.bind("<Button-1>", self._start_drag)
        self.head.bind("<B1-Motion>", self._drag)

        self.head_copy = tk.Frame(self.head, bg=BG)
        self.head_copy.pack(side="left", fill="x", expand=True)
        self.head_copy.bind("<Button-1>", self._start_drag)
        self.head_copy.bind("<B1-Motion>", self._drag)
        self.eyebrow = tk.Label(
            self.head_copy,
            text="LAN.ASSISTANT / VOICE",
            bg=BG,
            fg=AMBER_SOFT,
            font=(FONT, 8, "bold"),
            anchor="w",
        )
        self.eyebrow.pack(fill="x")
        self.eyebrow.bind("<Button-1>", self._start_drag)
        self.eyebrow.bind("<B1-Motion>", self._drag)
        self.title = tk.Label(
            self.head_copy,
            text="语音输入",
            bg=BG,
            fg=TEXT,
            font=(FONT, 12, "bold"),
            anchor="w",
        )
        self.title.pack(fill="x", pady=(2, 0))
        self.title.bind("<Button-1>", self._start_drag)
        self.title.bind("<B1-Motion>", self._drag)

        self.close_btn = tk.Label(
            self.head,
            text="✕",
            bg=BG,
            fg=MUTED,
            cursor="hand2",
            font=("Segoe UI", 11),
        )
        self.close_btn.pack(side="right", padx=(12, 0))
        self.close_btn.bind("<Button-1>", lambda _e: self.cancel())

        self.hint = tk.Label(
            self.shell,
            text="",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=(FONT, 10),
            justify="left",
            wraplength=self._px(620),
        )
        self.hint.pack(fill="x", padx=18, pady=(1, 5))

        self.body = tk.Frame(self.shell, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(7, 10))
        self.original_label = tk.Label(
            self.body,
            text="识别原文（可直接修改）",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=(FONT, 9, "bold"),
        )
        self.original_label.pack(fill="x")
        self.original = tk.Text(
            self.body,
            height=7,
            wrap="word",
            bg=PANEL,
            fg=TEXT,
            insertbackground=AMBER,
            selectbackground="#2d5475",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=AMBER,
            font=(FONT, 12),
        )
        self.original.pack(fill="both", expand=True, pady=(5, 12))
        self.translated_label = tk.Label(
            self.body,
            text="译文",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=(FONT, 9, "bold"),
        )
        self.translated_label.pack(fill="x")
        self.translated = tk.Text(
            self.body,
            height=5,
            wrap="word",
            bg=PANEL_DEEP,
            fg="#c6d8e7",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=LINE,
            font=(FONT, 11),
        )
        self.translated.pack(fill="both", expand=True, pady=(5, 4))
        self.translated.configure(state="disabled")

        self.actions = tk.Frame(self.shell, bg=BG)
        self.actions.pack(fill="x", padx=18, pady=(0, 14))
        self.btn_original = tk.Button(
            self.actions,
            text="插入原文",
            command=lambda: self.insert("original"),
            bg=AMBER,
            fg="#07131f",
            activebackground="#8fd0ff",
            activeforeground="#07131f",
            relief="flat",
            bd=0,
            padx=15,
            pady=8,
            font=(FONT, 10, "bold"),
            cursor="hand2",
        )
        self.btn_original.pack(side="left", padx=(0, 8))
        self.btn_translated = tk.Button(
            self.actions,
            text="插入译文",
            command=lambda: self.insert("translated"),
            bg="#20384a",
            fg=TEXT,
            activebackground="#2c4b63",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=15,
            pady=8,
            font=(FONT, 10),
            cursor="hand2",
        )
        self.btn_translated.pack(side="left", padx=(0, 8))
        self.btn_copy = tk.Button(
            self.actions,
            text="复制",
            command=self.copy,
            bg="#1b2c3a",
            fg=TEXT,
            activebackground="#29475e",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=15,
            pady=8,
            font=(FONT, 10),
            cursor="hand2",
        )
        self.btn_copy.pack(side="left", padx=(0, 8))
        self.btn_cancel = tk.Button(
            self.actions,
            text="取消",
            command=self.cancel,
            bg="#15212c",
            fg=MUTED,
            activebackground="#223342",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            font=(FONT, 10),
            cursor="hand2",
        )
        self.btn_cancel.pack(side="right")
        self.root.bind("<Escape>", lambda _e: self.cancel())

    def _start_drag(self, event) -> None:
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def _set_noactivate(self, enabled: bool) -> None:
        _set_noactivate(self.root, enabled)

    def _drag(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self.drag_x}+{event.y_root - self.drag_y}")

    def _poll_loop(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(STATUS_URL, timeout=2) as response:
                    self.remote = json.loads(response.read().decode("utf-8"))
            except Exception:
                pass
            time.sleep(POLL_SECONDS)

    def poll(self) -> None:
        try:
            self.apply(self.remote)
        except Exception:
            print(traceback.format_exc(), flush=True)
        finally:
            self.root.after(150, self.poll)

    def apply(self, data: dict) -> None:
        status = str(data.get("status", "idle") or "idle")
        voice_id = int(data.get("id", 0) or 0)
        if status == self.status and voice_id == self.current_id:
            return
        self.status = status
        self.current_id = voice_id
        if status == "recording":
            if data.get("hold"):
                hint = "松开快捷键结束；到达最长录音时间会自动结束。"
            elif data.get("automatic"):
                hint = "停顿约 1 秒后自动结束。"
            else:
                hint = "再按一次快捷键结束；到达最长录音时间会自动结束。"
            self.show_status("● 正在录音…", hint, small=True)
        elif status == "transcribing":
            self.show_status("正在识别…", "校对窗口马上出现。", small=True)
        elif status == "error":
            self.show_status("语音输入失败", str(data.get("error", "") or "请重试。"), small=False)
            self._set_noactivate(False)
            self.root.lift()
            self.root.focus_force()
        elif status == "ready":
            if voice_id == self.last_ready_id:
                return
            self.last_ready_id = voice_id
            self.show_ready(data)
        else:
            self.hide()

    def show_status(self, title: str, hint: str, small: bool) -> None:
        self.eyebrow.configure(text="VOICE INPUT · LIVE" if small else "VOICE INPUT · NOTICE")
        self.accent.configure(bg=AMBER if small else RED if "失败" in title else AMBER)
        self.title.configure(text=title)
        self.hint.configure(text=hint, fg=MUTED)
        self.hint.pack(fill="x", padx=18, pady=(1, 5))
        self.body.pack_forget()
        self.actions.pack_forget()
        self._set_noactivate(True)
        if small:
            width, height = self._px(440), self._px(122)
            x = max(0, (self.screen_w - width) // 2)
            y = self._px(42)
        else:
            width, height = self._px(560), self._px(190)
            x = max(0, (self.screen_w - width) // 2)
            y = max(0, (self.screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()

    def show_ready(self, data: dict) -> None:
        original = str(data.get("original", "") or "")
        translated = str(data.get("translated", "") or "")
        self.eyebrow.configure(text="VOICE INPUT · PROOFREAD")
        self.accent.configure(bg=AMBER)
        self.title.configure(text="确认要插入的文字")
        self.hint.configure(
            text=(
                "已自动校对同音字/错别字；请快速检查后插入。"
                if data.get("proofread")
                else "可直接修改原文；插入会回到刚才的输入框。"
            ),
            fg=MUTED,
        )
        self.body.pack(fill="both", expand=True, padx=18, pady=(7, 10))
        self.actions.pack(fill="x", padx=18, pady=(0, 14))
        self.original.configure(state="normal")
        self.original.delete("1.0", "end")
        self.original.insert("1.0", original)
        self.translated.configure(state="normal")
        self.translated.delete("1.0", "end")
        self.translated.insert("1.0", translated)
        self.translated.configure(state="disabled")
        self.btn_translated.configure(state="normal" if translated else "disabled")
        self.translated_label.configure(text="译文" if translated else "译文（未开启）")
        self._set_noactivate(False)
        width, height = self._px(640), self._px(540)
        x = max(0, (self.screen_w - width) // 2)
        y = max(0, (self.screen_h - height) // 2 - self._px(35))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.original.focus_set()

    def hide(self) -> None:
        self.body.pack_forget()
        self.actions.pack_forget()
        self.root.withdraw()

    def copy(self) -> None:
        text = self.translated.get("1.0", "end").strip() or self.original.get("1.0", "end").strip()
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self.hint.configure(
                text="已复制到剪贴板；在游戏/聊天框里按 Ctrl+V 粘贴",
                fg=AMBER,
            )
        except Exception as exc:
            self.hint.configure(text=f"复制失败：{exc}", fg=RED)

    def insert(self, which: str) -> None:
        widget = self.original if which == "original" else self.translated
        text = widget.get("1.0", "end").strip()
        if not text:
            return
        try:
            result = _post(INSERT_URL, {"text": text})
            if not result.get("ok"):
                # Games and custom DirectX chat boxes reject synthetic input.
                # Keep the reviewed text available for Ctrl+V instead of
                # silently losing it.
                self.copy()
                error = str(result.get("error", "") or "插入失败")
                self.hint.configure(
                    text=f"{error}；已复制到剪贴板，请按 Ctrl+V 粘贴",
                    fg=RED,
                )
                return
        except Exception as exc:
            self.copy()
            self.hint.configure(
                text=f"插入失败：{exc}；已复制到剪贴板，请按 Ctrl+V 粘贴",
                fg=RED,
            )
            return
        self.hide()
        self.status = "idle"

    def cancel(self) -> None:
        # Close immediately; the server call must not make the X button feel
        # unresponsive when ASR is still finishing in the background.
        self.hide()
        self.status = "idle"
        try:
            _post(CANCEL_URL, {})
        except Exception:
            print(traceback.format_exc(), flush=True)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    VoiceWindow().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
