"""Red flags of real companies, end to end, from recorded filing indexes and document excerpts.

The expected flags are those found by a live run on 2026-11-12 (and reproduced from the
excerpts by scripts/record_flag_fixtures.py). Apple and Walmart are the controls: no flag.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from stock_forensics.flags import RedFlags, Severity, analyze_filings, check_red_flags, years_before
from stock_forensics.flags.models import FlagKind
from stock_forensics.providers.sec_edgar import SecEdgarClient
from tests.flag_fixtures import (
    AS_OF,
    CIKS,
    FIXTURES,
    YEARS,
    excerpt,
    manifest,
    raw_submissions,
    read_excerpt,
    submissions,
)

# (filing date, code, accession number; None for the aggregate flag), newest first
EXPECTED: dict[str, list[tuple[str, str, str | None]]] = {
    "smci": [
        ("2025-02-11", "late_10q", "0001375365-25-000002"),
        ("2024-11-18", "auditor_engaged", "0001375365-24-000040"),
        ("2024-11-13", "late_10q", "0001375365-24-000039"),
        ("2024-10-30", "auditor_resigned", "0001375365-24-000036"),
        ("2024-08-30", "late_10k", "0001375365-24-000031"),
        ("2023-03-15", "auditor_dismissed", "0001375365-23-000023"),
    ],
    "khc": [
        ("2019-08-08", "late_10q", "0001637459-19-000066"),
        ("2019-05-10", "late_10q", "0001637459-19-000035"),
        ("2019-05-06", "non_reliance", "0001637459-19-000033"),
        ("2019-02-28", "late_10k", "0001637459-19-000019"),
    ],
    "wework": [
        ("2024-04-09", "auditor_declined_to_stand", "0001813756-24-000014"),
        ("2024-03-19", "late_10k", "0001193125-24-070358"),
        ("2023-11-13", "late_10q", "0001193125-23-276150"),
        ("2023-11-13", "auditor_declined_to_stand", "0001193125-23-276141"),
        ("2023-03-29", "going_concern_alleviated", "0001813756-23-000016"),
        ("2023-03-17", "late_10k", "0001193125-23-073442"),
        ("2021-12-17", "amendment_restatement", "0001193125-21-361365"),
        ("2021-12-17", "frequent_amendments", None),
        ("2021-12-01", "non_reliance", "0001193125-21-345312"),
        ("2021-10-26", "auditor_dismissed", "0001193125-21-308158"),
        ("2021-05-13", "late_10q", "0001193125-21-160462"),
        ("2021-05-12", "amendment_restatement", "0001193125-21-159283"),
        ("2021-05-12", "non_reliance", "0001193125-21-159275"),
    ],
    "tupperware": [
        ("2024-08-14", "late_10q", "0001008654-24-000064"),
        ("2024-05-10", "late_10q", "0001008654-24-000043"),
        ("2024-03-29", "late_10k", "0001008654-24-000036"),
        ("2024-01-29", "auditor_declined_to_stand", "0001008654-24-000009"),
        ("2023-11-09", "late_10q", "0001008654-23-000091"),
        ("2023-10-27", "auditor_declined_to_stand", "0001008654-23-000087"),
        ("2023-10-13", "going_concern_exists", "0001008654-23-000079"),
        ("2023-08-03", "late_10q", "0001008654-23-000060"),
        ("2023-05-08", "late_10q", "0001008654-23-000037"),
        ("2023-03-16", "late_10k", "0001008654-23-000024"),
        ("2023-03-16", "non_reliance", "0001008654-23-000022"),
        ("2021-08-05", "amendment_restatement", "0001008654-21-000070"),
    ],
    "bbby": [
        ("2023-06-14", "going_concern_exists", "0000886158-23-000059"),
        ("2023-04-26", "late_10k", "0001193125-23-118718"),
        ("2023-01-05", "late_10q", "0001193125-23-001983"),
        ("2021-04-27", "amendment_other", "0000886158-21-000018"),
    ],
    "spirit": [
        ("2026-03-16", "going_concern_exists", "0001628280-26-018265"),
        ("2025-05-12", "late_10q", "0000950103-25-005891"),
        ("2025-03-03", "going_concern_exists", "0001498710-25-000008"),
        ("2024-11-13", "late_10q", "0001193125-24-256230"),
    ],
    "aapl": [],
    "wmt": [],
}


def analyze(key: str) -> RedFlags:
    return analyze_filings(
        submissions(key),
        read_excerpt,
        as_of=AS_OF,
        since=years_before(AS_OF, YEARS[key]),
    )


def summary(result: RedFlags) -> list[tuple[str, str, str | None]]:
    return [(f.filing_date.isoformat(), f.code, f.accession_number) for f in result.flags]


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_flags_of_real_companies(key: str) -> None:
    result = analyze(key)
    if key == "wmt":  # offline, only the recent index is loaded: see the history test below
        assert result.flags == ()
        return
    assert summary(result) == EXPECTED[key]


@pytest.mark.parametrize("key", ["aapl", "wmt"])
def test_controls_have_no_flags_and_the_checks_really_ran(key: str) -> None:
    result = analyze(key)
    assert result.flags == ()
    coverage = {c.kind: c for c in result.coverage}
    assert set(coverage) == set(FlagKind)
    assert coverage[FlagKind.GOING_CONCERN].examined >= 3  # the 10-Ks were read
    assert all(c.flagged == 0 for c in result.coverage)


def test_every_flag_links_to_the_original_document() -> None:
    for key in EXPECTED:
        for flag in analyze(key).flags:
            if flag.accession_number is None:
                continue  # the aggregate "frequent amendments" flag has no single filing
            assert flag.document_url is not None
            digits = flag.accession_number.replace("-", "")
            assert flag.document_url.startswith("https://www.sec.gov/Archives/edgar/data/")
            assert f"/{digits}/" in flag.document_url
            assert flag.source.accession_number == flag.accession_number
            assert flag.source.form_type == flag.form
            assert flag.source.provider == "sec_edgar"
            assert flag.source.endpoint == flag.document_url


def test_flags_that_rest_on_text_quote_their_evidence() -> None:
    for key in EXPECTED:
        for flag in analyze(key).flags:
            if flag.kind in (FlagKind.GOING_CONCERN, FlagKind.RESTATEMENT):
                assert flag.evidence, f"{key} {flag.code} {flag.filing_date}"
                document = excerpt(flag.accession_number or "")
                assert all(" ".join(e.split()) in " ".join(document.split()) for e in flag.evidence)


def test_super_micro_shows_the_whole_story() -> None:
    flags = analyze("smci")
    resigned = next(f for f in flags.flags if f.code == "auditor_resigned")
    assert resigned.severity is Severity.HIGH
    assert resigned.form == "8-K"
    assert any("resignation" in e.lower() or "resigned" in e.lower() for e in resigned.evidence)
    late_annual = next(f for f in flags.flags if f.code == "late_10k")
    assert late_annual.period == date(2024, 6, 30)
    assert "followed 179 days after the notice" in late_annual.detail  # 10-K for fiscal 2024
    assert {f.kind for f in flags.flags} == {FlagKind.AUDITOR_CHANGE, FlagKind.LATE_FILING}


def test_kraft_heinz_restatement_came_with_a_late_annual_report() -> None:
    flags = analyze("khc")
    late = next(f for f in flags.flags if f.code == "late_10k")
    restated = next(f for f in flags.flags if f.code == "non_reliance")
    assert late.filing_date < restated.filing_date
    assert late.severity is Severity.MEDIUM
    assert restated.severity is Severity.HIGH
    assert "The 10-K for the period ended 2018-12-29 followed 99 days" in late.detail


def test_wework_going_concern_is_alleviated_and_tupperwares_is_not() -> None:
    wework = next(f for f in analyze("wework").flags if f.kind is FlagKind.GOING_CONCERN)
    tupperware = next(f for f in analyze("tupperware").flags if f.kind is FlagKind.GOING_CONCERN)
    assert (wework.code, wework.severity) == ("going_concern_alleviated", Severity.MEDIUM)
    assert (tupperware.code, tupperware.severity) == ("going_concern_exists", Severity.HIGH)


def test_spirit_part_iii_amendments_are_noted_not_flagged() -> None:
    result = analyze("spirit")
    amendments = next(c for c in result.coverage if c.kind is FlagKind.AMENDMENT)
    assert (amendments.examined, amendments.flagged) == (2, 0)
    assert any("2 routine amendment(s)" in note for note in amendments.notes)


def test_coverage_says_what_was_examined() -> None:
    result = analyze("tupperware")
    by_kind = {c.kind: (c.examined, c.flagged) for c in result.coverage}
    assert by_kind == {
        FlagKind.AUDITOR_CHANGE: (2, 2),
        FlagKind.RESTATEMENT: (1, 1),
        FlagKind.LATE_FILING: (7, 7),
        FlagKind.GOING_CONCERN: (3, 1),
        FlagKind.AMENDMENT: (1, 1),
    }


def test_missing_older_index_pages_are_stated_in_the_coverage() -> None:
    # Walmart's recent index starts in mid-2023; the older pages are not loaded here
    result = analyze("wmt")
    assert all(
        any("older filing index pages were not loaded" in note for note in c.notes)
        for c in result.coverage
    )
    # a young filing history gets a different note
    young = analyze("tupperware")
    assert not any(
        "older filing index pages were not loaded" in note
        for c in young.coverage
        for note in c.notes
    )


def test_flags_are_newest_first_and_serialize_to_json() -> None:
    result = analyze("wework")
    dates = [f.filing_date for f in result.flags]
    assert dates == sorted(dates, reverse=True)
    assert RedFlags.model_validate_json(result.model_dump_json()) == result
    assert result.of_kind(FlagKind.AMENDMENT)
    assert result.with_severity(Severity.HIGH)
    assert result.company_name == "WeWork Inc."


# --- the full path through the client: index, older pages, documents ------------------------


def wrap(text: str) -> bytes:
    return f"<html><body><p>{text}</p></body></html>".encode()


class SecServer:
    """A fake SEC that serves the recorded index, older pages and document excerpts."""

    def __init__(self, key: str, *, missing: set[str] | None = None) -> None:
        self.key = key
        self.missing = missing or set()
        self.requests: list[str] = []
        self.documents = {entry["url"]: acc for acc, entry in manifest().items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith(f"submissions/CIK{CIKS[self.key]:010d}.json"):
            return httpx.Response(200, content=json.dumps(raw_submissions(self.key)).encode())
        page = re.search(r"submissions/(CIK\d{10}-submissions-\d+\.json)$", url)
        if page:
            return httpx.Response(200, content=(FIXTURES / "older" / page.group(1)).read_bytes())
        accession = self.documents.get(url)
        if accession and accession not in self.missing:
            return httpx.Response(200, content=wrap(excerpt(accession)))
        if accession is None and "/Archives/" in url:  # an annual report without going-concern text
            return httpx.Response(200, content=wrap("Annual report without any such statement."))
        return httpx.Response(404)


def client_for(server: SecServer, tmp_path: Path) -> SecEdgarClient:
    return SecEdgarClient(
        user_agent="stock-forensics-tests test@example.com",
        cache_dir=tmp_path,
        transport=httpx.MockTransport(server),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def test_check_red_flags_through_the_client(tmp_path: Path) -> None:
    server = SecServer("tupperware")
    with client_for(server, tmp_path) as client:
        result = check_red_flags(client, CIKS["tupperware"], years=6, as_of=AS_OF)
    assert summary(result) == EXPECTED["tupperware"]
    assert result.cik == CIKS["tupperware"]
    assert result.since == date(2020, 11, 12)
    assert result.as_of == AS_OF


def test_check_red_flags_loads_older_pages_when_the_window_needs_them(tmp_path: Path) -> None:
    server = SecServer("wmt")
    with client_for(server, tmp_path) as client:
        result = check_red_flags(client, CIKS["wmt"], years=6, as_of=AS_OF)  # since 2020-11-12
    assert any("CIK0000104169-submissions-001.json" in url for url in server.requests)
    assert not any("submissions-002.json" in url for url in server.requests)  # ends in 2012
    assert result.flags == ()
    coverage = {c.kind: c.examined for c in result.coverage}
    assert coverage[FlagKind.GOING_CONCERN] == 6  # the older page supplied the earlier 10-Ks
    assert not any(
        "older filing index pages were not loaded" in n for c in result.coverage for n in c.notes
    )


def test_an_unreadable_document_is_flagged_from_the_index_and_noted(tmp_path: Path) -> None:
    resigned = next(
        acc
        for acc, e in manifest().items()
        if e["company"] == "smci" and e["filing_date"] == "2024-10-30"
    )
    server = SecServer("smci", missing={resigned})
    with client_for(server, tmp_path) as client:
        result = check_red_flags(client, CIKS["smci"], years=6, as_of=AS_OF)
    flag = next(f for f in result.flags if f.accession_number == resigned)
    assert flag.code == "auditor_changed"  # the reason could not be read
    assert flag.evidence == ()
    auditor = next(c for c in result.coverage if c.kind is FlagKind.AUDITOR_CHANGE)
    assert "8-K filed 2024-10-30 could not be read" in auditor.notes
