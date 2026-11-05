# Task 1 — dxFeed Demo Investigation

**Working report — updated after the first bounded latency test (November 5, 2026)**

This is the running report for Task 1. New test results should be added here, not written as separate reports. The findings below come from the demo responses and saved test outputs. No production access was used.

## 1. Methodology and tools

I used Python (`requests`) and dxFeed's public demo interfaces to collect historical candles. The main endpoint for validation was `https://demo.dxfeed.com/webservice/rest/events.json`. For each selected interval, the script requests 1-minute, 5-minute, and 1-hour candles for the same UTC window, saves the original responses, and normalizes the records into CSV.

I rebuilt 5-minute candles from five 1-minute candles and hourly candles from twelve 5-minute candles. I compared open, high, low, close, and volume with the directly returned candles, using small rounding tolerances. Trade counts were recorded but are not yet part of the comparison. I also checked for duplicate timestamps, invalid OHLC values, and gaps on the expected time grid where continuous trading was appropriate. Comparison windows use an **exclusive end boundary**: a candle stamped exactly at the requested ending time belongs to the next window.

Tools: `collect_compare.py` (collection and aggregation checks), `benchmark_rest.py` (small controlled API experiment), JSON/CSV evidence files, and preliminary access diagnostics.

## 2. Data consistency results

| Instrument / test | Window (UTC) | 1m → 5m | 5m → 1h | Observation |
| --- | --- | ---: | ---: | --- |
| AAPL, regular trading window | Nov 4, 15:00–16:00 | 12/12 match | 1/1 match | No gaps, duplicate timestamps, or invalid OHLC records in this window |
| TSLA, five-hour window | Nov 4, 15:00–20:00 | 60/60 match | 5/5 match | No reported aggregation mismatches |
| EUR/USD, bid candles | Nov 4, 15:00–20:00 | 60/60 match | 5/5 match | No reported aggregation mismatches |
| AAPL, around market opening | Nov 4, 14:00–16:00 | 24/24 match | 2/2 match | Pre-market and regular-session candles present |
| AAPL, around market closing | Nov 4, 20:00–22:00 | 24/24 match | 2/2 match | Regular-session and after-hours candles present |
| AAPL, Saturday | Oct 31, 15:00–16:00 | Not applicable | Not applicable | No candle records at any interval |

The stock market's regular session on November 4 was 14:30–21:00 UTC. Because the demo returned AAPL candles outside that window, clients must not assume the default candle feed contains regular-session trading alone. The session setting and instrument's trading schedule need to be considered before labeling missing or extra bars as bad data.

### Edge case A — End-time boundary is included in the response

The REST endpoint returned a record stamped exactly at the requested `toTime` for active market windows. For example, the AAPL 15:00–16:00 request returned 61 one-minute records, 13 five-minute records, and two hourly records, although only 60/12/1 belonged to the half-open comparison window [15:00, 16:00). The validator excludes the end-boundary candle rather than counting it twice. A client that appends adjoining query windows without deduplicating may otherwise duplicate candles.

### Edge case B — No weekend stock candles; initial validator false positive

For Saturday October 31, AAPL returned no candle records. The earlier validator incorrectly called these missing 1-minute and 5-minute intervals (`INCOMPLETE_LOWER_INTERVAL`) because it assumed continuous trading. A later script revision handles a no-data period at both granularities as `NO_DATA_TO_COMPARE`. The weekend result still needs to be regenerated with that revision before treating the new classification as a verified output. This was a validation-script issue, not evidence of a dxFeed market-data defect.

### Edge case C — Data outside regular stock trading hours

AAPL returned minute-by-minute candles before the 14:30 UTC regular-market opening and after the 21:00 UTC close on November 4. Aggregation remained consistent across those windows (24/24 1m-to-5m and 2/2 5m-to-1h matches for each test). Historical clients must make their session assumptions explicit; they should not discard extended-hours data as an error without first checking how the candle symbol is configured.

### Access and symbol-availability findings

The `tools-demo.dxfeed.com/candledata-preview` interface returned **HTTP 200 with a zero-byte body** for AAPL 1m, 5m, and 1h requests on November 4, including an additional text-format test. A separate IBM daily-candle control returned four records from the same demo service, so the empty AAPL responses were not sufficient to diagnose a general outage. The separate REST demo returned valid AAPL trade and historical candle records. We therefore used the working REST endpoint for the consistency study. A successful HTTP status alone was not enough to verify usable candle data.

RKLB was investigated as the preferred high-volatility stock. Both `RKLB` and `RKLB:US` historical candle requests returned REST `TIMED_OUT`, and a separate `RKLB` Trade snapshot also timed out. TSLA worked for the same historical window, so it was used for the high-volatility instrument category. These timeouts show a limitation encountered on the public demo, **not proof** that RKLB is unsupported or that dxFeed has a data-quality defect.

Across the successful comparison windows, no candle aggregation errors were observed. The meaningful findings are boundary behavior, session assumptions, empty-period handling, and symbol/interface availability.

## 3. Small REST latency and consistency experiment

**Test date:** November 5, 2026 (UTC). **Endpoint:** REST `events.json`. **Request:** `AAPL{=5m}`, historical candles from November 4, 15:00–16:00 UTC. I sent 12 requests in three groups (four requests each) at concurrency levels 1, 2, and 4, with pauses between groups. There were no automatic retries. This was deliberately a small public-demo experiment, not a production capacity or stress test.

| Concurrent requests | Requests | Successful | Median latency | Slowest request |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 4 | 4 | 733.0 ms | 769.6 ms |
| 2 | 4 | 4 | 1,184.9 ms | 1,329.7 ms |
| 4 | 4 | 4 | 1,577.7 ms | 2,692.2 ms |
| **Total** | **12** | **12** | **929.5 ms** | **2,692.2 ms** |

All requests returned HTTP 200, REST `status=OK`, and 13 candles (including the end-boundary candle). The historical **time/OHLC/volume** fingerprint matched across all 12 responses. This verifies consistency of those fields within the sample; it does not establish that every field or the raw response bytes were identical.

Latency was generally higher at greater concurrency, and the slowest request was at concurrency 4. With only four observations per level, I cannot infer whether latency grows linearly or exponentially, identify server capacity, or estimate a reliable production p95. There were no observed HTTP 429/503 responses or REST errors during this test. That does not prove rate limits do not exist. Measurements include client-to-demo network conditions and cannot isolate server processing time or caching.

**Remaining Part 2 requirement:** The assessment asks for parallel requests across **multiple symbols**. The current AAPL-only run is a useful baseline, but a small mixed-symbol run is still needed before Part 2 is complete. Staleness and real-time-versus-historical comparisons have not yet been established.

## 4. Operational interpretation (draft, subject to later evidence)

- **Silent/no-data risk:** HTTP 200 can accompany a zero-byte candle body, so monitoring should count parsed usable data, not just successful HTTP responses.
- **Client data-integrity risk:** Inclusive end-boundary records can be duplicated by clients merging adjacent history requests without a half-open-window rule.
- **Calendar/session risk:** Weekend no-data periods and extended-hours equity candles must be interpreted using market schedules and candle settings, not a continuous 24/7 grid.
- **Coverage/timeout risk:** RKLB requests timed out through this public demo even when other instruments succeeded; support should distinguish symbol/source availability from an assumed data defect.
- **Latency risk:** Small increases in concurrency coincided with higher observed latency. This deserves monitoring but is not enough to claim a bottleneck or define a production SLO.

For each risk in the final report, add an explicit detection method, safe mitigation, and escalation trigger. Do not claim those measures have already been deployed.

## 5. Still to complete

1. **Part 1:** Test or clearly scope out demo real-time/Radar versus historical behavior. The assessment explicitly asks for this comparison.
2. **Part 2:** Make a small, bounded **multi-symbol** parallel request test. Preserve individual timings and REST status, not only HTTP status.
3. **Part 3:** Finalize a risk table with monitoring, mitigation, and escalation criteria tied to observed findings.
4. **Part 4:** Write an incident simulation derived from an observation, clearly labeled hypothetical, with client update, internal escalation, and post-mortem outline.
5. **Part 5:** Propose one technical, one monitoring, and one documentation improvement, each with estimated complexity and business impact.
6. Prepare the final report, selected supporting evidence, and 45-minute presentation.

## 6. Evidence kept with the project

- `collect_compare.py`, `benchmark_rest.py`, `requirements.txt`
- Representative `data/rest_validation/` summaries and comparison CSVs for AAPL, TSLA, and EUR/USD
- `data/edge_cases/` opening/closing summaries and their comparisons; weekend summary **after** running the corrected validator
- `data/latency_test/samples.csv` and `data/latency_test/report.json`

Raw HTTP responses and early probes can stay local or in an optional `experiments/` folder. They do not need to be duplicated in every Git commit. Keep enough source data to reproduce the reported results.
