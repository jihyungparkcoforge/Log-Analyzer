# Log-Analyzer

Reads an application log file and prints a short report of what broke, when it started, and how severe it is.

## Log format

One event per line, comma-separated (see [`data/README.md`](data/README.md) for the full contract):

```
YYYY-MM-DD HH:MM:SS,LEVEL,service,message
2026-10-19 14:00:17,ERROR,payment-service,Connection timeout to db-primary
```

`LEVEL` is `INFO`, `WARNING` or `ERROR`. The message is everything after the third comma, so it may contain commas.

Malformed lines (bad timestamp, unknown level, missing fields, blank lines, ...) are skipped and counted, never fatal. The report lists how many were skipped and why.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
```

## Usage

```
uv run log-analyzer data/sample.log
```

`uv run python -m log_analyzer.reporting data/sample.log` is equivalent. The exit code is `0` when analysis completes (with or without findings) and `2` for a missing or unreadable file.

## What the report contains

Findings from two detectors in `src/log_analyzer/detection/`:

- **Repeated error message**: the same `ERROR` message from the same service at least 5 times in a 5-minute window (`warning`), or at least 10 times (`critical`).
- **Error spike**: an unusual failure rate compared with the same time slot on the previous 7 days. It needs per-window request counts and that history, which a log file alone does not contain, so the CLI currently skips it and says so in the report's notes.

Each finding shows its severity, type, start time, count, service, message and the detector's description. Findings are sorted `critical` before `warning`; equal severities are ordered by start time, then detector, service and message, so the output is deterministic. With no findings the report says `No findings detected.`

Excerpt of the actual output for `data/sample.log`:

```
============================================================
LOG ANALYSIS REPORT
============================================================
Log file: data/sample.log
Findings: 8

1. [CRITICAL] Repeated error message
   Started: 2026-10-19 15:30:16
   Count:   14 occurrences
   Service: payment-service
   Message: Connection timeout to db-primary
   Details: payment-service: 'Connection timeout to db-primary' repeated 14 times in the 5-minute window starting 2026-10-19T15:30:00

...

Notes:
  - 3558 entries parsed
  - 73 malformed lines skipped (bad_timestamp: 19, blank_line: 12, missing_fields: 35, unknown_level: 7)
  - Spike detection skipped (needs per-window request counts and prior-day history, which a log file alone does not provide): missing request count for 2026-10-19T14:00:00

============================================================
```

## Tests

```
uv run --with pytest pytest
```

GitHub Actions runs the same command on every pull request (`.github/workflows/ci.yml`).
