"""Core data model with provenance.

Every number in a report is a :class:`Value`: the number itself (or ``None`` with an
explanation), its unit, the period it refers to, and the :class:`Source` it came from.
Amounts are stored in absolute units (USD, not thousands or millions).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SIC_RE = re.compile(r"^\d{4}$")


class _Frozen(BaseModel):
    """Base for all models: immutable, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Unit(StrEnum):
    """Unit of a :class:`Value`."""

    USD = "USD"
    USD_PER_SHARE = "USD/share"
    SHARES = "shares"
    RATIO = "ratio"  # dimensionless, 0.25 means 25 %
    PERCENT = "percent"  # 25 means 25 %
    DAYS = "days"
    COUNT = "count"
    SCORE = "score"


class Source(_Frozen):
    """Where a value came from, precisely enough to verify it."""

    provider: str = Field(min_length=1, description='e.g. "sec_edgar"')
    endpoint: str | None = Field(default=None, description="URL or API endpoint")
    form_type: str | None = Field(default=None, description='e.g. "10-K", "8-K", "4"')
    accession_number: str | None = Field(default=None, description="e.g. 0000320193-23-000106")
    xbrl_concept: str | None = Field(default=None, description='e.g. "us-gaap:Revenues"')
    retrieved_at: datetime = Field(description="Timezone-aware retrieval timestamp")

    @field_validator("accession_number")
    @classmethod
    def _check_accession(cls, v: str | None) -> str | None:
        if v is not None and not _ACCESSION_RE.match(v):
            raise ValueError("accession_number must look like 0000320193-23-000106")
        return v

    @field_validator("retrieved_at")
    @classmethod
    def _check_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return v


class Period(_Frozen):
    """The fiscal period a value refers to.

    ``quarter`` is ``None`` for a full fiscal year. ``start`` is ``None`` for point-in-time
    values (balance sheet items), which only have an ``end`` date.
    """

    fiscal_year: int = Field(ge=1900, le=2200)
    quarter: int | None = Field(default=None, ge=1, le=4)
    start: date | None = None
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.start is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class Company(_Frozen):
    """Identity of a company as reported by the SEC."""

    ticker: str = Field(min_length=1)
    cik: int = Field(gt=0)
    name: str = Field(min_length=1)
    sic_code: str | None = Field(default=None, description="4-digit SIC code")
    industry: str | None = Field(default=None, description="SIC description")

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("sic_code")
    @classmethod
    def _check_sic(cls, v: str | None) -> str | None:
        if v is not None and not _SIC_RE.match(v):
            raise ValueError("sic_code must be 4 digits")
        return v

    @property
    def cik_padded(self) -> str:
        """CIK zero-padded to 10 digits, as used in SEC URLs."""
        return f"{self.cik:010d}"


class Value(_Frozen):
    """A number with provenance, or an explicitly explained gap.

    ``value`` is ``None`` when the number cannot be provided; ``note`` must then say why
    (never fill gaps with guesses or zeros). ``note`` may also carry a formula or remark
    for values that do have a number.

    ``inputs`` holds the values a computed number was calculated from (each with its own
    source), so a metric can be traced back to the filings. It is empty for reported values.
    """

    value: Decimal | None
    unit: Unit
    period: Period
    source: Source
    note: str | None = None
    inputs: tuple[Value, ...] = ()

    @model_validator(mode="after")
    def _missing_needs_reason(self) -> Self:
        if self.value is None and not (self.note and self.note.strip()):
            raise ValueError("a missing value needs a non-empty note explaining why")
        return self


Value.model_rebuild()  # resolve the self-reference of ``inputs``


def missing(
    reason: str,
    *,
    unit: Unit,
    period: Period,
    source: Source,
    inputs: tuple[Value, ...] = (),
) -> Value:
    """Build a :class:`Value` with no number and a clear explanation.

    ``source`` states where the number was looked for (e.g. the company-facts endpoint).
    ``inputs`` are the values that were available when a computation could not be completed.
    """
    return Value(value=None, unit=unit, period=period, source=source, note=reason, inputs=inputs)
