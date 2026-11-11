"""Fiscal calendar reconstruction: labels come from the earliest filing, not from later ones."""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_forensics.normalize.periods import build_calendar
from stock_forensics.providers.sec_models import Fact


def fact(
    start: str | None,
    end: str,
    fy: int,
    fp: str,
    form: str,
    filed: str,
    accn: str = "0000000001-24-000001",
) -> Fact:
    data: dict[str, Any] = {
        "end": end,
        "val": 1,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start is not None:
        data["start"] = start
    return Fact.model_validate(data)


def test_52_53_week_years_are_recognised_as_annual() -> None:
    # Apple-like: FY2023 has 370 days (2022-09-25 to 2023-09-30), FY2024 has 364 days
    cal = build_calendar(
        [
            fact("2022-09-25", "2023-09-30", 2023, "FY", "10-K", "2023-11-03"),
            fact("2023-10-01", "2024-09-28", 2024, "FY", "10-K", "2024-11-01"),
        ]
    )
    assert cal.annual == {
        2023: (date(2022, 9, 25), date(2023, 9, 30)),
        2024: (date(2023, 10, 1), date(2024, 9, 28)),
    }
    assert cal.fy_end(2024) == date(2024, 9, 28)
    assert cal.fy_start(2024) == date(2023, 10, 1)


def test_quarter_ends_come_from_10q_filings() -> None:
    cal = build_calendar(
        [
            fact("2023-10-01", "2024-09-28", 2024, "FY", "10-K", "2024-11-01"),
            fact("2023-10-01", "2023-12-30", 2024, "Q1", "10-Q", "2024-02-02"),
            fact("2023-12-31", "2024-03-30", 2024, "Q2", "10-Q", "2024-05-03"),  # discrete Q2
            fact("2023-10-01", "2024-03-30", 2024, "Q2", "10-Q", "2024-05-03"),  # 6M YTD
            fact("2023-10-01", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),  # 9M YTD
        ]
    )
    assert cal.quarter_ends == {
        (2024, 1): date(2023, 12, 30),
        (2024, 2): date(2024, 3, 30),
        (2024, 3): date(2024, 6, 29),
        (2024, 4): date(2024, 9, 28),
    }
    assert cal.fy_start(2024) == date(2023, 10, 1)
    assert cal.quarter_bounds(2024, 1) == (date(2023, 10, 1), date(2023, 12, 30))
    assert cal.quarter_bounds(2024, 2) == (date(2023, 12, 31), date(2024, 3, 30))
    assert cal.quarter_bounds(2024, 4) == (date(2024, 6, 30), date(2024, 9, 28))  # day after Q3
    assert cal.quarter_bounds(2025, 1) is None


def test_ordered_quarters_are_sorted_by_end_date() -> None:
    cal = build_calendar(
        [
            fact("2024-01-01", "2024-12-31", 2024, "FY", "10-K", "2025-02-14"),
            fact("2023-01-01", "2023-12-31", 2023, "FY", "10-K", "2024-02-15"),
            fact("2024-01-01", "2024-03-31", 2024, "Q1", "10-Q", "2024-05-10"),
        ]
    )
    assert [(fy, q) for fy, q, _ in cal.ordered_quarters()] == [(2023, 4), (2024, 1), (2024, 4)]


def test_fiscal_year_label_comes_from_the_earliest_filing() -> None:
    # January year end: the company labels the year ending 2024-01-31 as fiscal 2023.
    # The next 10-K repeats it as a comparative tagged fy=2024; that must not relabel it.
    cal = build_calendar(
        [
            fact("2023-02-01", "2024-01-31", 2023, "FY", "10-K", "2024-03-20"),
            fact("2023-02-01", "2024-01-31", 2024, "FY", "10-K", "2025-03-20"),  # comparative
            fact("2024-02-01", "2025-01-31", 2024, "FY", "10-K", "2025-03-20"),
        ]
    )
    assert cal.annual == {
        2023: (date(2023, 2, 1), date(2024, 1, 31)),
        2024: (date(2024, 2, 1), date(2025, 1, 31)),
    }


def test_old_comparative_does_not_replace_the_real_year() -> None:
    # The oldest year is only seen as a comparative in the FY2023 10-K (fy=2023), so it clashes
    # with the real FY2023. The newest period wins.
    cal = build_calendar(
        [
            fact("2021-01-01", "2021-12-31", 2023, "FY", "10-K", "2024-02-15"),
            fact("2023-01-01", "2023-12-31", 2023, "FY", "10-K", "2024-02-15"),
        ]
    )
    assert cal.annual == {2023: (date(2023, 1, 1), date(2023, 12, 31))}


def test_other_forms_and_odd_durations_are_ignored() -> None:
    cal = build_calendar(
        [
            fact("2024-01-01", "2024-12-31", 2024, "FY", "8-K", "2025-01-30"),  # wrong form
            fact("2024-01-01", "2024-02-28", 2024, "FY", "10-K", "2025-02-14"),  # not a year
        ]
    )
    assert cal.annual == {}
    assert cal.quarter_ends == {}
