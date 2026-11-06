"""Collection, normalization and reproducible local exports."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import requests

from .model import FIELDS, Holiday
from .scrapers import scrape_nasdaq, scrape_nyse, scrape_cme, scrape_opra_proxy

REQUIRED_COUNTS = {"NASDAQ": 12, "NYSE": 12, "CME": 10, "OPRA": 10}


def validate_dataset(rows: list[Holiday], year: int) -> list[Holiday]:
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        row.validate()
        if not row.date.startswith(f"{year}-"):
            raise ValueError(f"Out-of-year holiday: {row}")
        key = (row.exchange, row.date, row.status)
        if key in seen:
            raise ValueError(f"Duplicate market-date-status: {key}")
        seen.add(key)
    counts = Counter(r.exchange for r in rows)
    if year == 2026 and counts != Counter(REQUIRED_COUNTS):
        raise ValueError(f"Unexpected source coverage: {dict(counts)}; expected {REQUIRED_COUNTS}")
    return sorted(rows, key=lambda r: (r.exchange, r.date, r.status))


def reference_csv(path: Path) -> list[Holiday]:
    with path.open(newline="", encoding="utf-8") as file:
        return [Holiday(**row) for row in csv.DictReader(file)]


def collect_live(
    year: int,
    baseline: list[Holiday],
    allow_cme_reference: bool = False,
    allow_opra_reference: bool = False,
) -> list[Holiday]:
    """Collect each source and validate the complete dataset before export.

    Reference fallbacks require explicit opt-in and are attributed per row.
    A parsing failure or any unapproved source failure stops collection.
    """
    if year != 2026:
        raise ValueError("Only the verified 2026 calendar is supported")

    anchors = {
        row.holiday: row.date
        for row in baseline
        if row.exchange == "CME"
    }

    nasdaq_rows = scrape_nasdaq(year)
    print(f"NASDAQ: {len(nasdaq_rows)} live records")

    nyse_rows = scrape_nyse(year)
    print(f"NYSE: {len(nyse_rows)} live records")

    try:
        cme_rows = scrape_cme(year, anchors)
        print(f"CME: {len(cme_rows)} live records")
    except RuntimeError as error:
        if not allow_cme_reference:
            raise

        print(f"WARNING: {error}")
        print("WARNING: Using the reviewed 2026 CME reference snapshot.")
        cme_rows = [
            replace(
                row,
                source_basis="OFFICIAL_REFERENCE_FALLBACK",
                notes=(
                    f"{row.notes}; Reference snapshot used because live "
                    "CME site was inaccessible"
                ),
            )
            for row in baseline
            if row.exchange == "CME"
        ]
        if not cme_rows:
            raise ValueError("No reviewed CME reference records available") from error

    try:
        opra_rows = scrape_opra_proxy(year)
        print(f"OPRA (OCC proxy): {len(opra_rows)} live records")
    except requests.RequestException as error:
        if not allow_opra_reference:
            raise RuntimeError(
                "Unable to fetch OCC Information Memo #57897. "
                "To use the reviewed 2026 options-calendar reference instead, "
                "pass --allow-opra-reference. "
                f"Original error: {error}"
            ) from error

        print(f"WARNING: OCC memo could not be fetched: {error}")
        print("WARNING: Using reviewed 2026 OCC calendar as an OPRA proxy.")

        opra_rows = [
            replace(
                row,
                source_basis="OCC_PROXY_REFERENCE_FALLBACK",
                notes=(
                    f"{row.notes}; Reviewed 2026 reference snapshot "
                    "used because the OCC memo was inaccessible"
                ),
            )
            for row in baseline
            if row.exchange == "OPRA"
            and row.source_basis == "OCC_PROXY_NOT_OPRA_OFFICIAL"
        ]
        if not opra_rows:
            raise ValueError(
                "No verified OCC-proxy reference records were available"
            ) from error

    rows = nasdaq_rows + nyse_rows + cme_rows + opra_rows
    return validate_dataset(rows, year)


def export(rows: list[Holiday], out: Path, year: int) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"trading_holidays_{year}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(row.row() for row in rows)
    summary = {
        "year": year,
        "total_events": len(rows),
        "counts": dict(Counter(row.exchange for row in rows)),
        "status_counts": dict(Counter(row.status for row in rows)),
        "source_basis_counts": dict(Counter(row.source_basis for row in rows)),
        "opra_proxy_events": sum(
            row.exchange == "OPRA" and row.source_basis.startswith("OCC_PROXY")
            for row in rows
        ),
        "reference_fallback_events": sum(
            row.source_basis in {
                "OFFICIAL_REFERENCE_FALLBACK",
                "OCC_PROXY_REFERENCE_FALLBACK",
            }
            for row in rows
        ),
        "notes": [
            "CME rows are product-specific holiday windows, not confirmed full-session closures.",
            "OPRA rows are OCC-clearing-calendar proxy dates, not an OPRA-issued 2026 schedule.",
        ],
    }
    summary_path = out / f"validation_{year}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return csv_path, summary_path
