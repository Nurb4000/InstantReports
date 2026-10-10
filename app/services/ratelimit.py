"""In-memory sliding-window rate limiter for authentication endpoints.

Used to slow online password guessing against ``/auth/login``. The store is
per-process, so a multi-worker deployment (the entrypoint runs ``--workers 2``)
does not enforce an exact global limit; it still meaningfully raises the cost of
brute forcing. A shared store (Redis) would be required for exact enforcement.

The clock is injectable so the behaviour is unit-testable without sleeping.
"""
from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable


class RateLimiter:
    """Allow at most ``max_attempts`` events per ``window_seconds`` per key."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        kept = [t for t in self._events.get(key, []) if t > cutoff]
        if kept:
            self._events[key] = kept
        else:
            self._events.pop(key, None)
        return kept

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait, or 0 if within the limit.

        Does not record an event; call :meth:`record` on failure.
        """
        now = self._clock()
        events = self._prune(key, now)
        if len(events) < self.max_attempts:
            return 0
        oldest = min(events)
        remaining = self.window_seconds - (now - oldest)
        return max(1, int(remaining) + 1)

    def record(self, key: str) -> None:
        self._events[key].append(self._clock())

    def reset(self, key: str) -> None:
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()
