import json
from datetime import datetime

from log_analyzer.models import LogEntry
from log_analyzer.parsing.comma_strategy import TIMESTAMP_FORMAT, VALID_LEVELS
from log_analyzer.parsing.strategy import ParseError

REQUIRED_KEYS = ("ts", "level", "service", "message")


class JsonStrategy:
    """JSON Lines: one object per line with keys `ts`, `level`, `service`, `message`.

    `ts` uses the same `YYYY-MM-DD HH:MM:SS` format as the comma strategy. Extra
    keys are ignored and string values are stripped. A value of the wrong type
    is reported with the reason of its field (e.g. a numeric `ts` is `bad_timestamp`).
    """

    def parse_line(self, line: str, line_no: int) -> LogEntry:
        """Parse one JSON Lines record, raising ParseError(reason) if it is unusable."""
        line = line.rstrip("\r\n")
        if not line.strip():
            raise ParseError("blank_line")

        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            # RecursionError: absurdly nested input such as "[[[[...".
            raise ParseError("invalid_json") from None
        if not isinstance(record, dict):
            raise ParseError("invalid_json")
        if any(key not in record for key in REQUIRED_KEYS):
            raise ParseError("missing_fields")

        raw_timestamp, level, service, message = (
            value.strip() if isinstance(value, str) else None
            for value in (record[key] for key in REQUIRED_KEYS)
        )
        try:
            timestamp = datetime.strptime(raw_timestamp or "", TIMESTAMP_FORMAT)
        except ValueError:
            raise ParseError("bad_timestamp") from None
        if level not in VALID_LEVELS:
            raise ParseError("unknown_level")
        if not service:
            raise ParseError("empty_service")
        if not message:
            raise ParseError("empty_message")

        return LogEntry(timestamp, level, service, message, line_no)
