from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from log_analyzer.models import MalformedLine, ParseResult
from log_analyzer.parsing.comma_strategy import CommaSeparatedStrategy
from log_analyzer.parsing.strategy import LineParseStrategy, ParseError


class LogParser:
    """Parses log text into LogEntry objects using a pluggable line strategy.

    The parser holds only configuration, never per-run state, so a single
    instance can be shared safely. Each parse call returns a fresh ParseResult.
    Malformed lines are skipped and recorded in the result, never raised.
    """

    def __init__(
        self,
        strategy: LineParseStrategy | None = None,
        max_samples: int = 20,
    ) -> None:
        """
        Args:
            strategy: How to parse a single line. Defaults to the current comma-separated format.
            max_samples: How many malformed lines to keep for diagnostics.
        """
        self._strategy = strategy or CommaSeparatedStrategy()
        self._max_samples = max_samples

    def parse_file(self, path: str | Path) -> ParseResult:
        """Parse a log file. Undecodable bytes are replaced rather than raising."""
        with open(path, encoding="utf-8", errors="replace") as f:
            return self.parse_lines(f)

    def parse_lines(self, lines: Iterable[str]) -> ParseResult:
        """Parse any iterable of raw lines (a file object, a list, ...)."""
        entries = []
        reasons: Counter[str] = Counter()
        samples: list[MalformedLine] = []
        malformed_count = 0

        for line_no, line in enumerate(lines, start=1):
            try:
                entries.append(self._strategy.parse_line(line, line_no))
            except ParseError as error:
                malformed_count += 1
                reasons[error.reason] += 1
                if len(samples) < self._max_samples:
                    samples.append(
                        MalformedLine(line_no, error.reason, line.rstrip("\r\n"))
                    )

        return ParseResult(
            tuple(entries), malformed_count, dict(reasons), tuple(samples)
        )
