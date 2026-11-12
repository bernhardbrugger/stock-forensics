"""Text helpers and the going-concern detector."""

from __future__ import annotations

import re
from datetime import date

import pytest

from stock_forensics.flags.going_concern import (
    DOUBT,
    analyze_text,
    check_going_concern,
    classify,
)
from stock_forensics.flags.models import FlagKind, Severity
from stock_forensics.flags.text import html_to_text, section_after, sentences_matching
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing
from tests.flag_fixtures import SOURCE, excerpt, find_accession, manifest

# --- text helpers -----------------------------------------------------------------------


def test_html_to_text_strips_markup_and_decodes_entities() -> None:
    html = (
        "<html><head><style>p {color: red}</style><script>var x = 1;</script></head>"
        "<body><p>The Company&#8217;s auditor&nbsp;resigned.</p>\n"
        "<p>Next   paragraph &amp; more.</p>"
        "</body></html>"
    )
    assert html_to_text(html) == "The Company’s auditor resigned. Next paragraph & more."
    assert html_to_text(html.encode()) == html_to_text(html)


def test_sentences_matching_returns_the_whole_sentence_once() -> None:
    text = (
        "First sentence. There is substantial doubt about X. Another one. "
        "Again substantial doubt appears here. There is substantial doubt about X."
    )
    found = sentences_matching(text, DOUBT)
    assert found == [
        "There is substantial doubt about X.",
        "Again substantial doubt appears here.",
    ]


def test_sentences_matching_limits_very_long_sentences() -> None:
    text = "a " * 1000 + "substantial doubt " + "b " * 1000
    (sentence,) = sentences_matching(text, DOUBT)
    assert "substantial doubt" in sentence
    assert len(sentence) <= 400 * 2 + len("substantial doubt")


def test_sentences_matching_without_any_match() -> None:
    assert sentences_matching("Nothing to see here.", DOUBT) == []


def test_section_after_uses_the_first_specific_heading() -> None:
    heading = re.compile(r"Item 4\.01 Changes", re.I)
    text = (
        "Cover. Item 4.01 Changes in X: the details. Later text mentions Item 4.01 of this Report."
    )
    assert section_after(text, heading).startswith("Item 4.01 Changes in X: the details.")
    assert section_after("no heading at all", heading) == "no heading at all"
    assert len(section_after(text, heading, length=10)) == 10


# --- classification of single sentences -------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "There is substantial doubt about our ability to continue as a going concern.",
        "We have concluded that substantial doubt exists about the Company's ability to continue.",
        "Management believes there is substantial doubt about its ability to continue as a "
        "going concern.",
        "The Company has suffered recurring losses from operations that raise substantial "
        "doubt about its ability to continue as a going concern.",
        "These factors create substantial doubt about the Company's ability to continue.",
        "Substantial doubt exists about the ability to continue as a going concern.",
    ],
)
def test_sentences_that_state_doubt_are_exists(sentence: str) -> None:
    assert classify(sentence) == "exists"


@pytest.mark.parametrize(
    "sentence",
    [
        "Our losses and projected cash needs initially raised substantial doubt about the "
        "Company's ability to continue as a going concern.",
        "Management's plans are expected to alleviate the substantial doubt.",
        "The previously disclosed substantial doubt has been removed.",
    ],
)
def test_alleviated_doubt_is_recognised(sentence: str) -> None:
    assert classify(sentence) == "alleviated"


@pytest.mark.parametrize(
    "sentence",
    [
        # negations
        "There is no substantial doubt about our ability to continue as a going concern.",
        "These conditions do not raise substantial doubt about the Company's ability.",
        "This did not create substantial doubt.",
        "Substantial doubt no longer exists.",
        # conditionals and hypotheticals (typical risk-factor boilerplate)
        "If we fail to obtain financing, it could raise substantial doubt about our ability to "
        "continue.",
        "An adverse outcome may raise substantial doubt about the going concern assumption.",
        # the evaluation framework of ASC 205-40
        "Management must evaluate whether there are conditions and events that raise "
        "substantial doubt about the entity's ability to continue as a going concern.",
        "We evaluated whether conditions raise substantial doubt within one year.",
        # headings and cross references
        "Substantial Doubt Regarding the Company's Ability to Continue as a Going Concern",
        "Refer to Note 1 for disclosure of substantial doubt.",
    ],
)
def test_negations_conditionals_and_framing_are_not_flags(sentence: str) -> None:
    assert classify(sentence) == "other"


# --- real filings -----------------------------------------------------------------------

# accession -> expected status. The excerpts hold every "substantial doubt" sentence of the
# real 10-K, so the classifier is tested on the full mix of statements, plans and boilerplate.
REAL_CASES = {
    ("bbby", "2023-06-14"): "exists",  # Bed Bath & Beyond, fiscal 2022, Chapter 11
    ("wework", "2023-03-29"): "alleviated",  # "initially raised substantial doubt"
    ("spirit", "2025-03-03"): "exists",
    ("spirit", "2026-03-16"): "exists",
    ("tupperware", "2023-10-13"): "exists",
}


@pytest.mark.parametrize(("case", "expected"), sorted(REAL_CASES.items()))
def test_real_going_concern_filings(case: tuple[str, str], expected: str) -> None:
    accession = find_accession(case[0], "10-K", case[1])
    status, evidence = analyze_text(excerpt(accession))
    assert status == expected
    assert 1 <= len(evidence) <= 3
    full_text = excerpt(accession)
    assert all(sentence in full_text for sentence in evidence)  # verbatim, not rephrased
    assert any("going concern" in s.lower() for s in evidence)


def test_real_filings_that_state_no_doubt_are_not_flagged() -> None:
    negatives = [a for a, e in manifest().items() if e["kind"] == "going_concern_negative"]
    assert len(negatives) == 3  # Duos, Medinotec, Monopar
    for accession in negatives:
        text = excerpt(accession)
        assert "substantial doubt" in text.lower()  # they do mention the phrase ...
        assert analyze_text(text) == (None, [])  # ... but state no doubt


def test_tupperware_hypothetical_sentence_is_ignored_but_the_conclusion_counts() -> None:
    accession = find_accession("tupperware", "10-K", "2023-10-13")
    sentences = sentences_matching(excerpt(accession), DOUBT)
    kinds = {classify(s) for s in sentences}
    assert {"exists", "other"} <= kinds  # statements of doubt and boilerplate in one document
    assert analyze_text(excerpt(accession))[0] == "exists"  # a statement of doubt wins


# --- the check over a filing list -------------------------------------------------------


def _url(accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/1/{accession.replace('-', '')}/doc.htm"


def filing(form: str, filed: str, accession: str, period: str | None = None) -> Filing:
    return Filing(
        accession_number=accession,
        form=form,
        filing_date=date.fromisoformat(filed),
        report_date=date.fromisoformat(period) if period else None,
        primary_document="doc.htm",
        document_url=_url(accession),
    )


def source_for(f: Filing) -> Source:
    return SOURCE.model_copy(update={"form_type": f.form, "accession_number": f.accession_number})


GC_TEXT = (
    "<p>Based on this evaluation, management has concluded that there is substantial doubt "
    "about the Company's ability to continue as a going concern.</p>"
)
OK_TEXT = "<p>Management concluded there is no substantial doubt about the going concern.</p>"


def test_check_flags_only_annual_reports_in_the_window() -> None:
    filings = (
        filing("10-K", "2025-03-01", "0000000001-25-000001", "2024-12-31"),
        filing("10-K", "2024-03-01", "0000000001-24-000001", "2023-12-31"),
        filing("10-K", "2019-03-01", "0000000001-19-000001", "2018-12-31"),  # before the window
        filing("10-Q", "2025-05-01", "0000000001-25-000002", "2025-03-31"),  # not an annual report
    )
    texts = {
        "0000000001-25-000001": GC_TEXT,
        "0000000001-24-000001": OK_TEXT,
        "0000000001-19-000001": GC_TEXT,
        "0000000001-25-000002": GC_TEXT,
    }
    flags, coverage = check_going_concern(
        filings,
        lambda f: texts[f.accession_number],
        since=date(2020, 1, 1),
        source_for=source_for,
    )
    assert [f.accession_number for f in flags] == ["0000000001-25-000001"]
    flag = flags[0]
    assert (flag.kind, flag.code, flag.severity) == (
        FlagKind.GOING_CONCERN,
        "going_concern_exists",
        Severity.HIGH,
    )
    assert flag.period == date(2024, 12, 31)
    assert flag.document_url is not None
    assert flag.document_url.startswith("https://www.sec.gov/Archives/edgar/data/")
    assert flag.source.accession_number == "0000000001-25-000001"
    assert "going concern" in flag.evidence[0]
    assert (coverage.examined, coverage.flagged) == (2, 1)
    assert any("main document" in note for note in coverage.notes)


def test_unreadable_documents_are_reported_not_skipped_silently() -> None:
    filings = (
        filing("10-K", "2025-03-01", "0000000001-25-000001", "2024-12-31"),
        filing("10-K", "2024-03-01", "0000000001-24-000001", "2023-12-31"),
    )
    flags, coverage = check_going_concern(
        filings,
        lambda f: None if f.accession_number.endswith("25-000001") else OK_TEXT,
        since=date(2020, 1, 1),
        source_for=source_for,
    )
    assert flags == []
    assert coverage.examined == 1  # only the readable one
    assert any("filed 2025-03-01 could not be read" in note for note in coverage.notes)


def test_alleviated_doubt_is_a_medium_flag() -> None:
    text = (
        "<p>Our losses initially raised substantial doubt about our ability to continue as a "
        "going concern; management's plans alleviate that doubt.</p>"
    )
    flags, _ = check_going_concern(
        (filing("10-K", "2025-03-01", "0000000001-25-000001"),),
        lambda f: text,
        since=date(2020, 1, 1),
        source_for=source_for,
    )
    assert [(f.code, f.severity) for f in flags] == [("going_concern_alleviated", Severity.MEDIUM)]
