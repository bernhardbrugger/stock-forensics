"""Small on-disk cache for HTTP response bodies.

One file per URL: a JSON header line (url, fetched_at) followed by the raw body. The
original fetch time is kept so that provenance (``Source.retrieved_at``) stays truthful
when a response is served from the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

CACHE_DIR_ENV = "STOCK_FORENSICS_CACHE_DIR"


def default_cache_dir() -> Path:
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "stock-forensics"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CacheEntry:
    body: bytes
    fetched_at: datetime


class DiskCache:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._dir = directory if directory is not None else default_cache_dir()
        self._clock = clock

    def _path(self, url: str) -> Path:
        return self._dir / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:40] + ".cache")

    def get(self, url: str, ttl: timedelta | None) -> CacheEntry | None:
        """Return the cached entry, or ``None`` if missing, expired or unreadable.

        ``ttl=None`` means the entry never expires (use for immutable filings).
        """
        try:
            raw = self._path(url).read_bytes()
            header_line, _, body = raw.partition(b"\n")
            header = json.loads(header_line)
            fetched_at = datetime.fromisoformat(header["fetched_at"])
            if header["url"] != url or fetched_at.tzinfo is None:
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if ttl is not None and self._clock() - fetched_at >= ttl:
            return None
        return CacheEntry(body=body, fetched_at=fetched_at)

    def put(self, url: str, body: bytes) -> CacheEntry:
        """Store a body atomically and return the entry. Failures to write are ignored."""
        entry = CacheEntry(body=body, fetched_at=self._clock())
        header = json.dumps({"url": url, "fetched_at": entry.fetched_at.isoformat()})
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(header.encode("utf-8") + b"\n" + body)
                os.replace(tmp_name, self._path(url))
            except OSError:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # a cache that cannot be written must never break the request
        return entry
