"""Unit tests for the ephemeral cross-session context store."""

from __future__ import annotations

import pytest

from core.context.store import SharedContextStore


def _store(*, ttl_seconds=100.0, max_sessions=8, max_note_chars=50, clock=None):
    if clock is None:
        return SharedContextStore(
            ttl_seconds=ttl_seconds,
            max_sessions=max_sessions,
            max_note_chars=max_note_chars,
        )
    return SharedContextStore(
        ttl_seconds=ttl_seconds,
        max_sessions=max_sessions,
        max_note_chars=max_note_chars,
        clock=clock,
    )


def test_record_and_snapshot_roundtrip():
    store = _store()
    store.record("a", "working on auth")
    entries = store.snapshot()
    assert [(e.session_key, e.text) for e in entries] == [("a", "working on auth")]


def test_record_overwrites_same_session():
    store = _store()
    store.record("a", "first")
    store.record("a", "second")
    assert len(store) == 1
    assert store.snapshot()[0].text == "second"


def test_snapshot_excludes_requested_session():
    store = _store()
    store.record("a", "alpha work")
    store.record("b", "beta work")
    keys = {e.session_key for e in store.snapshot(exclude="a")}
    assert keys == {"b"}


def test_snapshot_orders_newest_first():
    times = iter([1.0, 2.0, 3.0])
    store = _store(clock=lambda: next(times))
    store.record("a", "older")
    store.record("b", "newer")
    # snapshot consumes the third clock tick
    assert [e.session_key for e in store.snapshot()] == ["b", "a"]


def test_empty_note_does_not_clobber_prior():
    store = _store()
    store.record("a", "real prompt")
    store.record("a", "   ")  # tool-loop turn -> no text
    assert store.snapshot()[0].text == "real prompt"


def test_empty_session_key_ignored():
    store = _store()
    store.record("", "orphan")
    assert len(store) == 0


def test_note_is_truncated_to_max_chars():
    store = _store(max_note_chars=5)
    store.record("a", "abcdefghij")
    assert store.snapshot()[0].text == "abcde"


def test_ttl_expiry_drops_stale_notes():
    now = {"t": 0.0}
    store = _store(ttl_seconds=10.0, clock=lambda: now["t"])
    store.record("a", "stale")
    now["t"] = 50.0
    store.record("b", "fresh")
    keys = {e.session_key for e in store.snapshot()}
    assert keys == {"b"}


def test_max_sessions_evicts_least_recently_updated():
    times = iter(float(i) for i in range(100))
    store = _store(max_sessions=2, clock=lambda: next(times))
    store.record("a", "one")
    store.record("b", "two")
    store.record("c", "three")  # should evict "a" (oldest)
    keys = {e.session_key for e in store.snapshot()}
    assert keys == {"b", "c"}


def test_forget_and_clear():
    store = _store()
    store.record("a", "x")
    store.record("b", "y")
    store.forget("a")
    assert {e.session_key for e in store.snapshot()} == {"b"}
    store.clear()
    assert len(store) == 0


@pytest.mark.parametrize(
    ("ttl_seconds", "max_sessions", "max_note_chars"),
    [
        (0, 1, 1),
        (1.0, 0, 1),
        (1.0, 1, 0),
    ],
)
def test_non_positive_bounds_rejected(ttl_seconds, max_sessions, max_note_chars):
    with pytest.raises(ValueError):
        SharedContextStore(
            ttl_seconds=ttl_seconds,
            max_sessions=max_sessions,
            max_note_chars=max_note_chars,
        )
