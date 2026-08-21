"""Async-bridge tests: heartbeat ticks concurrently with a blocked sync
thread, and a hard timeout releases the caller promptly even while the
abandoned thread keeps running (architecture doc §7's two guarantees —
"caller responsiveness" and "pool availability" — verified separately
from "spend", which Config.vlm_max_markers bounds instead).

Targets ``_convert_with_bridge`` directly rather than going through the
full tool/Client layer — the mechanism under test (anyio.to_thread +
CapacityLimiter + fail_after + heartbeat task group) doesn't need a real
MCPServer/Client round-trip, and testing it in isolation keeps a timing
assertion (``elapsed < ...``) from being muddied by unrelated protocol
overhead.
"""

from __future__ import annotations

import time

import anyio
import pytest

from refigure.api import Config, ConversionResult
from refigure.mcp.server import _convert_with_bridge

pytestmark = pytest.mark.anyio


class _RecordingCtx:
    def __init__(self) -> None:
        self.ticks: list[float] = []

    async def report_progress(self, progress, total=None, message=None) -> None:
        self.ticks.append(progress)


def _slow_convert(source, *, config: Config) -> ConversionResult:
    time.sleep(0.6)
    return ConversionResult(markdown="done")


def _instant_convert(source, *, config: Config) -> ConversionResult:
    return ConversionResult(markdown="fast")


async def test_heartbeat_ticks_concurrently_with_the_blocked_thread(monkeypatch) -> None:
    import refigure.mcp.server as server_module

    monkeypatch.setattr(server_module, "_HEARTBEAT_INTERVAL_S", 0.1)
    ctx = _RecordingCtx()

    result = await _convert_with_bridge(
        _slow_convert,
        b"irrelevant",
        Config(),
        mcp_ctx=ctx,  # type: ignore[arg-type]
        limiter=anyio.CapacityLimiter(4),
        timeout_s=5.0,
    )

    assert result.markdown == "done"
    assert len(ctx.ticks) >= 2, "heartbeat did not fire alongside the blocked sync thread"
    assert ctx.ticks == sorted(ctx.ticks), "progress must strictly increase, per the MCP contract"


async def test_hard_timeout_releases_the_caller_promptly() -> None:
    ctx = _RecordingCtx()
    start = time.monotonic()

    with pytest.raises(TimeoutError):
        await _convert_with_bridge(
            _slow_convert,  # sleeps 0.6s
            b"irrelevant",
            Config(),
            mcp_ctx=ctx,  # type: ignore[arg-type]
            limiter=anyio.CapacityLimiter(4),
            timeout_s=0.05,
        )

    elapsed = time.monotonic() - start
    assert elapsed < 0.5, (
        f"caller was not released promptly on timeout: {elapsed:.2f}s "
        "(the abandoned thread may still be running in the background — "
        "that's the documented, accepted trade-off, not a bug)"
    )


async def test_fast_conversion_returns_normally_with_no_timeout_error() -> None:
    result = await _convert_with_bridge(
        _instant_convert,
        b"irrelevant",
        Config(),
        mcp_ctx=_RecordingCtx(),  # type: ignore[arg-type]
        limiter=anyio.CapacityLimiter(4),
        timeout_s=5.0,
    )
    assert result.markdown == "fast"


async def test_capacity_limiter_bounds_concurrent_conversions() -> None:
    active = 0
    max_seen = 0

    def _tracked(source, *, config: Config) -> ConversionResult:
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        time.sleep(0.2)
        active -= 1
        return ConversionResult(markdown="ok")

    async def _run_one() -> None:
        await _convert_with_bridge(
            _tracked,
            b"x",
            Config(),
            mcp_ctx=_RecordingCtx(),  # type: ignore[arg-type]
            limiter=limiter,
            timeout_s=10.0,
        )

    limiter = anyio.CapacityLimiter(2)
    async with anyio.create_task_group() as tg:
        for _ in range(5):
            tg.start_soon(_run_one)

    assert max_seen <= 2, f"limiter did not bound concurrency, saw {max_seen} in flight"
