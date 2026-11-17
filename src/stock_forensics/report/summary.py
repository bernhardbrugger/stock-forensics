"""The short version of a report: overall light, headline and a few plain sentences.

Everything here is assembled from the findings by fixed rules, with the numbers taken from the
computed results. No language model writes any of it.
"""

from __future__ import annotations

from datetime import date

from stock_forensics.flags import years_before
from stock_forensics.flags.models import RedFlags, Severity
from stock_forensics.insiders.models import InsiderActivity
from stock_forensics.metrics.engine import Metrics
from stock_forensics.report.models import Light, Reason, Summary
from stock_forensics.report.traffic import (
    RECENT_YEARS,
    RED_SCREENS_FOR_RED,
    data_area,
    flags_area,
    growth_swing,
    insiders_area,
    plural,
    score_light,
    score_short,
    scores_area,
    worst,
)
from stock_forensics.scores import SCORE_NAMES
from stock_forensics.scores.base import Scores

_ORDER = {Light.RED: 0, Light.YELLOW: 1, Light.GRAY: 2, Light.INFO: 3, Light.GREEN: 4}


def headline(
    overall: Light, reasons: list[Reason], scores: Scores | None, scores_light: Light
) -> str:
    alarms = sum(1 for r in reasons if r.light is Light.RED)
    looks = sum(1 for r in reasons if r.light is Light.YELLOW)
    if overall is Light.RED:
        more = f" and {looks} more worth a look" if looks else ""
        return f"Alarm: {plural(alarms, 'serious finding')}{more}."
    if overall is Light.YELLOW:
        return f"Mostly fine, but {plural(looks, 'thing')} worth a look."
    if overall is Light.GREEN:
        text = "Nothing alarming in the checked data."
        if scores is None:
            text += " The forensic scores are not available."
        elif scores_light is Light.GRAY:
            text += " The forensic scores do not apply to this company."
        return text
    return "Could not be assessed from the data that could be checked."


def _scores_sentence(scores: Scores | None, metrics: Metrics | None) -> str | None:
    if scores is None:
        return None
    results = [scores.latest(name) for name in SCORE_NAMES]
    lights = [score_light(r) for r in results]
    if all(not r.applicable for r in results):
        reason = results[0].not_applicable_reason or "not applicable"
        return f"The four forensic scores don't apply to this kind of company ({reason})."
    fine = lights.count(Light.GREEN)
    standing_out = [
        score_short(r)
        for r, li in zip(results, lights, strict=True)
        if li in (Light.RED, Light.YELLOW)
    ]
    gray = lights.count(Light.GRAY)
    if not standing_out:
        if gray:
            return f"{fine} of the forensic screens look fine; {gray} could not be computed."
        return (
            "The four forensic screens (manipulation, distress, financial strength, earnings "
            "quality) all look fine."
        )
    not_computed = f", {gray} could not be computed" if gray else ""
    text = (
        f"Of the four forensic screens, {fine} look fine{not_computed}. "
        f"Standing out: {'; '.join(standing_out)}."
    )
    reds = lights.count(Light.RED)
    if reds == 1:
        text += " Only one is red, and one screen alone is no verdict."
    elif reds >= RED_SCREENS_FOR_RED:
        text += " Several screens agree, so this is worth taking seriously."
    swing = growth_swing(metrics)
    if swing is not None and any(
        r.name in ("beneish_m", "accruals_ratio") and li is not Light.GREEN
        for r, li in zip(results, lights, strict=True)
    ):
        year, growth = swing
        text += (
            f" Revenue moved {float(growth) * 100:+.0f}% in FY{year}, which makes Beneish and the "
            f"accruals ratio unreliable."
        )
    return text


def _flags_sentence(flags: RedFlags | None, as_of: date) -> str | None:
    if flags is None:
        return None
    years = max(1, round((flags.as_of - flags.since).days / 365))
    caveat = (
        " Some documents could not be read, so this may be incomplete."
        if any("could not be read" in n for c in flags.coverage for n in c.notes)
        else ""
    )
    if not flags.flags:
        return (
            f"No auditor change, restatement, late filing or going-concern doubt in the last "
            f"{plural(years, 'year')} of filings.{caveat}"
        )
    recent_from = years_before(as_of, RECENT_YEARS)
    serious = [
        f for f in flags.flags if f.severity is not Severity.LOW and f.filing_date >= recent_from
    ]
    old_high = [
        f for f in flags.flags if f.severity is Severity.HIGH and f.filing_date < recent_from
    ]
    if serious:
        top = max(serious, key=lambda f: (f.severity is Severity.HIGH, f.filing_date))
        return (
            f"Filings: {plural(len(serious), 'flag')} of medium or high severity in the last "
            f"{RECENT_YEARS} years, for example: {top.title} ({top.filing_date}).{caveat}"
        )
    if old_high:
        older = old_high[0]
        return (
            f"Filings: no recent flags, but an older serious one: "
            f"{older.title} ({older.filing_date}).{caveat}"
        )
    return f"Filings: only minor flags ({len(flags.flags)}), nothing serious.{caveat}"


def _insiders_sentence(activity: InsiderActivity | None) -> str | None:
    if activity is None:
        return None
    totals = activity.totals
    months = max(1, round((activity.as_of - activity.since).days / 30.4))
    if totals.sales.count == 0 and totals.purchases.count == 0:
        return f"No open-market insider trades in the last {plural(months, 'month')}."
    text = (
        f"Insiders: {plural(totals.sales.count, 'sale')} "
        f"({totals.planned_sales.count} under pre-planned 10b5-1 programs) and "
        f"{plural(totals.purchases.count, 'purchase')} in the last {plural(months, 'month')}."
    )
    if activity.clusters:
        text += (
            f" {plural(len(activity.clusters), 'cluster buy')} (several insiders buying together)."
        )
    return text


def takeaways(
    *,
    scores: Scores | None,
    metrics: Metrics | None,
    red_flags: RedFlags | None,
    insiders: InsiderActivity | None,
    as_of: date,
    errors: dict[str, str],
) -> tuple[str, ...]:
    sentences = [
        _scores_sentence(scores, metrics),
        _flags_sentence(red_flags, as_of),
        _insiders_sentence(insiders),
    ]
    out = [s for s in sentences if s]
    if errors:
        out.append(f"Could not be checked: {', '.join(sorted(errors))} (see below for why).")
    return tuple(out)


def summarize(
    *,
    scores: Scores | None,
    metrics: Metrics | None,
    red_flags: RedFlags | None,
    insiders: InsiderActivity | None,
    as_of: date,
    errors: dict[str, str],
) -> Summary:
    scores_light, scores_reasons = scores_area(scores, metrics, errors.get("scores"))
    flags_light, flags_reasons = flags_area(red_flags, as_of, errors.get("red_flags"))
    insiders_light, insider_reasons = insiders_area(insiders, errors.get("insiders"))
    overall = worst([scores_light.light, flags_light.light])
    reasons = sorted(
        scores_reasons + flags_reasons + insider_reasons, key=lambda r: _ORDER[r.light]
    )
    return Summary(
        overall=overall,
        headline=headline(overall, reasons, scores, scores_light.light),
        areas=(scores_light, flags_light, insiders_light, data_area(metrics, errors)),
        reasons=tuple(reasons),
        takeaways=takeaways(
            scores=scores,
            metrics=metrics,
            red_flags=red_flags,
            insiders=insiders,
            as_of=as_of,
            errors=errors,
        ),
    )
