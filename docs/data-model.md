# Data model

All models live in `src/stock_forensics/models.py` (pydantic v2, immutable, unknown fields are rejected).

Core idea: **every number is a `Value`** that carries its unit, its period and its source, so anyone can verify it. If a number cannot be provided, the `Value` says so explicitly instead of holding a guess or a zero.

## Models

| Model | Purpose | Key rules |
|---|---|---|
| `Unit` | Unit of a value: `USD`, `USD/share`, `shares`, `ratio`, `percent`, `days`, `count`, `score` | Amounts are in absolute units, never thousands or millions. `ratio` means 0.25 = 25 %, `percent` means 25 = 25 %. |
| `Source` | Where a value came from: `provider`, `endpoint`, `form_type`, `accession_number`, `xbrl_concept`, `retrieved_at` | `retrieved_at` must be timezone-aware. `accession_number` must look like `0000320193-23-000106`. |
| `Period` | `fiscal_year`, `quarter` (1-4, `None` = full year), `start`, `end` | `start` is `None` for point-in-time values (balance sheet). `start` must not be after `end`. |
| `Value` | `value`, `unit`, `period`, `source`, `note`, `inputs` | `value` is a `Decimal` or `None`. `None` requires a non-empty `note`. `source` is always required; for gaps it says where the number was looked for. `inputs` (default empty) holds the values a computed number was calculated from, each with its own source; see below. |
| `Company` | `ticker`, `cik`, `name`, `sic_code`, `industry` | Ticker is upper-cased. `cik_padded` gives the 10-digit form used in SEC URLs. `sic_code` is 4 digits. |

## Computed values and `inputs`

A reported number (for example revenue from a 10-K) has a `source` that points at the filing. A computed number (a ratio, a score) has no single filing, so it keeps the values it was computed from in `inputs`, each with its own source, and the formula in `note`. `Source.provider` of a computed value is `stock_forensics`, and its `retrieved_at` is that of the input data, so results are deterministic. Nesting is allowed: an input may itself have inputs.

## Why `Decimal`

Values are compared with the figures in the filings, so they must be exact. `Decimal` is serialized to JSON as a string, which round-trips without float rounding. Convert to `float` only at the edge where a library needs it (for example pandas).

## Missing data

```python
from stock_forensics.models import Unit, missing

gap = missing("concept not reported", unit=Unit.USD, period=period, source=source)
assert gap.value is None
```

A `Value` with `value=None` and no note is rejected. Reports show such values as "n/a" together with the note.

## JSON example

```json
{
  "value": "383285000000",
  "unit": "USD",
  "period": {
    "fiscal_year": 2023,
    "quarter": null,
    "start": "2022-10-02",
    "end": "2023-09-30"
  },
  "source": {
    "provider": "sec_edgar",
    "endpoint": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    "form_type": "10-K",
    "accession_number": "0000320193-23-000106",
    "xbrl_concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "retrieved_at": "2026-11-11T13:00:00Z"
  },
  "note": "Total net sales"
}
```

Save with `value.model_dump_json()`, load with `Value.model_validate_json(text)`; nothing is lost, including the source.
