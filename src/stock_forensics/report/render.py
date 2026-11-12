"""Render a :class:`ForensicReport` as HTML, Markdown or JSON.

The HTML is one self-contained file (inline CSS, no scripts, no external resources), so it can be
emailed, archived or opened offline. Numbers are formatted here for display only; the report
object keeps the exact values. Every figure in the HTML carries its provenance as a tooltip
(formula, inputs, filing, XBRL concept).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from stock_forensics.insiders.models import Category
from stock_forensics.metrics.engine import Group
from stock_forensics.models import Unit, Value
from stock_forensics.report.models import ForensicReport, Light
from stock_forensics.report.traffic import score_light
from stock_forensics.scores import SCORE_NAMES

Format = Literal["html", "md", "json"]
FORMATS: tuple[Format, ...] = ("html", "md", "json")
TEMPLATE_DIR = Path(__file__).parent / "templates"
METRIC_YEARS = 5
MAX_TRANSACTIONS = 30
MAX_PEOPLE = 10

LIGHT_LABELS = {
    Light.GREEN: "Clear",
    Light.YELLOW: "Caution",
    Light.RED: "Warning",
    Light.GRAY: "Not available",
    Light.INFO: "Information",
}
GROUP_TITLES = {
    Group.PROFITABILITY: "Profitability",
    Group.LIQUIDITY: "Liquidity and leverage",
    Group.CASH_FLOW: "Cash flow quality",
    Group.GROWTH: "Growth",
    Group.CAPITAL_ALLOCATION: "Capital allocation",
}
# How a ratio is shown: "percent" (0.25 -> 25.0 %) or "multiple" (1.5 -> 1.50x). Every published
# metric must be listed here (a test checks it), because the unit "ratio" alone does not tell.
METRIC_STYLE: dict[str, str] = {
    "gross_margin": "percent",
    "operating_margin": "percent",
    "net_margin": "percent",
    "roe": "percent",
    "roa": "percent",
    "roic": "percent",
    "current_ratio": "multiple",
    "quick_ratio": "multiple",
    "debt_to_equity": "multiple",
    "interest_coverage": "multiple",
    "fcf_margin": "percent",
    "cash_conversion": "multiple",
    "revenue_growth_yoy": "percent",
    "net_income_growth_yoy": "percent",
    "revenue_cagr_3y": "percent",
    "net_income_cagr_3y": "percent",
    "revenue_growth_consistency": "percent",
    "net_income_growth_consistency": "percent",
    "payout_ratio": "percent",
    "buyback_ratio": "percent",
    "total_payout_ratio": "percent",
    "share_count_change_yoy": "percent",
    "rnd_ratio": "percent",
    "accruals_ratio": "percent",  # the forensic score of the same name
}
KEY_FINANCIALS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "cash_and_equivalents",
    "total_assets",
    "total_liabilities",
    "total_equity",
)

# --- number formatting (display only) ---------------------------------------------------------


def format_usd(amount: Decimal) -> str:
    number = float(amount)
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= limit:
            return f"{sign}${magnitude / limit:,.1f}{suffix}"
    return f"{sign}${magnitude:,.0f}"


def format_number(amount: Decimal, digits: int = 2) -> str:
    return f"{float(amount):,.{digits}f}"


def format_shares(amount: Decimal) -> str:
    number = float(amount)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            return f"{number / limit:,.1f}{suffix}"
    return f"{number:,.0f}"


def format_value(value: Value | None, name: str | None = None) -> str:
    """A value as text; a gap is "n/a". ``name`` selects the style of a ratio metric."""
    if value is None or value.value is None:
        return "n/a"
    number = value.value
    match value.unit:
        case Unit.USD:
            return format_usd(number)
        case Unit.USD_PER_SHARE:
            return f"${float(number):,.2f}"
        case Unit.SHARES:
            return format_shares(number)
        case Unit.PERCENT:
            return f"{float(number):.1f}%"
        case Unit.DAYS:
            return f"{float(number):,.0f} days"
        case Unit.COUNT:
            return f"{float(number):,.0f}"
        case Unit.SCORE:
            return format_number(number, 2)
        case Unit.RATIO:
            if METRIC_STYLE.get(name or "", "multiple") == "percent":
                return f"{float(number) * 100:.1f}%"
            return f"{float(number):.2f}x"
    return format_number(number)  # pragma: no cover - all units are handled above


def component_text(value: Value) -> str:
    """A score input: indices and ratios as plain numbers ("0.96x" would suggest a multiple)."""
    if value.value is not None and value.unit is Unit.RATIO:
        return format_number(value.value, 3)
    return format_value(value)


def provenance(value: Value | None) -> str:
    """Formula, inputs and filings behind a value, as plain text for a tooltip."""
    if value is None:
        return ""
    lines: list[str] = []
    if value.note:
        lines.append(value.note)
    lines.append(_source_line(value))
    for item in value.inputs:
        lines.append(f"- {format_value(item)}: {_source_line(item)}")
    return "\n".join(lines)


def _source_line(value: Value) -> str:
    source = value.source
    parts = [source.xbrl_concept or source.provider]
    filing = " ".join(p for p in (source.form_type, source.accession_number) if p)
    if filing:
        parts.append(filing)
    parts.append(
        f"FY{value.period.fiscal_year}"
        + (f" Q{value.period.quarter}" if value.period.quarter else "")
    )
    parts.append(f"period end {value.period.end}")
    return ", ".join(parts)


def _short_reason(value: Value | None) -> str:
    if value is None or value.value is not None or not value.note:
        return ""
    return value.note.split(" (tried:")[0]


# --- view models for the templates ------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    text: str
    tooltip: str
    missing: bool


@dataclass(frozen=True)
class Row:
    label: str
    formula: str
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Table:
    title: str
    years: tuple[int, ...]
    rows: tuple[Row, ...]


def _cell(value: Value | None, name: str | None) -> Cell:
    missing = value is None or value.value is None
    tooltip = _short_reason(value) if missing else provenance(value)
    return Cell(format_value(value, name), tooltip, missing)


def metric_tables(report: ForensicReport) -> tuple[Table, ...]:
    if report.metrics is None:
        return ()
    years = report.metrics.fiscal_years[-METRIC_YEARS:]
    tables = []
    for group in Group:
        rows = tuple(
            Row(
                label=series.label,
                formula=series.formula,
                cells=tuple(_cell(series.annual.get(year), series.name) for year in years),
            )
            for series in report.metrics.of_group(group)
        )
        if rows:
            tables.append(Table(GROUP_TITLES[group], years, rows))
    return tuple(tables)


def financials_table(report: ForensicReport) -> Table | None:
    if report.statements is None:
        return None
    years = report.statements.fiscal_years[-METRIC_YEARS:]
    rows = tuple(
        Row(
            label=series.label,
            formula="",
            cells=tuple(_cell(series.annual.get(year), None) for year in years),
        )
        for name in KEY_FINANCIALS
        if (series := report.statements.series.get(name)) is not None
    )
    return Table("Key financials (as reported, fiscal years)", years, rows)


@dataclass(frozen=True)
class ScoreView:
    name: str
    label: str
    light: Light
    total: str
    zone: str
    interpretation: str
    fiscal_year: int
    variant: str | None
    applicable: bool
    reason: str | None
    formula: str
    reference: str
    constant: str | None
    components: tuple[dict[str, str], ...]
    thresholds: tuple[tuple[str, str, str], ...]
    limitations: tuple[str, ...]
    tooltip: str


def score_views(report: ForensicReport) -> tuple[ScoreView, ...]:
    if report.scores is None:
        return ()
    views = []
    for name in SCORE_NAMES:
        result = report.scores.latest(name)
        total = format_value(result.total, name)
        if name == "piotroski_f" and result.total.value is not None:
            total = f"{int(result.total.value)} of 9"
        components = tuple(
            {
                "label": c.label,
                "value": component_text(c.value),
                "weight": format_number(c.weight, 3) if c.weight is not None else "",
                "contribution": (
                    format_number(c.contribution, 3) if c.contribution is not None else ""
                ),
                "passed": "" if c.passed is None else ("yes" if c.passed else "no"),
                "tooltip": provenance(c.value),
            }
            for c in result.components
        )
        views.append(
            ScoreView(
                name=name,
                label=result.label,
                light=score_light(result),
                total=total,
                zone=(result.zone or "n/a").replace("_", " "),
                interpretation=result.interpretation,
                fiscal_year=result.fiscal_year,
                variant=result.variant,
                applicable=result.applicable,
                reason=result.not_applicable_reason,
                formula=result.formula,
                reference=result.reference,
                constant=(
                    format_number(result.constant, 3) if result.constant is not None else None
                ),
                components=components,
                thresholds=tuple(
                    (t.label, format_number(t.value, 2), t.meaning) for t in result.thresholds
                ),
                limitations=result.limitations,
                tooltip=provenance(result.total),
            )
        )
    return tuple(views)


def insider_view(report: ForensicReport) -> dict[str, Any] | None:
    activity = report.insiders
    if activity is None:
        return None
    market = [t for t in activity.transactions if t.category in (Category.PURCHASE, Category.SALE)]
    return {
        "activity": activity,
        "purchases": format_value(activity.totals.purchases.amount),
        "sales": format_value(activity.totals.sales.amount),
        "planned_sales": format_value(activity.totals.planned_sales.amount),
        "net": format_value(activity.totals.net_amount),
        "people": activity.people[:MAX_PEOPLE],
        "more_people": max(0, len(activity.people) - MAX_PEOPLE),
        "transactions": market[:MAX_TRANSACTIONS],
        "more_transactions": max(0, len(market) - MAX_TRANSACTIONS),
        "not_counted": {
            k.value.replace("_", " "): n for k, n in activity.totals.not_counted.items()
        },
    }


def build_context(report: ForensicReport) -> dict[str, Any]:
    return {
        "r": report,
        "financials": financials_table(report),
        "metric_tables": metric_tables(report),
        "scores": score_views(report),
        "insider": insider_view(report),
        "light_labels": LIGHT_LABELS,
    }


# --- rendering ---------------------------------------------------------------------------------


def _md_cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _environment(*, html: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]) if html else False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(
        {
            "val": format_value,
            "usd": format_usd,
            "num": format_number,
            "shares": format_shares,
            "prov": provenance,
            "md": _md_cell,
            "label": lambda light: LIGHT_LABELS[Light(light)],
            "iso": lambda d: d.isoformat() if isinstance(d, date) else str(d),
        }
    )
    return env


def render_html(report: ForensicReport) -> str:
    return _environment(html=True).get_template("report.html.j2").render(build_context(report))


def render_markdown(report: ForensicReport) -> str:
    return _environment(html=False).get_template("report.md.j2").render(build_context(report))


def render_json(report: ForensicReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def render(report: ForensicReport, fmt: Format) -> str:
    match fmt:
        case "html":
            return render_html(report)
        case "md":
            return render_markdown(report)
        case "json":
            return render_json(report)
