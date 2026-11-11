"""Record real SEC EDGAR responses and store trimmed copies as test fixtures.

Usage (makes 3 live requests):
    SEC_USER_AGENT="stock-forensics you@example.com" uv run python scripts/record_sec_fixtures.py

The responses are real, but cut down to keep the repo small: a few tickers, the most recent
filings, and a handful of XBRL concepts. Nothing is invented. The User-Agent is never
written to disk (no VCR cassettes, because they would record that header).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stock_forensics.providers.sec_edgar import (
    COMPANY_FACTS_URL,
    SUBMISSIONS_URL,
    TICKERS_URL,
    SecEdgarClient,
)

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sec_edgar"
AAPL_CIK = "0000320193"
KEEP_TICKERS = {"AAPL", "MSFT", "BRK-B", "NVDA", "JPM"}
KEEP_FILINGS = 150
KEEP_CONCEPTS = {
    "us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "Assets",
        "EarningsPerShareBasic",
    },
    "dei": {"EntityCommonStockSharesOutstanding"},
}
MIN_FISCAL_YEAR = 2021


def fetch_json(client: SecEdgarClient, url: str) -> Any:
    entry = client._fetch(url, allow_cache=False)  # noqa: SLF001 - raw body wanted here
    return json.loads(entry.body)


def write(name: str, data: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.name}: {path.stat().st_size / 1024:.0f} KiB")


def trim_tickers(raw: dict[str, Any]) -> dict[str, Any]:
    kept = [v for v in raw.values() if v["ticker"] in KEEP_TICKERS]
    return {str(i): v for i, v in enumerate(kept)}


def trim_submissions(raw: dict[str, Any]) -> dict[str, Any]:
    recent = raw["filings"]["recent"]
    raw["filings"]["recent"] = {k: v[:KEEP_FILINGS] for k, v in recent.items()}
    return raw


def trim_facts(raw: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for taxonomy, names in KEEP_CONCEPTS.items():
        concepts = raw["facts"].get(taxonomy, {})
        facts[taxonomy] = {}
        for name in names:
            concept = concepts.get(name)
            if concept is None:
                continue
            units = {
                unit: [f for f in rows if f.get("fy") is None or f["fy"] >= MIN_FISCAL_YEAR]
                for unit, rows in concept["units"].items()
            }
            facts[taxonomy][name] = {**concept, "units": units}
    raw["facts"] = facts
    return raw


def main() -> None:
    with SecEdgarClient(use_cache=False) as client:
        write("company_tickers.json", trim_tickers(fetch_json(client, TICKERS_URL)))
        write(
            "submissions_aapl.json",
            trim_submissions(fetch_json(client, SUBMISSIONS_URL.format(cik=AAPL_CIK))),
        )
        write(
            "companyfacts_aapl.json",
            trim_facts(fetch_json(client, COMPANY_FACTS_URL.format(cik=AAPL_CIK))),
        )


if __name__ == "__main__":
    main()
