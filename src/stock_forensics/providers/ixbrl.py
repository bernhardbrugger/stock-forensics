"""Read the numbers an inline-XBRL filing document prints.

Modern 10-K/10-Q documents mark every number with an ``ix:nonFraction`` tag that carries the
concept name, a scale (6 = millions) and a sign. Reading these gives the value exactly as the
document shows it, independent of the company-facts API.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_TAG = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
_ATTR = re.compile(r'(\w[\w:.-]*)="([^"]*)"')
_MARKUP = re.compile(r"<[^>]+>")


def inline_xbrl_values(html: str) -> dict[str, set[Decimal]]:
    """Map each concept (``"us-gaap:Assets"``) to the set of values printed for it."""
    found: dict[str, set[Decimal]] = {}
    for attrs_text, inner in _TAG.findall(html):
        attrs = dict(_ATTR.findall(attrs_text))
        name = attrs.get("name")
        text = _MARKUP.sub("", inner).replace(",", "").replace("\xa0", "").strip()
        if not name or not text:
            continue
        try:
            value = Decimal(text) * (Decimal(10) ** int(attrs.get("scale", "0")))
        except (InvalidOperation, ValueError):
            continue  # a dash or a word instead of a number
        if attrs.get("sign") == "-":
            value = -value
        found.setdefault(name, set()).add(value)
    return found
