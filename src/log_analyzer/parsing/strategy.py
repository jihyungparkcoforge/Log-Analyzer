from typing import Protocol

from log_analyzer.models import LogEntry


class ParseError(ValueError):
    """Raised by a strategy when a line cannot be parsed.

    Attributes:
        reason: Short tag describing the failure, e.g. "bad_timestamp".
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LineParseStrategy(Protocol):
    """How to turn one raw log line into a LogEntry.

    Implement this to support a new log format. Raise ParseError for any line
    that doesn't fit; the parser records it and moves on.
    """

    def parse_line(self, line: str, line_no: int) -> LogEntry: ...
