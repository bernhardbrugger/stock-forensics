"""Traffic lights of the report: how the findings turn into green, yellow and red.

All thresholds are here, in one place, and are explained in ``docs/report.md``:

* **Scores** (latest fiscal year) are *screens*, not verdicts. Beneish "likely manipulator",
  Altman distress, Piotroski weak and accruals high each count as one red screen. The area is
  **red only if two or more screens are red**; a lone red screen makes it yellow ("worth a
  look"), because every one of these models has a known false-alarm rate. Grey zone and
  "elevated" are yellow. The area is gray if no score applies (banks, insurers, REITs).
* **Growth swings**: if revenue moved by 25 % or more in one of the last three fiscal years,
  a non-green Beneish or accruals result gets a hint that both are error-prone for fast-growing,
  shrinking and cyclical companies.
* **Red flags**: red if there is a high flag in the last 3 years; yellow for a medium flag in the
  last 3 years or an older high flag; green otherwise. Low flags are shown, but change nothing.
  These are events in filings, not statistics, so one is enough.
* **Insiders**: information only, never a warning. Sales are mostly diversification, and many
  are planned under Rule 10b5-1. Cluster buys are mentioned as context.
* **Metrics**: no light. A cut-off such as "current ratio below 1" would be an invention.
* **Overall**: the worst light of scores and red flags (gray does not count).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from stock_forensics.flags import years_before
from stock_forensics.flags.models import RedFlags, Severity
from stock_forensics.insiders.models import InsiderActivity
from stock_forensics.metrics.engine import Metrics
from stock_forensics.report.models import AreaLight, Light, Reason
from stock_forensics.scores import SCORE_NAMES
from stock_forensics.scores.base import ScoreResult, Scores

RECENT_YEARS = 3
MAX_FLAG_REASONS = 6
RED_SCREENS_FOR_RED = 2  # a lone red screen is only "worth a look"
GROWTH_SWING = Decimal("0.25")  # |revenue growth| from which Beneish and accruals are shaky
GROWTH_LOOKBACK_YEARS = 3
GROWTH_SENSITIVE = ("beneish_m", "accruals_ratio")

SCORE_LIGHTS: dict[str, dict[str, Light]] = {
    "beneish_m": {"likely_manipulator": Light.RED, "unlikely_manipulator": Light.GREEN},
    "altman_z": {"distress": Light.RED, "grey": Light.YELLOW, "safe": Light.GREEN},
    "piotroski_f": {"weak": Light.RED, "neutral": Light.GREEN, "strong": Light.GREEN},
    "accruals_ratio": {"high": Light.RED, "elevated": Light.YELLOW, "normal": Light.GREEN},
}
_ORDER = {Light.GREEN: 0, Light.YELLOW: 1, Light.RED: 2}

# What each Beneish index measures, in plain words (used to name the biggest driver).
BENEISH_DRIVERS = {
    "DSRI": "receivables growing faster than sales",
    "GMI": "a shrinking gross margin",
    "AQI": "more assets that are neither cash nor property and equipment",
    "SGI": "strong sales growth",
    "DEPI": "slower depreciation",
    "SGAI": "overhead growing faster than sales",
    "TATA": "profits that are not backed by cash",
    "LVGI": "rising leverage",
}


def worst(lights: list[Light]) -> Light:
    """The worst of the green, yellow and red lights; gray if there is none of them."""
    graded = [light for light in lights if light in _ORDER]
    return max(graded, key=_ORDER.__getitem__) if graded else Light.GRAY


def score_light(result: ScoreResult) -> Light:
    if not result.applicable or result.zone is None:
        return Light.GRAY
    return SCORE_LIGHTS.get(result.name, {}).get(result.zone, Light.GRAY)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def growth_swing(metrics: Metrics | None) -> tuple[int, Decimal] | None:
    """The fiscal year with the biggest revenue move of 25 % or more in the last three years."""
    if metrics is None or "revenue_growth_yoy" not in metrics.series:
        return None
    series = metrics.series["revenue_growth_yoy"]
    best: tuple[int, Decimal] | None = None
    for year in metrics.fiscal_years[-GROWTH_LOOKBACK_YEARS:]:
        growth = series.annual[year].value
        if growth is None or abs(growth) < GROWTH_SWING:
            continue
        if best is None or abs(growth) > abs(best[1]):
            best = (year, growth)
    return best


def beneish_driver(result: ScoreResult) -> tuple[str, Decimal] | None:
    """The Beneish index that pushes the score up the most, with its contribution."""
    pushing = [c for c in result.components if c.contribution is not None and c.contribution > 0]
    if not pushing:
        return None
    top = max(pushing, key=lambda c: c.contribution or Decimal(0))
    assert top.contribution is not None
    return top.name, top.contribution


def score_short(result: ScoreResult) -> str:
    """A few words for a score that stands out, for the summary sentences."""
    total = result.total.value
    if result.name == "beneish_m":
        driver = beneish_driver(result)
        why = f" (mostly {BENEISH_DRIVERS.get(driver[0], driver[0])})" if driver else ""
        return f"Beneish M-Score{why}"
    if result.name == "altman_z":
        zone = (result.zone or "").replace("_", " ")
        return f"Altman {result.variant or 'Z'} in the {zone} zone"
    if result.name == "piotroski_f":
        return f"Piotroski F-Score {int(total) if total is not None else '?'} of 9"
    pct = f" at {float(total) * 100:.0f}%" if total is not None else ""
    return f"accruals ratio{pct}"


def _first_sentence(text: str) -> str:
    head = text.split(". ")[0].strip()
    return head if head.endswith(".") else head + "."


def _score_text(result: ScoreResult) -> str:
    if result.name == "beneish_m":
        cutoff = next((t.value for t in result.thresholds if t.label == "cutoff"), None)
        total = result.total.value
        driver = beneish_driver(result)
        text = f"Beneish M-Score is {float(total):.2f}" if total is not None else "Beneish M-Score"
        if cutoff is not None:
            text += f" (the alarm starts above {float(cutoff):.2f})"
        text += ": the model's sign of possible earnings manipulation."
        if driver:
            text += (
                f" Biggest driver: {BENEISH_DRIVERS.get(driver[0], driver[0])} "
                f"({driver[0]} adds {float(driver[1]):+.2f})."
            )
        return text
    return f"{result.label}: {_first_sentence(result.interpretation)}"


def _swing_hint(swing: tuple[int, Decimal]) -> str:
    year, growth = swing
    return (
        f"Revenue moved {float(growth) * 100:+.0f}% in FY{year}. This screen is error-prone for "
        f"fast-growing, shrinking and cyclical companies, so read it as a reason to look, not as "
        f"evidence."
    )


def scores_area(
    scores: Scores | None, metrics: Metrics | None, error: str | None
) -> tuple[AreaLight, list[Reason]]:
    label = "Forensic scores"
    if scores is None:
        return AreaLight(
            area="scores",
            label=label,
            light=Light.GRAY,
            summary=f"not available: {error}" if error else "not available",
        ), []
    results = [scores.latest(name) for name in SCORE_NAMES]
    lights = [score_light(r) for r in results]
    if all(not r.applicable for r in results):
        reason = results[0].not_applicable_reason or "not applicable"
        gray = Reason(
            area="scores",
            light=Light.GRAY,
            text=f"The forensic scores do not apply: {reason}.",
            section="scores",
        )
        return AreaLight(
            area="scores", label=label, light=Light.GRAY, summary=f"not applicable: {reason}"
        ), [gray]

    reds = lights.count(Light.RED)
    lone_red = reds == 1
    swing = growth_swing(metrics)
    reasons: list[Reason] = []
    for result, light in zip(results, lights, strict=True):
        if light not in (Light.RED, Light.YELLOW):
            continue
        shown = Light.YELLOW if light is Light.RED and reds < RED_SCREENS_FOR_RED else light
        hints: list[str] = []
        if light is Light.RED and lone_red:
            others = [
                r.label for r, other in zip(results, lights, strict=True) if other is Light.GREEN
            ]
            if others:
                hints.append(
                    f"The other screens disagree ({', '.join(others)} look fine), and one screen "
                    f"alone is not a verdict."
                )
            else:
                hints.append("One screen alone is not a verdict.")
        if swing is not None and result.name in GROWTH_SENSITIVE:
            hints.append(_swing_hint(swing))
        reasons.append(
            Reason(
                area="scores",
                light=shown,
                text=_score_text(result),
                section="scores",
                hint=" ".join(hints) or None,
            )
        )
    counts = {
        "fine": lights.count(Light.GREEN),
        "worth a look": lights.count(Light.YELLOW),
        "red": reds,
        "not available": lights.count(Light.GRAY),
    }
    summary = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
    if lone_red:
        summary += " (a lone red screen only counts as worth a look)"
    light = (
        Light.RED
        if reds >= RED_SCREENS_FOR_RED
        else worst([Light.YELLOW if li is Light.RED else li for li in lights])
    )
    return AreaLight(area="scores", label=label, light=light, summary=summary), reasons


def flags_area(
    flags: RedFlags | None, as_of: date, error: str | None
) -> tuple[AreaLight, list[Reason]]:
    label = "Red flags in filings"
    if flags is None:
        return AreaLight(
            area="red_flags",
            label=label,
            light=Light.GRAY,
            summary=f"not available: {error}" if error else "not available",
        ), []
    recent_from = years_before(as_of, RECENT_YEARS)
    high = [f for f in flags.flags if f.severity is Severity.HIGH]
    medium = [f for f in flags.flags if f.severity is Severity.MEDIUM]
    low = [f for f in flags.flags if f.severity is Severity.LOW]
    high_recent = [f for f in high if f.filing_date >= recent_from]
    medium_recent = [f for f in medium if f.filing_date >= recent_from]
    high_old = [f for f in high if f.filing_date < recent_from]
    if high_recent:
        light = Light.RED
    elif medium_recent or high_old:
        light = Light.YELLOW
    else:
        light = Light.GREEN

    reasons: list[Reason] = []
    for flag, flag_light in (
        [(f, Light.RED) for f in high_recent]
        + [(f, Light.YELLOW) for f in medium_recent + high_old]
    )[:MAX_FLAG_REASONS]:
        reasons.append(
            Reason(
                area="red_flags",
                light=flag_light,
                text=f"{flag.title} ({flag.filing_date})",
                section="red-flags",
            )
        )
    years = max(1, round((flags.as_of - flags.since).days / 365))
    summary = (
        f"{len(high)} high, {len(medium)} medium, {len(low)} low in the last {years} years"
        f" (the light looks at the last {RECENT_YEARS})"
    )
    return AreaLight(area="red_flags", label=label, light=light, summary=summary), reasons


def insiders_area(
    activity: InsiderActivity | None, error: str | None
) -> tuple[AreaLight, list[Reason]]:
    label = "Insider activity"
    if activity is None:
        return AreaLight(
            area="insiders",
            label=label,
            light=Light.GRAY,
            summary=f"not available: {error}" if error else "not available",
        ), []
    totals = activity.totals
    months = max(1, round((activity.as_of - activity.since).days / 30.4))
    summary = (
        f"{plural(totals.purchases.count, 'open-market purchase')}, "
        f"{plural(totals.sales.count, 'sale')} ({totals.planned_sales.count} under 10b5-1 plans) "
        f"in the last {months} months"
    )
    reasons = [
        Reason(
            area="insiders",
            light=Light.INFO,
            text=(
                f"Cluster buy: {len(cluster.insiders)} insiders bought between "
                f"{cluster.start} and {cluster.end}."
            ),
            section="insiders",
        )
        for cluster in activity.clusters
    ]
    return AreaLight(area="insiders", label=label, light=Light.INFO, summary=summary), reasons


def data_area(metrics: Metrics | None, errors: dict[str, str]) -> AreaLight:
    parts: list[str] = []
    if metrics is not None:
        year = metrics.fiscal_years[-1]
        missing = sum(1 for s in metrics.series.values() if s.annual[year].value is None)
        parts.append(f"{missing} of {len(metrics.series)} metrics not available for FY{year}")
    if errors:
        parts.append("sections missing: " + ", ".join(sorted(errors)))
    return AreaLight(
        area="data",
        label="Data coverage",
        light=Light.INFO,
        summary="; ".join(parts) or "complete",
    )
