"""The forensic report: one pipeline, one report object, three output formats.

``analyze("AAPL", client=...)`` runs every check and returns a :class:`ForensicReport`;
``render(report, "html" | "md" | "json")`` turns it into a single self-contained file.
See ``docs/report.md``.
"""

from __future__ import annotations

from stock_forensics.report.models import (
    DISCLAIMER,
    AreaLight,
    ForensicReport,
    Light,
    Reason,
    SourceEntry,
    Summary,
)
from stock_forensics.report.pipeline import analyze
from stock_forensics.report.render import render, render_html, render_json, render_markdown

__all__ = [
    "DISCLAIMER",
    "AreaLight",
    "ForensicReport",
    "Light",
    "Reason",
    "SourceEntry",
    "Summary",
    "analyze",
    "render",
    "render_html",
    "render_json",
    "render_markdown",
]
