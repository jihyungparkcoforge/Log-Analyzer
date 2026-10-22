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

`format`, `seed` (only when the run was seeded), `start`, `end`, `total_lines`, `malformed_lines`, `malformed_by_kind`, and `spike` (`start`, `end`, `window_error_counts`, `approx_error_count`, `dominant_service`, `top_message_template`).

`window_error_counts` holds the parseable ERROR count for each of the three 5-minute windows inside the spike; `approx_error_count` is their sum. `malformed_by_kind` uses the generator's labels, not the parser's reason tags.

## Regenerating

Two modes, both from `scripts/generate_logs.py`:

```
uv run python scripts/generate_logs.py --seed 42            # fixtures: data/sample.log + metadata
uv run python scripts/generate_logs.py --out /tmp/foo.log   # ad-hoc: that file + metadata only
uv run python scripts/generate_logs.py --out /tmp/foo.log --format comma --seed 7
```

- **Fixture mode (`--seed`, no `--out`):** one seeded plan is written once per registered format to its path in the script's `FIXTURES` map, each with a sibling `<path>.metadata.json`. Today only `comma` is registered, so only `sample.log` and `sample.log.metadata.json` are written. `FIXTURES` already reserves `sample.jsonl` for a later JSON format and will grow as formats are registered; unregistered formats are skipped. Passing `--format` here limits the run to that format.
- **Ad-hoc mode (`--out PATH`):** writes `PATH` and `PATH.metadata.json` only and never touches `data/`. `--format` defaults to `comma`, and `--seed` is optional (use it for a reproducible throwaway). With both `--seed` and `--out`, ad-hoc wins.
- With neither `--seed` nor `--out` the script exits 2 and writes nothing, so the fixtures are never overwritten by accident. An unknown `--format` also exits 2.

The same seed always gives byte-identical files, so `--seed 42` twice leaves git clean. The committed pair is generated with `--seed 42`; regenerate both files together and commit them together. Tests never run the script against `data/`; `tests/test_generator.py` imports it and writes only under a temporary directory.

The metadata records `format` and, when a seed was given, `seed`.

`total_lines - malformed_lines` is the number of entries a correct parser should return.
