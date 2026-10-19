# Spike and Pattern Detection Plan

## Goal and scope

Implement detection on top of the existing parser without changing parsing behavior. `LogParser.parse_file()` and `LogParser.parse_lines()` return a `ParseResult`; detectors consume its `entries`, a tuple of successfully parsed `LogEntry` objects. Malformed lines have already been excluded and remain available through the other `ParseResult` fields for diagnostics. Spike detection also needs request-volume data generated alongside the log.

Keep implementation in `src/log_analyzer/detection/` and tests in `tests/test_detection.py`.

## Input contract

- Each `LogEntry` provides `timestamp`, `level`, `service`, `message`, and `line_no`.
- Only entries with `level == "ERROR"` count toward either detector.
- Entries arrive in source order, which the parser does not guarantee is timestamp order. Sort by `(timestamp, line_no)` within detection before assigning entries to time windows.
- Timestamps are naive `datetime` values. Treat them as belonging to one consistent local timeline; do not infer a time zone.
- Compare repeated messages by their parsed `message` string exactly, including case. The parser has already stripped surrounding whitespace. Do not normalize IDs or other variable text in this first version.
- A log line is not necessarily one request. Do not use the number of parsed entries as the request count. The data generator must provide the number of completed requests for each current five-minute window, plus aggregate `(requests, failed_requests)` pairs for the same clock-time window on at least seven previous days. In generated data, each request has exactly one success/failure outcome, so `failed_requests <= requests`; the current window's failed-request count must match the corresponding `ERROR` request events in the log.

## Public types

1. Define a frozen `Finding` dataclass with `detector`, `description`, `started_at`, `severity`, `count`, and optional `service` and `message` fields. `started_at` is a `datetime`; severity is one of `"warning"` or `"critical"`.
2. Define a `DetectionInput` containing the parsed entries, current request counts keyed by window start, and dated `TrafficWindow(started_at, requests, failed_requests)` records. The historical aggregates are input data, not inferred from the three-hour sample file.
3. Define an abstract `BaseDetector` with `detect(data: DetectionInput) -> tuple[Finding, ...]`. It should hold no per-call results on the instance so repeated calls are independent. The repeated-message detector uses only `data.entries`.
4. Export `DetectionInput`, `Finding`, `BaseDetector`, `SpikeDetector`, and `RepeatedMessageDetector` from `detection/__init__.py`.

## Shared window rule

Use fixed, non-overlapping, half-open windows of configurable duration (default: five minutes). Align boundaries to clock time, so a five-minute window is, for example, `[15:30:00, 15:35:00)`. An event exactly at 15:35:00 belongs to the next window. Require a positive whole-minute window size that divides 1440, so daily slots align consistently. Each detector emits at most one finding per matching window and grouping key. Return findings ordered by `started_at`, then service and message for deterministic tests.

## SpikeDetector

- Count failed requests represented by `ERROR` entries in each current window, across services. Obtain the number of completed requests `N` for that window from `DetectionInput`; never substitute a count of all log lines.
- For the same five-minute clock-time slot on previous days, sum historical failed requests `H_E` and completed requests `H_N`. Estimate the expected failure probability as `p = (H_E + 1) / (H_N + 2)`. The `+1` and `+2` prevent an estimated probability of exactly zero or one when the history is small. Use the latest seven earlier dates with positive traffic in the matching slot; never use the current or future date. Raise `InsufficientHistoryError` when an error-containing window lacks this history. Windows without errors cannot be spikes and need no baseline. Missing request counts for error-containing windows, duplicate history windows, unaligned timestamps, and impossible counts raise `ValueError`.
- Model the current failed-request count as `X ~ Binomial(N, p)`. For observed count `E`, calculate the upper-tail probability `q = P(X >= E)`. If `N == 0`, emit no finding. The generator contract ensures `0 <= E <= N`.
- There are 288 five-minute windows per day. Use a daily false-alert budget of 0.05, giving a per-window warning cutoff `q <= 0.05 / 288`; use `q <= 0.005 / 288` for `critical`. These are configurable statistical decision levels rather than fixed error-count thresholds. Set `started_at` to the window start, and include `N`, `E`, expected errors `N * p`, and `q` in the description.
- Nightly activity is represented in the historical values for that same clock-time slot. A higher nighttime request volume with the usual failure rate should not alert; an unusual increase in the failure rate should. These daily budgets are nominal decision levels, not guaranteed false-alarm probabilities: the failure probability is estimated and real observations can be dependent.

## RepeatedMessageDetector

- Within each window, group `ERROR` entries by `(service, message)`. This keeps identical text from unrelated services separate.
- Emit a finding when a group reaches a configurable warning threshold (default: 5). Set `started_at` to the timestamp of that group's first entry in the window; include its service, message, window, and count in the description.
- Use a configurable critical threshold (default: 10): counts at or above it are `critical`, and other qualifying counts are `warning`. Validate `1 <= warning_threshold < critical_threshold`.

## Implementation sequence

1. Add the shared finding type and abstract detector contract.
2. Add a small private helper for sorting errors and computing their aligned window start; reuse it in both detectors.
3. Implement the spike detector's historical rate calculation and binomial upper-tail test, plus the repeated-message detector's counters. Keep detection logic independent of file I/O and `ParseResult` construction.
4. Export the public types and run pytest.

## Tests

Place pytest tests in `tests/test_detection.py`, as requested. Cover:

- A quiet log containing no errors returns no findings from either detector.
- Counts just below, at, and above the repeated-message warning and critical thresholds.
- Spike windows with the same failure rate but different request volumes, an increased failure rate, zero requests, missing history, and invalid request/failure counts.
- A synthetic nightly workload that is busier than daytime but matches its own historical failure rate, followed by a nightly window with an elevated failure rate.
- Events exactly on a window boundary, multiple windows, and unsorted input.
- Repeated text across different services, different messages in one service, and non-`ERROR` entries with matching text.
- Finding start times, descriptions, counts, severity, and deterministic ordering.
- Empty input, invalid configuration, and repeated calls to the same detector instance.
- Once the generator supplies aligned request counts and at least seven days of historical window aggregates, an integration test that parses `data/sample.log` and confirms the planted spike is detected. Keep unit tests based on small, explicit `LogEntry` and traffic fixtures.

The current three-hour sample has 36, 49, and 52 errors in the three five-minute windows beginning at 15:30, 15:35, and 15:40, but it does not include request counts or preceding days. Do not claim that the usage-aware rule was run on this sample until the generator provides that data.

Run `PYTHONPATH=src uv run --no-project --with pytest pytest tests/test_detection.py -v`. The tests generate explicit synthetic traffic histories and parser input in memory, including normal nightly load and abnormal failure rates. No request metrics are invented for the existing sample file.
