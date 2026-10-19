from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A single successfully parsed log line.

    Immutable, so it can be shared safely between pipeline stages.

    Attributes:
        timestamp: When the event was logged.
        level: Severity, one of "INFO", "WARNING" or "ERROR".
        service: Name of the service that emitted the line, e.g. "payment-service".
        message: Free-text message; may contain commas.
        line_no: 1-based line number in the source file, for tracing back to the raw log.
    """

    timestamp: datetime
    level: str
    service: str
    message: str
    line_no: int
