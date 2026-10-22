import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from log_analyzer.detection import (
    DetectionInput,
    Finding,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.detection.summary import LLMSummarizer, build_summary_context
from log_analyzer.parsing import LogParser
from log_analyzer.reporting.reporter import RULE, format_report, report_to_dict

DEFAULT_MODEL = "gpt-6-luna"


def run_detectors(
    path: str | Path, log_parser: LogParser | None = None
) -> tuple[DetectionInput, list[Finding], list[str]]:
    """Parse a log file and run both detectors; return the input, findings and notes."""
    result = (log_parser or LogParser()).parse_file(path)
    data = DetectionInput(result.entries)
    findings = list(RepeatedMessageDetector().detect(data))
    notes = [f"{len(result.entries)} entries parsed"]
    if result.malformed_count:
        reasons = ", ".join(
            f"{reason}: {count}"
            for reason, count in sorted(result.malformed_reasons.items())
        )
        notes.append(f"{result.malformed_count} malformed lines skipped ({reasons})")
    findings += SpikeDetector().detect(data)
    return data, findings, notes


def analyze(path: str | Path) -> str:
    """Parse a log file, run both detectors, and return the report text."""
    _, findings, notes = run_detectors(path)
    return format_report(findings, log_file=path, notes=notes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="log-analyzer",
        description="Analyze a log file and print a severity-sorted report.",
    )
    parser.add_argument("log_file", help="path to a log file")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="append an LLM summary of the findings (requires OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"OpenAI model for --summary (default: OPENAI_MODEL or {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        dest="json_path",
        help="also save the report as JSON to PATH",
    )
    args = parser.parse_args(argv)

    path = Path(args.log_file)
    if not path.is_file():
        print(f"error: log file not found: {path}", file=sys.stderr)
        return 2
    try:
        data, findings, notes = run_detectors(args.log_file)
    except OSError as error:
        print(f"error: cannot read {path}: {error.strerror or error}", file=sys.stderr)
        return 2

    summary = summary_error = None
    if args.summary:
        try:
            context = build_summary_context(data, findings)
            summary = LLMSummarizer(args.model).summarize(context)
        except (RuntimeError, ValueError) as error:
            summary_error = str(error)

    print(format_report(findings, log_file=args.log_file, notes=notes))
    if summary is not None:
        print(f"LLM SUMMARY (model: {args.model})\n\n{summary}\n{RULE}")

    if args.json_path:
        report = report_to_dict(
            findings, log_file=args.log_file, notes=notes, summary=summary
        )
        try:
            Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n")
        except OSError as error:
            print(
                f"error: cannot write {args.json_path}: {error.strerror or error}",
                file=sys.stderr,
            )
            return 2

    if summary_error:
        print(f"error: summary failed: {summary_error}", file=sys.stderr)
        return 1
    return 0
