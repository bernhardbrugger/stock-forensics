# Red flags from SEC filings

Code: `src/stock_forensics/flags/` (`going_concern.py`, `auditor.py`, `restatement.py`, `late_filing.py`, `amendments.py`, `text.py`, `models.py`). These are warning signs that are not in the numbers but in the filings.

```python
from stock_forensics.flags import check_red_flags
from stock_forensics.providers.sec_edgar import SecEdgarClient

with SecEdgarClient() as client:
    result = check_red_flags(client, cik, years=5)

for flag in result.flags:  # newest first
    print(flag.filing_date, flag.severity, flag.code, flag.form, flag.document_url)
    print("  ", flag.detail)
    for sentence in flag.evidence:  # quoted from the filing
        print("   >", sentence)
for check in result.coverage:  # what was examined, and what was not
    print(check.kind, check.examined, check.flagged, check.notes)
```

`analyze_filings(submissions, read_text, as_of=..., since=...)` does the same on an already loaded filing history with an injectable document reader (used by the tests).

Every `RedFlag` has `kind`, `code`, `severity` (`high`, `medium`, `low`), `title`, `detail`, `filing_date`, `form`, `period`, `accession_number`, **`document_url` (link to the original document)**, `evidence` (quoted sentences) and a `source` with the accession number. `RedFlags.coverage` has one entry per check: filings examined, flags found, and notes about what could not be checked. "No flag" can therefore be told apart from "not looked at". The traffic light of the report (step 8) is derived from the severities.

## The checks

| Check | Trigger | Codes (severity) |
|---|---|---|
| **Auditor change** | 8-K or 8-K/A with Item 4.01. The text says who ended the relationship. | `auditor_resigned` (high), `auditor_declined_to_stand` (medium), `auditor_changed` (medium, reason not stated), `auditor_dismissed` (low), `auditor_engaged` (low, new auditor appointed) |
| **Restatement** | 8-K or 8-K/A with Item 4.02 (non-reliance on previously issued financial statements). The flag comes from the item number; the text adds the quoted reason. | `non_reliance` (high) |
| **Late filing** | NT 10-K (medium) or NT 10-Q (low). The detail tells how many days later the 10-K or 10-Q followed, or that none was found. | `late_10k`, `late_10q` |
| **Going concern** | The 10-K text states substantial doubt (ASC 205-40). | `going_concern_exists` (high), `going_concern_alleviated` (medium) |
| **Amendments** | 10-K/A whose explanatory note restates or corrects the financial statements (high), any other substantive amendment (low), and two or more substantive amendments in the period (medium, `frequent_amendments`). Routine Part III amendments are not flagged. | `amendment_restatement`, `amendment_other`, `amendment_unclassified`, `frequent_amendments` |

The default period is the last five years (`years=`). Filings are taken from the SEC index; for companies with many filings `get_filing_history` loads the older index pages that the period needs (Walmart's `filings.recent` covers only about three and a half years).

### Going concern: how it is detected and why

Three ways were tried on real filings:

- **Searching the text of the 10-K** (used). All four real cases (Bed Bath & Beyond, WeWork, Spirit Airlines, Tupperware) were found with the decisive sentences, and in the five control companies (Apple, Microsoft, Walmart, Kraft Heinz, Super Micro) "going concern" does not appear at all. It needs only official archive URLs and is testable offline.
- **EDGAR full-text search** (`efts.sec.gov`, not used). It worked with our User-Agent and found the same 10-Ks, but it is not documented by the SEC (the details come from third-party descriptions such as [this analysis](https://orthogonal.info/sec-edgar-full-text-search-api-efts-python/)), it can change without notice, and a hit only says that both phrases occur somewhere in the file.
- **XBRL data** (not possible). The company facts hold numbers only. The auditor tags that the SEC requires since fiscal 2021 (`AuditorName`, see [Deloitte DART](https://dart.deloitte.com/USDART/home/news/all-news/2022/jan/new-xbrl-requirement)) are text, and no going-concern tag was found.

Each sentence containing "substantial doubt" is classified; only statements of doubt are flagged:

- **exists**: "there is substantial doubt", "we have concluded that substantial doubt exists", the auditor's "recurring losses ... that raise substantial doubt"
- **alleviated**: doubt was raised, but management's plans removed or mitigate it ("initially raised substantial doubt", "plans to alleviate")
- **not flagged**: negations ("there is no substantial doubt"), conditionals ("could raise substantial doubt"), the evaluation framework ("evaluate whether there are conditions that raise ...") and headings

Real filings of small companies that mention the phrase only in these ways (Duos Technologies, Medinotec, Monopar) are not flagged. The flag quotes up to three sentences, preferring those that name the going-concern conclusion.

### Auditor change and amendment texts

The Item 4.01 text is read from the heading on ("Item 4.01 Change in Registrant's Certifying Accountant"). A resignation outranks everything else in the text; then "declined to stand for re-appointment" or "will not seek to be retained"; then a dismissal or replacement; then the appointment of a new auditor. The follow-up 8-K that reports the successor is flagged as `auditor_engaged` (low); if it repeats the earlier event it can be flagged again as a decline or dismissal.

For a 10-K/A the "Explanatory Note" is read (the first three occurrences, 8000 characters each, because the note can follow a table of contents or a long description of a merger). Restatement wording is `restate...`, an error or misstatement that concerns the financial statements or a previously issued report, or "should no longer be relied on". Documents named "restated" (an "Amended and Restated Certificate of Incorporation") and a bare correction of the cover page do not count.

## Limitations

- Only the **main document** of each filing is read, not its exhibits. A going-concern statement that is only in an exhibit (companies that put their financial statements in Exhibit 13) is not seen. The coverage says so.
- The text patterns are for US English filings and can miss unusual wording. The flags quote their evidence so that a reader can check each one.
- A company that never filed a late report shows the NT notice with "no matching report was found". The SEC index sometimes names the filing date as the period of an NT form; then the period of the next report is shown.
- Severities are a judgement: a resignation of the auditor weighs more than a dismissal, a late 10-K more than a late 10-Q. They are a starting point for the traffic light, not a verdict.
- A flag is a reason to read the filing, not proof of wrongdoing.

## Tests and fixtures

- `tests/test_flags_text_and_going_concern.py`, `tests/test_flags_detectors.py`: every check on real excerpts and on hand-made variants (negations, conditionals, company names with periods, resignation vs. dismissal, cover-page fix vs. restatement, routine amendments, unreliable NT periods, unreadable documents).
- `tests/test_flags_cases.py`: the flags of Super Micro, Kraft Heinz, WeWork, Tupperware, Bed Bath & Beyond and Spirit Airlines, end to end, with Apple and Walmart as controls without any flag, and the full path through the client with a fake SEC server.
- `tests/test_filing_history.py`: `get_filing_history` with Walmart's recorded index pages.
- `tests/fixtures/flags/`: real, trimmed filing indexes and verbatim excerpts (the "substantial doubt" sentences of 10-Ks, the Item 4.01 and 4.02 sections of 8-Ks, the explanatory notes of 10-K/As), recorded by `scripts/record_flag_fixtures.py`. The script also checks that the flags found from the excerpts equal those of a live run.

Live check when this was written (2026-11-12): 43 flags for the six cases, none for Apple and Walmart, and the document link of every flag type of every company opens the original filing.
