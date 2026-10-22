from log_analyzer.parsing.comma_strategy import CommaSeparatedStrategy
from log_analyzer.parsing.json_strategy import JsonStrategy
from log_analyzer.parsing.parser import LogParser
from log_analyzer.parsing.strategy import LineParseStrategy, ParseError

# The one place format names map to strategy classes (used by `--format` and metadata).
STRATEGIES: dict[str, type[LineParseStrategy]] = {
    "comma": CommaSeparatedStrategy,
    "json": JsonStrategy,
}

__all__ = [
    "STRATEGIES",
    "CommaSeparatedStrategy",
    "JsonStrategy",
    "LineParseStrategy",
    "LogParser",
    "ParseError",
]
