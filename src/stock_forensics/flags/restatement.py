"""Restatements: 8-K filings with Item 4.02 (Non-Reliance on Previously Issued Statements).

Item 4.02 is filed when the company, or its auditor, concludes that financial statements already
published should no longer be relied on. The flag comes from the item number in the SEC filing
index; the text only adds the quoted reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, Severity
from stock_forensics.flags.text import html_to_text, section_after, sentences_matching
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing

HEADING = re.compile(r"Item\s+4\.02\.?\s*Non-?\s*Reliance", re.I)
REASON = re.compile(
    r"non-?reliance|no longer (be )?relied|should not be relied|restat\w+|errors?\b", re.I
)
FORMS = ("8-K", "8-K/A")
MAX_EVIDENCE = 3


def check_restatements(
    filings: tuple[Filing, ...],
    read_text: Callable[[Filing], bytes | str | None],
    *,
    since: date,
    source_for: Callable[[Filing], Source],
) -> tuple[list[RedFlag], Coverage]:
    relevant = [
        f for f in filings if f.form in FORMS and "4.02" in f.items and f.filing_date >= since
    ]
    flags: list[RedFlag] = []
    notes: list[str] = []
    for filing in sorted(relevant, key=lambda f: f.filing_date):
        raw = read_text(filing)
        evidence: list[str] = []
        if raw is None:
            notes.append(f"{filing.form} filed {filing.filing_date} could not be read")
        else:
            body = section_after(html_to_text(raw), HEADING)
            evidence = sentences_matching(body, REASON)[:MAX_EVIDENCE]
        flags.append(
            RedFlag(
                kind=FlagKind.RESTATEMENT,
                code="non_reliance",
                severity=Severity.HIGH,
                title="Non-reliance on previously issued financial statements",
                detail=(
                    "The company disclosed under Item 4.02 that previously issued financial "
                    "statements, or a related audit report or interim review, should no longer "
                    "be relied on (a restatement)."
                ),
                filing_date=filing.filing_date,
                form=filing.form,
                period=filing.report_date,
                accession_number=filing.accession_number,
                document_url=filing.document_url,
                evidence=tuple(evidence),
                source=source_for(filing),
            )
        )
    return flags, Coverage(
        kind=FlagKind.RESTATEMENT,
        examined=len(relevant),
        flagged=len(flags),
        notes=tuple(notes),
    )
