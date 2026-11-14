"""Small local Windows SAPI speech service for the candidate read-aloud buttons.

Runs separately from the main app on port 8766 so speaking never blocks the
conversation server. Uses System.Speech through Windows PowerShell.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import log_utils

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA = (Path(os.environ.get("LOCALAPPDATA", "")) / "Lan.Assistant" / "data") if FROZEN else (ROOT / "data")
PORT = 8766
CREATE_NO_WINDOW = 0x08000000

_log = None
try:
    DATA.mkdir(parents=True, exist_ok=True)
    _log = log_utils.RotatingStream(DATA / "tts.log", 1_000_000, 2)
    sys.stdout = _log
    sys.stderr = _log
except Exception:
    pass


class Speech:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None

    def running(self) -> bool:
        with self.lock:
            if self.proc is None:
                return False
            if self.proc.poll() is None:
                return True
            self.proc = None
            return False

    def stop(self) -> None:
        with self.lock:
            proc = self.proc
            self.proc = None
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

    def speak(self, text: str, lang: str = "en") -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        self.stop()
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
            with self.lock:
                self.proc = subprocess.Popen(
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
                    creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
            return True
        except Exception:
            with self.lock:
                self.proc = None
            return False


SPEECH = Speech()


class Handler(BaseHTTPRequestHandler):
    server_version = "LanAssistantTTS/1.0"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/tts/status":
            self._json({"running": SPEECH.running()})
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/tts":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        if data.get("action") == "shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if data.get("action") == "stop":
            SPEECH.stop()
            self._json({"ok": True, "running": False})
            return
        text = str(data.get("text", "") or "").strip()
        if not text:
            self._json({"ok": False, "error": "text is empty"}, 400)
            return
        ok = SPEECH.speak(text, str(data.get("lang", "en") or "en"))
        self._json({"ok": ok, "running": SPEECH.running()})

    def log_message(self, fmt: str, *args) -> None:
        try:
            print(f"{self.address_string()} - {fmt % args}", flush=True)
        except Exception:
            pass


def main() -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Another helper is already running; nothing to do.
        return 0
    print(f"TTS helper listening on {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SPEECH.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
