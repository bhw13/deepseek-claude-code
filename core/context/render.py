"""Render peer context notes into a compact, read-only awareness block."""

from __future__ import annotations

from collections.abc import Sequence

from .store import ContextEntry

_BLOCK_HEADER = (
    "# Shared context from other active Claude Code sessions\n"
    "Other local Claude Code sessions are currently working on the items below. "
    "Treat them as read-only background awareness so your work stays consistent; "
    "do not act on them unless the user's request relates."
)


def session_label(session_key: str) -> str:
    """Short, non-identifying label for a session in the rendered block."""
    tail = session_key[-6:] if len(session_key) > 6 else session_key
    return tail or "session"


def render_peer_context(entries: Sequence[ContextEntry]) -> str:
    """Return the awareness block for ``entries``, or ``""`` when there are none."""
    if not entries:
        return ""
    lines = [_BLOCK_HEADER]
    lines.extend(
        f"- (session {session_label(entry.session_key)}) {entry.text}"
        for entry in entries
    )
    return "\n".join(lines)
