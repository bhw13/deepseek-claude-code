"""Register the shared-context hook into Claude Code's user settings.

``cc`` and ``ds`` call :func:`ensure_context_hooks_installed` on launch to
idempotently merge ``UserPromptSubmit`` + ``SessionEnd`` entries into
``~/.claude/settings.json`` without disturbing the user's other settings or
hooks. ``fcc-uninstall-hooks`` removes them again.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

_HOOK_COMMAND_NAME = "fcc-context-hook"
_HOOK_EVENTS: dict[str, str] = {
    "UserPromptSubmit": "user-prompt-submit",
    "SessionEnd": "session-end",
}


def claude_settings_path() -> Path:
    """Return the user-scoped Claude Code settings file path."""
    return Path.home() / ".claude" / "settings.json"


def _resolve_hook_command() -> str:
    """Absolute path to the installed hook, falling back to the bare name."""
    return shutil.which(_HOOK_COMMAND_NAME) or _HOOK_COMMAND_NAME


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _is_fcc_hook_group(group: Any) -> bool:
    """Whether a matcher group is one we manage (command is our hook)."""
    if not isinstance(group, dict):
        return False
    for handler in group.get("hooks", []):
        if isinstance(handler, dict) and _HOOK_COMMAND_NAME in str(
            handler.get("command", "")
        ):
            return True
    return False


def _desired_group(command: str, mode: str) -> dict[str, Any]:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "args": [mode]}],
    }


def ensure_context_hooks_installed(*, settings_path: Path | None = None) -> bool:
    """Idempotently add our hook entries. Return True if the file changed."""
    path = settings_path or claude_settings_path()
    data = _load_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    command = _resolve_hook_command()

    changed = False
    for event, mode in _HOOK_EVENTS.items():
        existing = hooks.get(event)
        groups = existing if isinstance(existing, list) else []
        # Refresh our entries (in case the resolved path moved) but keep any
        # of the user's own hooks for this event untouched.
        kept = [group for group in groups if not _is_fcc_hook_group(group)]
        new_groups = [*kept, _desired_group(command, mode)]
        if groups != new_groups:
            hooks[event] = new_groups
            changed = True

    if changed:
        data["hooks"] = hooks
        _save_json(path, data)
    return changed


def remove_context_hooks(*, settings_path: Path | None = None) -> bool:
    """Remove our hook entries. Return True if the file changed."""
    path = settings_path or claude_settings_path()
    data = _load_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event in _HOOK_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = [group for group in groups if not _is_fcc_hook_group(group)]
        if len(kept) == len(groups):
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]

    if changed:
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        _save_json(path, data)
    return changed
