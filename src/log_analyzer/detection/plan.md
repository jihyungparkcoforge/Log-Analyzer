# Detection implementation and data contract

## Input and scope

Both detectors accept `DetectionInput(entries)`, containing only the parser's `LogEntry` objects. No request counts, traffic metadata, or external historical dataset is needed. The parser excludes malformed lines, including missing/invalid timestamps; the CLI reports their count. Detectors use the parser's naive timestamps and sort errors before analysis. An ERROR event is not necessarily a failed request.

## Shared interface

`BaseDetector.detect(data)` returns a tuple of `Finding` objects. Each finding has a description, started_at, severity, and count. Spike findings additionally contain the baseline median in `expected_errors`, threshold, and baseline_windows. The median is descriptive context, not a calibrated probabilistic expectation. Repeated-message findings contain service and message. Overlapping finding counts must not be added.

## Spike rule

- Use five-minute half-open windows, aligned to midnight. Configurable window_minutes must divide 1440.
- Count ERROR entries in each window, across services. INFO/WARNING entries also establish the observed timeline.
- Maintain at most 12 prior non-alert window counts. Empty slots between observed windows contribute zero. No future data is used, and all state is reset per call.
- Let m be the baseline median and MAD the median absolute deviation. Set threshold = max(10, ceil(m + 3 * 1.4826 * max(MAD, 1))). min_errors, sensitivity, and baseline_windows are configurable.
- With no baseline, m and MAD are zero; the default threshold is 10. No warm-up exception prevents initial windows from being analyzed.
- A count at or above threshold produces warning; at or above twice threshold produces critical. Exclude alerting windows from the baseline to avoid absorbing a sustained incident.
- started_at is the window start. Include the observed count, threshold, median, and baseline size in the description.
- These are explainable tuning parameters, not guaranteed false-positive probabilities. Raising min_errors or sensitivity reduces sensitivity; lowering them can increase alerts.

## Repeated-message rule

Group ERROR entries by (window, service, exact message). Five occurrences give warning, ten give critical by default. started_at is the first occurrence. Case and variable IDs remain significant.

## Limits

The first and last windows may be partial, and no missing events are inferred. Gaps count as zero observed errors but may reflect missing telemetry. The recent baseline does not establish daily seasonality: a predictable nightly burst may still alert. Multi-day seasonal comparison could later use timestamps from the same supplied log, but is not implemented. Both detectors currently retain parsed entries in memory.

## Integration and tests

The CLI parses a file, runs both detectors directly, and formats their findings. There is no skipped spike path for missing request metadata. The committed sample produces spike counts of 36, 49, and 52 at 15:30, 15:35, and 15:40, with threshold 10 and critical severity. tests/test_cli.py checks actual spike findings and their rendered output against the planted interval.

Tests in tests/test_detection.py cover quiet/empty inputs, initial windows, severity boundaries, changing baselines, sustained spikes, zero-error gaps, future-data isolation, malformed lines, window boundaries, repeated messages, and summary request/response handling. Reporting tests cover sorting and formatting.

Run: `PYTHONPATH=src uv run --no-project --with pytest pytest -v`.

## LLM summary

summary.py combines actual findings, bounded error samples, aggregate log counts, and caller-supplied operational notes. Notes can contain deployment context, dependencies, and runbooks; no retrieval is performed automatically. The model must not infer request counts or failure rates from log counts. Context is limited to 64 KB and omission counts are explicit.

The synthetic summary demo includes one hour of INFO activity followed by 300 ERROR events. It uses the real parser and detectors without metadata. The deployment note is explicitly synthetic.

Inspect context without an API call:
`PYTHONPATH=src uv run --no-project python -m log_analyzer.detection.summary_demo --context-only`

Run the English summary using OPENAI_API_KEY and OPENAI_MODEL from .env:
`PYTHONPATH=src uv run --no-project --env-file .env python -m log_analyzer.detection.summary_demo`

Default model: gpt-6-luna. API tests use mock responses, not paid calls or quality evaluation. Do not submit sensitive data without appropriate authorization; the prompt is not a redaction mechanism.
