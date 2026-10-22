# Sample data

| File | What it is |
|---|---|
| `sample.log` | Comma-separated sample: 3 hours of normal traffic, one planted error spike, and some malformed lines. |
| `sample.log.metadata.json` | Ground truth for `sample.log` (spike window, line counts). |
| `sample.jsonl` | The same incident as JSON Lines. |
| `sample.jsonl.metadata.json` | Ground truth for `sample.jsonl`. |

`--seed 42` writes both logs from one plan, so spike counts match. Commit all four files together.

## Log line formats

Two formats exist. Each file's sibling `<path>.metadata.json` records `"format"`.

### Comma (`format: "comma"`)

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

### JSON Lines (`format: "json"`)

One compact JSON object per line (NDJSON), so commas and quotes in the message stay valid and on one line:

```
{"ts":"2026-10-19 14:00:17","level":"ERROR","service":"payment-service","message":"Connection timeout to db-primary"}
```

| Key | Rule |
|---|---|
| `ts` | `YYYY-MM-DD HH:MM:SS`, same as comma |
| `level` | `INFO`, `WARNING` or `ERROR` |
| `service` | non-empty string |
| `message` | non-empty string |

Extra keys are ignored. Parser reasons: `blank_line`, `invalid_json`, `missing_fields`, `bad_timestamp`, `unknown_level`, `empty_service`, `empty_message`.

## Malformed lines

About 2% of lines are deliberately broken. A parser should skip and count them, not crash. In either format the kinds present are:

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
uv run python scripts/generate_logs.py --seed 42
uv run python scripts/generate_logs.py --out /tmp/foo.log
uv run python scripts/generate_logs.py --out /tmp/foo.jsonl --format json --seed 7
```

- **Fixture mode (`--seed`, no `--out`):** one seeded plan is written once per registered format to its path in the script's `FIXTURES` map (`sample.log` and `sample.jsonl`), each with a sibling `<path>.metadata.json`. Passing `--format` here limits the run to that format.
- **Ad-hoc mode (`--out PATH`):** writes `PATH` and `PATH.metadata.json` only and never touches `data/`. `--format` defaults to `comma`, and `--seed` is optional. With both `--seed` and `--out`, ad-hoc wins.
- With neither `--seed` nor `--out` the script exits 2 and writes nothing. An unknown `--format` also exits 2.

The same seed always gives byte-identical files, so `--seed 42` twice leaves git clean. The committed pair is generated with `--seed 42`; regenerate all four files together and commit them together. Tests never run the script against `data/`; `tests/test_generator.py` imports it and writes only under a temporary directory.

The metadata records `format` and, when a seed was given, `seed`.

`total_lines - malformed_lines` is the number of entries a correct parser should return.
