"""Unit tests for the file-backed cross-session context store."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.context import ContextEntry, render_peer_context
from core.context.store import FileContextStore


def _store(
    tmp_path: Path,
    *,
    ttl_seconds: float = 100.0,
    max_sessions: int = 8,
    max_note_chars: int = 50,
    clock=None,
) -> FileContextStore:
    path = tmp_path / "run" / "shared-context.json"
    if clock is None:
        return FileContextStore(
            path,
            ttl_seconds=ttl_seconds,
            max_sessions=max_sessions,
            max_note_chars=max_note_chars,
        )
    return FileContextStore(
        path,
        ttl_seconds=ttl_seconds,
        max_sessions=max_sessions,
        max_note_chars=max_note_chars,
        clock=clock,
    )


def test_record_and_snapshot_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("a", "working on auth")
    entries = store.snapshot()
    assert [(e.session_key, e.text) for e in entries] == [("a", "working on auth")]


def test_record_overwrites_same_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("a", "first")
    store.record("a", "second")
    entries = store.snapshot()
    assert len(entries) == 1
    assert entries[0].text == "second"


def test_snapshot_excludes_requested_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("a", "alpha work")
    store.record("b", "beta work")
    keys = {e.session_key for e in store.snapshot(exclude="a")}
    assert keys == {"b"}


def test_snapshot_orders_newest_first(tmp_path: Path) -> None:
    now = {"t": 1.0}
    store = _store(tmp_path, clock=lambda: now["t"])
    store.record("a", "older")
    now["t"] = 2.0
    store.record("b", "newer")
    assert [e.session_key for e in store.snapshot()] == ["b", "a"]


def test_empty_note_does_not_clobber_prior(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("a", "real prompt")
    store.record("a", "   ")  # tool-loop turn -> no text
    assert store.snapshot()[0].text == "real prompt"


def test_empty_session_key_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("", "orphan")
    assert store.snapshot() == []


def test_note_is_truncated_to_max_chars(tmp_path: Path) -> None:
    store = _store(tmp_path, max_note_chars=5)
    store.record("a", "abcdefghij")
    assert store.snapshot()[0].text == "abcde"


def test_ttl_expiry_drops_stale_notes(tmp_path: Path) -> None:
    now = {"t": 0.0}
    store = _store(tmp_path, ttl_seconds=10.0, clock=lambda: now["t"])
    store.record("a", "stale")
    now["t"] = 50.0
    store.record("b", "fresh")
    keys = {e.session_key for e in store.snapshot()}
    assert keys == {"b"}


def test_max_sessions_evicts_least_recently_updated(tmp_path: Path) -> None:
    times = iter(float(i) for i in range(100))
    store = _store(tmp_path, max_sessions=2, clock=lambda: next(times))
    store.record("a", "one")
    store.record("b", "two")
    store.record("c", "three")  # should evict "a" (oldest)
    keys = {e.session_key for e in store.snapshot()}
    assert keys == {"b", "c"}


def test_forget_last_note_deletes_file(tmp_path: Path) -> None:
    path = tmp_path / "run" / "shared-context.json"
    store = FileContextStore(path, ttl_seconds=100.0, max_sessions=8, max_note_chars=50)
    store.record("a", "x")
    store.record("b", "y")
    store.forget("a")
    assert {e.session_key for e in store.snapshot()} == {"b"}
    assert path.exists()
    store.forget("b")
    assert not path.exists()


def test_persists_across_store_instances(tmp_path: Path) -> None:
    """A second process (fresh store on the same file) sees the first's notes."""
    path = tmp_path / "run" / "shared-context.json"
    writer = FileContextStore(
        path, ttl_seconds=100.0, max_sessions=8, max_note_chars=50
    )
    writer.record("s1", "from one")

    reader = FileContextStore(
        path, ttl_seconds=100.0, max_sessions=8, max_note_chars=50
    )
    assert [e.text for e in reader.snapshot()] == ["from one"]


def test_corrupt_file_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "shared-context.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = FileContextStore(path, ttl_seconds=100.0, max_sessions=8, max_note_chars=50)
    assert store.snapshot() == []
    store.record("a", "recovered")
    assert [e.text for e in store.snapshot()] == ["recovered"]


@pytest.mark.parametrize(
    ("ttl_seconds", "max_sessions", "max_note_chars"),
    [
        (0, 1, 1),
        (1.0, 0, 1),
        (1.0, 1, 0),
    ],
)
def test_non_positive_bounds_rejected(
    tmp_path: Path, ttl_seconds: float, max_sessions: int, max_note_chars: int
) -> None:
    with pytest.raises(ValueError):
        FileContextStore(
            tmp_path / "c.json",
            ttl_seconds=ttl_seconds,
            max_sessions=max_sessions,
            max_note_chars=max_note_chars,
        )


def test_render_peer_context_lists_sessions() -> None:
    block = render_peer_context([ContextEntry("sess-123456", "do the thing", 1.0)])
    assert "Shared context from other active Claude Code sessions" in block
    assert "do the thing" in block
    assert "123456" in block


def test_render_peer_context_empty_returns_blank() -> None:
    assert render_peer_context([]) == ""
