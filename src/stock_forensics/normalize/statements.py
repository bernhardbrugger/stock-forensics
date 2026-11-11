"""Normalized financial statements built from SEC company facts.

``build_statements`` turns the raw XBRL facts into one time series per standard field:
annual values per fiscal year, quarterly values and a trailing-twelve-month value. Every
value is a :class:`~stock_forensics.models.Value` that names its concept and accession
number. Gaps are explicit (``None`` plus a reason), never zeros or guesses.

Rules, in short:

* Only 10-K/10-Q style filings count (amendments included). If several filings report the
  same period, the latest filing wins; when it differs from the original, the note says so
  ("restated").
* 10-Qs report Q1 as a quarter but Q2/Q3 as year-to-date, and cash flows only year-to-date.
  Missing quarters are derived by subtraction and labelled as derived (Q4 = year - 9
  months). Per-share and average figures are never derived.
* TTM is the sum of the last four consecutive quarters (additive flows only).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Period, Source, Unit, Value, missing
from stock_forensics.normalize.concepts import (
    FIELD_BY_NAME,
    FIELDS,
    UNIT_KEY,
    FieldSpec,
    Kind,
    Statement,
)
from stock_forensics.normalize.periods import (
    ALLOWED_FORMS,
    ANNUAL_FORMS,
    MAX_QUARTER_DAYS,
    START_TOLERANCE_DAYS,
    FiscalCalendar,
    build_calendar,
    duration_days,
)
from stock_forensics.providers.sec_models import CompanyFacts, Fact

PROVIDER = "sec_edgar"
_PERIOD_LABELS = {1: "Q1", 2: "6M YTD", 3: "9M YTD", 4: "FY"}


class NoFinancialDataError(ValueError):
    """The company has no annual (10-K) data in USD that can be normalized."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Series(_Frozen):
    """One standard field over time."""

    name: str
    label: str
    statement: Statement
    unit: Unit
    annual: dict[int, Value]  # fiscal year -> value (gaps are Values with value=None)
    quarterly: dict[str, Value]  # "2024Q3" -> value
    ttm: Value | None = None  # trailing twelve months; only for additive flow items


class Statements(_Frozen):
    cik: int
    company_name: str
    fiscal_years: tuple[int, ...]
    quarters: tuple[str, ...]
    series: dict[str, Series]
    source: Source

    def get(self, name: str) -> Series:
        return self.series[name]

    def annual(self, name: str, fiscal_year: int) -> Value:
        return self.series[name].annual[fiscal_year]

    def of_statement(self, statement: Statement) -> tuple[Series, ...]:
        return tuple(s for s in self.series.values() if s.statement is statement)

    @property
    def latest_fiscal_year(self) -> int:
        return self.fiscal_years[-1]


# --- internals --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Picked:
    val: Decimal
    concept: str  # already formatted for Source.xbrl_concept
    accn: str
    form: str
    filed: date
    note: str | None = None


@dataclass(frozen=True)
class _Key:
    fy: int
    q: int | None  # None = fiscal year


def _join(*parts: str | None) -> str | None:
    text = "; ".join(p for p in parts if p)
    return text or None


def _fmt(value: Decimal) -> str:
    return f"{value:f}"


def _pick_latest(name: str, facts: list[Fact]) -> dict[tuple[date | None, date], _Picked]:
    """Per (start, end): the latest filing wins; a differing original is named in the note."""
    groups: dict[tuple[date | None, date], list[Fact]] = defaultdict(list)
    for fact in facts:
        groups[(fact.start, fact.end)].append(fact)
    picked: dict[tuple[date | None, date], _Picked] = {}
    for key, group in groups.items():
        group.sort(key=lambda f: (f.filed, f.accn))
        first, latest = group[0], group[-1]
        note = None
        if first.val != latest.val:
            note = (
                f"restated: originally {_fmt(first.val)} in {first.accn} "
                f"(filed {first.filed}), now {_fmt(latest.val)} in {latest.accn}"
            )
        picked[key] = _Picked(
            val=latest.val,
            concept=f"us-gaap:{name}",
            accn=latest.accn,
            form=latest.form,
            filed=latest.filed,
            note=note,
        )
    return picked


class _ConceptData:
    def __init__(self, name: str, facts: tuple[Fact, ...]) -> None:
        allowed = [f for f in facts if f.form in ALLOWED_FORMS]
        # All filings: 10-Q comparatives can carry a newer figure for an older quarter.
        self.idx = _pick_latest(name, allowed)
        # Annual filings only: a fiscal-year value must cite a 10-K, not the next 10-Q that
        # repeats the year-end balance sheet as a comparative.
        self.annual_idx = _pick_latest(name, [f for f in allowed if f.form in ANNUAL_FORMS])
        self.by_end: dict[date, list[tuple[date | None, _Picked]]] = defaultdict(list)
        for key, picked in self.idx.items():
            self.by_end[key[1]].append((key[0], picked))


class _Engine:
    def __init__(self, facts: CompanyFacts) -> None:
        self._gaap = facts.facts.get("us-gaap", {})
        self._cache: dict[tuple[str, Unit], _ConceptData | None] = {}
        self._other_units: dict[str, tuple[str, ...]] = {}
        self.source = facts.source
        self.cal = self._calendar()

    # -- concept access --

    def _concept(self, name: str, unit: Unit) -> _ConceptData | None:
        cache_key = (name, unit)
        if cache_key not in self._cache:
            concept = self._gaap.get(name)
            data: _ConceptData | None = None
            if concept is not None:
                rows = concept.units.get(UNIT_KEY[unit])
                if rows is None:
                    self._other_units[name] = tuple(sorted(concept.units))
                else:
                    data = _ConceptData(name, rows)
            self._cache[cache_key] = data
        return self._cache[cache_key]

    def _calendar(self) -> FiscalCalendar:
        facts: list[Fact] = []
        for spec in FIELDS:
            if not spec.is_flow:
                continue
            for name in spec.all_concepts:
                concept = self._gaap.get(name)
                if concept is not None:
                    facts.extend(concept.units.get(UNIT_KEY[spec.unit], ()))
        return build_calendar(facts)

    # -- flows: annual and quarterly --

    def _find(
        self, data: _ConceptData, end: date, fy_start: date | None, *, discrete: bool
    ) -> _Picked | None:
        """A fact ending on ``end``: a discrete quarter, or a period from the fiscal year start."""
        candidates: list[_Picked] = []
        for start, picked in data.by_end.get(end, ()):
            if start is None:
                continue
            days = duration_days(start, end)
            if discrete:
                matches = days <= MAX_QUARTER_DAYS
            else:
                matches = (
                    days > MAX_QUARTER_DAYS
                    and fy_start is not None
                    and abs((start - fy_start).days) <= START_TOLERANCE_DAYS
                )
            if matches:
                candidates.append(picked)
        return max(candidates, key=lambda p: (p.filed, p.accn)) if candidates else None

    def _reported_discrete(self, data: _ConceptData, fy: int, q: int) -> _Picked | None:
        bounds = self.cal.quarter_bounds(fy, q)
        if bounds is None:
            return None
        return self._find(data, bounds[1], None, discrete=True)

    def _ytd(self, data: _ConceptData, fy: int, k: int) -> _Picked | None:
        end = self.cal.quarter_ends.get((fy, k))
        if end is None:
            return None
        if k == 1:
            return self._reported_discrete(data, fy, 1)
        direct = self._find(data, end, self.cal.fy_start(fy), discrete=False)
        if direct is not None:
            return direct
        parts = [self._reported_discrete(data, fy, j) for j in range(1, k + 1)]
        if any(p is None for p in parts):
            return None
        present = [p for p in parts if p is not None]
        last = present[-1]
        return _Picked(
            val=sum((p.val for p in present), Decimal(0)),
            concept=last.concept,
            accn=last.accn,
            form=last.form,
            filed=last.filed,
            note=f"derived: sum of reported quarters Q1..Q{k}",
        )

    def _discrete(self, data: _ConceptData, fy: int, q: int, *, derivable: bool) -> _Picked | None:
        reported = self._reported_discrete(data, fy, q)
        if reported is not None or not derivable or q == 1:
            return reported
        current = self._ytd(data, fy, q)
        previous = self._ytd(data, fy, q - 1)
        if current is None or previous is None:
            return None
        return _Picked(
            val=current.val - previous.val,
            concept=current.concept,
            accn=current.accn,
            form=current.form,
            filed=current.filed,
            note=(
                f"derived: {_PERIOD_LABELS[q]} {_fmt(current.val)} ({current.accn}) - "
                f"{_PERIOD_LABELS[q - 1]} {_fmt(previous.val)} ({previous.accn})"
            ),
        )

    def _concept_at(self, name: str, spec: FieldSpec, key: _Key) -> _Picked | None:
        data = self._concept(name, spec.unit)
        if data is None:
            return None
        if spec.kind is Kind.INSTANT:
            year_end = key.q is None or key.q == 4
            end = (
                self.cal.fy_end(key.fy)
                if key.q is None
                else self.cal.quarter_ends.get((key.fy, key.q))
            )
            if end is None:
                return None
            index = data.annual_idx if year_end else data.idx
            return index.get((None, end))
        if key.q is None:
            bounds = self.cal.annual.get(key.fy)
            if bounds is None:
                return None
            return self._find(data, bounds[1], bounds[0], discrete=False)
        return self._discrete(data, key.fy, key.q, derivable=spec.is_additive)

    # -- fields --

    def resolve(self, spec: FieldSpec, key: _Key) -> tuple[_Picked | None, str]:
        """The value of a field for a period, or ``None`` with the reason why not."""
        for name in spec.concepts:
            picked = self._concept_at(name, spec, key)
            if picked is not None:
                extra = spec.concept_notes.get(name)
                return (
                    _Picked(
                        picked.val,
                        picked.concept,
                        picked.accn,
                        picked.form,
                        picked.filed,
                        _join(picked.note, extra),
                    ),
                    "",
                )
        if spec.sum_slots:
            summed = self._sum_slots(spec, key)
            if summed is not None:
                return summed, ""
        if spec.difference is not None:
            minuend = self.resolve(FIELD_BY_NAME[spec.difference[0]], key)[0]
            subtrahend = self.resolve(FIELD_BY_NAME[spec.difference[1]], key)[0]
            if minuend is not None and subtrahend is not None:
                return (
                    _Picked(
                        val=minuend.val - subtrahend.val,
                        concept=f"derived: {spec.difference[0]} - {spec.difference[1]}",
                        accn=minuend.accn,
                        form=minuend.form,
                        filed=minuend.filed,
                        note=(
                            f"derived: {spec.difference[0]} ({minuend.concept}) minus "
                            f"{spec.difference[1]} ({subtrahend.concept})"
                        ),
                    ),
                    "",
                )
        return None, self._reason(spec, key)

    def _sum_slots(self, spec: FieldSpec, key: _Key) -> _Picked | None:
        parts: list[_Picked] = []
        absent: list[str] = []
        for slot in spec.sum_slots:
            found = next(
                (p for name in slot if (p := self._concept_at(name, spec, key)) is not None),
                None,
            )
            if found is None:
                absent.append(slot[0])
            else:
                parts.append(found)
        if not parts or (spec.sum_requires_all and absent):
            return None
        anchor = max(parts, key=lambda p: (p.filed, p.accn))
        note = "derived: sum of " + " + ".join(f"{p.concept}" for p in parts)
        if absent:
            note += f" (not reported: {', '.join('us-gaap:' + a for a in absent)})"
        return _Picked(
            val=sum((p.val for p in parts), Decimal(0)),
            concept=" + ".join(p.concept for p in parts),
            accn=anchor.accn,
            form=anchor.form,
            filed=anchor.filed,
            note=_join(note, *(p.note for p in parts)),
        )

    def _reason(self, spec: FieldSpec, key: _Key) -> str:
        tried = ", ".join(f"us-gaap:{c}" for c in spec.all_concepts)
        reason = f"not reported for this period (tried: {tried})"
        if spec.difference is not None:
            reason += f"; also not derivable as {spec.difference[0]} - {spec.difference[1]}"
        foreign = [
            f"us-gaap:{c} only in {', '.join(self._other_units[c])}"
            for c in spec.all_concepts
            if c in self._other_units
        ]
        if foreign:
            reason += f"; no {UNIT_KEY[spec.unit]} values ({'; '.join(foreign)})"
        if key.q is not None and spec.kind is Kind.FLOW_NON_ADDITIVE:
            reason += "; per-share and average figures cannot be derived by subtraction"
        return reason

    # -- periods and values --

    def period(self, spec: FieldSpec, key: _Key) -> Period:
        if key.q is None:
            start, end = self.cal.annual[key.fy]
            return Period(
                fiscal_year=key.fy,
                quarter=None,
                start=None if spec.kind is Kind.INSTANT else start,
                end=end,
            )
        bounds = self.cal.quarter_bounds(key.fy, key.q)
        assert bounds is not None
        quarter_start, quarter_end = bounds
        return Period(
            fiscal_year=key.fy,
            quarter=key.q,
            start=None if spec.kind is Kind.INSTANT else quarter_start,
            end=quarter_end,
        )

    def value(self, spec: FieldSpec, key: _Key, period: Period | None = None) -> Value:
        picked, reason = self.resolve(spec, key)
        period = period or self.period(spec, key)
        if picked is None:
            return missing(reason, unit=spec.unit, period=period, source=self.source)
        return self._make_value(spec, picked, period)

    def _make_value(self, spec: FieldSpec, picked: _Picked, period: Period) -> Value:
        return Value(
            value=picked.val,
            unit=spec.unit,
            period=period,
            source=Source(
                provider=PROVIDER,
                endpoint=self.source.endpoint,
                form_type=picked.form,
                accession_number=picked.accn,
                xbrl_concept=picked.concept,
                retrieved_at=self.source.retrieved_at,
            ),
            note=picked.note,
        )

    def ttm(self, spec: FieldSpec) -> Value:
        quarters = self.cal.ordered_quarters()
        if not quarters:
            return self._ttm_missing(spec, None, "no quarterly data")
        latest_fy, latest_q, latest_end = quarters[-1]
        if latest_q == 4:  # the latest period is a full fiscal year: TTM equals it
            picked, reason = self.resolve(spec, _Key(latest_fy, None))
            start, end = self.cal.annual[latest_fy]
            period = Period(fiscal_year=latest_fy, quarter=None, start=start, end=end)
            if picked is None:
                return missing(
                    f"TTM not available: fiscal year {latest_fy} {reason}",
                    unit=spec.unit,
                    period=period,
                    source=self.source,
                )
            return self._make_value(
                spec,
                _Picked(
                    picked.val,
                    picked.concept,
                    picked.accn,
                    picked.form,
                    picked.filed,
                    _join(picked.note, f"TTM equals fiscal year {latest_fy}"),
                ),
                period,
            )
        window = quarters[-4:]
        if not _consecutive(window):
            return self._ttm_missing(spec, latest_end, "quarter calendar has gaps", latest_fy)
        parts: list[_Picked] = []
        for fy, q, _ in window:
            picked, reason = self.resolve(spec, _Key(fy, q))
            if picked is None:
                return self._ttm_missing(
                    spec, latest_end, f"{fy}Q{q} unavailable ({reason})", latest_fy
                )
            parts.append(picked)
        newest = parts[-1]
        first_bounds = self.cal.quarter_bounds(window[0][0], window[0][1])
        period = Period(
            fiscal_year=latest_fy,
            quarter=None,
            start=first_bounds[0] if first_bounds else None,
            end=latest_end,
        )
        listing = ", ".join(
            f"{fy}Q{q} {_fmt(p.val)} ({p.accn})"
            for (fy, q, _), p in zip(window, parts, strict=True)
        )
        return self._make_value(
            spec,
            _Picked(
                val=sum((p.val for p in parts), Decimal(0)),
                concept=newest.concept,
                accn=newest.accn,
                form=newest.form,
                filed=newest.filed,
                note=f"TTM: sum of last four quarters: {listing}",
            ),
            period,
        )

    def _ttm_missing(
        self, spec: FieldSpec, end: date | None, why: str, fy: int | None = None
    ) -> Value:
        fiscal_year = fy if fy is not None else max(self.cal.annual, default=1900)
        period = Period(
            fiscal_year=fiscal_year,
            quarter=None,
            start=None,
            end=end or date(fiscal_year, 12, 31),
        )
        return missing(
            f"TTM not available: {why}", unit=spec.unit, period=period, source=self.source
        )


def _consecutive(window: list[tuple[int, int, date]]) -> bool:
    if len(window) != 4:
        return False
    for (fy_a, q_a, _), (fy_b, q_b, _) in zip(window, window[1:], strict=False):
        expected = (fy_a, q_a + 1) if q_a < 4 else (fy_a + 1, 1)
        if (fy_b, q_b) != expected:
            return False
    return True


def build_statements(facts: CompanyFacts, *, years: int = 6, quarters: int = 8) -> Statements:
    """Normalize company facts into standard annual, quarterly and TTM series.

    ``years`` and ``quarters`` limit how much history is returned (newest first kept).
    """
    engine = _Engine(facts)
    fiscal_years = sorted(engine.cal.annual)[-years:]
    if not fiscal_years:
        raise NoFinancialDataError(
            f"{facts.entity_name}: no annual (10-K) data with dated USD facts found."
        )
    recent_quarters = engine.cal.ordered_quarters()[-quarters:]
    quarter_keys = [(fy, q) for fy, q, _ in recent_quarters]

    series: dict[str, Series] = {}
    for spec in FIELDS:
        annual = {fy: engine.value(spec, _Key(fy, None)) for fy in fiscal_years}
        quarterly = {f"{fy}Q{q}": engine.value(spec, _Key(fy, q)) for fy, q in quarter_keys}
        series[spec.name] = Series(
            name=spec.name,
            label=spec.label,
            statement=spec.statement,
            unit=spec.unit,
            annual=annual,
            quarterly=quarterly,
            ttm=engine.ttm(spec) if spec.is_additive else None,
        )
    return Statements(
        cik=facts.cik,
        company_name=facts.entity_name,
        fiscal_years=tuple(fiscal_years),
        quarters=tuple(f"{fy}Q{q}" for fy, q in quarter_keys),
        series=series,
        source=facts.source,
    )
