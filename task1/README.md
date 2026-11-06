# Task 1

A reproducible investigation of dxFeed's public demo services, covering historical candle integrity, market-session edge cases, API response behavior, and delayed streaming data. The project approaches the demo as an L2 support case: reproduce observations, distinguish expected behavior from failures, assess client impact, and propose monitoring and operational improvements.

The full investigation and conclusions are in **[technical_report.md](technical_report.md)**.

## Scope

Three instruments were used for the primary consistency checks:

| Instrument | Category | Candle source |
| --- | --- | --- |
| `AAPL` | US equity | Trade-based candles |
| `EUR/USD` | FX pair | Bid-price candles (`price=bid`) |
| `TSLA` | Higher-volatility US equity | Trade-based candles |

The tests retrieved 1-minute, 5-minute, and 1-hour historical candles; compared aggregated OHLC, volume, and contributing-event counts; inspected session boundaries and weekends; and measured response time and consistency under small, controlled request bursts. Streaming observations used dxFeed's delayed SSE demo feed.

## Key findings

- **Historical aggregation:** 143/143 comparisons matched for the three primary instruments. Another 52/52 matched across AAPL opening/closing windows.
- **Query boundaries:** The REST endpoint returned candles timestamped exactly at `toTime`. Comparisons use a half-open `[start, end)` window to prevent double counting.
- **Market schedules:** AAPL extended-hours candles were present; the Saturday request returned no candles, as expected.
- **API reliability:** Both bounded benchmarks completed successfully (24/24 historical REST requests). Median response times increased in the concurrent stages, without observed request errors or changes in repeated historical results.
- **Symbol-specific availability:** Requests for `RKLB` returned `TIMED_OUT`, while control symbols were available. The cause was not established.
- **Streaming:** A delayed EUR/USD SSE capture showed 17 price-bearing events and 16 incremental updates after its snapshot. The event-count progression was compatible with REST snapshots, but the feed did not roll over to a newer candle, so finalized SSE–REST price equality was not verified.

These findings describe a **public, delayed demo**; they do not establish production capacity, exchange-truth accuracy, or a confirmed provider defect.

## Repository layout

```text
.
├── collect_compare.py          # Historical OHLC/volume/count validation
├── benchmark_rest.py           # Bounded single-symbol REST benchmark
├── benchmark_multi.py          # Bounded multi-symbol parallel benchmark
├── verify_partial_and_live.py  # Delayed SSE capture and offline review
├── requirements.txt
├── technical_report.md         # Investigation, evidence, impact and proposals
├── data/
│   ├── rest_validation/        # AAPL, TSLA and EUR/USD candle evidence
│   ├── edge_cases/             # Opening, closing and weekend observations
│   ├── latency_test/           # Per-request measurements and summaries
│   ├── streaming/              # Initial SSE empty-snapshot capture
│   └── final_checks/           # REST/SSE capture and reviewed interpretation
└── exps/                       # Initial endpoint-access diagnostics
```

The `data/` directory contains the responses and derived results used in the report. Raw REST JSON, normalized CSVs, comparison tables, and benchmark samples are retained so findings can be independently inspected.

## Running the tests

**Requirements:** Python 3.10+, `requests`, and network access to the public demo when collecting fresh data.

```bash
python -m pip install -r requirements.txt
```

**Historical validation** (UTC dates and times):

```bash
python collect_compare.py --symbol AAPL --date 2026-11-04
python collect_compare.py --symbol TSLA --date 2026-11-04 --start 15:00 --end 20:00
python collect_compare.py --symbol 'EUR/USD' --price-type bid --date 2026-11-04 --start 15:00 --end 20:00
```

**Market-session examples:**

```bash
python collect_compare.py --symbol AAPL --date 2026-11-04 --start 14:00 --end 16:00 --output data/edge_cases/aapl_open
python collect_compare.py --symbol AAPL --date 2026-11-04 --start 20:00 --end 22:00 --output data/edge_cases/aapl_close
python collect_compare.py --symbol AAPL --date 2026-10-31 --start 15:00 --end 16:00 --output data/edge_cases/aapl_weekend
```

**Bounded REST benchmarks:**

```bash
python benchmark_rest.py --dry-run
python benchmark_rest.py
python benchmark_multi.py --dry-run
python benchmark_multi.py
```

Both benchmarks intentionally use small workloads on a shared demo service; they are not stress tests. New runs can differ from the recorded results.

**Review the saved SSE capture without network access:**

```bash
python verify_partial_and_live.py --analyze-existing data/final_checks
```

This regenerates `data/final_checks/reviewed_summary.json`. A new short observation is also supported with `--symbol`, `--price-type`, and `--seconds`; the saved capture, rather than a new run, is the evidence cited in the report.

## Interpretation notes

The validator checks whether enough lower-interval candles exist before declaring an aggregation match. The requested end time is excluded when forming comparison windows, even if the API response contains a candle at that timestamp. A time-grid gap is a diagnostic, not automatically a defect: weekends, instrument sessions, and sparse trading must be considered.

For delayed SSE, wall-clock time must **not** be used by itself to declare a streamed candle finalized. The authoritative interpretation of the saved observation is `data/final_checks/reviewed_summary.json`. The earlier `final_check_summary.json` is retained as an audit artifact; its `DIFFERENCE_REQUIRES_INVESTIGATION` conclusion was superseded when delayed-stream semantics were accounted for.

## References

- [dxFeed REST API](https://kb.dxfeed.com/en/market-data-api/data-access-solutions/rest.html)
- [Candle symbol attributes](https://docs.dxfeed.com/dxfeed/api/com/dxfeed/event/candle/CandleSymbol.html)
- [Indexed-event snapshot flags](https://docs.dxfeed.com/dxfeed/api/com/dxfeed/event/IndexedEvent.html)
- [dxFeed demo and delayed-data context](https://kb.dxfeed.com/en/getting-started.html)
