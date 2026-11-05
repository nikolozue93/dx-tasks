#!/usr/bin/env python3
"""Collect and compare dxFeed REST candles without assuming the data is complete."""
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

BASE_URL = "https://demo.dxfeed.com/webservice/rest/events.json"
INTERVALS = {"1m": ("m", 60), "5m": ("5m", 300), "1h": ("h", 3600)}
HEADERS = ["period", "symbol", "utc_time", "time_ms", "open", "high", "low", "close", "volume", "count"]


def dt_from_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def parse_time(date, value):
    return datetime.fromisoformat(f"{date}T{value}:00+00:00")


def fmt_api_time(dt):
    return dt.strftime("%Y%m%d-%H%M%SZ")


def decode_candles(payload, expected_symbol):
    if payload.get("status") != "OK":
        raise ValueError(f"REST API status is not OK: {payload.get('status')!r}")
    candles_by_symbol = payload.get("Candle", {})
    if not isinstance(candles_by_symbol, dict):
        raise ValueError("Missing Candle object")
    values = candles_by_symbol.get(expected_symbol, [])
    if not isinstance(values, list):
        raise ValueError("Candle result was not a list")
    result = []
    for item in values:
        try:
            row = {"symbol": item["eventSymbol"], "time_ms": int(item["time"])}
            for key in ["open", "high", "low", "close", "volume"]:
                row[key] = Decimal(str(item[key]))
            row["count"] = int(item.get("count", 0))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError(f"Malformed candle: {item!r}") from exc
        if row["symbol"] != expected_symbol:
            raise ValueError(f"Unexpected event symbol: {row['symbol']}")
        result.append(row)
    return sorted(result, key=lambda row: row["time_ms"])


def write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def make_rows(candles, period):
    return [
        {
            "period": period,
            "symbol": c["symbol"],
            "utc_time": dt_from_ms(c["time_ms"]).isoformat(),
            "time_ms": c["time_ms"],
            **{k: str(c[k]) for k in ["open", "high", "low", "close", "volume"]},
            "count": c["count"],
        }
        for c in candles
    ]


def quality(candles, seconds, begin_ms, end_ms):
    in_window = [c for c in candles if begin_ms <= c["time_ms"] < end_ms]
    times = [c["time_ms"] for c in in_window]
    dupes = len(times) - len(set(times))
    invalid_ohlc = sum(not (c["low"] <= c["open"] <= c["high"]
                             and c["low"] <= c["close"] <= c["high"])
                       for c in in_window)
    # Valid on a fully trading, aligned hour. NOT a universal missing-market-data test.
    expected = list(range(begin_ms, end_ms, seconds * 1000))
    missing = sorted(set(expected).difference(times))
    return {"in_window": len(in_window), "at_end_boundary": sum(c["time_ms"] == end_ms for c in candles),
            "duplicates": dupes, "invalid_ohlc": invalid_ohlc,
            "missing_on_time_grid": len(missing),
            "first_missing_utc": [dt_from_ms(x).isoformat() for x in missing[:5]]}


def bucket_ms(timestamp_ms, seconds):
    width = seconds * 1000
    return (timestamp_ms // width) * width


def aggregate(children):
    children = sorted(children, key=lambda r: r["time_ms"])
    return {"open": children[0]["open"], "high": max(x["high"] for x in children),
            "low": min(x["low"] for x in children), "close": children[-1]["close"],
            "volume": sum((x["volume"] for x in children), Decimal("0"))}


def compare(lower, higher, higher_seconds, lower_seconds, begin_ms, end_ms):
    groups = defaultdict(list)
    for c in lower:
        if begin_ms <= c["time_ms"] < end_ms:
            groups[bucket_ms(c["time_ms"], higher_seconds)].append(c)
    higher_by_time = {c["time_ms"]: c for c in higher}
    output = []
    for start in range(begin_ms, end_ms, higher_seconds * 1000):
        children = groups.get(start, [])
        expected_count = higher_seconds // lower_seconds
        child_times = {c["time_ms"] for c in children}
        required_times = {start + j * lower_seconds * 1000 for j in range(expected_count)}
        row = {"utc_time": dt_from_ms(start).isoformat(), "expected_child_candles": expected_count,
               "actual_child_candles": len(children), "status": "", "different_fields": "",
               "expected_open": "", "actual_open": "", "expected_high": "", "actual_high": "",
               "expected_low": "", "actual_low": "", "expected_close": "", "actual_close": "",
               "expected_volume": "", "actual_volume": ""}
        if child_times != required_times:
            row["status"] = "INCOMPLETE_LOWER_INTERVAL"
        elif start not in higher_by_time:
            row["status"] = "MISSING_HIGHER_INTERVAL"
        else:
            expected = aggregate(children)
            actual = higher_by_time[start]
            different = []
            for field in ["open", "high", "low", "close", "volume"]:
                row[f"expected_{field}"] = str(expected[field])
                row[f"actual_{field}"] = str(actual[field])
                tolerance = Decimal("0.0001") if field != "volume" else max(
                    Decimal("0.01"), abs(expected[field]) * Decimal("0.000001"))
                if abs(expected[field] - actual[field]) > tolerance:
                    different.append(field)
            row["different_fields"] = ",".join(different)
            row["status"] = "DIFFERENCE" if different else "MATCH"
        output.append(row)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--date", default="2026-11-04")
    parser.add_argument("--start", default="15:00", help="UTC time HH:MM, default 15:00")
    parser.add_argument("--end", default="16:00", help="UTC time HH:MM, default 16:00")
    parser.add_argument("--output", default="data/rest_validation")
    args = parser.parse_args()
    begin = parse_time(args.date, args.start)
    end = parse_time(args.date, args.end)
    if begin >= end:
        parser.error("end must be after start")
    begin_ms = int(begin.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    results = {"source": BASE_URL, "symbol": args.symbol, "start_utc": begin.isoformat(),
               "end_utc": end.isoformat(), "periods": {}, "comparison": {}}
    session = requests.Session()
    session.headers.update({"User-Agent": "Devexperts-application-support-candle-validation/1.0"})
    for label, (symbol_period, secs) in INTERVALS.items():
        candle_symbol = f"{args.symbol}{{={symbol_period}}}"
        params = {"events": "Candle", "symbols": candle_symbol,
                  "fromTime": fmt_api_time(begin), "toTime": fmt_api_time(end),
                  "timeout": "10", "indent": ""}
        print(f"[{label}] Requesting {candle_symbol} ...")
        info = {"requested_symbol": candle_symbol}
        try:
            response = session.get(BASE_URL, params=params, timeout=(5, 25))
            info.update({"url": response.url, "http_status": response.status_code,
                         "bytes": len(response.content)})
            (output / f"{label}_response.json").write_text(response.text, encoding="utf-8")
            response.raise_for_status()
            data = response.json()
            records[label] = decode_candles(data, candle_symbol)
            info.update({"status": "OK" if records[label] else "NO_CANDLES",
                         "quality": quality(records[label], secs, begin_ms, end_ms)})
            write_csv(output / f"{label}_candles.csv", HEADERS, make_rows(records[label], label))
            print(f"    Received: {len(records[label])}; in window: {info['quality']['in_window']}; "
                  f"at end boundary: {info['quality']['at_end_boundary']}")
        except (requests.RequestException, ValueError) as exc:
            info["status"] = "ERROR"
            info["error"] = str(exc)
            print(f"    ERROR: {exc}")
        results["periods"][label] = info
    session.close()
    headers = ["utc_time", "expected_child_candles", "actual_child_candles", "status", "different_fields"] + [f"{mode}_{field}" for field in ["open", "high", "low", "close", "volume"] for mode in ["expected", "actual"]]
    for lo, hi, hi_s, lo_s in [("1m", "5m", 300, 60), ("5m", "1h", 3600, 300)]:
        if lo not in records or hi not in records:
            print(f"[{lo} -> {hi}] skipped: source data unavailable")
            continue
        comparisons = compare(records[lo], records[hi], hi_s, lo_s, begin_ms, end_ms)
        write_csv(output / f"comparison_{lo}_to_{hi}.csv", headers, comparisons)
        counts = dict(__import__("collections").Counter(row["status"] for row in comparisons))
        results["comparison"][f"{lo}_to_{hi}"] = counts
        print(f"[{lo} -> {hi}] {counts}")
        for diff in comparisons:
            if diff["status"] != "MATCH":
                print(f"    Inspect: {diff['utc_time']} {diff['status']} {diff['different_fields']}")
    (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved files under {output}/. Note: a DIFFERENCE is a finding to investigate, not proof of a dxFeed bug.")


if __name__ == "__main__":
    main()
