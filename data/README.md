# Sample data

| File | What it is |
|---|---|
| `sample.log` | Generated application log: 3 hours of normal traffic, one planted error spike, and some malformed lines. |
| `sample.log.metadata.json` | Ground truth for `sample.log` (spike window, line counts). Use it for test assertions. |

Both files are generated and committed so the whole team works from identical data.

## Log line format (current contract)

```
YYYY-MM-DD HH:MM:SS,LEVEL,service,message
2026-10-19 14:00:17,ERROR,payment-service,Connection timeout to db-primary
```

| Field | Rule |
|---|---|
| timestamp | `YYYY-MM-DD HH:MM:SS` |
| level | `INFO`, `WARNING` or `ERROR` (anything else is malformed) |
| service | non-empty name, e.g. `payment-service` |
| message | everything after the third comma, so it may contain commas |

## Malformed lines

About 2% of lines are deliberately broken. A parser should skip and count them, not crash. The kinds present:

- truncated line
- bad timestamp (e.g. `20XX-10-19 ...`)
- unknown level (e.g. `FATAL`)
- missing fields
- garbage text
- blank line
- stack-trace continuation line

## Metadata keys

`seed`, `start`, `end`, `total_lines`, `malformed_lines`, `malformed_by_kind`, and `spike` (`start`, `end`, `approx_error_count`, `dominant_service`, `top_message_template`).

`total_lines - malformed_lines` is the number of entries a correct parser should return.
