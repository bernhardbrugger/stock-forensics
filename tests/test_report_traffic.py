"""Traffic lights and the short summary: how findings become lights and plain sentences.

The rules (see ``docs/report.md``) are tested one by one, including their edges: a lone red
screen is only "worth a look", two red screens are red, a hard filing flag is red on its own,
and a revenue move of exactly 25 % counts as a growth swing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, RedFlags, Severity
from stock_forensics.metrics.engine import Metrics
from stock_forensics.models import Source
from stock_forensics.report import ForensicReport
from stock_forensics.report.models import Light
from stock_forensics.report.summary import headline, summarize, takeaways
from stock_forensics.report.traffic import (
    BENEISH_DRIVERS,
    SCORE_LIGHTS,
    beneish_driver,
    flags_area,
    growth_swing,
    insiders_area,
    score_light,
    scores_area,
    worst,
)
from stock_forensics.scores import SCORE_NAMES, compute_scores
from stock_forensics.scores.base import Scores

AS_OF = date(2026, 11, 12)
SOURCE = Source(provider="stock_forensics", retrieved_at=datetime(2026, 11, 12, tzinfo=UTC))
GREEN = {
    "beneish_m": "unlikely_manipulator",
    "altman_z": "safe",
    "piotroski_f": "strong",
    "accruals_ratio": "normal",
}
RED = {
    "beneish_m": "likely_manipulator",
    "altman_z": "distress",
    "piotroski_f": "weak",
    "accruals_ratio": "high",
}


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


def red_flags(*flags: RedFlag, notes: tuple[str, ...] = ()) -> RedFlags:
    return RedFlags(
        cik=1,
        company_name="Test Inc.",
        as_of=AS_OF,
        since=date(2021, 11, 12),
        flags=flags,
        coverage=(Coverage(kind=FlagKind.GOING_CONCERN, examined=5, flagged=0, notes=notes),),
        source=SOURCE,
    )


def with_zones(scores: Scores, **zones: str) -> Scores:
    """The scores with the zone of chosen scores replaced (latest fiscal year)."""
    year = scores.fiscal_years[-1]
    results = {name: dict(by_year) for name, by_year in scores.results.items()}
    for name, zone in zones.items():
        results[name][year] = results[name][year].model_copy(update={"zone": zone})
    return scores.model_copy(update={"results": results})


def all_green(scores: Scores) -> Scores:
    return with_zones(scores, **GREEN)


def with_growth(metrics: Metrics, year: int, growth: str) -> Metrics:
    """The metrics with the revenue growth of one fiscal year replaced."""
    series = metrics.series["revenue_growth_yoy"]
    value = series.annual[year].model_copy(update={"value": Decimal(growth)})
    patched = series.model_copy(update={"annual": {**series.annual, year: value}})
    return metrics.model_copy(update={"series": {**metrics.series, "revenue_growth_yoy": patched}})


@pytest.fixture(scope="module")
def scores(aapl_report: ForensicReport) -> Scores:
    assert aapl_report.scores is not None
    return aapl_report.scores


@pytest.fixture(scope="module")
def metrics(aapl_report: ForensicReport) -> Metrics:
    assert aapl_report.metrics is not None
    return aapl_report.metrics


# --- single lights -----------------------------------------------------------------------------


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
def test_score_zone_to_light(scores: Scores, name: str, zone: str, expected: Light) -> None:
    result = scores.latest(name).model_copy(update={"zone": zone})
    assert score_light(result) is expected


def test_score_zones_cover_every_score() -> None:
    assert set(SCORE_LIGHTS) == set(SCORE_NAMES)


def test_unknown_zone_and_missing_total_are_gray(scores: Scores) -> None:
    result = scores.latest("altman_z")
    assert score_light(result.model_copy(update={"zone": "something new"})) is Light.GRAY
    assert score_light(result.model_copy(update={"zone": None})) is Light.GRAY
    assert score_light(result.model_copy(update={"applicable": False})) is Light.GRAY


# --- the scores area: screens, not verdicts -------------------------------------------------


def test_apple_has_one_grey_zone_screen(scores: Scores, metrics: Metrics) -> None:
    area, reasons = scores_area(scores, metrics, None)
    assert area.light is Light.YELLOW
    assert area.summary == "3 fine, 1 worth a look"
    assert [r.light for r in reasons] == [Light.YELLOW]
    assert reasons[0].text.startswith("Altman Z-Score")


def test_all_green_screens_are_green(scores: Scores, metrics: Metrics) -> None:
    area, reasons = scores_area(all_green(scores), metrics, None)
    assert (area.light, area.summary, reasons) == (Light.GREEN, "4 fine", [])


def test_a_lone_red_screen_is_only_worth_a_look(scores: Scores, metrics: Metrics) -> None:
    lone = with_zones(all_green(scores), beneish_m=RED["beneish_m"])
    area, reasons = scores_area(lone, metrics, None)
    assert area.light is Light.YELLOW
    assert "a lone red screen only counts as worth a look" in area.summary
    assert [r.light for r in reasons] == [Light.YELLOW]
    hint = reasons[0].hint or ""
    assert "one screen alone is not a verdict" in hint
    assert "Altman" in hint and "Piotroski" in hint  # names the screens that disagree


def test_two_red_screens_are_red(scores: Scores, metrics: Metrics) -> None:
    pair = with_zones(all_green(scores), beneish_m=RED["beneish_m"], piotroski_f=RED["piotroski_f"])
    area, reasons = scores_area(pair, metrics, None)
    assert area.light is Light.RED
    assert [r.light for r in reasons] == [Light.RED, Light.RED]
    assert all("alone" not in (r.hint or "") for r in reasons)
    assert area.summary == "2 fine, 2 red"


def test_a_lone_red_next_to_a_yellow_screen_stays_yellow(scores: Scores, metrics: Metrics) -> None:
    mixed = with_zones(all_green(scores), altman_z="grey", accruals_ratio=RED["accruals_ratio"])
    area, reasons = scores_area(mixed, metrics, None)
    assert area.light is Light.YELLOW
    assert sorted(r.light.value for r in reasons) == ["yellow", "yellow"]


def test_scores_for_a_bank_are_gray_with_the_reason(
    scores: Scores, aapl_report: ForensicReport
) -> None:
    assert aapl_report.statements is not None
    bank = compute_scores(
        aapl_report.statements, sic_code="6021", industry="National Commercial Banks"
    )
    area, reasons = scores_area(bank, None, None)
    assert area.light is Light.GRAY
    assert area.summary.startswith("not applicable")
    assert reasons[0].light is Light.GRAY and "do not apply" in reasons[0].text


def test_missing_sections_are_gray_and_say_why() -> None:
    area, reasons = scores_area(None, None, "boom")
    assert (area.light, area.summary, reasons) == (Light.GRAY, "not available: boom", [])
    area, _ = flags_area(None, AS_OF, "bang")
    assert (area.light, area.summary) == (Light.GRAY, "not available: bang")
    area, _ = insiders_area(None, None)
    assert (area.light, area.summary) == (Light.GRAY, "not available")


# --- why a screen is red: driver and growth swings ------------------------------------------


def test_the_beneish_driver_is_named_in_plain_words(scores: Scores, metrics: Metrics) -> None:
    result = scores.latest("beneish_m")
    driver = beneish_driver(result)
    assert driver is not None
    name, contribution = driver
    assert contribution == max(c.contribution or Decimal(0) for c in result.components)
    red = with_zones(all_green(scores), beneish_m=RED["beneish_m"])
    _, reasons = scores_area(red, metrics, None)
    assert "Biggest driver" in reasons[0].text
    assert BENEISH_DRIVERS[name] in reasons[0].text
    assert "the alarm starts above -1.78" in reasons[0].text


@pytest.mark.parametrize(
    ("growth", "expected"),
    [("0.2499", False), ("0.25", True), ("-0.25", True), ("0.60", True), ("-0.2499", False)],
)
def test_growth_swing_edge(metrics: Metrics, growth: str, expected: bool) -> None:
    patched = with_growth(metrics, metrics.fiscal_years[-1], growth)
    swing = growth_swing(patched)
    assert (swing is not None) is expected
    if expected:
        assert swing == (metrics.fiscal_years[-1], Decimal(growth))


def test_growth_swing_only_looks_at_the_last_three_years(metrics: Metrics) -> None:
    assert growth_swing(metrics) is None  # Apple grew 2 to 15 % in these years
    old = with_growth(metrics, metrics.fiscal_years[-4], "0.9")
    assert growth_swing(old) is None
    recent = with_growth(metrics, metrics.fiscal_years[-3], "0.9")
    assert growth_swing(recent) is not None
    assert growth_swing(None) is None


def test_the_biggest_swing_wins(metrics: Metrics) -> None:
    first = with_growth(metrics, metrics.fiscal_years[-3], "-0.50")
    both = with_growth(first, metrics.fiscal_years[-1], "2.56")
    assert growth_swing(both) == (metrics.fiscal_years[-1], Decimal("2.56"))


def test_a_growth_swing_warns_about_beneish_and_accruals_only(
    scores: Scores, metrics: Metrics
) -> None:
    swing = with_growth(metrics, metrics.fiscal_years[-1], "2.56")
    flagged = with_zones(
        all_green(scores),
        beneish_m=RED["beneish_m"],
        altman_z="grey",
        accruals_ratio="elevated",
    )
    _, reasons = scores_area(flagged, swing, None)
    by_start = {r.text.split(" ")[0]: r for r in reasons}
    assert "+256%" in (by_start["Beneish"].hint or "")
    assert "error-prone" in (by_start["Beneish"].hint or "")
    assert "+256%" in (by_start["Accruals"].hint or "")
    assert by_start["Altman"].hint is None  # distress models do not depend on growth that way
    # no swing, no such hint
    _, calm = scores_area(flagged, metrics, None)
    assert all("error-prone" not in (r.hint or "") for r in calm)


# --- red flags --------------------------------------------------------------------------------


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


# --- overall light and the summary ------------------------------------------------------------


def overall(
    scores: Scores | None,
    metrics: Metrics | None,
    flags: RedFlags | None,
    errors: dict[str, str] | None = None,
) -> Light:
    return summarize(
        scores=scores,
        metrics=metrics,
        red_flags=flags,
        insiders=None,
        as_of=AS_OF,
        errors=errors or {},
    ).overall


def test_overall_is_the_worst_of_scores_and_flags(scores: Scores, metrics: Metrics) -> None:
    lone = with_zones(all_green(scores), beneish_m=RED["beneish_m"])
    pair = with_zones(lone, piotroski_f=RED["piotroski_f"])
    hard = red_flags(flag(Severity.HIGH, date(2026, 1, 1)))
    assert overall(lone, metrics, red_flags()) is Light.YELLOW  # the Micron case: no longer red
    assert overall(pair, metrics, red_flags()) is Light.RED
    assert (
        overall(all_green(scores), metrics, hard) is Light.RED
    )  # an event needs no second opinion
    assert overall(scores, metrics, None, {"red_flags": "x"}) is Light.YELLOW


def test_reasons_come_red_first(scores: Scores, metrics: Metrics) -> None:
    mixed = with_zones(all_green(scores), altman_z="grey")
    summary = summarize(
        scores=mixed,
        metrics=metrics,
        red_flags=red_flags(flag(Severity.HIGH, date(2026, 1, 1))),
        insiders=None,
        as_of=AS_OF,
        errors={},
    )
    assert [r.light for r in summary.reasons] == [Light.RED, Light.YELLOW]


def test_headlines(scores: Scores, metrics: Metrics, aapl_report: ForensicReport) -> None:
    assert aapl_report.summary.headline == "Mostly fine, but 1 thing worth a look."

    def line(sc: Scores | None, flags: RedFlags | None) -> str:
        return summarize(
            scores=sc, metrics=metrics, red_flags=flags, insiders=None, as_of=AS_OF, errors={}
        ).headline

    assert line(None, None) == "Could not be assessed from the data that could be checked."
    assert line(None, red_flags()) == (
        "Nothing alarming in the checked data. The forensic scores are not available."
    )
    assert line(all_green(scores), red_flags()) == "Nothing alarming in the checked data."
    both = red_flags(flag(Severity.HIGH, date(2026, 1, 1)), flag(Severity.MEDIUM, date(2026, 2, 1)))
    assert line(all_green(scores), both) == "Alarm: 1 serious finding and 1 more worth a look."
    assert aapl_report.statements is not None
    bank = compute_scores(aapl_report.statements, sic_code="6021")
    assert "do not apply" in line(bank, red_flags())
    assert headline(Light.RED, [], None, Light.RED).startswith("Alarm: 0 serious findings")


def test_takeaways_of_a_clean_company(aapl_report: ForensicReport, scores: Scores) -> None:
    assert aapl_report.summary.takeaways == (
        "Of the four forensic screens, 3 look fine. Standing out: Altman Z' in the grey zone.",
        "No auditor change, restatement, late filing or going-concern doubt in the last 5 years "
        "of filings. Some documents could not be read, so this may be incomplete.",
        "Insiders: 47 sales (44 under pre-planned 10b5-1 programs) and 0 purchases in the last "
        "12 months.",
    )


def test_takeaways_for_the_micron_case(scores: Scores, metrics: Metrics) -> None:
    swing = with_growth(metrics, metrics.fiscal_years[-1], "2.56")
    lone = with_zones(all_green(scores), beneish_m=RED["beneish_m"])
    sentences = takeaways(
        scores=lone, metrics=swing, red_flags=red_flags(), insiders=None, as_of=AS_OF, errors={}
    )
    first = sentences[0]
    assert first.startswith(
        "Of the four forensic screens, 3 look fine. Standing out: Beneish M-Score (mostly "
    )
    assert "Only one is red, and one screen alone is no verdict." in first
    assert "Revenue moved +256% in FY" in first and "unreliable" in first
    assert sentences[1] == (
        "No auditor change, restatement, late filing or going-concern doubt in the last 5 years "
        "of filings."
    )


def test_takeaways_say_when_screens_could_not_be_computed(scores: Scores, metrics: Metrics) -> None:
    # Like Super Micro: two screens have no result (zone None), two stand out.
    patched = scores
    year = scores.fiscal_years[-1]
    results = {n: dict(by_year) for n, by_year in scores.results.items()}
    for name in ("beneish_m", "piotroski_f"):
        results[name][year] = results[name][year].model_copy(update={"zone": None})
    patched = scores.model_copy(update={"results": results})
    patched = with_zones(patched, altman_z="grey", accruals_ratio=RED["accruals_ratio"])
    first = takeaways(
        scores=patched, metrics=metrics, red_flags=None, insiders=None, as_of=AS_OF, errors={}
    )[0]
    assert first.startswith(
        "Of the four forensic screens, 0 look fine, 2 could not be computed. Standing out: "
    )


def test_takeaways_when_screens_agree(scores: Scores, metrics: Metrics) -> None:
    pair = with_zones(all_green(scores), altman_z="distress", piotroski_f="weak")
    first = takeaways(
        scores=pair, metrics=metrics, red_flags=None, insiders=None, as_of=AS_OF, errors={}
    )[0]
    assert "Several screens agree, so this is worth taking seriously." in first
    assert "Only one is red" not in first


def test_takeaways_for_flags_and_gaps(scores: Scores, metrics: Metrics) -> None:
    recent = red_flags(flag(Severity.HIGH, date(2025, 3, 3)), flag(Severity.LOW, date(2026, 1, 1)))
    text = takeaways(
        scores=None,
        metrics=None,
        red_flags=recent,
        insiders=None,
        as_of=AS_OF,
        errors={"scores": "boom", "market": "x"},
    )
    assert text[0] == (
        "Filings: 1 flag of medium or high severity in the last 3 years, for example: "
        "A high flag (2025-03-03)."
    )
    assert text[-1] == "Could not be checked: market, scores (see below for why)."
    old = takeaways(
        scores=None,
        metrics=None,
        red_flags=red_flags(flag(Severity.HIGH, date(2020, 5, 5))),
        insiders=None,
        as_of=AS_OF,
        errors={},
    )
    assert old[0] == "Filings: no recent flags, but an older serious one: A high flag (2020-05-05)."
    minor = takeaways(
        scores=None,
        metrics=None,
        red_flags=red_flags(flag(Severity.LOW, date(2026, 1, 1))),
        insiders=None,
        as_of=AS_OF,
        errors={},
    )
    assert minor[0] == "Filings: only minor flags (1), nothing serious."


def test_takeaways_for_a_bank_and_for_quiet_insiders(aapl_report: ForensicReport) -> None:
    assert aapl_report.statements is not None
    bank = compute_scores(aapl_report.statements, sic_code="6021")
    text = takeaways(
        scores=bank, metrics=None, red_flags=None, insiders=None, as_of=AS_OF, errors={}
    )
    assert text[0].startswith("The four forensic scores don't apply to this kind of company (")


def test_data_area_counts_gaps_and_missing_sections(aapl_report: ForensicReport) -> None:
    data = aapl_report.summary.areas[-1]
    assert data.area == "data"
    assert data.light is Light.INFO
    assert data.summary == "1 of 25 metrics not available for FY2026"
