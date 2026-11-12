"""Result models for the red flags found in SEC filings.

A red flag is not a number but an event or a statement in a filing: an auditor change, a
non-reliance (restatement) notice, a late-filing notice, a going-concern doubt or a substantive
amendment of an annual report. Every flag names its filing (date, form, accession number, link
to the original document) and quotes the sentences it is based on. ``RedFlags.coverage`` states
what was examined, so that "no flag" can be told apart from "not checked".
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Source


class FlagKind(StrEnum):
    GOING_CONCERN = "going_concern"
    AUDITOR_CHANGE = "auditor_change"
    RESTATEMENT = "restatement"
    LATE_FILING = "late_filing"
    AMENDMENT = "amendment"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RedFlag(_Frozen):
    kind: FlagKind
    code: str  # finer classification, e.g. "auditor_resigned", "going_concern_exists"
    severity: Severity
    title: str
    detail: str  # plain-language explanation
    filing_date: date
    form: str
    period: date | None = None  # the period the filing refers to, if any
    accession_number: str | None = None
    document_url: str | None = None  # link to the original filing document
    evidence: tuple[str, ...] = ()  # quoted sentences the flag is based on
    source: Source


class Coverage(_Frozen):
    """What one check examined; without it "no flag" could also mean "not looked at"."""

    kind: FlagKind
    examined: int  # filings examined by this check
    flagged: int
    notes: tuple[str, ...] = ()  # what could not be checked or was left out on purpose


class RedFlags(_Frozen):
    cik: int
    company_name: str
    as_of: date
    since: date  # start of the examined period
    flags: tuple[RedFlag, ...]  # newest first
    coverage: tuple[Coverage, ...]
    source: Source

    def of_kind(self, kind: FlagKind) -> tuple[RedFlag, ...]:
        return tuple(f for f in self.flags if f.kind is kind)

    def with_severity(self, severity: Severity) -> tuple[RedFlag, ...]:
        return tuple(f for f in self.flags if f.severity is severity)
