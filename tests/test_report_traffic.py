"""Traffic lights: how scores, red flags and insiders turn into green, yellow and red.

The rules (see ``docs/report.md``) are tested one by one, including the boundary of the
three-year window for red flags.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from stock_forensics.flags.models import FlagKind, RedFlag, RedFlags, Severity
from stock_forensics.models import Source
from stock_forensics.report import ForensicReport
from stock_forensics.report.models import Light
from stock_forensics.report.traffic import (
    SCORE_LIGHTS,
    flags_area,
    insiders_area,
    score_light,
    scores_area,
    summarize,
    worst,
)
from stock_forensics.scores import SCORE_NAMES, compute_scores

AS_OF = date(2026, 11, 12)
SOURCE = Source(provider="stock_forensics", retrieved_at=datetime(2026, 11, 12, tzinfo=UTC))


def flag(severity: Severity, filing_date: date) -> RedFlag:
    return RedFlag(
        kind=FlagKind.AUDITOR_CHANGE,
        code="test",
        severity=severity,
        title=f"A {severity.value} flag",
        detail="detail",
        filing_date=filing_date,
        form="8-K",
        source=SOURCE,
    )


def red_flags(*flags: RedFlag) -> RedFlags:
    return RedFlags(
        cik=1,
        company_name="Test Inc.",
        as_of=AS_OF,
        since=date(2021, 11, 12),
        flags=flags,
        coverage=(),
        source=SOURCE,
    )


def test_worst_light() -> None:
    assert worst([Light.GREEN, Light.YELLOW]) is Light.YELLOW
    assert worst([Light.GREEN, Light.RED, Light.YELLOW]) is Light.RED
    assert worst([Light.GRAY, Light.GREEN]) is Light.GREEN  # gray means "no data", not "worse"
    assert worst([Light.INFO, Light.GREEN]) is Light.GREEN
    assert worst([Light.GRAY]) is Light.GRAY
    assert worst([]) is Light.GRAY


@pytest.mark.parametrize(
    ("name", "zone", "expected"),
    [(n, z, light) for n, zones in SCORE_LIGHTS.items() for z, light in zones.items()],
)
def test_score_zone_to_light(
    aapl_report: ForensicReport, name: str, zone: str, expected: Light
) -> None:
    assert aapl_report.scores is not None
    result = aapl_report.scores.latest(name).model_copy(update={"zone": zone})
    assert score_light(result) is expected


def test_score_zones_cover_every_score() -> None:
    assert set(SCORE_LIGHTS) == set(SCORE_NAMES)


def test_unknown_zone_and_missing_total_are_gray(aapl_report: ForensicReport) -> None:
    assert aapl_report.scores is not None
    result = aapl_report.scores.latest("altman_z")
    assert score_light(result.model_copy(update={"zone": "something new"})) is Light.GRAY
    assert score_light(result.model_copy(update={"zone": None})) is Light.GRAY
    assert score_light(result.model_copy(update={"applicable": False})) is Light.GRAY


def test_scores_area_uses_the_worst_applicable_score(aapl_report: ForensicReport) -> None:
    area, reasons = scores_area(aapl_report.scores, None)
    # Apple: Altman Z' is in the grey zone, the other three are clear.
    assert area.light is Light.YELLOW
    assert area.summary == "3 clear, 1 caution"
    assert [r.light for r in reasons] == [Light.YELLOW]
    assert reasons[0].text.startswith("Altman Z-Score")


def test_scores_area_for_a_bank_is_gray_with_the_reason(aapl_report: ForensicReport) -> None:
    assert aapl_report.statements is not None
    bank = compute_scores(
        aapl_report.statements, sic_code="6021", industry="National Commercial Banks"
    )
    area, reasons = scores_area(bank, None)
    assert area.light is Light.GRAY
    assert area.summary.startswith("not applicable")
    assert reasons[0].light is Light.GRAY
    assert "do not apply" in reasons[0].text


def test_missing_sections_are_gray_and_say_why() -> None:
    area, reasons = scores_area(None, "boom")
    assert (area.light, area.summary, reasons) == (Light.GRAY, "not available: boom", [])
    area, _ = flags_area(None, AS_OF, "bang")
    assert (area.light, area.summary) == (Light.GRAY, "not available: bang")
    area, _ = insiders_area(None, None)
    assert (area.light, area.summary) == (Light.GRAY, "not available")


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((), Light.GREEN),
        ((flag(Severity.HIGH, date(2025, 1, 1)),), Light.RED),
        ((flag(Severity.HIGH, date(2023, 11, 12)),), Light.RED),  # exactly three years: recent
        ((flag(Severity.HIGH, date(2023, 11, 11)),), Light.YELLOW),  # one day older
        ((flag(Severity.HIGH, date(2020, 1, 1)),), Light.YELLOW),
        ((flag(Severity.MEDIUM, date(2025, 6, 1)),), Light.YELLOW),
        ((flag(Severity.MEDIUM, date(2020, 6, 1)),), Light.GREEN),
        ((flag(Severity.LOW, date(2026, 1, 1)),), Light.GREEN),
        (
            (flag(Severity.MEDIUM, date(2025, 6, 1)), flag(Severity.HIGH, date(2024, 6, 1))),
            Light.RED,
        ),
    ],
)
def test_red_flag_light(flags: tuple[RedFlag, ...], expected: Light) -> None:
    area, reasons = flags_area(red_flags(*flags), AS_OF, None)
    assert area.light is expected
    shown = [f for f in flags if f.severity is not Severity.LOW and expected is not Light.GREEN]
    assert len(reasons) <= len(shown)


def test_red_flag_reasons_name_the_filing_date_and_are_limited() -> None:
    many = tuple(flag(Severity.HIGH, date(2026, 1, d)) for d in range(1, 10))
    area, reasons = flags_area(red_flags(*many), AS_OF, None)
    assert area.light is Light.RED
    assert len(reasons) == 6
    assert reasons[0].text == "A high flag (2026-01-01)"
    assert all(r.light is Light.RED and r.section == "red-flags" for r in reasons)


def test_insiders_are_information_only(aapl_report: ForensicReport) -> None:
    area, reasons = insiders_area(aapl_report.insiders, None)
    assert area.light is Light.INFO
    assert "47 sales (44 under 10b5-1 plans)" in area.summary
    assert all(r.light is Light.INFO for r in reasons)


def test_overall_is_the_worst_of_scores_and_flags(aapl_report: ForensicReport) -> None:
    def overall(flags: RedFlags | None, errors: dict[str, str] | None = None) -> Light:
        return summarize(
            scores=aapl_report.scores,
            metrics=aapl_report.metrics,
            red_flags=flags,
            insiders=aapl_report.insiders,
            as_of=AS_OF,
            errors=errors or {},
        ).overall

    assert overall(red_flags()) is Light.YELLOW  # the grey-zone Altman score
    assert overall(red_flags(flag(Severity.HIGH, date(2026, 1, 1)))) is Light.RED
    assert overall(None, {"red_flags": "x"}) is Light.YELLOW  # gray flags do not hide the score


def test_summary_headlines(aapl_report: ForensicReport) -> None:
    assert aapl_report.summary.headline == "1 caution to look at, no warnings."

    def headline(scores: object, flags: RedFlags | None) -> str:
        return summarize(
            scores=scores,  # type: ignore[arg-type]
            metrics=None,
            red_flags=flags,
            insiders=None,
            as_of=AS_OF,
            errors={},
        ).headline

    assert headline(None, None) == "Not assessable from the data that could be checked."
    assert headline(None, red_flags()) == (
        "No warning signs found in the checked data. The forensic scores are not available."
    )
    flagged = red_flags(
        flag(Severity.HIGH, date(2026, 1, 1)), flag(Severity.MEDIUM, date(2026, 2, 1))
    )
    assert headline(None, flagged) == "1 warning and 1 caution found."
    assert aapl_report.statements is not None
    bank = compute_scores(aapl_report.statements, sic_code="6021")
    assert "do not apply" in headline(bank, red_flags())


def test_data_area_counts_gaps_and_missing_sections(aapl_report: ForensicReport) -> None:
    data = aapl_report.summary.areas[-1]
    assert data.area == "data"
    assert data.light is Light.INFO
    assert data.summary == "1 of 25 metrics not available for FY2026"
