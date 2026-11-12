"""Turn filing documents into plain text and pick out the sentences around a phrase."""

from __future__ import annotations

import html
import re

_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
MAX_SENTENCE = 400  # characters kept on each side of a match


def html_to_text(raw: bytes | str) -> str:
    """Plain text of an HTML or text filing: no tags, entities decoded, one space between words."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = _SCRIPT.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return _SPACE.sub(" ", text).strip()


def sentences_matching(text: str, pattern: re.Pattern[str], limit: int = MAX_SENTENCE) -> list[str]:
    """The distinct sentences that contain a match, each cut to a readable length."""
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        start = text.rfind(". ", 0, match.start())
        start = start + 2 if start >= 0 else 0
        start = max(start, match.start() - limit)
        end = text.find(". ", match.end())
        end = end + 1 if end >= 0 else len(text)
        end = min(end, match.end() + limit)
        sentence = text[start:end].strip()
        if sentence and sentence not in seen:
            seen.add(sentence)
            found.append(sentence)
    return found


def section_after(text: str, heading: re.Pattern[str], length: int = 6000) -> str:
    """The text from the first match of ``heading`` on (or the whole text if there is none).

    The heading pattern must be specific (for example "Item 4.01 Changes in Registrant's
    Certifying Accountant"), because the same item number is also quoted in running text.
    """
    match = heading.search(text)
    if match is None:
        return text
    return text[match.start() : match.start() + length]
