"""Traffic lights of the report: how the findings turn into green, yellow and red.

All thresholds are here, in one place, and are explained in ``docs/report.md``:

* **Scores** (latest fiscal year): Beneish "likely manipulator" red; Altman distress red, grey
  zone yellow; Piotroski weak red; accruals high red, elevated yellow. The area shows the worst
  applicable light; gray if no score applies (banks, insurers, REITs).
* **Red flags**: red if there is a high flag in the last 3 years; yellow for a medium flag in the
  last 3 years or an older high flag; green otherwise. Low flags are shown, but change nothing.
* **Insiders**: information only, never a warning. Sales are mostly diversification, and many
  are planned under Rule 10b5-1. Cluster buys are mentioned as context.
* **Metrics**: no light. A cut-off such as "current ratio below 1" would be an invention.
* **Overall**: the worst light of scores and red flags (gray does not count).
"""

from __future__ import annotations

from datetime import date

from stock_forensics.flags import years_before
from stock_forensics.flags.models import RedFlags, Severity
from stock_forensics.insiders.models import InsiderActivity
from stock_forensics.metrics.engine import Metrics
from stock_forensics.report.models import AreaLight, Light, Reason, Summary
from stock_forensics.scores import SCORE_NAMES
from stock_forensics.scores.base import ScoreResult, Scores

RECENT_YEARS = 3
MAX_FLAG_REASONS = 6

SCORE_LIGHTS: dict[str, dict[str, Light]] = {
    "beneish_m": {"likely_manipulator": Light.RED, "unlikely_manipulator": Light.GREEN},
    "altman_z": {"distress": Light.RED, "grey": Light.YELLOW, "safe": Light.GREEN},
    "piotroski_f": {"weak": Light.RED, "neutral": Light.GREEN, "strong": Light.GREEN},
    "accruals_ratio": {"high": Light.RED, "elevated": Light.YELLOW, "normal": Light.GREEN},
}
_ORDER = {Light.GREEN: 0, Light.YELLOW: 1, Light.RED: 2}


def worst(lights: list[Light]) -> Light:
    """The worst of the green, yellow and red lights; gray if there is none of them."""
    graded = [light for light in lights if light in _ORDER]
    return max(graded, key=_ORDER.__getitem__) if graded else Light.GRAY


def score_light(result: ScoreResult) -> Light:
    if not result.applicable or result.zone is None:
        return Light.GRAY
    return SCORE_LIGHTS.get(result.name, {}).get(result.zone, Light.GRAY)


def _first_sentence(text: str) -> str:
    head = text.split(". ")[0].strip()
    return head if head.endswith(".") else head + "."


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def scores_area(scores: Scores | None, error: str | None) -> tuple[AreaLight, list[Reason]]:
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
    reasons: list[Reason] = []
    for result, light in zip(results, lights, strict=True):
        if light in (Light.RED, Light.YELLOW):
            reasons.append(
                Reason(
                    area="scores",
                    light=light,
                    text=f"{result.label}: {_first_sentence(result.interpretation)}",
                    section="scores",
                )
            )
    if all(not r.applicable for r in results):
        reason = results[0].not_applicable_reason or "not applicable"
        reasons.append(
            Reason(
                area="scores",
                light=Light.GRAY,
                text=f"The forensic scores do not apply: {reason}.",
                section="scores",
            )
        )
        summary = f"not applicable: {reason}"
    else:
        counts = {
            "clear": lights.count(Light.GREEN),
            "caution": lights.count(Light.YELLOW),
            "warning": lights.count(Light.RED),
            "not available": lights.count(Light.GRAY),
        }
        summary = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
    return AreaLight(area="scores", label=label, light=worst(lights), summary=summary), reasons


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


def summarize(
    *,
    scores: Scores | None,
    metrics: Metrics | None,
    red_flags: RedFlags | None,
    insiders: InsiderActivity | None,
    as_of: date,
    errors: dict[str, str],
) -> Summary:
    scores_light, scores_reasons = scores_area(scores, errors.get("scores"))
    flags_light, flags_reasons = flags_area(red_flags, as_of, errors.get("red_flags"))
    insiders_light, insider_reasons = insiders_area(insiders, errors.get("insiders"))
    overall = worst([scores_light.light, flags_light.light])
    reasons = scores_reasons + flags_reasons + insider_reasons

    warnings = sum(1 for r in reasons if r.light is Light.RED)
    cautions = sum(1 for r in reasons if r.light is Light.YELLOW)
    if overall is Light.RED:
        headline = f"{plural(warnings, 'warning')} and {plural(cautions, 'caution')} found."
    elif overall is Light.YELLOW:
        headline = f"{plural(cautions, 'caution')} to look at, no warnings."
    elif overall is Light.GREEN:
        headline = "No warning signs found in the checked data."
        if scores is None:
            headline += " The forensic scores are not available."
        elif scores_light.light is Light.GRAY:
            headline += " The forensic scores do not apply to this company."
    else:
        headline = "Not assessable from the data that could be checked."
    return Summary(
        overall=overall,
        headline=headline,
        areas=(scores_light, flags_light, insiders_light, data_area(metrics, errors)),
        reasons=tuple(reasons),
    )
