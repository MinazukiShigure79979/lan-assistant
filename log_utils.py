"""Small size-based log rotation shared by the tray and helper processes."""
from __future__ import annotations

import threading
from pathlib import Path


def _backup(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def rotate_file(path: Path, max_bytes: int, backups: int = 3) -> None:
    """Rotate ``path`` if it exceeds max_bytes, keeping numbered backups."""
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return
        if backups < 1:
            path.unlink(missing_ok=True)
            return
        _backup(path, backups).unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            source = _backup(path, index)
            if source.exists():
                source.replace(_backup(path, index + 1))
        path.replace(_backup(path, 1))
    except OSError:
        pass


class RotatingStream:
    """A text stdout/stderr replacement that rotates without a restart."""

    encoding = "utf-8"

    def __init__(self, path: Path, max_bytes: int = 5 * 1024 * 1024, backups: int = 3) -> None:
        self.path = Path(path)
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.backups = max(1, int(backups))
        self._lock = threading.RLock()
        self._handle = None
        rotate_file(self.path, self.max_bytes, self.backups)
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)

    def _rotate(self) -> None:
        try:
            if self._handle is not None:
                self._handle.close()
        except Exception:
            pass
        self._handle = None
        rotate_file(self.path, 0, self.backups)
        self._open()

    def write(self, text: object) -> int:
        value = str(text)
        if not value:
            return 0
        with self._lock:
            if self._handle is None:
                self._open()
            try:
                written = self._handle.write(value)
                self._handle.flush()
                if self.path.stat().st_size >= self.max_bytes:
                    self._rotate()
                return written
            except Exception:
                return len(value)

    def flush(self) -> None:
        with self._lock:
            try:
                if self._handle is not None:
                    self._handle.flush()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                if self._handle is not None:
                    self._handle.close()
            except Exception:
                pass
            self._handle = None

    def isatty(self) -> bool:
        return False
