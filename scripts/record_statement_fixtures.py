"""Record real company facts of AAPL, MSFT and WMT as fixtures for the statements tests.

Usage (makes 3 live requests):
    SEC_USER_AGENT="stock-forensics you@example.com" \
        uv run python scripts/record_statement_fixtures.py

Responses are real but cut down: only the concepts used by the field mapping, only USD,
share and per-share units, only 10-K/10-Q style filings and periods ending on or after
2022-12-31. The three companies use different tags (Apple/Microsoft: revenue from contracts
with customers, Walmart: ``Revenues`` and a January fiscal year end).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stock_forensics.normalize.concepts import FIELDS, UNIT_KEY
from stock_forensics.normalize.periods import ALLOWED_FORMS
from stock_forensics.providers.sec_edgar import COMPANY_FACTS_URL, SecEdgarClient

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sec_edgar" / "statements"
TICKERS = ("AAPL", "MSFT", "WMT")
MIN_END = "2022-12-31"
KEEP = {c for spec in FIELDS for c in spec.all_concepts}
KEEP_UNITS = set(UNIT_KEY.values())


def trim(raw: dict[str, Any]) -> dict[str, Any]:
    concepts: dict[str, Any] = {}
    for name, concept in raw["facts"].get("us-gaap", {}).items():
        if name not in KEEP:
            continue
        units = {}
        for unit, rows in concept["units"].items():
            if unit not in KEEP_UNITS:
                continue
            kept = [r for r in rows if r["form"] in ALLOWED_FORMS and r["end"] >= MIN_END]
            if kept:
                units[unit] = kept
        if units:
            concepts[name] = {"label": concept.get("label"), "units": units}
    return {"cik": raw["cik"], "entityName": raw["entityName"], "facts": {"us-gaap": concepts}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with SecEdgarClient(use_cache=False) as client:
        for ticker in TICKERS:
            cik = client.ticker_to_cik(ticker).cik
            entry = client._fetch(  # noqa: SLF001 - raw body wanted here
                COMPANY_FACTS_URL.format(cik=f"{cik:010d}"), allow_cache=False
            )
            data = trim(json.loads(entry.body))
            path = OUT / f"{ticker.lower()}_facts.json"
            path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
            print(f"wrote {path.name}: {path.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
