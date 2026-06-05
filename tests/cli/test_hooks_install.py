"""Tests for registering the shared-context hooks in Claude Code settings."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.hooks_install import ensure_context_hooks_installed, remove_context_hooks

_WHICH = "cli.hooks_install.shutil.which"


def _read(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _commands(data: dict, event: str) -> list[str]:
    return [h["command"] for g in data["hooks"].get(event, []) for h in g["hooks"]]


def test_ensure_creates_settings_with_both_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    with patch(_WHICH, return_value="/usr/bin/fcc-context-hook"):
        changed = ensure_context_hooks_installed(settings_path=settings)

    assert changed is True
    data = _read(settings)
    ups = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert ups["command"] == "/usr/bin/fcc-context-hook"
    assert ups["args"] == ["user-prompt-submit"]
    assert data["hooks"]["SessionEnd"][0]["hooks"][0]["args"] == ["session-end"]


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    with patch(_WHICH, return_value="/usr/bin/fcc-context-hook"):
        assert ensure_context_hooks_installed(settings_path=settings) is True
        assert ensure_context_hooks_installed(settings_path=settings) is False


def test_ensure_preserves_existing_settings_and_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "my-own.sh"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    with patch(_WHICH, return_value="/usr/bin/fcc-context-hook"):
        ensure_context_hooks_installed(settings_path=settings)

    data = _read(settings)
    assert data["model"] == "opus"
    commands = _commands(data, "UserPromptSubmit")
    assert "my-own.sh" in commands
    assert "/usr/bin/fcc-context-hook" in commands


def test_ensure_refreshes_moved_command_path(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    with patch(_WHICH, return_value="/old/fcc-context-hook"):
        ensure_context_hooks_installed(settings_path=settings)
    with patch(_WHICH, return_value="/new/fcc-context-hook"):
        changed = ensure_context_hooks_installed(settings_path=settings)

    assert changed is True
    # Old entry replaced in place, not duplicated.
    assert _commands(_read(settings), "UserPromptSubmit") == ["/new/fcc-context-hook"]


def test_remove_drops_only_our_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "my-own.sh"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    with patch(_WHICH, return_value="/usr/bin/fcc-context-hook"):
        ensure_context_hooks_installed(settings_path=settings)

    assert remove_context_hooks(settings_path=settings) is True
    data = _read(settings)
    assert _commands(data, "UserPromptSubmit") == ["my-own.sh"]
    # The event that only held our hook is dropped entirely.
    assert "SessionEnd" not in data["hooks"]


def test_remove_returns_false_when_absent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    assert remove_context_hooks(settings_path=settings) is False


def test_ensure_context_hooks_wrapper_prints_only_on_change(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cli import entrypoints

    with patch("cli.hooks_install.ensure_context_hooks_installed", return_value=True):
        entrypoints._ensure_context_hooks()
    assert "Registered" in capsys.readouterr().out

    with patch("cli.hooks_install.ensure_context_hooks_installed", return_value=False):
        entrypoints._ensure_context_hooks()
    assert capsys.readouterr().out == ""


def test_ensure_context_hooks_wrapper_swallows_oserror(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cli import entrypoints

    with patch(
        "cli.hooks_install.ensure_context_hooks_installed",
        side_effect=OSError("read-only home"),
    ):
        entrypoints._ensure_context_hooks()  # must not raise

    assert "Could not register" in capsys.readouterr().err
