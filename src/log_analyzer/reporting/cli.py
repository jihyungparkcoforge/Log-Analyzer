import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import dotenv_values

from log_analyzer.detection import (
    DetectionInput,
    Finding,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.detection.summary import LLMSummarizer, build_summary_context
from log_analyzer.parsing import STRATEGIES, LineParseStrategy, LogParser
from log_analyzer.reporting.reporter import RULE, format_report, report_to_dict

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_FORMAT = "comma"


def resolve_format(path: str | Path, explicit: str | None = None) -> str:
    """Pick the log format name: `--format`, else sibling metadata, else comma.

    The sibling file is `<log file>.metadata.json`; it is used only if it holds
    a readable JSON object with a "format" key. The log's contents are never sniffed.
    The returned name is not validated; see `STRATEGIES`.
    """
    if explicit is not None:
        return explicit
    metadata_path = Path(f"{path}.metadata.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_FORMAT
    if isinstance(metadata, dict) and "format" in metadata:
        return str(metadata["format"])
    return DEFAULT_FORMAT


def run_detectors(
    path: str | Path, log_parser: LogParser | None = None
) -> tuple[DetectionInput, list[Finding], list[str]]:
    """Parse a log file and run both detectors with their default settings.

    Args:
        path: Log file to analyze.
        log_parser: Parser to use; defaults to LogParser() (comma-separated format).

    Returns:
        The DetectionInput given to the detectors, the combined findings (unsorted),
        and notes on how many entries were parsed and malformed lines skipped.

    Raises:
        OSError: If the file cannot be opened or read.
    """
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


def analyze(path: str | Path, strategy: LineParseStrategy | None = None) -> str:
    """Parse a log file, run both detectors, and return the report text.

    Args:
        path: Log file to analyze.
        strategy: How to parse each line. Defaults to the parser's default (comma).

    Returns:
        The plain-text report from format_report.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    log_parser = LogParser(strategy=strategy) if strategy is not None else None
    _, findings, notes = run_detectors(path, log_parser)
    return format_report(findings, log_file=path, notes=notes)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the log-analyzer command line.

    Prints the report to stdout, optionally appends an LLM summary (--summary)
    and saves a JSON copy (--json). Errors go to stderr as one line, not a traceback.

    Args:
        argv: Command-line arguments without the program name; None reads sys.argv.

    Returns:
        Exit code: 0 on success (with or without findings), 1 if --summary failed
        (the report is still printed and any JSON still saved), 2 for a missing or
        unreadable log file, an unknown --format, or an unwritable --json path.
    """
    # Read the working directory's .env before resolving the default model.
    # Local LLM settings override stale exports; unrelated variables are untouched.
    settings = dotenv_values(Path.cwd() / ".env")
    for name in ("OPENAI_API_KEY", "ENDPOINT", "OPENAI_MODEL"):
        if settings.get(name) is not None:
            os.environ[name] = settings[name]

    parser = argparse.ArgumentParser(
        prog="log-analyzer",
        description="Analyze a log file and print a severity-sorted report.",
    )
    parser.add_argument("log_file", help="path to a log file")
    parser.add_argument(
        "--format",
        help=(
            f"log format ({', '.join(STRATEGIES)}); default: the 'format' in "
            f"<log file>.metadata.json if present, else {DEFAULT_FORMAT}"
        ),
    )
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
    format_name = resolve_format(path, args.format)
    if format_name not in STRATEGIES:
        supported = ", ".join(STRATEGIES)
        print(
            f"error: unsupported format '{format_name}' (supported: {supported})",
            file=sys.stderr,
        )
        return 2
    try:
        data, findings, notes = run_detectors(
            args.log_file, LogParser(strategy=STRATEGIES[format_name]())
        )
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
