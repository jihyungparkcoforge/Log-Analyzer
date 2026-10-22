"""Tests for LogParser: iteration, error bookkeeping, file handling and models.

Most tests use StubStrategy, so they exercise the parser's own behaviour and keep
passing when the default log format or any other strategy changes. Per-format
line parsing is covered in test_strategies.py.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from log_analyzer.models import LogEntry, MalformedLine, ParseResult
from log_analyzer.parsing import (
    STRATEGIES,
    CommaSeparatedStrategy,
    LogParser,
    ParseError,
)
from log_analyzer.reporting.cli import resolve_format

DATA = Path(__file__).parent.parent / "data"
SAMPLE = DATA / "sample.log"
SAMPLE_META = DATA / "sample.log.metadata.json"

GOOD = "2026-10-19 14:00:05,INFO,order-service,Order shipped order_id=89131"
STUB_TIME = datetime(2026, 1, 1)


class StubStrategy:
    """Format-independent strategy: `bad*` lines fail with reason `bad`, `blank*` with `blank`.

    Any other line becomes an INFO entry whose message is the stripped line.
    """

    def parse_line(self, line: str, line_no: int) -> LogEntry:
        """Return an entry for ordinary lines or raise ParseError for bad/blank ones."""
        text = line.rstrip("\r\n")
        if text.startswith("bad"):
            raise ParseError("bad")
        if text.startswith("blank"):
            raise ParseError("blank")
        return LogEntry(STUB_TIME, "INFO", "svc", text, line_no)


def stub_parser(**kwargs) -> LogParser:
    """Build a LogParser wired to StubStrategy."""
    return LogParser(strategy=StubStrategy(), **kwargs)


# --- Strategy wiring --------------------------------------------------------


def test_default_strategy_is_comma_separated():
    """With no strategy given, the parser reads the comma-separated format."""
    result = LogParser().parse_lines([GOOD])
    assert result.entries[0].service == "order-service"


def test_custom_strategy_replaces_the_default():
    """A supplied strategy is used instead of the default one."""
    result = stub_parser().parse_lines([GOOD])
    assert result.entries[0].message == GOOD
    assert result.entries[0].service == "svc"


def test_strategy_receives_one_based_line_numbers():
    """The parser passes 1-based line numbers to the strategy."""
    result = stub_parser().parse_lines(["a", "b", "c"])
    assert [e.line_no for e in result.entries] == [1, 2, 3]


def test_non_parse_errors_from_strategy_propagate():
    """Only ParseError is swallowed; other exceptions are real bugs and surface."""

    class Broken:
        """Strategy that always fails with an unexpected error."""

        def parse_line(self, line, line_no):
            """Always raise RuntimeError."""
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        LogParser(strategy=Broken()).parse_lines(["x"])


def test_each_parser_keeps_its_own_strategy():
    """Two parsers with different strategies do not interfere."""
    default, stub = LogParser(), stub_parser()
    assert default.parse_lines([GOOD]).entries[0].service == "order-service"
    assert stub.parse_lines([GOOD]).entries[0].service == "svc"


# --- parse_lines ------------------------------------------------------------


def test_parse_lines_empty_input():
    """No lines yields an empty ParseResult."""
    assert stub_parser().parse_lines([]) == ParseResult((), 0, {}, ())


def test_parse_lines_returns_entries_in_source_order():
    """Entries keep file order and are returned as a tuple."""
    result = stub_parser().parse_lines(["first", "second", "third"])
    assert [e.message for e in result.entries] == ["first", "second", "third"]
    assert isinstance(result.entries, tuple)
    assert result.malformed_count == 0


def test_parse_lines_skips_and_records_malformed_lines():
    """Bad lines are skipped, counted per reason, and kept as samples."""
    result = stub_parser().parse_lines(["ok", "bad1", "blank", "bad2", "ok"])
    assert [e.line_no for e in result.entries] == [1, 5]
    assert result.malformed_count == 3
    assert result.malformed_reasons == {"bad": 2, "blank": 1}
    assert result.malformed_samples == (
        MalformedLine(2, "bad", "bad1"),
        MalformedLine(3, "blank", "blank"),
        MalformedLine(4, "bad", "bad2"),
    )


def test_malformed_sample_raw_drops_trailing_newline_only():
    """The raw sample loses its line ending but keeps other whitespace."""
    result = stub_parser().parse_lines(["bad  \r\n"])
    assert result.malformed_samples[0].raw == "bad  "


def test_samples_are_capped_but_counts_are_not():
    """max_samples bounds the stored samples while counts stay exact."""
    result = stub_parser(max_samples=2).parse_lines(["bad"] * 5)
    assert result.malformed_count == 5
    assert result.malformed_reasons == {"bad": 5}
    assert [s.line_no for s in result.malformed_samples] == [1, 2]


def test_max_samples_zero_keeps_no_samples():
    """max_samples=0 records counts but no sample lines."""
    result = stub_parser(max_samples=0).parse_lines(["bad"])
    assert result.malformed_count == 1
    assert result.malformed_samples == ()


def test_default_max_samples_is_20():
    """By default at most 20 malformed samples are kept."""
    result = stub_parser().parse_lines(["bad"] * 25)
    assert len(result.malformed_samples) == 20


def test_parse_lines_accepts_a_generator():
    """Any iterable of lines works, including a one-shot generator."""
    result = stub_parser().parse_lines(line for line in ["a", "b"])
    assert len(result.entries) == 2


def test_parse_calls_are_independent():
    """A shared parser carries no state from one parse call to the next."""
    parser = stub_parser()
    first = parser.parse_lines(["bad"])
    second = parser.parse_lines(["ok"])
    assert first.malformed_count == 1
    assert second.malformed_count == 0
    assert second.malformed_reasons == {}
    assert second.entries[0].line_no == 1


# --- parse_file -------------------------------------------------------------


def test_parse_file_accepts_str_and_path(tmp_path):
    """parse_file gives the same result for a str path and a Path."""
    path = tmp_path / "a.log"
    path.write_text(GOOD + "\n", encoding="utf-8")
    assert LogParser().parse_file(path) == LogParser().parse_file(str(path))
    assert len(LogParser().parse_file(path).entries) == 1


def test_parse_file_uses_the_configured_strategy(tmp_path):
    """parse_file feeds each file line through the parser's strategy."""
    path = tmp_path / "a.log"
    path.write_text("ok\nbad\n", encoding="utf-8")
    result = stub_parser().parse_file(path)
    assert [e.message for e in result.entries] == ["ok"]
    assert result.malformed_reasons == {"bad": 1}


def test_parse_file_handles_crlf_and_missing_final_newline(tmp_path):
    """CRLF line endings and a last line without a newline both parse cleanly."""
    path = tmp_path / "a.log"
    path.write_bytes((GOOD + "\r\n" + GOOD).encode())
    result = LogParser().parse_file(path)
    assert len(result.entries) == 2
    assert result.malformed_count == 0


def test_parse_file_replaces_undecodable_bytes(tmp_path):
    """Invalid UTF-8 becomes U+FFFD instead of raising or dropping the line."""
    path = tmp_path / "a.log"
    path.write_bytes(b"has a bad byte \xff here\n")
    result = stub_parser().parse_file(path)
    assert result.malformed_count == 0
    assert "�" in result.entries[0].message


def test_parse_file_missing_raises_oserror(tmp_path):
    """A nonexistent file raises FileNotFoundError for the caller to handle."""
    with pytest.raises(FileNotFoundError):
        LogParser().parse_file(tmp_path / "nope.log")


# --- Models -----------------------------------------------------------------


def test_models_are_immutable():
    """LogEntry and ParseResult are frozen dataclasses."""
    entry = CommaSeparatedStrategy().parse_line(GOOD, 1)
    with pytest.raises(AttributeError):
        entry.level = "ERROR"
    with pytest.raises(AttributeError):
        LogParser().parse_lines([]).malformed_count = 1


# --- Sample file ------------------------------------------------------------


def test_sample_log_matches_generator_metadata():
    """Parsing data/sample.log reconciles with the totals the generator recorded."""
    meta = json.loads(SAMPLE_META.read_text())
    strategy = STRATEGIES[resolve_format(SAMPLE)]()
    result = LogParser(strategy=strategy).parse_file(SAMPLE)
    assert len(result.entries) + result.malformed_count == meta["total_lines"]
    assert result.malformed_count == meta["malformed_lines"]
    assert sum(result.malformed_reasons.values()) == result.malformed_count
    assert len(result.malformed_samples) == 20
    assert all(e.level in {"INFO", "WARNING", "ERROR"} for e in result.entries)


def test_sample_jsonl_matches_generator_metadata():
    """Parsing data/sample.jsonl reconciles with its sibling metadata."""
    path = DATA / "sample.jsonl"
    meta = json.loads(Path(f"{path}.metadata.json").read_text())
    strategy = STRATEGIES[resolve_format(path)]()
    result = LogParser(strategy=strategy).parse_file(path)
    assert meta["format"] == "json"
    assert len(result.entries) + result.malformed_count == meta["total_lines"]
    assert result.malformed_count == meta["malformed_lines"]
    assert len(result.malformed_samples) == 20
