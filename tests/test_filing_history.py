"""SecEdgarClient.get_filing_history: the recent index plus the older pages needed."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from stock_forensics.providers.sec_edgar import SecEdgarClient, SecEdgarError
from stock_forensics.providers.sec_models import parse_submissions
from tests.flag_fixtures import CIKS, FIXTURES, SOURCE, raw_submissions

CIK = CIKS["wmt"]
PAGE1 = "CIK0000104169-submissions-001.json"
PAGE2 = "CIK0000104169-submissions-002.json"


class Index:
    def __init__(self, main: dict[str, object] | None = None, **pages: dict[str, object]) -> None:
        self.main = main if main is not None else raw_submissions("wmt")
        self.pages = pages or {
            name: json.loads((FIXTURES / "older" / name).read_text(encoding="utf-8"))
            for name in (PAGE1, PAGE2)
        }
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith(f"CIK{CIK:010d}.json"):
            return httpx.Response(200, content=json.dumps(self.main).encode())
        match = re.search(r"submissions/(.+\.json)$", url)
        if match and match.group(1) in self.pages:
            return httpx.Response(200, content=json.dumps(self.pages[match.group(1)]).encode())
        return httpx.Response(404)


def client_for(index: Index, tmp_path: Path) -> SecEdgarClient:
    return SecEdgarClient(
        user_agent="stock-forensics-tests test@example.com",
        cache_dir=tmp_path,
        transport=httpx.MockTransport(index),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def test_older_pages_are_parsed_with_their_date_ranges() -> None:
    submissions = parse_submissions(raw_submissions("wmt"), SOURCE)
    assert submissions.older_files == (PAGE1, PAGE2)
    first, second = submissions.older_pages
    assert (first.name, first.filing_from, first.filing_to, first.filing_count) == (
        PAGE1,
        date(2012, 9, 6),
        date(2023, 6, 19),
        2000,
    )
    assert (second.filing_from, second.filing_to) == (date(1994, 2, 14), date(2012, 9, 4))


def test_recent_window_is_enough_when_since_is_recent(tmp_path: Path) -> None:
    index = Index()
    with client_for(index, tmp_path) as client:
        history = client.get_filing_history(CIK, date(2024, 1, 1))
    assert len(index.requests) == 1  # only the main index
    assert history.older_files == (PAGE1, PAGE2)  # nothing was merged


def test_only_the_pages_that_reach_since_are_loaded(tmp_path: Path) -> None:
    index = Index()
    with client_for(index, tmp_path) as client:
        history = client.get_filing_history(CIK, date(2020, 1, 1))
    assert [u.rsplit("/", 1)[-1] for u in index.requests] == [f"CIK{CIK:010d}.json", PAGE1]
    assert history.older_files == (PAGE2,)  # page 2 ends in 2012: not needed
    assert [p.name for p in history.older_pages] == [PAGE2]


def test_all_pages_are_loaded_without_a_since_date(tmp_path: Path) -> None:
    index = Index()
    with client_for(index, tmp_path) as client:
        history = client.get_filing_history(CIK)
    assert len(index.requests) == 3
    assert history.older_pages == ()
    assert history.older_files == ()


def test_merged_history_is_newest_first_and_has_the_recent_and_the_older_filings(
    tmp_path: Path,
) -> None:
    index = Index()
    with client_for(index, tmp_path) as client:
        plain = client.get_submissions(CIK)
        history = client.get_filing_history(CIK, date(2018, 1, 1))
    older_rows = len(index.pages[PAGE1]["accessionNumber"])  # type: ignore[arg-type]
    assert len(history.filings) == len(plain.filings) + older_rows
    keys = [(f.filing_date, f.accession_number) for f in history.filings]
    assert keys == sorted(keys, reverse=True)
    assert history.filings[0].filing_date == plain.filings[0].filing_date
    assert history.filings[-1].filing_date < plain.filings[-1].filing_date
    # the company information stays that of the main index
    assert history.name == plain.name
    assert history.sic_code == plain.sic_code


def test_the_same_filing_on_two_pages_is_kept_once(tmp_path: Path) -> None:
    main = raw_submissions("wmt")
    recent = main["filings"]["recent"]  # type: ignore[index]
    duplicate_page = {name: values[:1] for name, values in recent.items()}  # first recent row
    index = Index(main, **{PAGE1: duplicate_page, PAGE2: duplicate_page})
    with client_for(index, tmp_path) as client:
        plain = client.get_submissions(CIK)
        history = client.get_filing_history(CIK, None)
    assert len(history.filings) == len(plain.filings)


def test_an_unexpected_page_name_is_refused(tmp_path: Path) -> None:
    main = raw_submissions("wmt")
    main["filings"]["files"][0]["name"] = "../../etc/passwd"  # type: ignore[index]
    index = Index(main)
    with client_for(index, tmp_path) as client, pytest.raises(SecEdgarError, match="Unexpected"):
        client.get_filing_history(CIK, None)
    assert len(index.requests) == 1  # the bad name was never requested


def test_a_company_without_older_pages_returns_the_plain_index(tmp_path: Path) -> None:
    index = Index(raw_submissions("tupperware") | {"cik": f"{CIK:010d}"})
    index.main["filings"]["files"] = []  # type: ignore[index]
    with client_for(index, tmp_path) as client:
        history = client.get_filing_history(CIK, None)
    assert len(index.requests) == 1
    assert history.older_pages == ()
