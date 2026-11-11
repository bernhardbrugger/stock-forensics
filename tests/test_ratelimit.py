import threading
import time

import pytest

from stock_forensics.providers._ratelimit import RateLimiter, shared_limiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_limiter(per_second: float, fake: FakeTime) -> RateLimiter:
    return RateLimiter(per_second, clock=fake.clock, sleep=fake.sleep)


def test_first_request_does_not_wait() -> None:
    fake = FakeTime()
    make_limiter(10, fake).acquire()
    assert fake.sleeps == []


def test_burst_is_spread_out_below_the_limit() -> None:
    fake = FakeTime()
    limiter = make_limiter(10, fake)
    times: list[float] = []
    for _ in range(25):
        limiter.acquire()
        times.append(fake.now)

    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert min(gaps) >= 0.1 - 1e-9
    # never more than 10 requests inside any window of one second
    for i in range(len(times) - 10):
        assert times[i + 10] - times[i] >= 1.0 - 1e-9


def test_idle_time_is_not_banked_into_a_burst() -> None:
    fake = FakeTime()
    limiter = make_limiter(10, fake)
    limiter.acquire()
    fake.now += 60  # a long pause
    limiter.acquire()
    limiter.acquire()
    assert sum(fake.sleeps) == pytest.approx(0.1)  # only the second quick call waits


def test_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_shared_limiter_is_a_singleton() -> None:
    assert shared_limiter() is shared_limiter()


def test_limit_holds_across_threads() -> None:
    limiter = RateLimiter(500)  # real clock: 50 requests need at least ~0.098 s
    start = time.perf_counter()
    threads = [
        threading.Thread(target=lambda: [limiter.acquire() for _ in range(10)]) for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.perf_counter() - start >= 49 / 500 - 0.01
