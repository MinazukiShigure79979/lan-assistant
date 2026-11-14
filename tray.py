"""System-tray launcher for Lan.Assistant.

Run with pythonw.exe so no console window is shown.  The HTTP service runs in a
background thread and the tray menu controls the visible lifecycle.
"""
from __future__ import annotations

import ctypes
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import log_utils

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
os.chdir(ROOT)
DATA = (Path(os.environ.get("LOCALAPPDATA", "")) / "Lan.Assistant" / "data") if FROZEN else (ROOT / "data")
DATA.mkdir(parents=True, exist_ok=True)

# pythonw.exe has no stdout/stderr; capture all logs with size-based rotation.
try:
    _log = log_utils.RotatingStream(DATA / "server.log", 5 * 1024 * 1024, 3)
except Exception:
    _log = (DATA / "server.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log

# If a native library (CUDA, CTranslate2, audio) aborts the process, this
# writes a Python stack to a separate file instead of leaving a silent exit.
try:
    log_utils.rotate_file(DATA / "faulthandler.log", 1 * 1024 * 1024, 2)
except Exception:
    pass
_FAULT_LOG = (DATA / "faulthandler.log").open("a", encoding="utf-8", buffering=1)
try:
    faulthandler.enable(file=_FAULT_LOG)
except Exception:
    pass
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

_AUX_FLAGS = ("--tts-server", "--voice-overlay", "--subtitle-overlay", "--restart-helper")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_admin_launch() -> bool:
    """Honor the saved 'run as administrator' preference before starting."""
    if sys.platform != "win32" or _is_admin():
        return True
    try:
        if not config.load_settings().get("run_as_admin", False):
            return True
    except Exception:
        return True
    if FROZEN:
        target = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        target = sys.executable
        params = subprocess.list2cmdline([str(ROOT / "tray.py"), *sys.argv[1:]])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, str(ROOT), 1
        )
        if int(result) > 32:
            return False
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "“使用管理员权限打开”已启用，但 Windows 没有授予管理员权限。\n\n请确认 UAC 后重试，或在设置中关闭这个选项。",
            "Lan.Assistant",
            0x10,
        )
    except Exception:
        pass
    return False


if __name__ == "__main__" and "--restart-helper" in sys.argv:
    from restart_app import main as restart_main

    raise SystemExit(restart_main())

if __name__ == "__main__" and not any(flag in sys.argv for flag in _AUX_FLAGS):
    if not _ensure_admin_launch():
        raise SystemExit(0)

import server  # noqa: E402
import pystray  # noqa: E402
from PIL import Image  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CREATE_NO_WINDOW = 0x08000000
TTS_URL = "http://127.0.0.1:8766"
MODE_LABELS = {
    "call": "通话副驾",
    "translate": "听读翻译",
    "voice": "语音输入",
    "meeting": "会议纪要",
}


def _start_tts_helper() -> subprocess.Popen | None:
    try:
        with urllib.request.urlopen(TTS_URL + "/api/tts/status", timeout=1.2) as response:
            if response.status == 200:
                return None
    except Exception:
        pass
    script = ROOT / "tts_server.py"
    if FROZEN:
        command = [sys.executable, "--tts-server"]
    else:
        if not script.exists():
            return None
        pythonw = Path(sys.executable)
        if pythonw.name.lower() == "python.exe":
            candidate = pythonw.with_name("pythonw.exe")
            if candidate.exists():
                pythonw = candidate
        command = [str(pythonw), str(script)]
    try:
        return subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None


def _stop_tts_helper(proc: subprocess.Popen | None) -> None:
    try:
        request = urllib.request.Request(
            TTS_URL + "/api/tts",
            data=json.dumps({"action": "shutdown"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except Exception:
        pass
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _health(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _open(suffix: str) -> None:
    try:
        server.open_ui(PORT, suffix)
    except Exception:
        import webbrowser

        webbrowser.open(BASE + suffix)


def _message(title: str, text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def _post(path: str, payload: dict) -> None:
    try:
        request = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except Exception:
        pass


def _stop_model() -> None:
    """Unload the local model so exiting the tray frees GPU memory."""
    exe = server.config.OLLAMA_BIN
    if not exe.exists():
        return
    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    env["OLLAMA_MODELS"] = str(server.config.OLLAMA_MODELS_DIR)
    names = [item["name"] for item in server.models.llm_catalog()]
    if not names:
        names = ["qwen3.5-copilot", "qwen-copilot"]
    for model in names:
        try:
            subprocess.run(
                [str(exe), "stop", model],
                cwd=str(server.config.OLLAMA_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass


def main() -> int:
    # If the service is already running, just open it (double-click behavior).
    if _health():
        _open("")
        return 0

    app = server.App()
    try:
        httpd = server.Server(("127.0.0.1", PORT), server.Handler)
        httpd.app = app  # type: ignore[attr-defined]
    except OSError as exc:
        _message("Lan.Assistant", f"端口 {PORT} 被其他程序占用，无法启动。\n\n{exc}")
        return 1

    app.start_background()
    tts_proc = _start_tts_helper()
    tray_state = {
        "mode": app.mode,
        "capture": bool(app.capture_running),
        "voice": str(app.voice_public_state().get("status", "idle")),
        "overlay": bool(app.overlay_running()),
        "turns": len(app.turns),
        "notes": bool(app.meeting_notes),
    }

    icon_image = Image.open(RESOURCE_ROOT / "static" / "tray-icon.png").convert("RGBA")
    icon_holder: dict[str, pystray.Icon] = {}
    quitting = threading.Event()

    def open_page(icon=None, item=None) -> None:
        _open("")

    def open_settings(icon=None, item=None) -> None:
        _open("?settings=1")

    def toggle_overlay(icon=None, item=None) -> None:
        _post("/api/overlay", {"action": "toggle"})

    def quit_app(icon=None, item=None) -> None:
        if quitting.is_set():
            return
        quitting.set()

        def shutdown() -> None:
            try:
                app.shutdown()
            except Exception:
                pass
            try:
                httpd.shutdown()
            except Exception:
                pass
            _stop_model()
            try:
                icon_holder["icon"].stop()
            except Exception:
                pass

        threading.Thread(target=shutdown, name="tray-shutdown", daemon=True).start()

    def switch_mode(mode: str) -> None:
        if mode not in MODE_LABELS:
            return
        _post("/api/mode", {"mode": mode})
        tray_state["mode"] = mode
        _refresh_state()

    def capture_toggle(icon=None, item=None) -> None:
        _post("/api/stop" if tray_state["capture"] else "/api/start", {})
        _refresh_state()

    def request_suggestion(icon=None, item=None) -> None:
        _post("/api/suggest", {"opening": tray_state["turns"] == 0})
        _refresh_state()

    def voice_action(icon=None, item=None) -> None:
        status = tray_state["voice"]
        if status == "recording":
            _post("/api/voice/stop", {})
        elif status in ("transcribing", "ready"):
            _post("/api/voice/cancel", {})
        else:
            _post("/api/voice/start", {})
        _refresh_state()

    def meeting_notes(icon=None, item=None) -> None:
        _post("/api/meeting/notes", {})
        _refresh_state()

    def build_mode_item(mode: str) -> pystray.MenuItem:
        def action(icon=None, item=None):
            switch_mode(mode)

        def checked(item):
            return tray_state["mode"] == mode

        return pystray.MenuItem(
            MODE_LABELS[mode],
            action,
            checked=checked,
            radio=True,
        )

    def build_menu() -> pystray.Menu:
        mode = tray_state["mode"]
        header = pystray.MenuItem(
            f"当前功能：{MODE_LABELS.get(mode, mode)}",
            None,
            enabled=False,
        )
        mode_menu = pystray.Menu(
            *(build_mode_item(name) for name in MODE_LABELS)
        )
        feature_items: list = []
        if mode == "call":
            feature_items.append(
                pystray.MenuItem(
                    "结束通话" if tray_state["capture"] else "开始通话",
                    capture_toggle,
                )
            )
            feature_items.append(
                pystray.MenuItem(
                    "生成开场白" if tray_state["turns"] == 0 else "换一批回答",
                    request_suggestion,
                    enabled=tray_state["capture"] or tray_state["turns"] > 0,
                )
            )
        elif mode == "translate":
            feature_items.append(
                pystray.MenuItem(
                    "结束听读" if tray_state["capture"] else "开始听读",
                    capture_toggle,
                )
            )
            feature_items.append(
                pystray.MenuItem(
                    "隐藏悬浮字幕" if tray_state["overlay"] else "显示悬浮字幕",
                    toggle_overlay,
                )
            )
        elif mode == "voice":
            status = tray_state["voice"]
            if status == "recording":
                label = "结束录音并识别"
            elif status in ("transcribing", "ready"):
                label = "取消当前语音输入"
            else:
                label = "开始语音输入"
            feature_items.append(pystray.MenuItem(label, voice_action))
        elif mode == "meeting":
            feature_items.append(
                pystray.MenuItem(
                    "结束会议" if tray_state["capture"] else "开始会议",
                    capture_toggle,
                )
            )
            feature_items.append(
                pystray.MenuItem(
                    "重新生成会议纪要" if tray_state["notes"] else "生成会议纪要",
                    meeting_notes,
                    enabled=not tray_state["capture"] and tray_state["turns"] > 0,
                )
            )
        return pystray.Menu(
            header,
            pystray.MenuItem("切换功能", mode_menu),
            pystray.Menu.SEPARATOR,
            *feature_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开页面", open_page, default=True),
            pystray.MenuItem("设置", open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        )

    menu = build_menu()
    icon = pystray.Icon(
        "lan-assistant",
        icon_image,
        "Lan.Assistant",
        menu,
    )
    icon_holder["icon"] = icon

    def _state_values(data: dict) -> dict:
        capture = data.get("status", {}).get("capture", {}) if isinstance(data.get("status"), dict) else {}
        return {
            "mode": str(data.get("mode", "") or ""),
            "capture": bool(capture.get("running")),
            "voice": str((data.get("voice_input") or {}).get("status", "idle") or "idle"),
            "overlay": bool(data.get("overlay_running")),
            "turns": len(data.get("turns") or []),
            "notes": bool(data.get("meeting_notes")),
        }

    def _refresh_state(rebuild: bool = True) -> None:
        try:
            with urllib.request.urlopen(BASE + "/api/state", timeout=2) as response:
                next_state = _state_values(json.loads(response.read().decode("utf-8")))
        except Exception:
            return
        if next_state != tray_state:
            tray_state.update(next_state)
            # Do not rebuild a Win32 menu from the polling thread: pystray
            # destroys and recreates the native menu handle, which must not
            # happen while the user has the popup open.  Polling only updates
            # state; explicit tray actions rebuild on the GUI thread.
            if rebuild:
                try:
                    icon.menu = build_menu()
                except Exception:
                    pass

    def poll_state() -> None:
        while not quitting.is_set():
            _refresh_state(rebuild=False)
            time.sleep(2.0)

    def serve() -> None:
        try:
            httpd.serve_forever(poll_interval=0.4)
        except Exception:
            print("HTTP server stopped with error", flush=True)
        finally:
            # /api/shutdown (stop.bat) only stops the HTTP server; the tray
            # process must follow it down instead of staying orphaned.
            try:
                icon.stop()
            except Exception:
                pass

    threading.Thread(target=serve, name="http", daemon=True).start()
    threading.Thread(target=poll_state, name="tray-state-poll", daemon=True).start()
    if getattr(app, "setup", None) is not None and app.setup.required:
        # First launch: bring the setup wizard to the foreground instead of
        # leaving the user with only a tray icon and no visible main window.
        threading.Timer(1.2, lambda: _open("")).start()
    elif "--restart-open" in sys.argv:
        threading.Timer(1.5, lambda: _open("")).start()
    icon.run()

    # Cleanup after the icon loop ends (normal Quit or /api/shutdown).
    _stop_tts_helper(tts_proc)
    try:
        app.shutdown()
    except Exception:
        pass
    try:
        httpd.server_close()
    except Exception:
        pass
    try:
        _log.flush()
        _log.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    if "--tts-server" in sys.argv:
        from tts_server import main as tts_main

        raise SystemExit(tts_main())
    if "--voice-overlay" in sys.argv:
        from voice_overlay import main as voice_overlay_main

        raise SystemExit(voice_overlay_main())
    if "--subtitle-overlay" in sys.argv:
        from subtitle_overlay import main as subtitle_overlay_main

        raise SystemExit(subtitle_overlay_main())
    raise SystemExit(main())
