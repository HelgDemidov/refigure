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


# --- rate-limit (phase-3 spec §4) -------------------------------------------


def test_rate_limit_admits_up_to_the_configured_count() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=2, rate_limit_window_s=60
    )

    assert state.check_and_consume_rate_limit("caller-A") is True
    assert state.check_and_consume_rate_limit("caller-A") is True
    assert state.check_and_consume_rate_limit("caller-A") is False  # 3rd exceeds count=2


def test_rate_limit_is_independent_per_caller() -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=1, rate_limit_window_s=60
    )

    assert state.check_and_consume_rate_limit("caller-A") is True
    assert state.check_and_consume_rate_limit("caller-A") is False
    assert state.check_and_consume_rate_limit("caller-B") is True  # independent counter


def test_rate_limit_repeated_refusals_dont_drift_the_counter() -> None:
    # A refusal must never itself consume quota — otherwise enough refused
    # calls could eventually push the counter past a state where a NEW
    # legitimate call gets admitted by accident.
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=2, rate_limit_window_s=60
    )

    results = [state.check_and_consume_rate_limit("caller-A") for _ in range(5)]

    assert results == [True, True, False, False, False]


def test_rate_limit_window_resets_after_expiry(monkeypatch) -> None:
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=1, rate_limit_window_s=60
    )
    ticks = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr("refigure.mcp.state.time.monotonic", lambda: next(ticks))

    assert state.check_and_consume_rate_limit("caller-A") is True  # t=0, admits (1/1)
    assert state.check_and_consume_rate_limit("caller-A") is False  # t=0, still in window
    assert state.check_and_consume_rate_limit("caller-A") is True  # t=61, window expired, resets


def test_rate_limit_n_aware_admits_or_refuses_the_whole_amount_atomically() -> None:
    # n exists for phase 4's convert_batch (not exercised with n != 1
    # anywhere in phase 3 itself) — a batch admits or refuses as a whole,
    # never partially consuming quota for a refused amount.
    state = ServerState(
        max_entries=10, max_bytes=10_000, ttl_s=3600, rate_limit_count=5, rate_limit_window_s=60
    )

    assert state.check_and_consume_rate_limit("caller-A", n=3) is True  # 3/5
    assert state.check_and_consume_rate_limit("caller-A", n=3) is False  # would be 6/5, refused
    assert state.check_and_consume_rate_limit("caller-A", n=2) is True  # 5/5, exactly fits


# --- soft-cap (phase-3 spec §4) ---------------------------------------------


def test_soft_cap_disabled_by_default_allows_unbounded_growth_for_one_caller() -> None:
    state = ServerState(
        max_entries=100,
        max_bytes=100_000,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
    )
    first_id = state.insert("caller-A", "first")
    for i in range(50):
        state.insert("caller-A", f"more-{i}")

    assert state.get(first_id, "caller-A") == "first"  # never self-evicted, soft-cap off


def test_soft_cap_self_evicts_the_flooding_callers_own_oldest_entry() -> None:
    # max_entries=4 -> quota_entries = max(1, 4 // 4) = 1 per caller.
    state = ServerState(
        max_entries=4,
        max_bytes=100_000,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
        soft_cap_enabled=True,
    )
    first_id = state.insert("caller-A", "first")

    second_id = state.insert("caller-A", "second")  # self-evicts "first"

    assert state.get(first_id, "caller-A") is None
    assert state.get(second_id, "caller-A") == "second"


def test_soft_cap_flooding_caller_never_evicts_another_callers_entry() -> None:
    state = ServerState(
        max_entries=4,
        max_bytes=100_000,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
        soft_cap_enabled=True,
    )
    b_id = state.insert("caller-B", "b-entry")

    for i in range(20):
        state.insert("caller-A", f"flood-{i}")

    assert state.get(b_id, "caller-B") == "b-entry"


def test_soft_cap_self_eviction_triggers_on_the_byte_budget_too() -> None:
    # quota_bytes = max(1, 100 // 4) = 25 — a second insert that would push
    # this caller's own tracked bytes over 25 self-evicts, even though
    # quota_entries (100) is nowhere near hit.
    state = ServerState(
        max_entries=100,
        max_bytes=100,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
        soft_cap_enabled=True,
    )
    first_id = state.insert("caller-A", "x" * 20)

    second_id = state.insert("caller-A", "y" * 20)  # 20+20=40 > quota_bytes=25

    assert state.get(first_id, "caller-A") is None
    assert state.get(second_id, "caller-A") == "y" * 20


def test_on_evict_keeps_per_caller_bookkeeping_correct_on_global_eviction() -> None:
    # max_entries=4 -> quota_entries=1 per caller, but 5 DISTINCT callers
    # each inserting once (each individually within their own quota — 0
    # existing entries is under a quota of 1) still overflows the shared
    # store's global count limit on the 5th insert. That eviction goes
    # through on_evict, not soft-cap's own self-eviction path — this
    # confirms _by_caller/_caller_bytes stay consistent either way.
    state = ServerState(
        max_entries=4,
        max_bytes=100_000,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
        soft_cap_enabled=True,
    )
    ids = {
        caller: state.insert(caller, f"doc-from-{caller}")
        for caller in ("c1", "c2", "c3", "c4", "c5")
    }

    assert state.get(ids["c1"], "c1") is None  # evicted by the GLOBAL count limit
    assert state.get(ids["c5"], "c5") == "doc-from-c5"

    # on_evict must have cleaned up c1's per-caller index — otherwise a
    # second insert for c1 would wrongly self-evict against a stale entry
    # it no longer actually holds.
    assert len(state._by_caller.get("c1", {})) == 0
    assert state._caller_bytes.get("c1", 0) == 0
