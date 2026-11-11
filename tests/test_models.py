import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_forensics.models import Company, Period, Source, Unit, Value, missing

RETRIEVED = datetime(2026, 11, 11, 13, 0, 0, tzinfo=UTC)


def make_source(**overrides: object) -> Source:
    fields: dict[str, object] = {
        "provider": "sec_edgar",
        "endpoint": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "form_type": "10-K",
        "accession_number": "0000320193-23-000106",
        "xbrl_concept": "us-gaap:Revenues",
        "retrieved_at": RETRIEVED,
    }
    fields.update(overrides)
    return Source.model_validate(fields)


def make_period(**overrides: object) -> Period:
    fields: dict[str, object] = {
        "fiscal_year": 2023,
        "quarter": None,
        "start": date(2022, 10, 2),
        "end": date(2023, 9, 30),
    }
    fields.update(overrides)
    return Period.model_validate(fields)


def make_value(**overrides: object) -> Value:
    fields: dict[str, object] = {
        "value": Decimal("383285000000"),
        "unit": Unit.USD,
        "period": make_period(),
        "source": make_source(),
        "note": None,
    }
    fields.update(overrides)
    return Value.model_validate(fields)


# --- JSON round trip (the "done when" of step 1) ---------------------------------------


def test_value_json_roundtrip_via_file(tmp_path: Path) -> None:
    original = make_value(note="Total net sales")
    path = tmp_path / "value.json"
    path.write_text(original.model_dump_json(), encoding="utf-8")

    loaded = Value.model_validate_json(path.read_text(encoding="utf-8"))

    assert loaded == original
    assert loaded.source == original.source
    assert loaded.source.accession_number == "0000320193-23-000106"
    assert loaded.source.retrieved_at == RETRIEVED
    assert loaded.period == original.period


@pytest.mark.parametrize(
    "number",
    ["383285000000", "6.13", "-1234567.891234", "0", "0.0000001"],
)
def test_decimal_precision_survives_roundtrip(number: str) -> None:
    original = make_value(value=Decimal(number))
    loaded = Value.model_validate_json(original.model_dump_json())
    assert loaded.value == Decimal(number)


def test_value_json_shape_is_plain_strings() -> None:
    data = json.loads(make_value().model_dump_json())
    assert data["unit"] == "USD"
    assert data["value"] == "383285000000"  # Decimal as string: no float rounding
    assert data["source"]["provider"] == "sec_edgar"
    assert data["period"]["end"] == "2023-09-30"


# --- missing values ---------------------------------------------------------------------


def test_missing_helper_sets_none_and_reason() -> None:
    v = missing(
        "concept not reported",
        unit=Unit.USD,
        period=make_period(),
        source=make_source(accession_number=None),
    )
    assert v.value is None
    assert v.note == "concept not reported"
    assert v.unit is Unit.USD


def test_missing_value_roundtrips() -> None:
    v = missing("needs 2 fiscal years", unit=Unit.RATIO, period=make_period(), source=make_source())
    loaded = Value.model_validate_json(v.model_dump_json())
    assert loaded == v
    assert loaded.value is None
    assert loaded.note == "needs 2 fiscal years"


@pytest.mark.parametrize("note", [None, "", "   "])
def test_value_none_without_reason_is_rejected(note: str | None) -> None:
    with pytest.raises(ValidationError, match="non-empty note"):
        make_value(value=None, note=note)


def test_zero_is_a_real_value_not_missing() -> None:
    v = make_value(value=Decimal("0"), note=None)
    assert v.value == Decimal("0")


# --- units ------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", list(Unit))
def test_every_unit_roundtrips(unit: Unit) -> None:
    v = make_value(unit=unit)
    assert Value.model_validate_json(v.model_dump_json()).unit is unit


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_value(unit="furlongs")


def test_unit_accepts_its_string_value() -> None:
    assert make_value(unit="USD/share").unit is Unit.USD_PER_SHARE


# --- Source -----------------------------------------------------------------------------


def test_source_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_source(retrieved_at=datetime(2026, 11, 11, 13, 0, 0))


def test_source_accepts_non_utc_offset() -> None:
    cet = timezone(timedelta(hours=1))
    s = make_source(retrieved_at=datetime(2026, 11, 11, 14, 0, 0, tzinfo=cet))
    assert s.retrieved_at == RETRIEVED


@pytest.mark.parametrize("bad", ["320193-23-106", "0000320193-2023-000106", "abc", ""])
def test_source_rejects_bad_accession_number(bad: str) -> None:
    with pytest.raises(ValidationError, match="accession_number"):
        make_source(accession_number=bad)


def test_source_optional_fields_default_to_none() -> None:
    s = Source(provider="sec_edgar", retrieved_at=RETRIEVED)
    assert s.endpoint is None
    assert s.form_type is None
    assert s.accession_number is None
    assert s.xbrl_concept is None


def test_source_requires_provider() -> None:
    with pytest.raises(ValidationError):
        make_source(provider="")


# --- Period -----------------------------------------------------------------------------


def test_period_instant_has_only_end_date() -> None:
    p = make_period(start=None)
    assert p.start is None
    assert Period.model_validate_json(p.model_dump_json()) == p


def test_period_quarter_roundtrips() -> None:
    p = make_period(quarter=3, start=date(2023, 4, 2), end=date(2023, 7, 1))
    assert Period.model_validate_json(p.model_dump_json()).quarter == 3


@pytest.mark.parametrize("quarter", [0, 5, -1])
def test_period_rejects_invalid_quarter(quarter: int) -> None:
    with pytest.raises(ValidationError):
        make_period(quarter=quarter)


def test_period_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError, match="start must not be after end"):
        make_period(start=date(2024, 1, 1), end=date(2023, 1, 1))


# --- Company ----------------------------------------------------------------------------


def test_company_normalizes_ticker_and_pads_cik() -> None:
    c = Company(ticker=" aapl ", cik=320193, name="Apple Inc.", sic_code="3571")
    assert c.ticker == "AAPL"
    assert c.cik_padded == "0000320193"
    assert Company.model_validate_json(c.model_dump_json()) == c


def test_company_optional_industry_fields() -> None:
    c = Company(ticker="MSFT", cik=789019, name="MICROSOFT CORP")
    assert c.sic_code is None
    assert c.industry is None


@pytest.mark.parametrize("cik", [0, -5])
def test_company_rejects_non_positive_cik(cik: int) -> None:
    with pytest.raises(ValidationError):
        Company(ticker="X", cik=cik, name="X Corp")


@pytest.mark.parametrize("sic", ["357", "35711", "abcd"])
def test_company_rejects_bad_sic(sic: str) -> None:
    with pytest.raises(ValidationError, match="sic_code"):
        Company(ticker="X", cik=1, name="X Corp", sic_code=sic)


# --- strictness -------------------------------------------------------------------------


def test_models_are_immutable() -> None:
    v = make_value()
    with pytest.raises(ValidationError):
        v.value = Decimal("1")  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Source.model_validate({"provider": "x", "retrieved_at": RETRIEVED, "bogus": 1})
