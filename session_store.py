"""History sessions stored as local JSONL files.

Each capture appends a session header, turns, optional meeting notes and
subtitle edits.  This module only reads/deletes files inside SESSIONS_DIR; it
never follows a caller-supplied path.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import config

_MODES = {"call", "translate", "meeting"}
_FILE_TIME_RE = re.compile(r"(\d{8})-(\d{6})")


def _decode_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _parse_time(value: Any) -> tuple[datetime | None, float]:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed, parsed.timestamp()
        except Exception:
            pass
    return None, 0.0


def _filename_time(path: Path) -> tuple[datetime, float]:
    match = _FILE_TIME_RE.search(path.stem)
    if match:
        try:
            parsed = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").astimezone()
            return parsed, parsed.timestamp()
        except Exception:
            pass
    fallback = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return fallback, path.stat().st_mtime


def _mode(header: dict[str, Any], turns: list[dict[str, Any]], notes: dict[str, Any] | None) -> str:
    declared = str(header.get("mode", "") or "").lower()
    if declared in _MODES:
        return declared
    if notes:
        return "meeting"
    speakers = {str(turn.get("speaker", "") or "") for turn in turns}
    if "me" in speakers:
        return "call"
    if "other" in speakers:
        return "translate"
    return "unknown"


def _title(mode: str, turns: list[dict[str, Any]], notes: dict[str, Any] | None) -> str:
    if notes:
        title = str(notes.get("title", "") or "").strip()
        summary = str(notes.get("summary", "") or "").strip()
        if title and title not in ("会议纪要", "会议记录"):
            return title[:120]
        if summary:
            return summary[:120]
    for turn in turns:
        text = str(turn.get("en", "") or "").strip()
        if text:
            return text[:120]
    return {"call": "通话", "translate": "听读", "meeting": "会议"}.get(mode, "空会话")


def _scan(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception:
        return None
    header: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    notes: dict[str, Any] | None = None
    for line in raw.splitlines():
        value = _decode_line(line)
        if not value:
            continue
        kind = str(value.get("type", "") or "")
        if kind == "session" and not header:
            header = value
        elif kind == "turn":
            turns.append(value)
        elif kind == "meeting_notes":
            candidate = value.get("notes")
            if isinstance(candidate, dict):
                notes = candidate
        elif kind == "subtitle_edit":
            turn_id = str(value.get("id", "") or "")
            for turn in turns:
                if str(turn.get("id", "") or "") == turn_id:
                    if value.get("en"):
                        turn["en"] = value.get("en")
                    if value.get("zh"):
                        turn["zh"] = value.get("zh")
                    break
    declared_dt, declared_ts = _parse_time(header.get("started_at"))
    file_dt, file_ts = _filename_time(path)
    started = declared_dt or file_dt
    started_ts = declared_ts or file_ts
    mode = _mode(header, turns, notes)
    last_ts = max((float(turn.get("ts", 0) or 0) for turn in turns), default=started_ts)
    last_duration = max((int(turn.get("duration_ms", 0) or 0) for turn in turns), default=0)
    duration_s = max(0.0, (last_ts + last_duration / 1000.0) - started_ts) if turns else 0.0
    size = path.stat().st_size
    return {
        "id": path.name,
        "started_at": started.isoformat(timespec="seconds"),
        "started_ts": started_ts,
        "mode": mode,
        "title": _title(mode, turns, notes),
        "turns": len(turns),
        "duration_s": round(min(duration_s, 24 * 3600), 1),
        "size_bytes": size,
        "has_notes": bool(notes),
        "name": path.name,
    }


def _session_path(session_id: str) -> Path:
    raw = str(session_id or "").strip()
    if not raw or raw != Path(raw).name or not raw.endswith(".jsonl"):
        raise ValueError("无效的会话编号")
    path = (config.SESSIONS_DIR / raw).resolve()
    try:
        path.relative_to(config.SESSIONS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("无效的会话路径") from exc
    if not path.is_file():
        raise FileNotFoundError("会话不存在")
    return path


def list_sessions(active_name: str = "") -> dict[str, Any]:
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in config.SESSIONS_DIR.glob("*.jsonl"):
        row = _scan(path)
        if row is not None:
            row["active"] = bool(active_name and path.name == active_name)
            items.append(row)
    items.sort(key=lambda row: float(row.get("started_ts", 0)), reverse=True)
    total_size = sum(int(row.get("size_bytes", 0) or 0) for row in items)
    return {
        "sessions": items,
        "total": len(items),
        "total_size": total_size,
        "active": active_name or "",
    }


def read_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    header: dict[str, Any] = {}
    order: list[str] = []
    turns: dict[str, dict[str, Any]] = {}
    notes: dict[str, Any] | None = None
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise ValueError(f"读取会话失败：{exc}") from exc
    for line in raw.splitlines():
        value = _decode_line(line)
        if not value:
            continue
        kind = str(value.get("type", "") or "")
        if kind == "session" and not header:
            header = value
        elif kind == "turn":
            turn_id = str(value.get("id", "") or "")
            if not turn_id:
                continue
            if turn_id not in turns:
                order.append(turn_id)
                turns[turn_id] = {}
            turns[turn_id].update(value)
        elif kind == "meeting_notes":
            candidate = value.get("notes")
            if isinstance(candidate, dict):
                notes = candidate
        elif kind == "subtitle_edit":
            turn_id = str(value.get("id", "") or "")
            if turn_id in turns:
                if value.get("en"):
                    turns[turn_id]["en"] = value.get("en")
                if value.get("zh"):
                    turns[turn_id]["zh"] = value.get("zh")
    declared_dt, declared_ts = _parse_time(header.get("started_at"))
    file_dt, file_ts = _filename_time(path)
    started = declared_dt or file_dt
    started_ts = declared_ts or file_ts
    rows = [turns[key] for key in order]
    mode = _mode(header, rows, notes)
    return {
        "id": path.name,
        "started_at": started.isoformat(timespec="seconds"),
        "started_ts": started_ts,
        "mode": mode,
        "title": _title(mode, rows, notes),
        "size_bytes": path.stat().st_size,
        "turns": rows,
        "notes": notes,
    }


def delete_sessions(
    ids: list[str] | None = None,
    older_than_days: int | None = None,
    mode: str = "all",
    active_name: str = "",
) -> dict[str, Any]:
    listed = list_sessions(active_name)["sessions"]
    requested = {str(value) for value in (ids or []) if value}
    cutoff = None
    if older_than_days is not None:
        cutoff = time.time() - max(0, int(older_than_days)) * 86400
    selected = []
    for row in listed:
        name = str(row["id"])
        if requested and name not in requested:
            continue
        if not requested and older_than_days is None:
            continue
        if mode == "unknown":
            if str(row.get("mode")) != "unknown":
                continue
        elif mode in _MODES and str(row.get("mode")) != mode:
            continue
        if cutoff is not None and float(row.get("started_ts", 0) or 0) >= cutoff:
            continue
        selected.append(row)
    deleted: list[str] = []
    skipped_active: list[str] = []
    errors: list[str] = []
    for row in selected:
        name = str(row["id"])
        if row.get("active"):
            skipped_active.append(name)
            continue
        try:
            _session_path(name).unlink()
            deleted.append(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "skipped_active": skipped_active,
        "errors": errors,
        **list_sessions(active_name),
    }
