"""Tests for rate-limiter persistence (Gap #12).

The default in-memory limiter resets on restart and is per-process, so it is
ineffective behind the entrypoint's multiple workers. These tests cover the
pluggable store layer that makes the sliding window shareable and
restart-surviving:

* :class:`FileStore` persists events to disk (atomic write + advisory lock) so a
  fresh instance -- or another worker -- sees earlier events.
* :class:`RateLimiter` backed by a file store enforces the window across
  instances, i.e. state survives "restart".
* :func:`build_rate_limiter` selects the configured backend and clock.
"""

from __future__ import annotations

import os
import time

import pytest

from app.services.ratelimit import (
    FileStore,
    InMemoryStore,
    RateLimiter,
    build_rate_limiter,
)


def test_file_store_persists_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    FileStore(path).save("k", [1.0, 2.0])
    # A brand-new instance (simulating a restart / second worker) reads it back.
    assert FileStore(path).load("k") == [1.0, 2.0]


def test_file_store_save_replaces_not_appends(tmp_path):
    path = str(tmp_path / "state.json")
    store = FileStore(path)
    store.save("k", [1.0, 2.0])
    store.save("k", [3.0])
    assert store.load("k") == [3.0]


def test_file_store_delete_and_clear(tmp_path):
    path = str(tmp_path / "state.json")
    store = FileStore(path)
    store.save("a", [1.0])
    store.save("b", [2.0])
    store.delete("a")
    assert store.load("a") == []
    assert store.load("b") == [2.0]
    store.clear()
    assert store.load("b") == []


def test_file_store_does_not_clobber_other_keys(tmp_path):
    """Two writers (e.g. two workers) must not lose each other's keys."""
    path = str(tmp_path / "state.json")
    a = FileStore(path)
    b = FileStore(path)
    a.save("worker-a", [1.0])
    b.save("worker-b", [2.0])
    loaded = FileStore(path).load
    assert loaded("worker-a") == [1.0]
    assert loaded("worker-b") == [2.0]


def test_file_store_survives_corrupt_or_missing(tmp_path):
    path = str(tmp_path / "missing.json")
    # Missing file -> empty, no error.
    assert FileStore(path).load("k") == []
    # Corrupt JSON -> treated as empty rather than raising.
    with open(path, "w") as f:
        f.write("{ not json ")
    assert FileStore(path).load("k") == []


def test_rate_limiter_file_store_enforces_limit_across_instances(tmp_path):
    """The core Gap #12 guarantee: events recorded by one limiter are enforced by
    a fresh limiter sharing the same state file and window."""
    path = str(tmp_path / "state.json")
    clock = lambda: 0.0  # noqa: E731 -- fixed clock for determinism

    first = RateLimiter(max_attempts=2, window_seconds=60, clock=clock, store=FileStore(path))
    first.record("k")
    first.record("k")

    # Fresh limiter, same file/clock/window: it must see both prior events.
    second = RateLimiter(max_attempts=2, window_seconds=60, clock=clock, store=FileStore(path))
    assert second.retry_after("k") > 0  # limit already reached -> must wait


def test_rate_limiter_file_store_prunes_on_record(tmp_path):
    """Recording prunes stale events so a hot key cannot grow the state file."""
    path = str(tmp_path / "state.json")
    t = [0.0]

    def clock():
        return t[0]

    store = FileStore(path)
    limiter = RateLimiter(max_attempts=10, window_seconds=60, clock=clock, store=store)

    limiter.record("k")  # t=0
    t[0] = 120.0  # advance well past the window
    limiter.record("k")  # t=120 -> stale t=0 event must be pruned on save

    assert store.load("k") == [120.0]


def test_build_rate_limiter_memory_backend():
    limiter = build_rate_limiter(3, 30, backend="memory")
    assert isinstance(limiter._store, InMemoryStore)
    # In-memory default uses a monotonic clock.
    assert limiter._clock is time.monotonic


def test_build_rate_limiter_file_backend_uses_wall_clock(tmp_path):
    path = tmp_path / "state.json"
    limiter = build_rate_limiter(3, 30, backend="file", file_path=path)
    assert isinstance(limiter._store, FileStore)
    # File backend uses wall-clock time so the window is consistent across workers.
    assert limiter._clock is time.time


def test_build_rate_limiter_explicit_clock_overrides_backend(tmp_path):
    fixed = lambda: 12.0  # noqa: E731
    limiter = build_rate_limiter(3, 30, backend="file", file_path=tmp_path / "x.json", clock=fixed)
    assert limiter._clock is fixed


def test_rate_limiter_backward_compatible_default_store():
    """Constructing RateLimiter without a store still works (in-memory)."""
    limiter = RateLimiter(2, 10, clock=lambda: 0.0)
    assert isinstance(limiter._store, InMemoryStore)
    limiter.record("k")
    assert limiter.retry_after("k") == 0  # 1 of 2 used
