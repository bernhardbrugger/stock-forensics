"""Late filings: notifications of late filing (NT 10-K, NT 10-Q, Form 12b-25).

A company that cannot file its annual or quarterly report on time must say so with an NT form.
The flag comes from the form type; the detail adds how many days later the report followed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, Severity
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing

# NT form -> (the report it announces, forms that count as that report, severity)
_NOTICES: dict[str, tuple[str, tuple[str, ...], Severity]] = {
    "NT 10-K": ("annual report (10-K)", ("10-K", "10-KT", "10-K405"), Severity.MEDIUM),
    "NT 10-Q": ("quarterly report (10-Q)", ("10-Q", "10-QT"), Severity.LOW),
}


def _period_is_reliable(notice: Filing) -> bool:
    """The period of an NT form must end before the notice; the SEC index sometimes holds the
    filing date instead (Super Micro's NT 10-K of 2024-08-30 names the period 2024-08-30)."""
    return notice.report_date is not None and notice.report_date < notice.filing_date


def _follow_up(
    notice: Filing, report_forms: tuple[str, ...], filings: tuple[Filing, ...]
) -> Filing | None:
    """The earliest report of the announced kind filed after the notice.

    With a reliable period it must be the report for that period; otherwise the next report of
    that kind counts, and its own period is shown so that the reader can judge.
    """
    reliable = _period_is_reliable(notice)
    candidates = [
        f
        for f in filings
        if f.form in report_forms
        and f.filing_date >= notice.filing_date
        and (not reliable or f.report_date == notice.report_date)
        and (reliable or f.report_date is None or f.report_date < notice.filing_date)
    ]
    return min(candidates, key=lambda f: f.filing_date, default=None)


def check_late_filings(
    filings: tuple[Filing, ...],
    *,
    since: date,
    source_for: Callable[[Filing], Source],
) -> tuple[list[RedFlag], Coverage]:
    notices = [
        f
        for f in filings
        if f.filing_date >= since and any(f.form == nt or f.form == f"{nt}/A" for nt in _NOTICES)
    ]
    flags: list[RedFlag] = []
    for notice in sorted(notices, key=lambda f: f.filing_date):
        base = notice.form.removesuffix("/A")
        label, report_forms, severity = _NOTICES[base]
        report = _follow_up(notice, report_forms, filings)
        reliable = _period_is_reliable(notice)
        period = notice.report_date if reliable else (report.report_date if report else None)
        if report is None:
            outcome = (
                f"No matching {report_forms[0]} was filed afterwards in the examined filing "
                "history."
            )
        else:
            days = (report.filing_date - notice.filing_date).days
            outcome = (
                f"The {report.form} for the period ended {report.report_date} followed {days} "
                f"days after the notice, on {report.filing_date}."
            )
        period_text = f" for the period ended {period}" if period else ""
        flags.append(
            RedFlag(
                kind=FlagKind.LATE_FILING,
                code="late_10k" if base == "NT 10-K" else "late_10q",
                severity=severity,
                title=f"Late filing of the {label}",
                detail=(
                    f"The company filed {notice.form} on {notice.filing_date}: it could not file "
                    f"its {label}{period_text} on time. {outcome}"
                ),
                filing_date=notice.filing_date,
                form=notice.form,
                period=period,
                accession_number=notice.accession_number,
                document_url=notice.document_url,
                source=source_for(notice),
            )
        )
    return flags, Coverage(
        kind=FlagKind.LATE_FILING,
        examined=len(notices),
        flagged=len(flags),
        notes=("detected from the filing index (form type), no document text is read",),
    )
