"""Small always-on-top dictation window for Lan.Assistant.

It polls the local voice-input state:
- recording/transcribing: a compact no-activate status bar
- ready: an editable proofreading window with original + translation
- idle: hidden
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "voice-overlay.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
try:
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.unlink()
except Exception:
    pass
_log = LOG.open("a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log

import tkinter as tk
import tkinter.font as tkfont

STATUS_URL = "http://127.0.0.1:8765/api/voice/status"
INSERT_URL = "http://127.0.0.1:8765/api/voice/insert"
CANCEL_URL = "http://127.0.0.1:8765/api/voice/cancel"
POLL_SECONDS = 0.3
BG = "#121820"
PANEL = "#18212b"
LINE = "#2b3947"
TEXT = "#edf1f4"
MUTED = "#8b9bab"
AMBER = "#4c9dff"
RED = "#e2a0a0"
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


class VoiceWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
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
        self.root.after(200, self.poll)
        threading.Thread(target=self._poll_loop, name="voice-overlay-poll", daemon=True).start()

    def _build(self) -> None:
        self.shell = tk.Frame(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)
        self.shell.pack(fill="both", expand=True)
        self.head = tk.Frame(self.shell, bg=BG)
        self.head.pack(fill="x", padx=15, pady=(12, 5))
        self.head.bind("<Button-1>", self._start_drag)
        self.head.bind("<B1-Motion>", self._drag)
        self.title = tk.Label(self.head, text="Lan.Assistant · 语音输入", bg=BG, fg=TEXT,
                              font=("Microsoft YaHei UI", 10, "bold"))
        self.title.pack(side="left")
        self.title.bind("<Button-1>", self._start_drag)
        self.title.bind("<B1-Motion>", self._drag)
        self.close_btn = tk.Label(self.head, text="✕", bg=BG, fg=MUTED, cursor="hand2",
                                  font=("Segoe UI", 11))
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda _e: self.cancel())

        self.hint = tk.Label(self.shell, text="", bg=BG, fg=MUTED, anchor="w",
                             font=("Microsoft YaHei UI", 9), justify="left")
        self.hint.pack(fill="x", padx=15)

        self.body = tk.Frame(self.shell, bg=BG)
        self.body.pack(fill="both", expand=True, padx=15, pady=(8, 10))
        self.original_label = tk.Label(self.body, text="识别原文（可校对）", bg=BG, fg=MUTED, anchor="w",
                                       font=("Microsoft YaHei UI", 8))
        self.original_label.pack(fill="x")
        self.original = tk.Text(self.body, height=6, wrap="word", bg=PANEL, fg=TEXT, insertbackground=AMBER,
                                relief="flat", bd=0, padx=9, pady=7,
                                font=("Segoe UI", 11))
        self.original.pack(fill="both", expand=True, pady=(3, 7))
        self.translated_label = tk.Label(self.body, text="译文", bg=BG, fg=MUTED, anchor="w",
                                         font=("Microsoft YaHei UI", 8))
        self.translated_label.pack(fill="x")
        self.translated = tk.Text(self.body, height=5, wrap="word", bg=PANEL, fg="#c9d8e5",
                                  relief="flat", bd=0, padx=9, pady=7,
                                  font=("Microsoft YaHei UI", 10))
        self.translated.pack(fill="both", expand=True, pady=(3, 8))
        self.translated.configure(state="disabled")

        self.actions = tk.Frame(self.shell, bg=BG)
        self.actions.pack(fill="x", padx=15, pady=(0, 12))
        self.btn_original = tk.Button(self.actions, text="插入原文", command=lambda: self.insert("original"),
                                      bg=AMBER, fg="#f7fcff", activebackground="#8fd8ff",
                                      relief="flat", bd=0, padx=12, pady=6,
                                      font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2")
        self.btn_original.pack(side="left", padx=(0, 7))
        self.btn_translated = tk.Button(self.actions, text="插入译文", command=lambda: self.insert("translated"),
                                        bg="#263747", fg=TEXT, activebackground="#34506a",
                                        relief="flat", bd=0, padx=12, pady=6,
                                        font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.btn_translated.pack(side="left", padx=(0, 7))
        self.btn_cancel = tk.Button(self.actions, text="取消", command=self.cancel,
                                    bg="#1b2530", fg=MUTED, activebackground="#26323f",
                                    relief="flat", bd=0, padx=100, pady=6,
                                    font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.btn_cancel.pack(side="right")

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
        self.title.configure(text=f"Lan.Assistant · {title}")
        self.hint.configure(text=hint, fg=MUTED)
        self.hint.pack(fill="x", padx=15)
        self.body.pack_forget()
        self.actions.pack_forget()
        self._set_noactivate(True)
        if small:
            width, height = 390, 88
            x = max(0, (self.screen_w - width) // 2)
            y = 42
        else:
            width, height = 520, 170
            x = max(0, (self.screen_w - width) // 2)
            y = max(0, (self.screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()

    def show_ready(self, data: dict) -> None:
        original = str(data.get("original", "") or "")
        translated = str(data.get("translated", "") or "")
        self.title.configure(text="Lan.Assistant · 校对后插入")
        self.hint.configure(
            text=(
                "已自动校对同音字/错别字；请快速检查后插入。"
                if data.get("proofread")
                else "可直接修改原文；插入会回到刚才的输入框。"
            ),
            fg=MUTED,
        )
        self.body.pack(fill="both", expand=True, padx=15, pady=(8, 10))
        self.actions.pack(fill="x", padx=15, pady=(0, 12))
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
        width, height = 600, 480
        x = max(0, (self.screen_w - width) // 2)
        y = max(0, (self.screen_h - height) // 2 - 35)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.original.focus_set()

    def hide(self) -> None:
        self.body.pack_forget()
        self.actions.pack_forget()
        self.root.withdraw()

    def insert(self, which: str) -> None:
        widget = self.original if which == "original" else self.translated
        text = widget.get("1.0", "end").strip()
        if not text:
            return
        try:
            result = _post(INSERT_URL, {"text": text})
            if not result.get("ok"):
                self.hint.configure(text=str(result.get("error", "") or "插入失败"), fg=RED)
                return
        except Exception as exc:
            self.hint.configure(text=f"插入失败：{exc}", fg=RED)
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


if __name__ == "__main__":
    VoiceWindow().run()
