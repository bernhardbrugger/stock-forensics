"""Typed views of the SEC EDGAR JSON responses.

Models that mirror SEC payloads ignore unknown fields, so a new field added by the SEC does
not break parsing. Numbers are parsed as ``Decimal`` (see ``SecEdgarClient``) to stay exact.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Company, Source

_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Raw(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


# --- submissions (filing index) ---------------------------------------------------------


class Filing(_Frozen):
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None = None
    items: tuple[str, ...] = ()  # 8-K items such as "4.01", "4.02"
    primary_document: str | None = None
    document_url: str | None = None  # link to the original filing document


class Submissions(_Frozen):
    cik: int
    name: str
    sic_code: str | None = None
    industry: str | None = None
    tickers: tuple[str, ...] = ()
    fiscal_year_end: str | None = None  # "MMDD", e.g. "0930"
    filings: tuple[Filing, ...] = ()  # from filings.recent, newest first
    older_files: tuple[str, ...] = ()  # names of additional, older filing index pages
    source: Source

    def filings_of_form(self, form: str, *, include_amendments: bool = False) -> tuple[Filing, ...]:
        """Filings of one form type, e.g. ``"10-K"``; optionally with ``"10-K/A"``."""
        wanted = {form, f"{form}/A"} if include_amendments else {form}
        return tuple(f for f in self.filings if f.form in wanted)

    def to_company(self, ticker: str) -> Company:
        return Company(
            ticker=ticker,
            cik=self.cik,
            name=self.name,
            sic_code=self.sic_code,
            industry=self.industry,
        )


def _at(columns: dict[str, Any], name: str, index: int) -> Any:
    values = columns.get(name) or []
    return values[index] if index < len(values) else None


def _opt_date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_submissions(raw: dict[str, Any], source: Source) -> Submissions:
    cik = int(raw["cik"])
    recent: dict[str, Any] = (raw.get("filings") or {}).get("recent") or {}
    filings: list[Filing] = []
    for i, accession in enumerate(recent.get("accessionNumber") or []):
        document = _at(recent, "primaryDocument", i) or None
        items_text = _at(recent, "items", i) or ""
        filings.append(
            Filing(
                accession_number=accession,
                form=_at(recent, "form", i),
                filing_date=date.fromisoformat(_at(recent, "filingDate", i)),
                report_date=_opt_date(_at(recent, "reportDate", i)),
                items=tuple(p.strip() for p in items_text.split(",") if p.strip()),
                primary_document=document,
                document_url=(
                    f"{_ARCHIVES}/{cik}/{accession.replace('-', '')}/{document}"
                    if document
                    else None
                ),
            )
        )
    files = (raw.get("filings") or {}).get("files") or []
    return Submissions(
        cik=cik,
        name=raw["name"],
        sic_code=raw.get("sic") or None,
        industry=raw.get("sicDescription") or None,
        tickers=tuple(raw.get("tickers") or ()),
        fiscal_year_end=raw.get("fiscalYearEnd") or None,
        filings=tuple(filings),
        older_files=tuple(f["name"] for f in files if "name" in f),
        source=source,
    )


# --- company facts (XBRL) ---------------------------------------------------------------


class Fact(_Raw):
    """One reported XBRL fact. ``accn`` is the accession number of the reporting filing."""

    end: date
    start: date | None = None
    val: Decimal
    accn: str
    fy: int | None = None
    fp: str | None = None  # "FY", "Q1", "Q2", "Q3"
    form: str
    filed: date
    frame: str | None = None


class ConceptFacts(_Raw):
    label: str | None = None
    description: str | None = None
    units: dict[str, tuple[Fact, ...]]  # unit name ("USD", "shares", ...) -> facts


class CompanyFacts(_Frozen):
    cik: int
    entity_name: str
    facts: dict[str, dict[str, ConceptFacts]]  # taxonomy ("us-gaap", "dei") -> concept -> facts
    source: Source

    def concept(self, taxonomy: str, name: str) -> ConceptFacts | None:
        return self.facts.get(taxonomy, {}).get(name)


def parse_company_facts(raw: dict[str, Any], source: Source) -> CompanyFacts:
    facts = {
        taxonomy: {name: ConceptFacts.model_validate(concept) for name, concept in concepts.items()}
        for taxonomy, concepts in (raw.get("facts") or {}).items()
    }
    return CompanyFacts(
        cik=int(raw["cik"]),
        entity_name=raw["entityName"],
        facts=facts,
        source=source,
    )
