from __future__ import annotations

import asyncio
import time

import pytest

from mc_yaml_translator.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_max_streams_caps_concurrency():
    limiter = RateLimiter(max_streams=2, qps=None, qpm=None)
    running = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker():
        nonlocal running, peak
        async with limiter:
            async with lock:
                running += 1
                peak = max(peak, running)
            await asyncio.sleep(0.05)
            async with lock:
                running -= 1

    await asyncio.gather(*(worker() for _ in range(8)))
    assert peak == 2


@pytest.mark.asyncio
async def test_qps_spaces_requests():
    limiter = RateLimiter(max_streams=8, qps=8, qpm=None)  # 1/8 s interval
    times: list[float] = []

    async def worker():
        async with limiter:
            times.append(time.monotonic())

    t0 = time.monotonic()
    await asyncio.gather(*(worker() for _ in range(4)))
    elapsed = time.monotonic() - t0
    times.sort()
    # 4 starts at 8 qps need ~ 3 intervals of 0.125s ≈ 0.375s
    assert elapsed >= 0.25
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.10 for g in gaps)
