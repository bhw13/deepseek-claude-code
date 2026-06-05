"""Claude Code hook that shares one-line context between local sessions.

Registered as ``fcc-context-hook`` and wired into ``~/.claude/settings.json``:

- ``user-prompt-submit``: record this session's latest prompt, then return
  peers' prompts via ``hookSpecificOutput.additionalContext``.
- ``session-end``: drop this session's note (and delete the file when empty).

Both read the hook payload as JSON on stdin and must never fail the surrounding
Claude Code turn, so any error simply exits 0 without output.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from config.settings import Settings
    from core.context import FileContextStore


def _read_payload() -> dict[str, Any]:
    """Parse the hook's stdin JSON, tolerating empty or malformed input."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _build_store() -> tuple[FileContextStore | None, Settings]:
    """Return the file-backed store (or ``None`` when sharing is disabled)."""
    from config.paths import shared_context_file_path
    from config.settings import get_settings
    from core.context import FileContextStore

    settings = get_settings()
    if not settings.shared_context_enabled:
        return None, settings
    store = FileContextStore(
        shared_context_file_path(),
        ttl_seconds=settings.shared_context_ttl_seconds,
        max_sessions=settings.shared_context_max_sessions,
        max_note_chars=settings.shared_context_max_note_chars,
    )
    return store, settings


def _handle_user_prompt_submit(payload: dict[str, Any]) -> None:
    from core.context import render_peer_context

    store, settings = _build_store()
    if store is None:
        return
    session_id = payload.get("session_id")
    prompt = payload.get("prompt")
    if not isinstance(session_id, str) or not session_id or not isinstance(prompt, str):
        return

    block = ""
    max_inject = max(0, settings.shared_context_max_inject_notes)
    if max_inject > 0:
        peers = store.snapshot(exclude=session_id)[:max_inject]
        block = render_peer_context(peers)
    store.record(session_id, prompt)

    if block:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": block,
                }
            },
            sys.stdout,
        )


def _handle_session_end(payload: dict[str, Any]) -> None:
    store, _ = _build_store()
    if store is None:
        return
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id:
        store.forget(session_id)


_HANDLERS = {
    "user-prompt-submit": _handle_user_prompt_submit,
    "session-end": _handle_session_end,
}


def main(argv: Sequence[str] | None = None) -> None:
    """Dispatch the hook mode given as the first argument; never raise."""
    args = list(sys.argv[1:] if argv is None else argv)
    handler = _HANDLERS.get(args[0]) if args else None
    if handler is None:
        return
    try:
        handler(_read_payload())
    except Exception:
        # A hook must never break the Claude Code turn: swallow and exit 0.
        return
