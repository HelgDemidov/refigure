"""``refigure/mcp/_lru.py`` — the generic bounded-LRU + byte-budget store
shared by ``ServerState`` and ``BoundedLruVlmCache`` (architecture doc
§7-bis)."""

from __future__ import annotations

from refigure.mcp._lru import BoundedLru


def test_count_eviction_drops_the_oldest_entry() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=3, max_bytes=10_000)
    lru.insert("a", "A", 1)
    lru.insert("b", "B", 1)
    lru.insert("c", "C", 1)

    lru.insert("d", "D", 1)  # over max_entries=3 -> evicts "a" (oldest)

    assert len(lru) == 3
    assert lru.get("a") is None
    assert lru.get("b") == "B"
    assert lru.get("c") == "C"
    assert lru.get("d") == "D"


def test_get_touches_an_entry_as_most_recently_used() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=3, max_bytes=10_000)
    lru.insert("a", "A", 1)
    lru.insert("b", "B", 1)
    lru.insert("c", "C", 1)

    lru.get("a")  # "a" is now MRU, "b" becomes the oldest
    lru.insert("d", "D", 1)  # evicts "b", not "a"

    assert lru.get("a") == "A"
    assert lru.get("b") is None
    assert lru.get("c") == "C"


def test_byte_budget_eviction_subtracts_only_the_evicted_entrys_size() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=100, max_bytes=25)
    lru.insert("x", "X", 10)
    lru.insert("y", "Y", 10)
    assert lru.total_bytes == 20

    lru.insert("z", "Z", 10)  # total would be 30 > 25 -> evicts "x" (10 bytes)

    assert lru.total_bytes == 20
    assert lru.get("x") is None
    assert lru.get("y") == "Y"
    assert lru.get("z") == "Z"


def test_both_limits_evict_whichever_is_hit_first() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=2, max_bytes=10_000)
    lru.insert("a", "A", 1)
    lru.insert("b", "B", 1)

    lru.insert("c", "C", 1)  # count limit (2) hits before the byte budget does

    assert len(lru) == 2
    assert lru.get("a") is None


def test_a_single_oversized_entry_is_kept_not_evicted_to_zero() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=5, max_bytes=10)

    lru.insert("big", "BIG", 1000)  # far over max_bytes on its own

    assert len(lru) == 1
    assert lru.get("big") == "BIG"


def test_replacing_a_key_updates_the_running_total_not_a_sum() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=5, max_bytes=10_000)
    lru.insert("k", "V1", 5)

    lru.insert("k", "V2", 7)  # replace, not a second entry

    assert len(lru) == 1
    assert lru.total_bytes == 7
    assert lru.get("k") == "V2"


def test_get_on_a_missing_key_is_a_clean_none() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=5, max_bytes=10_000)

    assert lru.get("doesnotexist") is None
    assert len(lru) == 0
    assert lru.total_bytes == 0


def test_on_evict_fires_for_each_entry_evicted_by_insert() -> None:
    evicted: list[tuple[str, str, int]] = []
    lru: BoundedLru[str] = BoundedLru(
        max_entries=2, max_bytes=10_000, on_evict=lambda k, v, s: evicted.append((k, v, s))
    )
    lru.insert("a", "A", 1)
    lru.insert("b", "B", 1)

    lru.insert("c", "C", 1)  # over max_entries=2 -> evicts "a"

    assert evicted == [("a", "A", 1)]


def test_on_evict_does_not_fire_for_a_same_key_replacement() -> None:
    evicted: list[tuple[str, str, int]] = []
    lru: BoundedLru[str] = BoundedLru(
        max_entries=5, max_bytes=10_000, on_evict=lambda k, v, s: evicted.append((k, v, s))
    )
    lru.insert("k", "V1", 5)

    lru.insert("k", "V2", 7)  # a value update, not an eviction

    assert evicted == []


def test_on_evict_is_optional_and_defaults_to_a_no_op() -> None:
    # No on_evict passed — existing callers (vlm_cache.py, phase-2 state.py)
    # must see unchanged behavior, not a crash from a missing callback.
    lru: BoundedLru[str] = BoundedLru(max_entries=1, max_bytes=10_000)
    lru.insert("a", "A", 1)

    lru.insert("b", "B", 1)  # evicts "a" — must not raise

    assert lru.get("a") is None
    assert lru.get("b") == "B"


def test_remove_returns_the_value_and_shrinks_total_bytes() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=5, max_bytes=10_000)
    lru.insert("a", "A", 7)
    lru.insert("b", "B", 3)
    assert lru.total_bytes == 10

    removed = lru.remove("a")

    assert removed == "A"
    assert lru.get("a") is None
    assert lru.total_bytes == 3
    assert len(lru) == 1


def test_remove_on_a_missing_key_is_none_not_an_exception() -> None:
    lru: BoundedLru[str] = BoundedLru(max_entries=5, max_bytes=10_000)

    assert lru.remove("doesnotexist") is None


def test_remove_does_not_invoke_on_evict() -> None:
    # remove() is an explicit point-eviction the caller already knows
    # about and accounts for itself at the call site — invoking on_evict
    # too would double-count (see ServerState's soft-cap self-eviction,
    # the one real caller of remove()).
    evicted: list[tuple[str, str, int]] = []
    lru: BoundedLru[str] = BoundedLru(
        max_entries=5, max_bytes=10_000, on_evict=lambda k, v, s: evicted.append((k, v, s))
    )
    lru.insert("a", "A", 1)

    lru.remove("a")

    assert evicted == []
