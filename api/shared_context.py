"""Cross-session context sharing for the Claude-compatible proxy.

Bridges the neutral :class:`~core.context.store.SharedContextStore` to Anthropic
request shapes. For every real provider-bound turn it:

1. resolves a stable per-terminal session key (HTTP session header, else the
   request ``metadata.user_id``),
2. injects a compact, read-only awareness block built from *other* active
   sessions' latest prompts into the outgoing system prompt, then
3. records this turn's prompt so peer sessions can see it.

Mid tool-loop calls (whose last message carries no user text) are skipped, so
the injected system prompt only changes on a fresh human turn -- keeping a
single terminal's behavior unchanged and provider prompt caches stable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import FastAPI
from starlette.datastructures import Headers

from config.settings import Settings
from core.anthropic import extract_text_from_content
from core.context.store import ContextEntry, SharedContextStore
from core.trace import extract_claude_session_id_from_headers, trace_event

from .models.anthropic import Message, MessagesRequest, SystemContent

_DEFAULT_SESSION_KEY = "default"
_BLOCK_HEADER = (
    "# Shared context from other active Claude Code sessions\n"
    "Other local sessions sharing this proxy are currently working on the items "
    "below. Treat them as read-only background awareness so your work stays "
    "consistent; do not act on them unless the user's request relates."
)


def create_store(settings: Settings) -> SharedContextStore:
    """Build a store from settings (used at startup and as a lazy fallback)."""
    return SharedContextStore(
        ttl_seconds=settings.shared_context_ttl_seconds,
        max_sessions=settings.shared_context_max_sessions,
        max_note_chars=settings.shared_context_max_note_chars,
    )


def build_shared_context_manager(
    app: FastAPI, settings: Settings, headers: Headers
) -> SharedContextManager | None:
    """Return a per-request manager, or ``None`` when the feature is disabled."""
    if not settings.shared_context_enabled:
        return None
    return SharedContextManager(
        _get_or_create_store(app, settings),
        max_inject_notes=settings.shared_context_max_inject_notes,
        header_session_id=extract_claude_session_id_from_headers(headers),
    )


def _get_or_create_store(app: FastAPI, settings: Settings) -> SharedContextStore:
    """Reuse the app-scoped store, creating one if startup did not (test apps)."""
    existing = getattr(app.state, "shared_context_store", None)
    if isinstance(existing, SharedContextStore):
        return existing
    store = create_store(settings)
    app.state.shared_context_store = store
    return store


class SharedContextManager:
    """Capture each session's latest prompt and inject peers' prompts."""

    def __init__(
        self,
        store: SharedContextStore,
        *,
        max_inject_notes: int,
        header_session_id: str | None = None,
    ) -> None:
        self._store = store
        self._max_inject_notes = max(0, max_inject_notes)
        self._header_session_id = header_session_id

    def process(self, request: MessagesRequest) -> None:
        """Inject peer context then record this turn's prompt, both in place."""
        text = _latest_user_text(request.messages)
        if not text:
            # No fresh human turn (e.g. mid tool-loop): skip to stay cache-stable.
            return
        session_key = self._resolve_session_key(request)
        self._inject(request, exclude=session_key)
        self._store.record(session_key, text)

    def _resolve_session_key(self, request: MessagesRequest) -> str:
        if self._header_session_id:
            return self._header_session_id
        metadata = request.metadata
        if isinstance(metadata, Mapping):
            user_id = metadata.get("user_id")
            if isinstance(user_id, str) and user_id:
                return user_id
        return _DEFAULT_SESSION_KEY

    def _inject(self, request: MessagesRequest, *, exclude: str) -> None:
        if self._max_inject_notes <= 0:
            return
        entries = self._store.snapshot(exclude=exclude)
        if not entries:
            return
        selected = entries[: self._max_inject_notes]
        request.system = _append_system_text(request.system, _render_block(selected))
        trace_event(
            stage="ingress",
            event="api.shared_context.injected",
            source="api",
            note_count=len(selected),
        )


def _latest_user_text(messages: Sequence[Message]) -> str:
    """Return the trailing user message's text, or ``""`` for a tool-loop turn."""
    if not messages:
        return ""
    last = messages[-1]
    if last.role != "user":
        return ""
    return extract_text_from_content(last.content).strip()


def _session_label(session_key: str) -> str:
    """Short, non-identifying label for a session in the injected block."""
    tail = session_key[-6:] if len(session_key) > 6 else session_key
    return tail or "session"


def _render_block(entries: Sequence[ContextEntry]) -> str:
    lines = [_BLOCK_HEADER]
    lines.extend(
        f"- (session {_session_label(entry.session_key)}) {entry.text}"
        for entry in entries
    )
    return "\n".join(lines)


def _append_system_text(
    system: str | list[SystemContent] | None, block: str
) -> str | list[SystemContent]:
    if system is None:
        return block
    if isinstance(system, str):
        return f"{system}\n\n{block}" if system else block
    return [*system, SystemContent(type="text", text=block)]
