"""Per-model rate limiting: max_streams, then qps, then qpm; all set limits apply."""

from __future__ import annotations

import asyncio
import time
from collections import deque

from mc_yaml_translator.config import ModelConfig


class RateLimiter:
    """Async context manager that enforces a model's configured limits.

    All limits that are set are enforced together. A caller must:

    1. acquire a *max_streams* slot (concurrency semaphore);
    2. wait until starting now would not exceed *qps* (min interval 1/qps);
    3. wait until starting now would not exceed *qpm* (sliding 60s window).

    Omitted / zero / "null" limits are treated as unrestricted (a generous
    internal ceiling is still applied to max_streams so a runaway task set
    cannot open unbounded connections).
    """

    WINDOW = 60.0
    UNBOUNDED_STREAMS = 256

    def __init__(
        self,
        max_streams: int | None = None,
        qps: float | None = None,
        qpm: float | None = None,
    ) -> None:
        cap = max_streams if max_streams is not None else self.UNBOUNDED_STREAMS
        self.max_streams = cap
        self.qps = qps
        self.qpm = int(qpm) if qpm is not None else None
        self._sem = asyncio.Semaphore(cap)
        self._lock = asyncio.Lock()
        self._last_start = 0.0
        self._starts: deque[float] = deque()

    @classmethod
    def from_model(cls, model: ModelConfig) -> "RateLimiter":
        return cls(max_streams=model.max_streams, qps=model.qps, qpm=model.qpm)

    async def acquire(self) -> None:
        await self._sem.acquire()
        try:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    wait = 0.0
                    if self.qps:
                        wait = max(wait, (self._last_start + 1.0 / self.qps) - now)
                    if self.qpm:
                        cutoff = now - self.WINDOW
                        while self._starts and self._starts[0] <= cutoff:
                            self._starts.popleft()
                        if len(self._starts) >= self.qpm:
                            wait = max(wait, self._starts[0] + self.WINDOW - now)
                    if wait <= 0:
                        now = time.monotonic()
                        self._last_start = now
                        self._starts.append(now)
                        return
                await asyncio.sleep(wait)
        except BaseException:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()
