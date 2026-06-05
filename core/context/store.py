"""In-memory, process-lifetime store for cross-session context notes.

Ephemeral by design: nothing is persisted, so shared context can never survive
the proxy process nor bloat across sessions. Each session keeps a single,
overwritten note (bounded length); idle notes expire by TTL and the session
count is capped with least-recently-updated eviction. These three bounds keep
the store small and the injected context fresh, which avoids the context-rot
that an ever-growing history would cause.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """A single session's most recent context note."""

    session_key: str
    text: str
    updated_at: float


class SharedContextStore:
    """Thread-safe ``session_key -> latest note`` map with TTL and size caps."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_sessions: int,
        max_note_chars: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        if max_note_chars <= 0:
            raise ValueError("max_note_chars must be > 0")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._max_note_chars = max_note_chars
        self._clock = clock
        self._entries: dict[str, ContextEntry] = {}
        self._lock = Lock()

    def record(self, session_key: str, text: str) -> None:
        """Store (overwriting) the latest note for ``session_key``.

        No-ops on an empty key or empty note so a tool-loop turn never clobbers
        a session's last real prompt.
        """
        note = text.strip()[: self._max_note_chars].strip()
        if not session_key or not note:
            return
        now = self._clock()
        with self._lock:
            self._entries[session_key] = ContextEntry(
                session_key=session_key, text=note, updated_at=now
            )
            self._evict_locked(now)

    def snapshot(self, *, exclude: str | None = None) -> list[ContextEntry]:
        """Return live notes (newest first), optionally excluding one session."""
        now = self._clock()
        with self._lock:
            self._evict_locked(now)
            entries = [entry for key, entry in self._entries.items() if key != exclude]
        entries.sort(key=lambda entry: entry.updated_at, reverse=True)
        return entries

    def forget(self, session_key: str) -> None:
        """Drop a session's note (e.g. when its terminal disconnects)."""
        with self._lock:
            self._entries.pop(session_key, None)

    def clear(self) -> None:
        """Remove all notes."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __bool__(self) -> bool:
        # A store is always a valid object; never falsy when empty (avoids the
        # ``store or default`` footgun, since ``__len__`` would otherwise make an
        # empty store falsy).
        return True

    def _evict_locked(self, now: float) -> None:
        """Drop expired notes, then enforce the session cap (oldest first)."""
        expiry = now - self._ttl_seconds
        stale = [
            key for key, entry in self._entries.items() if entry.updated_at < expiry
        ]
        for key in stale:
            del self._entries[key]
        overflow = len(self._entries) - self._max_sessions
        if overflow > 0:
            ordered = sorted(self._entries.values(), key=lambda entry: entry.updated_at)
            for entry in ordered[:overflow]:
                del self._entries[entry.session_key]
