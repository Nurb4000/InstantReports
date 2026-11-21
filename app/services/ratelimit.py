"""Sliding-window rate limiter with a pluggable persistence store.

Used to slow online password guessing against ``/auth/login`` and to rate-limit
heavy-compute / API-key endpoints. By default the store is in-process, so a
multi-worker deployment (the entrypoint runs ``--workers 2``) does not enforce an
exact global limit; it still meaningfully raises the cost of abuse. Set
``RATE_LIMIT_BACKEND=file`` to persist state to disk so the window is shared
across workers and survives restarts. A fully exact global limit would still
require a shared store such as Redis.

The event clock is injectable so behaviour is unit-testable without sleeping.
For the file backend, wall-clock time is used (rather than ``time.monotonic``)
because monotonic time is not comparable across processes or restarts.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class RateLimitStore(Protocol):
    """Persistence backend for per-key event timestamps (monotonic/wall secs)."""

    def load(self, key: str) -> list[float]:
        """Return the stored event timestamps for ``key`` (possibly stale)."""

    def save(self, key: str, events: list[float]) -> None:
        """Atomically replace ``key``'s event list with ``events``."""

    def delete(self, key: str) -> None:
        ...

    def clear(self) -> None:
        ...


class InMemoryStore(RateLimitStore):
    """Per-process dict store (the historical default)."""

    def __init__(self) -> None:
        self._events: dict[str, list[float]] = defaultdict(list)

    def load(self, key: str) -> list[float]:
        return list(self._events.get(key, []))

    def save(self, key: str, events: list[float]) -> None:
        self._events[key] = list(events)

    def delete(self, key: str) -> None:
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()


class FileStore(RateLimitStore):
    """Disk-backed store shared across workers.

    Events are persisted as JSON ``{key: [timestamps]}``. Writes are serialised
    with an advisory file lock (``fcntl.flock``) and performed atomically via a
    per-writer temp file + ``os.replace`` so a crash mid-write cannot corrupt the
    state file. Uses wall-clock timestamps so the window is consistent across
    processes.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = str(path)
        self._lock_path = f"{self._path}.lock"
        parent = os.path.dirname(self._path) or "."
        os.makedirs(parent, exist_ok=True)
        # ``_thread_lock`` serialises threads within this worker; the flock below
        # serialises across workers. The lock fd is held for the process lifetime.
        self._thread_lock = threading.Lock()
        self._lock_fd = open(self._lock_path, "w")

    def _read(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        tmp = f"{self._path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self._path)

    def _mutate(self, mutate: Callable[[dict], None]) -> None:
        with self._thread_lock:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                data = self._read()
                mutate(data)
                self._write(data)
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def load(self, key: str) -> list[float]:
        with self._thread_lock:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                return list(self._read().get(key, []))
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def save(self, key: str, events: list[float]) -> None:
        self._mutate(lambda data: data.__setitem__(key, list(events)))

    def delete(self, key: str) -> None:
        self._mutate(lambda data: data.pop(key, None))

    def clear(self) -> None:
        self._mutate(lambda data: data.clear())


class RateLimiter:
    """Allow at most ``max_attempts`` events per ``window_seconds`` per key."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        store: RateLimitStore | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        # ``store`` is optional for backward compatibility; callers that build the
        # limiter directly get an in-memory store unless they pass one explicitly.
        self._store: RateLimitStore = store if store is not None else InMemoryStore()

    @staticmethod
    def _prune(events: list[float], now: float, window: int) -> list[float]:
        cutoff = now - window
        return [t for t in events if t > cutoff]

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait, or 0 if within the limit.

        Does not record an event; call :meth:`record` on failure.
        """
        now = self._clock()
        events = self._prune(self._store.load(key), now, self.window_seconds)
        if len(events) < self.max_attempts:
            return 0
        oldest = min(events)
        remaining = self.window_seconds - (now - oldest)
        return max(1, int(remaining) + 1)

    def record(self, key: str) -> None:
        now = self._clock()
        # Prune then persist the trimmed list so a hot key cannot grow the store
        # without bound.
        events = self._prune(self._store.load(key), now, self.window_seconds)
        events.append(now)
        self._store.save(key, events)

    def reset(self, key: str) -> None:
        self._store.delete(key)

    def clear(self) -> None:
        self._store.clear()


def build_rate_limiter(
    max_attempts: int,
    window_seconds: int,
    *,
    backend: str | None = None,
    file_path: str | os.PathLike | None = None,
    clock: Callable[[], float] | None = None,
) -> RateLimiter:
    """Build a :class:`RateLimiter` using the configured persistence backend.

    ``backend`` defaults to ``settings.RATE_LIMIT_BACKEND`` (``"memory"`` or
    ``"file"``). The file backend uses wall-clock time so the window is shared
    across workers and restarts; the in-memory backend uses a monotonic clock.
    An explicit ``clock`` overrides the backend default (useful for tests).
    """
    from app.config import settings

    backend = backend or getattr(settings, "RATE_LIMIT_BACKEND", "memory")
    if backend == "file":
        path = file_path or settings.RATE_LIMIT_FILE
        return RateLimiter(
            max_attempts,
            window_seconds,
            clock=clock if clock is not None else time.time,
            store=FileStore(path),
        )
    return RateLimiter(
        max_attempts,
        window_seconds,
        clock=clock if clock is not None else time.monotonic,
        store=InMemoryStore(),
    )
