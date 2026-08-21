"""``refigure/mcp/vlm_cache.py`` — ``BoundedLruVlmCache`` (the shared,
copy-on-get/set VLM cache) and ``acquire_vlm_cache_file_lock`` (the
two-instance guard for ``--mcp-vlm-cache``).

Not ``tests/unit/vlm/test_vlm_cache.py`` — that file covers
``FileCacheBackend``/``InMemoryCacheBackend`` (``refigure/vlm/cache.py``,
core); this one is the MCP-layer's own bounded-LRU implementation.
"""

from __future__ import annotations

import pytest

from refigure.mcp.vlm_cache import BoundedLruVlmCache, acquire_vlm_cache_file_lock


def test_get_returns_a_copy_not_the_stored_reference() -> None:
    """The real aliasing chain this class exists to close: core's
    judge-backfill path (vlm/__init__.py) mutates the dict it gets back
    from cache.get() in place — a cache handing out live references would
    let that in-progress mutation leak into a concurrent conversion's own
    read of the same entry before either finishes."""
    cache = BoundedLruVlmCache(max_entries=10, max_bytes=10_000)
    cache.set("m1", {"model": "x", "markdown": "desc", "judge_verdict": None})

    got = cache.get("m1")
    assert got is not None
    got["judge_verdict"] = ["mutated by the caller"]

    assert cache.get("m1")["judge_verdict"] is None


def test_set_stores_a_copy_not_the_callers_own_dict() -> None:
    """The other half of the same aliasing chain: a caller mutating its
    OWN dict after calling set() must not retroactively corrupt the
    already-stored entry."""
    cache = BoundedLruVlmCache(max_entries=10, max_bytes=10_000)
    value = {"model": "x", "markdown": "desc2", "judge_verdict": None}

    cache.set("m2", value)
    value["judge_verdict"] = ["mutated after set()"]

    assert cache.get("m2")["judge_verdict"] is None


def test_get_on_a_miss_is_none() -> None:
    cache = BoundedLruVlmCache(max_entries=10, max_bytes=10_000)

    assert cache.get("doesnotexist") is None


def test_bounded_eviction_delegates_to_lru_not_a_second_implementation() -> None:
    cache = BoundedLruVlmCache(max_entries=1, max_bytes=10_000)
    cache.set("m1", {"model": "x", "markdown": "a", "judge_verdict": None})

    cache.set("m2", {"model": "x", "markdown": "b", "judge_verdict": None})  # evicts m1

    assert cache.get("m1") is None
    assert cache.get("m2") is not None


def test_two_build_server_calls_on_the_same_path_reject_the_second(tmp_path) -> None:
    path = tmp_path / "cache.json"

    acquire_vlm_cache_file_lock(path)

    with pytest.raises(ValueError, match="already has"):
        acquire_vlm_cache_file_lock(path)


def test_two_different_paths_do_not_conflict(tmp_path) -> None:
    acquire_vlm_cache_file_lock(tmp_path / "a.json")

    acquire_vlm_cache_file_lock(tmp_path / "b.json")  # must not raise


def test_lock_guard_degrades_to_a_warning_without_fcntl(tmp_path, monkeypatch, caplog) -> None:
    import refigure.mcp.vlm_cache as vlm_cache_module

    monkeypatch.setattr(vlm_cache_module, "fcntl", None)

    with caplog.at_level("WARNING"):
        acquire_vlm_cache_file_lock(tmp_path / "cache.json")  # must not raise

    assert "fcntl unavailable" in caplog.text
