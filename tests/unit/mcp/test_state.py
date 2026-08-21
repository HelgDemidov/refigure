"""``refigure/mcp/state.py`` — ``ServerState`` (the LRU conversion-result
store behind ``refigure://conversion/{id}``) and ``resolve_caller_id()``."""

from __future__ import annotations

from refigure.mcp.state import _LOCAL_CALLER_ID, ServerState, resolve_caller_id


def test_resolve_caller_id_is_the_local_sentinel_outside_any_request() -> None:
    # No MCP request context is active here (no Client/server round trip) —
    # get_access_token() has nothing to read, same as a real stdio call.
    assert resolve_caller_id() == _LOCAL_CALLER_ID


def test_insert_then_get_round_trips_the_markdown() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=30, rate_limit_window_s=60
    )

    conversion_id = state.insert("caller-A", "hello world markdown")

    assert state.get(conversion_id, "caller-A") == "hello world markdown"


def test_get_with_the_wrong_caller_id_is_not_found_not_forbidden() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=30, rate_limit_window_s=60
    )
    conversion_id = state.insert("caller-A", "secret to caller-A")

    result = state.get(conversion_id, "caller-B")

    # None, not a distinct "forbidden" outcome — see ServerState's own
    # docstring: distinguishing them would leak that SOMETHING exists at
    # this id to a caller who shouldn't even know that.
    assert result is None


def test_get_on_an_unknown_id_is_none() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=30, rate_limit_window_s=60
    )

    assert state.get("doesnotexist", "caller-A") is None


def test_expired_ttl_is_treated_as_not_found(monkeypatch) -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=100, rate_limit_count=30, rate_limit_window_s=60
    )
    ticks = iter([0.0, 200.0])  # insert() reads once (0.0), get() reads once (200.0)
    monkeypatch.setattr("refigure.mcp.state.time.monotonic", lambda: next(ticks))

    conversion_id = state.insert("caller-A", "expires later")

    assert state.get(conversion_id, "caller-A") is None


def test_insert_returns_a_url_safe_token_of_the_expected_shape() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=30, rate_limit_window_s=60
    )

    conversion_id = state.insert("caller-A", "x")

    # secrets.token_urlsafe(16) -> a URL-safe base64 string, no padding.
    assert conversion_id
    assert all(c.isalnum() or c in "-_" for c in conversion_id)


def test_lru_and_byte_eviction_apply_through_server_state_too() -> None:
    state = ServerState(
        max_entries=1, max_bytes=10_000, ttl_s=3600, rate_limit_count=30, rate_limit_window_s=60
    )
    first_id = state.insert("caller-A", "first")

    second_id = state.insert("caller-A", "second")  # evicts the first entry

    assert state.get(first_id, "caller-A") is None
    assert state.get(second_id, "caller-A") == "second"
