"""Changes of the auditor: 8-K filings with Item 4.01 (Certifying Accountant).

The 8-K text tells who ended the relationship, which matters a lot:

* ``auditor_resigned``             the auditor resigned: high
* ``auditor_declined_to_stand``    the auditor declined to stand for re-appointment: medium
* ``auditor_dismissed``            the company dismissed or replaced the auditor: low
* ``auditor_engaged``              only the engagement of a new auditor is reported: low
* ``auditor_changed``              a change, but the text does not say how: medium
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, Severity
from stock_forensics.flags.text import html_to_text, section_after, sentences_matching
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing

# Filings write "Change" or "Changes in Registrant's Certifying Accountant".
# Patterns use ``.{0,n}?`` instead of ``[^.]`` because company names contain periods ("P.C.").
HEADING = re.compile(r"Item\s+4\.01\.?\s*Changes?\s+(in|to)\s+Registrant", re.I)
RESIGNED = re.compile(r"\b(resigned|resignation)\b", re.I)
DECLINED = re.compile(
    r"declin\w+ to (stand|seek)"
    r"|\b(will|did|does|would) not (stand|seek) for re-?(appointment|election)"
    r"|decided not to (stand|seek)"
    r"|will not seek to be (retained|re-?appointed|re-?engaged)"
    r"|not seek re-?(appointment|election|engagement|tention)",
    re.I,
)
DISMISSED = re.compile(
    r"\bdismiss(ed|al)\b"
    r"|terminated the (engagement|appointment|relationship)"
    r"|\b(decided|determined|approved)\b.{0,80}?\b(to (change|replace|dismiss)|replacement of)\b",
    re.I,
)
ENGAGED = re.compile(
    r"\b(engaged|appointed|selected|retained|engagement of|appointment of|selection of)\b"
    r".{0,200}?(registered public accounting firm|independent auditor|accountants?)",
    re.I,
)
FORMS = ("8-K", "8-K/A")
MAX_EVIDENCE = 2

_CLASSES: tuple[tuple[str, re.Pattern[str], Severity, str, str], ...] = (
    (
        "auditor_resigned",
        RESIGNED,
        Severity.HIGH,
        "Auditor resigned",
        "The company's auditor resigned.",
    ),
    (
        "auditor_declined_to_stand",
        DECLINED,
        Severity.MEDIUM,
        "Auditor declined to stand for re-appointment",
        "The company's auditor declined to stand for re-appointment.",
    ),
    (
        "auditor_dismissed",
        DISMISSED,
        Severity.LOW,
        "Company dismissed or replaced its auditor",
        "The company dismissed or replaced its auditor.",
    ),
    (
        "auditor_engaged",
        ENGAGED,
        Severity.LOW,
        "New auditor engaged",
        "The filing reports the engagement of a new auditor.",
    ),
)


def classify(text: str) -> tuple[str, Severity, str, str, list[str]]:
    """Code, severity, title, detail and evidence sentences for the text of an 8-K Item 4.01."""
    body = section_after(text, HEADING)
    for code, pattern, severity, title, detail in _CLASSES:
        evidence = sentences_matching(body, pattern)
        if evidence:
            return code, severity, title, detail, evidence[:MAX_EVIDENCE]
    return (
        "auditor_changed",
        Severity.MEDIUM,
        "Change in certifying accountant",
        "The filing reports a change in the certifying accountant; the text does not say who "
        "ended the relationship.",
        [],
    )


def check_auditor_changes(
    filings: tuple[Filing, ...],
    read_text: Callable[[Filing], bytes | str | None],
    *,
    since: date,
    source_for: Callable[[Filing], Source],
) -> tuple[list[RedFlag], Coverage]:
    relevant = [
        f for f in filings if f.form in FORMS and "4.01" in f.items and f.filing_date >= since
    ]
    flags: list[RedFlag] = []
    notes: list[str] = []
    for filing in sorted(relevant, key=lambda f: f.filing_date):
        raw = read_text(filing)
        evidence: list[str] = []
        if raw is None:
            notes.append(f"{filing.form} filed {filing.filing_date} could not be read")
            code = "auditor_changed"
            severity = Severity.MEDIUM
            title = "Change in certifying accountant"
            detail = (
                "The filing reports a change in the certifying accountant; its text could not "
                "be read, so it is unknown who ended the relationship."
            )
        else:
            code, severity, title, detail, evidence = classify(html_to_text(raw))
        flags.append(
            RedFlag(
                kind=FlagKind.AUDITOR_CHANGE,
                code=code,
                severity=severity,
                title=title,
                detail=detail,
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
        kind=FlagKind.AUDITOR_CHANGE,
        examined=len(relevant),
        flagged=len(flags),
        notes=tuple(notes),
    )
