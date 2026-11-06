# dxFeed Demo Services: Technical Investigation and Operational Review

**Assessment:** Support Engineer — Task 1  
**Environment:** Public dxFeed demo endpoints  
**Investigation period:** November 3–November 6, 2026  
**Perspective:** Acting L2 Support Engineer

## Executive summary

This investigation evaluated the reliability and consistency of dxFeed's publicly available demo services. The work combined historical candle validation, market-session edge cases, limited concurrent-request testing, and an investigation of delayed streaming candle updates. All tests were read-only, and no production logs, internal metrics, or privileged data access were available.

Across three primary instruments—**AAPL**, **EUR/USD bid**, and **TSLA**—all **143 historical aggregation comparisons** matched across 1-minute, 5-minute, and 1-hour intervals. Another **52 comparisons** around the AAPL opening and closing windows matched. No duplicate timestamps, invalid OHLC values, or gaps were detected in those continuous test windows. Three noteworthy integration edge cases were documented: inclusive query-end timestamps, extended-hours equity candles, and empty weekend results.

Two bounded REST benchmarks, each comprising 12 requests, returned **24/24 successful responses containing candles**. Their higher-concurrency stages showed increased client-observed latency, but no errors or changes to repeated historical candle values. Separately, attempts to retrieve RKLB data produced `TIMED_OUT` responses; the upstream reason could not be established.

A delayed EUR/USD streaming capture produced **17 price-bearing candle events**, including **16 incremental updates after the initial snapshot**. The sequence was consistent with the event-count progression in surrounding REST observations, but the stream did not roll over to a newer five-minute bar. Therefore, **exact equality between a finalized SSE candle and its historical REST value remains unverified**. No confirmed dxFeed pricing defect was observed.

The primary support lessons are to distinguish successful HTTP transport from usable data, interpret candles using their market sessions and event semantics, monitor data freshness by symbol, and communicate evidence and uncertainty separately from root-cause hypotheses.

## 1. Scope and methodology

### 1.1 Instruments and interfaces

| Instrument | Selection rationale | Candle notation |
| --- | --- | --- |
| AAPL | Reference US equity with retrievable intraday data | `AAPL{=m}`, `{=5m}`, `{=h}` |
| EUR/USD | Representative FX pair, quote-based pricing | `EUR/USD{=m,price=bid}` and corresponding 5m/1h periods |
| TSLA | Practical higher-volatility equity representative | `TSLA{=m}`, `{=5m}`, `{=h}` |

TSLA was used as the high-volatility representative after RKLB, the initial candidate, repeatedly returned `TIMED_OUT`. TSLA's selection reflects its practical use as a volatile equity; this study did not calculate or statistically rank its realized volatility.

The primary historical endpoint was:

`https://demo.dxfeed.com/webservice/rest/events.json`

The `eventSource` demo endpoint was used for an SSE observation. An earlier Candlewebservice preview endpoint was also probed during access diagnosis. The public demo is a test environment with delayed streaming data, so results cannot be generalized directly to a production SLA.

### 1.2 Data processing and checks

The Python collector requested 1-minute, 5-minute, and 1-hour candles over explicitly specified UTC windows, preserved the original JSON responses, and wrote normalized candle CSV files. It reconstructed each five-minute candle from five one-minute candles and each hourly candle from twelve five-minute candles, then checked:

- **Open:** first constituent candle's open
- **High / low:** maximum high and minimum low over constituents
- **Close:** last constituent candle's close
- **Volume and contributing-event count:** sums over the constituent candles
- **Quality:** duplicate timestamps, OHLC validity, expected time-grid coverage, and end-boundary records

Small numeric tolerances were used for decimal representation and provider rounding. Event `count` is the number of contributing events in the candle and **must not automatically be interpreted as executed-trade count for FX bid candles**.

Comparison windows were deliberately treated as half-open intervals, `[start, end)`, regardless of whether the REST response included a candle exactly at `toTime`. A comparison was marked successful only when the expected lower-interval records were available. Time-grid gaps were interpreted in the context of instrument hours and market closures, rather than automatically classified as provider failures.

Two small historical REST benchmarks measured end-to-end request duration, HTTP/API status, record counts, and repeated-response fingerprints. One benchmark varied concurrency for AAPL; the other compared sequential and three-way parallel requests across all three instruments. These were deliberately bounded public-demo observations, not capacity tests.

## 2. Historical candle integrity results

| Test window (UTC) | 1m → 5m | 5m → 1h | Finding |
| --- | ---: | ---: | --- |
| AAPL, Nov 4, 15:00–16:00 | 12/12 | 1/1 | All matched |
| TSLA, Nov 4, 15:00–20:00 | 60/60 | 5/5 | All matched |
| EUR/USD bid, Nov 4, 15:00–20:00 | 60/60 | 5/5 | All matched |
| AAPL, Nov 4, 14:00–16:00 (opening) | 24/24 | 2/2 | All matched |
| AAPL, Nov 4, 20:00–22:00 (closing) | 24/24 | 2/2 | All matched |
| AAPL, Oct 31, 15:00–16:00 (Saturday) | N/A | N/A | No candles at any timeframe |

**Total:** 143/143 matches in the main instrument checks, plus 52/52 in the opening/closing edge-case windows, for **195/195 successful historical comparisons** over the tested periods. The normal and extended-session windows had no detected missing time-grid records, duplicate timestamps, or invalid OHLC candles. These checks establish *internal consistency* within the same provider and selected windows, not accuracy against an independent exchange feed.

### Worked example: AAPL, November 4, 15:00–15:05 UTC

Reconstructing the five-minute candle from five one-minute records produced the following values, matching the directly retrieved five-minute candle:

| Field | Aggregated 1m | Direct 5m |
| --- | ---: | ---: |
| Open | 351.675 | 351.675 |
| High | 353.6000 | 353.6000 |
| Low | 351.2122 | 351.2122 |
| Close | 353.5500 | 353.5500 |
| Volume | 2,375,224.583047 | 2,375,224.583047 |
| Contributing-event count | 36,478 | 36,478 |

The preserved `comparison_1m_to_5m.csv` and `comparison_5m_to_1h.csv` files provide the same checks for all periods. The stored raw JSON responses allow independent recalculation.

## 3. Anomalies, edge cases, and client impact

The assignment requests at least three anomalies **or edge cases**. The following observations are confirmed integration-relevant behavior; they are **not presented as three diagnosed dxFeed defects**.

### Case A — Inclusive query-end candle

**Observation.** AAPL's November 4 request for 15:00–16:00 UTC returned 61 one-minute, 13 five-minute, and two hourly candles, including records stamped at exactly 16:00 UTC. Only 60/12/1 belong to the chosen half-open interval.

**Hypothesis.** `toTime` is treated as an inclusive retrieval boundary. The dataset itself is not inconsistent; client aggregation semantics determine whether that extra record is included.

**Client impact.** Concatenating adjacent queries without boundary handling can duplicate records, distort volume, or trigger false mismatch alarms. **Mitigation:** use half-open windows in downstream processing and deduplicate on `(symbol, period, timestamp)`.

### Case B — No AAPL candles on Saturday

**Observation.** The October 31, 2026, 15:00–16:00 UTC query returned no AAPL candles at any tested timeframe. An early validator version labeled the empty interval `INCOMPLETE_LOWER_INTERVAL`; a corrected version classifies it as `NO_DATA_TO_COMPARE`.

**Hypothesis.** The lack of records is expected because the underlying US equity market is closed. This is a validation-rule issue, not evidence of missing market data.

**Client impact.** Schedule-unaware monitoring might report false outages or fill a closed period with artificial bars. **Mitigation:** classify gaps using instrument trading hours, holidays, and market-data semantics before raising an alert.

### Case C — Extended-hours equity candles

**Observation.** AAPL candles appeared both before 14:30 UTC and after 21:00 UTC on November 4, spanning the normal US equity session boundaries (9:30 a.m.–4:00 p.m. EST). Aggregation matched in both two-hour windows.

**Hypothesis.** The requested candle symbol includes extended-session observations by default. The documented `tho=true` candle attribute can restrict data to trading hours; session alignment is a separate attribute.

**Client impact.** Trading systems that assume regular-session-only bars may calculate different indicators, opening prices, or chart views. **Mitigation:** make session policy explicit in symbols, queries, charts, and downstream validation.

### Case D — Symbol-specific timeout and ambiguous no-data behavior

**Observation.** `RKLB` minute, five-minute, and hourly historical candle requests returned REST `TIMED_OUT`. A separate Trade snapshot request did likewise. The `RKLB:US` variant did not resolve the problem. AAPL, TSLA, and EUR/USD requests were successful control cases. In earlier diagnostics, a Candlewebservice preview returned HTTP 200 with an empty body for AAPL intraday requests, while an IBM daily candle request returned data and the separate REST candle endpoint returned AAPL history.

**Hypotheses.** Restricted demo symbol coverage, request routing, upstream source availability, or delayed source response are possible explanations. The observations do **not** determine which one caused the timeout or imply a production-wide outage.

**Client impact.** Users can mistake a successful HTTP code, an empty body, and a timeout event for equivalent outcomes. **Mitigation:** inspect API-level status and valid event counts, test known-good control symbols, and surface distinct no-data/timeout diagnostics.

### Case E — Delayed SSE updates are not finalized bars

**Initial observation.** A 20-second EUR/USD SSE session produced an `OK` stream, an empty candle snapshot marker (`eventFlags=14`) with `NaN` OHLC, and no usable prices. That marker must not be plotted or compared as a real candle.

**Follow-up observation.** A later bounded EUR/USD bid five-minute SSE capture produced **18 candle-shaped events, of which 17 contained prices**. A single candle labeled 22:25 UTC received **16 incremental updates after the snapshot** during a capture around 22:42 UTC. The two surrounding REST snapshots and the SSE stream followed this count sequence:

| Observation | Contributing-event count | Close |
| --- | ---: | ---: |
| REST before | 65 | 1.12164 |
| Initial valid SSE snapshot | 67 | 1.12172 |
| Subsequent SSE states | 68–84 (not every integer necessarily observed) | Updated repeatedly |
| REST after | 86 | 1.12180 |

The SSE records progressed in the expected direction relative to the REST observations. The changing SSE close is expected for a candle still being updated in the **delayed feed**, even if its market timestamp is much older than the client's wall clock.

**What was verified:** incremental candle states, repeated changes within the same candle index, and plausible REST/SSE event-count ordering. **What was not verified:** the final price equality of a *completed* candle, because no rollover to a new five-minute SSE period occurred in the capture. A previous script's `DIFFERENCE_REQUIRES_INVESTIGATION` status was superseded after correcting its wall-clock-based finalization assumption.

**Client impact.** Comparing intermediate streamed closes against a later REST value can generate false data-integrity incidents; treating empty snapshot markers as prices can corrupt charts. **Mitigation:** honor snapshot/removal flags, reject non-finite prices, distinguish market-event time from receipt time and feed delay, and compare finalized bars only after appropriate feed-progress evidence.

## 4. Bounded request-reliability experiments

### 4.1 AAPL-only concurrency test

Four historical AAPL five-minute candle requests were sent at each of three concurrency levels (12 total):

| Concurrent requests | Successful | Median latency | Observed max |
| ---: | ---: | ---: | ---: |
| 1 | 4/4 | 733.0 ms | ~770 ms |
| 2 | 4/4 | 1,184.9 ms | ~1,330 ms |
| 4 | 4/4 | 1,577.7 ms | ~2,692 ms |

All returned HTTP 200 and REST `OK`; successful response fingerprints were identical. No HTTP 429/503 or REST `TIMED_OUT` was observed during this benchmark.

### 4.2 Mixed-symbol sequential versus parallel test

The second experiment used AAPL, TSLA, and EUR/USD bid five-minute candles over a fixed historical window, with six sequential and six three-way parallel requests (two per symbol per stage):

| Stage | Successful | Median latency | Range |
| --- | ---: | ---: | ---: |
| Sequential (1 worker) | 6/6 | 734.0 ms | 613.2–873.2 ms |
| Parallel (up to 3 workers) | 6/6 | 1,690.6 ms | 979.8–2,178.0 ms |

Every request returned HTTP 200, REST `OK`, and 13 candle records including the endpoint's end-boundary candle. Fingerprints remained stable **within each symbol**. Across both experiments, the bounded historical REST workload had **24/24 successful requests and no observed throttling errors**.

### 4.3 Operational interpretation

**What failed first?** Nothing failed in the two bounded benchmark runs. RKLB timeouts were observed in *separate* symbol-availability tests, so they cannot be attributed to concurrency.

**What degraded first?** End-to-end latency was the first and only observed performance change under greater concurrency. The values include network time and upstream waits; they are not server CPU/processing measurements.

**Was degradation graceful?** Successful, internally consistent results continued despite longer response times. Rejection, resource exhaustion, retry storms, or failover were not exercised, so true graceful-overload behavior is unknown.

**Linear or exponential?** Neither conclusion is justified. The sample sizes (four per level, six per stage), uncontrolled client/network effects, and fixed stage order are insufficient to identify a scaling law.

**Rate limits, caches, staleness?** No rate-limit response appeared, but this does not show that rate limiting is absent. Matching fixed historical fingerprints do not demonstrate cache hits. The delayed stream cannot independently establish a production freshness target.

## 5. Operational risk assessment

| Risk | Detection strategy | Mitigation | L2 escalation criterion |
| --- | --- | --- | --- |
| **Data integrity / double counting** | Aggregate lower periods; flag duplicate timestamps and OHLC violations | Half-open windows, timestamp deduplication, price/count validation | Reproducible mismatch across complete buckets with raw evidence |
| **Latency and upstream timeout** | Per-endpoint and per-symbol latency trends, REST `TIMED_OUT` rate, control-symbol checks | Bounded retry with backoff, clear client timeout policy, distinguish transport vs source wait | Repeated timeouts affecting active instruments or multiple clients |
| **Stale/invalid streaming states** | Last *valid* update age, feed-progress markers, counts of non-finite prices/empty snapshots | Honor SSE flags, track delayed-feed watermark, compare only finalized bars | Event age exceeds an agreed symbol/session-specific freshness budget |
| **Silent no-data responses** | HTTP status versus parsed valid event count; empty body and zero-record trends | Typed no-data states and schema checks; calendar-aware classification | Unexpected empty results during active sessions with healthy controls |
| **Monitoring blind spots / false positives** | Separate metrics for closed markets, inactive symbols and feed failures | Market calendars, per-symbol dashboards, synthetic probes | Confirmed active-session gap or multiple independent monitoring signals |

**Proposed monitoring measures** are request success, HTTP and REST status, valid event count per response, end-to-end p50/p95/p99 latency over sufficiently large samples, last usable update and its age, per-symbol timeouts, and session-aware candle completeness.

**Illustrative service targets, not dxFeed commitments:** 99% valid outcomes over a rolling 30-day period and a historical-query p95 under three seconds, but only after defining request size, symbol coverage, market-hours exceptions, and a production measurement population. A valid weekend empty result should count as success, not an availability failure.

**Illustrative investigation triggers:** three consecutive active-symbol `TIMED_OUT` responses while control symbols succeed; no valid event during an open market beyond an agreed freshness budget; or a sustained endpoint-level latency/error regression across several consecutive monitoring windows. Escalation priority should depend on confirmed client impact and service ownership, not only on an isolated demo request.

## 6. Incident simulation — RKLB unavailable in the demo

This is a **hypothetical L2 incident constructed from observed demo behavior**, not an actual dxFeed production incident. A client reports that RKLB charts are not loading, while broader market charts continue to work.

### 6.1 Investigation and working hypotheses

**Observed reproduction:** historical REST candle requests for `RKLB{=m}`, `RKLB{=5m}`, and `RKLB{=h}` returned `TIMED_OUT`; a Trade snapshot also timed out. A `RKLB:US` variant did not improve the result. AAPL and TSLA were available as controls.

**L2 sequence:** (1) Record request IDs if supplied, UTC timestamps, exact endpoint/parameters, HTTP code, body, and client impact; (2) reproduce with an isolated request and known-good symbols; (3) verify the symbol's documented identity, feed coverage, and data entitlements; (4) distinguish historical-only behavior from live Trade/Quote paths; (5) inspect upstream source health, source timeouts, and internal request traces **if access is granted**; (6) evaluate scope before setting severity or retry policy; (7) escalate with a concise evidence packet.

**Working hypotheses:** unsupported demo coverage or mapping, upstream data-source unavailability, and entitlement/routing differences. No root cause was established by public access alone.

### 6.2 Customer communication (simulated)

> We reproduced a timeout when requesting RKLB data through the public demo. Requests for other instruments completed successfully in the same test environment. We are checking whether this is related to symbol coverage, an upstream data source, or a request-specific limitation. At present we have not established a wider outage or a recovery time. We will share an update when those checks are complete and discuss an alternative route if one is available.

### 6.3 Internal escalation note (simulated)

**To:** Market Data / REST API support engineering  
**Subject:** Reproducible RKLB `TIMED_OUT` in public demo; control symbols succeed  
**Impact:** A hypothetical client cannot retrieve RKLB candles or a Trade snapshot. Real customer scope is unknown.  
**Evidence:** Failed RKLB 1m/5m/1h historical requests and Trade snapshot; `RKLB:US` variant also failed; successful AAPL/TSLA historical controls. The relevant UTC window was November 4, 2026, 15:00–20:00.  
**Requested checks:** symbol mapping, demo subscription coverage, entitlements, upstream source availability, source wait-time metrics, and route-specific timeouts.  
**Unknowns:** confirmed root cause, onset, production impact, and recovery ETA.

### 6.4 Post-incident review outline

Record timeline and detection path, confirmed client/instrument scope, actual root cause (only after internal verification), upstream dependencies, why controls did or did not detect the condition, communication cadence, mitigations, and follow-up actions with owner/date. Re-run the original requests after any fix to validate recovery. No fictional root cause or recovery outcome is assigned in this simulation.

## 7. Product improvement proposals

| Category | Problem | Proposed change | Estimated complexity | Business impact |
| --- | --- | --- | --- | --- |
| **Technical / API** | A transport-successful empty body, expected market closure, unsupported symbol, and upstream timeout are easy to confuse | Structured, machine-readable response classifications with symbol/window metadata and consistent error semantics | **Medium–high** (API contract and backend coordination) | Faster diagnosis, fewer false client incidents, safer fallback handling |
| **Monitoring** | HTTP success alone cannot detect absent, stale, or invalid market values | Per-symbol valid-event/freshness dashboards, status-aware synthetic requests, and session-aware alerts | **Medium** (telemetry and alert-rule changes) | Earlier detection of narrow upstream problems; lower support investigation time |
| **Documentation / DX** | End-time inclusivity, session attributes, delayed snapshots, and candle finalization are subtle | Practical REST/SSE examples for half-open windows, `tho=true`, empty snapshots, and delayed-feed progression | **Low** (documentation/examples) | Fewer integration mistakes and more self-service resolution |

These are proposals based on demo usage, not claims that every production dxFeed product lacks the corresponding capability. Design and priority should be confirmed with service owners.

## 8. Conclusions and evidence limitations

The evidence supports **consistent historical aggregation** in the selected periods and **stable historical response data** throughout two small concurrent-request experiments. The main customer-facing risks observed were query-boundary duplication, session-definition confusion, schedule-unaware gap alerts, and ambiguous access/no-data states. The SSE investigation also demonstrated that updating a delayed candle is not the same as finalizing it, and that an `OK` streaming status need not contain a usable price.

No production service failure, causal RKLB root cause, cache behavior, rate-limit threshold, or finalized streaming-versus-historical price equality was proven. These boundaries are intentional: an L2 investigation should report reproductions and hypotheses separately, avoid attributing server defects to expected market behavior, and request additional internal diagnostics only when the affected customer and severity justify them.

### Evidence index

| Evidence | Repository path |
| --- | --- |
| Historical raw candles, normalized records, summaries and comparisons | `data/rest_validation/` |
| Opening, closing, weekend observations | `data/edge_cases/` |
| Single-symbol benchmark per-request samples and summary | `data/latency_test/samples.csv`, `data/latency_test/report.json` |
| Mixed-symbol benchmark samples and summary | `data/latency_test/multi_symbol/` |
| First empty SSE snapshot | `data/streaming/eurusd_stream.txt` |
| Valid SSE capture, before/after REST snapshots, and corrected evaluation | `data/final_checks/` |
| Reproduction and diagnostics | `collect_compare.py`, `benchmark_rest.py`, `benchmark_multi.py`, `verify_partial_and_live.py`, `exps/` |

The earlier `data/final_checks/final_check_summary.json` records an **initial, superseded** wall-clock-based interpretation. `reviewed_summary.json` is the corrected assessment and should be used for conclusions.

### Official references

1. [dxFeed REST API — requests, candle notation and limits](https://kb.dxfeed.com/en/market-data-api/data-access-solutions/rest.html)
2. [dxFeed CandleSymbol — price, session and alignment attributes](https://docs.dxfeed.com/dxfeed/api/com/dxfeed/event/candle/CandleSymbol.html)
3. [dxFeed IndexedEvent — snapshot and removal flags](https://docs.dxfeed.com/dxfeed/api/com/dxfeed/event/IndexedEvent.html)
4. [dxFeed Getting Started — demo and delayed-data context](https://kb.dxfeed.com/en/getting-started.html)
