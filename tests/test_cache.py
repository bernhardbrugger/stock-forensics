from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_forensics.providers._cache import CACHE_DIR_ENV, DiskCache, default_cache_dir

URL = "https://data.sec.gov/submissions/CIK0000320193.json"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, clock=Clock())
    cache.put(URL, b'{"a": 1}')
    entry = cache.get(URL, timedelta(hours=1))
    assert entry is not None
    assert entry.body == b'{"a": 1}'
    assert entry.fetched_at == T0


def test_missing_entry_is_none(tmp_path: Path) -> None:
    assert DiskCache(tmp_path).get(URL, timedelta(hours=1)) is None


def test_entry_expires_after_ttl(tmp_path: Path) -> None:
    clock = Clock()
    cache = DiskCache(tmp_path, clock=clock)
    cache.put(URL, b"x")
    clock.now = T0 + timedelta(minutes=59)
    assert cache.get(URL, timedelta(hours=1)) is not None
    clock.now = T0 + timedelta(hours=1)
    assert cache.get(URL, timedelta(hours=1)) is None


def test_ttl_none_never_expires(tmp_path: Path) -> None:
    clock = Clock()
    cache = DiskCache(tmp_path, clock=clock)
    cache.put(URL, b"x")
    clock.now = T0 + timedelta(days=3650)
    assert cache.get(URL, None) is not None


def test_zero_ttl_is_always_stale(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, clock=Clock())
    cache.put(URL, b"x")
    assert cache.get(URL, timedelta(0)) is None


def test_binary_and_multiline_bodies_survive(tmp_path: Path) -> None:
    body = b"line1\nline2\r\n\x00\xff\n"
    cache = DiskCache(tmp_path, clock=Clock())
    cache.put(URL, body)
    entry = cache.get(URL, None)
    assert entry is not None
    assert entry.body == body


def test_put_overwrites_older_entry(tmp_path: Path) -> None:
    clock = Clock()
    cache = DiskCache(tmp_path, clock=clock)
    cache.put(URL, b"old")
    clock.now = T0 + timedelta(hours=2)
    cache.put(URL, b"new")
    entry = cache.get(URL, timedelta(hours=1))
    assert entry is not None
    assert entry.body == b"new"
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "content",
    [
        b"garbage without header",
        b"\n\n",
        b'{"url": "x"}\nbody',
        b'{"url": "https://data.sec.gov/submissions/CIK0000320193.json", '
        b'"fetched_at": "2026-11-11T12:00:00"}\nbody',  # naive timestamp
        b'{"url": "https://other.example/x", "fetched_at": "2026-11-11T12:00:00+00:00"}\nbody',
    ],
)
def test_damaged_entries_are_treated_as_missing(tmp_path: Path, content: bytes) -> None:
    cache = DiskCache(tmp_path, clock=Clock())
    cache.put(URL, b"ok")
    (next(tmp_path.glob("*.cache"))).write_bytes(content)
    assert cache.get(URL, None) is None


def test_unwritable_cache_never_breaks_the_caller(tmp_path: Path) -> None:
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("file in the way")
    cache = DiskCache(blocker / "cache", clock=Clock())
    entry = cache.put(URL, b"x")  # must not raise
    assert entry.body == b"x"
    assert cache.get(URL, None) is None


def test_default_dir_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CACHE_DIR_ENV, str(tmp_path / "custom"))
    assert default_cache_dir() == tmp_path / "custom"


def test_default_dir_is_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CACHE_DIR_ENV, raising=False)
    assert default_cache_dir() == Path.home() / ".cache" / "stock-forensics"
