"""Cross-process, file-backed store for shared cross-session context notes.

Ephemeral by design: a single JSON file under ``~/.fcc/run/`` holds one
overwritten note per Claude Code session. Multiple local processes (``cc`` and
``ds``) read and write it through their ``UserPromptSubmit`` hook, so terminals
stay aware of each other regardless of where inference runs. Wall-clock
timestamps keep TTL comparisons valid across processes; an advisory file lock
plus atomic replace keep concurrent updates safe. Idle notes expire by TTL and
the session count is capped with least-recently-updated eviction, so the file
stays small and the injected context fresh.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """A single session's most recent context note."""

    session_key: str
    text: str
    updated_at: float


class FileContextStore:
    """Cross-process ``session_key -> latest note`` map persisted as JSON."""

    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: float,
        max_sessions: int,
        max_note_chars: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        if max_note_chars <= 0:
            raise ValueError("max_note_chars must be > 0")
        self._path = Path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._max_note_chars = max_note_chars
        self._clock = clock

    def record(self, session_key: str, text: str) -> None:
        """Store (overwriting) the latest note for ``session_key``.

        No-ops on an empty key or empty note so a tool-loop turn never clobbers
        a session's last real prompt.
        """
        note = text.strip()[: self._max_note_chars].strip()
        if not session_key or not note:
            return
        now = self._clock()
        with FileLock(self._lock_path):
            entries = self._load()
            entries[session_key] = ContextEntry(
                session_key=session_key, text=note, updated_at=now
            )
            self._evict(entries, now)
            self._save(entries)

    def snapshot(self, *, exclude: str | None = None) -> list[ContextEntry]:
        """Return live notes (newest first), optionally excluding one session."""
        now = self._clock()
        with FileLock(self._lock_path):
            entries = self._load()
            if self._evict(entries, now):
                self._save(entries)
            live = [entry for key, entry in entries.items() if key != exclude]
        live.sort(key=lambda entry: entry.updated_at, reverse=True)
        return live

    def forget(self, session_key: str) -> None:
        """Drop a session's note; delete the file once the last note is gone."""
        with FileLock(self._lock_path):
            entries = self._load()
            if entries.pop(session_key, None) is None:
                return
            if entries:
                self._save(entries)
            else:
                self._path.unlink(missing_ok=True)

    def _load(self) -> dict[str, ContextEntry]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        entries: dict[str, ContextEntry] = {}
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            text = value.get("text")
            updated_at = value.get("updated_at")
            if isinstance(text, str) and isinstance(updated_at, int | float):
                entries[key] = ContextEntry(
                    session_key=key, text=text, updated_at=float(updated_at)
                )
        return entries

    def _save(self, entries: dict[str, ContextEntry]) -> None:
        payload = {
            key: {"text": entry.text, "updated_at": entry.updated_at}
            for key, entry in entries.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, self._path)

    def _evict(self, entries: dict[str, ContextEntry], now: float) -> bool:
        """Drop expired notes, then enforce the session cap (oldest first)."""
        expiry = now - self._ttl_seconds
        stale = [key for key, entry in entries.items() if entry.updated_at < expiry]
        for key in stale:
            del entries[key]
        removed = bool(stale)
        overflow = len(entries) - self._max_sessions
        if overflow > 0:
            ordered = sorted(entries.values(), key=lambda entry: entry.updated_at)
            for entry in ordered[:overflow]:
                del entries[entry.session_key]
            removed = True
        return removed
