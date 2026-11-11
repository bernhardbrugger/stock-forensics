"""Fiscal calendar reconstruction from XBRL facts.

Pitfall this module exists for: in the SEC company-facts data, ``fy`` and ``fp`` describe the
*filing's* fiscal period, not the period of the value. A 10-K for fiscal 2023 also carries
the 2021 and 2022 comparatives, all tagged ``fy=2023``. A period is therefore identified by
its exact ``(start, end)`` dates, and it gets its fiscal label (``fy``/``fp``) from the
**earliest** filing that reports it, which is the filing of that period itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from stock_forensics.providers.sec_models import Fact

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A", "10-QT"})
ALLOWED_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS

ANNUAL_DAYS = (340, 380)  # 52/53-week years are 364 or 371 days
MAX_QUARTER_DAYS = 105  # 12/13-week quarters are 84 to 98 days
START_TOLERANCE_DAYS = 3  # year-to-date periods start on the fiscal year start


def duration_days(start: date, end: date) -> int:
    return (end - start).days


@dataclass(frozen=True)
class FiscalCalendar:
    annual: dict[int, tuple[date, date]]  # fiscal year -> (start, end)
    fy_starts: dict[int, date]  # fiscal year -> start (also known for the open year)
    quarter_ends: dict[tuple[int, int], date]  # (fiscal year, quarter 1-4) -> end

    def fy_start(self, fy: int) -> date | None:
        return self.fy_starts.get(fy)

    def fy_end(self, fy: int) -> date | None:
        bounds = self.annual.get(fy)
        return bounds[1] if bounds else None

    def quarter_bounds(self, fy: int, q: int) -> tuple[date | None, date] | None:
        """(start, end) of a discrete quarter; ``None`` if its end date is unknown."""
        end = self.quarter_ends.get((fy, q))
        if end is None:
            return None
        if q == 1:
            return self.fy_start(fy), end
        previous = self.quarter_ends.get((fy, q - 1))
        return (previous + timedelta(days=1) if previous else None), end

    def ordered_quarters(self) -> list[tuple[int, int, date]]:
        """All known quarters as (fiscal year, quarter, end), oldest first."""
        return sorted(((fy, q, end) for (fy, q), end in self.quarter_ends.items()), key=_by_end)


def _by_end(item: tuple[int, int, date]) -> date:
    return item[2]


def build_calendar(facts: Iterable[Fact]) -> FiscalCalendar:
    """Derive fiscal years and quarter ends from the facts of the mapped concepts."""
    earliest: dict[tuple[date | None, date], Fact] = {}
    for fact in facts:
        if fact.form not in ALLOWED_FORMS or fact.fy is None or fact.fp is None:
            continue
        key = (fact.start, fact.end)
        current = earliest.get(key)
        if current is None or (fact.filed, fact.accn) < (current.filed, current.accn):
            earliest[key] = fact

    annual: dict[int, tuple[date, date]] = {}
    fy_starts: dict[int, date] = {}
    quarter_ends: dict[tuple[int, int], date] = {}

    for (start, end), fact in earliest.items():
        if start is None or fact.fy is None or fact.fp is None:
            continue
        days = duration_days(start, end)
        if (
            fact.fp == "FY"
            and fact.form in ANNUAL_FORMS
            and ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]
        ):
            known = annual.get(fact.fy)
            if known is None or end > known[1]:  # a clash means an older comparative: keep newest
                annual[fact.fy] = (start, end)
                fy_starts[fact.fy] = start
        elif fact.fp in {"Q1", "Q2", "Q3"} and fact.form in QUARTERLY_FORMS:
            q = int(fact.fp[1])
            known_end = quarter_ends.get((fact.fy, q))
            if known_end is None or end > known_end:
                quarter_ends[(fact.fy, q)] = end
            # Q1 and year-to-date periods start on the fiscal year start (a discrete Q2/Q3 does not)
            if (q == 1 and days <= MAX_QUARTER_DAYS) or days > MAX_QUARTER_DAYS:
                fy_starts.setdefault(fact.fy, start)

    for fy, (_, end) in annual.items():
        quarter_ends[(fy, 4)] = end
    return FiscalCalendar(annual=annual, fy_starts=fy_starts, quarter_ends=quarter_ends)
