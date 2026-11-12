"""Forensic scores: a systematic search for warning signs in the numbers.

* Beneish M-Score: earnings manipulation screen
* Altman Z-Score (Z, Z' or Z''): distress risk
* Piotroski F-Score: financial strength, nine signals
* Accruals ratio: earnings quality

Each score returns a :class:`ScoreResult` with total, components, thresholds, zone,
interpretation and limitations. Banks, insurers, holding companies and REITs are marked
"not applicable" via their SIC code. See ``docs/scores.md``.
"""

from __future__ import annotations

from collections.abc import Mapping

from stock_forensics.models import Value
from stock_forensics.normalize.statements import Statements
from stock_forensics.scores.accruals import compute_accruals
from stock_forensics.scores.altman import compute_altman
from stock_forensics.scores.base import Component, ScoreResult, Scores, Threshold
from stock_forensics.scores.beneish import compute_beneish
from stock_forensics.scores.industry import exclusion_reason
from stock_forensics.scores.piotroski import compute_piotroski

SCORE_NAMES = ("beneish_m", "altman_z", "piotroski_f", "accruals_ratio")
UNKNOWN_SIC_NOTE = (
    "The SIC code is unknown, so it could not be checked whether this is a bank, insurer "
    "or REIT for which the score is not meaningful."
)


def compute_scores(
    statements: Statements,
    *,
    sic_code: str | None,
    industry: str | None = None,
    market_cap: Mapping[int, Value] | None = None,
) -> Scores:
    """All four scores for every fiscal year in ``statements``.

    ``sic_code`` (from the SEC submissions) decides applicability and the Altman variant.
    ``market_cap`` (fiscal year to market value of equity, a ``Value`` in USD) lets
    manufacturers use the original Altman Z; without it they get the book-value Z'.
    """
    excluded = exclusion_reason(sic_code, industry)
    results: dict[str, dict[int, ScoreResult]] = {name: {} for name in SCORE_NAMES}
    for fy in statements.fiscal_years:
        computed = {
            "beneish_m": compute_beneish(statements, fy, excluded=excluded),
            "altman_z": compute_altman(
                statements, fy, sic_code=sic_code, excluded=excluded, market_cap=market_cap
            ),
            "piotroski_f": compute_piotroski(statements, fy, excluded=excluded),
            "accruals_ratio": compute_accruals(statements, fy, excluded=excluded),
        }
        for name, result in computed.items():
            if sic_code is None:
                result = result.model_copy(
                    update={"limitations": (*result.limitations, UNKNOWN_SIC_NOTE)}
                )
            results[name][fy] = result
    return Scores(
        cik=statements.cik,
        company_name=statements.company_name,
        sic_code=sic_code,
        industry=industry,
        fiscal_years=statements.fiscal_years,
        results=results,
        source=statements.source,
    )


__all__ = [
    "SCORE_NAMES",
    "Component",
    "ScoreResult",
    "Scores",
    "Threshold",
    "compute_scores",
]
