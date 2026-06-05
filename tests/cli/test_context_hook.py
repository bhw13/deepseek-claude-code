"""Tests for the fcc-context-hook Claude Code hook."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cli.context_hook import main
from config.settings import Settings
from core.context.store import FileContextStore


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "shared_context_enabled": True,
        "shared_context_ttl_seconds": 100.0,
        "shared_context_max_sessions": 8,
        "shared_context_max_note_chars": 600,
        "shared_context_max_inject_notes": 5,
    }
    base.update(overrides)
    return Settings.model_construct(**base)


def _store(path: Path) -> FileContextStore:
    return FileContextStore(path, ttl_seconds=100.0, max_sessions=8, max_note_chars=600)


def _run(
    mode: str,
    payload: dict,
    *,
    settings: Settings,
    file_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with (
        patch("config.settings.get_settings", return_value=settings),
        patch("config.paths.shared_context_file_path", return_value=file_path),
    ):
        main([mode])
    return capsys.readouterr().out


def test_user_prompt_submit_records_and_stays_silent_when_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    file_path = tmp_path / "ctx.json"
    out = _run(
        "user-prompt-submit",
        {"session_id": "s1", "prompt": "build the parser"},
        settings=_settings(),
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert out == ""  # no peers yet -> nothing injected
    assert [e.text for e in _store(file_path).snapshot()] == ["build the parser"]


def test_user_prompt_submit_injects_peer_context_excluding_self(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    file_path = tmp_path / "ctx.json"
    settings = _settings()
    _run(
        "user-prompt-submit",
        {"session_id": "s1", "prompt": "peer working on auth"},
        settings=settings,
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    out = _run(
        "user-prompt-submit",
        {"session_id": "s2", "prompt": "my own task"},
        settings=settings,
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "peer working on auth" in context
    assert "my own task" not in context


def test_session_end_forgets_session_and_deletes_last_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    file_path = tmp_path / "ctx.json"
    settings = _settings()
    _run(
        "user-prompt-submit",
        {"session_id": "s1", "prompt": "only note"},
        settings=settings,
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert file_path.exists()

    _run(
        "session-end",
        {"session_id": "s1"},
        settings=settings,
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert not file_path.exists()


def test_disabled_sharing_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    file_path = tmp_path / "ctx.json"
    out = _run(
        "user-prompt-submit",
        {"session_id": "s1", "prompt": "hi"},
        settings=_settings(shared_context_enabled=False),
        file_path=file_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert out == ""
    assert not file_path.exists()


def test_malformed_stdin_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{ not json"))
    with (
        patch("config.settings.get_settings", return_value=_settings()),
        patch(
            "config.paths.shared_context_file_path",
            return_value=tmp_path / "ctx.json",
        ),
    ):
        main(["user-prompt-submit"])  # must not raise
    assert capsys.readouterr().out == ""


def test_unknown_or_missing_mode_is_noop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["nonsense"])
    main([])
    assert capsys.readouterr().out == ""
