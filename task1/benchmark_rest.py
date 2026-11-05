#!/usr/bin/env python3
"""Small, bounded dxFeed REST-demo latency experiment (NOT a load test)."""

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://demo.dxfeed.com/webservice/rest/events.json"
MAX_CONCURRENCY = 4
MAX_TOTAL = 30


def measure(symbol, request_id, level, start_time, end_time):
    params = {"events": "Candle", "symbols": symbol,
              "fromTime": start_time, "toTime": end_time,
              "timeout": "5"}
    started = datetime.now(timezone.utc).isoformat()
    tick = time.perf_counter()
    row = {"timestamp_utc": started, "level": level, "request_id": request_id,
           "latency_ms": None, "http_status": None, "api_status": "", "candle_count": 0,
           "fingerprint": "", "response_bytes": 0, "error": ""}
    try:
        response = requests.get(BASE, params=params, timeout=(5, 12),
                                headers={"User-Agent": "dxFeed-candidate-assessment-bounded-test/1.0"})
        row["latency_ms"] = round((time.perf_counter() - tick) * 1000, 1)
        row["http_status"] = response.status_code
        row["response_bytes"] = len(response.content)
        response.raise_for_status()
        payload = response.json()
        row["api_status"] = str(payload.get("status", "MISSING"))
        candle_section = payload.get("Candle", {})
        candles = candle_section.get(symbol, []) if isinstance(candle_section, dict) else []
        if not isinstance(candles, list):
            raise ValueError("Unexpected Candle response shape")
        row["candle_count"] = len(candles)
        if row["api_status"] == "OK" and candles:
            stable_fields = ("time", "open", "high", "low", "close", "volume")
            stable = [tuple(c.get(k) for k in stable_fields) for c in candles]
            stable.sort(key=lambda r: r[0])
            row["fingerprint"] = hashlib.sha256(json.dumps(stable, separators=(",", ":")).encode()).hexdigest()[:16]
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        row["latency_ms"] = round((time.perf_counter() - tick) * 1000, 1)
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def percentile(values, p):
    if not values:
        return None
    x = sorted(values)
    pos = (len(x) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(x[lo] + (x[hi] - x[lo]) * (pos - lo), 1)


def summarize(rows):
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    ok = [r for r in rows if r["http_status"] == 200 and r["api_status"] == "OK" and r["candle_count"] > 0]
    signatures = sorted({r["fingerprint"] for r in ok if r["fingerprint"]})
    return {"requests": len(rows), "successes_with_candles": len(ok),
            "other_responses": len(rows) - len(ok),
            "latency_ms_mean": round(statistics.mean(latencies), 1) if latencies else None,
            "latency_ms_p50": percentile(latencies, .5),
            "latency_ms_p95": percentile(latencies, .95),
            "unique_successful_response_signatures": len(signatures),
            "all_successful_responses_identical": len(signatures) == 1 if ok else None,
            "api_status_counts": {key: sum(r["api_status"] == key for r in rows)
                                  for key in sorted({r["api_status"] for r in rows})},
            "http_status_counts": {str(key): sum(r["http_status"] == key for r in rows)
                                   for key in sorted({r["http_status"] for r in rows}, key=lambda x: str(x))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL{=5m}")
    parser.add_argument("--start", default="20261104-150000Z")
    parser.add_argument("--end", default="20261104-160000Z")
    parser.add_argument("--levels", default="1,2,4", help="Comma-separated simultaneous request counts")
    parser.add_argument("--requests-per-level", type=int, default=4)
    parser.add_argument("--pause", type=float, default=2.0, help="Seconds between levels")
    parser.add_argument("--output", default="data/latency_test")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        levels = [int(n.strip()) for n in args.levels.split(",")]
    except ValueError:
        parser.error("--levels must contain integers")
    if not levels or any(n < 1 or n > MAX_CONCURRENCY for n in levels):
        parser.error(f"Concurrency must be between 1 and {MAX_CONCURRENCY}")
    if args.requests_per_level < 1 or args.requests_per_level * len(levels) > MAX_TOTAL:
        parser.error(f"Request count must be between 1 and {MAX_TOTAL} total")
    if args.pause < 1.0:
        parser.error("Please keep at least a one-second pause between levels")
    print(f"Bounded REST experiment: {args.symbol}, levels={levels}, "
          f"{args.requests_per_level} requests each ({len(levels)*args.requests_per_level} total)")
    if args.dry_run:
        return
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    # No automatic retries and no unbounded concurrency.
    for i, level in enumerate(levels):
        if i:
            time.sleep(args.pause)
        print(f"Concurrency {level}: sending {args.requests_per_level} requests")
        with ThreadPoolExecutor(max_workers=level) as pool:
            futures = [pool.submit(measure, args.symbol, len(rows) + j + 1, level, args.start, args.end)
                       for j in range(args.requests_per_level)]
            batch = [f.result() for f in futures]
        rows.extend(batch)
        info = summarize(batch)
        print(f"  OK with candles: {info['successes_with_candles']}/{len(batch)}; "
              f"median {info['latency_ms_p50']}ms; other: {info['other_responses']}")
        # Public demo: stop rather than hammer a service signaling overload.
        if any(r["http_status"] in (429, 503) or r["api_status"] == "TIMED_OUT" for r in batch):
            print("Stopping after service indicated throttling, unavailability, or data timeout.")
            break
    with (output / "samples.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    report = {"source": BASE, "symbol": args.symbol, "fromTime": args.start,
              "toTime": args.end, "level_stats": {str(n): summarize([r for r in rows if r["level"] == n])
                                                      for n in levels if any(r["level"] == n for r in rows)},
              "overall": summarize(rows), "notes": [
                  "A bounded public-demo experiment, not a capacity or production load test.",
                  "P95 with only a few samples is descriptive and not an SLA estimate.",
                  "No observed 429 responses does not establish absence of rate limits."]}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {output / 'samples.csv'} and {output / 'report.json'}")


if __name__ == "__main__":
    main()
