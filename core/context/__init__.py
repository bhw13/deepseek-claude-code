"""Cross-session context sharing primitives (file-backed, ephemeral)."""

from .render import render_peer_context, session_label
from .store import ContextEntry, FileContextStore

__all__ = [
    "ContextEntry",
    "FileContextStore",
    "render_peer_context",
    "session_label",
]
