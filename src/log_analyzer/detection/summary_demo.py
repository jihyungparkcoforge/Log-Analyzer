"""Run with --context-only or --model MODEL (requires OPENAI_API_KEY)."""

import argparse
import json
import os
from datetime import datetime, timedelta

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.detection.summary import LLMSummarizer, build_summary_context
from log_analyzer.parsing import LogParser


def demo_context() -> dict:
    """Build a reproducible synthetic incident through the real parser/detectors."""
    start = datetime(2026, 10, 19, 23)
    lines = [
        f"{start + timedelta(seconds=i % 300):%Y-%m-%d %H:%M:%S},ERROR,"
        "payment-service,Connection timeout to db-primary"
        for i in range(300)
    ]
    # Add one hour of quiet activity using logs alone; no traffic metadata exists.
    lines += [
        f"{start - timedelta(minutes=5 * i):%Y-%m-%d %H:%M:%S},INFO,"
        "payment-service,Background activity"
        for i in range(1, 13)
    ]
    data = DetectionInput(LogParser().parse_lines(lines).entries)
    # Both detectors flag the same 300 events for different reasons.
    # Concatenate findings, but never interpret their counts as 600 failures.
    findings = SpikeDetector().detect(data) + RepeatedMessageDetector().detect(data)
    return build_summary_context(
        data,
        findings,
        notes={
            "dataset": "Synthetic log-only demonstration: 300 ERROR events after one hour "
            "of INFO activity. Request counts and failure rates are unknown.",
            "deployment": "Synthetic event: payment-service v1.4 deployed at 22:55. "
            "No evidence yet that this deployment caused the errors.",
            "dependencies": "payment-service depends on db-primary.",
            "runbook": "Check database connectivity and connection pool metrics; "
            "compare deployment changes before considering a rollback.",
        },
    )


def main() -> None:
    """Print the JSON context offline, or submit it to the configured model."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--context-only", action="store_true")
    mode.add_argument(
        "--model",
        # Precedence: explicit CLI flag, then environment, then project default.
        default=os.environ.get("OPENAI_MODEL", "gpt-6-luna"),
        help="OpenAI model (default: OPENAI_MODEL or gpt-6-luna)",
    )
    args = parser.parse_args()
    context = demo_context()
    if args.context_only:
        # Inspect exactly what will be sent without making a paid API request.
        print(json.dumps(context, ensure_ascii=False, indent=2))
    else:
        try:
            print(LLMSummarizer(args.model).summarize(context))
        except (RuntimeError, ValueError) as error:
            parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
