"""The forensic report: everything the analysis found about one company, in one object.

The report keeps the full results of every section (statements, metrics, scores, red flags,
insider activity). If a section could not be produced, it is ``None`` and ``errors`` says why,
so the rest of the report is still delivered and nothing is missing silently.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stock_forensics.flags.models import RedFlags
from stock_forensics.insiders.models import InsiderActivity
from stock_forensics.metrics.engine import Metrics
from stock_forensics.normalize.statements import Statements
from stock_forensics.scores.base import Scores

DISCLAIMER = (
    "Not financial advice. This report flags risks in public data (SEC filings). It does not "
    "recommend buying or selling anything, and a flag is a reason to read the filing, not proof "
    "of wrongdoing."
)


class Light(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GRAY = "gray"  # not applicable or not available
    INFO = "info"  # shown for information, no warning attached


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Reason(_Frozen):
    """One reason behind a traffic light."""

    area: str
    light: Light
    text: str
    section: str  # anchor of the section of the report with the details


class AreaLight(_Frozen):
    area: str  # "scores", "red_flags", "insiders", "data"
    label: str
    light: Light
    summary: str


class Summary(_Frozen):
    overall: Light
    headline: str
    areas: tuple[AreaLight, ...]
    reasons: tuple[Reason, ...]


class SourceEntry(_Frozen):
    """A filing that numbers of the report come from."""

    accession_number: str
    form_type: str | None
    period_end: date | None
    url: str  # the filing on sec.gov
    used_for: tuple[str, ...]


class ForensicReport(_Frozen):
    ticker: str
    company_name: str
    cik: int
    sic_code: str | None = None
    industry: str | None = None
    fiscal_year_end: str | None = None  # "MMDD"
    as_of: date
    retrieved_at: datetime  # when the SEC data was fetched
    version: str
    statements: Statements | None = None
    metrics: Metrics | None = None
    scores: Scores | None = None
    red_flags: RedFlags | None = None
    insiders: InsiderActivity | None = None
    summary: Summary
    sources: tuple[SourceEntry, ...] = ()
    errors: dict[str, str] = {}  # section -> why it is missing
    disclaimer: str = DISCLAIMER
