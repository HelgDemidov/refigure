"""Unit tests for refigure.vlm.cache's two VlmCacheBackend implementations
(InMemoryCacheBackend, FileCacheBackend) against the Protocol contract
defined in refigure.api (get/set) plus FileCacheBackend-specific disk
persistence and corrupted-file handling.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from refigure.vlm.cache import FileCacheBackend, InMemoryCacheBackend

_BACKEND_FACTORIES = {
    "in_memory": lambda tmp_path: InMemoryCacheBackend(),
    "file": lambda tmp_path: FileCacheBackend(tmp_path / "cache.json"),
}


@pytest.fixture(params=list(_BACKEND_FACTORIES))
def backend(request: pytest.FixtureRequest, tmp_path: Path):
    return _BACKEND_FACTORIES[request.param](tmp_path)


# --- Protocol conformance (identical behavior expected from both backends) ---


def test_get_on_fresh_cache_returns_none(backend) -> None:
    assert backend.get("any-key") is None


def test_set_then_get_returns_the_exact_value(backend) -> None:
    value = {"description": "a bar chart", "confidence": 0.9}
    backend.set("key-1", value)
    assert backend.get("key-1") == value


def test_set_on_existing_key_overwrites_previous_value(backend) -> None:
    backend.set("key-1", {"description": "first"})
    backend.set("key-1", {"description": "second"})
    assert backend.get("key-1") == {"description": "second"}


def test_distinct_keys_do_not_collide(backend) -> None:
    backend.set("key-a", {"description": "A"})
    backend.set("key-b", {"description": "B"})
    assert backend.get("key-a") == {"description": "A"}
    assert backend.get("key-b") == {"description": "B"}


# --- FileCacheBackend-specific ---


def test_file_backend_persists_across_independent_instances(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    value = {"description": "a pie chart"}

    first = FileCacheBackend(path)
    first.set("key-1", value)

    second = FileCacheBackend(path)
    assert second.get("key-1") == value


def test_file_backend_set_writes_valid_json_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    backend = FileCacheBackend(path)
    backend.set("key-1", {"description": "a scatter plot"})

    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"key-1": {"description": "a scatter plot"}}


def test_file_backend_construction_on_missing_path_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist-yet" / "cache.json"
    backend = FileCacheBackend(path)
    assert backend.get("any-key") is None


def test_file_backend_creates_parent_directory_on_first_set(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "cache.json"
    backend = FileCacheBackend(path)
    assert not path.parent.exists()

    backend.set("key-1", {"description": "value"})

    assert path.parent.exists()
    assert path.exists()


def test_file_backend_corrupted_file_falls_back_to_empty_cache(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_bytes(b"not valid json at all {{{")

    backend = FileCacheBackend(path)

    assert backend.get("any-key") is None


# --- FileCacheBackend concurrency/atomicity (mcp-server-phase1-skeleton
# spec §3): a single instance shared across concurrently-running
# conversions can have multiple set() calls interleave in one process —
# these guard the lock + atomic-write fix, reproducing the live experiment
# from that spec's design work as a real regression test. ---


def test_file_backend_concurrent_set_loses_no_writes(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    backend = FileCacheBackend(path)
    n_threads, n_keys = 8, 50

    def writer(t: int) -> None:
        for i in range(n_keys):
            backend.set(f"key-{t}-{i}", {"markdown": "x" * 200, "judge_verdict": None})

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(writer, range(n_threads)))

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert len(on_disk) == n_threads * n_keys

    reloaded = FileCacheBackend(path)
    assert all(
        reloaded.get(f"key-{t}-{i}") is not None for t in range(n_threads) for i in range(n_keys)
    )


def test_file_backend_set_failure_mid_write_leaves_the_original_file_intact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.json"
    backend = FileCacheBackend(path)
    backend.set("key-1", {"markdown": "original, untouched"})

    with patch("refigure.vlm.cache.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            backend.set("key-2", {"markdown": "never lands"})

    # The original file survives the failed write unmodified — never a
    # half-written/truncated file — and the crashed set()'s temp file is
    # cleaned up, not left behind.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"key-1": {"markdown": "original, untouched"}}
    leftover_tmp_files = [p for p in tmp_path.iterdir() if p.name != "cache.json"]
    assert leftover_tmp_files == []


def test_file_backend_lock_is_a_real_threading_lock(tmp_path: Path) -> None:
    """Cheap structural guard, not a race reproduction: confirms the fix
    is actually wired up (a regression here would otherwise only show up
    as an occasional flaky failure in the concurrency test above)."""
    backend = FileCacheBackend(tmp_path / "cache.json")
    assert isinstance(backend._lock, type(threading.Lock()))


# --- InMemoryCacheBackend-specific ---


def test_in_memory_backend_instances_are_independent() -> None:
    first = InMemoryCacheBackend()
    second = InMemoryCacheBackend()

    first.set("key-1", {"description": "only in first"})

    assert first.get("key-1") == {"description": "only in first"}
    assert second.get("key-1") is None
