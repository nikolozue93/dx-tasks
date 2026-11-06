# Task 3 — Technical Report: Trading Holiday Collection and Automation

**Evaluation date:** 6 November 2026  
**Target calendar:** 2026  
**Scope:** NASDAQ, NYSE, CME Globex, and OPRA-related U.S. options holidays

## 1. Objective and outcome

The assignment requires collecting holiday schedules for four market sources, cleaning them into a consistent dataset, and publishing the output to Google Drive as both a CSV file and a native Google Sheet. The implemented Python batch pipeline performs source collection, normalization, validation, CSV export, and OAuth-authenticated publishing.

**The verified end-to-end run completed successfully.** It produced **44 records**, updated an existing CSV in Google Drive, and updated an existing native Google Sheet. Two websites blocked automated access: the pipeline collected NASDAQ and NYSE live and used individually approved, source-attributed reference fallbacks for CME and the OCC data used as an OPRA proxy. Therefore, the result demonstrates successful **mixed live/reference automation**, **not** successful live scraping of all four sources.

## 2. Sources and collection strategy

| Market | Primary source | Collection method | Verified run |
|---|---|---|---|
| NASDAQ | [Nasdaq Trader calendar](https://nasdaqtrader.com/Trader.aspx?id=Calendar) | HTTP Requests + BeautifulSoup; 2026 holiday/status table | 12 live rows |
| NYSE | [NYSE hours and calendars](https://www.nyse.com/markets/hours-calendars) | HTTP Requests + BeautifulSoup; 2026 column, holiday notes and equity early-close rules | 12 live rows |
| CME | [CME Group trading hours](https://www.cmegroup.com/trading-hours.html) | HTTP first, then optional Playwright/Chromium, then opt-in reviewed fallback | 10 reference rows |
| OPRA-related options | [OCC Information Memo #57897](https://infomemo.theocc.com/infomemos?number=57897) | HTML/PDF text parser for OCC calendar; opt-in reviewed fallback | 10 OCC-reference proxy rows |

### NASDAQ and NYSE

Both live sources produced 12 records: **10 full-day closures** and **two 13:00 America/New_York equity early closes** (the day after Thanksgiving and Christmas Eve). The NYSE parser reads the year-specific column, cleans annotations such as footnote markers, and derives its 2026 early-close dates from the corresponding official holiday rules. The NASDAQ parser reads explicit market-status values.

### CME Globex

The CME website returned **HTTP 403** to a conventional request. The browser fallback failed with `net::ERR_HTTP2_PROTOCOL_ERROR` at `https://www.cmegroup.com/trading-hours.html`. Because `--allow-cme-reference` was explicitly supplied, the pipeline used the reviewed 2026 snapshot, marking all 10 rows `OFFICIAL_REFERENCE_FALLBACK`.

CME holiday announcements describe **trading-hours windows**, which can begin before and end after the canonical holiday date. Futures products do not all follow one universal closure schedule. Accordingly, the normalized status is `HOLIDAY_HOURS`, with `window_start` and `window_end` retained and the note that product-specific schedules must be consulted. The live CME parser, when accessible, cross-checks its parsed window against reviewed holiday dates rather than guessing a canonical date from an arbitrary range.

### OPRA and the OCC proxy

OPRA distributes consolidated U.S. listed-options market data; it is **not itself an exchange**. A directly issued OPRA 2026 holiday schedule was not verified for this project. Instead, the pipeline uses OCC's published **2026 options-clearing holiday calendar** as a proxy for common market non-trading days. This does **not** establish precise OPRA feed hours, early closes, or exceptional closures.

The OCC memo endpoint returned **HTTP 403** in the verified run. With `--allow-opra-reference`, the pipeline loaded the 10 reviewed proxy dates and tagged them `OCC_PROXY_REFERENCE_FALLBACK`. In a successful direct retrieval, the collector can parse either HTML text or a PDF response. The proxy limitation is retained in every row's source metadata.

## 3. Processing and validation

The project separates responsibilities across `src/scrapers.py` (collection/parsing), `src/model.py` (validated record), `src/pipeline.py` (coverage/fallback policy/export), and `src/publish.py` (Google API integration).

The common CSV schema is:

```text
exchange,date,holiday,status,close_time_local,timezone,
window_start,window_end,source_url,source_basis,notes
```

The parser normalizes dates to `YYYY-MM-DD` and holiday names to a common vocabulary. Market state is represented by three deliberately distinct values: `CLOSED`, `EARLY_CLOSE`, and `HOLIDAY_HOURS`. Dates, timezones, source URLs, CME date windows, and proxy/reference provenance are preserved.

Validation enforces known market labels, valid dates, status-dependent fields, a 2026 date range, no duplicate `(exchange, date, status)` rows, and the expected source coverage of 12 NASDAQ / 12 NYSE / 10 CME / 10 OPRA-proxy records. It rejects CME records marked as universal `CLOSED` and OPRA records incorrectly labeled as an official direct source. Any unapproved source failure, parsing error, or incomplete source coverage prevents the export-and-publish sequence from completing. Reference fallbacks must be explicitly enabled on the command line.

### Verified output

| Metric | Result |
|---|---:|
| Total records | **44** |
| NASDAQ | 12 |
| NYSE | 12 |
| CME | 10 |
| OPRA proxy | 10 |
| Full-day closure records | 30 |
| Early-close records | 4 |
| Holiday-hours records | 10 |
| Live-source records | 24 |
| Explicit reference fallback records | 20 |

For this run, `source_basis` distinguished 24 `OFFICIAL` live records, 10 `OFFICIAL_REFERENCE_FALLBACK` CME records, and 10 `OCC_PROXY_REFERENCE_FALLBACK` options-proxy records. The pipeline wrote `output/trading_holidays_2026.csv` and `output/validation_2026.json`; the latter records coverage, status breakdown, source-basis breakdown, fallback count, and interpretation notes.

## 4. Automated Google Drive / Sheets publishing

The `--publish` option runs **after** successful collection and validation. A single Google OAuth Desktop Application client requests access to the **Google Drive API** and **Google Sheets API**. The first run authorizes the user's account through the browser; `token.json` stores the local reusable authorization. Client credentials and token files are excluded from version control.

With both destination IDs set in `.env`, the publisher first verifies that `SHEET_ID` identifies a native Google spreadsheet and `CSV_FILE_ID` identifies a CSV file. It then:

1. Reads the validated local CSV and clears/writes the target native Sheet through the Sheets API.
2. Freezes and formats the header and resizes columns.
3. Reads back the Sheet to verify its header and expected row count.
4. Replaces the contents of the existing Drive CSV through the Drive API.
5. Prints both destination URLs only after the publishing operations complete.

When neither ID is configured, the publisher can locate or create destinations by name in a `Trading Holidays` folder. Using explicit IDs is preferable for a repeatable submission because it avoids duplicate files and ambiguous names. The source `.env.example` retains placeholders; real OAuth files and tokens remain local.

**Successful authenticated run:**

```bash
python main.py --mode live \
  --allow-cme-reference \
  --allow-opra-reference \
  --publish
```

The terminal confirmed successful validation and printed the two destinations:

- [Google Drive CSV](https://drive.google.com/file/d/1ep8SBMlPaQTo6koUOdl5D27gQRlrLAi8/view)
- [Native Google Sheet](https://docs.google.com/spreadsheets/d/1cPXo5rnFn0mngogdZAz2wESUNscn7CppteWWO9waojU/edit)

A subsequent Drive metadata read confirmed the CSV was updated, and the native spreadsheet's header and source attribution were independently read back. Google file access for reviewers is controlled by the owner's sharing settings.

## 5. Tests and reproducibility

The committed `unittest` suite contains **12 passing offline tests** when the latest parsing/fallback and publishing patches are combined. It covers:

- NASDAQ status and early-close parsing; NYSE year-column and shortened-session handling.
- CME holiday-window parsing and year-boundary handling.
- OCC text and HTML responses and source-basis labeling.
- Reference dataset totals and record validation.
- Individually opt-in CME and OCC fallbacks, including the case where both source requests fail.
- The `.env` publisher destination configuration: both IDs set, neither set, and incomplete configuration rejected.

Run the suite with:

```bash
python -m unittest discover -s tests -v
```

These tests use fixtures and mocks; they **do not** demonstrate live site connectivity or real Google authorization. The separate authenticated `--publish` execution provides the end-to-end evidence. The reviewed `reference/holidays_2026.csv` additionally permits deterministic offline reproduction using `python main.py --mode reference`.

## 6. Operational limitations and improvements

- **CME/OCC access:** HTTP 403 and browser HTTP/2 errors can prevent live collection. The opt-in reference approach preserves coverage and provenance but **does not refresh blocked sources automatically**. Future production use should include a supported source feed or manual reference review, with warnings surfaced to operators.
- **Calendar precision:** CME hours vary by product, and the OCC proxy does not verify OPRA-specific operating status. The dataset is suitable for calendar awareness and support investigation, not contract-level trade-routing decisions without further checks.
- **Year specificity:** The implemented and reviewed snapshot supports **2026 only**; changing the CLI year is not sufficient to create a validated calendar for later years.
- **Scheduling:** The script is a batch pipeline. Regular unattended execution requires a separate scheduler (such as cron or CI) plus failure alerts and periodic review of reference data. It does not implement monitoring or continuous background collection by itself.
- **OAuth lifecycle:** A testing-mode Google OAuth app may require reauthorization. Credentials must remain outside Git, and changing scopes or target ownership can require fresh consent.

## Conclusion

Task 3's practical data and publishing flow was demonstrated end-to-end: **44 validated 2026 records were exported locally and published by the Python script to an existing CSV and native Google Sheet**. The implementation is reproducible, has 12 passing offline tests, and explicitly exposes rather than conceals its two live-access limitations. The outstanding limitation is **live scraping completeness**, not the Google Drive/Sheets publishing functionality.
