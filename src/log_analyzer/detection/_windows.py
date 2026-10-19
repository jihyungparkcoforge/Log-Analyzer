from datetime import datetime, timedelta

from log_analyzer.detection.base import DetectionInput
from log_analyzer.models import LogEntry


def validate_window(minutes: int) -> None:
    if type(minutes) is not int or minutes <= 0 or 1440 % minutes:
        raise ValueError("window_minutes must be a positive divisor of 1440")


def window_start(timestamp: datetime, minutes: int) -> datetime:
    if timestamp.tzinfo is not None:
        raise ValueError("timestamps must be naive, matching the parser contract")
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    slot = (timestamp.hour * 60 + timestamp.minute) // minutes
    return midnight + timedelta(minutes=slot * minutes)


def errors(data: DetectionInput, minutes: int) -> list[LogEntry]:
    for entry in data.entries:
        window_start(entry.timestamp, minutes)
    return sorted(
        (entry for entry in data.entries if entry.level == "ERROR"),
        key=lambda entry: (entry.timestamp, entry.line_no),
    )
