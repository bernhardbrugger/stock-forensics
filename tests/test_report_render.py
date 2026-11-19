"""Rendering: the HTML is self-contained and safe, the Markdown and JSON are complete."""

from __future__ import annotations

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from stock_forensics.metrics import ALL_SPECS
from stock_forensics.models import Unit
from stock_forensics.report import (
    DISCLAIMER,
    ForensicReport,
    render,
    render_html,
    render_json,
    render_markdown,
)
from stock_forensics.report.models import Light
from stock_forensics.report.render import (
    METRIC_STYLE,
    format_shares,
    format_usd,
    format_value,
    metric_tables,
    provenance,
)

VOID = {"meta", "br", "hr", "img", "input", "link"}


class Balance(HTMLParser):
    """Checks that every opened element is closed in order."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.hrefs: list[str] = []
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        self.hrefs += [v for k, v in attrs if k == "href" and v]
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with {self.stack[-3:]} open")
        else:
            self.stack.pop()


@pytest.fixture(scope="module")
def html(aapl_report: ForensicReport) -> str:
    return render_html(aapl_report)


def test_html_is_well_formed(html: str) -> None:
    parser = Balance()
    parser.feed(html)
    assert parser.errors == []
    assert parser.stack == []
    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in html


def test_html_is_self_contained(html: str) -> None:
    parser = Balance()
    parser.feed(html)
    assert not parser.tags & {"script", "link", "img", "iframe", "form", "object", "embed"}
    assert "@import" not in html
    assert "url(" not in html
    assert not re.search(r"\bsrc\s*=", html)
    assert parser.hrefs, "filings are linked"
    assert all(h.startswith(("https://www.sec.gov/", "#")) for h in parser.hrefs)


def test_html_tells_what_a_reader_needs(html: str) -> None:
    assert "<title>AAPL forensic report</title>" in html
    assert "Apple Inc." in html
    assert DISCLAIMER in html
    assert "Mostly fine, but 1 thing worth a look." in html
    assert '<h3 style="margin-top:1.5rem">In short</h3>' in html
    for heading in ("Forensic scores", "Red flags in SEC filings", "Insider activity", "Sources"):
        assert heading in html
    assert "prefers-color-scheme: dark" in html  # dark mode


def test_lights_are_never_only_a_color(html: str) -> None:
    """Each light is a dot plus a text label, so colour blindness does not matter."""
    badges = re.findall(
        r'<span class="badge [a-z ]+"><span class="dot" aria-hidden="true"></span>([^<]+)</span>',
        html,
    )
    assert badges
    assert set(badges) <= {"Looks fine", "Worth a look", "Alarm", "Not available", "FYI"} | {
        "High severity",
        "Medium severity",
        "Low severity",
    }


def test_html_escapes_everything_from_outside(aapl_report: ForensicReport) -> None:
    hostile = aapl_report.model_copy(
        update={"company_name": '<script>alert("x")</script> & Co', "industry": "<b>bold</b>"}
    )
    out = render_html(hostile)
    assert "<script" not in out
    assert "<b>bold</b>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp; Co" in out
    parser = Balance()
    parser.feed(out)
    assert parser.errors == [] and parser.stack == []


def test_gaps_show_as_na_with_the_reason_as_tooltip(aapl_report: ForensicReport, html: str) -> None:
    assert aapl_report.metrics is not None
    gap = aapl_report.metrics.annual("interest_coverage", 2026)
    assert gap.value is None and gap.note
    assert 'class="num missing"' in html
    assert ">n/a</td>" in html
    cells = [c for t in metric_tables(aapl_report) for row in t.rows for c in row.cells]
    missing = [c for c in cells if c.missing]
    assert missing and all(c.text == "n/a" and c.tooltip for c in missing)


def test_every_figure_carries_its_provenance(aapl_report: ForensicReport) -> None:
    assert aapl_report.metrics is not None
    value = aapl_report.metrics.latest("gross_margin")
    text = provenance(value)
    assert value.note and value.note in text
    for item in value.inputs:
        assert item.source.accession_number in text
        assert item.source.xbrl_concept in text


def test_html_shows_no_python_leftovers(html: str) -> None:
    for junk in ("None", "Decimal(", "StrictUndefined", "{{", "{%"):
        assert junk not in html, junk
    assert not re.search(r"(nan|inf)", html, re.IGNORECASE)


def test_markdown_is_complete_and_has_valid_tables(aapl_report: ForensicReport) -> None:
    md = render_markdown(aapl_report)
    assert md.startswith("# Apple Inc. (AAPL): forensic report")
    assert DISCLAIMER in md
    for heading in (
        "## Result: Worth a look",
        "## Forensic scores",
        "## Red flags in SEC filings",
        "## Insider activity (Form 4)",
        "## Key figures",
        "## Sources",
    ):
        assert heading in md
    for junk in ("None", "Decimal(", "{{", "{%"):
        assert junk not in md, junk
    # every table row has as many cells as its header
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| ---"):
            width = line.count("|")
            header = lines[i - 1]
            assert header.startswith("|") and header.count("|") == width
            j = i + 1
            while j < len(lines) and lines[j].startswith("|"):
                assert re.sub(r"\\\|", "", lines[j]).count("|") == width, lines[j]
                j += 1


def test_markdown_escapes_table_breaking_characters(aapl_report: ForensicReport) -> None:
    hostile = aapl_report.model_copy(
        update={
            "summary": aapl_report.summary.model_copy(
                update={
                    "areas": (
                        aapl_report.summary.areas[0].model_copy(update={"summary": "a|b\nc"}),
                    )
                }
            )
        }
    )
    md = render_markdown(hostile)
    assert "| a\\|b c |" in md


def test_json_roundtrips_without_loss(aapl_report: ForensicReport) -> None:
    text = render_json(aapl_report)
    assert text.endswith("\n")
    assert ForensicReport.model_validate_json(text) == aapl_report


def test_render_dispatch(aapl_report: ForensicReport) -> None:
    assert render(aapl_report, "html") == render_html(aapl_report)
    assert render(aapl_report, "md") == render_markdown(aapl_report)
    assert render(aapl_report, "json") == render_json(aapl_report)


def test_report_without_sections_still_renders(aapl_report: ForensicReport) -> None:
    empty = aapl_report.model_copy(
        update={
            "statements": None,
            "metrics": None,
            "scores": None,
            "red_flags": None,
            "insiders": None,
            "sources": (),
            "errors": {"statements": "boom", "scores": "needs statements"},
        }
    )
    for fmt in ("html", "md", "json"):
        out = render(empty, fmt)  # type: ignore[arg-type]
        assert "Apple Inc." in out
    html = render_html(empty)
    assert "Some parts of this report are missing" in html
    assert "Not available: boom." in html
    assert "No filings to list." in html
    parser = Balance()
    parser.feed(html)
    assert parser.errors == [] and parser.stack == []
    assert Light.GRAY.value in html


def test_every_ratio_metric_has_a_display_style() -> None:
    ratios = {s.name for s in ALL_SPECS if s.unit is Unit.RATIO}
    assert ratios <= set(METRIC_STYLE)
    assert set(METRIC_STYLE.values()) <= {"percent", "multiple", "number"}


def test_number_formats(aapl_report: ForensicReport) -> None:
    assert format_usd(Decimal("383285000000")) == "$383.3B"
    assert format_usd(Decimal("-1234567")) == "-$1.2M"
    assert format_usd(Decimal("1500")) == "$1.5K"
    assert format_usd(Decimal("42")) == "$42"
    assert format_usd(Decimal("2500000000000")) == "$2.5T"
    assert format_shares(Decimal("15115823000")) == "15.1B"
    assert format_shares(Decimal("950")) == "950"
    assert format_value(None) == "n/a"
    assert aapl_report.metrics is not None and aapl_report.statements is not None
    margin = aapl_report.metrics.latest("gross_margin")
    assert margin.value is not None
    assert format_value(margin, "gross_margin") == f"{float(margin.value) * 100:.1f}%"
    assert format_value(aapl_report.metrics.latest("current_ratio"), "current_ratio").endswith("x")
    assert format_value(aapl_report.statements.annual("eps_diluted", 2026)) == "$8.98"
    assert format_value(aapl_report.statements.annual("revenue", 2026)) == "$478.8B"


def micron_like(aapl_report: ForensicReport) -> ForensicReport:
    """Apple's report with one red Beneish screen after a +256 % revenue year (the Micron case)."""
    from stock_forensics.report.summary import summarize
    from tests.test_report_traffic import RED, all_green, with_growth, with_zones

    assert aapl_report.scores is not None and aapl_report.metrics is not None
    scores = with_zones(all_green(aapl_report.scores), beneish_m=RED["beneish_m"])
    metrics = with_growth(aapl_report.metrics, aapl_report.metrics.fiscal_years[-1], "2.56")
    summary = summarize(
        scores=scores,
        metrics=metrics,
        red_flags=aapl_report.red_flags,
        insiders=aapl_report.insiders,
        as_of=aapl_report.as_of,
        errors={},
    )
    return aapl_report.model_copy(update={"scores": scores, "metrics": metrics, "summary": summary})


def test_a_lone_red_screen_is_explained_not_shouted(aapl_report: ForensicReport) -> None:
    report = micron_like(aapl_report)
    assert report.summary.overall is Light.YELLOW
    html = render_html(report)
    assert 'class="verdict yellow"' in html
    assert "Alarm" not in html.split("Forensic scores</h2>")[0].split("</style>")[1]
    assert "one screen alone is not a verdict" in html
    assert "Revenue moved +256% in FY2026" in html
    assert "The model itself reads this as red." in html  # on the score card
    assert "Biggest driver" in html
    md = render_markdown(report)
    assert "## Result: Worth a look" in md and "In short:" in md
    assert "one screen alone is not a verdict" in md


def test_score_cards_show_what_counts_and_what_the_model_says(aapl_report: ForensicReport) -> None:
    from stock_forensics.report.render import score_views

    views = {v.name: v for v in score_views(micron_like(aapl_report))}
    assert (views["beneish_m"].light, views["beneish_m"].own_reading) == (Light.YELLOW, Light.RED)
    assert (views["altman_z"].light, views["altman_z"].own_reading) == (Light.GREEN, Light.GREEN)


def test_every_section_has_a_plain_words_line(aapl_report_market: ForensicReport) -> None:
    html = render_html(aapl_report_market)
    assert html.count("In plain words:") == 5
    md = render_markdown(aapl_report_market)
    assert md.count("In plain words:") >= 2


def test_every_market_ratio_has_a_display_style() -> None:
    from stock_forensics.market.indicators import SPECS as TREND
    from stock_forensics.market.technicals import SPECS as TECHNICALS
    from stock_forensics.market.valuation import SPECS as VALUATION

    ratios = {name for name, _, _, unit in VALUATION if unit is Unit.RATIO}
    ratios |= {name for name, _, _ in TECHNICALS}  # all technicals are ratios
    ratios |= {name for name, _, _ in TREND if name not in {"last_cross", "rsi_14w", "macd_w"}}
    assert ratios <= set(METRIC_STYLE), ratios - set(METRIC_STYLE)
