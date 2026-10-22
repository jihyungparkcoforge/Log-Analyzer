from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from log_analyzer.detection import Finding

SEVERITY_ORDER = {"critical": 0, "warning": 1}
TITLES = {"spike": "Error spike", "repeated_message": "Repeated error message"}
RULE = "=" * 60
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Order findings for display, most severe first.

    Ties are broken by start time, then detector, service, message and description,
    so the same findings always produce the same order. Unknown severities sort last,
    and missing service/message values are treated as empty strings.

    Args:
        findings: Findings from any detectors, in any order.

    Returns:
        A new sorted list; the input is not modified.
    """
    return sorted(
        findings,
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, len(SEVERITY_ORDER)),
            f.started_at,
            f.detector,
            f.service or "",
            f.message or "",
            f.description,
        ),
    )


def _count_text(finding: Finding) -> str:
    """Return the finding's count with a unit for its detector type.

    "errors" for spikes, "occurrences" for repeated messages, and the bare
    number for any other detector.
    """
    if finding.detector == "spike":
        text = f"{finding.count} errors"
        return text
    if finding.detector == "repeated_message":
        return f"{finding.count} occurrences"
    return str(finding.count)


def format_report(
    findings: Iterable[Finding],
    log_file: str | Path | None = None,
    notes: Iterable[str] = (),
) -> str:
    """Render findings as a plain-text report, most severe first.

    Each finding is shown as its number, severity, title, start time, count, the
    optional service and message, and the detector's own description. Nothing is
    recalculated; an empty collection prints "No findings detected.".

    Args:
        findings: Findings from any detectors, in any order.
        log_file: Path shown in the header; omitted when None.
        notes: Extra lines listed under "Notes:", e.g. parse statistics.

    Returns:
        The report as one string, lines joined with newlines.
    """
    ordered = sort_findings(findings)
    lines = [RULE, "LOG ANALYSIS REPORT", RULE]
    if log_file is not None:
        lines.append(f"Log file: {log_file}")
    lines.append(f"Findings: {len(ordered)}")
    lines.append("")

    if not ordered:
        lines += ["No findings detected.", ""]
    for number, finding in enumerate(ordered, start=1):
        title = TITLES.get(finding.detector, finding.detector)
        lines.append(f"{number}. [{finding.severity.upper()}] {title}")
        lines.append(f"   Started: {finding.started_at.strftime(TIMESTAMP_FORMAT)}")
        lines.append(f"   Count:   {_count_text(finding)}")
        if finding.service:
            lines.append(f"   Service: {finding.service}")
        if finding.message:
            lines.append(f"   Message: {finding.message}")
        lines.append(f"   Details: {finding.description}")
        lines.append("")

    notes = list(notes)
    if notes:
        lines.append("Notes:")
        lines += [f"  - {note}" for note in notes]
        lines.append("")

    lines.append(RULE)
    return "\n".join(lines)


def report_to_dict(
    findings: Iterable[Finding],
    log_file: str | Path | None = None,
    notes: Iterable[str] = (),
    summary: str | None = None,
) -> dict:
    """Build the report as a JSON-serializable dict, in the same order as the text.

    Args:
        findings: Findings from any detectors, in any order.
        log_file: Path of the analyzed log; stored as a string, or None.
        notes: Extra lines, e.g. parse statistics.
        summary: Optional LLM summary text; None when not requested or failed.

    Returns:
        A dict with log_file, finding_count, findings (every Finding field, with
        started_at as an ISO 8601 string), notes and summary.
    """
    ordered = sort_findings(findings)
    return {
        "log_file": None if log_file is None else str(log_file),
        "finding_count": len(ordered),
        "findings": [
            {**asdict(f), "started_at": f.started_at.isoformat()} for f in ordered
        ],
        "notes": list(notes),
        "summary": summary,
    }
