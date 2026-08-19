"""``tests/unit/test_io.py`` — ``refigure._io``'s ``normalize_source``.

``run_with_timeout`` here is a small dependency-free safety net for the
FIFO/named-pipe tests in this module and in ``tests/unit/docx/test_docx.py``/
``tests/unit/xlsx/test_xlsx.py`` (security-audit finding #17): none of
``requirements-dev.txt``'s pinned tools include ``pytest-timeout``,
and this is deliberately not a reason to add a new dependency for 3 tests —
a daemon ``threading.Thread`` with ``join(timeout=...)`` is enough. Using a
daemon thread (not ``concurrent.futures.ThreadPoolExecutor``, whose worker
threads are non-daemon and would otherwise block interpreter exit) means
that even if the fix under test regresses and the call genuinely hangs
forever blocked in a blocking file-open, the test process itself still exits
cleanly — it just reports the timeout as a failure instead of freezing CI.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from refigure._io import NotARegularFileError, normalize_source

_T = TypeVar("_T")


def run_with_timeout(fn: Callable[[], _T], *, timeout: float = 5.0) -> _T:
    """Run ``fn()`` on a daemon thread, failing the test if it doesn't
    return within ``timeout`` seconds instead of letting a regression hang
    the whole suite."""
    result: list[_T] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            error.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        pytest.fail(f"{fn} did not complete within {timeout}s — looks like it hung")
    if error:
        raise error[0]
    return result[0]


def test_normalize_source_passes_through_a_regular_file_path(tmp_path: Path) -> None:
    path = tmp_path / "doc.bin"
    path.write_bytes(b"content")
    assert normalize_source(path) == path


def test_normalize_source_passes_through_bytes() -> None:
    assert normalize_source(b"raw bytes") == b"raw bytes"


def test_normalize_source_reads_a_file_like_object_fully() -> None:
    assert normalize_source(io.BytesIO(b"stream contents")) == b"stream contents"


def test_normalize_source_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(NotARegularFileError):
        normalize_source(tmp_path)


def test_normalize_source_rejects_a_fifo_without_hanging(tmp_path: Path) -> None:
    # The whole point: a FIFO with no writer would make zipfile.ZipFile(path)
    # (inside zipsafe.check_archive, downstream of normalize_source) hang
    # forever with no timeout anywhere in the call chain. is_file() is a
    # non-blocking stat() call — it must reject the FIFO WITHOUT ever
    # opening it, so this test must never actually open the fifo for
    # reading (that would defeat the point and could hang the test itself).
    fifo_path = tmp_path / "pipe"
    os.mkfifo(fifo_path)

    with pytest.raises(NotARegularFileError):
        run_with_timeout(lambda: normalize_source(fifo_path))
