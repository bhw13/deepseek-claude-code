"""Unit and integration tests for cross-session context sharing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from api.app import create_app
from api.models.anthropic import MessagesRequest
from api.shared_context import (
    SharedContextManager,
    _append_system_text,
    build_shared_context_manager,
)
from config.settings import Settings
from core.context.store import SharedContextStore
from providers.nvidia_nim import NvidiaNimProvider


def _request(content, *, metadata=None, system=None) -> MessagesRequest:
    payload: dict = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": content}],
    }
    if metadata is not None:
        payload["metadata"] = metadata
    if system is not None:
        payload["system"] = system
    return MessagesRequest.model_validate(payload)


def _manager(store=None, **kwargs) -> SharedContextManager:
    if store is None:
        store = SharedContextStore(
            ttl_seconds=100.0, max_sessions=8, max_note_chars=600
        )
    kwargs.setdefault("max_inject_notes", 5)
    return SharedContextManager(store, **kwargs)


# ---------------------------------------------------------------------------
# Manager: capture + injection
# ---------------------------------------------------------------------------
def test_process_records_latest_user_prompt():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)
    manager = _manager(store, header_session_id="sessA")
    manager.process(_request("build the parser"))
    assert store.snapshot()[0].text == "build the parser"


def test_process_injects_peer_note_into_system():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)
    store.record("peer", "refactoring the router")

    manager = _manager(store, header_session_id="sessA")
    request = _request("write tests")
    manager.process(request)

    assert isinstance(request.system, str)
    assert "refactoring the router" in request.system
    assert "other active Claude Code sessions" in request.system


def test_session_never_sees_its_own_note():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)
    manager = _manager(store, header_session_id="sessA")

    first = _request("task one")
    manager.process(first)
    second = _request("task two")
    manager.process(second)

    # Second turn excludes sessA's own earlier note -> no injection.
    assert second.system is None


def test_tool_loop_turn_is_skipped():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)
    store.record("peer", "doing something")
    manager = _manager(store, header_session_id="sessA")

    tool_turn = _request(
        [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
    )
    manager.process(tool_turn)

    assert tool_turn.system is None  # no injection
    assert all(e.session_key != "sessA" for e in store.snapshot())  # no capture


def test_disabled_turn_when_no_peers():
    manager = _manager(header_session_id="sessA")
    request = _request("solo work")
    manager.process(request)
    assert request.system is None


def test_session_key_prefers_header_then_metadata():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)

    header_mgr = _manager(store, header_session_id="from-header")
    header_mgr.process(_request("x", metadata={"user_id": "from-meta"}))
    assert store.snapshot()[0].session_key == "from-header"

    store.clear()
    meta_mgr = _manager(store, header_session_id=None)
    meta_mgr.process(_request("y", metadata={"user_id": "from-meta"}))
    assert store.snapshot()[0].session_key == "from-meta"

    store.clear()
    default_mgr = _manager(store, header_session_id=None)
    default_mgr.process(_request("z"))
    assert store.snapshot()[0].session_key == "default"


def test_max_inject_notes_caps_injected_block():
    store = SharedContextStore(ttl_seconds=100.0, max_sessions=8, max_note_chars=600)
    for i in range(5):
        store.record(f"peer{i}", f"peer task {i}")

    manager = _manager(store, header_session_id="sessA", max_inject_notes=2)
    request = _request("my work")
    manager.process(request)

    assert isinstance(request.system, str)
    injected = sum(1 for i in range(5) if f"peer task {i}" in request.system)
    assert injected == 2


# ---------------------------------------------------------------------------
# _append_system_text
# ---------------------------------------------------------------------------
def test_append_system_text_none_returns_block():
    assert _append_system_text(None, "BLOCK") == "BLOCK"


def test_append_system_text_string_concatenates():
    assert _append_system_text("base", "BLOCK") == "base\n\nBLOCK"


def test_append_system_text_list_appends_block():
    result = _append_system_text(
        MessagesRequest.model_validate(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "x"}],
                "system": [{"type": "text", "text": "base"}],
            }
        ).system,
        "BLOCK",
    )
    assert isinstance(result, list)
    assert result[-1].text == "BLOCK"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_build_manager_returns_none_when_disabled():
    settings = Settings()
    settings.shared_context_enabled = False
    app = create_app(lifespan_enabled=False)
    assert build_shared_context_manager(app, settings, Headers({})) is None


# ---------------------------------------------------------------------------
# End-to-end: injected context reaches the provider
# ---------------------------------------------------------------------------
_routed_calls: list = []


async def _capturing_stream(*args, **kwargs):
    _routed_calls.append(args[0])
    yield "event: message_start\ndata: {}\n\n"
    yield "[DONE]\n\n"


@pytest.fixture
def e2e_client():
    _routed_calls.clear()
    app = create_app()
    mock_provider = MagicMock(spec=NvidiaNimProvider)
    mock_provider.stream_response = _capturing_stream

    with (
        patch("api.dependencies.resolve_provider", return_value=mock_provider),
        patch(
            "providers.registry.ProviderRegistry.validate_configured_models",
            new_callable=AsyncMock,
        ),
        patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
        TestClient(app) as client,
    ):
        yield client


def _post(client, text, session_id):
    return client.post(
        "/v1/messages",
        headers={"anthropic-session-id": session_id},
        json={
            "model": "claude-3-sonnet",
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 64,
            "stream": True,
        },
    )


def test_context_flows_between_two_sessions(e2e_client):
    first = _post(e2e_client, "build the JSON parser", "sessA")
    assert first.status_code == 200
    # First session sees no peers -> system untouched.
    assert _routed_calls[0].system is None

    second = _post(e2e_client, "now fix the failing tests", "sessB")
    assert second.status_code == 200
    injected_system = _routed_calls[1].system
    assert isinstance(injected_system, str)
    assert "build the JSON parser" in injected_system
