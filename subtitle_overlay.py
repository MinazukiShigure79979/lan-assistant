"""Always-on-top subtitle text for Listen & Translate mode.

There is deliberately no background bar: the window is transparent except for
the text itself, plus a thin dark shadow for readability over bright scenes.
Run with pythonw.exe; it polls the local Call Copilot service.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
_DATA = (Path(os.environ.get("LOCALAPPDATA", "")) / "Lan.Assistant" / "data") if FROZEN else (ROOT / "data")
LOG = _DATA / "overlay.log"
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

STATE_URL = "http://127.0.0.1:8765/api/subtitle"
POLL_SECONDS = 0.28
HIDE_AFTER_SECONDS = 5.5
TRANSPARENT_KEY = "#010203"


def _click_through(root: tk.Tk) -> None:
    """Make the top-level window click-through without changing its alpha."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = root.winfo_id()
        parent = user32.GetParent(hwnd)
        target = parent or hwnd
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        style = user32.GetWindowLongW(target, GWL_EXSTYLE)
        # Do not add WS_EX_LAYERED here: Tk already manages the layered
        # transparent window, and layering the wrong handle can hide the text.
        user32.SetWindowLongW(
            target,
            GWL_EXSTYLE,
            style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )
    except Exception:
        pass


def _raise_topmost(root: tk.Tk) -> None:
    """Force the native top-level above other topmost windows (e.g. a player)."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = root.winfo_id()
        target = user32.GetParent(hwnd) or hwnd
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            target,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    except Exception:
        pass


class Overlay:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 1.0)
        self.root.configure(bg=TRANSPARENT_KEY)
        if sys.platform == "win32":
            # Every pixel of this key colour becomes fully transparent.  Only
            # the glyphs and their shadow remain visible.
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.withdraw()

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.width = min(1500, max(820, int(self.screen_w * 0.88)))
        self.height = 206
        self.x = max(0, (self.screen_w - self.width) // 2)
        self.y = max(0, self.screen_h - self.height - 58)
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

        self.canvas = tk.Canvas(
            self.root,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)
        self.font_original = tkfont.Font(family="Segoe UI", size=15, weight="normal")
        self.font_chinese = tkfont.Font(family="Microsoft YaHei UI", size=23, weight="bold")
        self._clickthrough_applied = False

        self.visible = False
        self.last_turn_id = ""
        self.last_text = ""
        self.last_change = 0.0
        self.displayed_at = 0.0
        self.displayed_final = False
        self.displayed_turn_id = ""
        self.hold_until = 0.0
        self.pending_render: Optional[dict] = None
        self.pending_after = 0.0
        self.events: queue.Queue = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        threading.Thread(target=self._poll, name="subtitle-poll", daemon=True).start()
        self.root.after(120, self._consume)

    @staticmethod
    def _hold_seconds(en: str, zh: str, is_final: bool) -> float:
        if not is_final:
            return 0.0
        if zh:
            return max(2.4, min(4.2, 1.4 + len(zh) * 0.07 + len(en) * 0.02))
        return max(2.2, min(3.2, 1.2 + len(en) * 0.025))

    def _show_item(self, item: dict, reset_hold: bool = True) -> None:
        now = time.time()
        self.last_text = item["key"]
        self.last_turn_id = item["turn_id"]
        self.last_change = now
        self.displayed_at = now
        self.displayed_final = not item["is_partial"]
        self.displayed_turn_id = item["turn_id"]
        if reset_hold:
            self.hold_until = now + self._hold_seconds(item["en"], item["zh"], not item["is_partial"])
        elif item["zh"] and not item["is_partial"]:
            # Give the newly arrived Chinese line enough reading time too.
            self.hold_until = max(
                self.hold_until,
                now + self._hold_seconds(item["en"], item["zh"], True),
            )
        print(
            f"subtitle {item['turn_id']}{' partial' if item['is_partial'] else ''}: "
            f"{item['en'][:80]!r} / {item['zh'][:80]!r} stay={self.hold_until - now:.1f}s",
            flush=True,
        )
        self._render(item["en"][:900], item["zh"][:900], partial=item["is_partial"])
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")
        self._show()

    def _flush_pending(self) -> None:
        if self.pending_render is None or time.time() < self.pending_after:
            return
        item = self.pending_render
        self.pending_render = None
        self._show_item(item)

    def _render(self, original: str, chinese: str, partial: bool = False) -> None:
        self.canvas.delete("all")
        cx = self.width // 2
        wrap = self.width - 140
        # Lay out one or two lines without a background, then centre the group.
        en_item = self.canvas.create_text(
            cx,
            0,
            text=original[:600],
            font=self.font_original,
            fill="#e9f1f7",
            width=wrap,
            justify="center",
            anchor="n",
        )
        en_box = self.canvas.bbox(en_item) or (0, 0, 0, 30)
        en_h = max(24, en_box[3] - en_box[1])
        zh_text = chinese[:600] if chinese else (None if partial else "字幕翻译中…")
        zh_item = None
        zh_h = 0
        if zh_text is not None:
            zh_item = self.canvas.create_text(
                cx,
                en_h + 8,
                text=zh_text,
                font=self.font_chinese,
                fill="#ffffff",
                width=wrap,
                justify="center",
                anchor="n",
            )
            zh_box = self.canvas.bbox(zh_item) or (0, 0, 0, 32)
            zh_h = max(30, zh_box[3] - zh_box[1])
        total = en_h + (8 + zh_h if zh_item is not None else 0)
        start = max(4, (self.height - total) // 2)
        self.canvas.move(en_item, 0, start)
        if zh_item is not None:
            self.canvas.move(zh_item, 0, start)
        # A soft dark outline instead of a bar; keeps text legible over bright
        # scenes while letting the video show through everywhere else.
        for dx, dy in ((-2, 1), (2, 1), (0, 2), (0, -1)):
            self.canvas.create_text(
                cx + dx,
                start + dy,
                text=original[:600],
                font=self.font_original,
                fill="#000000",
                width=wrap,
                justify="center",
                anchor="n",
                tags="shadow",
            )
            if zh_item is not None:
                self.canvas.create_text(
                    cx + dx,
                    start + en_h + 8 + dy,
                    text=zh_text,
                    font=self.font_chinese,
                    fill="#000000",
                    width=wrap,
                    justify="center",
                    anchor="n",
                    tags="shadow",
                )
        self.canvas.tag_lower("shadow")
        self.canvas.tag_raise(en_item)
        if zh_item is not None:
            self.canvas.tag_raise(zh_item)

    def _poll(self) -> None:
        while not self._stop.is_set():
            state = None
            try:
                with urllib.request.urlopen(STATE_URL, timeout=0.8) as response:
                    state = json.loads(response.read().decode("utf-8"))
            except Exception:
                state = None
            try:
                while self.events.full():
                    self.events.get_nowait()
                self.events.put_nowait(state)
            except Exception:
                pass
            time.sleep(POLL_SECONDS)

    def _hide(self) -> None:
        self.pending_render = None
        if self.visible:
            print("hide", flush=True)
            self.root.withdraw()
            self.visible = False

    def _show(self) -> None:
        if not self.visible:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.update_idletasks()
            self.root.update()
            if not self._clickthrough_applied:
                _click_through(self.root)
                self._clickthrough_applied = True
            _raise_topmost(self.root)
            self.visible = True
            print(f"show hwnd={self.root.winfo_id()}", flush=True)

    def _consume(self) -> None:
        try:
            state = self.events.get_nowait()
        except queue.Empty:
            state = None
        if state is not None:
            self._apply(state)
        self._flush_pending()
        if (
            self.visible
            and self.pending_render is None
            and time.time() - self.last_change > HIDE_AFTER_SECONDS
        ):
            self._hide()
        self.root.after(100, self._consume)

    def _apply(self, state: dict) -> None:
        try:
            if state.get("mode") != "translate":
                self._hide()
                return
            if not state.get("capture"):
                self._hide()
                return

            partial_state = state.get("partial") or {}
            partial_text = str(partial_state.get("stable") or partial_state.get("text") or "").strip()
            if partial_text:
                en = partial_text
                zh = str(partial_state.get("zh", "") or "").strip()
                is_partial = True
                turn_id = f"partial-{partial_state.get('seq', 0)}"
            else:
                latest = state.get("latest")
                if not latest or latest.get("speaker") != "other":
                    self._hide()
                    return
                en = str(latest.get("en", "") or "").strip()
                zh = str(latest.get("zh", "") or "").strip()
                is_partial = False
                turn_id = str(latest.get("id", ""))

            if not en:
                self._hide()
                return
            item = {
                "en": en,
                "zh": zh,
                "is_partial": is_partial,
                "turn_id": turn_id,
                "key": ("P:" if is_partial else "F:") + en + "\n" + zh,
            }
            if item["key"] == self.last_text:
                return
            now = time.time()
            if self.displayed_final and now < self.hold_until:
                if not is_partial and turn_id == self.displayed_turn_id:
                    # The translation for the line already on screen arrived.
                    self._show_item(item, reset_hold=False)
                    if self.pending_render is not None:
                        self.pending_after = self.hold_until
                    return
                self.pending_render = item
                self.pending_after = self.hold_until
                return
            if is_partial and now - self.displayed_at < 0.9:
                self.pending_render = item
                self.pending_after = self.displayed_at + 0.9
                return
            self.pending_render = None
            self._show_item(item)
        except Exception:
            import traceback

            print("apply error:\n" + traceback.format_exc(), flush=True)
            self._hide()

    def run(self) -> None:
        print(f"overlay starting screen={self.screen_w}x{self.screen_h}", flush=True)
        self.root.mainloop()


def main() -> int:
    Overlay().run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback

        print(traceback.format_exc(), flush=True)
        raise
