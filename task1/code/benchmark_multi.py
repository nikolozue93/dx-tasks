#!/usr/bin/env python3
"""Small mixed-symbol dxFeed REST demo check; not a production load test.

Run six sequential and six parallel requests (two per symbol per level).
Requires benchmark_rest.py in the same directory.
"""

import argparse
import csv
import json
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmark_rest import measure

SYMBOLS = ("AAPL{=5m}", "TSLA{=5m}", "EUR/USD{=5m,price=bid}")
START = "20261104-150000Z"
END = "20261104-160000Z"


def good(row):
    return (row["http_status"] == 200 and row["api_status"] == "OK"
            and row["candle_count"] > 0 and not row["error"])


def stats(rows):
    usable = [r for r in rows if good(r)]
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    signatures = {symbol: sorted({r["fingerprint"] for r in usable
                                   if r["symbol"] == symbol and r["fingerprint"]})
                  for symbol in sorted({r["symbol"] for r in rows})}
    return {
        "requests": len(rows),
        "successful_with_candles": len(usable),
        "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "min_latency_ms": min(latencies) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "http_status_counts": dict(sorted(Counter(str(r["http_status"]) for r in rows).items())),
        "api_status_counts": dict(sorted(Counter(r["api_status"] for r in rows).items())),
        "candle_counts": sorted({r["candle_count"] for r in usable}),
        "unique_successful_fingerprints_by_symbol": {symbol: len(sigs) for symbol, sigs in signatures.items()},
        "consistent_within_each_symbol": (
            all(len(sigs) == 1 for sigs in signatures.values())
            if usable and all(any(r["symbol"] == symbol for r in usable)
                              for symbol in {r["symbol"] for r in rows}) else None
        ),
    }


def run(level, rounds, start, end, next_id):
    # One round contains exactly one request for each of the three symbols.
    tasks = [(symbol, next_id + idx) for idx, symbol in
             enumerate(SYMBOLS * rounds)]
    with ThreadPoolExecutor(max_workers=level) as executor:
        futures = [executor.submit(measure, symbol, req_id, level, start, end)
                   for symbol, req_id in tasks]
        results = [f.result() for f in futures]
    for (symbol, _), row in zip(tasks, results):
        row["symbol"] = symbol
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default="data/latency_test/multi_symbol")
    parser.add_argument("--pause", type=float, default=3.0,
                        help="Seconds between sequential and concurrent levels (min 2)")
    args = parser.parse_args()
    if args.pause < 2:
        parser.error("Keep a pause of at least 2 seconds on the public demo")
    print("Mixed-symbol historical REST check: 12 requests total")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Fixed UTC window: {START} to {END}")
    print("Stage 1: 6 sequential requests (2 per symbol)")
    print("Stage 2: 6 requests, up to 3 simultaneous (2 per symbol)")
    if args.dry_run:
        return

    all_rows = []
    for level in (1, 3):
        if all_rows:
            time.sleep(args.pause)
        rows = run(level, 2, START, END, len(all_rows) + 1)
        all_rows.extend(rows)
        s = stats(rows)
        print(f"Concurrency {level}: {s['successful_with_candles']}/{s['requests']} successful; "
              f"median {s['median_latency_ms']} ms; "
              f"API status counts {s['api_status_counts']}")
        if any(r["http_status"] in (429, 503) or r["api_status"] == "TIMED_OUT"
               for r in rows):
            print("Stopping: demo signaled throttling, unavailability, or source timeout.")
            break

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    fields = list(all_rows[0].keys())
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    report = {
        "source": "https://demo.dxfeed.com/webservice/rest/events.json",
        "fromTime": START,
        "toTime": END,
        "symbols": list(SYMBOLS),
        "stages": {
            str(level): {
                "overall": stats([r for r in all_rows if r["level"] == level]),
                "by_symbol": {symbol: stats([r for r in all_rows
                                            if r["level"] == level and r["symbol"] == symbol])
                              for symbol in SYMBOLS},
            }
            for level in (1, 3) if any(r["level"] == level for r in all_rows)
        },
        "overall": stats(all_rows),
        "notes": [
            "Small public-demo experiment; not a capacity or production load test.",
            "Latency includes client network and upstream waiting; no server-only processing metric.",
            "Two samples per symbol per level do not support a stable percentile or SLA claim.",
            "Fingerprints are checked within the same symbol; different instruments must not match.",
            "Only completed historical candle windows are queried; live freshness is not tested.",
        ],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {out / 'samples.csv'} and {out / 'report.json'}")


if __name__ == "__main__":
    main()
