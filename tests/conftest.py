"""Shared fixtures: one analysed report of Apple, built once from the recorded data."""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_forensics.report import ForensicReport, analyze
from tests.report_fixtures import AS_OF, FakeSec, make_client


@pytest.fixture(scope="session")
def aapl_report(tmp_path_factory: pytest.TempPathFactory) -> ForensicReport:
    cache_dir: Path = tmp_path_factory.mktemp("sec-cache")
    with make_client(cache_dir, FakeSec()) as client:
        return analyze("AAPL", client=client, as_of=AS_OF)
