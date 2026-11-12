"""Record real SEC data as fixtures for the red-flag tests (makes live requests).

Usage:
    SEC_USER_AGENT="stock-forensics you@example.com" \
        uv run python scripts/record_flag_fixtures.py

Writes to tests/fixtures/flags/:
  submissions_<key>.json   the filing index of a company, cut down to the forms the checks use
  older/<page>.json        two older index pages of Walmart (to test merging pages)
  excerpts/<accession>.txt verbatim text from the filing that a check reads: all "substantial
                           doubt" sentences of a 10-K, the Item 4.01 or 4.02 section of an 8-K,
                           the explanatory note of a 10-K/A
  manifest.json            what each excerpt is and where it comes from

Nothing is invented: the excerpts are cut from the real documents. At the end the flags found
from the excerpts are compared with the flags of a live run; any difference is reported.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from stock_forensics.flags import analyze_filings, check_red_flags, years_before
from stock_forensics.flags.amendments import explanatory_text
from stock_forensics.flags.auditor import HEADING as HEADING_401
from stock_forensics.flags.going_concern import DOUBT
from stock_forensics.flags.restatement import HEADING as HEADING_402
from stock_forensics.flags.text import html_to_text, section_after, sentences_matching
from stock_forensics.providers.sec_edgar import SUBMISSIONS_URL, SecEdgarClient
from stock_forensics.providers.sec_models import Filing

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "flags"
AS_OF = date(2026, 11, 12)
KEEP_FROM = date(2018, 1, 1)
KEEP_FORMS = {"10-K", "10-K/A", "10-KT", "10-Q", "NT 10-K", "NT 10-Q", "8-K", "8-K/A"}
COLUMNS = ("accessionNumber", "filingDate", "reportDate", "form", "items", "primaryDocument")

# key -> (cik, years to examine)
COMPANIES: dict[str, tuple[int, int]] = {
    "smci": (1375365, 6),
    "khc": (1637459, 8),
    "wework": (1813756, 6),
    "tupperware": (1008654, 6),
    "bbby": (886158, 6),
    "spirit": (1498710, 6),
    "aapl": (320193, 6),
    "wmt": (104169, 6),
}
# Companies whose 10-K text is recorded as a negative example (no going-concern doubt stated).
NEGATIVES = {
    "duos": (
        1396536,
        "https://www.sec.gov/Archives/edgar/data/1396536/000107997326000405/duos_10k-123125.htm",
    ),
    "medinotec": (
        1931055,
        "https://www.sec.gov/Archives/edgar/data/1931055/000166357726000175/mdnc_10k022826.htm",
    ),
    "monopar": (
        1645469,
        "https://www.sec.gov/Archives/edgar/data/1645469/000143774926010841/mnpr20251231c_10ka.htm",
    ),
}


def keep_row(columns: dict[str, list[Any]], i: int) -> bool:
    form = columns["form"][i]
    if form not in KEEP_FORMS or columns["filingDate"][i] < KEEP_FROM.isoformat():
        return False
    if form in ("8-K", "8-K/A"):
        items = columns["items"][i] or ""
        return "4.01" in items or "4.02" in items
    return True


def trim_columns(columns: dict[str, list[Any]]) -> dict[str, list[Any]]:
    rows = [i for i in range(len(columns["accessionNumber"])) if keep_row(columns, i)]
    return {name: [columns[name][i] for i in rows] for name in COLUMNS}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def excerpt_for(filing: Filing, text: str) -> tuple[str, str] | None:
    """The part of the document a check reads, and its kind; ``None`` if no check reads it."""
    if filing.form in ("10-K", "10-KT", "10-K405"):
        sentences = sentences_matching(text, DOUBT)
        return ("\n\n".join(sentences), "going_concern") if sentences else None
    if filing.form in ("8-K", "8-K/A") and "4.01" in filing.items:
        return section_after(text, HEADING_401, 2500), "auditor_change"
    if filing.form in ("8-K", "8-K/A") and "4.02" in filing.items:
        return section_after(text, HEADING_402, 2500), "restatement"
    if filing.form in ("10-K/A", "10-KT/A"):
        return explanatory_text(text), "amendment"
    return None


def main() -> None:
    manifest: dict[str, dict[str, str]] = {}
    histories = {}
    with SecEdgarClient() as client:
        for key, (cik, years) in COMPANIES.items():
            raw = json.loads(
                client._fetch(  # noqa: SLF001 - raw index wanted here
                    SUBMISSIONS_URL.format(cik=f"{cik:010d}"), allow_cache=False
                ).body
            )
            raw["filings"]["recent"] = trim_columns(raw["filings"]["recent"])
            write_json(OUT / f"submissions_{key}.json", raw)
            print(f"{key}: {len(raw['filings']['recent']['accessionNumber'])} rows")
            histories[key] = client.get_filing_history(cik, years_before(AS_OF, years))

            # Walmart: keep the older index pages as well (merge test)
            if key == "wmt":
                for page in raw["filings"]["files"]:
                    page_raw = json.loads(
                        client._fetch(  # noqa: SLF001
                            f"https://data.sec.gov/submissions/{page['name']}", allow_cache=False
                        ).body
                    )
                    write_json(OUT / "older" / page["name"], trim_columns(page_raw))

        # Excerpts of the documents that the checks read
        since_by_key = {k: years_before(AS_OF, y) for k, (_, y) in COMPANIES.items()}
        for key, history in histories.items():
            for filing in history.filings:
                if filing.filing_date < since_by_key[key] or filing.document_url is None:
                    continue
                relevant = filing.form in ("10-K", "10-KT", "10-K/A", "10-KT/A") or (
                    filing.form in ("8-K", "8-K/A")
                    and ("4.01" in filing.items or "4.02" in filing.items)
                )
                if not relevant:
                    continue
                text = html_to_text(client.get_document(filing.document_url))
                found = excerpt_for(filing, text)
                if found is None:
                    continue
                excerpt, kind = found
                write_text(OUT / "excerpts" / f"{filing.accession_number}.txt", excerpt)
                manifest[filing.accession_number] = {
                    "company": key,
                    "form": filing.form,
                    "filing_date": filing.filing_date.isoformat(),
                    "kind": kind,
                    "url": filing.document_url,
                }

        for key, (_cik, url) in NEGATIVES.items():
            text = html_to_text(client.get_document(url))
            sentences = sentences_matching(text, DOUBT)
            accession = re.search(r"/(\d{18})/", url)
            assert accession is not None
            number = accession.group(1)
            accn = f"{number[:10]}-{number[10:12]}-{number[12:]}"
            write_text(OUT / "excerpts" / f"{accn}.txt", "\n\n".join(sentences))
            manifest[accn] = {
                "company": key,
                "form": "10-K",
                "filing_date": "",
                "kind": "going_concern_negative",
                "url": url,
            }
        write_json(OUT / "manifest.json", manifest)

        # Consistency: the flags found from the excerpts must equal the flags of a live run.
        excerpts = {
            a: (OUT / "excerpts" / f"{a}.txt").read_text(encoding="utf-8") for a in manifest
        }
        problems = 0
        for key, (cik, years) in COMPANIES.items():
            since = years_before(AS_OF, years)

            def read(filing: Filing) -> str | None:
                text = excerpts.get(filing.accession_number)
                if text is not None:
                    return text
                return "" if filing.form in ("10-K", "10-KT") else None

            offline = analyze_filings(histories[key], read, as_of=AS_OF, since=since)
            live = check_red_flags(client, cik, years=years, as_of=AS_OF)
            a = [(f.code, f.accession_number) for f in offline.flags]
            b = [(f.code, f.accession_number) for f in live.flags]
            if a != b:
                problems += 1
                print(f"MISMATCH for {key}:\n  excerpts: {a}\n  live:     {b}")
        print(
            "excerpts reproduce the live flags for all companies"
            if not problems
            else f"{problems} mismatch(es)"
        )


if __name__ == "__main__":
    main()
