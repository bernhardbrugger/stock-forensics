"""Going-concern doubt in annual reports (10-K).

The 10-K text is searched for "substantial doubt" (the phrase of ASC 205-40 and of the auditor's
report). Each sentence is classified; the filing is flagged only on sentences that state doubt:

* ``exists``      the company or its auditor states that substantial doubt exists or is raised
                  ("there is substantial doubt", "we have concluded that substantial doubt exists",
                  "recurring losses ... that raise substantial doubt")
* ``alleviated``  doubt was raised but management's plans removed or mitigate it ("initially raised
                  substantial doubt", "plans to alleviate substantial doubt")
* nothing         negations ("does not raise"), conditionals ("could raise"), the evaluation
                  framework ("evaluate whether there are conditions that raise ...") and headings
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, Severity
from stock_forensics.flags.text import html_to_text, sentences_matching
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing

DOUBT = re.compile(r"substantial doubt", re.I)
NEGATED = re.compile(
    r"\b(no|not|never)\b[^.]{0,60}substantial doubt"
    r"|\b(does|did|do|has|have|had) not (raise|cast|create|result)"
    r"|\bno longer\b[^.]{0,80}substantial doubt"
    r"|without substantial doubt",
    re.I,
)
FRAMING = re.compile(r"\bwhether\b[^.]{0,250}substantial doubt|\bevaluat\w+ (if|whether)\b", re.I)
HYPOTHETICAL = re.compile(
    r"\b(could|may|might|would|if|should)\b[^.]{0,140}substantial doubt"
    r"|substantial doubt[^.]{0,100}\b(could|may|might)\b",
    re.I,
)
ALLEVIATED = re.compile(
    r"alleviat|mitigat|initially (raised|created)|previously (raised|disclosed)"
    r"|has been (removed|eliminated)|eliminat",
    re.I,
)
EXISTS = re.compile(
    r"\bthere (is|exists)\b[^.]{0,40}substantial doubt"
    r"|substantial doubt (exists|remains|continues)"
    r"|(concluded|determined|believes?|states?|noted)\b[^.]{0,60}substantial doubt"
    r"|\b(raise|raises|raised|create|creates|created|creating|cast|casts)\b[^.]{0,20}"
    r"substantial doubt",
    re.I,
)
CORE_PHRASE = re.compile(r"going concern|ability to continue", re.I)

ANNUAL_REPORTS = ("10-K", "10-KT", "10-K405")
MAX_EVIDENCE = 3


def classify(sentence: str) -> str:
    """``exists``, ``alleviated`` or ``other`` for one sentence containing "substantial doubt"."""
    if NEGATED.search(sentence):
        return "other"
    if FRAMING.search(sentence) or HYPOTHETICAL.search(sentence):
        return "other"
    if ALLEVIATED.search(sentence):
        return "alleviated"
    if EXISTS.search(sentence):
        return "exists"
    return "other"


def analyze_text(text: str) -> tuple[str | None, list[str]]:
    """Status (``exists``, ``alleviated`` or ``None``) of a 10-K text and its evidence sentences."""
    sentences = sentences_matching(text, DOUBT)
    by_class: dict[str, list[str]] = {"exists": [], "alleviated": [], "other": []}
    for sentence in sentences:
        by_class[classify(sentence)].append(sentence)
    for status in ("exists", "alleviated"):
        chosen = by_class[status]
        if chosen:
            # prefer sentences that name the going-concern conclusion itself
            chosen = sorted(chosen, key=lambda s: not CORE_PHRASE.search(s))
            return status, chosen[:MAX_EVIDENCE]
    return None, []


def check_going_concern(
    filings: tuple[Filing, ...],
    read_text: Callable[[Filing], bytes | str | None],
    *,
    since: date,
    source_for: Callable[[Filing], Source],
) -> tuple[list[RedFlag], Coverage]:
    annual = [f for f in filings if f.form in ANNUAL_REPORTS and f.filing_date >= since]
    flags: list[RedFlag] = []
    notes: list[str] = []
    examined = 0
    for filing in sorted(annual, key=lambda f: f.filing_date):
        raw = read_text(filing)
        if raw is None:
            notes.append(f"{filing.form} filed {filing.filing_date} could not be read")
            continue
        examined += 1
        status, evidence = analyze_text(html_to_text(raw))
        if status is None:
            continue
        exists = status == "exists"
        flags.append(
            RedFlag(
                kind=FlagKind.GOING_CONCERN,
                code="going_concern_exists" if exists else "going_concern_alleviated",
                severity=Severity.HIGH if exists else Severity.MEDIUM,
                title=(
                    "Substantial doubt about the ability to continue as a going concern"
                    if exists
                    else "Going-concern doubt raised but alleviated by management's plans"
                ),
                detail=(
                    f"The {filing.form} for the period ended {filing.report_date} states that "
                    "substantial doubt exists about the company's ability to continue as a going "
                    "concern."
                    if exists
                    else f"The {filing.form} for the period ended {filing.report_date} says that "
                    "substantial doubt was raised and then alleviated or mitigated by management's "
                    "plans."
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
    notes.append("only the main document of each annual report is read, not its exhibits")
    return flags, Coverage(
        kind=FlagKind.GOING_CONCERN,
        examined=examined,
        flagged=len(flags),
        notes=tuple(notes),
    )
