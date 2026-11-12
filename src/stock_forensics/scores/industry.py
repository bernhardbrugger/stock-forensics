"""Industry exclusions for the forensic scores, based on the SIC code.

Beneish, Altman, Piotroski and the accruals ratio are built for operating, non-financial
companies. For banks, other lenders, brokers, insurers, holding companies and REITs the
statement structure is different (deposits, premiums, no gross profit, interest as the core
business), so the scores are not meaningful and are reported as "not applicable".
"""

from __future__ import annotations

# (first SIC, last SIC, description). Division "Finance, insurance and real estate" without
# real estate operators and developers (6500-6599), which have ordinary operating statements.
_EXCLUDED: tuple[tuple[int, int, str], ...] = (
    (6000, 6099, "bank or other depository institution"),
    (6100, 6199, "non-depository credit institution"),
    (6200, 6299, "securities or commodity broker, dealer or exchange"),
    (6300, 6399, "insurance carrier"),
    (6400, 6499, "insurance agent or broker"),
    (6700, 6797, "holding company or investment office"),
    (6798, 6798, "real estate investment trust (REIT)"),
    (6799, 6799, "investor or investment office"),
)


def exclusion_reason(sic_code: str | None, industry: str | None = None) -> str | None:
    """Why the scores do not apply to this SIC code, or ``None`` if they do (or it is unknown)."""
    if sic_code is None or not sic_code.isdigit():
        return None
    sic = int(sic_code)
    for first, last, description in _EXCLUDED:
        if first <= sic <= last:
            name = f"{description}, SIC {sic_code}"
            if industry:
                name += f", {industry}"
            return f"not meaningful for a {name}"
    return None


def is_manufacturer(sic_code: str | None) -> bool:
    """SIC division D (2000-3999): the sample the original Altman Z-score was built on."""
    return sic_code is not None and sic_code.isdigit() and 2000 <= int(sic_code) <= 3999
