# Trading Holiday Schedule Automation

A Python batch pipeline that gathers, normalizes, validates, and publishes **2026 trading holiday schedules** for NASDAQ, NYSE, CME Globex, and an OPRA-related U.S. options calendar. The output is a single source-attributed CSV, with optional automatic publishing to **Google Drive and a native Google Sheet**.

The implementation distinguishes full-market holidays, early closes, and CME product-dependent holiday hours. It also distinguishes live retrieval from reviewed reference data when a source blocks automated access.

## Results

The verified 6 November 2026 run collected NASDAQ and NYSE live, then used explicitly permitted reference fallbacks for CME and the OCC calendar used as an OPRA proxy.

| Market | Records | Interpretation | Method in verified run |
|---|---:|---|---|
| NASDAQ | 12 | 10 full-day closures and 2 early closes | Live HTTP/HTML |
| NYSE | 12 | 10 full-day closures and 2 early closes | Live HTTP/HTML |
| CME Globex | 10 | Product-dependent holiday-hours windows | Reviewed 2026 reference fallback |
| OPRA-related options | 10 | OCC options-clearing holiday dates; **not an OPRA-issued schedule** | Reviewed OCC reference fallback |
| **Total** | **44** | 30 closures, 4 early closes, 10 holiday-hour events | Mixed live/reference |

The local run exported 44 validated records and successfully published both outputs through the Google APIs:

- [Published CSV on Google Drive](https://drive.google.com/file/d/1ep8SBMlPaQTo6koUOdl5D27gQRlrLAi8/view)
- [Published native Google Sheet](https://docs.google.com/spreadsheets/d/1cPXo5rnFn0mngogdZAz2wESUNscn7CppteWWO9waojU/edit)

Access to these files depends on their Google Drive sharing permissions.

## Data sources and scope

| Market | Reference |
|---|---|
| NASDAQ | [Nasdaq Trader holiday calendar](https://nasdaqtrader.com/Trader.aspx?id=Calendar) |
| NYSE | [NYSE hours and calendars](https://www.nyse.com/markets/hours-calendars) |
| CME | [CME Group trading hours](https://www.cmegroup.com/trading-hours.html) |
| OPRA proxy | [OCC Information Memo #57897](https://infomemo.theocc.com/infomemos?number=57897) |

**Important:** CME Globex has contract-specific holiday hours. A `HOLIDAY_HOURS` row is **not** a claim that the entire CME market is closed. OPRA is an options market-data plan, not an exchange; the OCC calendar is a documented **proxy** for common options holidays, not proof of OPRA's own feed operating schedule.

Each CSV record contains `exchange`, `date`, `holiday`, `status`, `close_time_local`, `timezone`, `window_start`, `window_end`, `source_url`, `source_basis`, and `notes`. Dates use ISO `YYYY-MM-DD`; U.S. equity early-close times are local Eastern Time.

## Repository structure

```text
task3/
├── main.py                    # CLI entry point
├── src/
│   ├── model.py               # Record schema and field validation
│   ├── scrapers.py            # Source collectors and parsers
│   ├── pipeline.py            # Coverage checks, fallback policy, 
│   └── publish.py             # Google OAuth + Drive/Sheets publication
├── tests/
│   ├── test_parsers.py        # Parsing, coverage and fallback tests
│   └── test_publish.py        # Publisher destination configuration 
├── reference/
│   └── holidays_2026.csv      # Reviewed 2026 reference snapshot
├── output/
│   ├── trading_holidays_2026.csv
│   └── validation_2026.json
├── .env.example
├── .gitignore
├── requirements.txt
├── requirements-browser.txt
├── README.md
└── technical_report.md
```

Generated output is rewritten on each successful collection. The `reference/` snapshot is retained separately so a reference run can be reproduced without network access.

## Setup

Use Python 3.11 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

CME browser collection is optional and requires Playwright/Chromium:

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

For Google publishing, enable the **Google Drive API** and **Google Sheets API** in one Google Cloud project, create a **Desktop app OAuth client**, and save its downloaded JSON as `credentials.json` in `task3/`. For a personal Gmail account, use an **External** OAuth audience with your account added as a test user if the application is in Testing mode.

Copy `.env.example` to `.env` and set the two IDs of the existing destinations:

```dotenv
SHEET_ID=your_google_sheet_id
CSV_FILE_ID=your_csv_file_id
```

The IDs select **which existing native Sheet and Drive CSV to update**. They are not authentication secrets and do not replace OAuth credentials. When both IDs are absent, the publisher instead finds or creates files by name in a `Trading Holidays` folder. Only one ID is rejected to avoid ambiguous destinations.

The first publish opens a browser consent flow and caches the authorization in `token.json`.

## Commands

Run the reviewed 2026 dataset without scraping:

```bash
python main.py --mode reference
```

Attempt all sources live (fails if any source cannot be retrieved):

```bash
python main.py --mode live
```

Collect live sources while **explicitly allowing** the reviewed CME and OCC reference records if their sites are inaccessible:

```bash
python main.py --mode live \
  --allow-cme-reference \
  --allow-opra-reference
```

Collect, validate, export, and **automatically update** both Google destinations:

```bash
python main.py --mode live \
  --allow-cme-reference \
  --allow-opra-reference \
  --publish
```

The fallback flags do not force reference data when live retrieval succeeds. When used, affected rows receive `OFFICIAL_REFERENCE_FALLBACK` (CME) or `OCC_PROXY_REFERENCE_FALLBACK` (OPRA proxy) in `source_basis`; the JSON summary records fallback counts. Publishing runs **only after dataset validation**. The program defaults to `--mode reference` if a mode is not specified.

Run offline unit tests:

```bash
python -m unittest discover -s tests -v
```

**12 offline tests pass** against the finalized implementation. They exercise parsers, reference validation, explicit source fallbacks, and Google destination-ID configuration; they do not substitute for testing remote services. The authenticated `--publish` run above was also completed successfully.

## Limitations and security

- The verified live run received HTTP **403** from CME, an HTTP/2 browser error on its fallback, and HTTP **403** from OCC. These were documented access failures, not evidence of missing holidays. Twenty records came from the explicitly selected 2026 reference snapshots.
- The supported data year is **2026**. The reviewed snapshot must be updated and the parsers verified before extending the pipeline to another year.
- NYSE/NASDAQ early closes describe the U.S. equities schedule; options sessions can differ. CME hours are product-specific. OCC is **not** an authoritative OPRA operational-hours source.
- This is a **batch script**, not a resident scheduler. To refresh it automatically at an interval, schedule the CLI command separately and monitor failures; Google testing-mode authorizations may require renewal.
- Never commit `.env`, `credentials.json`, `token.json`, or virtual environments. These are excluded by `.gitignore`. Do commit tests and the reviewed reference snapshot for reproducibility.

See [technical_report.md](technical_report.md) for the investigation, validation evidence, publishing flow, and operational trade-offs.
