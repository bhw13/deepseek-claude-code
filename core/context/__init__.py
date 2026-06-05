"""In-memory cross-session context sharing primitives (process-lifetime only)."""

from .store import ContextEntry, SharedContextStore

__all__ = ["ContextEntry", "SharedContextStore"]
