"""Amendments of annual reports (10-K/A).

Most 10-K/A filings are routine: a company without a proxy statement adds the Part III
information (directors, pay) within 120 days. Those are not flagged. The "Explanatory Note" of
each amendment is read:

* ``amendment_restatement``  restates or corrects the financial statements: high
* ``amendment_other``        any other substantive amendment: low
* ``amendment_unclassified`` the document could not be read: low
* ``frequent_amendments``    two or more substantive amendments in the period: medium
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, Severity
from stock_forensics.flags.text import html_to_text, sentences_matching
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing

FORMS = ("10-K/A", "10-KT/A", "10-K405/A")
NOTE = re.compile(r"EXPLANATORY\s+NOTE", re.I)
# "Restated certificate of incorporation" and "amended and restated credit agreement" are
# documents, not a restatement of the financial statements; a bare "correct an error on the
# cover page" is not one either: an error must concern the financial statements.
RESTATEMENT = re.compile(
    r"(?<!amended and )restat\w*+"  # possessive: no backtracking into the word
    r"(?!\s+(certificate|by-?laws|articles|charter|credit|plan|agreement|indenture))"
    r"|\b(errors?|misstatements?)\b.{0,80}?(financial statements|previously issued|audit report)"
    r"|(financial statements|previously issued).{0,80}?\b(errors?|misstatements?)\b"
    r"|\bcorrect\w*\b.{0,60}?(financial statements|previously issued|audit report)"
    r"|should no longer be relied",
    re.I,
)
PART_III = re.compile(r"part\s+iii\b", re.I)
WINDOW = 8000  # characters read after each "Explanatory Note" (long notes describe a merger first)
MAX_NOTES = 3  # the note can follow a table of contents, so look at the first few
MAX_EVIDENCE = 2
FREQUENT_FROM = 2


def explanatory_text(text: str) -> str:
    windows = [text[m.start() : m.start() + WINDOW] for m in list(NOTE.finditer(text))[:MAX_NOTES]]
    return " ".join(windows) if windows else text[:WINDOW]


def classify(text: str) -> tuple[str, list[str]]:
    """``restatement``, ``part_iii`` (routine) or ``other``, with evidence sentences."""
    note = explanatory_text(text)
    reasons = sentences_matching(note, RESTATEMENT)
    if reasons:
        return "restatement", reasons[:MAX_EVIDENCE]
    if PART_III.search(note):
        return "part_iii", []
    return "other", sentences_matching(note, re.compile(r"amend\w*", re.I))[:MAX_EVIDENCE]


def check_amendments(
    filings: tuple[Filing, ...],
    read_text: Callable[[Filing], bytes | str | None],
    *,
    since: date,
    source_for: Callable[[Filing], Source],
    aggregate_source: Source,
) -> tuple[list[RedFlag], Coverage]:
    amendments = [f for f in filings if f.form in FORMS and f.filing_date >= since]
    flags: list[RedFlag] = []
    notes: list[str] = []
    routine = 0
    substantive: list[Filing] = []
    for filing in sorted(amendments, key=lambda f: f.filing_date):
        raw = read_text(filing)
        kind: str
        evidence: list[str]
        if raw is None:
            notes.append(f"{filing.form} filed {filing.filing_date} could not be read")
            kind, evidence = "unclassified", []
        else:
            kind, evidence = classify(html_to_text(raw))
        if kind == "part_iii":
            routine += 1
            continue
        if kind != "unclassified":
            substantive.append(filing)
        code, severity, title, detail = {
            "restatement": (
                "amendment_restatement",
                Severity.HIGH,
                "Annual report amended to restate or correct the financial statements",
                "The amendment's explanatory note mentions a restatement or the correction of "
                "errors.",
            ),
            "other": (
                "amendment_other",
                Severity.LOW,
                "Annual report amended",
                "The annual report was amended; the explanatory note does not describe a "
                "restatement and is not limited to Part III.",
            ),
            "unclassified": (
                "amendment_unclassified",
                Severity.LOW,
                "Annual report amended (content not checked)",
                "The annual report was amended; the document could not be read.",
            ),
        }[kind]
        flags.append(
            RedFlag(
                kind=FlagKind.AMENDMENT,
                code=code,
                severity=severity,
                title=title,
                detail=f"{filing.form} filed {filing.filing_date} for the period ended "
                f"{filing.report_date}. {detail}",
                filing_date=filing.filing_date,
                form=filing.form,
                period=filing.report_date,
                accession_number=filing.accession_number,
                document_url=filing.document_url,
                evidence=tuple(evidence),
                source=source_for(filing),
            )
        )
    if len(substantive) >= FREQUENT_FROM:
        dates = ", ".join(str(f.filing_date) for f in substantive)
        flags.append(
            RedFlag(
                kind=FlagKind.AMENDMENT,
                code="frequent_amendments",
                severity=Severity.MEDIUM,
                title="Frequent amendments of annual reports",
                detail=(
                    f"{len(substantive)} substantive amendments of annual reports since "
                    f"{since}: {dates}."
                ),
                filing_date=substantive[-1].filing_date,
                form=substantive[-1].form,
                evidence=tuple(f"{f.form} filed {f.filing_date}" for f in substantive),
                source=aggregate_source,
            )
        )
    if routine:
        notes.append(f"{routine} routine amendment(s) adding Part III information were not flagged")
    return flags, Coverage(
        kind=FlagKind.AMENDMENT,
        examined=len(amendments),
        flagged=len(flags),
        notes=tuple(notes),
    )
