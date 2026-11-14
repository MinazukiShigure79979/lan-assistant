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
from collections import deque
from pathlib import Path

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
POLL_SECONDS = 0.15
HIDE_AFTER_SECONDS = 6.0
MAX_ABSOLUTE_AGE = 6.0
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
        self.height = 350
        self.x = max(0, (self.screen_w - self.width) // 2)
        self.y = max(0, self.screen_h - self.height - 36)
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

        self.canvas = tk.Canvas(
            self.root,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)
        self.appearance_key = ""
        self.en_color = "#f4f8fd"
        self.zh_color = "#ffffff"
        self._apply_appearance({})
        self._clickthrough_applied = False

        self.visible = False
        self.last_turn_id = ""
        self.last_text = ""
        self.last_change = 0.0
        self.final_lines: deque[dict] = deque(maxlen=3)
        self.expired_turn_ids: set[str] = set()
        self.render_key = ""
        self.poll_seconds = POLL_SECONDS
        self.events: queue.Queue = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        threading.Thread(target=self._poll, name="subtitle-poll", daemon=True).start()
        self.root.after(120, self._consume)

    @staticmethod
    def _max_age(en: str, zh: str) -> float:
        if zh:
            return max(2.2, min(5.5, 1.6 + len(zh) * 0.12 + len(en) * 0.015))
        return max(2.0, min(4.5, 1.3 + len(en) * 0.04))

    def _expire_lines(self, now: float) -> bool:
        if not self.final_lines:
            return False
        kept = []
        for row in self.final_lines:
            if float(row.get("expires_at", 0) or 0) <= now:
                self.expired_turn_ids.add(str(row.get("turn_id", "")))
            else:
                kept.append(row)
        if len(kept) == len(self.final_lines):
            return False
        self.final_lines = deque(kept, maxlen=3)
        return True

    @staticmethod
    def _dim(color: str) -> str:
        try:
            raw = str(color or "").lstrip("#")
            if len(raw) != 6:
                return color
            r, g, b = (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
            factor = 0.76
            return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"
        except Exception:
            return color

    def _apply_appearance(self, appearance: dict) -> None:
        family = str(appearance.get("font_family") or "Microsoft YaHei UI")[:80]
        original_size = max(10, min(40, int(appearance.get("original_size", 15) or 15)))
        translation_size = max(14, min(56, int(appearance.get("translation_size", 23) or 23)))
        original_color = str(appearance.get("original_color") or "#f4f8fd")
        translation_color = str(appearance.get("translation_color") or "#ffffff")
        key = (family, original_size, translation_size, original_color, translation_color)
        if key == self.appearance_key:
            return
        self.appearance_key = key
        self.font_original = tkfont.Font(family=family, size=original_size, weight="normal")
        self.font_original_small = tkfont.Font(
            family=family, size=max(9, original_size - 3), weight="normal"
        )
        self.font_chinese = tkfont.Font(family=family, size=translation_size, weight="bold")
        self.font_chinese_small = tkfont.Font(
            family=family, size=max(12, translation_size - 5), weight="bold"
        )
        self.en_color = original_color
        self.zh_color = translation_color
        self.height = max(350, min(760, int(190 + translation_size * 6.5 + original_size * 2)))
        self.y = max(0, self.screen_h - self.height - 36)
        self.render_key = ""
        if getattr(self, "visible", False):
            self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

    def _render_stack(self, entries: list[dict]) -> None:
        self.canvas.delete("all")
        cx = self.width // 2
        wrap = self.width - 140
        count = len(entries)
        prepared: list[dict] = []
        total = 0
        for index, entry in enumerate(entries):
            is_partial = bool(entry.get("partial"))
            only_one = count == 1
            en_font = self.font_original if (only_one or is_partial) else self.font_original_small
            zh_font = self.font_chinese if (only_one or is_partial) else self.font_chinese_small
            latest = is_partial or index == count - 1
            en_fill = self.en_color if latest else self._dim(self.en_color)
            zh_fill = self.zh_color if latest else self._dim(self.zh_color)
            en = str(entry.get("en", "") or "")[:600]
            zh = str(entry.get("zh", "") or "")[:600]
            en_item = self.canvas.create_text(
                cx,
                0,
                text=en,
                font=en_font,
                fill=en_fill,
                width=wrap,
                justify="center",
                anchor="n",
            )
            en_box = self.canvas.bbox(en_item) or (0, 0, 0, 24)
            en_h = max(20, en_box[3] - en_box[1])
            zh_item = None
            zh_h = 0
            if zh:
                zh_item = self.canvas.create_text(
                    cx,
                    0,
                    text=zh,
                    font=zh_font,
                    fill=zh_fill,
                    width=wrap,
                    justify="center",
                    anchor="n",
                )
                zh_box = self.canvas.bbox(zh_item) or (0, 0, 0, 26)
                zh_h = max(24, zh_box[3] - zh_box[1])
            shadows = []
            for dx, dy in ((-2, 1), (2, 1), (0, 2), (0, -1)):
                shadows.append(
                    self.canvas.create_text(
                        cx + dx,
                        dy,
                        text=en,
                        font=en_font,
                        fill="#000000",
                        width=wrap,
                        justify="center",
                        anchor="n",
                        tags="shadow",
                    )
                )
                if zh:
                    shadows.append(
                        self.canvas.create_text(
                            cx + dx,
                            dy + en_h + 6,
                            text=zh,
                            font=zh_font,
                            fill="#000000",
                            width=wrap,
                            justify="center",
                            anchor="n",
                            tags="shadow",
                        )
                    )
            gap = 10 if index < count - 1 else 0
            prepared.append(
                {
                    "en": en_item,
                    "zh": zh_item,
                    "shadows": shadows,
                    "en_h": en_h,
                    "zh_h": zh_h,
                    "gap": gap,
                }
            )
            total += en_h + (6 + zh_h if zh_item is not None else 0) + gap
        y = max(4, (self.height - total) // 2)
        for block in prepared:
            self.canvas.move(block["en"], 0, y)
            if block["zh"] is not None:
                self.canvas.move(block["zh"], 0, y + block["en_h"] + 6)
            for shadow in block["shadows"]:
                self.canvas.move(shadow, 0, y)
            self.canvas.tag_raise(block["en"])
            if block["zh"] is not None:
                self.canvas.tag_raise(block["zh"])
            y += block["en_h"] + (6 + block["zh_h"] if block["zh"] is not None else 0) + block["gap"]
        self.canvas.tag_lower("shadow")

    def _poll(self) -> None:
        while not self._stop.is_set():
            state = None
            try:
                with urllib.request.urlopen(STATE_URL, timeout=0.8) as response:
                    state = json.loads(response.read().decode("utf-8"))
                if isinstance(state, dict):
                    poll_ms = int(state.get("poll_ms", 150) or 150)
                    self.poll_seconds = max(0.08, min(1.0, poll_ms / 1000.0))
            except Exception:
                state = None
            try:
                while self.events.full():
                    self.events.get_nowait()
                self.events.put_nowait(state)
            except Exception:
                pass
            time.sleep(self.poll_seconds)

    def _hide(self, clear: bool = True) -> None:
        if clear:
            self.final_lines.clear()
            self.expired_turn_ids.clear()
            self.render_key = ""
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
        if self.visible and time.time() - self.last_change > HIDE_AFTER_SECONDS:
            self._hide(clear=False)
        self.root.after(100, self._consume)

    def _apply(self, state: dict) -> None:
        try:
            if state.get("mode") != "translate":
                self._hide()
                return
            if not state.get("capture"):
                self._hide()
                return
            self._apply_appearance(state.get("appearance") or {})

            now = time.time()
            self._expire_lines(now)
            latest = state.get("latest")
            if latest and latest.get("speaker") == "other":
                en = str(latest.get("en", "") or "").strip()
                zh = str(latest.get("zh", "") or "").strip()
                turn_id = str(latest.get("id", ""))
                if en and turn_id not in self.expired_turn_ids:
                    existing = next(
                        (row for row in self.final_lines if row["turn_id"] == turn_id),
                        None,
                    )
                    if existing is not None:
                        if existing.get("en") != en or existing.get("zh") != zh:
                            existing["en"] = en
                            existing["zh"] = zh
                            first_seen = float(existing.get("first_seen", now) or now)
                            existing["first_seen"] = first_seen
                            existing["expires_at"] = min(
                                first_seen + MAX_ABSOLUTE_AGE,
                                now + self._max_age(en, zh),
                            )
                    else:
                        self.final_lines.append(
                            {
                                "turn_id": turn_id,
                                "en": en,
                                "zh": zh,
                                "partial": False,
                                "first_seen": now,
                                "expires_at": now + self._max_age(en, zh),
                            }
                        )

            partial_state = state.get("partial") or {}
            partial_text = str(
                partial_state.get("stable") or partial_state.get("text") or ""
            ).strip()
            partial_item = None
            if partial_text:
                latest_en = str((latest or {}).get("en", "") or "").strip()
                if not (latest_en and (latest_en.startswith(partial_text) or partial_text == latest_en)):
                    partial_item = {
                        "turn_id": f"partial-{partial_state.get('seq', 0)}",
                        "en": partial_text,
                        "zh": str(partial_state.get("zh", "") or "").strip(),
                        "partial": True,
                    }

            finals = list(self.final_lines)
            if partial_item is not None:
                # Keep two finalized lines above the live line while speaking.
                finals = finals[-2:]
                entries = finals + [partial_item]
            else:
                entries = finals[-3:]
            if not entries:
                self._hide(clear=False)
                return

            key = "|".join(
                f"{'P' if row.get('partial') else 'F'}:{row.get('turn_id')}:{row.get('en')}:{row.get('zh')}"
                for row in entries
            )
            if key == self.render_key:
                return
            self.render_key = key
            self.last_change = time.time()
            latest_entry = entries[-1]
            self.last_turn_id = str(latest_entry.get("turn_id", ""))
            print(
                f"subtitle stack {len(entries)} "
                f"{'partial' if latest_entry.get('partial') else 'final'}: "
                f"{str(latest_entry.get('en', ''))[:70]!r} / "
                f"{str(latest_entry.get('zh', ''))[:70]!r}",
                flush=True,
            )
            self._render_stack(entries)
            self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")
            self._show()
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
