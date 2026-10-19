from collections.abc import Mapping
from dataclasses import dataclass

from log_analyzer.models.log_entry import LogEntry


@dataclass(frozen=True, slots=True)
class MalformedLine:
    """A line that could not be parsed, kept for diagnostics.

    Attributes:
        line_no: 1-based line number in the source.
        reason: Short tag explaining the failure, e.g. "bad_timestamp".
        raw: The original line text, without its trailing newline.
    """

    line_no: int
    reason: str
    raw: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of parsing one log source. Created fresh by each parse call.

    Attributes:
        entries: Successfully parsed entries, in source order.
        malformed_count: Total number of lines that failed to parse.
        malformed_reasons: Count of failures per reason tag.
        malformed_samples: The first few failures, for diagnostics (bounded in size).
    """

    entries: tuple[LogEntry, ...]
    malformed_count: int
    malformed_reasons: Mapping[str, int]
    malformed_samples: tuple[MalformedLine, ...]
