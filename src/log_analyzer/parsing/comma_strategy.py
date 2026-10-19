from datetime import datetime

from log_analyzer.models import LogEntry
from log_analyzer.parsing.strategy import ParseError

VALID_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class CommaSeparatedStrategy:
    """The current log format: `YYYY-MM-DD HH:MM:SS,LEVEL,service,message`.

    The message is everything after the third comma, so it may contain commas.
    """

    def parse_line(self, line: str, line_no: int) -> LogEntry:
        line = line.rstrip("\r\n")
        if not line.strip():
            raise ParseError("blank_line")

        parts = line.split(",", 3)
        if len(parts) < 4:
            raise ParseError("missing_fields")
        raw_timestamp, level, service, message = (part.strip() for part in parts)

        try:
            timestamp = datetime.strptime(raw_timestamp, TIMESTAMP_FORMAT)
        except ValueError:
            raise ParseError("bad_timestamp") from None
        if level not in VALID_LEVELS:
            raise ParseError("unknown_level")
        if not service:
            raise ParseError("empty_service")
        if not message:
            raise ParseError("empty_message")

        return LogEntry(timestamp, level, service, message, line_no)
