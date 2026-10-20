import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.parsing import LogParser
from log_analyzer.reporting.reporter import format_report


def analyze(path: str | Path) -> str:
    """Parse a log file, run both detectors, and return the report text."""
    result = LogParser().parse_file(path)
    data = DetectionInput(result.entries)
    findings = list(RepeatedMessageDetector().detect(data))
    notes = [f"{len(result.entries)} entries parsed"]
    if result.malformed_count:
        reasons = ", ".join(
            f"{reason}: {count}"
            for reason, count in sorted(result.malformed_reasons.items())
        )
        notes.append(f"{result.malformed_count} malformed lines skipped ({reasons})")
    try:
        findings += SpikeDetector().detect(data)
    except ValueError as error:
        notes.append(
            "Spike detection skipped (needs per-window request counts and "
            f"prior-day history, which a log file alone does not provide): {error}"
        )
    return format_report(findings, log_file=path, notes=notes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="log-analyzer",
        description="Analyze a log file and print a severity-sorted report.",
    )
    parser.add_argument("log_file", help="path to a log file")
    args = parser.parse_args(argv)

    path = Path(args.log_file)
    if not path.is_file():
        print(f"error: log file not found: {path}", file=sys.stderr)
        return 2
    try:
        report = analyze(args.log_file)
    except OSError as error:
        print(f"error: cannot read {path}: {error.strerror or error}", file=sys.stderr)
        return 2
    print(report)
    return 0
