"""Auditor changes, restatements, late filings and amendments."""

from __future__ import annotations

from datetime import date

import pytest

from stock_forensics.flags.amendments import check_amendments
from stock_forensics.flags.amendments import classify as classify_amendment
from stock_forensics.flags.auditor import check_auditor_changes
from stock_forensics.flags.auditor import classify as classify_auditor
from stock_forensics.flags.late_filing import check_late_filings
from stock_forensics.flags.models import FlagKind, Severity
from stock_forensics.flags.restatement import check_restatements
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing
from tests.flag_fixtures import SOURCE, excerpt, find_accession, manifest


def source_for(f: Filing) -> Source:
    return SOURCE.model_copy(update={"form_type": f.form, "accession_number": f.accession_number})


def _url(accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/1/{accession.replace('-', '')}/doc.htm"


def filing(
    form: str,
    filed: str,
    accession: str,
    period: str | None = None,
    items: tuple[str, ...] = (),
) -> Filing:
    return Filing(
        accession_number=accession,
        form=form,
        filing_date=date.fromisoformat(filed),
        report_date=date.fromisoformat(period) if period else None,
        items=items,
        primary_document="doc.htm",
        document_url=_url(accession),
    )


# =========================================================================================
# Auditor changes (8-K Item 4.01)
# =========================================================================================

# (company, form, filing date) -> expected code and severity. The excerpts are the real
# Item 4.01 sections of these 8-Ks.
AUDITOR_CASES = {
    # Super Micro: EY sent a letter of resignation on October 24, 2024
    ("smci", "8-K", "2024-10-30"): ("auditor_resigned", Severity.HIGH),
    # Super Micro appointed BDO as the new auditor
    ("smci", "8-K", "2024-11-18"): ("auditor_engaged", Severity.LOW),
    # Super Micro replaced Deloitte (Deloitte "will not" continue after the board decision)
    ("smci", "8-K", "2023-03-15"): ("auditor_dismissed", Severity.LOW),
    # Tupperware: PwC is declining to stand for re-appointment, "no dispute"
    ("tupperware", "8-K", "2023-10-27"): ("auditor_declined_to_stand", Severity.MEDIUM),
    ("tupperware", "8-K", "2024-01-29"): ("auditor_declined_to_stand", Severity.MEDIUM),
    # WeWork: EY "will not seek to be retained"; the heading says "Change", not "Changes"
    ("wework", "8-K", "2023-11-13"): ("auditor_declined_to_stand", Severity.MEDIUM),
    # WeWork dismissed its auditor (an 8-K/A)
    ("wework", "8-K/A", "2021-10-26"): ("auditor_dismissed", Severity.LOW),
}


@pytest.mark.parametrize(("case", "expected"), sorted(AUDITOR_CASES.items()))
def test_real_auditor_changes_are_classified(
    case: tuple[str, str, str], expected: tuple[str, Severity]
) -> None:
    accession = find_accession(*case)
    code, severity, title, detail, evidence = classify_auditor(excerpt(accession))
    assert (code, severity) == expected
    assert title
    assert detail
    assert len(evidence) <= 2
    assert all(sentence in excerpt(accession) for sentence in evidence)


def test_a_resignation_outranks_everything_else_in_the_text() -> None:
    text = (
        "Item 4.01 Changes in Registrant's Certifying Accountant. On May 1, the auditor "
        "resigned. The Company dismissed no one and engaged Foo & Co., P.C. as its new "
        "independent registered public accounting firm."
    )
    code, severity, *_ = classify_auditor(text)
    assert (code, severity) == ("auditor_resigned", Severity.HIGH)


def test_company_names_with_periods_do_not_hide_the_engagement() -> None:
    text = (
        "Item 4.01 Changes in Registrant's Certifying Accountant. The Audit Committee "
        "appointed BDO USA, P.C. (BDO) as the Company's new independent registered public "
        "accounting firm."
    )
    assert classify_auditor(text)[0] == "auditor_engaged"


def test_a_change_without_a_stated_reason_is_a_medium_flag() -> None:
    text = "Item 4.01 Changes in Registrant's Certifying Accountant. Nothing is said about why."
    code, severity, _, detail, evidence = classify_auditor(text)
    assert (code, severity, evidence) == ("auditor_changed", Severity.MEDIUM, [])
    assert "does not say" in detail


def test_auditor_check_builds_flags_with_links_and_reports_unreadable_filings() -> None:
    filings = (
        filing("8-K", "2024-10-30", "0000000001-24-000001", "2024-10-24", ("4.01", "9.01")),
        filing("8-K", "2024-05-01", "0000000001-24-000002", None, ("4.01",)),
        filing("8-K", "2024-03-01", "0000000001-24-000003", None, ("2.02",)),  # no 4.01
        filing("8-K", "2018-03-01", "0000000001-18-000001", None, ("4.01",)),  # before the window
    )
    texts = {"0000000001-24-000001": excerpt(find_accession("smci", "8-K", "2024-10-30"))}
    flags, coverage = check_auditor_changes(
        filings,
        lambda f: texts.get(f.accession_number),
        since=date(2020, 1, 1),
        source_for=source_for,
    )
    assert [(f.accession_number, f.code) for f in flags] == [
        ("0000000001-24-000002", "auditor_changed"),  # unreadable: still flagged from the index
        ("0000000001-24-000001", "auditor_resigned"),
    ] or [(f.accession_number, f.code) for f in flags] == [
        ("0000000001-24-000001", "auditor_resigned"),
        ("0000000001-24-000002", "auditor_changed"),
    ]
    by_code = {f.code: f for f in flags}
    assert by_code["auditor_resigned"].document_url is not None
    assert by_code["auditor_resigned"].kind is FlagKind.AUDITOR_CHANGE
    assert by_code["auditor_changed"].evidence == ()
    assert (coverage.examined, coverage.flagged) == (2, 2)
    assert coverage.notes == ("8-K filed 2024-05-01 could not be read",)


# =========================================================================================
# Restatements (8-K Item 4.02)
# =========================================================================================


@pytest.mark.parametrize(
    ("company", "form", "filed"),
    [
        ("khc", "8-K", "2019-05-06"),  # Kraft Heinz
        ("tupperware", "8-K", "2023-03-16"),
        ("wework", "8-K", "2021-05-12"),
        ("wework", "8-K", "2021-12-01"),
    ],
)
def test_real_non_reliance_8ks_are_flagged_with_the_reason_quoted(
    company: str, form: str, filed: str
) -> None:
    accession = find_accession(company, form, filed)
    f = filing(form, filed, accession, None, ("4.02",))
    flags, coverage = check_restatements(
        (f,), lambda _: excerpt(accession), since=date(2015, 1, 1), source_for=source_for
    )
    (flag,) = flags
    assert (flag.kind, flag.code, flag.severity) == (
        FlagKind.RESTATEMENT,
        "non_reliance",
        Severity.HIGH,
    )
    assert 1 <= len(flag.evidence) <= 3
    assert any("reli" in s.lower() or "restat" in s.lower() for s in flag.evidence)
    assert coverage.examined == 1


def test_restatement_is_flagged_from_the_index_even_if_the_text_is_unreadable() -> None:
    f = filing("8-K", "2024-03-01", "0000000001-24-000001", None, ("4.02", "7.01"))
    flags, coverage = check_restatements(
        (f,), lambda _: None, since=date(2020, 1, 1), source_for=source_for
    )
    assert len(flags) == 1
    assert flags[0].evidence == ()
    assert coverage.notes == ("8-K filed 2024-03-01 could not be read",)


def test_other_items_and_old_filings_are_not_restatements() -> None:
    filings = (
        filing("8-K", "2024-03-01", "0000000001-24-000001", None, ("2.02",)),
        filing("8-K", "2018-03-01", "0000000001-18-000001", None, ("4.02",)),
        filing("10-K", "2024-03-01", "0000000001-24-000002", None, ()),
    )
    flags, coverage = check_restatements(
        filings, lambda _: "", since=date(2020, 1, 1), source_for=source_for
    )
    assert flags == []
    assert coverage.examined == 0


# =========================================================================================
# Late filings (NT 10-K, NT 10-Q)
# =========================================================================================


def test_late_annual_report_reports_the_delay_until_the_10k() -> None:
    filings = (
        filing("NT 10-K", "2019-02-28", "0000000001-19-000001", "2018-12-29"),
        filing("10-K", "2019-06-07", "0000000001-19-000002", "2018-12-29"),
        filing("10-K", "2018-03-01", "0000000001-18-000001", "2017-12-30"),  # another year
    )
    flags, coverage = check_late_filings(filings, since=date(2018, 1, 1), source_for=source_for)
    (flag,) = flags
    assert (flag.kind, flag.code, flag.severity) == (
        FlagKind.LATE_FILING,
        "late_10k",
        Severity.MEDIUM,
    )
    # 2019-02-28 -> 2019-06-07: 31 days left in the month + 31 + 30 + 31 + 7 = 99... checked below
    assert (date(2019, 6, 7) - date(2019, 2, 28)).days == 99
    assert (
        "The 10-K for the period ended 2018-12-29 followed 99 days after the notice, "
        "on 2019-06-07." in flag.detail
    )
    assert flag.period == date(2018, 12, 29)
    assert flag.accession_number == "0000000001-19-000001"
    assert (coverage.examined, coverage.flagged) == (1, 1)


def test_late_quarterly_report_is_low_and_a_missing_follow_up_is_stated() -> None:
    filings = (filing("NT 10-Q", "2024-11-13", "0000000001-24-000001", "2024-09-30"),)
    flags, _ = check_late_filings(filings, since=date(2020, 1, 1), source_for=source_for)
    (flag,) = flags
    assert (flag.code, flag.severity) == ("late_10q", Severity.LOW)
    assert "No matching 10-Q was filed afterwards" in flag.detail


def test_follow_up_must_match_the_period_and_come_after_the_notice() -> None:
    filings = (
        filing("NT 10-Q", "2024-05-10", "0000000001-24-000001", "2024-03-31"),
        filing("10-Q", "2024-08-09", "0000000001-24-000002", "2024-06-30"),  # the next quarter
        filing("10-Q", "2024-05-01", "0000000001-24-000003", "2024-03-31"),  # before the notice
    )
    (flag,), _ = check_late_filings(filings, since=date(2020, 1, 1), source_for=source_for)
    assert "No matching 10-Q was filed afterwards" in flag.detail


def test_old_notices_and_other_forms_are_ignored() -> None:
    filings = (
        filing("NT 10-K", "2017-02-28", "0000000001-17-000001", "2016-12-31"),
        filing("NT 11-K", "2024-06-28", "0000000001-24-000001", "2023-12-31"),
        filing("10-K", "2024-03-01", "0000000001-24-000002", "2023-12-31"),
    )
    flags, coverage = check_late_filings(filings, since=date(2020, 1, 1), source_for=source_for)
    assert flags == []
    assert coverage.examined == 0


def test_an_nt_form_whose_period_is_the_filing_date_uses_the_next_report() -> None:
    # Super Micro's NT 10-K of 2024-08-30 names the period 2024-08-30 in the SEC index; the
    # 10-K that followed (2025-02-25) is for the fiscal year ended 2024-06-30.
    filings = (
        filing("NT 10-K", "2024-08-30", "0000000001-24-000001", "2024-08-30"),
        filing("10-K", "2025-02-25", "0000000001-25-000001", "2024-06-30"),
        filing("10-K", "2025-08-29", "0000000001-25-000002", "2025-06-30"),  # the year after
    )
    (flag,) = check_late_filings(filings, since=date(2020, 1, 1), source_for=source_for)[0]
    assert flag.period == date(2024, 6, 30)  # taken from the report that followed
    assert "for the period ended 2024-06-30 on time" in flag.detail
    assert "followed 179 days after the notice, on 2025-02-25" in flag.detail
    assert (date(2025, 2, 25) - date(2024, 8, 30)).days == 179


def test_an_unreliable_period_without_any_follow_up_is_left_out_of_the_text() -> None:
    filings = (filing("NT 10-K", "2024-03-19", "0000000001-24-000001", None),)
    (flag,) = check_late_filings(filings, since=date(2020, 1, 1), source_for=source_for)[0]
    assert flag.period is None
    assert "for the period ended" not in flag.detail
    assert "No matching 10-K was filed afterwards" in flag.detail


# =========================================================================================
# Amendments of annual reports (10-K/A)
# =========================================================================================


def amendment_accession(company: str, filed: str) -> str:
    return find_accession(company, "10-K/A", filed)


# Real explanatory notes: restatements, a cover-page fix and routine Part III amendments
AMENDMENT_CASES = {
    ("wework", "2021-05-12"): "restatement",  # SPAC warrants, SEC staff statement
    ("wework", "2021-12-17"): "restatement",  # redeemable shares classified as temporary equity
    ("tupperware", "2021-08-05"): "restatement",  # newly identified misstatements
    ("bbby", "2021-04-27"): "other",  # "to correct an error on the cover page"
    ("spirit", "2025-04-30"): "part_iii",
    ("spirit", "2026-04-30"): "part_iii",
}


@pytest.mark.parametrize(("case", "expected"), sorted(AMENDMENT_CASES.items()))
def test_real_amendments_are_classified(case: tuple[str, str], expected: str) -> None:
    kind, evidence = classify_amendment(excerpt(amendment_accession(*case)))
    assert kind == expected
    if kind == "part_iii":
        assert evidence == []
    else:
        assert evidence
        assert all(s in excerpt(amendment_accession(*case)) for s in evidence)


@pytest.mark.parametrize(
    "text",
    [
        "EXPLANATORY NOTE This Amendment restates the consolidated financial statements.",
        "EXPLANATORY NOTE The Company identified an error in its previously issued financial "
        "statements.",
        "EXPLANATORY NOTE The financial statements contained a material misstatement.",
        "EXPLANATORY NOTE The previously issued audit report should no longer be relied upon.",
    ],
)
def test_restatement_wording_is_recognised(text: str) -> None:
    assert classify_amendment(text)[0] == "restatement"


@pytest.mark.parametrize(
    "text",
    [
        # a cover-page fix is not a restatement
        "EXPLANATORY NOTE This Amendment corrects an error on the cover page.",
        # documents named "restated" are not a restatement of the financial statements
        "EXPLANATORY NOTE This Amendment adds the Amended and Restated Certificate of "
        "Incorporation as Exhibit 3.1.",
        "EXPLANATORY NOTE This Amendment files the Restated Certificate of Incorporation.",
    ],
)
def test_wording_that_only_looks_like_a_restatement_is_not_one(text: str) -> None:
    assert classify_amendment(text)[0] == "other"


def test_part_iii_amendment_is_routine_unless_it_restates() -> None:
    assert classify_amendment("EXPLANATORY NOTE This Amendment adds Part III information.")[0] == (
        "part_iii"
    )
    assert (
        classify_amendment(
            "EXPLANATORY NOTE This Amendment adds Part III and restates the financial statements."
        )[0]
        == "restatement"
    )


def test_amendment_check_flags_restatements_and_counts_frequent_amendments() -> None:
    filings = (
        filing("10-K/A", "2021-12-17", "0000000001-21-000003", "2020-12-31"),
        filing("10-K/A", "2021-05-12", "0000000001-21-000002", "2020-12-31"),
        filing("10-K/A", "2021-04-30", "0000000001-21-000001", "2020-12-31"),
        filing("10-K", "2021-03-01", "0000000001-21-000000", "2020-12-31"),
    )
    texts = {
        "0000000001-21-000003": excerpt(amendment_accession("wework", "2021-12-17")),
        "0000000001-21-000002": excerpt(amendment_accession("wework", "2021-05-12")),
        "0000000001-21-000001": excerpt(amendment_accession("spirit", "2025-04-30")),
    }
    flags, coverage = check_amendments(
        filings,
        lambda f: texts[f.accession_number],
        since=date(2020, 1, 1),
        source_for=source_for,
        aggregate_source=SOURCE,
    )
    codes = [(f.filing_date.isoformat(), f.code, f.severity) for f in flags]
    assert ("2021-05-12", "amendment_restatement", Severity.HIGH) in codes
    assert ("2021-12-17", "amendment_restatement", Severity.HIGH) in codes
    assert not any(f.accession_number == "0000000001-21-000001" for f in flags)  # routine
    frequent = next(f for f in flags if f.code == "frequent_amendments")
    assert frequent.severity is Severity.MEDIUM
    assert frequent.accession_number is None
    assert frequent.source.provider == "sec_edgar"  # the fixture source stands in here
    assert frequent.evidence == ("10-K/A filed 2021-05-12", "10-K/A filed 2021-12-17")
    assert (coverage.examined, coverage.flagged) == (3, 3)
    assert "1 routine amendment(s) adding Part III information were not flagged" in coverage.notes


def test_one_substantive_amendment_is_not_frequent() -> None:
    filings = (filing("10-K/A", "2021-05-12", "0000000001-21-000002", "2020-12-31"),)
    flags, _ = check_amendments(
        filings,
        lambda _: excerpt(amendment_accession("wework", "2021-05-12")),
        since=date(2020, 1, 1),
        source_for=source_for,
        aggregate_source=SOURCE,
    )
    assert [f.code for f in flags] == ["amendment_restatement"]


def test_unreadable_amendment_is_low_and_does_not_count_as_substantive() -> None:
    filings = (
        filing("10-K/A", "2021-05-12", "0000000001-21-000002", "2020-12-31"),
        filing("10-K/A", "2021-12-17", "0000000001-21-000003", "2020-12-31"),
    )
    flags, coverage = check_amendments(
        filings,
        lambda _: None,
        since=date(2020, 1, 1),
        source_for=source_for,
        aggregate_source=SOURCE,
    )
    assert {f.code for f in flags} == {"amendment_unclassified"}
    assert all(f.severity is Severity.LOW for f in flags)
    assert len(coverage.notes) == 2


def test_the_fixture_manifest_has_every_kind_of_excerpt() -> None:
    kinds = {entry["kind"] for entry in manifest().values()}
    assert kinds == {
        "going_concern",
        "going_concern_negative",
        "auditor_change",
        "restatement",
        "amendment",
    }
