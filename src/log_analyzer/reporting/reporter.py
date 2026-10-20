from collections.abc import Iterable
from pathlib import Path

from log_analyzer.detection import Finding

SEVERITY_ORDER = {"critical": 0, "warning": 1}
TITLES = {"spike": "Error spike", "repeated_message": "Repeated error message"}
RULE = "=" * 60
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Most severe first; ties broken by start time, then detector, service, message."""
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
    if finding.detector == "spike":
        text = f"{finding.count} errors"
        if finding.request_count is not None:
            text += f" / {finding.request_count} requests"
        return text
    if finding.detector == "repeated_message":
        return f"{finding.count} occurrences"
    return str(finding.count)


def format_report(
    findings: Iterable[Finding],
    log_file: str | Path | None = None,
    notes: Iterable[str] = (),
) -> str:
    """Render findings as a plain-text report, most severe first."""
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
