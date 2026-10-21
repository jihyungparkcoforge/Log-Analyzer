from collections import defaultdict
from datetime import datetime

from log_analyzer.detection._windows import errors, validate_window, window_start
from log_analyzer.detection.base import BaseDetector, DetectionInput, Finding
from log_analyzer.models import LogEntry


class RepeatedMessageDetector(BaseDetector):
    """Flag exact error-message repetitions within each service and time window."""

    def __init__(
        self,
        window_minutes: int = 5,
        warning_threshold: int = 5,
        critical_threshold: int = 10,
    ) -> None:
        validate_window(window_minutes)
        if (
            type(warning_threshold) is not int
            or type(critical_threshold) is not int
            or not 1 <= warning_threshold < critical_threshold
        ):
            raise ValueError("require integer thresholds: 1 <= warning < critical")
        self.window_minutes = window_minutes
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold

    def detect(self, data: DetectionInput) -> tuple[Finding, ...]:
        # Service is part of the key: identical text in unrelated services is separate.
        # Message comparison is exact; variable IDs are not normalized.
        groups: dict[tuple[datetime, str, str], list[LogEntry]] = defaultdict(list)
        for entry in errors(data, self.window_minutes):
            key = (
                window_start(entry.timestamp, self.window_minutes),
                entry.service,
                entry.message,
            )
            groups[key].append(entry)
        findings = []
        for (start, service, message), entries in groups.items():
            count = len(entries)
            if count < self.warning_threshold:
                continue
            findings.append(
                Finding(
                    detector="repeated_message",
                    description=f"{service}: {message!r} repeated {count} times in the "
                    f"{self.window_minutes}-minute window starting {start.isoformat()}",
                    # errors() sorted the input, so this is the first occurrence.
                    started_at=entries[0].timestamp,
                    severity="critical"
                    if count >= self.critical_threshold
                    else "warning",
                    count=count,
                    service=service,
                    message=message,
                )
            )
        return tuple(
            sorted(findings, key=lambda f: (f.started_at, f.service, f.message))
        )
