"""Process-wide rate limiter for outgoing requests.

SEC EDGAR allows at most 10 requests per second. The limiter hands out request slots that
are spaced ``1 / max_per_second`` apart, so the limit holds even for bursts and across
threads. The clock and the sleep function are injectable for tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

DEFAULT_MAX_PER_SECOND = 8.0  # below the SEC limit of 10, with a safety margin


class RateLimiter:
    def __init__(
        self,
        max_per_second: float = DEFAULT_MAX_PER_SECOND,
        *,
        clock: Callable[[], float] = time.perf_counter,  # monotonic() is only ~15 ms on Windows
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until the caller may send one request."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)


_shared: RateLimiter | None = None
_shared_lock = threading.Lock()


def shared_limiter() -> RateLimiter:
    """The limiter shared by every client in this process."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = RateLimiter()
        return _shared
