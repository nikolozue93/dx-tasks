#!/usr/bin/env python3
import argparse
import json
import math
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


REST_URL = "https://demo.dxfeed.com/webservice/rest/events.json"
SSE_URL = "https://demo.dxfeed.com/webservice/rest/eventSource"

FIVE_MINUTES_MS = 5 * 60 * 1000
PRICE_FIELDS = ("open", "high", "low", "close")
FINAL_FIELDS = PRICE_FIELDS + ("volume", "count")
PRICE_TOLERANCE = Decimal("0.0000001")


def utc_now():
    return datetime.now(timezone.utc)


def request_time(value):
    return value.strftime("%Y%m%d-%H%M%SZ")


def epoch_millis(value):
    """Accept REST epoch milliseconds or SSE ISO-8601 timestamps."""
    if isinstance(value, (int, float)) or str(value).isdigit():
        return int(value)

    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def iso_utc(epoch_ms):
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def finite_number(value):
    try:
        number = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return None

    return number if number.is_finite() else None


def valid_candle(record):
    """Ignore REMOVE_EVENT markers, NaNs and impossible OHLC values."""
    if int(record.get("eventFlags", 0)) & 2:
        return False

    values = [finite_number(record.get(field)) for field in PRICE_FIELDS]
    if any(value is None for value in values):
        return False

    open_, high, low, close = values
    return low <= open_ <= high and low <= close <= high


def candle_changed(first, second):
    """Price/count changes are normal while a stream updates a candle."""
    return any(
        finite_number(first.get(field)) != finite_number(second.get(field))
        for field in FINAL_FIELDS
    )


def read_rest(session, symbol, start, end):
    params = {
        "events": "Candle",
        "symbols": symbol,
        "fromTime": request_time(start),
        "toTime": request_time(end),
        "timeout": "10",
        "indent": "",
    }

    try:
        response = session.get(REST_URL, params=params, timeout=(5, 20))
        data = response.json()

        candles = data.get("Candle", {}).get(symbol, [])
        if not isinstance(candles, list):
            candles = []

        return {
            "http_status": response.status_code,
            "api_status": data.get("status"),
            "request_url": response.url,
            "valid_candles": sum(valid_candle(row) for row in candles),
            "candles": candles,
            "observed_at_utc": utc_now().isoformat(),
        }

    except (requests.RequestException, ValueError, TypeError) as error:
        return {
            "error": str(error),
            "candles": [],
            "observed_at_utc": utc_now().isoformat(),
        }


def read_sse(session, symbol, start, duration_seconds):
    params = {
        "events": "Candle",
        "symbols": symbol,
        "fromTime": request_time(start),
    }

    messages = []
    events = []
    connection_error = None
    snapshot_active = False
    snapshot_finished = False
    start_clock = time.monotonic()

    try:
        with session.get(
            SSE_URL,
            params=params,
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(5, duration_seconds + 7),
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines(decode_unicode=True):
                if time.monotonic() - start_clock >= duration_seconds:
                    break
                if not line or not line.startswith("data:"):
                    continue

                xml_text = line.removeprefix("data:").strip()
                messages.append(xml_text)

                try:
                    root = ET.fromstring(xml_text)
                except ET.ParseError:
                    continue

                for node in root.findall(".//{*}Candle"):
                    candle = {
                        element.tag.rsplit("}", 1)[-1]: element.text or ""
                        for element in node
                    }
                    candle["received_at_utc"] = utc_now().isoformat()

                    flags = int(candle.get("eventFlags", "0"))
                    if flags & 4:  # SNAPSHOT_BEGIN
                        snapshot_active = True

                    candle["from_snapshot"] = (
                        snapshot_active or not snapshot_finished
                    )
                    events.append(candle)

                    if flags & (8 | 16):  # SNAPSHOT_END or SNAPSHOT_SNIP
                        snapshot_active = False
                        snapshot_finished = True

    except (requests.RequestException, ValueError) as error:
        connection_error = str(error)

    return {
        "events": events,
        "raw_messages": messages,
        "error": connection_error,
        "snapshot_finished": snapshot_finished,
    }


def index_by_period(candles):
    return {
        epoch_millis(item["time"]): item
        for item in candles
        if "time" in item and valid_candle(item)
    }


def compare_final_fields(stream_record, rest_record):
    different = []

    for field in FINAL_FIELDS:
        stream_value = finite_number(stream_record.get(field))
        rest_value = finite_number(rest_record.get(field))

        if stream_value is None or rest_value is None:
            continue

        tolerance = PRICE_TOLERANCE if field in PRICE_FIELDS else Decimal(0)
        if abs(stream_value - rest_value) > tolerance:
            different.append(field)

    return different


def evaluate_capture(rest_before, sse_events, rest_after):
    """Analyze saved events using FEED progression rather than wall clock.

    - A later candle's timestamp is the evidence of a stream rollover.
    - Repeated updates to the same bar are valid incremental candle states.
    - A later REST request is another state, not necessarily the same instant.
    """
    before = index_by_period(rest_before.get("candles", []))
    after = index_by_period(rest_after.get("candles", []))

    by_period = defaultdict(list)
    for event in sse_events:
        if "time" not in event or not valid_candle(event):
            continue

        by_period[epoch_millis(event["time"])].append(event)

    periods = sorted(by_period)
    incremental_bars = []
    completed_checks = []
    temporal_checks = []

    for bar_start in periods:
        updates = by_period[bar_start]
        live_updates = [item for item in updates if not item.get("from_snapshot")]
        counts = [finite_number(item.get("count")) for item in live_updates]
        counts = [item for item in counts if item is not None]

        changed = len(live_updates) >= 2 and any(
            candle_changed(left, right)
            for left, right in zip(live_updates, live_updates[1:])
        )

        if changed:
            incremental_bars.append({
                "bar_start_utc": iso_utc(bar_start),
                "updates_after_snapshot": len(live_updates),
                "first_count": int(counts[0]) if counts else None,
                "last_count": int(counts[-1]) if counts else None,
                "interpretation": "Changing candle state on delayed SSE feed",
            })

        # A wall-clock comparison is unsafe for delayed market data.
        # Only a NEWER period in the stream establishes that the feed moved on.
        stream_rolled_over = any(later > bar_start for later in periods)

        if stream_rolled_over and bar_start in after:
            differences = compare_final_fields(updates[-1], after[bar_start])
            completed_checks.append({
                "bar_start_utc": iso_utc(bar_start),
                "status": (
                    "MATCH_AT_OBSERVED_ROLLOVER"
                    if not differences
                    else "DIFFERENT_AFTER_ROLLOVER_REVIEW_CORRECTIONS"
                ),
                "different_fields": differences,
            })

        # Assess chronology without claiming exact same-instant equality.
        if bar_start in before and bar_start in after:
            before_count = finite_number(before[bar_start].get("count"))
            after_count = finite_number(after[bar_start].get("count"))
            sse_counts = [finite_number(item.get("count")) for item in updates]
            sse_counts = [value for value in sse_counts if value is not None]

            if before_count is not None and after_count is not None and sse_counts:
                ordered = (
                    before_count <= min(sse_counts)
                    and max(sse_counts) <= after_count
                )
                temporal_checks.append({
                    "bar_start_utc": iso_utc(bar_start),
                    "rest_before_count": int(before_count),
                    "sse_count_range": [int(min(sse_counts)), int(max(sse_counts))],
                    "rest_after_count": int(after_count),
                    "ordered_as_expected": ordered,
                })

    if completed_checks and all(
        check["status"] == "MATCH_AT_OBSERVED_ROLLOVER"
        for check in completed_checks
    ):
        equality_status = "MATCH_FOR_OBSERVED_ROLLOVER_BARS"
    elif completed_checks:
        equality_status = "REVIEW_DIFFERENCES_AFTER_STREAM_ROLLOVER"
    else:
        equality_status = "NOT_VERIFIED_NO_STREAM_ROLLOVER"

    return {
        "price_bearing_sse_events": sum(len(items) for items in by_period.values()),
        "unique_sse_periods": len(by_period),
        "updating_candles": incremental_bars,
        "partial_candle_result": (
            "OBSERVED_INCREMENTAL_CANDLE_STATES"
            if incremental_bars
            else "NOT_VERIFIED"
        ),
        "rest_sse_ordering": temporal_checks,
        "rest_sse_ordering_status": (
            "COUNTS_CONSISTENT_WITH_PROGRESSION"
            if temporal_checks and all(item["ordered_as_expected"] for item in temporal_checks)
            else "NOT_VERIFIED"
        ),
        "completed_comparison": equality_status,
        "completed_bars_compared": completed_checks,
        "note": (
            "SSE is delayed. A candle timestamp earlier than computer time does "
            "not prove that the SSE feed has finalized that candle. A changing "
            "close is expected until feed rollover. REST-before and REST-after "
            "are different observations, not simultaneous snapshots."
        ),
    }


def output_directory(path):
    directory = Path(path)
    if directory.is_absolute():
        return directory
    return Path(__file__).resolve().parents[1] / directory


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def analyze_existing(folder):
    """Re-evaluate the user's existing capture without hitting the demo."""
    before = json.loads((folder / "rest_before.json").read_text())
    after = json.loads((folder / "rest_after.json").read_text())
    events = json.loads((folder / "sse_events.json").read_text())

    reviewed = evaluate_capture(before, events, after)
    save_json(folder / "reviewed_summary.json", reviewed)
    return reviewed


def capture_new(symbol, duration_seconds, folder):
    start = utc_now() - timedelta(minutes=20)

    with requests.Session() as session:
        session.headers.update({
            "User-Agent": "dxfeed-support-assessment/candle-observation"
        })
        before = read_rest(session, symbol, start, utc_now())
        stream = read_sse(session, symbol, start, duration_seconds)
        after = read_rest(session, symbol, start, utc_now())

    save_json(folder / "rest_before.json", before)
    save_json(folder / "rest_after.json", after)
    save_json(folder / "sse_events.json", stream["events"])
    (folder / "sse_capture.xmltxt").write_text(
        "\n".join(stream["raw_messages"]), encoding="utf-8"
    )

    reviewed = evaluate_capture(before, stream["events"], after)
    reviewed.update({
        "candle_symbol": symbol,
        "sse_message_count": len(stream["raw_messages"]),
        "sse_candle_event_count": len(stream["events"]),
        "sse_connection_error": stream["error"],
    })
    save_json(folder / "reviewed_summary.json", reviewed)

    return reviewed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--price-type", default="bid", choices=[
        "bid", "ask", "mark", "last", "none"
    ])
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--output", default="data/final_checks")
    parser.add_argument(
        "--analyze-existing",
        type=Path,
        metavar="FOLDER",
        help="Review previously saved REST/SSE files without making network requests",
    )
    args = parser.parse_args()

    if args.analyze_existing:
        folder = args.analyze_existing.resolve()
        result = analyze_existing(folder)
    else:
        if not 5 <= args.seconds <= 45:
            parser.error("--seconds must be between 5 and 45")

        suffix = (
            f",price={args.price_type}" if args.price_type != "none" else ""
        )
        symbol = f"{args.symbol}{{=5m{suffix}}}"

        folder = output_directory(args.output)
        folder.mkdir(parents=True, exist_ok=True)
        result = capture_new(symbol, args.seconds, folder)

    print(f"Price-bearing SSE events: {result['price_bearing_sse_events']}")
    print(f"Incremental candles: {len(result['updating_candles'])}")
    print(f"Partial states: {result['partial_candle_result']}")
    print(f"Temporal check: {result['rest_sse_ordering_status']}")
    print(f"Final values: {result['completed_comparison']}")
    print(f"Saved to: {folder / 'reviewed_summary.json'}")


if __name__ == "__main__":
    main()
